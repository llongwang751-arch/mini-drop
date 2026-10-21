// 本文件独立验证诊断扩展的授权别名、来源窗口、脱敏输出和固定工具边界。
// HTTP 响应均为显式合成协议夹具；数据库用例使用独立 PostgreSQL schema，不调用模型或生产服务。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
)

func extensionRegistry(t *testing.T, services []TelemetryService) *ObservabilityRegistry {
	t.Helper()
	raw, err := json.Marshal(map[string]any{"schema_version": 1, "services": services})
	if err != nil {
		t.Fatal(err)
	}
	r, err := NewObservabilityRegistry(raw)
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func extensionAdapter(t *testing.T, response any) (*Suggestions, TelemetryConnector, *atomic.Int64) {
	t.Helper()
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Method != http.MethodGet || r.URL.Path != "/v1/signals" || r.URL.Query().Get("service") != "fixture-root" || r.URL.Query().Get("limit") != "30" {
			t.Error("adapter request escaped fixed route or bound service")
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(response); err != nil {
			t.Error(err)
		}
	}))
	t.Cleanup(server.Close)
	c := TelemetryConnector{Kind: "drop-adapter", BaseURL: server.URL, AllowHTTP: true}
	r := extensionRegistry(t, []TelemetryService{{ID: "fixture-root", Label: "合成协议夹具", AgentID: "fixture-agent", Connectors: map[string]TelemetryConnector{"metrics": c}}})
	return &Suggestions{Observability: r}, c, &calls
}

func TestDiagnosticExtensionsLogOutputOmitsFreeText(t *testing.T) {
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	window := SignalWindow{Start: now.Add(-time.Minute), End: now.Add(time.Minute)}
	raw := []byte(`{"level":"ERROR","event_code":"UPSTREAM_TIMEOUT","duration_ms":12.5,"status_code":504,"trace_id":"0123456789abcdef0123456789abcdef","message":"PRIVATE_CUSTOMER_LOG","password":"PRIVATE_TOKEN","attributes":{"host":"PRIVATE_HOST"}}`)
	row, ok := normalizeTelemetryLog(now, raw, window)
	if !ok || row.Level != "error" || row.EventCode != "UPSTREAM_TIMEOUT" || row.TraceRef == "" || row.DurationMS == nil || *row.DurationMS != 12.5 || row.StatusCode == nil || *row.StatusCode != 504 {
		t.Fatal("valid bounded fields lost", row)
	}
	encoded, _ := json.Marshal(row)
	for _, forbidden := range []string{"PRIVATE_", "0123456789abcdef", "message", "password", "attributes"} {
		if strings.Contains(string(encoded), forbidden) {
			t.Fatal("raw log field leaked", forbidden)
		}
	}
	for _, stamp := range []time.Time{window.Start.Add(-time.Nanosecond), window.End.Add(time.Nanosecond)} {
		if _, ok := normalizeTelemetryLog(stamp, raw, window); ok {
			t.Fatal("out-of-window log accepted")
		}
	}
	row, ok = normalizeTelemetryLog(now, []byte(`{"level":"INJECT","event_code":"FREE_INSTRUCTION","duration_ms":-1,"status_code":999}`), window)
	if !ok || row.Level != "unknown" || row.EventCode != "UNKNOWN" || row.DurationMS != nil || row.StatusCode != nil {
		t.Fatal("untrusted enum or invalid numeric field accepted", row)
	}
	if _, ok := normalizeTelemetryLog(now, []byte(strings.Repeat("x", 8193)), window); ok {
		t.Fatal("oversized log accepted")
	}
}

