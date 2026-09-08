package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"strings"
	"testing"

	"mini-drop/apiserver/internal/config"
	mini_drop "mini-drop/apiserver/internal/gen/mini_drop"
	"mini-drop/apiserver/internal/repository"
)

func TestValidateIdempotencyKey(t *testing.T) {
	for _, valid := range []string{
		"create-task-20260805-0001",
		"valid-token-1234567890",
		strings.Repeat("k", 128),
	} {
		if err := validateIdempotencyKey(valid); err != nil {
			t.Fatalf("expected valid idempotency key %q, got %v", valid, err)
		}
	}
	for _, invalid := range []string{
		"", "short", "with space", strings.Repeat("k", 129),
	} {
		if err := validateIdempotencyKey(invalid); err == nil {
			t.Fatalf("expected invalid idempotency key %q", invalid)
		}
	}
}

func TestRequestTraceRetainsTraceIDAndCreatesChildSpan(t *testing.T) {
	server := &Server{}
	handler := server.requestTrace(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Trace-ID") != "4bf92f3577b34da6a3ce929d0e0e4736" {
			t.Fatalf("unexpected trace id: %s", r.Header.Get("X-Trace-ID"))
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.Header.Set("Traceparent", "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
	recorder := httptest.NewRecorder()

	handler.ServeHTTP(recorder, request)

	traceparent := recorder.Header().Get("Traceparent")
	traceID, flags, ok := parseTraceparent(traceparent)
	if !ok || traceID != "4bf92f3577b34da6a3ce929d0e0e4736" || flags != "01" {
		t.Fatalf("unexpected response trace context: %q", traceparent)
	}
	if strings.Contains(traceparent, "00f067aa0ba902b7") {
		t.Fatalf("server must create a child span instead of reusing the caller span: %q", traceparent)
	}
}

func TestRequestTraceReplacesInvalidAllZeroContext(t *testing.T) {
	server := &Server{}
	handler := server.requestTrace(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	request := httptest.NewRequest(http.MethodGet, "/", nil)
	request.Header.Set("Traceparent", "00-00000000000000000000000000000000-0000000000000000-01")
	recorder := httptest.NewRecorder()

	handler.ServeHTTP(recorder, request)

	traceID, _, ok := parseTraceparent(recorder.Header().Get("Traceparent"))
	if !ok || traceID == "00000000000000000000000000000000" {
		t.Fatalf("invalid context was not replaced: %q", recorder.Header().Get("Traceparent"))
	}
}

func TestReadIdempotencyKeyNormalizesWhitespace(t *testing.T) {
	req := httptest.NewRequest(http.MethodPost, "/api/tasks", nil)
	req.Header.Set("Idempotency-Key", "  key-value-0001  ")
	key, err := readIdempotencyKey(req)
	if err != nil {
		t.Fatal(err)
	}
	if key != "key-value-0001" {
		t.Fatalf("key=%q", key)
	}
}

func TestNormalizeTaskResourceBudget(t *testing.T) {
	budget, err := normalizeTaskResourceBudget(nil, 15, 300)
	if err != nil {
		t.Fatal(err)
	}
	if budget.MaxDurationSec != 15 || budget.MaxMemoryMB != 1024 || budget.MaxOutputMB != 256 {
		t.Fatalf("unexpected defaults: %#v", budget)
	}
	if _, err := normalizeTaskResourceBudget(
		&taskResourceBudget{MaxCPUPercent: 101}, 15, 300,
	); err == nil {
		t.Fatal("expected CPU budget validation error")
	}
	if _, err := normalizeTaskResourceBudget(
		&taskResourceBudget{MaxDurationSec: 10}, 15, 300,
	); err == nil {
		t.Fatal("expected duration budget validation error")
	}
}

func TestApplyTypedTaskPayload(t *testing.T) {
	desc := &mini_drop.TaskDesc{}
	applyTypedTaskPayload(desc, createTaskRequest{
		CollectorType: "go_pprof", TargetPID: 123, DurationSec: 20,
		SampleRate: 99, Options: map[string]any{"pprof_url": "http://service:6060/debug/pprof/profile"},
	})
	if desc.GetPprof() == nil || desc.GetPprof().GetPid() != 123 ||
		desc.GetPprof().GetEndpoint() != "http://service:6060/debug/pprof/profile" {
		t.Fatalf("unexpected typed pprof payload: %#v", desc.GetPprof())
	}
}

func TestApplyTypedContinuousTaskPayload(t *testing.T) {
	desc := &mini_drop.TaskDesc{}
	applyTypedTaskPayload(desc, createTaskRequest{
		CollectorType: "continuous_perf", TargetPID: 456, DurationSec: 3600,
		SampleRate: 49,
		Options: map[string]any{
			"window_seconds":              300,
			"trigger_cpu_percent":         75,
			"trigger_consecutive_samples": 4,
			"trigger_wait_seconds":        1800,
			"retention_tier":              "extended",
		},
	})
	payload := desc.GetContinuousPerf()
	if payload == nil || payload.GetPid() != 456 || payload.GetWindowSeconds() != 300 ||
		payload.GetTriggerCpuPercent() != 75 || payload.GetTriggerConsecutiveSamples() != 4 ||
		payload.GetTriggerWaitSeconds() != 1800 || payload.GetRetentionTier() != "extended" {
		t.Fatalf("unexpected typed continuous payload: %#v", payload)
	}
}

func TestParseProcessCandidateLimitIsBounded(t *testing.T) {
	for raw, want := range map[string]int{
		"": 20, "invalid": 20, "0": 20, "12": 12, "101": 100,
	} {
		if got := parseProcessCandidateLimit(raw); got != want {
			t.Fatalf("raw=%q got=%d want=%d", raw, got, want)
		}
	}
}

func testServer(t *testing.T, auth bool) (*httptest.Server, *httptest.Server) {
	t.Helper()
	legacy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/healthz" {
			_ = json.NewEncoder(w).Encode(map[string]any{"code": 0})
			return
		}
		w.Header().Set("X-Legacy", "true")
		_, _ = io.WriteString(w, `{"code":0,"message":"ok","data":{"proxied":true}}`)
	}))
	handler := New(config.Config{
		ListenAddr: ":0", AuthEnabled: auth, APIKey: "test-key",
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	return httptest.NewServer(handler), legacy
}

func TestNativeHealthAndMe(t *testing.T) {
	server, legacy := testServer(t, false)
	defer server.Close()
	defer legacy.Close()

	for _, path := range []string{"/api/healthz", "/api/me"} {
		resp, err := http.Get(server.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("%s status=%d", path, resp.StatusCode)
		}
		if resp.Header.Get("X-Content-Type-Options") != "nosniff" ||
			resp.Header.Get("Referrer-Policy") != "no-referrer" ||
			resp.Header.Get("Cache-Control") != "no-store" {
			t.Fatalf("%s missing direct API security headers: %#v", path, resp.Header)
		}
		_ = resp.Body.Close()
	}
}

func TestPrometheusMetricsAreServedByGo(t *testing.T) {
	server, legacy := testServer(t, false)
	defer server.Close()
	defer legacy.Close()

	first, err := http.Get(server.URL + "/api/me")
	if err != nil {
		t.Fatal(err)
	}
	_ = first.Body.Close()
	resp, err := http.Get(server.URL + "/api/metrics")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	if resp.StatusCode != http.StatusOK ||
		!strings.Contains(string(body), "mini_drop_http_requests_total") ||
		!strings.Contains(string(body), "mini_drop_http_responses_total{class=\"2xx\"}") ||
		!strings.Contains(string(body), "mini_drop_http_request_duration_seconds_sum") ||
		!strings.Contains(resp.Header.Get("Content-Type"), "text/plain") {
		t.Fatalf("unexpected metrics response: status=%d body=%s", resp.StatusCode, body)
	}
}

func TestTaskKindCatalogIsServedByGo(t *testing.T) {
	server, legacy := testServer(t, false)
	defer server.Close()
	defer legacy.Close()

	resp, err := http.Get(server.URL + "/api/task-kinds")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status=%d", resp.StatusCode)
	}
	var body struct {
		Data struct {
			Items []map[string]any `json:"items"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	if len(body.Data.Items) < 8 {
		t.Fatalf("task kinds=%d", len(body.Data.Items))
	}
}

func TestUnknownAPIPathDoesNotCrossLegacyCatchAll(t *testing.T) {
	api, legacy := testServer(t, false)
	defer api.Close()
	defer legacy.Close()

	resp, err := http.Get(api.URL + "/api/not-a-declared-contract")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("unknown API status=%d, want 404", resp.StatusCode)
	}
}

func TestAuthRejectsAndAcceptsAPIKey(t *testing.T) {
	server, legacy := testServer(t, true)
	defer server.Close()
	defer legacy.Close()

	resp, _ := http.Get(server.URL + "/api/me")
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unexpected unauthenticated status: %d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/api/me", nil)
	req.Header.Set("X-API-Key", "test-key")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("authenticated status=%d", resp.StatusCode)
	}
}

func TestBrowserSessionAuthenticatesNativeEventSourceRequests(t *testing.T) {
	server, legacy := testServer(t, true)
	defer server.Close()
	defer legacy.Close()

	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	client := &http.Client{Jar: jar}
	req, _ := http.NewRequest(http.MethodPost, server.URL+"/api/auth/session", nil)
	req.Header.Set("X-API-Key", "test-key")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("session status=%d", resp.StatusCode)
	}

	req, _ = http.NewRequest(http.MethodGet, server.URL+"/api/me", nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("cookie-authenticated status=%d", resp.StatusCode)
	}
}

func TestRBACPrincipalAndResourceScope(t *testing.T) {
	legacy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"code":0,"message":"ok","data":{}}`)
	}))
	defer legacy.Close()
	handler := New(config.Config{
		ListenAddr: ":0", AuthEnabled: true,
		Principals: []config.Principal{
			{ID: "reader", APIKey: "reader-key", Roles: []string{"viewer"}, AgentIDs: []string{"agent-a"}},
			{ID: "operator-a", APIKey: "operator-key", Roles: []string{"operator"}, AgentIDs: []string{"agent-a"}, ServiceIDs: []string{"service-a"}, Environments: []string{"staging"}},
			{ID: "approver", APIKey: "approver-key", Roles: []string{"approver"}, AgentIDs: []string{"*"}, ServiceIDs: []string{"*"}, Environments: []string{"*"}},
		},
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	server := httptest.NewServer(handler)
	defer server.Close()

	request := func(method, path, key, body string) *http.Response {
		req, err := http.NewRequest(method, server.URL+path, bytes.NewBufferString(body))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("X-API-Key", key)
		req.Header.Set("Content-Type", "application/json")
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		return resp
	}

	resp := request(http.MethodGet, "/api/me", "reader-key", "")
	var me map[string]any
	_ = json.NewDecoder(resp.Body).Decode(&me)
	_ = resp.Body.Close()
	data := me["data"].(map[string]any)
	if resp.StatusCode != http.StatusOK || data["user_id"] != "reader" {
		t.Fatalf("unexpected /me response: status=%d body=%#v", resp.StatusCode, me)
	}

	resp = request(http.MethodPost, "/api/v2/diagnostic-skills/skill-a/publish", "operator-key", `{}`)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("operator must not publish a Skill: status=%d", resp.StatusCode)
	}

	resp = request(http.MethodPost, "/api/v2/diagnostic-skills/skill-a/campaign", "approver-key", `{}`)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("approver should pass governance RBAC before unavailable worker: status=%d", resp.StatusCode)
	}

	resp = request(http.MethodPost, "/api/v2/diagnostic-experiments/exp-a/approve", "operator-key", `{"reason":"ship"}`)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("operator must not approve an experiment rollout: status=%d", resp.StatusCode)
	}

	resp = request(http.MethodPost, "/api/v2/diagnostic-experiments/exp-a/approve", "approver-key", `{"reason":"ship"}`)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("approver should pass experiment RBAC before unavailable worker: status=%d", resp.StatusCode)
	}

}

