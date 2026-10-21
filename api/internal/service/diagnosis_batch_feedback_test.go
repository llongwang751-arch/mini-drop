// 回归形态来自2026-10-21受控DeepSeek诊断66：原首候选UTF-8摘要为
// 091c554f68da9c8485b6a4676568af4eb94eadcc45552ff92a6324cf82481b2a。
// 真实形态是E5误写扁平路径、两条E12补证facts、E12冒充复测计划；此处明确为合成缩减，
// 数值改为最小样本，不包含原符号/日志/目标，不把离线回归称为新模型效果验收。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
)

const reducedDiagnosis66Batch = `{"report_version":2,"facts":[{"id":"F1","evidence_id":"E1","path":"/measurement_unit","operator":"eq","value":"samples","unit":"unit"},{"id":"F7","evidence_id":"E5","path":"/occurrences/0/self_weight","operator":"eq","value":1,"unit":"samples"},{"id":"F8","evidence_id":"E5","path":"/ancestors_truncated","operator":"eq","value":false,"unit":"boolean"},{"id":"F17","evidence_id":"E12","path":"/duration_seconds","operator":"eq","value":5,"unit":"seconds"},{"id":"F18","evidence_id":"E12","path":"/frequency_hz","operator":"eq","value":2,"unit":"hertz"}],"hypotheses":[{"kind":"insufficient_evidence","fact_ids":["F1","F7"],"evidence_ids":["E1","E5"]}],"verification_requests":[{"origin":"tool_plan","kind":"repeat_collection","source_evidence_id":"E12","duration_seconds":5,"frequency_hz":2}]}`

func reducedDiagnosis66Trace() []DiagnosisStep {
	return []DiagnosisStep{
		{EvidenceID: "E1", Tool: "read_profile_summary", Observation: json.RawMessage(`{"kind":"PYTHON_CPU","measurement_unit":"samples","total_weight":1,"node_count":2}`)},
		{EvidenceID: "E3", Tool: "inspect_hotspots", Observation: json.RawMessage(`{"top_functions":[{"rank":1,"self_percent":100,"total_percent":100,"symbol_hint":"unknown"}]}`)},
		{EvidenceID: "E5", Tool: "inspect_call_paths", Observation: json.RawMessage(`{"tree_truncated":false,"occurrences":[{"self_weight":1,"ancestors_truncated":false}]}`)},
		{EvidenceID: "E12", Tool: "propose_followup_collection", Observation: json.RawMessage(`{"plan":{"kind":"RESOURCE_SNAPSHOT","duration_seconds":5,"frequency_hz":2},"requires_confirmation":true}`)},
	}
}