func TestDiagnosticExtensionsAdapterRejectsIdentityWindowAndUnits(t *testing.T) {
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	window := SignalWindow{now.Add(-time.Minute), now.Add(time.Minute)}
	for _, tc := range []struct {
		name   string
		mutate func(map[string]any)
	}{
		{"other-service", func(r map[string]any) { r["service_id"] = "another-service" }},
		{"other-signal", func(r map[string]any) { r["signal"] = "logs" }},
		{"other-schema", func(r map[string]any) { r["schema_version"] = 2 }},
		{"wrong-unit", func(r map[string]any) {
			r["metrics"] = []any{map[string]any{"name": "latency_p95_ms", "value": 12, "unit": "seconds", "observed_at": now}}
		}},
		{"outside-window", func(r map[string]any) {
			r["metrics"] = []any{map[string]any{"name": "latency_p95_ms", "value": 12, "unit": "milliseconds", "observed_at": now.Add(-2 * time.Minute)}}
		}},
		{"unknown-metric", func(r map[string]any) {
			r["metrics"] = []any{map[string]any{"name": "customer_secret", "value": 12, "unit": "milliseconds", "observed_at": now}}
		}},
		{"invalid-ratio", func(r map[string]any) {
			r["metrics"] = []any{map[string]any{"name": "error_rate", "value": 1.1, "unit": "ratio", "observed_at": now}}
		}},
		{"duplicate-metric", func(r map[string]any) {
			m := map[string]any{"name": "error_rate", "value": 0.1, "unit": "ratio", "observed_at": now}
			r["metrics"] = []any{m, m}
		}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			response := map[string]any{"schema_version": 1, "service_id": "fixture-root", "signal": "metrics", "metrics": []any{map[string]any{"name": "latency_p95_ms", "value": 12, "unit": "milliseconds", "observed_at": now}}}
			tc.mutate(response)
			s, c, calls := extensionAdapter(t, response)
			out := BusinessSignalObservation{Signal: "metrics", Window: window}
			if err := s.querySignalAdapter(context.Background(), c, "fixture-root", &out); err == nil {
				t.Fatal("invalid adapter response accepted")
			}
			if calls.Load() != 1 {
				t.Fatal("unexpected fetch count", calls.Load())
			}
		})
	}
}

func TestDiagnosticExtensionsAdapterHashesIdentifiersAndChecksTraceCounts(t *testing.T) {
	now := time.Date(2026, 10, 21, 12, 0, 0, 0, time.UTC)
	window := SignalWindow{now.Add(-time.Minute), now.Add(time.Minute)}
	t.Run("trace-counts", func(t *testing.T) {
		for _, counts := range []struct{ spans, errors int }{{-1, 0}, {1, 2}, {100001, 0}} {
			s, c, _ := extensionAdapter(t, map[string]any{"schema_version": 1, "service_id": "fixture-root", "signal": "traces", "traces": []any{map[string]any{"trace_id": "private-trace-0123456789", "duration_ms": 10, "span_count": counts.spans, "error_count": counts.errors, "started_at": now}}})
			out := BusinessSignalObservation{Signal: "traces", Window: window}
			if err := s.querySignalAdapter(context.Background(), c, "fixture-root", &out); err == nil {
				t.Fatal("inconsistent trace counts accepted", counts)
			}
		}
	})
	t.Run("safe-fields", func(t *testing.T) {
		for _, signal := range []string{"traces", "changes"} {
			response := map[string]any{"schema_version": 1, "service_id": "fixture-root", "signal": signal,
				"traces":  []any{map[string]any{"trace_id": "PRIVATE_TRACE_0123456789", "duration_ms": 10, "span_count": 3, "error_count": 1, "started_at": now, "attributes": "PRIVATE_TRACE_ATTR"}},
				"changes": []any{map[string]any{"timestamp": now, "change_type": "deployment", "revision": "PRIVATE_REVISION", "message": "PRIVATE_DEPLOY_DESCRIPTION"}}, "message": "PRIVATE_RESPONSE"}
			s, c, _ := extensionAdapter(t, response)
			out := BusinessSignalObservation{Signal: signal, Window: window}
			if err := s.querySignalAdapter(context.Background(), c, "fixture-root", &out); err != nil {
				t.Fatal(err)
			}
			encoded, _ := json.Marshal(out)
			if strings.Contains(string(encoded), "PRIVATE_") {
				t.Fatal("raw source identifier escaped", string(encoded))
			}
			if signal == "traces" && (len(out.Traces) != 1 || len(out.Changes) != 0 || out.Traces[0].TraceRef == "") {
				t.Fatal("trace projection mixed signal fields", out)
			}
			if signal == "changes" && (len(out.Changes) != 1 || len(out.Traces) != 0 || out.Changes[0].RevisionRef == "") {
				t.Fatal("change projection mixed signal fields", out)
			}
		}
	})
}

