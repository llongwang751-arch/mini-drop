package service

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/testutil"
	"gorm.io/gorm"
)

func loadDiagnosisCatalog(t *testing.T) {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "task-kinds.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err := domain.LoadKinds(b); err != nil {
		t.Fatal(err)
	}
}
func TestDiagnosisToolBoundary(t *testing.T) {
	loadDiagnosisCatalog(t)
	// Explicitly imported unit fixture, never a production measurement.
	raw := json.RawMessage(`{"kind":"PERF_CPU","total_samples":100,"top_functions":[{"name":"/customer/private/secret-function","self_percent":80,"total_percent":90}]}`)
	task := domain.Task{Kind: "PERF_CPU", Parameters: json.RawMessage(`{"pid":7,"duration_seconds":5,"frequency_hz":99,"scope":"process"}`)}
	for _, name := range diagnosisToolNames() {
		b, _, err := diagnosisTool(name, json.RawMessage(`{}`), raw, task)
		if err != nil {
			t.Fatal(name, err)
		}
		if strings.Contains(string(b), "customer") || strings.Contains(string(b), "secret-function") || strings.Contains(string(b), "\"pid\"") {
			t.Fatal("tool leaked an identifier")
		}
	}
	cases := []struct{ name, args string }{
		{"shell", `{}`}, {"inspect_hotspots", `{"command":"rm"}`}, {"inspect_hotspots", `{} junk`}, {"inspect_hotspots", `{} {}`}, {"inspect_hotspots", `null`},
		{"inspect_hotspots", `{"duration_seconds":10}`}, {"propose_verification", `{"kind":"SCRIPT"}`}, {"propose_verification", `{"kind":"GO_CPU"}`},
		{"propose_verification", `{"duration_seconds":61}`}, {"propose_verification", `{"frequency_hz":100000}`}, {"propose_verification", `{"duration_seconds":-1}`},
	}
	for _, c := range cases {
		t.Run(c.name+c.args, func(t *testing.T) {
			if _, _, err := diagnosisTool(c.name, json.RawMessage(c.args), raw, task); err == nil {
				t.Fatal("unsafe tool accepted")
			}
		})
	}
	invalid := json.RawMessage(`{"kind":"PERF_CPU","top_functions":[{"self_percent":101}]}`)
	if _, _, err := diagnosisTool("inspect_hotspots", json.RawMessage(`{}`), invalid, task); err == nil {
		t.Fatal("invalid measurement accepted")
	}
}
func TestDiagnosisReportEvidenceValidation(t *testing.T) {
	trace := []DiagnosisStep{{EvidenceID: "E1"}}
	good := `{"summary":"线索待验证","hypotheses":[{"claim":"热点集中","evidence_ids":["E1"],"verification":"保持负载复测"}],"limitations":["没有业务延迟"]}`
	if _, err := validateDiagnosisReport(good, trace); err != nil {
		t.Fatal(err)
	}
	if _, err := validateDiagnosisReport(strings.Replace(good, "热点集中", "样本数<1000且CPU占比>0，仅描述差异", 1), trace); err != nil {
		t.Fatal("numeric inequalities are plain text", err)
	}
	for _, bad := range []string{strings.Replace(good, "E1", "E99", 1), good + " {}", strings.Replace(good, "热点集中", "<script>x</script>", 1), strings.Replace(good, "\"summary\":", "\"shell\":\"rm\",\"summary\":", 1), strings.Replace(good, "\"limitations\":", "\"plan\":{},\"limitations\":", 1)} {
		if _, err := validateDiagnosisReport(bad, trace); err == nil {
			t.Fatal("invalid report accepted")
		}
	}
}

