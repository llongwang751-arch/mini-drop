// 本文件使用明确合成的缩减Analyzer结果，不访问生产或模型。
// 质量计数1条未知栈/1个占位/1个未知样本/380总样本来自受控task102；
// 函数名与树结构均改为测试数据，不把已保留的未知权重当业务函数或根因证据。
package service

import (
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"drop/api/internal/domain"
)

func samplingFixture() map[string]any {
	q := func() map[string]any {
		return map[string]any{"schema_version": 1, "source": "collector_pyspy_raw_v1", "normalized": true, "unresolved_stack_lines": 1, "placeholder_count": 1, "unresolved_sample_weight": 1, "total_sample_weight": 380, "private_note": "PRIVATE_QUALITY_NOTE"}
	}
	return map[string]any{
		"kind": "PYTHON_CPU", "total_samples": 380, "node_count": 3,
		"summary":       map[string]any{"source": "py-spy", "unit": "samples"},
		"metadata":      map[string]any{"source": "py-spy", "unit": "samples", "sample_count": 380, "provenance": "collector_artifact", "input_format": "folded", "pipeline": "python_cpu_v1", "pyspy_raw_quality": q(), "private_note": "PRIVATE_METADATA_NOTE", "collector": map[string]any{"schema_version": 1, "kind": "PYTHON_CPU", "runner": "py-spy", "private_environment": "PRIVATE_COLLECTOR_NOTE"}},
		"sections":      map[string]any{"sampling_quality": q()},
		"top_functions": []any{map[string]any{"name": "PRIVATE_SYNTHETIC_SYMBOL", "self_percent": 99.73684210526316, "total_percent": 99.73684210526316}, map[string]any{"name": "[py-spy unresolved frame]", "self_percent": 0.2631578947368421, "total_percent": 0.2631578947368421}},
	}
}

func samplingTask() domain.Task {
	return domain.Task{Kind: "PYTHON_CPU", Parameters: json.RawMessage(`{"scope":"process","pid":42,"duration_seconds":8,"frequency_hz":49}`)}
}