func TestDiagnosticExtensionsFixedRoutesRefuseRedirectAndOversize(t *testing.T) {
	var redirected atomic.Int64
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { redirected.Add(1); fmt.Fprint(w, `{}`) }))
	defer destination.Close()
	for _, mode := range []string{"redirect", "oversize"} {
		t.Run(mode, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "redirect" {
					http.Redirect(w, r, destination.URL, http.StatusFound)
					return
				}
				fmt.Fprint(w, strings.Repeat("x", (256<<10)+1))
			}))
			defer server.Close()
			c := TelemetryConnector{Kind: "drop-adapter", BaseURL: server.URL, AllowHTTP: true}
			r := extensionRegistry(t, []TelemetryService{{ID: "fixture-root", Label: "合成来源", AgentID: "fixture-agent", Connectors: map[string]TelemetryConnector{"metrics": c}}})
			if _, err := r.fetch(context.Background(), c, "/v1/signals", nil); err == nil {
				t.Fatal("unbounded or redirected response accepted")
			}
		})
	}
	if redirected.Load() != 0 {
		t.Fatal("configured source redirected to an unregistered destination")
	}
}

func TestDiagnosticExtensionsFollowupRejectsIndependentConfigurationBeforeDatabase(t *testing.T) {
	// 此处不设置 Repo：被禁止的种类必须在查询调查或创建任务之前直接拒绝。
	for _, kind := range []string{"SCRIPT", "BOLT_PROFILE", "COMPOSITE"} {
		t.Run(kind, func(t *testing.T) {
			s := &Suggestions{}
			args, _ := json.Marshal(map[string]any{"kind": kind, "duration_seconds": 5, "frequency_hz": 99, "rationale": "明确合成单元测试", "evidence_ids": []string{"E1"}})
			trace := []DiagnosisStep{{EvidenceID: "E1", Tool: "inspect_hotspots"}}
			if _, err := s.proposeFollowup(context.Background(), DiagnosisRun{}, args, trace); err == nil {
				t.Fatal("independently configured collector accepted")
			}
		})
	}
}

