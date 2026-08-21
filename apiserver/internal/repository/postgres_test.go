package repository

import (
	"encoding/json"
	"reflect"
	"testing"
	"time"
)

func TestSameTaskRequestCanonicalizesJSON(t *testing.T) {
	a := []byte(`{"name":"x","agent_id":"a1","options":{"sample_rate":99}}`)
	// Same semantic document, different key order and whitespace.
	b := []byte(`{ "options": { "sample_rate": 99 }, "agent_id": "a1", "name": "x" }`)
	if !sameTaskRequest(a, b) {
		t.Fatal("semantically equal requests should match")
	}
	if sameTaskRequest(a, []byte(`{"name":"y","agent_id":"a1"}`)) {
		t.Fatal("different requests must not match")
	}
	if sameTaskRequest([]byte(`not json`), a) {
		t.Fatal("invalid JSON must not match")
	}
}

func TestCountJSONArraySupportsCurrentAndLegacyHypothesisShapes(t *testing.T) {
	for _, raw := range []string{
		`{"hypotheses":[{"id":"h1"},{"id":"h2"}]}`,
		`{"nodes":[{"id":"h1"},{"id":"h2"}]}`,
	} {
		var value map[string]any
		if err := json.Unmarshal([]byte(raw), &value); err != nil {
			t.Fatal(err)
		}
		if got := countJSONArray(value, "hypotheses", "nodes"); got != 2 {
			t.Fatalf("count=%d raw=%s", got, raw)
		}
	}
}

func TestFirstNonEmptyMapPrefersEffectiveRange(t *testing.T) {
	requested := map[string]any{"source": "requested"}
	effective := map[string]any{"source": "effective"}
	if got := firstNonEmptyMap(effective, requested)["source"]; got != "effective" {
		t.Fatalf("unexpected source: %v", got)
	}
	if got := firstNonEmptyMap(map[string]any{}, requested)["source"]; got != "requested" {
		t.Fatalf("unexpected fallback source: %v", got)
	}
}

func TestBuildCoverageNormalizesProbeStateAndTarget(t *testing.T) {
	items := buildCoverage([]map[string]any{{
		"probe_id": "cpu_profile",
		"step_id":  "step-1",
		"task_id":  "task-1",
		"status":   "READY",
		"target":   map[string]any{"instance_id": "worker-a"},
	}})
	if len(items) != 1 {
		t.Fatalf("coverage length=%d", len(items))
	}
	if items[0]["status"] != "QUEUED" || items[0]["target"] != "worker-a" {
		t.Fatalf("unexpected coverage: %#v", items[0])
	}
}

func TestClusterCaseFromSessionPreservesUnifiedContract(t *testing.T) {
	now := time.Date(2026, 8, 1, 10, 0, 0, 0, time.UTC)
	caseItem := clusterCaseFromSession(map[string]any{
		"diagnosis_id":         "diag-1",
		"raw_query":            "检查 CPU 抖动",
		"status":               "COMPLETED",
		"normalized_intent":    map[string]any{"analysis_strategy": "EVIDENCE_FIRST"},
		"target_scope":         map[string]any{"service_id": "service-a"},
		"requested_time_range": map[string]any{"source": "requested"},
		"effective_time_range": map[string]any{"source": "effective"},
		"hypothesis_graph":     map[string]any{"nodes": []any{map[string]any{"id": "h1"}}},
		"conclusion_versions":  []any{map[string]any{"version": 1}},
		"child_task_ids":       []any{"task-1"},
		"created_at":           now,
		"updated_at":           now,
	}, 2)
	if caseItem.CaseID != "diag-1" || caseItem.Strategy != "EVIDENCE_FIRST" {
		t.Fatalf("unexpected case identity: %#v", caseItem)
	}
	if caseItem.CanonicalStatus != "COMPLETED" {
		t.Fatalf("canonical status=%q", caseItem.CanonicalStatus)
	}
	if caseItem.HypothesisCount != 1 || caseItem.EvidenceCount != 2 || caseItem.ReportVersionCount != 1 {
		t.Fatalf("unexpected counts: %#v", caseItem)
	}
	if caseItem.TimeRange["source"] != "effective" {
		t.Fatalf("unexpected time range: %#v", caseItem.TimeRange)
	}
	if diagnosticCaseMap(caseItem)["case_id"] != "diag-1" {
		t.Fatal("unified detail map lost case_id")
	}
}

