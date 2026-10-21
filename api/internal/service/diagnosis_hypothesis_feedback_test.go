// 这些fixture缩减自本仓库受控现场诊断41和合成holdout诊断42的真实失败形态。
// 只保留拓扑、空变更与引用关系；不含业务日志、进程标识或原始profile，不是新模型效果验收。
package service

import (
	"encoding/json"
	"strings"
	"testing"
)

func reducedUnsupportedHypothesisFixture(kind string) (string, []DiagnosisStep) {
	trace := []DiagnosisStep{
		{EvidenceID: "E1", Tool: "read_profile_summary", Observation: json.RawMessage(`{"kind":"RESOURCE_SNAPSHOT","measurement_unit":"bytes","total_weight":0,"node_count":1}`)},
		{EvidenceID: "E3", Tool: "inspect_hotspots", Observation: json.RawMessage(`{"top_functions":[]}`)},
		{EvidenceID: "E6", Tool: "inspect_service_topology", Observation: json.RawMessage(`{"nodes":[{"service_ref":"S1"},{"service_ref":"S2"}],"edges":[{"from":"S2","to":"S1"}],"causality_proven":false}`)},
		{EvidenceID: "E9", Tool: "query_business_signals", Observation: json.RawMessage(`{"available":true,"changes":[]}`)},
	}
	content := `{"report_version":2,"facts":[{"id":"F15","evidence_id":"E6","path":"/edges","operator":"count","value":1,"unit":"count"},{"id":"F16","evidence_id":"E6","path":"/nodes","operator":"count","value":2,"unit":"count"}],"hypotheses":[{"kind":"` + kind + `","fact_ids":["F15","F16"],"evidence_ids":["E6","E9"]}],"verification_requests":[{"origin":"model_proposal","kind":"correlate_business_signals"}]}`
	return content, trace
}

func TestDiagnosisFeedbackRejectsTopologyAsChangeOrMixedEvidence(t *testing.T) {
	for _, tc := range []struct{ kind, reason string }{
		{"deployment_regression", "hypothesis_change_evidence_missing"},
		{"mixed_signals", "hypothesis_mixed_evidence_missing"},
	} {
		t.Run(tc.kind, func(t *testing.T) {
			content, trace := reducedUnsupportedHypothesisFixture(tc.kind)
			_, err := validateDiagnosisModelReportV2(content, trace)
			if err == nil || safeAgentError(err) != "AI_INVALID_REPORT" {
				t.Fatal("无支持证据的假设被采纳", err)
			}
			feedback := feedbackObject(t, err)
			if feedback["reason"] != tc.reason || feedback["section"] != "hypotheses" || feedback["index"] != float64(0) || feedback["field"] != "fact_ids" {
				t.Fatal("缺少必要前提的准确定位", feedback)
			}
			fixed := strings.Replace(content, `"kind":"`+tc.kind+`"`, `"kind":"insufficient_evidence"`, 1)
			if _, err = validateDiagnosisModelReportV2(fixed, trace); err != nil {
				t.Fatal("有事实引用的证据不足报告不能通过", err)
			}
		})
	}
}

func TestDiagnosisFeedbackKeepsAllHypothesisPrerequisitesAndReferenceFences(t *testing.T) {
	for _, tc := range []struct{ kind, reason string }{
		{"allocation_gc", "hypothesis_allocation_evidence_missing"},
		{"lock_io", "hypothesis_lock_evidence_missing"},
		{"cpu_hotspot", "hypothesis_cpu_evidence_missing"},
		{"business_latency", "hypothesis_latency_evidence_missing"},
		{"PRIVATE_KIND", "hypothesis_kind_invalid"},
	} {
		content, trace := reducedUnsupportedHypothesisFixture(tc.kind)
		_, err := validateDiagnosisModelReportV2(content, trace)
		if err == nil {
			t.Fatal("必要前提被放宽", tc.kind)
		}
		feedback := feedbackObject(t, err)
		encoded, _ := json.Marshal(feedback)
		if feedback["reason"] != tc.reason || strings.Contains(string(encoded), "PRIVATE_") {
			t.Fatal("原因不明确或未知类型被回显", feedback)
		}
	}
	content, trace := reducedUnsupportedHypothesisFixture("insufficient_evidence")
	for _, tc := range []struct{ old, replacement, reason string }{
		{`["F15","F16"]`, `["F15","PRIVATE_FACT"]`, "hypothesis_fact_reference_invalid"},
		{`["E6","E9"]`, `["E6","PRIVATE_SOURCE"]`, "hypothesis_evidence_invalid"},
		{`["E6","E9"]`, `["E9"]`, "hypothesis_fact_reference_invalid"},
		{`["E6","E9"]`, `["E6","E6"]`, "hypothesis_evidence_invalid"},
	} {
		_, err := validateDiagnosisModelReportV2(strings.Replace(content, tc.old, tc.replacement, 1), trace)
		if err == nil {
			t.Fatal("错误引用被采纳")
		}
		feedback := feedbackObject(t, err)
		encoded, _ := json.Marshal(feedback)
		if feedback["reason"] != tc.reason || strings.Contains(string(encoded), "PRIVATE_") {
			t.Fatal("引用纠错反馈泄漏或原因错误", feedback)
		}
	}
}

func TestDiagnosisFeedbackScalarCountDoesNotInventArrayLength(t *testing.T) {
	content, trace := reducedUnsupportedHypothesisFixture("insufficient_evidence")
	content = strings.Replace(content, `"path":"/edges","operator":"count","value":1`, `"path":"/node_count","operator":"count","value":1`, 1)
	content = strings.Replace(content, `"id":"F15","evidence_id":"E6"`, `"id":"F15","evidence_id":"E1"`, 1)
	_, err := validateDiagnosisModelReportV2(content, trace)
	if err == nil {
		t.Fatal("数字被当数组计数")
	}
	feedback := feedbackObject(t, err)
	if feedback["reason"] != "fact_scalar_operator_invalid" || feedback["field"] != "operator" {
		t.Fatal("没有提示数字应使用eq", feedback)
	}
}
