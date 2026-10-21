// 固定导入合成观测通过真正的诊断工具构造 trace，再验证新报告协议。
// 这里测试的是程序事实边界，不调用模型，也不把控制器选择的诊断类别称作准确率。
package service

import (
	"encoding/json"
	"strings"
	"testing"

	"drop/api/internal/domain"
)

func diagnosisFactFixture(t *testing.T, flavor string) []DiagnosisStep {
	t.Helper()
	loadDiagnosisCatalog(t)
	first, second, hint := 70, 30, "malloc"
	kind := "IMPORT_COLLAPSED"
	parameters := json.RawMessage(`{"source":"import"}`)
	if flavor == "perf" {
		kind = "PERF_CPU"
		parameters = json.RawMessage(`{"pid":7,"scope":"process","duration_seconds":20,"frequency_hz":99}`)
	}
	if flavor == "normal" {
		first, second, hint = 82, 18, "fixture.expected_work"
	}
	top := []map[string]any{
		{"name": hint, "self_percent": first, "total_percent": first},
		{"name": "fixture.other_branch", "self_percent": second, "total_percent": second},
		{"name": "fixture.parent", "self_percent": 0, "total_percent": 100},
	}
	weight := 1000
	if flavor == "zero" {
		top = []map[string]any{}
		weight = 0
	}
	profile, _ := json.Marshal(map[string]any{
		"kind": kind, "total_samples": weight, "node_count": 4,
		"top_functions": top, "summary": map[string]any{"unit": "samples", "source": "import"},
		"flamegraph": map[string]any{"name": "all", "value": weight, "self": 0, "children": []any{
			map[string]any{"name": "fixture.parent", "value": weight, "self": 0, "children": []any{
				map[string]any{"name": hint, "value": weight * first / 100, "self": weight * first / 100, "children": []any{}},
				map[string]any{"name": "fixture.other_branch", "value": weight * second / 100, "self": weight * second / 100, "children": []any{}},
			}},
		}},
	})
	task := domain.Task{Kind: kind, Parameters: parameters}
	trace := []DiagnosisStep{}
	for i, call := range []struct{ tool, args string }{
		{"read_profile_summary", `{}`}, {"inspect_hotspots", `{}`}, {"check_collection_quality", `{}`},
		{"inspect_call_paths", `{"rank":1}`}, {"inspect_call_paths", `{"rank":2}`}, {"propose_verification", `{}`},
	} {
		body, _, err := diagnosisTool(call.tool, json.RawMessage(call.args), profile, task)
		if err != nil {
			t.Fatal(err)
		}
		trace = append(trace, DiagnosisStep{EvidenceID: "E" + string(rune('1'+i)), Tool: call.tool, Observation: body})
	}
	return trace
}

