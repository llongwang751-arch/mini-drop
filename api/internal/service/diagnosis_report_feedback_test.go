// 回归来自2026-10-21受控DeepSeek诊断39的实际失败候选，候选UTF-8摘要：
// 0fdd7d971d11456d1280ee5bdcea53afc696a03acac82acf658722bbbf749bed。
// 缩减保留真实E1计数、热点数值和E12补证计划及三条verification请求；删去符号、日志、目标信息与其他23条事实。
// 最小假设改为证据不足以隔离来源错误；这不是完整模型报告的语义验收，也没有新的模型调用。
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"

	"drop/api/internal/domain"
)

const controlledDiagnosis39Reduced = `{"report_version":2,"facts":[{"id":"F1","evidence_id":"E1","path":"/measurement_unit","operator":"eq","value":"samples","unit":"unit"}],"hypotheses":[{"kind":"insufficient_evidence","fact_ids":["F1"],"evidence_ids":["E1"]}],"verification_requests":[{"origin":"tool_plan","kind":"repeat_collection","source_evidence_id":"E12","duration_seconds":5,"frequency_hz":2},{"origin":"model_proposal","kind":"collect_wait_io","duration_seconds":10,"frequency_hz":99},{"origin":"model_proposal","kind":"correlate_business_signals"}]}`

func controlledDiagnosis39Trace() []DiagnosisStep {
	return []DiagnosisStep{
		{EvidenceID: "E1", Tool: "read_profile_summary", Observation: json.RawMessage(`{"kind":"PYTHON_CPU","measurement_unit":"samples","total_weight":387,"node_count":22}`)},
		{EvidenceID: "E3", Tool: "inspect_hotspots", Observation: json.RawMessage(`{"top_functions":[{"rank":1,"self_percent":94.832,"total_percent":99.7416,"symbol_hint":"unknown"},{"rank":2,"self_percent":4.9096,"total_percent":4.9096,"symbol_hint":"unknown"},{"rank":3,"self_percent":0.2584,"total_percent":0.2584,"symbol_hint":"unknown"},{"rank":4,"self_percent":0,"total_percent":100,"symbol_hint":"io_or_network"},{"rank":5,"self_percent":0,"total_percent":100,"symbol_hint":"unknown"},{"rank":6,"self_percent":0,"total_percent":100,"symbol_hint":"unknown"},{"rank":7,"self_percent":0,"total_percent":0.2584,"symbol_hint":"unknown"},{"rank":8,"self_percent":0,"total_percent":99.7416,"symbol_hint":"unknown"},{"rank":9,"self_percent":0,"total_percent":0.2584,"symbol_hint":"unknown"},{"rank":10,"self_percent":0,"total_percent":100,"symbol_hint":"io_or_network"}]}`)},
		{EvidenceID: "E12", Tool: "propose_followup_collection", Observation: json.RawMessage(`{"plan":{"kind":"RESOURCE_SNAPSHOT","duration_seconds":5,"frequency_hz":2},"requires_confirmation":true}`)},
	}
}

