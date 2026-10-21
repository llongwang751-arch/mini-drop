// 本文件用明确导入的受控业务聚合值回归线上41暴露的资源证据缺口。
// 不访问生产、不调用模型；隐私负控中的PID和日志内容均为测试哨兵。
package service

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"drop/api/internal/domain"
)

func resourceFixture(t *testing.T) map[string]any {
	t.Helper()
	raw, err := os.ReadFile("testdata/resource_snapshot_controlled_41.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]any
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	decoder.UseNumber()
	if err := decoder.Decode(&fixture); err != nil {
		t.Fatal(err)
	}
	return fixture
}

func resourceFixtureBytes(t *testing.T, fixture map[string]any) json.RawMessage {
	t.Helper()
	raw, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func resourceFixtureAggregate(fixture map[string]any) map[string]any {
	return fixture["sections"].(map[string]any)["resources"].(map[string]any)
}

func resourceFixtureTask() domain.Task {
	return domain.Task{Kind: "RESOURCE_SNAPSHOT", Parameters: json.RawMessage(`{"scope":"process","pid":424242,"duration_seconds":5,"frequency_hz":2}`)}
}

func resourceFixtureObservation(t *testing.T) map[string]any {
	t.Helper()
	raw, plan, err := diagnosisTool("read_profile_summary", json.RawMessage(`{}`), resourceFixtureBytes(t, resourceFixture(t)), resourceFixtureTask())
	if err != nil || plan != nil {
		t.Fatalf("只读资源摘要失败或生成了执行计划：%v", err)
	}
	body, err := diagnosisDecodeObject(raw)
	if err != nil {
		t.Fatal(err)
	}
	return body
}

func TestDiagnosisResourceSummaryProjectsActualCountersWithoutRawPayload(t *testing.T) {
	body := resourceFixtureObservation(t)
	resources := body["resources"].(map[string]any)
	peaks := resources["peaks"].(map[string]any)
	if resources["sample_count"] != json.Number("10") || peaks["rss_bytes"] != json.Number("26378240") || peaks["cpu_percent"] != json.Number("53.95180938063228") || len(peaks) != 5 {
		t.Fatalf("实际计数被替换、丢失或补零：%v", peaks)
	}
	if _, exists := peaks["memory_used_bytes"]; exists {
		t.Fatal("未采集的主机内存被补为零")
	}
	if _, exists := peaks["memory_total_bytes"]; exists {
		t.Fatal("未采集的主机内存总量被补为零")
	}
	if body["total_weight"] != json.Number("0") || body["stack_scope"] != "resource_counters_only_no_function_stacks" || resources["scope"] != "process" {
		t.Fatal("空profile与实际资源观测的语义未区分")
	}
	encoded, _ := json.Marshal(body)
	for _, forbidden := range []string{"EXPLICIT_IMPORTED_PRIVATE_MARKER", "424242", `"samples":`, `"dmesg":`, `"pid":`, "network_scope", "unknown_private_peak"} {
		if strings.Contains(string(encoded), forbidden) {
			t.Fatalf("资源摘要泄露未授权字段：%s", forbidden)
		}
	}
	if len(encoded) > 8192 || len(resources["units"].(map[string]any)) != 8 {
		t.Fatal("资源输出大小或固定单位映射不受控")
	}
}

func TestDiagnosisResourceSummaryOmitsMissingObservationsAndKeepsActualZero(t *testing.T) {
	for _, tc := range []struct {
		name   string
		change func(map[string]any)
	}{
		{"missing_section", func(v map[string]any) { delete(v, "sections") }},
		{"null_resource", func(v map[string]any) { v["sections"].(map[string]any)["resources"] = nil }},
		{"missing_count", func(v map[string]any) { delete(resourceFixtureAggregate(v), "sample_count") }},
		{"null_count", func(v map[string]any) { resourceFixtureAggregate(v)["sample_count"] = nil }},
		{"zero_count", func(v map[string]any) { resourceFixtureAggregate(v)["sample_count"] = json.Number("0") }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fixture := resourceFixture(t)
			tc.change(fixture)
			projected, err := diagnosisResourceProjection(resourceFixtureBytes(t, fixture), resourceFixtureTask())
			if err != nil || projected != nil {
				t.Fatalf("无观测时必须保持未知，不能补计数/峰值：%v %v", projected, err)
			}
		})
	}
	fixture := resourceFixture(t)
	resourceFixtureAggregate(fixture)["peaks"].(map[string]any)["cpu_percent"] = json.Number("0")
	task := resourceFixtureTask()
	task.Parameters = nil
	projected, err := diagnosisResourceProjection(resourceFixtureBytes(t, fixture), task)
	if err != nil || projected["peaks"].(map[string]any)["cpu_percent"] != json.Number("0") || projected["scope"] != "unknown" {
		t.Fatal("实际零值不能被当作缺失；未知scope不能虚构为process")
	}
}

func TestDiagnosisResourceProjectionRejectsMalformedAggregates(t *testing.T) {
	for _, tc := range []struct {
		name  string
		field string
		value any
	}{
		{"boolean_count", "sample_count", true},
		{"string_count", "sample_count", "10"},
		{"negative_count", "sample_count", json.Number("-1")},
		{"fraction_count", "sample_count", json.Number("1.1")},
		{"excessive_count", "sample_count", json.Number("3601")},
		{"boolean_cpu", "cpu_percent", true},
		{"string_cpu", "cpu_percent", "53"},
		{"negative_cpu", "cpu_percent", json.Number("-0.1")},
		{"excessive_cpu", "cpu_percent", json.Number("100000.1")},
		{"fraction_rss", "rss_bytes", json.Number("1.01")},
		{"unsafe_integer", "rss_bytes", json.Number("9007199254740992")},
		{"rounding_cannot_hide_fraction", "rss_bytes", json.Number("9007199254740991.1")},
		{"huge_exponent", "rss_bytes", json.Number("1e1000")},
		{"negative_threads", "threads", json.Number("-1")},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fixture := resourceFixture(t)
			aggregate := resourceFixtureAggregate(fixture)
			if tc.field == "sample_count" {
				aggregate[tc.field] = tc.value
			} else {
				aggregate["peaks"].(map[string]any)[tc.field] = tc.value
			}
			_, err := diagnosisResourceProjection(resourceFixtureBytes(t, fixture), resourceFixtureTask())
			if failure, ok := err.(*domain.Error); !ok || failure.Code != "INVALID_EVIDENCE" {
				t.Fatalf("坏聚合不能进入模型证据：%v", err)
			}
		})
	}
	for _, kind := range []string{"PYTHON_CPU", "PROC_CPU", "GO_HEAP"} {
		t.Run("non_resource_"+kind, func(t *testing.T) {
			task := resourceFixtureTask()
			task.Kind = kind
			projected, err := diagnosisResourceProjection(resourceFixtureBytes(t, resourceFixture(t)), task)
			if err != nil || projected != nil {
				t.Fatal("非RESOURCE任务不得读取或输出资源聚合")
			}
		})
	}
	fixture := resourceFixture(t)
	fixture["kind"] = "PYTHON_CPU"
	if _, err := diagnosisResourceProjection(resourceFixtureBytes(t, fixture), resourceFixtureTask()); err == nil {
		t.Fatal("任务与分析结果kind不一致仍产生资源证据")
	}
	if _, err := diagnosisResourceProjection(json.RawMessage(strings.Repeat(" ", (8<<20)+1)), resourceFixtureTask()); err == nil {
		t.Fatal("超限输入未拒绝")
	}
}

