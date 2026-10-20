// 本文件验证任务全部 HTTP 读面和写面的资源授权，含降权后关联源码的访问隔离。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package httpapi

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"drop/api/internal/telemetry"
	"drop/api/internal/testutil"
)

func TestPostgresHTTPAuthorizationCoversEveryTaskSurface(t *testing.T) {
	db := testutil.Postgres(t)
	r := repository.New(db)
	ctx := context.Background()
	owner := domain.User{ID: "owner", Username: "owner", DisplayName: "Owner", Role: "operator"}
	other := domain.User{ID: "other", Username: "other", DisplayName: "Other", Role: "operator"}
	viewer := domain.User{ID: "viewer", Username: "viewer", DisplayName: "Viewer", Role: "viewer"}
	for _, u := range []domain.User{owner, other, viewer} {
		if err := db.Create(&u).Error; err != nil {
			t.Fatal(err)
		}
	}
	token := strings.Repeat("a", 64)
	csrf := strings.Repeat("b", 64)
	r.NewSession(ctx, repository.Session{TokenHash: repository.Digest(token), UserID: other.ID, CSRFToken: csrf, ExpiresAt: time.Now().Add(time.Hour), CreatedAt: time.Now()})
	task, attempt, _, err := r.CreateTask(ctx, domain.Task{Name: "Imported authorization fixture", Kind: "IMPORT_COLLAPSED", OwnerID: owner.ID, Parameters: []byte(`{"source":"import"}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "fixture", IdempotencyKey: "http-authorization-task"}, false, domain.Audit{Action: "test", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	a := domain.Artifact{TaskID: task.ID, AttemptID: attempt.ID, Kind: "RAW", Name: "fixture.folded", ObjectKey: "fixture/profile.folded", ContentType: "text/plain", Size: 3, SHA256: strings.Repeat("0", 64), Status: "ready"}
	if err := db.Create(&a).Error; err != nil {
		t.Fatal(err)
	}
	aid := "private-agent"
	db.Create(&domain.Agent{ID: aid, Hostname: "hidden-host", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)})
	sc := domain.Schedule{Name: "private-schedule", OwnerID: owner.ID, IntervalMinutes: 1, Timezone: "UTC", Template: []byte(`{}`), NextRunAt: time.Now().Add(time.Minute), Enabled: true}
	if err := db.Create(&sc).Error; err != nil {
		t.Fatal(err)
	}
	server := &Server{Repo: r, Tasks: &service.Tasks{Repo: r}, Telemetry: telemetry.New(db, nil)}
	server.Suggestions, _ = service.NewSuggestions(r, server.Tasks, service.AIConfig{})
	router := server.Router()
	request := func(method, path, body string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.AddCookie(&http.Cookie{Name: "drop_session", Value: token})
		req.Header.Set("X-CSRF-Token", csrf)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Idempotency-Key", "http-authorization-key")
		w := httptest.NewRecorder()
		router.ServeHTTP(w, req)
		return w
	}
	for _, suffix := range []string{"", "/result", "/partial-result", "/events", "/events/stream", "/artifacts", "/suggestions", "/diagnoses", "/verifications", "/shares", fmt.Sprintf("/artifacts/%d/download", a.ID)} {
		path := fmt.Sprintf("/api/v1/tasks/%d%s", task.ID, suffix)
		if w := request("GET", path, ""); w.Code != 404 {
			t.Fatalf("GET %s returned %d: %s", path, w.Code, w.Body.String())
		}
	}
	privateInvestigation := service.Investigation{RequestedBy: owner.ID, AgentID: aid, Goal: "private investigation fixture", Parameters: []byte(`{"pid":7}`), Consent: true, RequestKey: "private-investigation-fixture", InputDigest: "fixture", Status: "success", Trace: []byte(`[]`)}
	if err := db.Create(&privateInvestigation).Error; err != nil {
		t.Fatal(err)
	}
	for _, surface := range []struct{ method, suffix, body string }{{"GET", "", ""}, {"POST", "/confirm", `{"confirm":true}`}, {"POST", "/cancel", `{}`}} {
		if w := request(surface.method, fmt.Sprintf("/api/v1/investigations/%d%s", privateInvestigation.ID, surface.suffix), surface.body); w.Code != 404 {
			t.Fatal("private investigation exposed", surface.method, surface.suffix, w.Code)
		}
	}
	for _, surface := range []struct{ method, path, body string }{{"GET", "/code-projects", ""}, {"GET", "/code-repairs", ""}, {"GET", "/code-repairs/1", ""}, {"POST", "/code-repairs", `{"project_id":"crm-python","goal":"fixture","consent":true}`}, {"POST", "/code-repairs/1/cancel", `{}`}} {
		if w := request(surface.method, "/api/v1"+surface.path, surface.body); w.Code != 403 {
			t.Fatal("non-admin entered source boundary", surface.path, w.Code)
		}
	}
	// 源码管理员降权后，即使 Agent 授权仍有效，也不能通过自己关联的调查继续读取源码。
	// 该测试验证列表、详情和关联修复路径都重新检查当前权限。
	db.Create(&domain.Group{ID: "source-fixture-group", Name: "Source role regression"})
	db.Create(&domain.Agent{ID: "source-fixture-agent", Hostname: "source-fixture", Version: "fixture", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)})
	db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES(?,?,'operator')", "source-fixture-group", other.ID)
	db.Exec("INSERT INTO group_agents(group_id,agent_id,permission) VALUES(?,?,'operate')", "source-fixture-group", "source-fixture-agent")
	project := "crm-python"
	linked := service.Investigation{RequestedBy: other.ID, AgentID: "source-fixture-agent", Goal: "source role fixture", Parameters: []byte(`{}`), Consent: true, RequestKey: "former-admin-source-fixture", InputDigest: "fixture", Status: "success", Trace: []byte(`[]`), CodeProjectID: &project, Report: []byte(`{"code_validation":{"source":"private-source-fixture-marker"}}`)}
	if err := db.Create(&linked).Error; err != nil {
		t.Fatal(err)
	}
	if w := request("GET", fmt.Sprintf("/api/v1/investigations/%d", linked.ID), ""); w.Code != 404 {
		t.Fatal("downgraded owner accessed source report", w.Code)
	}
	if w := request("GET", "/api/v1/investigations", ""); w.Code != 200 || strings.Contains(w.Body.String(), "private-source-fixture-marker") {
		t.Fatal("source report leaked through investigation list", w.Code)
	}
	for _, suffix := range []string{"cancel", "retry"} {
		path := fmt.Sprintf("/api/v1/tasks/%d/%s", task.ID, suffix)
		if w := request("POST", path, `{}`); w.Code != 404 {
			t.Fatalf("POST %s returned %d", path, w.Code)
		}
	}
	for _, suffix := range []string{"", "/metrics", "/events", "/diagnostics"} {
		if w := request("GET", "/api/v1/agents/"+aid+suffix, ""); w.Code != 404 {
			t.Fatal(suffix, w.Code)
		}
	}
	if w := request("GET", fmt.Sprintf("/api/v1/schedules/%d/records", sc.ID), ""); w.Code != 404 {
		t.Fatal(w.Code)
	}
	if w := request("PATCH", fmt.Sprintf("/api/v1/schedules/%d", sc.ID), `{"enabled":false}`); w.Code != 404 {
		t.Fatal(w.Code)
	}
	if w := request("GET", "/api/v1/overview", ""); w.Code != 200 || strings.Contains(w.Body.String(), "hidden-host") || !strings.Contains(w.Body.String(), `"tasks_total":0`) {
		t.Fatal(w.Code, w.Body.String())
	}
	uid := other.ID
	if _, err := r.CreateShare(ctx, owner, task.ID, &uid, nil, "read"); err != nil {
		t.Fatal(err)
	}
	if w := request("GET", fmt.Sprintf("/api/v1/tasks/%d", task.ID), ""); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	if w := request("POST", fmt.Sprintf("/api/v1/tasks/%d/cancel", task.ID), `{}`); w.Code != 404 {
		t.Fatal("read share became operate", w.Code)
	}
	for _, surface := range []struct{ suffix, body string }{
		{"diagnoses", `{"goal":"检查","mode":"evidence"}`},
		{"diagnoses/1/cancel", `{}`},
		{"diagnoses/1/collect", `{"confirm":true}`},
	} {
		if w := request("POST", fmt.Sprintf("/api/v1/tasks/%d/%s", task.ID, surface.suffix), surface.body); w.Code != 404 {
			t.Fatal("read share entered diagnosis write surface", surface.suffix, w.Code)
		}
	}
	db.Exec("UPDATE task_shares SET permission='operate'")
	if w := request("POST", fmt.Sprintf("/api/v1/tasks/%d/cancel", task.ID), `{}`); w.Code != 200 {
		t.Fatal(w.Code, w.Body.String())
	}
	db.Model(&repository.Session{}).Where("token_hash=?", repository.Digest(token)).Update("user_id", viewer.ID)
	if w := request("POST", "/api/v1/tasks", `{"kind":"IMPORT_COLLAPSED","collapsed_text":"fixture 1"}`); w.Code != 403 {
		t.Fatal("viewer created task", w.Code)
	}
	if w := request("POST", "/api/v1/client-errors", `{"code":"FLAME_LAYOUT_FAILED","message":"raw profile must never be accepted"}`); w.Code != 400 {
		t.Fatal("unbounded telemetry accepted", w.Code)
	}
	if w := request("POST", "/api/v1/client-errors", `{"code":"FLAME_LAYOUT_FAILED"}`); w.Code != 202 {
		t.Fatal(w.Code, w.Body.String())
	}
	metrics := request("GET", "/metrics", "")
	if metrics.Code != 200 {
		t.Fatal(metrics.Code, metrics.Body.String())
	}
	for _, text := range []string{"drop_agents_online", "drop_task_queue_depth", "drop_tasks_overdue", "drop_db_pool_wait_seconds_total", `drop_web_errors_total{code="FLAME_LAYOUT_FAILED"} 1`, `drop_metrics_collection_error 0`} {
		if !strings.Contains(metrics.Body.String(), text) {
			t.Fatal("missing SQL/counter metric", text)
		}
	}
}