func editControlledDiagnosis39(t *testing.T, edit func(map[string]any)) string {
	t.Helper()
	var data map[string]any
	if err := json.Unmarshal([]byte(controlledDiagnosis39Reduced), &data); err != nil {
		t.Fatal(err)
	}
	edit(data)
	raw, err := json.Marshal(data)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func feedbackObject(t *testing.T, err error) map[string]any {
	t.Helper()
	provider, ok := err.(diagnosisReportFeedbackProvider)
	if !ok {
		t.Fatalf("缺少专用安全反馈：%T", err)
	}
	raw, valid := provider.safeDiagnosisReportFeedback().safeJSON()
	if !valid {
		t.Fatal("反馈无法安全序列化")
	}
	var result map[string]any
	if json.Unmarshal([]byte(raw), &result) != nil {
		t.Fatal("反馈不是JSON")
	}
	return result
}

func TestDiagnosisFeedbackControlledDeepSeek39SourceMismatchAndSafeRepairs(t *testing.T) {
	trace := controlledDiagnosis39Trace()
	_, err := validateDiagnosisModelReportV2(controlledDiagnosis39Reduced, trace)
	if err == nil || safeAgentError(err) != "AI_INVALID_REPORT" {
		t.Fatal("追加补证被误认同kind复测", err)
	}
	f := feedbackObject(t, err)
	for key, want := range map[string]any{"reason": "verification_source_tool_mismatch", "section": "verification_requests", "index": float64(0), "field": "source_evidence_id", "evidence_id": "E12", "expected_tool": "propose_verification", "actual_tool": "propose_followup_collection"} {
		if f[key] != want {
			t.Fatalf("反馈定位错误：%s=%v", key, f[key])
		}
	}
	for _, repair := range []string{"model-proposal", "remove-invalid-item"} {
		t.Run(repair, func(t *testing.T) {
			content := editControlledDiagnosis39(t, func(data map[string]any) {
				requests := data["verification_requests"].([]any)
				if repair == "remove-invalid-item" {
					data["verification_requests"] = requests[1:]
				} else {
					first := requests[0].(map[string]any)
					first["origin"] = "model_proposal"
					delete(first, "source_evidence_id")
				}
			})
			report, err := validateDiagnosisModelReportV2(content, trace)
			if err != nil || report == nil || report.ReportVersion != 2 {
				t.Fatal("合法纠正被拒绝", err)
			}
			if !strings.Contains(report.Summary, "3项具有正self权重") || !strings.Contains(report.Hypotheses[0].Verification, "不是工具返回或已校验计划") {
				t.Fatal("修正后权威事实或建议边界改变")
			}
		})
	}
}

func TestDiagnosisFeedbackFactsAndUnknownSourcesNeverEchoUntrustedFields(t *testing.T) {
	for _, tc := range []struct {
		name, reason string
		edit         func(map[string]any)
	}{
		{"path", "fact_path_not_allowed", func(v map[string]any) { v["facts"].([]any)[0].(map[string]any)["path"] = "/PRIVATE_CUSTOMER_PATH" }},
		{"unit", "fact_unit_mismatch", func(v map[string]any) { v["facts"].([]any)[0].(map[string]any)["unit"] = "PRIVATE_UNIT_SECRET" }},
		{"value", "fact_value_mismatch", func(v map[string]any) { v["facts"].([]any)[0].(map[string]any)["value"] = "PRIVATE_VALUE_SECRET" }},
		{"unknown-source", "fact_source_not_found", func(v map[string]any) { v["facts"].([]any)[0].(map[string]any)["evidence_id"] = "E777" }},
		{"malicious-source", "fact_source_not_found", func(v map[string]any) { v["facts"].([]any)[0].(map[string]any)["evidence_id"] = "E1\nPRIVATE_TOKEN" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, err := validateDiagnosisModelReportV2(editControlledDiagnosis39(t, tc.edit), controlledDiagnosis39Trace())
			if err == nil {
				t.Fatal("非法事实被接受")
			}
			f := feedbackObject(t, err)
			raw, _ := json.Marshal(f)
			if f["reason"] != tc.reason || f["section"] != "facts" || f["index"] != float64(0) || strings.Contains(string(raw), "PRIVATE_") || strings.Contains(string(raw), "E777") {
				t.Fatal("反馈定位失败或回显未验证内容", string(raw))
			}
			if tc.name == "unit" && f["expected_unit"] != "unit" {
				t.Fatal("未返回固定期望单位")
			}
		})
	}
	trace := controlledDiagnosis39Trace()
	trace[2].Tool = "PRIVATE_TOOL_SECRET"
	_, err := validateDiagnosisModelReportV2(controlledDiagnosis39Reduced, trace)
	f := feedbackObject(t, err)
	if _, exists := f["actual_tool"]; exists {
		t.Fatal("未知工具名称被回显")
	}
	unknown := editControlledDiagnosis39(t, func(v map[string]any) {
		v["verification_requests"].([]any)[0].(map[string]any)["source_evidence_id"] = "E999"
	})
	_, err = validateDiagnosisModelReportV2(unknown, controlledDiagnosis39Trace())
	f = feedbackObject(t, err)
	if f["reason"] != "verification_source_not_found" || f["evidence_id"] != nil {
		t.Fatal("未验证的E编号被当成有效出处")
	}
}

type malformedDiagnosisFeedback struct{}

func (malformedDiagnosisFeedback) Error() string { return "PRIVATE_UPSTREAM_ERROR" }
func (malformedDiagnosisFeedback) Unwrap() error {
	return domain.Fail("AI_INVALID_REPORT", "PRIVATE_DOMAIN_MESSAGE")
}
func (malformedDiagnosisFeedback) safeDiagnosisReportFeedback() diagnosisReportFeedback {
	index := 0
	return diagnosisReportFeedback{Reason: "fact_value_mismatch", Section: "facts", Index: &index, Field: "value", EvidenceID: "PRIVATE_TOKEN"}
}

func TestDiagnosisFeedbackRuntimeOnlyUsesValidatedDedicatedInterface(t *testing.T) {
	_, typed := validateDiagnosisModelReportV2(controlledDiagnosis39Reduced, controlledDiagnosis39Trace())
	for _, err := range []error{errors.New("PRIVATE_UPSTREAM_ERROR"), domain.Fail("AI_INVALID_REPORT", "PRIVATE_DOMAIN_MESSAGE"), fmt.Errorf("PRIVATE_WRAPPER: %w", typed), malformedDiagnosisFeedback{}} {
		runner, run, _ := importedAgentSession(t, "diagnosis", nil, func(context.Context, string) (json.RawMessage, error) { return nil, err })
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 1, CallID: "start"})
		_, response := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: 1, CallID: "finish", Content: "explicit imported fixture"})
		raw, _ := json.Marshal(response)
		if strings.Contains(string(raw), "PRIVATE_") || strings.Contains(string(response["feedback"]), "verification_source_tool_mismatch") {
			t.Fatal("普通异常或未验专用反馈被回填给模型")
		}
	}
}