func TestDiagnosisLastRoundFormatRepairIsBounded(t *testing.T) {
	for _, fixed := range []bool{true, false} {
		t.Run(fmt.Sprint(fixed), func(t *testing.T) {
			var calls atomic.Int64
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				n := calls.Add(1)
				message := map[string]any{}
				finish := "stop"
				if n <= 3 {
					finish = "tool_calls"
					name := "inspect_hotspots"
					if n == 2 {
						name = "check_collection_quality"
					}
					message["tool_calls"] = []any{map[string]any{"id": fmt.Sprint(n), "type": "function", "function": map[string]any{"name": name, "arguments": "{}"}}}
				} else {
					ref := "E99"
					if n == 5 && fixed {
						ref = "E2"
					}
					if n == 5 {
						var body map[string]any
						json.NewDecoder(r.Body).Decode(&body)
						if _, ok := body["tools"]; ok {
							t.Error("format repair exposed tools")
						}
					}
					message["content"] = `{"summary":"fixture","hypotheses":[{"claim":"待验证","evidence_ids":["` + ref + `"],"verification":"复测"}],"limitations":[]}`
				}
				json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"finish_reason": finish, "message": message}}})
			}))
			defer server.Close()
			s := Suggestions{Config: AIConfig{Endpoint: server.URL, Model: "format-repair-fixture"}, Client: server.Client()}
			trace := []DiagnosisStep{{EvidenceID: "E1", Observation: json.RawMessage(`{}`)}}
			first, _ := json.Marshal(trace[0])
			_, err := s.diagnosisModel(context.Background(), "fixture", first, func(name string, args json.RawMessage) (json.RawMessage, error) {
				step := DiagnosisStep{EvidenceID: fmt.Sprintf("E%d", len(trace)+1), Tool: name, Observation: json.RawMessage(`{}`)}
				trace = append(trace, step)
				b, _ := json.Marshal(step)
				return b, nil
			}, &trace)
			if calls.Load() != 5 || (err == nil) != fixed {
				t.Fatal("format repair bypassed validation or budget", calls.Load(), err)
			}
		})
	}
}

func TestDiagnosisModelRepairsRejectedFinalWithinBudget(t *testing.T) {
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := calls.Add(1)
		message := map[string]any{}
		finish := "stop"
		if n == 1 {
			finish = "tool_calls"
			message["tool_calls"] = []any{map[string]any{"id": "h", "type": "function", "function": map[string]any{"name": "inspect_hotspots", "arguments": "{}"}}, map[string]any{"id": "q", "type": "function", "function": map[string]any{"name": "check_collection_quality", "arguments": "{}"}}}
		} else {
			id := "E99"
			if n == 3 {
				id = "E2"
			}
			message["content"] = `{"summary":"显式模型协议fixture","hypotheses":[{"claim":"待核对","evidence_ids":["` + id + `"],"verification":"保持负载复测"}],"limitations":[]}`
		}
		json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"finish_reason": finish, "message": message}}})
	}))
	defer server.Close()
	s := Suggestions{Config: AIConfig{Endpoint: server.URL, Model: "explicit-protocol-fixture"}, Client: server.Client()}
	trace := []DiagnosisStep{{EvidenceID: "E1", Tool: "read_profile_summary", Observation: json.RawMessage(`{}`)}}
	first, _ := json.Marshal(trace[0])
	report, err := s.diagnosisModel(context.Background(), "fixture", first, func(name string, args json.RawMessage) (json.RawMessage, error) {
		step := DiagnosisStep{EvidenceID: fmt.Sprintf("E%d", len(trace)+1), Tool: name, Observation: json.RawMessage(`{}`)}
		trace = append(trace, step)
		b, _ := json.Marshal(step)
		return b, nil
	}, &trace)
	if err != nil || report == nil || calls.Load() != 3 {
		t.Fatal("invalid reference was not repaired within budget", err, calls.Load())
	}
}

func TestDiagnosisLocalSymbolHints(t *testing.T) {
	for symbol, want := range map[string]string{"pthread_mutex_lock": "lock_or_wait", "runtime.mallocgc": "allocation", "G1Collect": "garbage_collection", "epoll_wait": "io_or_network", "sched_yield": "scheduler", "customer_internal_name": "unknown"} {
		if got := diagnosisSymbolHint(symbol); got != want {
			t.Errorf("hint classification: got %s want %s", got, want)
		}
	}
}

