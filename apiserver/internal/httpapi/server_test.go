package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"mini-drop/apiserver/internal/config"
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
	upstream, _ := url.Parse(legacy.URL)
	handler := New(config.Config{
		ListenAddr: ":0", LegacyAPIURL: upstream, AuthEnabled: auth, APIKey: "test-key",
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
		_ = resp.Body.Close()
	}
}

func TestProxyPreservesExistingAPI(t *testing.T) {
	server, legacy := testServer(t, false)
	defer server.Close()
	defer legacy.Close()
	resp, err := http.Get(server.URL + "/api/ai-config")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.Header.Get("X-Legacy") != "true" {
		t.Fatal("request did not reach legacy API")
	}
	if resp.Header.Get("X-Request-ID") == "" {
		t.Fatal("request id not returned")
	}
}

func TestProxyReplacesClientGatewayHeaderWithInternalCredential(t *testing.T) {
	var gotGatewayToken, gotPrincipal string
	legacy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotGatewayToken = r.Header.Get("X-Mini-Drop-Gateway-Token")
		gotPrincipal = r.Header.Get("X-Mini-Drop-Principal")
		_, _ = io.WriteString(w, `{"code":0}`)
	}))
	defer legacy.Close()
	upstream, _ := url.Parse(legacy.URL)
	handler := New(config.Config{
		LegacyAPIURL: upstream, AuthEnabled: true, InternalGatewayToken: "internal-secret",
		Principals: []config.Principal{{
			ID: "operator-a", APIKey: "operator-key", Roles: []string{"operator"},
			AgentIDs: []string{"agent-a"}, ServiceIDs: []string{"service-a"}, Environments: []string{"staging"},
		}},
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	server := httptest.NewServer(handler)
	defer server.Close()

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/api/ai-config", nil)
	req.Header.Set("X-API-Key", "operator-key")
	req.Header.Set("X-Mini-Drop-Gateway-Token", "attacker-controlled")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = resp.Body.Close()
	if gotGatewayToken != "internal-secret" || gotPrincipal != "operator-a" {
		t.Fatalf("unexpected upstream identity: token=%q principal=%q", gotGatewayToken, gotPrincipal)
	}
}

func TestRealWorldBenchmarkRoutesAreProxiedToPython(t *testing.T) {
	var requests []struct {
		method string
		path   string
		body   string
	}
	legacy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		requests = append(requests, struct {
			method string
			path   string
			body   string
		}{r.Method, r.URL.Path, string(body)})
		_, _ = io.WriteString(w, `{"code":0}`)
	}))
	defer legacy.Close()
	upstream, _ := url.Parse(legacy.URL)
	api := httptest.NewServer(New(config.Config{LegacyAPIURL: upstream}, slog.New(slog.NewTextHandler(io.Discard, nil))))
	defer api.Close()

	resp, err := http.Get(api.URL + "/api/v1/real-world-benchmarks/catalog")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	resp, err = http.Post(
		api.URL+"/api/v1/real-world-benchmarks/runs",
		"application/json",
		strings.NewReader(`{"case_id":"case-public-1"}`),
	)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()

	if len(requests) != 2 {
		t.Fatalf("proxied requests=%d, want 2", len(requests))
	}
	if requests[0].method != http.MethodGet || requests[0].path != "/api/v1/real-world-benchmarks/catalog" {
		t.Fatalf("unexpected catalog request: %#v", requests[0])
	}
	if requests[1].method != http.MethodPost || requests[1].path != "/api/v1/real-world-benchmarks/runs" || requests[1].body != `{"case_id":"case-public-1"}` {
		t.Fatalf("unexpected run request: %#v", requests[1])
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

func TestCompositeRoutesAreProxiedToPython(t *testing.T) {
	api, legacy := testServer(t, false)
	defer api.Close()
	defer legacy.Close()

	// Schedules are now native Go handlers (tested in schedules_test.go);
	// composite tasks remain proxied to the Python engine.
	for _, path := range []string{
		"/api/composite-tasks",
		"/api/composite-tasks/composite_1/aggregate",
	} {
		resp, err := http.Get(api.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("path %s status=%d, want 200 (proxied to Python)", path, resp.StatusCode)
		}
	}
}

func TestAuthRejectsAndAcceptsAPIKey(t *testing.T) {
	server, legacy := testServer(t, true)
	defer server.Close()
	defer legacy.Close()

	resp, _ := http.Get(server.URL + "/api/ai-config")
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unexpected unauthenticated status: %d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	req, _ := http.NewRequest(http.MethodGet, server.URL+"/api/ai-config", nil)
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

func TestRBACPrincipalAndResourceScope(t *testing.T) {
	legacy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, `{"code":0,"message":"ok","data":{}}`)
	}))
	defer legacy.Close()
	upstream, _ := url.Parse(legacy.URL)
	handler := New(config.Config{
		ListenAddr: ":0", LegacyAPIURL: upstream, AuthEnabled: true,
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

	resp = request(http.MethodPost, "/api/v1/diagnoses", "reader-key", `{"query":"check cpu"}`)
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("viewer write status=%d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	resp = request(http.MethodPost, "/api/v1/diagnoses", "operator-key", `{"query":"check cpu","context":{"agent_id":"agent-b","service_id":"service-a","environment":"staging"}}`)
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("out-of-scope diagnosis status=%d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	resp = request(http.MethodPost, "/api/v1/diagnoses/diag-1/approvals", "operator-key", `{"step_id":"step-1","decision":"approve"}`)
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("operator approval status=%d", resp.StatusCode)
	}
	_ = resp.Body.Close()

	resp = request(http.MethodPost, "/api/v1/diagnoses/diag-1/approvals", "approver-key", `{"step_id":"step-1","decision":"approve"}`)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("approver status=%d", resp.StatusCode)
	}
	_ = resp.Body.Close()
}

func TestCompositeEventCursorRoundTrip(t *testing.T) {
	raw := formatEventCursor(123, 456, 789)
	taskID, auditID, diagnosisID := parseEventCursor(raw)
	if taskID != 123 || auditID != 456 || diagnosisID != 789 {
		t.Fatalf("unexpected cursor values: %d %d %d", taskID, auditID, diagnosisID)
	}
}

func TestLegacyNumericEventCursor(t *testing.T) {
	taskID, auditID, diagnosisID := parseEventCursor("42")
	if taskID != 42 || auditID != 0 || diagnosisID != 0 {
		t.Fatalf("unexpected legacy cursor values: %d %d %d", taskID, auditID, diagnosisID)
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

func TestDiagnosisWriteBoundaryValidatesAndForwards(t *testing.T) {
	server, legacy := testServer(t, false)
	defer server.Close()
	defer legacy.Close()

	bad := []string{
		`{"query":"x"}`,
		`{"query":"CPU is high","budget_profile":"unbounded"}`,
		`{"query":"CPU is high","unknown":true}`,
		`{"query":"CPU is high","evaluation_oracle":{"root":"secret"}}`,
		`{"query":"CPU is high","case_id":"` + strings.Repeat("c", 129) + `"}`,
		`{"query":"CPU is high","baseline_task_ids":["task-1","task-1"]}`,
		`{"query":"CPU is high","baseline_task_ids":["../task-1"]}`,
	}
	for _, payload := range bad {
		resp, err := http.Post(server.URL+"/api/v1/diagnoses", "application/json", bytes.NewBufferString(payload))
		if err != nil {
			t.Fatal(err)
		}
		if resp.StatusCode != http.StatusBadRequest {
			t.Fatalf("invalid command accepted: status=%d payload=%s", resp.StatusCode, payload)
		}
		_ = resp.Body.Close()
	}

	payload := `{"query":"order service CPU is high","case_id":"  case-public-1  ","budget_profile":"production_safe","baseline_task_ids":["task-baseline-1"]}`
	resp, err := http.Post(server.URL+"/api/v1/diagnoses", "application/json", bytes.NewBufferString(payload))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK || resp.Header.Get("X-Legacy") != "true" {
		t.Fatalf("valid diagnosis command was not forwarded: status=%d", resp.StatusCode)
	}
}

func TestDiagnosisApprovalBoundaryRejectsBroadScope(t *testing.T) {
	server, legacy := testServer(t, false)
	defer server.Close()
	defer legacy.Close()
	payload := `{"step_id":"step-1","decision":"approve","scope":"all_future"}`
	resp, err := http.Post(server.URL+"/api/v1/diagnoses/diag-1/approvals", "application/json", bytes.NewBufferString(payload))
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("broad approval scope accepted: status=%d", resp.StatusCode)
	}
}

func TestAIWriteAuditEventTypesFitDatabaseContract(t *testing.T) {
	for _, eventType := range []string{diagnosisCommandAcceptedEvent, probeApprovalAcceptedEvent} {
		if len(eventType) > 32 {
			t.Fatalf("audit event type exceeds audit_logs.event_type varchar(32): %q", eventType)
		}
	}
}
