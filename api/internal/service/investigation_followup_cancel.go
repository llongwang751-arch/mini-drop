// 追加分支的取消和过期回收只作用于平台已经创建并关联的本人任务。
// 即使请求者随后失去Agent授权，系统仍可回收其已授权启动的工作，不能因此继续派发或读取新数据。
package service

import (
	"context"
	"fmt"
	"slices"
	"time"

	"drop/api/internal/domain"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

func (s *Suggestions) cancelInvestigationFollowups(ctx context.Context, tx *gorm.DB, root Investigation, code, status string) error {
	var rows []InvestigationFollowup
	if err := tx.Where("investigation_id=? AND status IN ('proposed','queued','collecting','diagnosing')", root.ID).Order("id").Find(&rows).Error; err != nil {
		return err
	}
	return s.cancelInvestigationFollowupRows(ctx, tx, root, rows, code, status)
}

// 单分支推进失败也必须撤销已经派发的工作，不能只把界面状态改成失败。
// rows 只能由受锁调查的数据库关系产生；不能接收客户端提供的任务列表。
func (s *Suggestions) cancelInvestigationFollowupRows(ctx context.Context, tx *gorm.DB, root Investigation, rows []InvestigationFollowup, code, status string) error {
	if len(rows) == 0 {
		return nil
	}
	byID, err := lockInvestigationFollowupTasks(ctx, tx, root, rows)
	if err != nil {
		return err
	}
	// 该主体来自受信任的调查所有者，不从HTTP或模型提供的task ID选择任意工作。
	// Repository.Cancel继续限制creator作用域；这里不伪造管理员角色或增加授权。
	actor := domain.User{ID: root.RequestedBy}
	for _, row := range rows {
		if row.TaskID != nil {
			task, exists := byID[*row.TaskID]
			if !exists || task.OwnerID != root.RequestedBy {
				return domain.Fail("INVALID_EVIDENCE", "补证任务所有者或来源关系异常")
			}
			if task.DeletedAt == nil {
				if _, err := s.Repo.Cancel(ctx, actor, task.ID, Audit(actor, "investigation.followup_cleanup", "task", fmt.Sprint(task.ID), "investigation-worker")); err != nil {
					return err
				}
			}
		}
		if row.DiagnosisID != nil {
			if err := tx.Model(&DiagnosisRun{}).Where("id=? AND requested_by=? AND status IN ('pending','running')", *row.DiagnosisID, root.RequestedBy).Updates(map[string]any{"status": "failed", "error_code": code, "lease_owner": nil, "lease_expires_at": nil, "updated_at": time.Now().UTC()}).Error; err != nil {
				return err
			}
		}
		if err := tx.Model(&InvestigationFollowup{}).Where("id=? AND investigation_id=? AND status IN ('proposed','queued','collecting','diagnosing')", row.ID, root.ID).Updates(map[string]any{"status": status, "error_code": code, "updated_at": time.Now().UTC()}).Error; err != nil {
			return err
		}
	}
	return tx.Create(ptrAuditDiagnosis(Audit(actor, "investigation.followup_cleanup", "investigation", fmt.Sprint(root.ID), "investigation-worker"))).Error
}

// 推进和回收共用同一锁序。工作器先固定此范围，再检查来源/创建诊断；
// 后续失败回收可以重复加锁，不能在已持有task锁后才首次请求verification锁。
func lockInvestigationFollowupTasks(ctx context.Context, tx *gorm.DB, root Investigation, rows []InvestigationFollowup) (map[int64]domain.Task, error) {
	taskIDs := []int64{}
	// 原复测可能与追加分支并存；所有取消入口都必须先锁复测，再按升序锁任务。
	// 若先锁追加分支的来源任务，复测工作器会形成 verification→task 的反向等待。
	if root.VerificationID != nil {
		var verification VerificationRun
		if err := tx.Raw("SELECT * FROM verification_runs WHERE id=? FOR UPDATE", *root.VerificationID).Scan(&verification).Error; err != nil {
			return nil, err
		}
		if verification.ID > 0 {
			taskIDs = append(taskIDs, verification.BaselineTaskID, verification.CandidateTaskID)
		}
	}
	if root.BaselineTaskID != nil {
		taskIDs = append(taskIDs, *root.BaselineTaskID)
	}
	for _, row := range rows {
		if row.InvestigationID != root.ID {
			return nil, domain.Fail("INVALID_EVIDENCE", "补证分支不属于当前调查")
		}
		taskIDs = append(taskIDs, row.ParentTaskID)
		if row.TaskID != nil {
			taskIDs = append(taskIDs, *row.TaskID)
		}
	}
	slices.Sort(taskIDs)
	taskIDs = slices.Compact(taskIDs)
	var tasks []domain.Task
	if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id IN ?", taskIDs).Order("id").Find(&tasks).Error; err != nil {
		return nil, err
	}
	byID := map[int64]domain.Task{}
	for _, task := range tasks {
		byID[task.ID] = task
	}
	return byID, nil
}
