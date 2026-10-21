// 本模块把诊断提出的补证计划转换为可审阅的调查分支。
// 模型只能建议同一授权目标的其他采集器；确认、任务创建及再次诊断均由持久化状态机完成。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

type FollowupProposal struct {
	Plan                 CollectionPlan `json:"plan"`
	EvidenceIDs          []string       `json:"evidence_ids"`
	RequiresConfirmation bool           `json:"requires_confirmation"`
}

type InvestigationFollowup struct {
	ID                int64           `json:"id,string"`
	InvestigationID   int64           `json:"investigation_id,string"`
	ParentTaskID      int64           `json:"parent_task_id,string"`
	SourceDiagnosisID int64           `json:"source_diagnosis_id,string"`
	Plan              json.RawMessage `json:"plan"`
	PlanDigest        string          `json:"plan_digest"`
	Status            string          `json:"status"`
	TaskID            *int64          `json:"task_id,string,omitempty"`
	DiagnosisID       *int64          `json:"diagnosis_id,string,omitempty"`
	ErrorCode         *string         `json:"error_code,omitempty"`
	CreatedAt         time.Time       `json:"created_at"`
	UpdatedAt         time.Time       `json:"updated_at"`
}

// boundInvestigation 只沿数据库中的真实来源关系查找调查，不从问题文本猜测目标。
// 复测、补证分支均继承根调查身份；歧义关系必须拒绝，不能随便选一条。
func (s *Suggestions) boundInvestigation(ctx context.Context, run DiagnosisRun) (Investigation, bool, error) {
	var rows []Investigation
	err := s.Repo.Context(ctx).Raw(`SELECT DISTINCT i.* FROM investigations i
 LEFT JOIN verification_runs v ON v.id=i.verification_id
 LEFT JOIN investigation_followups f ON f.investigation_id=i.id
 WHERE (i.baseline_task_id=? OR v.candidate_task_id=? OR f.task_id=?)
 AND i.requested_by=? LIMIT 2`, run.TaskID, run.TaskID, run.TaskID, run.RequestedBy).Scan(&rows).Error
	if err != nil || len(rows) == 0 {
		return Investigation{}, false, err
	}
	if len(rows) != 1 {
		return Investigation{}, false, domain.Fail("INVALID_EVIDENCE", "诊断对应多个调查")
	}
	return rows[0], true, nil
}

func (s *Suggestions) followupCatalog(ctx context.Context, run DiagnosisRun) ([]map[string]any, error) {
	items := []map[string]any{}
	i, found, err := s.boundInvestigation(ctx, run)
	if err != nil || !found {
		return items, err
	}
	u, err := s.Repo.User(ctx, run.RequestedBy)
	if err != nil {
		return items, err
	}
	a, err := s.Repo.AuthorizedAgent(ctx, u, i.AgentID, true)
	if err != nil {
		return items, err
	}
	var names []string
	if json.Unmarshal(a.Capabilities, &names) != nil {
		return items, domain.Fail("INVALID_EVIDENCE", "采集能力不可读")
	}
	for _, name := range names {
		k, ok := domain.Kind(name)
		if !ok || !k.AgentRunner || slices.Contains([]string{"SCRIPT", "BOLT", "BOLT_PROFILE", "COMPOSITE"}, name) {
			continue
		}
		items = append(items, map[string]any{"kind": k.ID, "default_duration_seconds": k.DefaultDuration, "default_frequency_hz": k.DefaultFrequency})
	}
	return items, nil
}