func diagnosisFactContent(t *testing.T, mutate func(map[string]any)) string {
	t.Helper()
	value := map[string]any{
		"report_version": 2,
		"facts": []any{
			map[string]any{"id": "F1", "evidence_id": "E2", "path": "/top_functions", "operator": "count_positive_self", "value": 2, "unit": "count"},
			map[string]any{"id": "F2", "evidence_id": "E2", "path": "/top_functions/0/self_percent", "operator": "eq", "value": 70, "unit": "percent"},
			map[string]any{"id": "F3", "evidence_id": "E2", "path": "/top_functions/0/symbol_hint", "operator": "eq", "value": "allocation", "unit": "category"},
		},
		"hypotheses":            []any{map[string]any{"kind": "allocation_gc", "fact_ids": []string{"F1", "F2", "F3"}, "evidence_ids": []string{"E2"}}},
		"verification_requests": []any{map[string]any{"origin": "tool_instruction", "kind": "manual_import", "source_evidence_id": "E6"}},
	}
	if mutate != nil {
		mutate(value)
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	return string(encoded)
}

func TestDiagnosisFactsV2ActualToolsRenderAuthorityAndLegacyRead(t *testing.T) {
	trace := diagnosisFactFixture(t, "allocation")
	report, err := validateDiagnosisModelReportV2(diagnosisFactContent(t, nil), trace)
	if err != nil {
		t.Fatal(err)
	}
	if report.ReportVersion != 2 || len(report.Facts) != 3 || !strings.Contains(report.Summary, "2项具有正self权重") || !strings.Contains(report.Summary, "排名1 self占比70%") || !strings.Contains(report.Summary, "排名2 self占比30%") {
		t.Fatalf("程序摘要未保留两个正权重项目: %+v", report)
	}
	if !strings.Contains(report.Hypotheses[0].Verification, "没有返回可执行采集计划或时长/频率") {
		t.Fatal("人工导入指令被渲染为采集计划")
	}
	if report.Plan != nil {
		t.Fatal("模型报告自行生成了可执行计划")
	}
	var old DiagnosisReport
	if json.Unmarshal([]byte(`{"summary":"旧报告","hypotheses":[{"claim":"旧假设","evidence_ids":["E1"],"verification":"待复测"}],"limitations":[]}`), &old) != nil || old.ReportVersion != 0 || len(old.Facts) != 0 {
		t.Fatal("旧报告读取兼容性改变")
	}
}

func TestDiagnosisFactsV2RejectsWrongValuesUnitsScopeAndMetadata(t *testing.T) {
	trace := diagnosisFactFixture(t, "allocation")
	cases := []struct {
		name   string
		mutate func(map[string]any)
	}{
		{"false-exclusive-hotspot-count", func(v map[string]any) { v["facts"].([]any)[0].(map[string]any)["value"] = 1 }},
		{"wrong-percent", func(v map[string]any) { v["facts"].([]any)[1].(map[string]any)["value"] = 71 }},
		{"wrong-unit", func(v map[string]any) { v["facts"].([]any)[1].(map[string]any)["unit"] = "milliseconds" }},
		{"borrow-field-from-other-tool", func(v map[string]any) { v["facts"].([]any)[1].(map[string]any)["evidence_id"] = "E6" }},
		{"unknown-evidence", func(v map[string]any) { v["facts"].([]any)[1].(map[string]any)["evidence_id"] = "E99" }},
		{"unrestricted-path", func(v map[string]any) { v["facts"].([]any)[1].(map[string]any)["path"] = "/../credentials" }},
		{"duplicate-fact-id", func(v map[string]any) { v["facts"].([]any)[1].(map[string]any)["id"] = "F1" }},
		{"unknown-fact-reference", func(v map[string]any) { v["hypotheses"].([]any)[0].(map[string]any)["fact_ids"] = []string{"F99"} }},
		{"missing-source-reference", func(v map[string]any) { v["hypotheses"].([]any)[0].(map[string]any)["evidence_ids"] = []string{"E1"} }},
		{"free-authoritative-summary", func(v map[string]any) { v["summary"] = "唯一叶帧热点" }},
		{"free-authoritative-claim", func(v map[string]any) { v["hypotheses"].([]any)[0].(map[string]any)["claim"] = "已确认根因" }},
		{"forged-runtime", func(v map[string]any) { v["agent_runtime"] = map[string]any{} }},
		{"forged-knowledge", func(v map[string]any) { v["knowledge"] = map[string]any{} }},
		{"forged-followups", func(v map[string]any) { v["followup_plans"] = []any{} }},
		{"wrong-version", func(v map[string]any) { v["report_version"] = 1 }},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if _, err := validateDiagnosisModelReportV2(diagnosisFactContent(t, c.mutate), trace); err == nil {
				t.Fatal("错误主张通过事实核验")
			}
		})
	}
	content := strings.Replace(diagnosisFactContent(t, nil), `"value":70`, `"value":70.000000000000000001`, 1)
	if _, err := validateDiagnosisModelReportV2(content, trace); err == nil {
		t.Fatal("浮点舍入掩盖了数值差异")
	}
	duplicate := append(append([]DiagnosisStep{}, trace...), trace[0])
	if _, err := validateDiagnosisModelReportV2(diagnosisFactContent(t, nil), duplicate); err == nil {
		t.Fatal("重复E编号应拒绝")
	}
}

