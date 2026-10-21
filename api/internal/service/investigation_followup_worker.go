// 补证工作器复用普通采集和诊断，不另造采集状态。
// 每个根调查最多两个追加分支，分支失败保留独立记录；不能把失败补证改写成根因确认。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

func (s *Suggestions) advanceInvestigationFollowups(ctx context.Context) error {
	var ids []int64
	// 未操作的草稿与无计划的历史success不能一直占据最早一页，阻塞新分支。
	if err := s.Repo.Context(ctx).Raw(`SELECT i.id FROM investigations i WHERE
 EXISTS(SELECT 1 FROM investigation_followups f WHERE f.investigation_id=i.id AND f.status IN ('queued','collecting','diagnosing'))
 OR ((i.created_at<=now()-interval '20 minutes' OR i.status IN ('failed','canceled')) AND
 EXISTS(SELECT 1 FROM investigation_followups f WHERE f.investigation_id=i.id AND f.status='proposed'))
 OR (i.created_at>now()-interval '20 minutes' AND i.status NOT IN ('failed','canceled')
 AND (SELECT count(*) FROM investigation_followups f WHERE f.investigation_id=i.id)<2
 AND EXISTS(SELECT 1 FROM diagnosis_runs d WHERE d.status='success' AND d.requested_by=i.requested_by
 AND (d.id=i.diagnosis_id OR d.id IN (SELECT candidate_diagnosis_id FROM verification_runs WHERE id=i.verification_id)
 OR d.id IN (SELECT diagnosis_id FROM investigation_followups WHERE investigation_id=i.id))
 AND jsonb_array_length(CASE WHEN jsonb_typeof(d.report->'followup_plans')='array' THEN d.report->'followup_plans' ELSE '[]'::jsonb END)>0
 AND NOT EXISTS(SELECT 1 FROM investigation_followups f WHERE f.investigation_id=i.id AND f.source_diagnosis_id=d.id)))
 ORDER BY CASE WHEN EXISTS(SELECT 1 FROM investigation_followups f WHERE f.investigation_id=i.id AND f.status IN ('queued','collecting','diagnosing')) THEN 0 ELSE 1 END,i.id LIMIT 16`).Scan(&ids).Error; err != nil {
		return err
	}
	for _, id := range ids {
		if err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
			var root Investigation
			if err := tx.Raw("SELECT * FROM investigations WHERE id=? FOR UPDATE SKIP LOCKED", id).Scan(&root).Error; err != nil {
				return err
			}
			if root.ID == 0 {
				return nil
			}
			local := *s
			local.Repo = repository.New(tx)
			local.Tasks = &Tasks{Repo: local.Repo, Storage: s.Tasks.Storage, Control: s.Tasks.Control}
			if root.Status == "canceled" || root.Status == "failed" {
				return local.cancelInvestigationFollowups(ctx, tx, root, "DIAGNOSIS_CANCELED", "canceled")
			}
			if time.Since(root.CreatedAt) > 20*time.Minute {
				return local.cancelInvestigationFollowups(ctx, tx, root, "DIAGNOSIS_TIMEOUT", "failed")
			}
			u, err := local.Repo.User(ctx, root.RequestedBy)
			if err != nil {
				return err
			}
			if !u.CanWrite() {
				return local.cancelInvestigationFollowups(ctx, tx, root, "AUTHORIZATION_REVOKED", "failed")
			}
			if _, err = local.AuthorizeInvestigation(ctx, u, root.ID); err != nil {
				return local.cancelInvestigationFollowups(ctx, tx, root, "AUTHORIZATION_REVOKED", "failed")
			}
			var activeRows []InvestigationFollowup
			if err = tx.Where("investigation_id=? AND status IN ('queued','collecting','diagnosing')", root.ID).Order("id").Find(&activeRows).Error; err != nil {
				return err
			}
			if _, err = lockInvestigationFollowupTasks(ctx, tx, root, activeRows); err != nil {
				return err
			}
			if err = local.stageFollowupPlans(ctx, tx, root); err != nil {
				return err
			}
			var rows []InvestigationFollowup
			if err = tx.Where("investigation_id=? AND status IN ('queued','collecting','diagnosing')", id).Order("id").Limit(2).Find(&rows).Error; err != nil {
				return err
			}
			for _, row := range rows {
				if err = local.advanceFollowup(ctx, tx, u, root, &row); err != nil {
					row.Status = "failed"
					code := safeAgentError(err)
					row.ErrorCode = &code
					if cleanupErr := local.cancelInvestigationFollowupRows(ctx, tx, root, []InvestigationFollowup{row}, code, "failed"); cleanupErr != nil {
						return cleanupErr
					}
				}
				if err = tx.Model(&row).Updates(map[string]any{"status": row.Status, "task_id": row.TaskID, "diagnosis_id": row.DiagnosisID, "error_code": row.ErrorCode, "updated_at": time.Now().UTC()}).Error; err != nil {
					return err
				}
			}
			return nil
		}); err != nil {
			return err
		}
	}
	return nil
}

