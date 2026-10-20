package service

import (
	"context"
	"encoding/json"
	"fmt"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

// Human-confirmed follow-up collection. The source task remains locked through
// creation, fencing concurrent reanalysis, retry or deletion. Task creation and
// the provenance event/audit are committed in the same transaction.
func (s *Suggestions) CollectDiagnosis(ctx context.Context, u domain.User, taskID, runID int64, key, request string) (domain.Task, bool, error) {
	var created domain.Task
	var replay bool
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		r := repository.New(tx)
		var task domain.Task
		if err := tx.Raw("SELECT * FROM tasks WHERE id=? FOR UPDATE", taskID).Scan(&task).Error; err != nil {
			return err
		}
		var err error
		task, err = r.AuthorizeTaskWrite(ctx, u, taskID)
		if err != nil {
			return err
		}
		var run DiagnosisRun
		if err := tx.Where("id=? AND task_id=? AND status='success'", runID, taskID).First(&run).Error; err != nil {
			return domain.Fail("NOT_FOUND", "未找到成功诊断")
		}
		a, err := r.LatestAttempt(ctx, taskID)
		if err != nil {
			return err
		}
		if a.ID != run.AttemptID || task.AnalysisStatus != "SUCCESS" || task.CollectionStatus != "COLLECTED" {
			return domain.Fail("STALE_ATTEMPT", "采集或分析已更新，请重新诊断")
		}
		scoped := *s
		scoped.Repo = r
		if err := scoped.ValidateDiagnosisSource(ctx, run); err != nil {
			return err
		}
		var report DiagnosisReport
		if json.Unmarshal(run.Report, &report) != nil || report.Plan == nil || report.Plan.Kind != task.Kind || task.AgentID == nil {
			return domain.Fail("INVALID_PARAMETERS", "该诊断没有可执行的复测草案")
		}
		var p domain.Parameters
		if json.Unmarshal(task.Parameters, &p) != nil || p.Source == "import" {
			return domain.Fail("INVALID_PARAMETERS", "导入结果请重新上传对照数据")
		}
		p.Source = ""
		p.Traceparent = ""
		p.DurationSeconds = report.Plan.DurationSeconds
		p.FrequencyHz = report.Plan.FrequencyHz
		tasks := &Tasks{Repo: r, Storage: s.Tasks.Storage, Control: s.Tasks.Control}
		created, replay, err = tasks.Create(ctx, u, domain.CreateTask{Name: fmt.Sprintf("诊断#%d复测 / 任务#%d", run.ID, taskID), Kind: task.Kind, AgentID: *task.AgentID, Parameters: p, GroupID: task.GroupID, Visibility: task.Visibility}, key, request)
		if err != nil {
			return err
		}
		if replay {
			return nil
		}
		event := domain.EventFor(task, &run.AttemptID, "DIAGNOSIS_VERIFICATION_CREATED", map[string]any{"diagnosis_id": fmt.Sprint(run.ID), "verification_task_id": fmt.Sprint(created.ID)})
		if err := tx.Create(&event).Error; err != nil {
			return err
		}
		verification := VerificationRun{DiagnosisID: run.ID, BaselineTaskID: taskID, CandidateTaskID: created.ID, RequestedBy: u.ID, Status: "waiting"}
		if err := tx.Create(&verification).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(u, "diagnosis.collect", "task", fmt.Sprint(taskID), request))).Error
	})
	return created, replay, err
}
