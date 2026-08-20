package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"mini-drop/apiserver/internal/repository"
)

type fakeScheduleStore struct {
	schedules map[string]repository.Schedule
	records   map[string][]repository.ScheduleRecord
	createErr error
	fireErr   error
	nextID    int
}

func newFakeScheduleStore() *fakeScheduleStore {
	return &fakeScheduleStore{
		schedules: map[string]repository.Schedule{},
		records:   map[string][]repository.ScheduleRecord{},
	}
}

func (f *fakeScheduleStore) CreateSchedule(_ context.Context, input repository.CreateScheduleInput) (*repository.Schedule, error) {
	if f.createErr != nil {
		return nil, f.createErr
	}
	f.nextID++
	s := &repository.Schedule{
		ID: "schedule_test" + string(rune('0'+f.nextID)),
		Name: input.Name, CronExpression: input.CronExpression,
		Timezone: input.Timezone, TaskTemplate: input.TaskTemplate,
		Enabled: input.Enabled, NextRunAt: time.Now().UTC(),
		CreatedAt: time.Now().UTC(), UpdatedAt: time.Now().UTC(),
	}
	f.schedules[s.ID] = *s
	return s, nil
}

func (f *fakeScheduleStore) UpdateSchedule(_ context.Context, id string, input repository.CreateScheduleInput) (*repository.Schedule, error) {
	s, ok := f.schedules[id]
	if !ok {
		return nil, repository.ErrNotFound
	}
	s.Name, s.CronExpression, s.Timezone, s.TaskTemplate, s.Enabled = input.Name, input.CronExpression, input.Timezone, input.TaskTemplate, input.Enabled
	f.schedules[id] = s
	return &s, nil
}

func (f *fakeScheduleStore) DeleteSchedule(_ context.Context, id string) (bool, error) {
	if _, ok := f.schedules[id]; !ok {
		return false, nil
	}
	delete(f.schedules, id)
	return true, nil
}

func (f *fakeScheduleStore) ListSchedules(_ context.Context) ([]repository.Schedule, error) {
	out := make([]repository.Schedule, 0, len(f.schedules))
	for _, s := range f.schedules {
		out = append(out, s)
	}
	return out, nil
}

func (f *fakeScheduleStore) GetSchedule(_ context.Context, id string) (*repository.Schedule, error) {
	s, ok := f.schedules[id]
	if !ok {
		return nil, repository.ErrNotFound
	}
	return &s, nil
}

func (f *fakeScheduleStore) ListScheduleRecords(_ context.Context, id string) ([]repository.ScheduleRecord, error) {
	return f.records[id], nil
}

func (f *fakeScheduleStore) FireSchedule(_ context.Context, id string, _ time.Time) (string, error) {
	if f.fireErr != nil {
		return "", f.fireErr
	}
	if _, ok := f.schedules[id]; !ok {
		return "", repository.ErrNotFound
	}
	taskID := "task_schedule_" + id
	f.records[id] = append(f.records[id], repository.ScheduleRecord{ID: "r1", ScheduleID: id, TaskID: &taskID, Status: "created"})
	return taskID, nil
}

func scheduleMux(t *testing.T, store repository.ScheduleStore) *http.ServeMux {
	t.Helper()
	mux := http.NewServeMux()
	(&scheduleHandlers{store: store}).register(mux)
	return mux
}

// withPrincipal injects the development (admin, all-agents) principal that the
// auth middleware would have set, since these tests exercise the handler layer
// directly.
func withPrincipal(req *http.Request) *http.Request {
	return req.WithContext(context.WithValue(req.Context(), principalContextKey{}, developmentPrincipal()))
}