func editDiagnosis66(t *testing.T, edit func(map[string]any)) string {
	t.Helper()
	var report map[string]any
	if err := json.Unmarshal([]byte(reducedDiagnosis66Batch), &report); err != nil {
		t.Fatal(err)
	}
	edit(report)
	raw, err := json.Marshal(report)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func repairDiagnosis66(report map[string]any, callpath, followupFacts, verification bool) {
	facts := report["facts"].([]any)
	if callpath {
		facts[2].(map[string]any)["path"] = "/occurrences/0/ancestors_truncated"
	}
	if followupFacts {
		report["facts"] = facts[:3]
	}
	if verification {
		plan := report["verification_requests"].([]any)[0].(map[string]any)
		plan["origin"] = "model_proposal"
		delete(plan, "source_evidence_id")
	}
}

func batchFeedbackObject(t *testing.T, err error) map[string]any {
	t.Helper()
	provider, ok := err.(diagnosisReportBatchFeedbackProvider)
	if !ok {
		t.Fatalf("多错误未使用专用batch类型：%T", err)
	}
	raw, valid := provider.safeDiagnosisReportBatchFeedback().safeJSON()
	if !valid || len(raw) > diagnosisReportBatchMaxBytes || len(raw) > 4096 {
		t.Fatalf("反馈不安全或突破现有SDK长度：%d", len(raw))
	}
	var result map[string]any
	if json.Unmarshal([]byte(raw), &result) != nil {
		t.Fatal("批量反馈不是完整JSON")
	}
	return result
}

func TestDiagnosisBatchFeedbackControlled66ShowsAllIndependentErrors(t *testing.T) {
	report, err := validateDiagnosisModelReportV2(reducedDiagnosis66Batch, reducedDiagnosis66Trace())
	if err == nil || report != nil || safeAgentError(err) != "AI_INVALID_REPORT" {
		t.Fatal("多错误报告返回了局部成功", report, err)
	}
	batch := batchFeedbackObject(t, err)
	items := batch["errors"].([]any)
	if len(items) != 4 || batch["truncated"] != false {
		t.Fatal("66的4个独立错误未完整返回", batch)
	}
	want := map[string]bool{"facts/2/fact_path_not_allowed": true, "facts/3/fact_path_not_allowed": true, "facts/4/fact_path_not_allowed": true, "verification_requests/0/verification_source_tool_mismatch": true}
	for _, item := range items {
		value := item.(map[string]any)
		key := fmt.Sprintf("%s/%v/%s", value["section"], value["index"], value["reason"])
		if !want[key] {
			t.Fatal("包含错误派生或未预期项目", key)
		}
		delete(want, key)
		if value["section"] == "verification_requests" && (value["evidence_id"] != "E12" || value["expected_tool"] != "propose_verification" || value["actual_tool"] != "propose_followup_collection") {
			t.Fatal("E12计划来源错未精确解释", value)
		}
	}
	if len(want) != 0 {
		t.Fatal("遗漏错误", want)
	}
	for _, fix := range []struct {
		name                          string
		callpath, facts, verification bool
	}{
		{"only-callpath", true, false, false}, {"only-facts", false, true, false}, {"only-verification", false, false, true}, {"missing-verification", true, true, false}, {"missing-callpath", false, true, true},
	} {
		t.Run(fix.name, func(t *testing.T) {
			candidate := editDiagnosis66(t, func(value map[string]any) { repairDiagnosis66(value, fix.callpath, fix.facts, fix.verification) })
			if report, err := validateDiagnosisModelReportV2(candidate, reducedDiagnosis66Trace()); err == nil || report != nil {
				t.Fatal("部分纠正被当成全报告通过")
			}
		})
	}
	full := editDiagnosis66(t, func(value map[string]any) { repairDiagnosis66(value, true, true, true) })
	if report, err := validateDiagnosisModelReportV2(full, reducedDiagnosis66Trace()); err != nil || report == nil || len(report.Facts) != 3 {
		t.Fatal("完整合法纠正不能通过", err)
	}
}

func TestDiagnosisBatchFeedbackFairCapAndNoCascadingHypotheses(t *testing.T) {
	candidate := editDiagnosis66(t, func(value map[string]any) {
		facts := value["facts"].([]any)
		facts = facts[:1]
		for i := 2; i <= 24; i++ {
			facts = append(facts, map[string]any{"id": fmt.Sprintf("F%d", i), "evidence_id": "E1", "path": "/PRIVATE_PATH_SECRET", "operator": "eq", "value": "PRIVATE_VALUE_SECRET", "unit": "PRIVATE_UNIT_SECRET"})
		}
		value["facts"] = facts
		value["hypotheses"] = []any{
			map[string]any{"kind": "PRIVATE_KIND_SECRET", "fact_ids": []string{"F1"}, "evidence_ids": []string{"E1"}},
			map[string]any{"kind": "cpu_hotspot", "fact_ids": []string{"F2"}, "evidence_ids": []string{"E1"}},
		}
	})
	report, err := validateDiagnosisModelReportV2(candidate, reducedDiagnosis66Trace())
	if report != nil || err == nil {
		t.Fatal("超多错误被接受")
	}
	batch := batchFeedbackObject(t, err)
	items := batch["errors"].([]any)
	if len(items) != 6 || batch["truncated"] != true {
		t.Fatal("项目数未有界或未提示尚有错误", batch)
	}
	for i, section := range []string{"facts", "verification_requests", "hypotheses"} {
		if items[i].(map[string]any)["section"] != section {
			t.Fatal("facts淹没其他分区", batch)
		}
	}
	raw, _ := json.Marshal(batch)
	if strings.Contains(string(raw), "PRIVATE_") || strings.Contains(string(raw), "hypothesis_cpu_evidence_missing") {
		t.Fatal("反馈泄漏候选内容或从无效F2推导级联假设错误")
	}
}

type unsafeDiagnosisBatchFeedback struct{}

func (unsafeDiagnosisBatchFeedback) Error() string { return "PRIVATE_BATCH_ERROR" }
func (unsafeDiagnosisBatchFeedback) safeDiagnosisReportBatchFeedback() diagnosisReportBatchFeedback {
	first, second := 0, 1
	return diagnosisReportBatchFeedback{Errors: []diagnosisReportFeedback{
		{Reason: "fact_value_mismatch", Section: "facts", Index: &first, Field: "value", EvidenceID: "E1"},
		{Reason: "fact_value_mismatch", Section: "facts", Index: &second, Field: "value", EvidenceID: "PRIVATE_BATCH_SOURCE"},
	}}
}

func TestDiagnosisBatchFeedbackUTF8BudgetAndUnsafeProviderFailClosed(t *testing.T) {
	items := []diagnosisReportFeedback{}
	for i := 0; i < 24; i++ {
		index := i % 4
		items = append(items, diagnosisReportFeedback{Reason: "hypothesis_change_evidence_missing", Section: "hypotheses", Index: &index, Field: "fact_ids", EvidenceID: "E999", ExpectedTool: "propose_verification", ActualTool: "propose_followup_collection", ExpectedUnit: "requests_per_second"})
	}
	raw, valid := (diagnosisReportBatchFeedback{Errors: items}).safeJSON()
	if !valid || len(raw) > 3500 || !json.Valid([]byte(raw)) {
		t.Fatal("中文UTF8反馈越界或被截断", len(raw))
	}
	var batch map[string]any
	_ = json.Unmarshal([]byte(raw), &batch)
	if batch["truncated"] != true || len(batch["errors"].([]any)) > 6 {
		t.Fatal("未保留截断标志或最多6项边界")
	}
	if _, valid := (unsafeDiagnosisBatchFeedback{}).safeDiagnosisReportBatchFeedback().safeJSON(); valid {
		t.Fatal("批量白名单允许不安全EID")
	}
	runner, run, _ := importedAgentSession(t, "diagnosis", nil, func(context.Context, string) (json.RawMessage, error) { return nil, unsafeDiagnosisBatchFeedback{} })
	importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 1, CallID: "start"})
	_, rejected := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: 1, CallID: "unsafe", Content: "explicit imported controller fixture"})
	encoded, _ := json.Marshal(rejected)
	if strings.Contains(string(encoded), "PRIVATE_") || strings.Contains(string(rejected["feedback"]), "report_validation_failed") {
		t.Fatal("运行时回显不安全batch")
	}
}

