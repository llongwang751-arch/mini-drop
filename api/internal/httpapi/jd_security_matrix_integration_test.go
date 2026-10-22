// JD安全补强使用真实HTTP路由和真实隔离PostgreSQL，不复制一个“假API”验证自己。
// 夹具明确是合成任务；请求经会话、CSRF、资源授权、确认处理器，再核对业务表全行快照。
// rate_limits是拒绝请求正常更新的防滥用计数，单独排除；业务、事件、outbox及审计不能变化。
package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"drop/api/internal/testutil"
)

func TestPostgresJDSecurityMatrixDenialsHaveNoBusinessSideEffects(t *testing.T) {
	db := testutil.Postgres(t)
	repo := repository.New(db)
	ctx := context.Background()
	owner := domain.User{ID: "imported-jd-owner", Username: "imported-jd-owner", Role: "operator"}
	other := domain.User{ID: "imported-jd-other", Username: "imported-jd-other", Role: "operator"}
	reader := domain.User{ID: "imported-jd-reader", Username: "imported-jd-reader", Role: "operator"}
	viewer := domain.User{ID: "imported-jd-viewer", Username: "imported-jd-viewer", Role: "viewer"}
	for _, u := range []domain.User{owner, other, reader, viewer} {
		if err := db.Create(&u).Error; err != nil {
			t.Fatal(err)
		}
	}
	csrf := strings.Repeat("c", 64)
	tokens := map[string]string{}
	for i, u := range []domain.User{owner, other, reader, viewer} {
		token := strings.Repeat(fmt.Sprint(i+1), 64)
		tokens[u.ID] = token
		if err := repo.NewSession(ctx, repository.Session{TokenHash: repository.Digest(token), UserID: u.ID, CSRFToken: csrf, ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
			t.Fatal(err)
		}
	}
	expired := strings.Repeat("e", 64)
	if err := repo.NewSession(ctx, repository.Session{TokenHash: repository.Digest(expired), UserID: owner.ID, CSRFToken: csrf, ExpiresAt: time.Now().Add(-time.Hour)}); err != nil {
		t.Fatal(err)
	}
	task, attempt, _, err := repo.CreateTask(ctx, domain.Task{Name: "Imported JD security matrix", Kind: "IMPORT_COLLAPSED", OwnerID: owner.ID, Parameters: []byte(`{"source":"import"}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "imported-jd", IdempotencyKey: "imported-jd-security"}, false, domain.Audit{Action: "fixture.create", ResourceType: "task", RequestID: "imported-jd", Detail: []byte(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := repo.CreateShare(ctx, owner, task.ID, &reader.ID, nil, "read"); err != nil {
		t.Fatal(err)
	}
	artifact := domain.Artifact{TaskID: task.ID, AttemptID: attempt.ID, Kind: "RAW", Name: "imported-jd.folded", ObjectKey: "imported-jd/profile.folded", ContentType: "text/plain", Size: 3, SHA256: strings.Repeat("0", 64), Status: "ready"}
	if err := db.Create(&artifact).Error; err != nil {
		t.Fatal(err)
	}
	expiredAt := time.Now().Add(-time.Hour)
	expiredArtifact := artifact
	expiredArtifact.ID = 0
	expiredArtifact.Name = "imported-jd-expired.folded"
	expiredArtifact.ObjectKey = "imported-jd/expired.folded"
	expiredArtifact.ExpiresAt = &expiredAt
	if err := db.Create(&expiredArtifact).Error; err != nil {
		t.Fatal(err)
	}
	agent := domain.Agent{ID: "imported-jd-agent", Hostname: "imported-jd-agent", Version: "fixture", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	root := service.Investigation{RequestedBy: owner.ID, AgentID: agent.ID, Goal: "Imported JD confirm boundary", Parameters: []byte(`{"pid":7}`), Consent: true, RequestKey: "imported-jd-investigation", InputDigest: "fixture", Status: "awaiting_confirmation", Trace: []byte(`[]`), Plan: []byte(`{"kind":"PROC_CPU","duration_seconds":1,"frequency_hz":2}`)}
	if err := db.Create(&root).Error; err != nil {
		t.Fatal(err)
	}
	source := service.DiagnosisRun{TaskID: task.ID, AttemptID: attempt.ID, RequestedBy: owner.ID, Goal: "Imported JD diagnosis", Mode: "evidence", KnowledgeMode: "off", ReportProtocol: 2, Status: "success", InputDigest: "fixture", RequestKey: "imported-jd-diagnosis", Trace: []byte(`[]`), Report: []byte(`{}`)}
	// 数据库外键要求真实分析job，而不是把一个不存在的ID塞进夹具。
	var job struct{ ID int64 }
	if err := db.Raw("INSERT INTO analysis_jobs(task_id,attempt_id,status) VALUES(?,?,'pending') RETURNING id", task.ID, attempt.ID).Scan(&job).Error; err != nil {
		t.Fatal(err)
	}
	source.AnalysisJobID = job.ID
	source.AnalysisRun = 1
	if err := db.Create(&source).Error; err != nil {
		t.Fatal(err)
	}
	followup := service.InvestigationFollowup{InvestigationID: root.ID, ParentTaskID: task.ID, SourceDiagnosisID: source.ID, Plan: []byte(`{"plan":{"kind":"PROC_CPU","duration_seconds":1,"frequency_hz":2},"evidence_ids":["E1"],"requires_confirmation":true}`), PlanDigest: "fixture", Status: "proposed"}
	if err := db.Create(&followup).Error; err != nil {
		t.Fatal(err)
	}
	server := &Server{Repo: repo, Tasks: &service.Tasks{Repo: repo}}
	server.Suggestions, err = service.NewSuggestions(repo, server.Tasks, service.AIConfig{})
	if err != nil {
		t.Fatal(err)
	}
	router := server.Router()
	request := func(method, path, body, token, suppliedCSRF string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		if token != "" {
			req.AddCookie(&http.Cookie{Name: "drop_session", Value: token})
		}
		req.Header.Set("X-CSRF-Token", suppliedCSRF)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Idempotency-Key", "imported-jd-matrix-request")
		w := httptest.NewRecorder()
		router.ServeHTTP(w, req)
		return w
	}
	// 固定表名白名单；仅比行数发现不了“把proposed偷偷改成queued”等状态泄露。
	// 会话/防滥用计数单独管理，所有领域数据和审计均按完整JSONB行比较。
	snapshot := func() map[string]string {
		t.Helper()
		out := map[string]string{}
		for _, table := range []string{"tasks", "task_attempts", "task_events", "outbox", "artifacts", "analysis_jobs", "analysis_job_runs", "diagnosis_runs", "verification_runs", "investigations", "investigation_followups", "code_repairs", "diagnostic_cases", "diagnostic_playbooks", "task_shares", "agent_checkpoints", "agent_tool_receipts", "audit_logs"} {
			var rows string
			query := "SELECT COALESCE(jsonb_agg(x ORDER BY x::text),'[]'::jsonb)::text FROM (SELECT to_jsonb(t) AS x FROM " + table + " AS t) AS rows"
			if err := db.Raw(query).Scan(&rows).Error; err != nil {
				t.Fatal(err)
			}
			out[table] = rows
		}
		return out
	}
	taskPath := fmt.Sprintf("/api/v1/tasks/%d", task.ID)
	collectPath := fmt.Sprintf("%s/diagnoses/%d/collect", taskPath, source.ID)
	downloadPath := fmt.Sprintf("%s/artifacts/%d/download", taskPath, artifact.ID)
	rootPath := fmt.Sprintf("/api/v1/investigations/%d/confirm", root.ID)
	followupPath := fmt.Sprintf("/api/v1/investigations/%d/followups/%d/decision", root.ID, followup.ID)
	type scenario struct {
		name, method, path, body, token, csrf, code string
		status                                      int
	}
	cases := []scenario{}
	for _, identity := range []struct{ name, token string }{{"missing-session", ""}, {"malformed-session", "short"}, {"unknown-session", strings.Repeat("f", 64)}, {"expired-session", expired}} {
		for _, endpoint := range []struct{ method, path, body string }{{"GET", taskPath, ""}, {"GET", downloadPath, ""}, {"POST", collectPath, `{"confirm":true}`}, {"POST", rootPath, `{"confirm":true}`}, {"POST", followupPath, `{"confirm":true,"reviewed":true}`}} {
			cases = append(cases, scenario{identity.name + endpoint.path, endpoint.method, endpoint.path, endpoint.body, identity.token, csrf, "UNAUTHENTICATED", 401})
		}
	}
	for _, supplied := range []struct{ name, value string }{{"missing-csrf", ""}, {"wrong-csrf", strings.Repeat("z", 64)}} {
		for _, endpoint := range []struct{ path, body string }{{collectPath, `{"confirm":true}`}, {rootPath, `{"confirm":true}`}, {followupPath, `{"confirm":true,"reviewed":true}`}} {
			cases = append(cases, scenario{supplied.name + endpoint.path, "POST", endpoint.path, endpoint.body, tokens[owner.ID], supplied.value, "CSRF_INVALID", 403})
		}
	}
	cases = append(cases,
		scenario{"other-task", "GET", taskPath, "", tokens[other.ID], csrf, "NOT_FOUND", 404},
		scenario{"other-download", "GET", downloadPath, "", tokens[other.ID], csrf, "NOT_FOUND", 404},
		scenario{"other-collect", "POST", collectPath, `{"confirm":true}`, tokens[other.ID], csrf, "NOT_FOUND", 404},
		scenario{"other-root-confirm", "POST", rootPath, `{"confirm":true}`, tokens[other.ID], csrf, "NOT_FOUND", 404},
		scenario{"other-followup", "POST", followupPath, `{"confirm":true,"reviewed":true}`, tokens[other.ID], csrf, "NOT_FOUND", 404},
		scenario{"read-share-collect", "POST", collectPath, `{"confirm":true}`, tokens[reader.ID], csrf, "NOT_FOUND", 404},
		scenario{"read-share-cancel", "POST", taskPath + "/cancel", `{}`, tokens[reader.ID], csrf, "NOT_FOUND", 404},
		scenario{"owner-unconfirmed-collect", "POST", collectPath, `{"confirm":false}`, tokens[owner.ID], csrf, "CONSENT_REQUIRED", 400},
		scenario{"owner-unconfirmed-root", "POST", rootPath, `{"confirm":false}`, tokens[owner.ID], csrf, "CONSENT_REQUIRED", 400},
		scenario{"owner-unreviewed-followup", "POST", followupPath, `{"confirm":true,"reviewed":false}`, tokens[owner.ID], csrf, "CONSENT_REQUIRED", 400},
		scenario{"owner-substituted-target", "POST", collectPath, `{"confirm":true,"target":{"pid":1}}`, tokens[owner.ID], csrf, "INVALID_PARAMETERS", 400},
		scenario{"owner-substituted-followup", "POST", followupPath, `{"confirm":true,"reviewed":true,"target":{"pid":1}}`, tokens[owner.ID], csrf, "INVALID_PARAMETERS", 400},
		scenario{"owner-expired-download", "GET", fmt.Sprintf("%s/artifacts/%d/download", taskPath, expiredArtifact.ID), "", tokens[owner.ID], csrf, "ARTIFACT_EXPIRED", 410},
		scenario{"owner-unknown-artifact", "GET", taskPath + "/artifacts/999999/download", "", tokens[owner.ID], csrf, "NOT_FOUND", 404},
		scenario{"viewer-create", "POST", "/api/v1/tasks", `{"kind":"IMPORT_COLLAPSED","collapsed_text":"imported_fixture 1"}`, tokens[viewer.ID], csrf, "FORBIDDEN", 403},
	)
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			before := snapshot()
			response := request(item.method, item.path, item.body, item.token, item.csrf)
			var envelope struct {
				Error struct {
					Code string `json:"code"`
				} `json:"error"`
			}
			if err := json.Unmarshal(response.Body.Bytes(), &envelope); err != nil {
				t.Fatal("实际路由未返回JSON错误信封", response.Code)
			}
			if response.Code != item.status || envelope.Error.Code != item.code {
				t.Fatalf("拒绝边界：status=%d code=%s want=%d/%s", response.Code, envelope.Error.Code, item.status, item.code)
			}
			if !reflect.DeepEqual(before, snapshot()) {
				t.Fatal("被拒绝请求修改了领域状态、事件、outbox或审计")
			}
		})
	}
	// 成功对照证明夹具并非“所有请求都失败”的无效测试；不触发模型、采集或下载。
	for _, identity := range []domain.User{owner, reader} {
		if w := request("GET", taskPath, "", tokens[identity.ID], csrf); w.Code != 200 {
			t.Fatal("有效所有者/只读分享不能读取任务", w.Code)
		}
	}
}