func TestDiagnosisResourceFactScalarChecksSourceAndSpecificRanges(t *testing.T) {
	for _, tc := range []struct {
		path  string
		value json.Number
		valid bool
	}{
		{"/resources/sample_count", "10", true},
		{"/resources/sample_count", "0", false},
		{"/resources/sample_count", "3601", false},
		{"/resources/peaks/cpu_percent", "125.5", true},
		{"/resources/peaks/cpu_percent", "100000", true},
		{"/resources/peaks/cpu_percent", "100000.1", false},
		{"/resources/peaks/cpu_percent", "-1", false},
		{"/resources/peaks/rss_bytes", "9007199254740991", true},
		{"/resources/peaks/rss_bytes", "9007199254740992", false},
		{"/resources/peaks/rss_bytes", "9007199254740991.1", false},
		{"/resources/peaks/threads", "2.5", false},
		{"/resources/peaks/unknown", "1", false},
		{"/resources/samples/0/pid", "424242", false},
		{"/resources/dmesg", "1", false},
		{"/resources/peaks/memory_used_bytes", "0", false},
	} {
		t.Run(tc.path+"_"+string(tc.value), func(t *testing.T) {
			if got := diagnosisResourceFactScalar(resourceFixtureObservation(t), tc.path, tc.value); got != tc.valid {
				t.Fatalf("资源数值边界错误，got %t want %t", got, tc.valid)
			}
		})
	}
	if diagnosisFactScalarType(json.Number("125.5"), "percent") {
		t.Fatal("资源多核百分比不能放宽普通热点percent约束")
	}
	for _, tc := range []struct {
		name   string
		change func(map[string]any, map[string]any)
	}{
		{"wrong_kind", func(body, _ map[string]any) { body["kind"] = "PYTHON_CPU" }},
		{"wrong_source", func(_, resource map[string]any) { resource["source"] = "model" }},
		{"schema_zero", func(_, resource map[string]any) { resource["schema_version"] = json.Number("0") }},
		{"schema_future", func(_, resource map[string]any) { resource["schema_version"] = json.Number("2") }},
		{"schema_string", func(_, resource map[string]any) { resource["schema_version"] = "1" }},
		{"unknown_scope", func(_, resource map[string]any) { resource["scope"] = "PRIVATE_HOST" }},
		{"zero_observations", func(_, resource map[string]any) { resource["sample_count"] = json.Number("0") }},
		{"wrong_units", func(_, resource map[string]any) { resource["units"].(map[string]any)["cpu_percent"] = "ratio" }},
		{"missing_units", func(_, resource map[string]any) { delete(resource["units"].(map[string]any), "rss_bytes") }},
		{"extra_units", func(_, resource map[string]any) { resource["units"].(map[string]any)["pid"] = "count" }},
		{"missing_peak", func(_, resource map[string]any) { delete(resource["peaks"].(map[string]any), "rss_bytes") }},
		{"null_peak", func(_, resource map[string]any) { resource["peaks"].(map[string]any)["rss_bytes"] = nil }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body := resourceFixtureObservation(t)
			tc.change(body, body["resources"].(map[string]any))
			if diagnosisResourceFactScalar(body, "/resources/peaks/rss_bytes", json.Number("26378240")) {
				t.Fatal("资源来源、单位或实际字段缺失没有被拒绝")
			}
		})
	}
}

