package service

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

func investigationFixture(t *testing.T) (*gorm.DB, *Suggestions, domain.User, domain.Task) {
	t.Helper()
	db, s, u, task := diagnosisFixture(t)
	u.Role = "platform_admin"
	if err := db.Model(&domain.User{}).Where("id=?", u.ID).Update("role", u.Role).Error; err != nil {
		t.Fatal(err)
	}
	aid := "investigation-fixture-agent"
	a := domain.Agent{ID: aid, Hostname: "explicit-test-fixture", Version: "fixture", Platform: "linux", Status: "online", Capabilities: json.RawMessage(`["PERF_CPU","PROC_CPU","RESOURCE_SNAPSHOT","SCRIPT"]`), Labels: json.RawMessage(`{}`), Resources: json.RawMessage(`{"processes":[{"pid":7,"uid":10001,"start_time_ticks":42}],"target_policy":{"host_processes":true,"allowed_uids":[10001]}}`), LastSeenAt: time.Now()}
	if err := db.Create(&a).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&task).Updates(map[string]any{"kind": "PERF_CPU", "target_agent_id": aid, "params": `{"pid":7,"duration_seconds":5,"frequency_hz":99,"scope":"process","target":{"type":"process","pid":7,"expected_uid":10001,"check_uid":true,"start_time_ticks":42}}`}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec(`UPDATE analysis_jobs SET result=jsonb_set(jsonb_set(result,'{kind}','"PERF_CPU"'),'{summary}','{"unit":"samples"}') WHERE task_id=?`, task.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.First(&task, task.ID).Error; err != nil {
		t.Fatal(err)
	}
	return db, s, u, task
}
func TestDiagnosisComparisonDoesNotInventCausality(t *testing.T) {
	aid := "fixture"
	task := domain.Task{Kind: "PERF_CPU", AgentID: &aid, Parameters: json.RawMessage(`{"pid":7,"scope":"process"}`)}
	a := json.RawMessage(`{"total_samples":2000,"summary":{"unit":"samples"},"top_functions":[{"name":"private_customer_function","self_percent":80}]}`)
	b := json.RawMessage(`{"total_samples":3000,"summary":{"unit":"samples"},"top_functions":[{"name":"private_customer_function","self_percent":20}]}`)
	got, err := compareProfiles(a, b, task, task)
	if err != nil || !got.Comparable || got.RootCauseStatus != "not_tested" || got.Outcome != "comparison_ready" || len(got.Differences) != 1 || got.Differences[0].Delta != -60 {
		t.Fatal(got, err)
	}
	if got.Differences[0].Symbol == "private_customer_function" {
		t.Fatal("raw symbol leaked")
	}
	bad := task
	bad.Parameters = json.RawMessage(`{"pid":8,"scope":"process"}`)
	got, err = compareProfiles(a, b, task, bad)
	if err != nil || got.Comparable {
		t.Fatal("different target compared", err)
	}
	got, err = compareProfiles(a, json.RawMessage(`{"total_samples":3000,"summary":{"unit":"bytes"}}`), task, task)
	if err != nil || got.Comparable {
		t.Fatal("mixed units compared", err)
	}
	got, err = compareProfiles(a, json.RawMessage(`{"total_samples":30,"summary":{"unit":"samples"}}`), task, task)
	if err != nil || got.Outcome != "insufficient_evidence" {
		t.Fatal("small sample counted as proof", err)
	}
}
func TestPostgresDiagnosisAutomaticVerification(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	run := requestDiagnosisFixture(t, s, u, task, "evidence", "automatic-verification-fixture")
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	candidate, _, err := s.CollectDiagnosis(ctx, u, task.ID, run.ID, "automatic-confirmation-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err = s.CollectDiagnosis(ctx, u, task.ID, run.ID, "automatic-confirmation-fixture", "fixture"); err != nil {
		t.Fatal(err)
	}
	var count int64
	db.Model(&VerificationRun{}).Count(&count)
	if count != 1 {
		t.Fatal("duplicate automatic workflow", count)
	}
	if err := s.processVerification(ctx); err != nil {
		t.Fatal(err)
	}
	var verification VerificationRun
	db.First(&verification)
	if verification.CandidateDiagnosisID != nil {
		t.Fatal("diagnosed incomplete collection")
	}
	a, err := s.Repo.LatestAttempt(ctx, candidate.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&domain.Task{}).Where("id=?", candidate.ID).Updates(map[string]any{"collection_status": "COLLECTED", "analysis_status": "SUCCESS"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,attempt,result) SELECT ?,?,'folded_v1','success',1,result FROM analysis_jobs WHERE task_id=?", candidate.ID, a.ID, task.ID).Error; err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	errs := make(chan error, 4)
	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); errs <- s.processVerification(ctx) }()
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	db.First(&verification, verification.ID)
	if verification.CandidateDiagnosisID == nil {
		t.Fatal("no automatic candidate diagnosis")
	}
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.processVerification(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&verification, verification.ID)
	if verification.Status != "success" {
		t.Fatal("automatic workflow failed", verification.Status, verification.ErrorCode)
	}
	var report struct{ Comparison ProfileComparison }
	if json.Unmarshal(verification.Report, &report) != nil || !report.Comparison.Comparable || report.Comparison.RootCauseStatus != "not_tested" {
		t.Fatal("invalid comparison report")
	}
	var diagnosis DiagnosisRun
	db.First(&diagnosis, *verification.CandidateDiagnosisID)
	var trace []DiagnosisStep
	json.Unmarshal(diagnosis.Trace, &trace)
	found := false
	for _, step := range trace {
		if step.Tool == "compare_profiles" {
			found = true
		}
	}
	if !found {
		t.Fatal("candidate diagnosis did not actually compare")
	}
	// Lifecycle fixtures below exercise durable linkage, not real CRM profiling.
	rev := strings.Repeat("a", 64)
	s.Config.Endpoint = "http://explicit-unused-model-fixture.invalid"
	s.Code = &CodeExecutor{client: &http.Client{Transport: fixtureRoundTripper(func(req *http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(`{"data":{"id":"crm-python","source_revision":"` + rev + `"}}`)), Header: make(http.Header)}, nil
	})}}
	project := "crm-python"
	linked := Investigation{RequestedBy: u.ID, AgentID: *task.AgentID, Goal: "explicit linked-workflow fixture", Parameters: task.Parameters, Consent: true, RequestKey: "linked-fixture", InputDigest: "fixture", Status: "verifying", Trace: json.RawMessage(`[]`), BaselineTaskID: &task.ID, DiagnosisID: &run.ID, VerificationID: &verification.ID, CodeProjectID: &project, CodeSourceRevision: &rev}
	if err := db.Create(&linked).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.advanceInvestigations(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&linked, linked.ID)
	if linked.CodeRepairID == nil || linked.Status != "verifying" {
		t.Fatal("source job was not linked", linked.Status, linked.ErrorCode)
	}
	if err := db.Model(&CodeRepair{}).Where("id=?", *linked.CodeRepairID).Updates(map[string]any{"status": "success", "report": `{"summary":"explicit lifecycle fixture","validation":{"accepted":true,"functional_regression":"passed","root_cause_status":"validated_in_controlled_crm_benchmark"}}`}).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.advanceInvestigations(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&linked, linked.ID)
	if linked.Status != "success" || !strings.Contains(string(linked.Report), "code_validation") {
		t.Fatal("linked validation did not finish", linked.Status, linked.ErrorCode)
	}
}
func TestPostgresDiagnosisVerificationSourceReanalysisFence(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	run := requestDiagnosisFixture(t, s, u, task, "evidence", "verify-stale-fixture")
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	_, _, err := s.CollectDiagnosis(ctx, u, task.ID, run.ID, "verify-stale-confirmation", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("UPDATE analysis_jobs SET attempt=attempt+1 WHERE task_id=?", task.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.processVerification(ctx); err != nil {
		t.Fatal(err)
	}
	var v VerificationRun
	db.First(&v)
	if v.Status != "failed" || v.ErrorCode == nil || *v.ErrorCode != "STALE_ATTEMPT" || v.CandidateDiagnosisID != nil {
		t.Fatal("stale baseline allowed follow-up", v.Status, v.ErrorCode)
	}
}
func TestPostgresDiagnosisNaturalCollectionPlanningAndConfirmation(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		name, args := "list_collectors", `{}`
		if n == 2 {
			name = "propose_collection"
			args = `{"kind":"PERF_CPU","duration_seconds":4,"frequency_hz":999,"rationale":"explicit invalid test fixture"}`
		}
		if n >= 3 {
			name = "propose_collection"
			args = `{"kind":"PERF_CPU","duration_seconds":4,"frequency_hz":49,"rationale":"显式测试fixture：先取得函数级CPU证据"}`
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"message": map[string]any{"role": "assistant", "tool_calls": []any{map[string]any{"id": fmt.Sprintf("fixture-%d", n), "type": "function", "function": map[string]any{"name": name, "arguments": args}}}}}}})
	}))
	defer server.Close()
	s.Config = AIConfig{Endpoint: server.URL, Model: "explicit-planning-fixture", AllowHTTP: true}
	in := InvestigationInput{Goal: "CPU慢，请自行选择支持的采集器", AgentID: *task.AgentID, Parameters: domain.Parameters{PID: 7}, Consent: true}
	run, err := s.RequestInvestigation(ctx, u, in, "natural-collection-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	replay, err := s.RequestInvestigation(ctx, u, in, "natural-collection-fixture", "fixture")
	if err != nil || replay.ID != run.ID {
		t.Fatal("planning replay failed", err)
	}
	if err := s.planNextInvestigation(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&run, run.ID)
	if run.Status != "awaiting_confirmation" || calls.Load() != 3 {
		t.Fatal("planner did not repair invalid parameters", run.Status, calls.Load(), run.ErrorCode)
	}
	var count int64
	db.Model(&domain.Task{}).Count(&count)
	if count != 1 {
		t.Fatal("planning executed collection before confirmation")
	}
	if err := s.ConfirmInvestigation(ctx, u, run.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	if err := s.advanceInvestigations(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&run, run.ID)
	if run.Status != "collecting" || run.BaselineTaskID == nil {
		t.Fatal("confirmed plan did not create task", run.Status, run.ErrorCode)
	}
	var collected domain.Task
	db.First(&collected, *run.BaselineTaskID)
	var params domain.Parameters
	json.Unmarshal(collected.Parameters, &params)
	if collected.Kind != "PERF_CPU" || params.Target == nil || params.Target.StartTimeTicks != 42 || params.Target.ExpectedUID != 10001 {
		t.Fatal("planned collection lost target identity")
	}
	if err := s.CancelInvestigation(ctx, u, run.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	if err := s.advanceInvestigations(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&run, run.ID)
	if run.Status != "canceled" {
		t.Fatal("worker overwrote cancellation")
	}
}