func TestPostgresDiagnosticExtensionsAliasAuthorizationIsStable(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	u.Role = "operator"
	if err := db.Model(&domain.User{}).Where("id=?", u.ID).Update("role", u.Role).Error; err != nil {
		t.Fatal(err)
	}
	groups := []string{"telemetry-root-group", "telemetry-alpha-group", "telemetry-beta-group"}
	for _, gid := range groups {
		if err := db.Exec("INSERT INTO groups(id,name) VALUES(?,?)", gid, gid).Error; err != nil {
			t.Fatal(err)
		}
		if err := db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES(?,?,'operator')", gid, u.ID).Error; err != nil {
			t.Fatal(err)
		}
	}
	if err := db.Exec("INSERT INTO group_agents(group_id,agent_id,permission) VALUES(?,?,'operate')", groups[0], *task.AgentID).Error; err != nil {
		t.Fatal(err)
	}
	var requests atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		t.Error("unauthorized alias should never contact any connector")
		fmt.Fprint(w, `{}`)
	}))
	defer server.Close()
	c := TelemetryConnector{Kind: "drop-adapter", BaseURL: server.URL, AllowHTTP: true}
	s.Observability = extensionRegistry(t, []TelemetryService{
		{ID: "fixture-root", Label: "根服务", AgentID: *task.AgentID, GroupID: &groups[0], Dependencies: []string{"fixture-beta", "fixture-alpha"}, Connectors: map[string]TelemetryConnector{"metrics": c}},
		{ID: "fixture-alpha", Label: "原S2服务", AgentID: *task.AgentID, GroupID: &groups[1], Connectors: map[string]TelemetryConnector{"metrics": c}},
		{ID: "fixture-beta", Label: "原S3服务", AgentID: *task.AgentID, GroupID: &groups[2], Connectors: map[string]TelemetryConnector{"metrics": c}},
	})
	var jobID int64
	if err := db.Table("analysis_jobs").Where("task_id=?", task.ID).Pluck("id", &jobID).Error; err != nil {
		t.Fatal(err)
	}
	run := DiagnosisRun{TaskID: task.ID, AnalysisJobID: jobID, RequestedBy: u.ID, TelemetryServiceID: "fixture-root"}
	check := func(want []string) {
		t.Helper()
		services, err := s.telemetryScope(ctx, run)
		if err != nil {
			t.Fatal(err)
		}
		ids := []string{}
		for _, service := range services {
			ids = append(ids, service.ID)
		}
		if !reflect.DeepEqual(ids, want) {
			t.Fatalf("alias binding changed: %v want %v", ids, want)
		}
	}
	check([]string{"fixture-root", "fixture-alpha", "fixture-beta"})
	if err := db.Exec("DELETE FROM group_members WHERE group_id=? AND user_id=?", groups[1], u.ID).Error; err != nil {
		t.Fatal(err)
	}
	check([]string{"fixture-root", "", "fixture-beta"})
	observation, err := s.queryBusinessSignals(ctx, run, json.RawMessage(`{"service_ref":"S2","signal":"metrics"}`))
	if err != nil {
		t.Fatal(err)
	}
	var denied BusinessSignalObservation
	if json.Unmarshal(observation, &denied) != nil || denied.Available || denied.Reason != "service_not_authorized" || requests.Load() != 0 {
		t.Fatal("revoked S2 rebound to another service", string(observation))
	}
	topology, err := s.inspectServiceTopology(ctx, run, json.RawMessage(`{}`))
	if err != nil {
		t.Fatal(err)
	}
	var graph struct {
		Nodes []struct {
			ServiceRef string `json:"service_ref"`
		}
	}
	if err := json.Unmarshal(topology, &graph); err != nil {
		t.Fatal(err)
	}
	refs := []string{}
	for _, node := range graph.Nodes {
		refs = append(refs, node.ServiceRef)
	}
	if !reflect.DeepEqual(refs, []string{"S1", "S3"}) {
		t.Fatal("topology compressed aliases or exposed unauthorized node", string(topology))
	}
	if err := db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES(?,?,'operator')", groups[1], u.ID).Error; err != nil {
		t.Fatal(err)
	}
	check([]string{"fixture-root", "fixture-alpha", "fixture-beta"})
	// 读取旧报告和写入迟到终态必须使用同一别名/授权语义，任务本身仍有权限不能代替业务组授权。
	trace := []DiagnosisStep{
		{EvidenceID: "E1", Tool: "read_profile_summary", Observation: mustSignalJSON(map[string]any{"observability_revision": s.Observability.Digest})},
		{EvidenceID: "E2", Tool: "query_business_signals", Observation: json.RawMessage(`{"available":true,"service_ref":"S2","signal":"metrics"}`)},
	}
	run.Trace = mustSignalJSON(trace)
	pin := func() error {
		tx := db.Begin()
		defer tx.Rollback()
		if tx.Error != nil {
			return tx.Error
		}
		return s.pinDiagnosticSignals(ctx, tx, run, trace)
	}
	if err := s.AuthorizeDiagnosisSignals(ctx, u, run); err != nil {
		t.Fatal("authorized historical signal hidden", err)
	}
	if err := pin(); err != nil {
		t.Fatal("authorized source could not be pinned", err)
	}
	if err := db.Exec("DELETE FROM group_members WHERE group_id=? AND user_id=?", groups[1], u.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.AuthorizeDiagnosisSignals(ctx, u, run); err == nil {
		t.Fatal("old report retained access after source-group revocation")
	}
	if err := pin(); err == nil {
		t.Fatal("late model result accepted after source-group revocation")
	}
	if err := db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES(?,?,'operator')", groups[1], u.ID).Error; err != nil {
		t.Fatal(err)
	}
	s.Observability.Digest = strings.Repeat("b", 64)
	if err := s.AuthorizeDiagnosisSignals(ctx, u, run); err == nil {
		t.Fatal("old aliases accepted after registry snapshot changed")
	}
	if err := pin(); err == nil {
		t.Fatal("changed registry accepted by terminal signal fence")
	}
}

