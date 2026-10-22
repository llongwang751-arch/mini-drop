// 明确合成的时钟/遥测数据用于窗口回归；PG用例经过真实工具方法和HTTP适配器，
// 不调用模型或生产服务。历史正常流量故意多于故障，旧扩窗会把P95稀释为正常值。
package service

import (
	"context"
	"encoding/json"
	"math"
	"net/http"
	"net/http/httptest"
	"sort"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
)

func TestDiagnosticSignalCollectionWindowsRequireBoundedClockProvenance(t *testing.T) {
	now := time.Date(2026, 10, 23, 1, 0, 0, 0, time.UTC)
	outerStart, outerEnd := now.Add(-40*time.Second), now.Add(-time.Second)
	start, end := now.Add(-30*time.Second), now.Add(-10*time.Second)
	metadata := func(a, b int64) json.RawMessage {
		raw, _ := json.Marshal(map[string]int64{"collection_started_unix_ms": a, "collection_finished_unix_ms": b})
		return raw
	}
	attempt := domain.Attempt{StartedAt: &outerStart, FinishedAt: &outerEnd, RunnerMetadata: metadata(start.UnixMilli(), end.UnixMilli())}
	window, err := collectionSignalWindows(attempt, now)
	if err != nil || window.Scope != "collector_execution" || !window.Collection.Start.Equal(start) || !window.Collection.End.Equal(end) || !window.Context.Start.Equal(outerStart.Add(-time.Minute)) || !window.Context.End.Equal(outerEnd.Add(time.Minute)) {
		t.Fatal("collector boundary or separate legacy context lost", window, err)
	}
	t.Run("millisecond-clock-intersects-attempt-boundary", func(t *testing.T) {
		quantized := attempt
		precise := outerStart.Add(500 * time.Microsecond)
		quantized.StartedAt = &precise
		quantized.RunnerMetadata = metadata(outerStart.UnixMilli(), end.UnixMilli())
		got, err := collectionSignalWindows(quantized, now)
		if err != nil || !got.Collection.Start.Equal(precise) || got.Scope != "collector_execution" {
			t.Fatal("millisecond quantization escaped or invalidated attempt boundary", got, err)
		}
	})
	for _, raw := range []json.RawMessage{nil, json.RawMessage(`{}`), json.RawMessage(`{"duration_ms":20000}`)} {
		legacy := attempt
		legacy.RunnerMetadata = raw
		got, err := collectionSignalWindows(legacy, now)
		if err != nil || got.Scope != "attempt_execution" || !got.Collection.Start.Equal(outerStart) || !got.Collection.End.Equal(outerEnd) {
			t.Fatal("old task fallback invented precise collector clocks", got, err)
		}
	}
	for _, tc := range []struct {
		name string
		raw  json.RawMessage
	}{
		{"one-clock", json.RawMessage(`{"collection_started_unix_ms":1}`)},
		{"null-start", json.RawMessage(`{"collection_started_unix_ms":null,"collection_finished_unix_ms":2}`)},
		{"fractional", json.RawMessage(`{"collection_started_unix_ms":1.5,"collection_finished_unix_ms":2}`)},
		{"string", json.RawMessage(`{"collection_started_unix_ms":"1","collection_finished_unix_ms":2}`)},
		{"overflow", json.RawMessage(`{"collection_started_unix_ms":9223372036854775808,"collection_finished_unix_ms":9223372036854775809}`)},
		{"negative", metadata(-1, 2)},
		{"zero-duration", metadata(start.UnixMilli(), start.UnixMilli())},
		{"reverse", metadata(end.UnixMilli(), start.UnixMilli())},
		{"before-attempt", metadata(outerStart.Add(-time.Millisecond).UnixMilli(), end.UnixMilli())},
		{"after-attempt", metadata(start.UnixMilli(), outerEnd.Add(time.Millisecond).UnixMilli())},
		{"broken-json", json.RawMessage(`{`)},
		{"array", json.RawMessage(`[]`)},
		{"null-object", json.RawMessage(`null`)},
		{"whitespace-only", json.RawMessage(" \n\t")},
		{"oversized", json.RawMessage(strings.Repeat(" ", 32769) + `{}`)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			bad := attempt
			bad.RunnerMetadata = tc.raw
			got, err := collectionSignalWindows(bad, now)
			if err == nil || got.Scope != "" || !got.Collection.Start.IsZero() || !got.Context.Start.IsZero() {
				t.Fatal("invalid metadata fell back to misleading broader window", got, err)
			}
		})
	}
}

