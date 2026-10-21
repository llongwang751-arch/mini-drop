// 终态提交需要把“结束前重新读取”与真正写报告之间的窗口收住。
// 本文件只锁本次已使用的来源并重核本地事实；登记执行器的网络 describe 已在前置重读完成，锁内不访问外部服务。
package service

import (
	"context"
	"encoding/json"
	"slices"
	"sort"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// PinDiagnosticKnowledge 必须在 finishDiagnosis 的同一事务内、diagnosisFence 之前调用。
// 锁序沿用 verification→升序 tasks→case；随后锁住此次授权所依赖的用户、共享和成员行。
// 这样案例撤销、来源重分析或已存在授权行的撤销会与成功提交串行；不可变登记版本仍以前置只读核验为边界。
func (s *Suggestions) PinDiagnosticKnowledge(ctx context.Context, tx *gorm.DB, run DiagnosisRun, trace []DiagnosisStep) error {
	loaded, err := knowledgeFromDiagnosisTrace(trace)
	if err != nil {
		return err
	}
	if err = s.pinDiagnosticPlaybooks(ctx, tx, run, loaded); err != nil {
		return err
	}
	if loaded == nil || len(loaded.Cases) == 0 {
		return nil
	}
	changed := func() error {
		return domain.Fail("KNOWLEDGE_CHANGED", "使用的诊断经验已撤销、失效或来源改变，请重新诊断")
	}
	if len(loaded.Cases) > 3 {
		return changed()
	}
	ids := []int64{}
	for _, prior := range loaded.Cases {
		if prior.ID <= 0 || slices.Contains(ids, prior.ID) || !prior.PriorOnly {
			return changed()
		}
		ids = append(ids, prior.ID)
	}
	sort.Slice(ids, func(i, j int) bool { return ids[i] < ids[j] })
	var verification VerificationRun
	if err = tx.WithContext(ctx).Raw("SELECT * FROM verification_runs WHERE candidate_diagnosis_id=? FOR UPDATE", run.ID).Scan(&verification).Error; err != nil {
		return err
	}
	var cases []DiagnosticCase
	if err = tx.WithContext(ctx).Where("id IN ?", ids).Order("id").Find(&cases).Error; err != nil {
		return err
	}
	if len(cases) != len(ids) {
		return changed()
	}
	taskIDs := []int64{run.TaskID}
	if verification.ID > 0 {
		taskIDs = append(taskIDs, verification.BaselineTaskID, verification.CandidateTaskID)
	}
	for _, c := range cases {
		taskIDs = append(taskIDs, c.TaskID)
	}
	sort.Slice(taskIDs, func(i, j int) bool { return taskIDs[i] < taskIDs[j] })
	taskIDs = slices.Compact(taskIDs)
	var tasks []domain.Task
	if err = tx.WithContext(ctx).Clauses(clause.Locking{Strength: "UPDATE"}).Where("id IN ?", taskIDs).Order("id").Find(&tasks).Error; err != nil {
		return err
	}
	if len(tasks) != len(taskIDs) {
		return changed()
	}
	if err = tx.WithContext(ctx).Clauses(clause.Locking{Strength: "SHARE"}).Where("id IN ?", ids).Order("id").Find(&cases).Error; err != nil {
		return err
	}
	var current domain.User
	if err = tx.WithContext(ctx).Clauses(clause.Locking{Strength: "SHARE"}).Where("id=?", run.RequestedBy).First(&current).Error; err != nil {
		return err
	}
	if err = pinDiagnosticAuthorization(ctx, tx, current, taskIDs); err != nil {
		return err
	}
	r := repository.New(tx)
	if _, err = r.AuthorizeTaskWrite(ctx, current, run.TaskID); err != nil {
		return changed()
	}
	currentProject, currentRevision, err := diagnosticProjectContext(ctx, tx, run.TaskID, run.ID)
	if err != nil {
		return changed()
	}
	byID := map[int64]DiagnosticCase{}
	for _, c := range cases {
		byID[c.ID] = c
	}
	for _, prior := range loaded.Cases {
		c, exists := byID[prior.ID]
		if !exists || c.OwnerID != current.ID || c.Status != "active" || c.DiagnosisID != prior.SourceDiagnosisID || c.DiagnosisID == run.ID || c.Outcome != prior.Outcome || c.CreatedAt.After(run.CreatedAt) || c.TaskKind == "" || c.ProjectID != currentProject || c.SourceRevision != currentRevision {
			return changed()
		}
		_, task, source, e := diagnosticCaseSource(ctx, tx, current, c.TaskID, c.DiagnosisID, false)
		if e != nil || source.AttemptID != c.AttemptID || source.AnalysisJobID != c.AnalysisJobID || source.AnalysisRun != c.AnalysisRun || task.Kind != c.TaskKind {
			return changed()
		}
		var currentTask domain.Task
		for _, t := range tasks {
			if t.ID == run.TaskID {
				currentTask = t
			}
		}
		if currentTask.Kind != c.TaskKind || diagnosticAgentID(currentTask) != c.AgentID || diagnosticAgentID(task) != c.AgentID {
			return changed()
		}
		project, revision, e := diagnosticProjectContext(ctx, tx, c.TaskID, c.DiagnosisID)
		if e != nil || project != c.ProjectID || revision != c.SourceRevision {
			return changed()
		}
		if c.Outcome == "verified_repair" {
			if c.CodeRepairID == nil {
				return changed()
			}
			if _, e = diagnosticVerifiedRepair(ctx, tx, current, c.TaskID, c.DiagnosisID, *c.CodeRepairID, c.SourceRevision); e != nil {
				return changed()
			}
		} else if c.Outcome != "reviewed_correction" || diagnosticCorrectionText[c.CorrectionCode] == "" {
			return changed()
		}
		var refs []DiagnosticSkillRef
		if json.Unmarshal(c.SkillRefs, &refs) != nil {
			return changed()
		}
		a, _ := json.Marshal(refs)
		b, _ := json.Marshal(prior.SkillRefs)
		if string(a) != string(b) {
			return changed()
		}
	}
	// PostgreSQL now() 固定为事务开始时刻；使用 clock_timestamp 防止等锁期间已过期的案例被接受。
	var active int64
	if err = tx.WithContext(ctx).Model(&DiagnosticCase{}).Where("id IN ? AND status='active' AND expires_at>clock_timestamp()", ids).Count(&active).Error; err != nil {
		return err
	}
	if active != int64(len(ids)) {
		return changed()
	}
	return nil
}

func diagnosticAgentID(task domain.Task) string {
	if task.AgentID == nil {
		return ""
	}
	return *task.AgentID
}

// pinDiagnosticAuthorization 只锁本次来源可能使用的授权行，不锁全平台的权限表。
// 共享和组成员在普通查询中是 EXISTS 条件；锁住这些实际行后再授权，可阻止“读后撤权、写前仍沿用旧授权”。
func pinDiagnosticAuthorization(ctx context.Context, tx *gorm.DB, u domain.User, taskIDs []int64) error {
	if u.IsPlatformAdmin() {
		return nil
	}
	var shareRows []struct {
		ID      int64
		GroupID *string
	}
	if err := tx.WithContext(ctx).Raw(`SELECT id,group_id FROM task_shares WHERE task_id IN ?
 AND (user_id=? OR group_id IN (SELECT group_id FROM group_members WHERE user_id=?)) ORDER BY id FOR SHARE`, taskIDs, u.ID, u.ID).Scan(&shareRows).Error; err != nil {
		return err
	}
	var groups []struct{ GroupID string }
	return tx.WithContext(ctx).Raw(`SELECT group_id FROM group_members WHERE user_id=? AND
 (group_id IN (SELECT group_id FROM tasks WHERE id IN ? AND group_id IS NOT NULL)
 OR group_id IN (SELECT group_id FROM task_shares WHERE task_id IN ? AND group_id IS NOT NULL)) ORDER BY group_id FOR SHARE`, u.ID, taskIDs, taskIDs).Scan(&groups).Error
}
