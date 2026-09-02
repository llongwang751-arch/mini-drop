package taskkind

import "testing"

func TestCatalogHasUniqueValidKinds(t *testing.T) {
	seen := map[string]bool{}
	seenProfilerType := map[uint32]bool{}
	for _, item := range List() {
		if item.ID == "" || seen[item.ID] {
			t.Fatalf("invalid or duplicate task kind %q", item.ID)
		}
		seen[item.ID] = true
		if seenProfilerType[item.ProfilerType] {
			t.Fatalf("duplicate profiler type %d", item.ProfilerType)
		}
		seenProfilerType[item.ProfilerType] = true
		if item.DefaultDurationSec < 1 || item.DefaultDurationSec > item.MaxDurationSec {
			t.Fatalf("invalid duration policy for %s", item.ID)
		}
		if item.DefaultSampleRate < 1 || item.DefaultSampleRate > item.MaxSampleRate {
			t.Fatalf("invalid sample-rate policy for %s", item.ID)
		}
	}
	if _, ok := Lookup("database_lock"); ok {
		t.Fatal("Python-only compatibility collector must not enter the C++ production catalog")
	}
}

func TestGeneratedOptionRulesRejectInvalidKnownOptions(t *testing.T) {
	perf, ok := Lookup("perf_cpu")
	if !ok {
		t.Fatal("perf_cpu is missing")
	}
	if err := ValidateOptions(perf, map[string]any{
		"callgraph": "dwarf", "timeout_sec": float64(30),
	}); err != nil {
		t.Fatalf("valid options rejected: %v", err)
	}
	if err := ValidateOptions(perf, map[string]any{"callgraph": "shell"}); err == nil {
		t.Fatal("invalid callgraph accepted")
	}
	if err := ValidateOptions(perf, map[string]any{"timeout_sec": 30.5}); err == nil {
		t.Fatal("fractional timeout accepted")
	}
	if err := ValidateOptions(perf, map[string]any{"campaign_run_id": "extension"}); err != nil {
		t.Fatalf("provenance extension rejected: %v", err)
	}
}