func samplingRaw(t *testing.T, fixture map[string]any) json.RawMessage {
	t.Helper()
	raw, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func samplingMaps(fixture map[string]any) (map[string]any, map[string]any) {
	return fixture["sections"].(map[string]any)["sampling_quality"].(map[string]any), fixture["metadata"].(map[string]any)["pyspy_raw_quality"].(map[string]any)
}

func samplingObservation(t *testing.T, fixture map[string]any, task domain.Task, name string) json.RawMessage {
	t.Helper()
	raw, plan, err := diagnosisTool(name, json.RawMessage(`{}`), samplingRaw(t, fixture), task)
	if err != nil || plan != nil {
		t.Fatalf("只读工具失败或产生执行计划：%s %v", name, err)
	}
	return raw
}

func TestDiagnosisSamplingQualityControlled102ProjectionAndPrivacy(t *testing.T) {
	raw := samplingObservation(t, samplingFixture(), samplingTask(), "check_collection_quality")
	body, err := diagnosisDecodeObject(raw)
	if err != nil {
		t.Fatal(err)
	}
	q := body["sampling_quality"].(map[string]any)
	if len(q) != 7 || q["source"] != "collector_pyspy_raw_v1" || q["normalized"] != true {
		t.Fatal("质量来源或固定字段投影错误", q)
	}
	for key, value := range map[string]string{"unresolved_stack_lines": "1", "placeholder_count": "1", "unresolved_sample_weight": "1", "total_sample_weight": "380"} {
		if q[key] != json.Number(value) {
			t.Fatal("真实缩减计数被丢弃或补造", key, q[key])
		}
	}
	if !strings.Contains(string(raw), "未知占位符") || !strings.Contains(string(raw), "不能") {
		t.Fatal("工具未保留未知栈不是业务函数的边界")
	}
	for _, name := range []string{"check_collection_quality", "read_profile_summary", "inspect_hotspots"} {
		observed := samplingObservation(t, samplingFixture(), samplingTask(), name)
		if strings.Contains(string(observed), "PRIVATE_") || strings.Contains(string(observed), `"pid"`) || strings.Contains(string(observed), "private_note") {
			t.Fatalf("%s回显原始自由字段", name)
		}
	}
}

func TestDiagnosisSamplingQualityOldMissingRemainsUnknownAndCleanIsNotNoLossProof(t *testing.T) {
	old := samplingFixture()
	delete(old["sections"].(map[string]any), "sampling_quality")
	delete(old["metadata"].(map[string]any), "pyspy_raw_quality")
	body, _ := diagnosisDecodeObject(samplingObservation(t, old, samplingTask(), "check_collection_quality"))
	if _, exists := body["sampling_quality"]; exists {
		t.Fatal("旧结果缺少质量元数据却补出零未知样本")
	}
	clean := samplingFixture()
	q, meta := samplingMaps(clean)
	for _, value := range []map[string]any{q, meta} {
		value["normalized"] = false
		value["unresolved_stack_lines"] = 0
		value["placeholder_count"] = 0
		value["unresolved_sample_weight"] = 0
	}
	raw := samplingObservation(t, clean, samplingTask(), "check_collection_quality")
	body, _ = diagnosisDecodeObject(raw)
	if body["sampling_quality"].(map[string]any)["normalized"] != false || !strings.Contains(string(raw), "丢样") {
		t.Fatal("干净适配被误认成全部丢样已测定")
	}
}

func TestDiagnosisSamplingQualityRejectsInconsistentCountsAndSpoofedProvenance(t *testing.T) {
	for _, tc := range []struct {
		name string
		edit func(map[string]any, *domain.Task)
	}{
		{"missing_section", func(v map[string]any, _ *domain.Task) { delete(v["sections"].(map[string]any), "sampling_quality") }},
		{"missing_metadata", func(v map[string]any, _ *domain.Task) { delete(v["metadata"].(map[string]any), "pyspy_raw_quality") }},
		{"q_metadata_differ", func(v map[string]any, _ *domain.Task) { q, _ := samplingMaps(v); q["placeholder_count"] = 2 }},
		{"wrong_summary_source", func(v map[string]any, _ *domain.Task) { v["summary"].(map[string]any)["source"] = "import" }},
		{"wrong_unit", func(v map[string]any, _ *domain.Task) { v["summary"].(map[string]any)["unit"] = "bytes" }},
		{"missing_unit", func(v map[string]any, _ *domain.Task) { delete(v["summary"].(map[string]any), "unit") }},
		{"wrong_result_kind", func(v map[string]any, _ *domain.Task) { v["kind"] = "GO_CPU" }},
		{"imported_task", func(_ map[string]any, task *domain.Task) { task.Parameters = json.RawMessage(`{"source":"import"}`) }},
		{"invalid_task_parameters", func(_ map[string]any, task *domain.Task) { task.Parameters = json.RawMessage(`{"source":`) }},
		{"metadata_import", func(v map[string]any, _ *domain.Task) { v["metadata"].(map[string]any)["source"] = "import" }},
		{"metadata_unit_mismatch", func(v map[string]any, _ *domain.Task) { v["metadata"].(map[string]any)["unit"] = "bytes" }},
		{"metadata_count_mismatch", func(v map[string]any, _ *domain.Task) { v["metadata"].(map[string]any)["sample_count"] = 379 }},
		{"provenance_import", func(v map[string]any, _ *domain.Task) { v["metadata"].(map[string]any)["provenance"] = "import" }},
		{"wrong_pipeline", func(v map[string]any, _ *domain.Task) { v["metadata"].(map[string]any)["pipeline"] = "java_cpu_v1" }},
		{"wrong_input_format", func(v map[string]any, _ *domain.Task) { v["metadata"].(map[string]any)["input_format"] = "jfr" }},
		{"runner_name", func(v map[string]any, _ *domain.Task) {
			v["metadata"].(map[string]any)["collector"].(map[string]any)["runner"] = "perf"
		}},
		{"runner_kind", func(v map[string]any, _ *domain.Task) {
			v["metadata"].(map[string]any)["collector"].(map[string]any)["kind"] = "GO_CPU"
		}},
		{"runner_schema", func(v map[string]any, _ *domain.Task) {
			v["metadata"].(map[string]any)["collector"].(map[string]any)["schema_version"] = 2
		}},
		{"runner_source_import", func(v map[string]any, _ *domain.Task) {
			v["metadata"].(map[string]any)["collector"].(map[string]any)["source"] = "import"
		}},
		{"runner_provenance_import", func(v map[string]any, _ *domain.Task) {
			v["metadata"].(map[string]any)["collector"].(map[string]any)["provenance"] = "import"
		}},
		{"profile_total_mismatch", func(v map[string]any, _ *domain.Task) { v["total_samples"] = 379 }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			v, task := samplingFixture(), samplingTask()
			tc.edit(v, &task)
			raw, plan, err := diagnosisTool("check_collection_quality", json.RawMessage(`{}`), samplingRaw(t, v), task)
			if err == nil || plan != nil || len(raw) != 0 {
				t.Fatal("不一致质量被工具转成事实", err)
			}
			if strings.Contains(err.Error(), "PRIVATE_") {
				t.Fatal("错误回显原字段")
			}
		})
	}
	for _, tc := range []struct {
		name, field string
		value       any
	}{
		{"source", "source", "import"}, {"schema", "schema_version", 2}, {"normalized", "normalized", false}, {"negative_lines", "unresolved_stack_lines", -1}, {"lines_exceed_weight", "unresolved_stack_lines", 2}, {"missing_placeholder", "placeholder_count", 0}, {"too_many_placeholders", "placeholder_count", 129}, {"negative_weight", "unresolved_sample_weight", -1}, {"weight_exceeds_total", "unresolved_sample_weight", 381}, {"total", "total_sample_weight", 379},
	} {
		t.Run(tc.name, func(t *testing.T) {
			v := samplingFixture()
			q, meta := samplingMaps(v)
			q[tc.field], meta[tc.field] = tc.value, tc.value
			if raw, _, err := diagnosisTool("check_collection_quality", json.RawMessage(`{}`), samplingRaw(t, v), samplingTask()); err == nil || len(raw) != 0 {
				t.Fatal("非法匹配计数被接受")
			}
		})
	}
}