func TestScanDiagnosisSessionPreservesOptionalBenchmarkCaseID(t *testing.T) {
	now := time.Date(2026, 8, 21, 10, 0, 0, 0, time.UTC)
	benchmarkCaseID := "rw-case-1"
	for name, caseID := range map[string]*string{
		"populated": &benchmarkCaseID,
		"null":      nil,
	} {
		t.Run(name, func(t *testing.T) {
			values := []any{
				"diag-1", caseID, "creator-1", "检查 CPU 抖动",
				[]byte(`{}`), []byte(`{}`), []byte(`{}`), []byte(`{}`),
				(*string)(nil), (*string)(nil), "COMPLETED", "default",
				[]byte(`{}`), []byte(`{}`), []byte(`{}`), []byte(`{}`),
				[]byte(`[]`), []byte(`[]`), "model-1", "planner-1",
				(*string)(nil), (*time.Time)(nil), 1, now, now, now,
			}
			session, err := scanDiagnosisSession(func(dest ...any) error {
				if len(dest) != len(values) {
					t.Fatalf("scan destination count=%d want=%d", len(dest), len(values))
				}
				for index := range dest {
					reflect.ValueOf(dest[index]).Elem().Set(reflect.ValueOf(values[index]))
				}
				return nil
			})
			if err != nil {
				t.Fatal(err)
			}
			if got := stringValue(session["case_id"]); got != stringValue(caseID) {
				t.Fatalf("benchmark case_id=%q want=%q", got, stringValue(caseID))
			}
			if got := clusterCaseFromSession(session, 0).CaseID; got != "diag-1" {
				t.Fatalf("resource case_id=%q want diagnosis identity", got)
			}
		})
	}
}

func TestCanonicalDiagnosisStatusCoversAllDiagnosisGenerations(t *testing.T) {
	tests := map[string]string{
		"NEEDS_CLARIFICATION":   "CREATED",
		"HYPOTHESIZING":         "PLANNING",
		"PROBING":               "COLLECTING",
		"ANALYZING":             "ANALYZING",
		"WAITING_FOR_APPROVAL":  "WAITING_APPROVAL",
		"SUCCEEDED":             "COMPLETED",
		"INSUFFICIENT_EVIDENCE": "PARTIAL",
		"TIMED_OUT":             "FAILED",
		"CANCELLED":             "CANCELLED",
	}
	for native, want := range tests {
		if got := canonicalDiagnosisStatus(native); got != want {
			t.Fatalf("native=%s canonical=%s want=%s", native, got, want)
		}
	}
}

func TestLegacyDiagnosticCasePreservesTaskAndEvidence(t *testing.T) {
	now := time.Date(2026, 8, 5, 10, 0, 0, 0, time.UTC)
	item := legacyDiagnosticCase("rca-1", "task-1", "SUCCEEDED", "CPU hotspot", 2, 1, now, now)
	if item.Source != "legacy_rca" || item.CanonicalStatus != "COMPLETED" {
		t.Fatalf("unexpected legacy projection: %#v", item)
	}
	if item.EvidenceCount != 2 || item.ReportVersionCount != 1 || len(item.TaskIDs) != 1 {
		t.Fatalf("legacy counts lost: %#v", item)
	}
}

func TestLatestItemReturnsLastValue(t *testing.T) {
	if got := latestItem([]any{"v1", "v2"}); got != "v2" {
		t.Fatalf("latest=%v", got)
	}
	if got := latestItem(nil); got != nil {
		t.Fatalf("expected nil, got %v", got)
	}
}