func TestScheduleHandlers_ListAndCreate(t *testing.T) {
	store := newFakeScheduleStore()
	mux := scheduleMux(t, store)

	// create
	body := `{"name":"nightly","cron_expression":"0 3 * * *","timezone":"Asia/Shanghai","enabled":true,"task_template":{"name":"t","agent_id":"agent_a","target_pid":1,"collector_type":"sys_metrics","sample_rate":11,"duration_sec":15}}`
	req := httptest.NewRequest(http.MethodPost, "/api/schedules", strings.NewReader(body))
	resp := httptest.NewRecorder()
	mux.ServeHTTP(resp, withPrincipal(req))
	if resp.Code != http.StatusOK {
		t.Fatalf("create status=%d body=%s", resp.Code, resp.Body.String())
	}
	var created struct {
		Data repository.Schedule `json:"data"`
	}
	if err := json.Unmarshal(resp.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	if created.Data.Name != "nightly" || created.Data.CronExpression != "0 3 * * *" {
		t.Fatalf("unexpected created schedule: %+v", created.Data)
	}

	// list
	req = httptest.NewRequest(http.MethodGet, "/api/schedules", nil)
	resp = httptest.NewRecorder()
	mux.ServeHTTP(resp, withPrincipal(req))
	if resp.Code != http.StatusOK {
		t.Fatalf("list status=%d", resp.Code)
	}
	var listed struct {
		Data struct {
			Items []repository.Schedule `json:"items"`
		} `json:"data"`
	}
	if err := json.Unmarshal(resp.Body.Bytes(), &listed); err != nil {
		t.Fatal(err)
	}
	if len(listed.Data.Items) != 1 {
		t.Fatalf("expected 1 schedule, got %d", len(listed.Data.Items))
	}
}

func TestScheduleHandlers_TriggerAndRecords(t *testing.T) {
	store := newFakeScheduleStore()
	store.schedules["schedule_1"] = repository.Schedule{
		ID: "schedule_1", Name: "s", CronExpression: "0 3 * * *", Timezone: "Asia/Shanghai",
		TaskTemplate: map[string]any{"name": "t", "agent_id": "agent_a", "collector_type": "sys_metrics", "target_pid": 1, "sample_rate": 11, "duration_sec": 15},
		Enabled: true,
	}
	mux := scheduleMux(t, store)

	req := httptest.NewRequest(http.MethodPost, "/api/schedules/schedule_1/trigger", nil)
	resp := httptest.NewRecorder()
	mux.ServeHTTP(resp, withPrincipal(req))
	if resp.Code != http.StatusOK {
		t.Fatalf("trigger status=%d body=%s", resp.Code, resp.Body.String())
	}
	var tr struct {
		Data struct {
			TaskID string `json:"task_id"`
		} `json:"data"`
	}
	if err := json.Unmarshal(resp.Body.Bytes(), &tr); err != nil {
		t.Fatal(err)
	}
	if tr.Data.TaskID == "" {
		t.Fatal("expected a task_id from trigger")
	}

	req = httptest.NewRequest(http.MethodGet, "/api/schedules/schedule_1/records", nil)
	resp = httptest.NewRecorder()
	mux.ServeHTTP(resp, withPrincipal(req))
	if resp.Code != http.StatusOK {
		t.Fatalf("records status=%d", resp.Code)
	}
	var rec struct {
		Data struct {
			Items []repository.ScheduleRecord `json:"items"`
		} `json:"data"`
	}
	if err := json.Unmarshal(resp.Body.Bytes(), &rec); err != nil {
		t.Fatal(err)
	}
	if len(rec.Data.Items) != 1 {
		t.Fatalf("expected 1 record, got %d", len(rec.Data.Items))
	}
}

func TestScheduleHandlers_AgentScopeRejected(t *testing.T) {
	store := newFakeScheduleStore()
	mux := scheduleMux(t, store)

	// developmentPrincipal allows "*"; simulate a restricted principal by
	// setting the context to a principal whose AgentIDs do not include agent_b.
	restricted := &requestPrincipal{
		ID: "viewer", Roles: []string{"operator"}, AgentIDs: []string{"agent_a"},
		ServiceIDs: []string{"*"}, Environments: []string{"*"},
	}
	body := `{"name":"x","cron_expression":"0 3 * * *","task_template":{"agent_id":"agent_b","collector_type":"sys_metrics"}}`
	req := httptest.NewRequest(http.MethodPost, "/api/schedules", strings.NewReader(body))
	req = req.WithContext(context.WithValue(req.Context(), principalContextKey{}, restricted))
	resp := httptest.NewRecorder()
	mux.ServeHTTP(resp, req)
	if resp.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for out-of-scope agent, got %d body=%s", resp.Code, resp.Body.String())
	}
}

func TestScheduleHandlers_CreateValidationError(t *testing.T) {
	store := newFakeScheduleStore()
	store.createErr = errors.New("invalid cron/timezone")
	mux := scheduleMux(t, store)
	body := `{"name":"x","cron_expression":"not valid","task_template":{}}`
	req := httptest.NewRequest(http.MethodPost, "/api/schedules", strings.NewReader(body))
	resp := httptest.NewRecorder()
	mux.ServeHTTP(resp, withPrincipal(req))
	if resp.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid cron, got %d body=%s", resp.Code, resp.Body.String())
	}
}