func TestDiagnosisSamplingQualityRequiredZeroFieldsCannotBecomeInventedDefaults(t *testing.T) {
	for _, field := range []string{"schema_version", "source", "normalized", "unresolved_stack_lines", "placeholder_count", "unresolved_sample_weight", "total_sample_weight"} {
		for _, mode := range []string{"missing", "null"} {
			t.Run(field+"/"+mode, func(t *testing.T) {
				v := samplingFixture()
				q, meta := samplingMaps(v)
				for _, values := range []map[string]any{q, meta} {
					values["normalized"] = false
					values["unresolved_stack_lines"] = 0
					values["placeholder_count"] = 0
					values["unresolved_sample_weight"] = 0
					if mode == "missing" {
						delete(values, field)
					} else {
						values[field] = nil
					}
				}
				if raw, _, err := diagnosisTool("check_collection_quality", json.RawMessage(`{}`), samplingRaw(t, v), samplingTask()); err == nil || len(raw) != 0 {
					t.Fatal("缺失/null被补为已观测零或false")
				}
			})
		}
	}
}

func samplingTrace(t *testing.T, fixture map[string]any) []DiagnosisStep {
	t.Helper()
	trace := []DiagnosisStep{}
	for i, name := range []string{"read_profile_summary", "inspect_hotspots", "check_collection_quality"} {
		trace = append(trace, DiagnosisStep{EvidenceID: fmt.Sprintf("E%d", i+1), Tool: name, Observation: samplingObservation(t, fixture, samplingTask(), name)})
	}
	return trace
}

func TestDiagnosisSamplingQualityFiniteFactPathsValuesAndUnits(t *testing.T) {
	trace := samplingTrace(t, samplingFixture())
	content := `{"report_version":2,"facts":[{"id":"F1","evidence_id":"E3","path":"/sampling_quality/normalized","operator":"eq","value":true,"unit":"boolean"},{"id":"F2","evidence_id":"E3","path":"/sampling_quality/unresolved_stack_lines","operator":"eq","value":1,"unit":"count"},{"id":"F3","evidence_id":"E3","path":"/sampling_quality/placeholder_count","operator":"eq","value":1,"unit":"count"},{"id":"F4","evidence_id":"E3","path":"/sampling_quality/unresolved_sample_weight","operator":"eq","value":1,"unit":"samples"},{"id":"F5","evidence_id":"E3","path":"/sampling_quality/total_sample_weight","operator":"eq","value":380,"unit":"samples"}],"hypotheses":[{"kind":"insufficient_evidence","fact_ids":["F1","F2","F3","F4","F5"],"evidence_ids":["E3"]}],"verification_requests":[{"origin":"model_proposal","kind":"repeat_collection"}]}`
	if report, err := validateDiagnosisModelReportV2(content, trace); err != nil || report == nil || len(report.Facts) != 5 {
		t.Fatal("真实质量计数不能被准确引用", err)
	}
	for _, tc := range []struct{ name, old, replacement string }{
		{"wrong_unit", `"unit":"samples"`, `"unit":"milliseconds"`}, {"wrong_value", `"value":380`, `"value":379`}, {"free_path", `/sampling_quality/normalized`, `/sampling_quality/PRIVATE_NOTE`}, {"wrong_tool", `"evidence_id":"E3"`, `"evidence_id":"E2"`}, {"scalar_count", `"operator":"eq"`, `"operator":"count"`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if report, err := validateDiagnosisModelReportV2(strings.Replace(content, tc.old, tc.replacement, 1), trace); err == nil || report != nil {
				t.Fatal("错误单位、数值、路径或来源被采纳")
			}
		})
	}
}