func TestPostgresDiagnosticExtensionsSignalWindowAndUnavailableClearPartial(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Second)
	if err := db.Model(&task).Update("created_at", now.Add(-time.Hour)).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Table("analysis_jobs").Where("task_id=?", task.ID).Update("updated_at", now).Error; err != nil {
		t.Fatal(err)
	}
	var jobID int64
	if err := db.Table("analysis_jobs").Where("task_id=?", task.ID).Pluck("id", &jobID).Error; err != nil {
		t.Fatal(err)
	}
	attempt, err := s.Repo.LatestAttempt(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	started, finished := now.Add(-10*time.Second), now
	if err := db.Model(&domain.Attempt{}).Where("id=?", attempt.ID).Updates(map[string]any{"started_at": started, "finished_at": finished}).Error; err != nil {
		t.Fatal(err)
	}
	run := DiagnosisRun{TaskID: task.ID, AttemptID: attempt.ID, AnalysisJobID: jobID, RequestedBy: u.ID, TelemetryServiceID: "fixture-root"}
	window, err := s.signalWindow(ctx, run)
	if err != nil || !window.Start.Equal(started.Add(-time.Minute)) || !window.End.Equal(finished.Add(time.Minute)) {
		t.Fatal("signal window not bound to actual collection attempt", window, err)
	}
	var calls atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{"schema_version": 1, "service_id": "fixture-root", "signal": "metrics", "metrics": []any{
			map[string]any{"name": "latency_p95_ms", "value": 12, "unit": "milliseconds", "observed_at": now},
			map[string]any{"name": "error_rate", "value": 3, "unit": "ratio", "observed_at": now},
		}})
	}))
	defer server.Close()
	c := TelemetryConnector{Kind: "drop-adapter", BaseURL: server.URL, AllowHTTP: true}
	s.Observability = extensionRegistry(t, []TelemetryService{{ID: "fixture-root", Label: "合成协议来源", AgentID: *task.AgentID, Connectors: map[string]TelemetryConnector{"metrics": c}}})
	raw, err := s.queryBusinessSignals(ctx, run, json.RawMessage(`{"service_ref":"S1","signal":"metrics"}`))
	if err != nil {
		t.Fatal(err)
	}
	var out BusinessSignalObservation
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 1 || out.Available || len(out.Metrics) != 0 || len(out.Logs) != 0 || len(out.Traces) != 0 || len(out.Changes) != 0 || out.Reason == "" {
		t.Fatal("failed source retained misleading partial values", string(raw))
	}
	if err := db.Table("analysis_jobs").Where("task_id=?", task.ID).Update("updated_at", now.Add(-2*time.Hour)).Error; err != nil {
		t.Fatal(err)
	}
	// 排队与分析更新时间不能改变采集执行窗口，导入数据也不能借创建时间伪装出执行时钟。
	if again, err := s.signalWindow(ctx, run); err != nil || !again.Start.Equal(window.Start) || !again.End.Equal(window.End) {
		t.Fatal("analysis timestamp changed collection window", again, err)
	}
	for _, tc := range []struct {
		name       string
		start, end any
	}{
		{"missing-start", nil, finished},
		{"missing-finish", started, nil},
		{"reverse-clock", finished, started},
		{"over-wide", now.Add(-14 * time.Minute), now},
		{"future-clock", now.Add(3 * time.Minute), now.Add(4 * time.Minute)},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if err := db.Model(&domain.Attempt{}).Where("id=?", attempt.ID).Updates(map[string]any{"started_at": tc.start, "finished_at": tc.end}).Error; err != nil {
				t.Fatal(err)
			}
			if _, err := s.signalWindow(ctx, run); err == nil {
				t.Fatal("unverifiable collection clock accepted")
			}
			raw, err := s.queryBusinessSignals(ctx, run, json.RawMessage(`{"service_ref":"S1","signal":"metrics"}`))
			if err != nil {
				t.Fatal(err)
			}
			var missing BusinessSignalObservation
			if json.Unmarshal(raw, &missing) != nil || missing.Available || missing.Reason != "collection_window_unknown" || !missing.Window.Start.IsZero() || !missing.Window.End.IsZero() || len(missing.Metrics) > 0 || calls.Load() != 1 {
				t.Fatal("unknown clock made a source call or invented a window", string(raw))
			}
		})
	}
	for _, args := range []string{`{"service_ref":"S1","signal":"metrics","query":"PRIVATE_QUERY"}`, `{"service_ref":"S1","signal":"metrics","start":"2000-01-01"}`, `{"service_ref":"S0","signal":"logs"}`, `{"service_ref":"S9","signal":"logs"}`} {
		if _, err := s.queryBusinessSignals(ctx, run, json.RawMessage(args)); err == nil {
			t.Fatal("model supplied query/window/alias accepted", args)
		}
	}
}

