package domain

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// Explicit imported synthetic data; never a production profile.
func importedPartialFixture() map[string]any {
	return map[string]any{
		"schema_version": 1, "completeness": "partial", "task_id": "7", "attempt_id": "20",
		"analysis_job_id": "31", "analysis_run": 2, "input_artifact_id": "51", "input_digest": strings.Repeat("a", 64), "kind": "IMPORT_COLLAPSED",
		"completed_stages": []string{"input_verified", "parsed"}, "missing_stages": []string{"diagnostic_rules", "result_artifacts", "final_commit"},
		"summary":       map[string]any{"source": "import", "unit": "samples", "total_samples": 4, "unique_stacks": 1, "node_count": 2, "max_depth": 2},
		"top_functions": []any{map[string]any{"name": "synthetic_work", "symbol_sha256": strings.Repeat("b", 64), "self": 4, "total": 4, "self_percent": 100, "total_percent": 100}},
	}
}

func TestPartialResultStrictBoundary(t *testing.T) {
	payload, _ := json.Marshal(importedPartialFixture())
	parsed, err := DecodePartialResult(payload)
	if err != nil || parsed.TaskID != "7" || parsed.AnalysisRun != 2 || parsed.Summary.Source != "import" {
		t.Fatal("valid imported partial rejected")
	}
	for _, name := range []string{"missing_identity", "unknown_field", "null_summary", "unknown_source", "bad_unit", "oversize_name", "unsafe_name", "unsafe_counter", "negative_count", "deep_tree", "too_many_stacks", "bad_percentage", "wrong_completed_stage", "wrong_missing_stage", "success_marker", "invalid_digest", "noncanonical_id", "invalid_run", "too_many_rows"} {
		t.Run(name, func(t *testing.T) {
			v := importedPartialFixture()
			summary := v["summary"].(map[string]any)
			rows := v["top_functions"].([]any)
			first := rows[0].(map[string]any)
			switch name {
			case "missing_identity":
				delete(v, "attempt_id")
			case "unknown_field":
				v["metadata"] = map[string]any{"private": "synthetic-only"}
			case "null_summary":
				v["summary"] = nil
			case "unknown_source":
				summary["source"] = "private-source-path"
			case "bad_unit":
				summary["unit"] = "file:///synthetic"
			case "oversize_name":
				first["name"] = strings.Repeat("界", 54)
			case "unsafe_name":
				first["name"] = "synthetic\x00symbol"
			case "unsafe_counter":
				summary["total_samples"] = float64(1 << 53)
			case "negative_count":
				summary["total_samples"] = -1
			case "deep_tree":
				summary["max_depth"] = 129
			case "too_many_stacks":
				summary["unique_stacks"] = 100001
			case "bad_percentage":
				first["self_percent"] = 100.1
			case "wrong_completed_stage":
				v["completed_stages"] = []string{"parsed"}
			case "wrong_missing_stage":
				v["missing_stages"] = []string{"final_commit"}
			case "success_marker":
				v["completeness"] = "success"
			case "invalid_digest":
				v["input_digest"] = strings.Repeat("G", 64)
			case "noncanonical_id":
				v["task_id"] = "07"
			case "invalid_run":
				v["analysis_run"] = 0
			case "too_many_rows":
				v["top_functions"] = append(rows, make([]any, 20)...)
			}
			b, _ := json.Marshal(v)
			if _, err := DecodePartialResult(b); err == nil {
				t.Fatal("unsafe partial accepted")
			}
		})
	}
	for _, b := range [][]byte{append(payload, []byte(" {}")...), []byte("null"), []byte(strings.Repeat(" ", 16385)), []byte(`{"summary":{"total_samples":1e999}}`)} {
		if _, err := DecodePartialResult(b); err == nil {
			t.Fatal("invalid JSON/size accepted")
		}
	}
}

func TestPartialResultSharedImportedContractFixture(t *testing.T) {
	payload, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "fixtures", "partial-result-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := DecodePartialResult(payload); err != nil {
		t.Fatal("shared imported contract fixture rejected")
	}
	for _, name := range []string{"synthetic\u200bsymbol", "synthetic\ue000symbol"} {
		v := importedPartialFixture()
		v["top_functions"].([]any)[0].(map[string]any)["name"] = name
		body, _ := json.Marshal(v)
		if _, err := DecodePartialResult(body); err == nil {
			t.Fatal("Unicode category C symbol accepted")
		}
	}
	for key, stages := range map[string][]string{"completed_stages": {"input_verified,parsed"}, "missing_stages": {"diagnostic_rules,result_artifacts,final_commit"}} {
		v := importedPartialFixture()
		v[key] = stages
		body, _ := json.Marshal(v)
		if _, err := DecodePartialResult(body); err == nil {
			t.Fatal("invalid merged stage accepted")
		}
	}
}
