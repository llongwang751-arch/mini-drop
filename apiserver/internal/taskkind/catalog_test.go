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