func TestDiagnosisFactsV2DistinguishesToolPlanAndUnverifiedProposal(t *testing.T) {
	trace := diagnosisFactFixture(t, "allocation")
	for _, origin := range []string{"tool_plan", "tool_instruction", "model_proposal"} {
		t.Run(origin+"-false-attribution", func(t *testing.T) {
			content := diagnosisFactContent(t, func(v map[string]any) {
				v["verification_requests"] = []any{map[string]any{"origin": origin, "kind": "repeat_collection", "source_evidence_id": "E6", "duration_seconds": 60, "frequency_hz": 99}}
			})
			if _, err := validateDiagnosisModelReportV2(content, trace); err == nil {
				t.Fatal("人工导入E6被冒充为已生成参数计划")
			}
		})
	}
	proposal := diagnosisFactContent(t, func(v map[string]any) {
		v["verification_requests"] = []any{map[string]any{"origin": "model_proposal", "kind": "repeat_collection", "duration_seconds": 60, "frequency_hz": 99}}
	})
	report, err := validateDiagnosisModelReportV2(proposal, trace)
	if err != nil || !strings.Contains(report.Hypotheses[0].Verification, "模型待审建议（不是工具返回或已校验计划）") || report.Plan != nil {
		t.Fatal("合法模型建议没有与工具事实分开", err)
	}
	missingField := diagnosisFactContent(t, func(v map[string]any) {
		v["facts"].([]any)[1] = map[string]any{"id": "F2", "evidence_id": "E6", "path": "/duration_seconds", "operator": "eq", "value": 60, "unit": "seconds"}
	})
	if _, err := validateDiagnosisModelReportV2(missingField, trace); err == nil {
		t.Fatal("缺失字段被补成默认计划参数")
	}
	perfTrace := diagnosisFactFixture(t, "perf")
	actualPlan := diagnosisFactContent(t, func(v map[string]any) {
		v["verification_requests"] = []any{map[string]any{"origin": "tool_plan", "kind": "repeat_collection", "source_evidence_id": "E6"}}
	})
	verified, err := validateDiagnosisModelReportV2(actualPlan, perfTrace)
	if err != nil || !strings.Contains(verified.Hypotheses[0].Verification, "工具E6实际返回同类复测草案") {
		t.Fatal("真实工具计划没有被确认来源", err)
	}
	wrongPlan := diagnosisFactContent(t, func(v map[string]any) {
		v["verification_requests"] = []any{map[string]any{"origin": "tool_plan", "kind": "repeat_collection", "source_evidence_id": "E6", "frequency_hz": 30}}
	})
	if _, err := validateDiagnosisModelReportV2(wrongPlan, perfTrace); err == nil {
		t.Fatal("声明频率与实际工具计划不同仍被接受")
	}
}

func TestDiagnosisFactsV2NormalInsufficientAndWrongAttributionControls(t *testing.T) {
	normal := diagnosisFactFixture(t, "normal")
	content := diagnosisFactContent(t, func(v map[string]any) {
		v["facts"].([]any)[1].(map[string]any)["value"] = 82
		v["facts"].([]any)[2].(map[string]any)["value"] = "unknown"
		v["hypotheses"].([]any)[0].(map[string]any)["kind"] = "normal_load"
	})
	report, err := validateDiagnosisModelReportV2(content, normal)
	if err != nil || strings.Contains(report.Hypotheses[0].Claim, "已确认") {
		t.Fatal("正常高负载负控未保持待核验边界", err)
	}
	wrong := strings.Replace(content, `"kind":"normal_load"`, `"kind":"allocation_gc"`, 1)
	if _, err := validateDiagnosisModelReportV2(wrong, normal); err == nil {
		t.Fatal("unknown提示被错误归因为分配线索")
	}
	zero := diagnosisFactFixture(t, "zero")
	insufficient := diagnosisFactContent(t, func(v map[string]any) {
		v["facts"] = []any{map[string]any{"id": "F1", "evidence_id": "E2", "path": "/top_functions", "operator": "count_positive_self", "value": 0, "unit": "count"}}
		v["hypotheses"] = []any{map[string]any{"kind": "insufficient_evidence", "fact_ids": []string{"F1"}, "evidence_ids": []string{"E2"}}}
	})
	if _, err := validateDiagnosisModelReportV2(insufficient, zero); err != nil {
		t.Fatal(err)
	}
	if _, err := validateDiagnosisModelReportV2(strings.Replace(insufficient, `"kind":"insufficient_evidence"`, `"kind":"cpu_hotspot"`, 1), zero); err == nil {
		t.Fatal("无正权重仍通过热点归因")
	}
}

