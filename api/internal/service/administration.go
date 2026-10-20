// Package service：管理操作会同时影响采集与分析的合法性：重分析重建分析身份，删除撤销上传权限，停用 Agent 阻止后续派发。
// 状态修改、事件和审计在同一事务内完成，后台旧工作器依赖这些身份变化退出。
package service

import (
	"context"
	"fmt"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func (s *Tasks) SetAgentEnabled(ctx context.Context, u domain.User, id string, enabled bool, request string) (domain.Agent, error) {
	var a domain.Agent
	if !u.IsPlatformAdmin() {
		return a, domain.Fail("FORBIDDEN", "只有平台管理员可禁用Agent")
	}
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", id).First(&a).Error; err != nil {
			return err
		}
		state := "disabled"
		if enabled {
			state = "offline"
			if time.Since(a.LastSeenAt) < 45*time.Second {
				state = "online"
			}
		}
		if err := tx.Model(&a).Update("status", state).Error; err != nil {
			return err
		}
		action := "agent.disable"
		if enabled {
			action = "agent.enable"
		}
		return tx.Create(ptrAudit(Audit(u, action, "agent", id, request))).Error
	})
	return a, err
}

// Reanalyze 重新校验任务权限、当前采集制品和旧分析状态，再创建新的分析运行身份。
// 旧 Analyzer 的租约或结果此后不能覆盖新 run；采集状态与原始制品不会被重新解释为新采集。
func (s *Tasks) Reanalyze(ctx context.Context, u domain.User, id int64, request string) error {
	if !u.IsPlatformAdmin() {
		return domain.Fail("FORBIDDEN", "只有平台管理员可重新分析")
	}
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var task domain.Task
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", id).First(&task).Error; err != nil {
			return err
		}
		if task.CollectionStatus != "COLLECTED" {
			return domain.Fail("INVALID_STATE", "仅已采集任务可重分析")
		}
		var attempt domain.Attempt
		if err := tx.Where("task_id=?", id).Order("attempt DESC").First(&attempt).Error; err != nil {
			return err
		}
		var job struct {
			ID      int64
			Status  string
			Attempt int
		}
		if err := tx.Raw("SELECT id,status,attempt FROM analysis_jobs WHERE task_id=? AND attempt_id=? FOR UPDATE", id, attempt.ID).Scan(&job).Error; err != nil {
			return err
		}
		if job.ID == 0 || job.Status != "success" && job.Status != "failed" {
			return domain.Fail("INVALID_STATE", "当前分析尚未结束，不覆盖执行租约")
		}
		var raw int64
		if err := tx.Table("artifacts").Where("task_id=? AND attempt_id=? AND kind='RAW' AND status='ready' AND (expires_at IS NULL OR expires_at>now())", id, attempt.ID).Count(&raw).Error; err != nil {
			return err
		}
		if raw == 0 {
			return domain.Fail("ARTIFACT_EXPIRED", "原始制品已过期或不可用")
		}
		if err := tx.Exec("INSERT INTO analysis_job_runs(job_id,task_id,attempt_id,run_number,status,result,error_code,analyzer_version) SELECT id,task_id,attempt_id,attempt,status,result,error_code,analyzer_version FROM analysis_jobs WHERE id=? ON CONFLICT DO NOTHING", job.ID).Error; err != nil {
			return err
		}
		if err := tx.Exec("UPDATE analysis_jobs SET status='pending',available_at=now(),max_attempts=attempt+3,lease_owner=NULL,lease_expires_at=NULL,result=NULL,error_code=NULL,error_message=NULL,updated_at=now() WHERE id=?", job.ID).Error; err != nil {
			return err
		}
		if err := tx.Exec("UPDATE suggestions SET superseded_at=now() WHERE task_id=? AND superseded_at IS NULL", id).Error; err != nil {
			return err
		}
		if err := tx.Model(&task).Updates(map[string]any{"analysis_status": "PENDING", "error_code": nil, "error_message": nil, "updated_at": time.Now().UTC(), "version": gorm.Expr("version+1")}).Error; err != nil {
			return err
		}
		event := domain.EventFor(task, &attempt.ID, "ANALYSIS_REQUEUED", map[string]any{"previous_run": job.Attempt})
		if err := tx.Create(&event).Error; err != nil {
			return err
		}
		return tx.Create(ptrAudit(Audit(u, "task.reanalyze", "task", fmt.Sprint(id), request))).Error
	})
}

// Delete 在事务中标记删除并撤销当前上传授权，同时记录事件和审计；删除可见性与磁盘对象清理是不同阶段。
func (s *Tasks) Delete(ctx context.Context, u domain.User, id int64, request string) error {
	if !u.CanWrite() {
		return domain.Fail("FORBIDDEN", "只读角色")
	}
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var task domain.Task
		if err := repository.ScopeWrite(tx, u).Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", id).First(&task).Error; err != nil {
			return err
		}
		if err := repository.New(tx).CanManageTask(ctx, u, task); err != nil {
			return err
		}
		if !task.Terminal() {
			return domain.Fail("INVALID_STATE", "请先取消或等待任务结束")
		}
		if err := tx.Model(&task).Update("deleted_at", time.Now().UTC()).Error; err != nil {
			return err
		}
		if err := tx.Model(&domain.Attempt{}).Where("task_id=?", task.ID).Updates(map[string]any{"raw_upload_url": "", "upload_targets": "[]"}).Error; err != nil {
			return err
		}
		event := domain.EventFor(task, nil, "TASK_DELETED", map[string]any{"retention": "artifacts retain their existing expiry"})
		if err := tx.Create(&event).Error; err != nil {
			return err
		}
		return tx.Create(ptrAudit(Audit(u, "task.delete", "task", fmt.Sprint(id), request))).Error
	})
}