func TestEventCursorRoundTrip(t *testing.T) {
	raw := formatEventCursor(123, 456)
	taskID, auditID := parseEventCursor(raw)
	if taskID != 123 || auditID != 456 {
		t.Fatalf("unexpected cursor values: %d %d", taskID, auditID)
	}
}

func TestLegacyNumericEventCursor(t *testing.T) {
	taskID, auditID := parseEventCursor("42")
	if taskID != 42 || auditID != 0 {
		t.Fatalf("unexpected legacy cursor values: %d %d", taskID, auditID)
	}
}

func TestValidateArtifactLocation(t *testing.T) {
	s := &Server{cfg: config.Config{MinIOBucket: "mini-drop"}}
	valid := repository.Artifact{
		TaskID: "task-1", Bucket: "mini-drop", ObjectKey: "tasks/task-1/result/data.json",
	}
	bucket, key, err := s.validateArtifactLocation(valid)
	if err != nil || bucket != "mini-drop" || key != valid.ObjectKey {
		t.Fatalf("valid location rejected: bucket=%q key=%q err=%v", bucket, key, err)
	}
	invalid := []repository.Artifact{
		{TaskID: "task-1", Bucket: "other", ObjectKey: valid.ObjectKey},
		{TaskID: "task-1", Bucket: "mini-drop", ObjectKey: "tasks/task-2/data.json"},
		{TaskID: "task-1", Bucket: "mini-drop", ObjectKey: "tasks/task-1/../secret"},
		{TaskID: "task-1", Bucket: "mini-drop", ObjectKey: "/tasks/task-1/data.json"},
	}
	for _, artifact := range invalid {
		if _, _, err := s.validateArtifactLocation(artifact); err == nil {
			t.Fatalf("invalid location accepted: %#v", artifact)
		}
	}
}

func TestSafeDownloadFilename(t *testing.T) {
	if got := safeDownloadFilename(`../../report\flame.svg`); got != "flame.svg" {
		t.Fatalf("unexpected filename: %q", got)
	}
	if got := safeDownloadFilename("bad\r\nname;\".json"); got != "badname.json" {
		t.Fatalf("unsafe characters not removed: %q", got)
	}
	if got := safeDownloadFilename(""); got != "artifact.bin" {
		t.Fatalf("unexpected empty fallback: %q", got)
	}
}
