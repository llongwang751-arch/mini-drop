// Package service：把现有诊断证据与业务校验接入独立 AgentScope runner。
// Python 负责真正的 ReAct 循环，Go 闭包负责读取授权证据与验证报告，最终运行证明只能由控制面附加。
package service

import (
	"context"
	"encoding/json"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

// diagnosisAgentScope 把初始脱敏证据交给 AgentScope，并将工具执行与最终验收留在 Go 闭包中。
// 必要热点、质量和复测比较必须真实调用；runner 返回的 JSON 只有与 Go 保存的 accepted 结果一致才能形成报告。
func (s *Suggestions) diagnosisAgentScope(ctx context.Context, run DiagnosisRun, owner string, first json.RawMessage, invoke func(string, json.RawMessage) (json.RawMessage, error), trace *[]DiagnosisStep) (*DiagnosisReport, error) {
	used := map[string]bool{"read_profile_summary": true}
	for _, step := range *trace {
		used[step.Tool] = true
	}
	var initial DiagnosisStep
	_ = json.Unmarshal(first, &initial)
	var summary struct {
		VerificationAvailable bool `json:"verification_available"`
	}
	_ = json.Unmarshal(initial.Observation, &summary)
	spec := agentRunSpec{
		Phase: "diagnosis", Model: s.Config.Model, SystemPrompt: diagnosisSystemForProtocol(run.ReportProtocol),
		Messages: []diagnosisMessage{{Role: "user", Content: "诊断问题：" + run.Goal + "\n已授权的初始工具证据：" + string(first) + diagnosisKnowledgeContext(*trace)}}, Tools: diagnosisDefinitions(),
		Fence: func(work context.Context) error {
			return s.Repo.Context(work).Transaction(func(tx *gorm.DB) error { _, err := s.diagnosisFence(work, tx, run, owner); return err })
		},
		Checkpoint: NewAgentCheckpointStore(s.Repo, AgentCheckpointBinding{ScopeType: "diagnosis", ScopeID: run.ID, InputDigest: run.InputDigest}, func(work context.Context, tx *gorm.DB) error {
			_, err := s.diagnosisFence(work, tx, run, owner)
			return err
		}),
		Invoke: func(_ context.Context, name string, arguments json.RawMessage) (agentToolResult, error) {
			observation, err := invoke(name, arguments)
			if err == nil {
				used[name] = true
			}
			return agentToolResult{Observation: observation}, err
		},
		Finish: func(_ context.Context, content string) (json.RawMessage, error) {
			if !used["inspect_hotspots"] || !used["check_collection_quality"] || summary.VerificationAvailable && !used["compare_profiles"] {
				return nil, domain.Fail("AI_INSUFFICIENT_EVIDENCE", "未完成必要的热点、质量或复测检查")
			}
			var report *DiagnosisReport
			var err error
			if run.ReportProtocol >= 2 {
				report, err = validateDiagnosisModelReportV2(content, *trace)
			} else {
				report, err = validateDiagnosisReport(content, *trace)
			}
			if err != nil {
				return nil, err
			}
			return json.Marshal(report)
		},
	}
	if run.ReportProtocol < 2 {
		legacyBudget := agentBudget{5, 7, 2, 150000}
		spec.Budget = &legacyBudget
	}
	result, metadata, err := s.Runner.Run(ctx, spec)
	if err != nil {
		return nil, err
	}
	var report DiagnosisReport
	if json.Unmarshal(result, &report) != nil {
		return nil, domain.Fail("AI_INVALID_REPORT", "诊断报告无效")
	}
	report.AgentRuntime = metadata
	return &report, nil
}