func TestPostgresDiagnosticExtensionsFollowupActiveNotStarvedByCompletedRoots(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	run := requestDiagnosisFixture(t, s, u, task, "evidence", "extension-starvation-source")
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	makeRoot := func(index int) Investigation {
		t.Helper()
		root := Investigation{RequestedBy: u.ID, AgentID: *task.AgentID, Goal: "明确合成补证调度夹具", Parameters: task.Parameters, Consent: true, RequestKey: fmt.Sprintf("extension-starvation-%d", index), InputDigest: strings.Repeat("a", 64), Status: "success", Trace: json.RawMessage(`[]`), BaselineTaskID: &task.ID, DiagnosisID: &run.ID}
		if err := db.Create(&root).Error; err != nil {
			t.Fatal(err)
		}
		return root
	}
	// 老调查没有报告补证计划，也没有活动分支；不能长期占满一轮的16个调度槽。
	for n := 0; n < 17; n++ {
		makeRoot(n)
	}
	active := makeRoot(17)
	row := InvestigationFollowup{InvestigationID: active.ID, ParentTaskID: task.ID, SourceDiagnosisID: run.ID, Plan: json.RawMessage(`{}`), PlanDigest: strings.Repeat("b", 64), Status: "collecting", TaskID: &task.ID}
	if err := db.Create(&row).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.advanceInvestigationFollowups(ctx); err != nil {
		t.Fatal(err)
	}
	if err := db.First(&row, row.ID).Error; err != nil {
		t.Fatal(err)
	}
	// 此夹具没有模型配置，真实状态机应在尝试后续诊断时明确失败；保持 collecting 表示根本没被调度。
	if row.Status != "failed" || row.ErrorCode == nil || *row.ErrorCode != "AI_UNAVAILABLE" {
		t.Fatal("active followup starved behind completed roots", row.Status, row.ErrorCode)
	}
}

func TestPostgresDiagnosticExtensionsPlaybookConcurrentCreationIsIdempotent(t *testing.T) {
	db, s, u, _ := investigationFixture(t)
	ctx := context.Background()
	in := DiagnosticPlaybookInput{LogicalKey: "extension-concurrent-method", Version: 1, ExpiresDays: 30, Skill: DiagnosticSkill{
		Title: "合成方法并发夹具", TaskKinds: []string{"PERF_CPU"}, RequiredTools: []string{"inspect_hotspots"},
		Steps: []string{"核对当前热点"}, CounterEvidence: []string{"观测不足时补证"}, StopConditions: []string{"授权失效则停止"}, Verification: []string{"保持输入条件复测"},
	}}
	var wg sync.WaitGroup
	start := make(chan struct{})
	results := make(chan struct {
		row DiagnosticPlaybook
		err error
	}, 8)
	for n := 0; n < 8; n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			row, err := s.CreateDiagnosticPlaybook(ctx, u, in, "extension-concurrent-fixture")
			results <- struct {
				row DiagnosticPlaybook
				err error
			}{row, err}
		}()
	}
	close(start)
	wg.Wait()
	close(results)
	var id int64
	for result := range results {
		if result.err != nil {
			t.Fatal("same immutable version failed concurrent replay", result.err)
		}
		if id == 0 {
			id = result.row.ID
		}
		if result.row.ID != id {
			t.Fatal("same immutable version created multiple identities")
		}
	}
	var count int64
	if err := db.Model(&DiagnosticPlaybook{}).Where("owner_id=? AND logical_key=?", u.ID, in.LogicalKey).Count(&count).Error; err != nil || count != 1 {
		t.Fatal("immutable version duplicated", count, err)
	}
	in.Skill.Title = "不同内容不得覆盖已创建版本"
	_, err := s.CreateDiagnosticPlaybook(ctx, u, in, "extension-conflicting-fixture")
	if e, ok := err.(*domain.Error); !ok || e.Code != "IDEMPOTENCY_CONFLICT" {
		t.Fatal("different content did not return deterministic conflict", err)
	}
}