// stageFollowupPlans 只读取已经成功提交、来源仍有效的报告。
// 同一调查锁将并发发现收敛到最多两个分支，唯一键阻止重复创建。
func (s *Suggestions) stageFollowupPlans(ctx context.Context, tx *gorm.DB, root Investigation) error {
	var count int64
	if err := tx.Model(&InvestigationFollowup{}).Where("investigation_id=?", root.ID).Count(&count).Error; err != nil {
		return err
	}
	if count >= 2 {
		return nil
	}
	var runs []DiagnosisRun
	if err := tx.Raw(`SELECT DISTINCT d.* FROM diagnosis_runs d WHERE d.status='success' AND d.requested_by=? AND
 (d.id=? OR d.id IN (SELECT candidate_diagnosis_id FROM verification_runs WHERE id=?)
 OR d.id IN (SELECT diagnosis_id FROM investigation_followups WHERE investigation_id=?)) ORDER BY d.id LIMIT 6`, root.RequestedBy, root.DiagnosisID, root.VerificationID, root.ID).Scan(&runs).Error; err != nil {
		return err
	}
	for _, run := range runs {
		var report DiagnosisReport
		if json.Unmarshal(run.Report, &report) != nil || len(report.FollowupPlans) == 0 {
			continue
		}
		if err := s.ValidateDiagnosisSource(ctx, run); err != nil {
			continue
		}
		for _, plan := range report.FollowupPlans {
			if count >= 2 {
				return nil
			}
			if !plan.RequiresConfirmation {
				continue
			}
			data, _ := json.Marshal(plan)
			digest := repository.DigestBytes(data)
			var exists int64
			if err := tx.Model(&InvestigationFollowup{}).Where("investigation_id=? AND source_diagnosis_id=? AND plan_digest=?", root.ID, run.ID, digest).Count(&exists).Error; err != nil {
				return err
			}
			if exists > 0 {
				continue
			}
			row := InvestigationFollowup{InvestigationID: root.ID, ParentTaskID: run.TaskID, SourceDiagnosisID: run.ID, Plan: data, PlanDigest: digest, Status: "proposed"}
			if err := tx.Create(&row).Error; err != nil {
				return err
			}
			if err := tx.Create(ptrAuditDiagnosis(Audit(domain.User{ID: root.RequestedBy}, "investigation.followup_proposed", "investigation", fmt.Sprint(root.ID), "investigation-worker"))).Error; err != nil {
				return err
			}
			count++
		}
	}
	return nil
}

func (s *Suggestions) advanceFollowup(ctx context.Context, tx *gorm.DB, u domain.User, root Investigation, row *InvestigationFollowup) error {
	if time.Since(root.CreatedAt) > 20*time.Minute {
		return domain.Fail("DIAGNOSIS_TIMEOUT", "补证超出调查时间预算")
	}
	if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,417626))", root.RequestedBy).Error; err != nil {
		return err
	}
	var source DiagnosisRun
	if err := tx.Where("id=? AND status='success'", row.SourceDiagnosisID).First(&source).Error; err != nil {
		return err
	}
	var parent domain.Task
	if err := tx.Raw("SELECT * FROM tasks WHERE id=? FOR UPDATE", row.ParentTaskID).Scan(&parent).Error; err != nil {
		return err
	}
	if _, err := s.Repo.AuthorizeTaskWrite(ctx, u, row.ParentTaskID); err != nil {
		return err
	}
	if err := s.ValidateDiagnosisSource(ctx, source); err != nil {
		return err
	}
	if row.Status == "queued" {
		var p FollowupProposal
		if json.Unmarshal(row.Plan, &p) != nil {
			return domain.Fail("INVALID_EVIDENCE", "补证计划不可读")
		}
		input, err := s.validateCollectionPlan(ctx, u, root, p.Plan)
		if err != nil {
			return err
		}
		input.Name = fmt.Sprintf("调查#%d补证#%d / %s", root.ID, row.ID, p.Plan.Kind)
		task, _, err := s.Tasks.Create(ctx, u, input, fmt.Sprintf("investigation:%d:followup:%d", root.ID, row.ID), "investigation-worker")
		if err != nil {
			return err
		}
		row.TaskID = &task.ID
		row.Status = "collecting"
		return nil
	}
	if row.TaskID == nil {
		return domain.Fail("INVALID_STATE", "补证任务缺失")
	}
	task, err := s.Repo.AuthorizeTaskWrite(ctx, u, *row.TaskID)
	if err != nil {
		return err
	}
	if task.CollectionStatus == "FAILED" || task.CollectionStatus == "CANCELED" || task.AnalysisStatus == "FAILED" {
		return domain.Fail("COLLECTION_FAILED", "补证采集或分析失败")
	}
	if row.Status == "collecting" {
		if task.CollectionStatus != "COLLECTED" || task.AnalysisStatus != "SUCCESS" {
			return nil
		}
		diagnosis, err := s.RequestDiagnosis(ctx, u, task.ID, DiagnosisInput{Goal: root.Goal, Mode: "model", Consent: root.Consent, TelemetryServiceID: root.TelemetryServiceID}, "investigation-worker", fmt.Sprintf("followup:%d:diagnosis", row.ID))
		if err != nil {
			return err
		}
		row.DiagnosisID = &diagnosis.ID
		row.Status = "diagnosing"
		return nil
	}
	if row.DiagnosisID == nil {
		return domain.Fail("INVALID_STATE", "补证诊断缺失")
	}
	var diagnosis DiagnosisRun
	if err := tx.Where("id=?", *row.DiagnosisID).First(&diagnosis).Error; err != nil {
		return err
	}
	if diagnosis.Status == "failed" || diagnosis.Status == "canceled" {
		return domain.Fail("DIAGNOSIS_FAILED", "补证诊断未通过")
	}
	if diagnosis.Status == "success" {
		if err := s.ValidateDiagnosisSource(ctx, diagnosis); err != nil {
			return err
		}
		row.Status = "success"
	}
	return nil
}