func TestDiagnosisSamplingQualityUnresolvedWeightCannotProveCPUHotspot(t *testing.T) {
	loadDiagnosisCatalog(t)
	allUnknown := samplingFixture()
	allUnknown["top_functions"] = []any{map[string]any{"name": "[py-spy unresolved stack]", "self_percent": 100, "total_percent": 100}}
	q, meta := samplingMaps(allUnknown)
	q["unresolved_sample_weight"], meta["unresolved_sample_weight"] = 380, 380
	trace := samplingTrace(t, allUnknown)
	hotspots, _ := diagnosisDecodeObject(trace[1].Observation)
	if hotspots["top_functions"].([]any)[0].(map[string]any)["unresolved"] != true {
		t.Fatal("可信固定未知marker未明确标记")
	}
	for _, fact := range []string{`{"id":"F1","evidence_id":"E2","path":"/top_functions/0/self_percent","operator":"eq","value":100,"unit":"percent"}`, `{"id":"F1","evidence_id":"E2","path":"/top_functions","operator":"count_positive_self","value":1,"unit":"count"}`} {
		content := `{"report_version":2,"facts":[` + fact + `],"hypotheses":[{"kind":"cpu_hotspot","fact_ids":["F1"],"evidence_ids":["E2"]}],"verification_requests":[{"origin":"model_proposal","kind":"repeat_collection"}]}`
		if report, err := validateDiagnosisModelReportV2(content, trace); err == nil || report != nil {
			t.Fatal("全未知正权重被当成业务函数热点")
		}
		if report, err := validateDiagnosisModelReportV2(strings.Replace(content, `"kind":"cpu_hotspot"`, `"kind":"insufficient_evidence"`, 1), trace); err != nil || report == nil {
			t.Fatal("未知权重的如实计数不能作为证据不足事实", err)
		}
	}
	// 存在真实已解析行时可以调查热点，但它仍不是已确认根因；普通占比上界继续是100。
	trace = samplingTrace(t, samplingFixture())
	content := `{"report_version":2,"facts":[{"id":"F1","evidence_id":"E2","path":"/top_functions/0/self_percent","operator":"eq","value":99.73684210526316,"unit":"percent"}],"hypotheses":[{"kind":"cpu_hotspot","fact_ids":["F1"],"evidence_ids":["E2"]}],"verification_requests":[{"origin":"model_proposal","kind":"repeat_collection"}]}`
	if report, err := validateDiagnosisModelReportV2(content, trace); err != nil || report == nil {
		t.Fatal("正常已解析热点的调查方向被误拒", err)
	}
	invalid := samplingFixture()
	invalid["top_functions"].([]any)[0].(map[string]any)["self_percent"] = 101
	if _, _, err := diagnosisTool("inspect_hotspots", json.RawMessage(`{}`), samplingRaw(t, invalid), samplingTask()); err == nil {
		t.Fatal("新增资源/质量规则放宽了普通热点100%上限")
	}
	old := samplingFixture()
	delete(old["sections"].(map[string]any), "sampling_quality")
	delete(old["metadata"].(map[string]any), "pyspy_raw_quality")
	observed, _ := diagnosisDecodeObject(samplingObservation(t, old, samplingTask(), "inspect_hotspots"))
	if _, exists := observed["top_functions"].([]any)[1].(map[string]any)["unresolved"]; exists {
		t.Fatal("仅凭用户字符串冒充可信未知marker")
	}
}
