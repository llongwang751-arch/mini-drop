package httpapi

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
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
		_ = resp.Body.Close()
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

}

func TestCompositeEventCursorRoundTrip(t *testing.T) {
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