func TestPostgresDiagnosticExtensionsCommitWaitsVerificationBeforeTaskLocks(t *testing.T) {
	db, s, u, baseline := investigationFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	source := requestDiagnosisFixture(t, s, u, baseline, "evidence", "extension-lock-order-source")
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	candidate, _, err := s.CollectDiagnosis(ctx, u, baseline.ID, source.ID, "extension-lock-order-candidate", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	attempt, err := s.Repo.LatestAttempt(ctx, candidate.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&domain.Task{}).Where("id=?", candidate.ID).Updates(map[string]any{"collection_status": "COLLECTED", "analysis_status": "SUCCESS"}).Error; err != nil {
		t.Fatal(err)
	}
	// 导入同一受控profile只是建立完整来源关系；这里测试行锁顺序，不把它当成新的真实采样。
	if err := db.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,attempt,result) SELECT ?,?,'folded_v1','success',1,result FROM analysis_jobs WHERE task_id=?", candidate.ID, attempt.ID, baseline.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.processVerification(ctx); err != nil {
		t.Fatal(err)
	}
	var verification VerificationRun
	if err := db.Where("candidate_task_id=?", candidate.ID).First(&verification).Error; err != nil || verification.CandidateDiagnosisID == nil {
		t.Fatal("candidate diagnosis binding not created", err)
	}
	var run DiagnosisRun
	if err := db.First(&run, *verification.CandidateDiagnosisID).Error; err != nil {
		t.Fatal(err)
	}
	blocker := db.WithContext(ctx).Begin()
	defer blocker.Rollback()
	if err := blocker.Exec("SELECT id FROM verification_runs WHERE id=? FOR UPDATE", verification.ID).Error; err != nil {
		t.Fatal(err)
	}
	pidReady := make(chan int, 1)
	done := make(chan error, 1)
	go func() {
		tx := db.WithContext(ctx).Begin()
		defer tx.Rollback()
		var pid int
		if err := tx.Raw("SELECT pg_backend_pid()").Scan(&pid).Error; err != nil {
			done <- err
			return
		}
		pidReady <- pid
		if err := s.pinDiagnosisScope(ctx, tx, run, nil); err != nil {
			done <- err
			return
		}
		done <- tx.Commit().Error
	}()
	var pid int
	select {
	case pid = <-pidReady:
	case err := <-done:
		t.Fatal(err)
	case <-ctx.Done():
		t.Fatal(ctx.Err())
	}
	waiting := false
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if err := db.Raw("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=? AND wait_event_type='Lock')", pid).Scan(&waiting).Error; err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case err := <-done:
			t.Fatal("commit scope skipped verification lock", err)
		default:
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !waiting {
		t.Fatal("commit did not wait on the held verification row")
	}
	// 观察到真正的数据库等待后再探测任务锁，避免以定时sleep推测另一协程是否已运行。
	probe := db.WithContext(ctx).Begin()
	if err := probe.Exec("SELECT id FROM tasks WHERE id IN (?,?) ORDER BY id FOR UPDATE NOWAIT", baseline.ID, candidate.ID).Error; err != nil {
		probe.Rollback()
		t.Fatal("commit locked a task before acquiring verification; inverse worker order can deadlock", err)
	}
	if err := probe.Rollback().Error; err != nil {
		t.Fatal(err)
	}
	if err := blocker.Commit().Error; err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal("commit did not resume after verification unlock", ctx.Err())
	}
}