// 资源采集没有函数热点，真实测量位于程序生成的summary.resources。
// 只有同一资源摘要的固定来源、单位、正采样数与全部实际峰值都有效时，才能把该E编号用作补证依据。
// 普通CPU摘要、空resources、知识和登记拓扑均不能借此成为测量证据。
func followupResourceMeasurement(step DiagnosisStep) bool {
	if step.Tool != "read_profile_summary" {
		return false
	}
	body, err := diagnosisDecodeObject(step.Observation)
	if err != nil || body["kind"] != "RESOURCE_SNAPSHOT" {
		return false
	}
	resources, ok := body["resources"].(map[string]any)
	if !ok {
		return false
	}
	peaks, ok := resources["peaks"].(map[string]any)
	if !ok || len(peaks) == 0 || len(peaks) > len(diagnosisResourceFields) {
		return false
	}
	for name, value := range peaks {
		if !diagnosisResourceFactScalar(body, "/resources/peaks/"+name, value) {
			return false
		}
	}
	return true
}

func (s *Suggestions) proposeFollowup(ctx context.Context, run DiagnosisRun, args json.RawMessage, trace []DiagnosisStep) (json.RawMessage, error) {
	var in struct {
		CollectionPlan
		EvidenceIDs []string `json:"evidence_ids"`
	}
	if !decodeAgentJSON(args, &in) || strings.TrimSpace(string(args)) == "null" || len(in.EvidenceIDs) < 1 || len(in.EvidenceIDs) > 4 {
		return nil, domain.Fail("AI_TOOL_REJECTED", "追加采集需要有效计划和本轮证据")
	}
	for _, id := range in.EvidenceIDs {
		if !slices.ContainsFunc(trace, func(step DiagnosisStep) bool {
			return step.EvidenceID == id && (slices.Contains([]string{"inspect_hotspots", "inspect_call_paths", "check_collection_quality", "query_business_signals"}, step.Tool) || followupResourceMeasurement(step))
		}) {
			return nil, domain.Fail("AI_TOOL_REJECTED", "追加采集必须引用本轮实际观测")
		}
	}
	if slices.Contains([]string{"SCRIPT", "BOLT", "BOLT_PROFILE", "COMPOSITE"}, in.Kind) {
		return nil, domain.Fail("AI_TOOL_REJECTED", "此类工具需要独立配置，不接受自动补证建议")
	}
	i, found, err := s.boundInvestigation(ctx, run)
	if err != nil {
		return nil, err
	}
	if !found {
		return json.Marshal(map[string]any{"available": false, "reason": "investigation_required"})
	}
	if i.Status == "canceled" || i.Status == "failed" {
		return nil, domain.Fail("INVALID_STATE", "原调查已结束或取消")
	}
	var n int64
	if err := s.Repo.Context(ctx).Model(&InvestigationFollowup{}).Where("investigation_id=?", i.ID).Count(&n).Error; err != nil {
		return nil, err
	}
	if n >= 2 {
		return json.Marshal(map[string]any{"available": false, "reason": "followup_budget_exhausted"})
	}
	u, err := s.Repo.User(ctx, run.RequestedBy)
	if err != nil {
		return nil, err
	}
	current, err := s.Repo.Task(ctx, u, run.TaskID)
	if err != nil {
		return nil, err
	}
	for _, step := range trace {
		if slices.Contains(in.EvidenceIDs, step.EvidenceID) && step.Tool == "read_profile_summary" && current.Kind != "RESOURCE_SNAPSHOT" {
			return nil, domain.Fail("AI_TOOL_REJECTED", "资源测量摘要必须与当前任务采集类型一致")
		}
	}
	if _, err = s.validateCollectionPlan(ctx, u, i, in.CollectionPlan); err != nil {
		return json.Marshal(map[string]any{"available": true, "validated": false, "error_code": safeAgentError(err), "next": "按实际采集能力和参数修正草案；缺少端口或工具配置时说明需要配置，不猜测。"})
	}
	if in.Kind == current.Kind {
		return json.Marshal(map[string]any{"available": true, "validated": false, "reason": "current_collector_already_observed", "next": "当前采集器已有观测；同类复采请调用propose_verification提出受控复测，其他信号才使用补证采集。"})
	}
	return json.Marshal(FollowupProposal{Plan: in.CollectionPlan, EvidenceIDs: in.EvidenceIDs, RequiresConfirmation: true})
}