func TestDiagnosisBatchFeedbackGoFinishRetainsFiveAndNineRoundBudgets(t *testing.T) {
	fixed := editDiagnosis66(t, func(value map[string]any) { repairDiagnosis66(value, true, true, true) })
	for _, limit := range []int{5, 9} {
		for _, mode := range []string{"repair", "tools-denied", "extra-round-denied"} {
			t.Run(fmt.Sprintf("%d-%s", limit, mode), func(t *testing.T) {
				executedTools := 0
				runner, run, session := importedAgentSession(t, "diagnosis", func(context.Context, string, json.RawMessage) (agentToolResult, error) {
					executedTools++
					return agentToolResult{}, nil
				}, func(_ context.Context, content string) (json.RawMessage, error) {
					report, err := validateDiagnosisModelReportV2(content, reducedDiagnosis66Trace())
					if err != nil {
						return nil, err
					}
					return json.Marshal(report)
				})
				if limit == 9 {
					session.budget = agentBudget{9, 14, 2, 150000}
				}
				for round := 1; round < limit; round++ {
					importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: round, CallID: fmt.Sprintf("round-%d", round)})
				}
				_, rejected := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: limit - 1, CallID: "batch-rejected", Content: reducedDiagnosis66Batch})
				var feedback string
				if json.Unmarshal(rejected["feedback"], &feedback) != nil || !strings.Contains(feedback, "report_validation_failed") || len(feedback) > 3500 || string(rejected["format_only"]) != "true" {
					t.Fatal("真实Go finish未传完整有界batch", rejected)
				}
				importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: limit, CallID: "last-format"})
				switch mode {
				case "repair":
					_, accepted := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: limit, CallID: "fully-fixed", Content: fixed})
					if string(accepted["accepted"]) != "true" {
						t.Fatal("格式轮完整修正不能通过")
					}
				case "tools-denied":
					_, denied := importedAgentCallback(t, runner, run, agentCallback{Type: "tool", Round: limit, CallID: "forbidden-tool", Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)})
					if string(denied["fatal"]) != "true" {
						t.Fatal("格式轮仍能执行工具")
					}
				case "extra-round-denied":
					importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: limit, CallID: "still-invalid", Content: reducedDiagnosis66Batch})
					_, denied := importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: limit + 1, CallID: "forbidden-extra"})
					if string(denied["fatal"]) != "true" {
						t.Fatal("批量反馈增加模型轮数")
					}
				}
				if session.round != limit || session.toolCalls != 0 || executedTools != 0 {
					t.Fatal("反馈改变预算或执行账本")
				}
			})
		}
	}
}