func TestDiagnosisFeedbackGoFinishKeepsLegacyAndV2FormatRoundBudgets(t *testing.T) {
	corrected := editControlledDiagnosis39(t, func(v map[string]any) {
		first := v["verification_requests"].([]any)[0].(map[string]any)
		first["origin"] = "model_proposal"
		delete(first, "source_evidence_id")
	})
	for _, max := range []int{5, 9} {
		for _, mode := range []string{"repair", "tools-denied", "extra-model-denied"} {
			t.Run(fmt.Sprintf("%d-%s", max, mode), func(t *testing.T) {
				executedTools := 0
				// 工具回调真实可用；拒绝必须来自格式轮权限，而非nil回调的偶然失败。
				runner, run, session := importedAgentSession(t, "diagnosis", func(context.Context, string, json.RawMessage) (agentToolResult, error) {
					executedTools++
					return agentToolResult{}, nil
				}, func(_ context.Context, content string) (json.RawMessage, error) {
					report, err := validateDiagnosisModelReportV2(content, controlledDiagnosis39Trace())
					if err != nil {
						return nil, err
					}
					return json.Marshal(report)
				})
				if max == 9 {
					session.budget = agentBudget{9, 14, 2, 150000}
				}
				for round := 1; round < max; round++ {
					importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: round, CallID: fmt.Sprintf("start-%d", round)})
				}
				_, rejected := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: max - 1, CallID: "bad-source", Content: controlledDiagnosis39Reduced})
				if string(rejected["error_code"]) != `"AI_INVALID_REPORT"` || string(rejected["format_only"]) != "true" || !strings.Contains(string(rejected["feedback"]), "verification_source_tool_mismatch") {
					t.Fatal("真实finish回调未收到精确反馈或未进入原格式轮", rejected)
				}
				importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: max, CallID: "last-start"})
				switch mode {
				case "repair":
					_, accepted := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: max, CallID: "fixed-source", Content: corrected})
					if string(accepted["accepted"]) != "true" {
						t.Fatal("最后格式轮不能接受合法修正", accepted)
					}
					_, late := importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: max + 1, CallID: "after-accept"})
					if string(late["fatal"]) != "true" {
						t.Fatal("已接受结果后仍可继续模型")
					}
				case "tools-denied":
					_, denied := importedAgentCallback(t, runner, run, agentCallback{Type: "tool", Round: max, CallID: "forbidden-format-tool", Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)})
					if string(denied["fatal"]) != "true" {
						t.Fatal("格式轮精确反馈增加了工具权限")
					}
				case "extra-model-denied":
					importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: max, CallID: "still-bad", Content: controlledDiagnosis39Reduced})
					_, denied := importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: max + 1, CallID: "forbidden-extra-model"})
					if string(denied["fatal"]) != "true" {
						t.Fatal("精确反馈增加第六/十模型轮")
					}
				}
				if session.round != max || session.toolCalls != 0 || executedTools != 0 {
					t.Fatal("反馈改变原模型或工具账本", session.round, session.toolCalls, executedTools)
				}
			})
		}
	}
}
