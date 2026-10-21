// 成功报告提交前一次性固定所有来源任务，避免不同知识/信号分支以不同顺序追加行锁。
// 锁序统一为复测关系→升序任务→知识记录→授权行，与复测工作器保持一致。
package service

import (
	"context"
	"slices"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

func (s *Suggestions) pinDiagnosisScope(ctx context.Context, tx *gorm.DB, run DiagnosisRun, trace []DiagnosisStep) error {
	var verification VerificationRun
	if err := tx.Raw("SELECT * FROM verification_runs WHERE candidate_diagnosis_id=? FOR UPDATE", run.ID).Scan(&verification).Error; err != nil {
		return err
	}
	ids := []int64{run.TaskID}
	if verification.ID > 0 {
		ids = append(ids, verification.BaselineTaskID, verification.CandidateTaskID)
	}
	knowledge, err := knowledgeFromDiagnosisTrace(trace)
	if err != nil {
		return err
	}
	if knowledge != nil {
		if len(knowledge.Cases) > 3 {
			return domain.Fail("INVALID_EVIDENCE", "案例引用超过预算")
		}
		caseIDs := []int64{}
		for _, prior := range knowledge.Cases {
			caseIDs = append(caseIDs, prior.ID)
		}
		if len(caseIDs) > 0 {
			var sourceIDs []int64
			if err := tx.Model(&DiagnosticCase{}).Where("id IN ?", caseIDs).Pluck("task_id", &sourceIDs).Error; err != nil {
				return err
			}
			ids = append(ids, sourceIDs...)
		}
	}
	slices.Sort(ids)
	ids = slices.Compact(ids)
	var locked []int64
	if err := tx.Raw("SELECT id FROM tasks WHERE id IN ? ORDER BY id FOR UPDATE", ids).Scan(&locked).Error; err != nil {
		return err
	}
	if len(locked) != len(ids) {
		return domain.Fail("STALE_ATTEMPT", "报告来源任务已变化")
	}
	return nil
}