func TestDiagnosisCallPathEvidenceAndBudgets(t *testing.T) {
	raw := json.RawMessage(`{"flamegraph":{"name":"all","value":100,"self":0,"children":[{"name":"/private/customer/caller","value":100,"self":10,"children":[{"name":"pthread_mutex_lock","value":90,"self":80,"children":[{"name":"/private/customer/child","value":10,"self":10}]}]}]}}`)
	b, err := diagnosisCallPaths(raw, "pthread_mutex_lock")
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range []string{"private", "customer", "pthread_mutex_lock"} {
		if strings.Contains(string(b), secret) {
			t.Fatal("call path symbol leaked")
		}
	}
	var out struct {
		Available     bool
		Occurrences   []map[string]any
		ExaminedNodes int `json:"examined_nodes"`
		Truncated     bool
	}
	if json.Unmarshal(b, &out) != nil || !out.Available || len(out.Occurrences) != 1 {
		t.Fatal("call path lost")
	}
	children := make([]map[string]any, 5000)
	for i := range children {
		children[i] = map[string]any{"name": fmt.Sprintf("fixture-%d", i), "value": 1, "self": 1}
	}
	many, _ := json.Marshal(map[string]any{"flamegraph": map[string]any{"name": "all", "value": 5000, "self": 0, "children": children}})
	b, err = diagnosisCallPaths(many, "fixture-0")
	if err != nil {
		t.Fatal(err)
	}
	json.Unmarshal(b, &out)
	if out.ExaminedNodes > 4096 || !out.Truncated {
		t.Fatal("tree traversal exceeded budget")
	}
	b, err = diagnosisCallPaths(json.RawMessage(strings.Repeat("x", (8<<20)+1)), "fixture")
	if err != nil || !strings.Contains(string(b), "interactive_budget") {
		t.Fatal("size limit missing")
	}
}

func TestDiagnosisCallPathMarksEveryContextLimit(t *testing.T) {
	// Imported trees exercise omissions in returned context, even when the whole
	// source tree fits the traversal budget.
	leaf := func() *diagnosisTree { return &diagnosisTree{Name: "fixture-hotspot", Value: 1, Self: 1} }
	deep := leaf()
	for i := 0; i < 8; i++ {
		deep = &diagnosisTree{Name: "fixture-parent", Value: 1, Children: []*diagnosisTree{deep}}
	}
	wide := &diagnosisTree{Name: "fixture-hotspot", Value: 4}
	for i := 0; i < 4; i++ {
		wide.Children = append(wide.Children, &diagnosisTree{Name: "fixture-child", Value: 1, Self: 1})
	}
	repeated := &diagnosisTree{Name: "all", Value: 7}
	for i := 0; i < 7; i++ {
		repeated.Children = append(repeated.Children, leaf())
	}
	for _, c := range []struct {
		name, flag string
		tree       *diagnosisTree
	}{{"ancestors", "ancestors_truncated", deep}, {"callees", "callees_truncated", wide}, {"occurrences", "occurrences_truncated", repeated}} {
		t.Run(c.name, func(t *testing.T) {
			raw, _ := json.Marshal(map[string]any{"flamegraph": c.tree})
			b, err := diagnosisCallPaths(raw, "fixture-hotspot")
			var out map[string]any
			if err != nil || json.Unmarshal(b, &out) != nil || out["truncated"] != true || out["tree_truncated"] != false {
				t.Fatal("context omission must be explicit and separate from traversal truncation", string(b), err)
			}
			if c.name == "occurrences" {
				if out[c.flag] != true || out["matched_occurrences"] != float64(7) || len(out["occurrences"].([]any)) != 5 {
					t.Fatal("hidden occurrences were not marked")
				}
			} else if out["occurrences"].([]any)[0].(map[string]any)[c.flag] != true {
				t.Fatal("partial occurrence context was not marked")
			}
		})
	}
}

