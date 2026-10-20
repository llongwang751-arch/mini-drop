package service

import (
	"context"
	"encoding/json"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

func (s *Suggestions) diagnosisAgentScope(ctx context.Context, run DiagnosisRun, owner string, first json.RawMessage, invoke func(string, json.RawMessage) (json.RawMessage, error), trace *[]DiagnosisStep) (*DiagnosisReport, error) {
	used := map[string]bool{"read_profile_summary": true}
	var initial DiagnosisStep
	_ = json.Unmarshal(first, &initial)
	var summary struct {
		VerificationAvailable bool `json:"verification_available"`
	}
	_ = json.Unmarshal(initial.Observation, &summary)
	spec := agentRunSpec{
		Phase: "diagnosis", Model: s.Config.Model, SystemPrompt: diagnosisSystem,
		Messages: []diagnosisMessage{{Role: "user", Content: "诊断问题：" + run.Goal + "\n已授权的初始工具证据：" + string(first)}}, Tools: diagnosisDefinitions(),
		Fence: func(work context.Context) error {
			return s.Repo.Context(work).Transaction(func(tx *gorm.DB) error { _, err := s.diagnosisFence(work, tx, run, owner); return err })
		},
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
			report, err := validateDiagnosisReport(content, *trace)
			if err != nil {
				return nil, err
			}
			return json.Marshal(report)
		},
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