func TestDiagnosisResourceQualityDistinguishesCountersFromFunctionSamples(t *testing.T) {
	for _, kind := range []string{"RESOURCE_SNAPSHOT", "PYTHON_CPU"} {
		task := resourceFixtureTask()
		task.Kind = kind
		raw, _, err := diagnosisTool("check_collection_quality", json.RawMessage(`{}`), resourceFixtureBytes(t, resourceFixture(t)), task)
		if err != nil {
			t.Fatal(err)
		}
		text := string(raw)
		if (kind == "RESOURCE_SNAPSHOT") != strings.Contains(text, "资源快照不产生函数调用栈") || (kind == "PYTHON_CPU") != strings.Contains(text, "没有正采样权重") {
			t.Fatalf("资源计数与空CPU profile被混淆：%s", kind)
		}
	}
}

func TestDiagnosisResourceActualProjectionToV2ReportKeepsObservedSampleAndRSS(t *testing.T) {
	for _, cpu := range []json.Number{"53.95180938063228", "125.5"} {
		t.Run("cpu_"+string(cpu), func(t *testing.T) {
			fixture := resourceFixture(t)
			resourceFixtureAggregate(fixture)["peaks"].(map[string]any)["cpu_percent"] = cpu
			raw := resourceFixtureBytes(t, fixture)
			trace := []DiagnosisStep{}
			for index, name := range []string{"read_profile_summary", "inspect_hotspots", "check_collection_quality"} {
				observation, _, err := diagnosisTool(name, json.RawMessage(`{}`), raw, resourceFixtureTask())
				if err != nil {
					t.Fatal(err)
				}
				trace = append(trace, DiagnosisStep{EvidenceID: "E" + string(rune('1'+index)), Tool: name, Observation: observation})
			}
			candidate := map[string]any{
				"report_version": 2,
				"facts": []any{
					map[string]any{"id": "F1", "evidence_id": "E1", "path": "/resources/sample_count", "operator": "eq", "value": 10, "unit": "count"},
					map[string]any{"id": "F2", "evidence_id": "E1", "path": "/resources/peaks/rss_bytes", "operator": "eq", "value": 26378240, "unit": "bytes"},
					map[string]any{"id": "F3", "evidence_id": "E1", "path": "/resources/peaks/cpu_percent", "operator": "eq", "value": cpu, "unit": "percent"},
				},
				"hypotheses":            []any{map[string]any{"kind": "insufficient_evidence", "fact_ids": []string{"F1", "F2", "F3"}, "evidence_ids": []string{"E1"}}},
				"verification_requests": []any{map[string]any{"origin": "model_proposal", "kind": "correlate_business_signals"}},
			}
			encoded, err := json.Marshal(candidate)
			if err != nil {
				t.Fatal(err)
			}
			report, err := validateDiagnosisModelReportV2(string(encoded), trace)
			if err != nil {
				t.Fatalf("真实资源投影不能生成合法v2报告：%v", err)
			}
			if report.ReportVersion != 2 || len(report.Facts) != 3 || !strings.Contains(report.Summary, "10个实际样本") || !strings.Contains(report.Summary, "RSS峰值=26378240bytes") || !strings.Contains(report.Summary, "CPU峰值="+string(cpu)+"%") {
				t.Fatalf("程序摘要未保留实际资源聚合：%s", report.Summary)
			}
			if strings.Contains(report.Summary, "没有正观测") || strings.Contains(report.Summary, "没有正self") || !strings.Contains(report.Summary, "尚未证明") {
				t.Fatal("资源空函数栈被写成无观测，或遗漏因果边界")
			}
		})
	}
}