func TestPostgresDiagnosticSignalWindowSeparatesCollectorMetricsFromContext(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Second)
	outerStart, outerEnd := now.Add(-40*time.Second), now.Add(-time.Second)
	start, end := now.Add(-30*time.Second), now.Add(-10*time.Second)
	attempt, err := s.Repo.LatestAttempt(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	metadata, _ := json.Marshal(map[string]int64{"collection_started_unix_ms": start.UnixMilli(), "collection_finished_unix_ms": end.UnixMilli()})
	if err := db.Model(&domain.Attempt{}).Where("id=?", attempt.ID).Updates(map[string]any{"started_at": outerStart, "finished_at": outerEnd, "runner_metadata": string(metadata)}).Error; err != nil {
		t.Fatal(err)
	}
	type importedEvent struct {
		at      time.Time
		latency float64
	}
	events := []importedEvent{}
	for i := range 1200 {
		events = append(events, importedEvent{outerStart.Add(time.Second + time.Duration(i)*time.Millisecond), 1})
	}
	for i := range 20 {
		events = append(events, importedEvent{start.Add(time.Second + time.Duration(i)*time.Millisecond), 900})
	}
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		q := r.URL.Query()
		a, errA := time.Parse(time.RFC3339Nano, q.Get("start"))
		z, errZ := time.Parse(time.RFC3339Nano, q.Get("end"))
		if errA != nil || errZ != nil || !z.After(a) || r.Method != http.MethodGet || r.URL.Path != "/v1/signals" || q.Get("service") != "fixture-root" || q.Get("signal") != "metrics" || q.Get("limit") != "30" {
			t.Error("tool escaped bounded HTTP adapter contract")
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		durations := []float64{}
		observed := time.Time{}
		for _, e := range events {
			if !e.at.Before(a) && !e.at.After(z) {
				durations = append(durations, e.latency)
				if e.at.After(observed) {
					observed = e.at
				}
			}
		}
		sort.Float64s(durations)
		if len(durations) == 0 {
			t.Error("valid collection lost all controlled events")
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		p95 := durations[int(math.Ceil(float64(len(durations))*.95))-1]
		_ = json.NewEncoder(w).Encode(map[string]any{"schema_version": 1, "service_id": "fixture-root", "signal": "metrics", "metrics": []any{
			map[string]any{"name": "latency_p95_ms", "value": p95, "unit": "milliseconds", "observed_at": observed},
			map[string]any{"name": "error_rate", "value": 0, "unit": "ratio", "observed_at": observed},
			map[string]any{"name": "throughput_rps", "value": float64(len(durations)) / z.Sub(a).Seconds(), "unit": "requests_per_second", "observed_at": observed},
		}})
	}))
	defer server.Close()
	s.Observability = extensionRegistry(t, []TelemetryService{{ID: "fixture-root", Label: "合成时窗回归", AgentID: *task.AgentID, Connectors: map[string]TelemetryConnector{"metrics": {Kind: "drop-adapter", BaseURL: server.URL, AllowHTTP: true}}}})
	run := DiagnosisRun{TaskID: task.ID, AttemptID: attempt.ID, RequestedBy: u.ID, TelemetryServiceID: "fixture-root"}
	query := func() BusinessSignalObservation {
		t.Helper()
		raw, err := s.queryBusinessSignals(ctx, run, json.RawMessage(`{"service_ref":"S1","signal":"metrics"}`))
		if err != nil {
			t.Fatal(err)
		}
		var got BusinessSignalObservation
		if err := json.Unmarshal(raw, &got); err != nil {
			t.Fatal(err)
		}
		return got
	}
	actual := query()
	if !actual.Available || actual.WindowScope != "collector_execution" || actual.ContextQueried || actual.ContextWindow == nil || !actual.Window.Start.Equal(start) || !actual.Window.End.Equal(end) || len(actual.Metrics) != 3 || actual.Metrics[0].Value != 900 || !actual.ContextWindow.Start.Equal(outerStart.Add(-time.Minute)) || !actual.ContextWindow.End.Equal(outerEnd.Add(time.Minute)) {
		t.Fatal("historical normal traffic diluted collector P95", actual)
	}
	if err := db.Model(&domain.Attempt{}).Where("id=?", attempt.ID).Update("runner_metadata", `{}`).Error; err != nil {
		t.Fatal(err)
	}
	legacy := query()
	if !legacy.Available || legacy.WindowScope != "attempt_execution" || !legacy.Window.Start.Equal(outerStart) || legacy.Metrics[0].Value != 1 || legacy.ContextQueried {
		t.Fatal("legacy attempt scope not honestly labeled", legacy)
	}
	if err := db.Model(&domain.Attempt{}).Where("id=?", attempt.ID).Update("runner_metadata", `{"collection_started_unix_ms":1}`).Error; err != nil {
		t.Fatal(err)
	}
	bad := query()
	if calls.Load() != 2 || bad.Available || bad.Reason != "collection_window_unknown" || !bad.Window.Start.IsZero() || bad.WindowScope != "" || bad.ContextWindow != nil || len(bad.Metrics) != 0 {
		t.Fatal("invalid metadata queried source or invented zero-normal metrics", bad, calls.Load())
	}
}