func followupProposals(trace []DiagnosisStep) []FollowupProposal {
	items := []FollowupProposal{}
	for _, step := range trace {
		if step.Tool != "propose_followup_collection" {
			continue
		}
		var p FollowupProposal
		if json.Unmarshal(step.Observation, &p) == nil && p.RequiresConfirmation && p.Plan.Kind != "" && len(items) < 2 {
			items = append(items, p)
		}
	}
	return items
}

func (s *Suggestions) ListInvestigationFollowups(ctx context.Context, u domain.User, id int64) ([]InvestigationFollowup, error) {
	items := []InvestigationFollowup{}
	if _, err := s.AuthorizeInvestigation(ctx, u, id); err != nil {
		return items, err
	}
	err := s.Repo.Context(ctx).Where("investigation_id=?", id).Order("id").Limit(2).Find(&items).Error
	if err == nil {
		visible := []InvestigationFollowup{}
		for _, item := range items {
			var source DiagnosisRun
			if s.Repo.Context(ctx).Where("id=?", item.SourceDiagnosisID).First(&source).Error == nil && s.AuthorizeDiagnosisSignals(ctx, u, source) == nil {
				visible = append(visible, item)
			}
		}
		items = visible
	}
	return items, err
}

// DecideInvestigationFollowup 把审阅决定和审计放在同一事务。
// 统一按调查→来源任务→分支加锁；确认后仅入队，后台仍重新核验能力、目标启动身份和来源轮次。
func (s *Suggestions) DecideInvestigationFollowup(ctx context.Context, u domain.User, id, followupID int64, confirm bool, request string) (InvestigationFollowup, error) {
	var out InvestigationFollowup
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		local := *s
		local.Repo = repository.New(tx)
		var root Investigation
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", id).First(&root).Error; err != nil {
			return err
		}
		current, err := local.Repo.User(ctx, u.ID)
		if err != nil {
			return err
		}
		if _, err = local.AuthorizeInvestigation(ctx, current, id); err != nil {
			return err
		}
		if !current.CanWrite() || root.Status == "canceled" || root.Status == "failed" {
			return domain.Fail("INVALID_STATE", "当前调查不接受追加采集")
		}
		if err = tx.Where("id=? AND investigation_id=?", followupID, id).First(&out).Error; err != nil {
			return err
		}
		var parent domain.Task
		if err = tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", out.ParentTaskID).First(&parent).Error; err != nil {
			return err
		}
		if err = tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=? AND investigation_id=?", followupID, id).First(&out).Error; err != nil {
			return err
		}
		if out.Status != "proposed" {
			if confirm && out.Status != "rejected" && out.Status != "canceled" || !confirm && out.Status == "rejected" {
				return nil
			}
			return domain.Fail("INVALID_STATE", "此补证计划已经处理")
		}
		if confirm {
			if time.Since(root.CreatedAt) > 20*time.Minute {
				return domain.Fail("DIAGNOSIS_TIMEOUT", "追加采集超出调查时间预算")
			}
			var diagnosis DiagnosisRun
			if err = tx.Where("id=? AND status='success'", out.SourceDiagnosisID).First(&diagnosis).Error; err != nil {
				return err
			}
			if err = local.ValidateDiagnosisSource(ctx, diagnosis); err != nil {
				return err
			}
			var proposal FollowupProposal
			if json.Unmarshal(out.Plan, &proposal) != nil {
				return domain.Fail("INVALID_EVIDENCE", "补证计划不可读")
			}
			if _, err = local.validateCollectionPlan(ctx, current, root, proposal.Plan); err != nil {
				return err
			}
			out.Status = "queued"
		} else {
			out.Status = "rejected"
		}
		out.UpdatedAt = time.Now().UTC()
		if err = tx.Model(&out).Updates(map[string]any{"status": out.Status, "updated_at": out.UpdatedAt}).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(current, "investigation.followup_decision", "investigation", fmt.Sprint(id), request))).Error
	})
	return out, err
}