func TestDiagnosisFactsV2UnverifiedTextNeverBecomesAuthority(t *testing.T) {
	trace := diagnosisFactFixture(t, "allocation")
	content := diagnosisFactContent(t, func(v map[string]any) {
		v["hypotheses"].([]any)[0].(map[string]any)["unverified_interpretation"] = "可能需要继续核查对象生命周期，当前未证明。"
	})
	report, err := validateDiagnosisModelReportV2(content, trace)
	if err != nil {
		t.Fatal(err)
	}
	if report.Hypotheses[0].UnverifiedInterpretation == "" || strings.Contains(report.Summary, "对象生命周期，当前未证明") || strings.Contains(report.Hypotheses[0].Claim, "对象生命周期，当前未证明") {
		t.Fatal("自由解释混入了权威事实")
	}
	if _, err := validateDiagnosisReportForProtocol(`{"summary":"旧格式","hypotheses":[{"claim":"旧假设","evidence_ids":["E1"],"verification":"复测"}],"limitations":[]}`, trace, 2); err == nil {
		t.Fatal("新运行静默回退到v1")
	}
}

func TestDiagnosisFactsV2TypedBusinessAndRegisteredTopology(t *testing.T) {
	trace := diagnosisFactFixture(t, "allocation")
	business := json.RawMessage(`{"available":true,"signal":"metrics","metrics":[{"name":"latency_p95_ms","value":123.5,"unit":"milliseconds"},{"name":"error_rate","value":0.02,"unit":"ratio"}],"logs":[{"duration_ms":20,"status_code":503,"event_code":"synthetic_fixture"}],"traces":[{"duration_ms":130,"span_count":4,"error_count":1}],"changes":[{"timestamp":"2026-10-22T00:00:00Z","change_type":"deployment"}]}`)
	topology := json.RawMessage(`{"nodes":[{"service_ref":"S1"},{"service_ref":"S2"}],"edges":[{"from":"S1","to":"S2","source":"registered_dependency"}],"causality_proven":false}`)
	trace = append(trace, DiagnosisStep{EvidenceID: "E7", Tool: "query_business_signals", Observation: business}, DiagnosisStep{EvidenceID: "E8", Tool: "inspect_service_topology", Observation: topology})
	for _, request := range []diagnosisFactRequest{
		{ID: "F10", EvidenceID: "E7", Path: "/metrics/0/value", Operator: "eq", Value: json.RawMessage(`123.5`), Unit: "milliseconds"},
		{ID: "F11", EvidenceID: "E7", Path: "/metrics/1/value", Operator: "eq", Value: json.RawMessage(`0.02`), Unit: "ratio"},
		{ID: "F12", EvidenceID: "E7", Path: "/logs/0/status_code", Operator: "eq", Value: json.RawMessage(`503`), Unit: "status_code"},
		{ID: "F13", EvidenceID: "E7", Path: "/traces/0/error_count", Operator: "eq", Value: json.RawMessage(`1`), Unit: "count"},
		{ID: "F14", EvidenceID: "E8", Path: "/edges", Operator: "count", Value: json.RawMessage(`1`), Unit: "count"},
		{ID: "F15", EvidenceID: "E8", Path: "/causality_proven", Operator: "eq", Value: json.RawMessage(`false`), Unit: "boolean"},
		{ID: "F16", EvidenceID: "E7", Path: "/changes/0/timestamp", Operator: "eq", Value: json.RawMessage(`"2026-10-22T00:00:00Z"`), Unit: "timestamp"},
		{ID: "F17", EvidenceID: "E7", Path: "/changes/0/change_type", Operator: "eq", Value: json.RawMessage(`"deployment"`), Unit: "category"},
	} {
		var step DiagnosisStep
		for _, candidate := range trace {
			if candidate.EvidenceID == request.EvidenceID {
				step = candidate
			}
		}
		if _, err := diagnosisResolveFact(request, step, "samples"); err != nil {
			t.Fatalf("实际有界标量未通过: %s %v", request.Path, err)
		}
	}
	for _, request := range []diagnosisFactRequest{
		{ID: "F1", EvidenceID: "E7", Path: "/metrics/0/value", Operator: "eq", Value: json.RawMessage(`123.5`), Unit: "samples"},
		{ID: "F1", EvidenceID: "E7", Path: "/logs/0/event_code", Operator: "eq", Value: json.RawMessage(`"synthetic_fixture"`), Unit: "category"},
		{ID: "F1", EvidenceID: "E8", Path: "/causality_proven", Operator: "eq", Value: json.RawMessage(`true`), Unit: "boolean"},
	} {
		var step DiagnosisStep
		for _, candidate := range trace {
			if candidate.EvidenceID == request.EvidenceID {
				step = candidate
			}
		}
		if _, err := diagnosisResolveFact(request, step, "samples"); err == nil {
			t.Fatalf("错误单位、自由文本或拓扑因果主张通过: %s", request.Path)
		}
	}
	wrongUnit := DiagnosisStep{EvidenceID: "E7", Tool: "query_business_signals", Observation: json.RawMessage(strings.Replace(string(business), `"unit":"milliseconds"`, `"unit":"samples"`, 1))}
	if _, err := diagnosisResolveFact(diagnosisFactRequest{ID: "F1", EvidenceID: "E7", Path: "/metrics/0/value", Operator: "eq", Value: json.RawMessage(`123.5`), Unit: "milliseconds"}, wrongUnit, "samples"); err == nil {
		t.Fatal("指标名称与来源单位不一致仍被接受")
	}
	for _, kind := range []string{"business_latency", "downstream_dependency", "deployment_regression"} {
		var request diagnosisFactRequest
		step := trace[len(trace)-2]
		switch kind {
		case "business_latency":
			request = diagnosisFactRequest{ID: "F9", EvidenceID: "E7", Path: "/metrics/0/value", Operator: "eq", Value: json.RawMessage(`123.5`), Unit: "milliseconds"}
		case "downstream_dependency":
			step = trace[len(trace)-1]
			request = diagnosisFactRequest{ID: "F9", EvidenceID: "E8", Path: "/edges", Operator: "count", Value: json.RawMessage(`1`), Unit: "count"}
		case "deployment_regression":
			request = diagnosisFactRequest{ID: "F9", EvidenceID: "E7", Path: "/changes/0/change_type", Operator: "eq", Value: json.RawMessage(`"deployment"`), Unit: "category"}
		}
		fact, err := diagnosisResolveFact(request, step, "samples")
		if err != nil {
			t.Fatal(err)
		}
		if _, err = diagnosisV2HypothesisText(kind, []DiagnosisVerifiedFact{fact}); err != nil {
			t.Fatal("有真实类型化观测的待验证方向被拒", kind, err)
		}
		if _, err = diagnosisV2HypothesisText(kind, []DiagnosisVerifiedFact{}); err == nil {
			t.Fatal("没有对应观测仍接受业务方向", kind)
		}
	}
	unavailable := DiagnosisStep{EvidenceID: "E7", Tool: "query_business_signals", Observation: json.RawMessage(strings.Replace(string(business), `"available":true`, `"available":false`, 1))}
	if _, err := diagnosisResolveFact(diagnosisFactRequest{ID: "F9", EvidenceID: "E7", Path: "/metrics/0/value", Operator: "eq", Value: json.RawMessage(`123.5`), Unit: "milliseconds"}, unavailable, "samples"); err == nil {
		t.Fatal("不可用源中的残留数值被当作当前证据")
	}
	if _, err := diagnosisResolveFact(diagnosisFactRequest{ID: "F9", EvidenceID: "E7", Path: "/available", Operator: "eq", Value: json.RawMessage(`false`), Unit: "boolean"}, unavailable, "samples"); err != nil {
		t.Fatal("不可用标记本身应可说明证据不足", err)
	}
}

func TestDiagnosisFactsV2BoundedExactNumbersAndProtocolBudgets(t *testing.T) {
	if !diagnosisSameScalar(json.Number("70"), json.RawMessage(`70e0`)) {
		t.Fatal("相等的JSON数值应可核对")
	}
	for _, value := range []string{"1e999999999", "1e-999999999"} {
		if _, ok := diagnosisNumeric(json.Number(value)); ok {
			t.Fatal("巨大指数不能进入大数分配")
		}
	}
	if diagnosisFactScalarType(json.Number("1.1"), "ratio") || diagnosisFactScalarType(json.Number("-1"), "milliseconds") || diagnosisFactScalarType(json.Number("1.5"), "count") {
		t.Fatal("数值量纲范围校验失效")
	}
	if !strings.Contains(diagnosisSystemForProtocol(2), "最多8轮普通") || !strings.Contains(diagnosisSystemForProtocol(2), "最多15次工具") || strings.Contains(diagnosisSystem, "最多8轮普通") {
		t.Fatal("v2与旧传输预算说明混淆")
	}
}