func diagnosisFixture(t *testing.T) (*gorm.DB, *Suggestions, domain.User, domain.Task) {
	t.Helper()
	db := testutil.Postgres(t)
	repo := repository.New(db)
	ctx := context.Background()
	u := domain.User{ID: "diagnosis-owner", Username: "diagnosis-owner", Role: "operator"}
	if err := db.Create(&u).Error; err != nil {
		t.Fatal(err)
	}
	task, a, _, err := repo.CreateTask(ctx, domain.Task{Name: "Explicit imported diagnosis fixture", Kind: "IMPORT_COLLAPSED", OwnerID: u.ID, Visibility: "private", Parameters: json.RawMessage(`{"source":"import"}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", IdempotencyKey: "diagnosis-fixture", RequestID: "fixture"}, false, domain.Audit{Action: "fixture", ResourceType: "task", Detail: json.RawMessage(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&task).Updates(map[string]any{"collection_status": "COLLECTED", "analysis_status": "SUCCESS"}).Error; err != nil {
		t.Fatal(err)
	}
	raw := `{"kind":"IMPORT_COLLAPSED","total_samples":1000,"node_count":4,"top_functions":[{"name":"/private/customer/hotloop","self_percent":80,"total_percent":90},{"name":"idle","self_percent":20,"total_percent":20}]}`
	if err := db.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,attempt,result) VALUES(?,?,'folded_v1','success',1,?)", task.ID, a.ID, raw).Error; err != nil {
		t.Fatal(err)
	}
	ai, err := NewSuggestions(repo, &Tasks{Repo: repo}, AIConfig{})
	if err != nil {
		t.Fatal(err)
	}
	return db, ai, u, task
}
func requestDiagnosisFixture(t *testing.T, s *Suggestions, u domain.User, task domain.Task, mode, key string) DiagnosisRun {
	t.Helper()
	r, err := s.RequestDiagnosis(context.Background(), u, task.ID, DiagnosisInput{Goal: "检查热点与证据质量", Mode: mode, Consent: true}, "fixture", key)
	if err != nil {
		t.Fatal(err)
	}
	return r
}
func fixtureDiagnosisProvider(t *testing.T, s *Suggestions, hook func(int)) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	calls := &atomic.Int32{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := int(calls.Add(1))
		raw := make([]byte, 0)
		var body map[string]json.RawMessage
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		raw, _ = json.Marshal(body)
		if strings.Contains(string(raw), "private/customer") || strings.Contains(string(raw), "hotloop") {
			t.Error("raw symbol leaked to provider")
		}
		if hook != nil {
			hook(n)
		}
		var messages []diagnosisMessage
		json.Unmarshal(body["messages"], &messages)
		toolResults := 0
		for _, m := range messages {
			if m.Role == "tool" {
				toolResults++
			}
		}
		call := func(id, name string) map[string]any {
			return map[string]any{"id": id, "type": "function", "function": map[string]any{"name": name, "arguments": "{}"}}
		}
		reason := "tool_calls"
		message := map[string]any{"role": "assistant", "content": nil}
		if toolResults == 0 {
			message["tool_calls"] = []any{call("hotspots", "inspect_hotspots"), call("quality", "check_collection_quality")}
		} else if toolResults == 2 {
			message["tool_calls"] = []any{call("verification", "propose_verification")}
		} else {
			reason = "stop"
			message["content"] = `{"summary":"测试响应：热点需要复测","hypotheses":[{"claim":"导入样本集中，但不能证明业务延迟","evidence_ids":["E2","E3"],"verification":"同负载复测并记录P99"}],"limitations":["明确导入的测试响应，不是真实模型输出"]}`
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"finish_reason": reason, "message": message}}})
	}))
	s.Config = AIConfig{Endpoint: server.URL, Model: "explicit-fixture-model", AllowHTTP: true}
	s.Client = server.Client()
	s.Client.Timeout = 2 * time.Second
	return server, calls
}

func TestPostgresDiagnosisLocalAdmissionAndIdempotency(t *testing.T) {
	db, s, u, task := diagnosisFixture(t)
	ctx := context.Background()
	r := requestDiagnosisFixture(t, s, u, task, "evidence", "evidence-first")
	replay := requestDiagnosisFixture(t, s, u, task, "evidence", "evidence-first")
	if replay.ID != r.ID {
		t.Fatal("not idempotent")
	}
	if _, err := s.RequestDiagnosis(ctx, u, task.ID, DiagnosisInput{Goal: "changed", Mode: "evidence"}, "fixture", "evidence-first"); err == nil {
		t.Fatal("different payload replayed")
	}
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&r, r.ID)
	if r.Status != "success" || !strings.Contains(string(r.Report), "\"engine\": \"evidence\"") || len(r.Trace) == 0 {
		t.Fatal("local diagnosis did not finish", r.Status, string(r.Report))
	}
	var events int64
	db.Table("task_events").Where("task_id=? AND type='DIAGNOSIS_TOOL_COMPLETED'", task.ID).Count(&events)
	if events != 5 {
		t.Fatal("trace/event not durable", events)
	}
	bad := u
	bad.Role = "viewer"
	if err := db.Model(&domain.User{}).Where("id=?", u.ID).Update("role", "viewer").Error; err != nil {
		t.Fatal(err)
	}
	if _, err := s.RequestDiagnosis(ctx, bad, task.ID, DiagnosisInput{Goal: "denied", Mode: "evidence"}, "fixture", "viewer-denied"); err == nil {
		t.Fatal("reader started diagnosis")
	}
	db.Model(&domain.User{}).Where("id=?", u.ID).Update("role", "operator")
	var wg sync.WaitGroup
	var accepted atomic.Int32
	for i := 0; i < 6; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			_, err := s.RequestDiagnosis(ctx, u, task.ID, DiagnosisInput{Goal: "检查", Mode: "evidence"}, "fixture", fmt.Sprintf("concurrent-%d", i))
			if err == nil {
				accepted.Add(1)
			}
		}(i)
	}
	wg.Wait()
	if accepted.Load() != 3 {
		t.Fatal("concurrent admission bypassed budget", accepted.Load())
	}
}

func TestPostgresDiagnosisModelToolLoopAndFences(t *testing.T) {
	for _, scenario := range []string{"success", "cancel", "revoke", "reanalyze", "lease", "recovery", "exhausted", "concurrent"} {
		t.Run(scenario, func(t *testing.T) {
			db, s, u, task := diagnosisFixture(t)
			ctx := context.Background()
			var r DiagnosisRun
			server, calls := fixtureDiagnosisProvider(t, s, func(n int) {
				if n != 1 {
					return
				}
				switch scenario {
				case "cancel":
					if err := s.CancelDiagnosis(ctx, u, task.ID, r.ID, "fixture"); err != nil {
						t.Error(err)
					}
				case "revoke":
					db.Model(&domain.User{}).Where("id=?", u.ID).Update("role", "viewer")
				case "reanalyze":
					db.Exec("UPDATE analysis_jobs SET attempt=attempt+1 WHERE task_id=?", task.ID)
				case "lease":
					db.Exec("UPDATE diagnosis_runs SET lease_owner='other-fixture-worker' WHERE id=?", r.ID)
				}
			})
			defer server.Close()
			r = requestDiagnosisFixture(t, s, u, task, "model", "model-loop-fixture")
			if scenario == "recovery" || scenario == "exhausted" {
				attempts := 1
				if scenario == "exhausted" {
					attempts = 3
				}
				db.Exec("UPDATE diagnosis_runs SET status='running',attempts=?,lease_owner='dead-fixture-worker',lease_expires_at=now()-interval '1 second' WHERE id=?", attempts, r.ID)
			}
			if scenario == "concurrent" {
				var wg sync.WaitGroup
				errs := make(chan error, 2)
				for i := 0; i < 2; i++ {
					wg.Add(1)
					go func() { defer wg.Done(); errs <- s.processDiagnosis(ctx) }()
				}
				wg.Wait()
				close(errs)
				for err := range errs {
					if err != nil {
						t.Fatal(err)
					}
				}
			} else if err := s.processDiagnosis(ctx); err != nil {
				t.Fatal(err)
			}
			db.First(&r, r.ID)
			expected := map[string]string{"cancel": "DIAGNOSIS_CANCELED", "revoke": "AUTHORIZATION_REVOKED", "reanalyze": "STALE_ATTEMPT", "exhausted": "DIAGNOSIS_RETRY_EXHAUSTED"}
			if code, ok := expected[scenario]; ok {
				if r.Status != "failed" || r.ErrorCode == nil || *r.ErrorCode != code || len(r.Report) > 0 {
					t.Fatal("failure fence missing", r.Status, r.ErrorCode, string(r.Report))
				}
			} else if scenario == "lease" {
				if r.Status != "running" || len(r.Report) > 0 {
					t.Fatal("stale worker committed")
				}
			} else {
				if r.Status != "success" || calls.Load() != 3 || !strings.Contains(string(r.Report), "explicit-fixture-model") {
					t.Fatal("tool loop failed", r.Status, r.ErrorCode, calls.Load())
				}
			}
			var complete int64
			db.Table("task_events").Where("type='DIAGNOSIS_COMPLETED' AND task_id=?", task.ID).Count(&complete)
			if scenario != "success" && scenario != "recovery" && scenario != "concurrent" && complete != 0 {
				t.Fatal("failed worker emitted success")
			}
		})
	}
}

func TestPostgresDiagnosisFinalEventAtomicity(t *testing.T) {
	db, s, u, task := diagnosisFixture(t)
	ctx := context.Background()
	r := requestDiagnosisFixture(t, s, u, task, "evidence", "atomic-event-fixture")
	ddl := `CREATE FUNCTION reject_diagnosis_completion() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.type='DIAGNOSIS_COMPLETED' THEN RAISE EXCEPTION 'fixture_commit_failure'; END IF; RETURN NEW; END $$; CREATE TRIGGER diagnosis_fail_final BEFORE INSERT ON task_events FOR EACH ROW EXECUTE FUNCTION reject_diagnosis_completion();`
	if err := db.Exec(ddl).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.processDiagnosis(ctx); err == nil {
		t.Fatal("completion event failure did not abort transaction")
	}
	db.First(&r, r.ID)
	if r.Status != "running" || len(r.Report) > 0 {
		t.Fatal("final status escaped failed event transaction")
	}
	if err := db.Exec("DROP TRIGGER diagnosis_fail_final ON task_events; UPDATE diagnosis_runs SET lease_expires_at=now()-interval '1 second' WHERE id=?", r.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	db.First(&r, r.ID)
	if r.Status != "success" {
		t.Fatal("failed completion was not recovered")
	}
}

func TestPostgresDiagnosisConfirmedCollection(t *testing.T) {
	db, s, u, task := diagnosisFixture(t)
	ctx := context.Background()
	u.Role = "platform_admin"
	db.Model(&domain.User{}).Where("id=?", u.ID).Update("role", u.Role)
	aid := "controlled-fixture-agent"
	agent := domain.Agent{ID: aid, Hostname: "controlled-fixture", Version: "fixture", Platform: "linux", Status: "online", Capabilities: json.RawMessage(`["PERF_CPU"]`), Labels: json.RawMessage(`{}`), Resources: json.RawMessage(`{"processes":[{"pid":7,"uid":10001,"start_time_ticks":42}],"target_policy":{"host_processes":true,"allowed_uids":[10001]}}`), LastSeenAt: time.Now()}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&task).Updates(map[string]any{"kind": "PERF_CPU", "target_agent_id": aid, "params": `{"pid":7,"duration_seconds":5,"frequency_hz":99,"scope":"process","target":{"type":"process","pid":7,"expected_uid":10001,"check_uid":true,"start_time_ticks":42}}`}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("UPDATE analysis_jobs SET result=jsonb_set(result,'{kind}','\"PERF_CPU\"') WHERE task_id=?", task.ID).Error; err != nil {
		t.Fatal(err)
	}
	r := requestDiagnosisFixture(t, s, u, task, "evidence", "collection-plan-fixture")
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	var before int64
	db.Table("tasks").Count(&before)
	if before != 1 {
		t.Fatal("local read-only tools executed a collection")
	}
	created, replay, err := s.CollectDiagnosis(ctx, u, task.ID, r.ID, "confirmed-collection-fixture", "fixture")
	if err != nil || replay || created.ID == task.ID {
		t.Fatal("confirmed collection failed", err)
	}
	again, replay, err := s.CollectDiagnosis(ctx, u, task.ID, r.ID, "confirmed-collection-fixture", "fixture")
	if err != nil || !replay || created.ID != again.ID {
		t.Fatal("collection replay duplicated side effect", err)
	}
	var p domain.Parameters
	json.Unmarshal(created.Parameters, &p)
	if p.PID != 7 || p.Target == nil || p.Target.StartTimeTicks != 42 || !p.Target.CheckUID || p.Target.ExpectedUID != 10001 || p.DurationSeconds != 10 {
		t.Fatal("follow-up target fence/config lost")
	}
	var count int64
	db.Table("task_events").Where("type='DIAGNOSIS_VERIFICATION_CREATED'").Count(&count)
	if count != 1 {
		t.Fatal("provenance event duplicated", count)
	}
	db.Exec("UPDATE analysis_jobs SET attempt=attempt+1 WHERE task_id=?", task.ID)
	if _, _, err := s.CollectDiagnosis(ctx, u, task.ID, r.ID, "stale-collection-fixture", "fixture"); err == nil {
		t.Fatal("stale analysis executed collection")
	}
}

// Opt-in actual provider acceptance. Input is explicitly imported fixture data.
// It exercises the same durable worker and validates evidence references; no
// private symbols, raw profiles, credentials or provider body enter test logs.
func TestPostgresDiagnosisLiveDeepSeek(t *testing.T) {
	file := os.Getenv("LIVE_AI_KEY_FILE")
	if file == "" {
		t.Skip("LIVE_AI_KEY_FILE required for actual provider acceptance")
	}
	key, err := os.ReadFile(file)
	if err != nil {
		t.Fatal("model key unavailable")
	}
	db, s, u, task := diagnosisFixture(t)
	s.Config = AIConfig{Endpoint: "https://api.deepseek.com/chat/completions", APIKey: strings.TrimSpace(string(key)), Model: "deepseek-flash"}
	s.Client = &http.Client{Timeout: 35 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	r := requestDiagnosisFixture(t, s, u, task, "model", "real-provider-fixture")
	if err := s.processDiagnosis(context.Background()); err != nil {
		t.Fatal("worker persistence failed")
	}
	db.First(&r, r.ID)
	if r.Status != "success" {
		code := "unknown"
		if r.ErrorCode != nil {
			code = *r.ErrorCode
		}
		t.Fatal("actual provider acceptance failed", code)
	}
	if out := os.Getenv("LIVE_AI_REPORT_FILE"); out != "" {
		b, _ := json.MarshalIndent(map[string]any{"source": "explicit_imported_test_fixture", "provider": "deepseek", "model": s.Config.Model, "status": r.Status, "trace": r.Trace, "report": r.Report}, "", "  ")
		if err := os.WriteFile(out, b, 0600); err != nil {
			t.Fatal("cannot save sanitized acceptance")
		}
	}
}
