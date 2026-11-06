package main

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func newTestStore(t *testing.T) *store {
	t.Helper()
	s, err := newStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return s
}
func request(a *api, method, path, body, key, origin string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, "http://127.0.0.1:4330"+path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Idempotency-Key", key)
	if origin != "" {
		r.Header.Set("Origin", origin)
	}
	w := httptest.NewRecorder()
	a.ServeHTTP(w, r)
	return w
}

func TestCreateReplayAndConflict(t *testing.T) {
	a := &api{newTestStore(t)}
	if w := request(a, "POST", "/tasks", `{"iterations":1000}`, "create-001", ""); w.Code != 201 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := request(a, "POST", "/tasks", `{"iterations":1000}`, "create-001", ""); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := request(a, "POST", "/tasks", `{"iterations":2000}`, "create-001", ""); w.Code != 409 {
		t.Fatal(w.Code, w.Body.String())
	}
	if len(a.store.list()) != 1 || len(a.store.queue) != 1 {
		t.Fatal("duplicate request created work")
	}
}

func TestFailedJournalWriteCannotPublishMemoryOrQueue(t *testing.T) {
	s := newTestStore(t)
	// A directory at the final file name deterministically makes rename fail,
	// without depending on Unix chmod semantics or touching an existing journal.
	if err := os.Mkdir(filepath.Join(s.root, "journal.json"), 0700); err != nil {
		t.Fatal(err)
	}
	if _, _, err := s.create(1000, "disk-fail-01"); err == nil {
		t.Fatal("journal write unexpectedly succeeded")
	}
	if len(s.list()) != 0 || len(s.queue) != 0 || len(s.state.Keys) != 0 || s.state.NextID != 1 {
		t.Fatal("failed disk commit leaked task, key, ID, or queued work")
	}
}

func TestStrictInputAndOrigin(t *testing.T) {
	a := &api{newTestStore(t)}
	for _, body := range []string{`{"iterations":1000,"command":"ignored"}`, `{"iterations":1000,"iterations":2000}`, `{"iterations":true}`, `{"iterations":1000} {}`, `null`, `{"iterations":999}`} {
		if w := request(a, "POST", "/tasks", body, "strict-001", ""); w.Code != 400 {
			t.Fatal(body, w.Code)
		}
	}
	if w := request(a, "POST", "/tasks", `{"iterations":1000}`, "strict-002", "https://example.invalid"); w.Code != 403 {
		t.Fatal(w.Code)
	}
	if w := request(a, "POST", "/tasks", `{"iterations":1000}`, "strict-003", "http://127.0.0.1:4322"); w.Code != 201 {
		t.Fatal(w.Code)
	}
}

func TestRestartFailsRunningAndRequeuesQueued(t *testing.T) {
	s := newTestStore(t)
	first, _, err := s.create(1000, "restart-01")
	if err != nil {
		t.Fatal(err)
	}
	job := <-s.queue
	if _, err = s.begin(job); err != nil {
		t.Fatal(err)
	}
	second, _, err := s.create(2000, "restart-02")
	if err != nil {
		t.Fatal(err)
	}
	reopened, err := newStore(s.root)
	if err != nil {
		t.Fatal(err)
	}
	if err = reopened.recover(); err != nil {
		t.Fatal(err)
	}
	if got := reopened.get(first.ID); got.Status != "FAILED" || got.ErrorCode != "INTERRUPTED_RESTART" || got.Events[len(got.Events)-1].Kind != "TASK_INTERRUPTED" {
		t.Fatal(got)
	}
	if got := <-reopened.queue; got.ID != second.ID {
		t.Fatal("running work was blindly resampled")
	}
	if len(reopened.queue) != 0 {
		t.Fatal("unexpected additional work")
	}
}

func TestOldAttemptCannotPublishAfterRetry(t *testing.T) {
	s := newTestStore(t)
	created, _, err := s.create(1000, "attempt-01")
	if err != nil {
		t.Fatal(err)
	}
	old := <-s.queue
	if _, err = s.begin(old); err != nil {
		t.Fatal(err)
	}
	if err = s.finish(old, nil, "TEACHING_FAILURE"); err != nil {
		t.Fatal(err)
	}
	retried, _, err := s.retry(created.ID, "attempt-02")
	if err != nil || retried.Attempt != 2 {
		t.Fatal(retried, err)
	}
	currentJob := <-s.queue
	if _, err = s.begin(currentJob); err != nil {
		t.Fatal(err)
	}
	if err = s.finish(old, json.RawMessage(`{"wrong":true}`), ""); err != errStale {
		t.Fatal("old publication accepted", err)
	}
	current := s.get(created.ID)
	if current.Attempt != 2 || current.Status != "RUNNING" || len(current.Result) != 0 {
		t.Fatal(current)
	}
	if _, replay, err := s.retry(created.ID, "attempt-02"); err != nil || !replay || len(s.queue) != 0 {
		t.Fatal("retry replay created another attempt", err)
	}
}

func TestResultHashAndUnitsMustMatch(t *testing.T) {
	raw := []byte(`{"schema_version":1,"source":"controlled_self","unit":"cpu_ticks","clock_ticks_per_second":1000,"total_ticks":3,"iterations":1000,"checksum":"0123456789abcdef"}`)
	profile, ok := validateRaw(raw, 1000)
	if !ok {
		t.Fatal("valid reference raw rejected")
	}
	result := map[string]any{"schema_version": 1, "summary": map[string]any{"source": "controlled_self", "unit": "cpu_ticks", "total_ticks": 3, "clock_ticks_per_second": 1000, "limitations": []string{"self CPU only"}}, "flamegraph": map[string]any{"name": "all", "value": 3, "self": 0, "children": []any{map[string]any{"name": "controlled_process", "value": 3, "self": 3, "children": []any{}}}}, "input_sha256": inputDigest(raw)}
	encoded, _ := json.Marshal(result)
	if !validateResult(encoded, raw, profile) {
		t.Fatal("valid reference result rejected")
	}
	result["input_sha256"] = "wrong"
	encoded, _ = json.Marshal(result)
	if validateResult(encoded, raw, profile) {
		t.Fatal("wrong input SHA accepted")
	}
	result["input_sha256"] = inputDigest(raw)
	result["summary"].(map[string]any)["unit"] = "milliseconds"
	encoded, _ = json.Marshal(result)
	if validateResult(encoded, raw, profile) {
		t.Fatal("wrong measurement unit accepted")
	}
}
