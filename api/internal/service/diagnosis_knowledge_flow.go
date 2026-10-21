// Package service：把版本化诊断方法与历史先验接入本次证据链。
// Skill 规定必须执行的工具，但不能扩展工具权限；历史案例不能单独支持当前假设。
package service

import (
	"context"
	"encoding/json"
	"strings"

	"drop/api/internal/domain"
)

// diagnosisKnowledgeContext 只补充程序实际返回的知识观测，不接受客户端拼接的 Skill 或案例。
// 这段上下文与 E1 一起进入本次 ReAct；知识读取占用相应报告协议的轨迹总预算。
func diagnosisKnowledgeContext(trace []DiagnosisStep) string {
	for i := 0; i < len(trace); i++ {
		if trace[i].Tool == "read_diagnostic_knowledge" {
			data, _ := json.Marshal(trace[i])
			return "\n本次已加载的诊断方法与历史先验（不是本次测量事实）：" + string(data)
		}
	}
	return ""
}

func knowledgeFromDiagnosisTrace(trace []DiagnosisStep) (*DiagnosticKnowledge, error) {
	for i := len(trace) - 1; i >= 0; i-- {
		if trace[i].Tool != "read_diagnostic_knowledge" {
			continue
		}
		var knowledge DiagnosticKnowledge
		if json.Unmarshal(trace[i].Observation, &knowledge) != nil || !knowledge.PriorOnly {
			return nil, domain.Fail("INVALID_EVIDENCE", "诊断知识观测无效")
		}
		return &knowledge, nil
	}
	return nil, nil
}

func validateDiagnosticKnowledgeSnapshot(trace []DiagnosisStep, fresh DiagnosticKnowledge) error {
	loaded, err := knowledgeFromDiagnosisTrace(trace)
	if err != nil || loaded == nil {
		return err
	}
	a, _ := json.Marshal(loaded)
	b, _ := json.Marshal(fresh)
	if string(a) != string(b) {
		return domain.Fail("KNOWLEDGE_CHANGED", "使用的诊断经验已撤销、失效或来源改变，请重新诊断")
	}
	return nil
}

// validateDiagnosticSkillUse 将方法中的必需工具变成可验证条件。
// 工具返回“不适用/缺少调用树”仍是一次真实观测，模型应据此陈述边界，不能编造补齐。
func validateDiagnosticSkillUse(trace []DiagnosisStep) error {
	knowledge, err := knowledgeFromDiagnosisTrace(trace)
	if err != nil || knowledge == nil {
		return err
	}
	used := map[string]bool{}
	for _, step := range trace {
		used[step.Tool] = true
	}
	for _, skill := range knowledge.Skills {
		for _, tool := range skill.RequiredTools {
			if !used[tool] {
				return domain.Fail("AI_INSUFFICIENT_EVIDENCE", "尚未完成已匹配诊断Skill要求的工具检查")
			}
		}
	}
	return nil
}

// validateLoadedDiagnosticKnowledge 在模型结束后重新读取当前权限、撤销、有效期与来源版本。
// 已加载案例若失效，整份新报告不能继续附带旧案例的可信标签；调用方须重新发起诊断。
func (s *Suggestions) validateLoadedDiagnosticKnowledge(ctx context.Context, run DiagnosisRun, trace []DiagnosisStep) (*DiagnosticKnowledge, error) {
	loaded, err := knowledgeFromDiagnosisTrace(trace)
	if err != nil || loaded == nil {
		return loaded, err
	}
	u, err := s.Repo.User(ctx, run.RequestedBy)
	if err != nil {
		return nil, err
	}
	fresh, err := s.ReadDiagnosticKnowledge(ctx, u, run, json.RawMessage(`{}`), run.KnowledgeMode == "experience")
	if err != nil {
		return nil, err
	}
	if err := validateDiagnosticKnowledgeSnapshot(trace, fresh); err != nil {
		return nil, err
	}
	return loaded, nil
}

func diagnosisEvidenceID(trace []DiagnosisStep, tool string) string {
	for _, step := range trace {
		if step.Tool == tool && strings.HasPrefix(step.EvidenceID, "E") {
			return step.EvidenceID
		}
	}
	return ""
}
