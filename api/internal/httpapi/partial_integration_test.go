// 本文件验证部分结果的当前分析运行身份及授权；重试、重分析或权限变化后旧数据不可见。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"drop/api/internal/telemetry"
	"drop/api/internal/testutil"
)

func TestPostgresPartialResultCurrentRunAndAuthorization(t *testing.T) {
	db := testutil.Postgres(t)
	r := repository.New(db)
	ctx := context.Background()
	owner := domain.User{ID: "partial-owner", Username: "partial-owner", Role: "operator"}
	viewer := domain.User{ID: "partial-viewer", Username: "partial-viewer", Role: "viewer"}
	other := domain.User{ID: "partial-other", Username: "partial-other", Role: "operator"}
	for _, u := range []domain.User{owner, viewer, other} {
		if err := db.Create(&u).Error; err != nil {
			t.Fatal(err)
		}
	}
	tokens := map[string]string{owner.ID: strings.Repeat("c", 64), viewer.ID: strings.Repeat("d", 64), other.ID: strings.Repeat("e", 64)}
	for uid, token := range tokens {
		if err := r.NewSession(ctx, repository.Session{TokenHash: repository.Digest(token), UserID: uid, CSRFToken: strings.Repeat("f", 64), ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
			t.Fatal(err)
		}
	}
	task, attempt, _, err := r.CreateTask(ctx, domain.Task{Name: "Imported partial-result fixture", Kind: "IMPORT_COLLAPSED", OwnerID: owner.ID, Parameters: []byte(`{"source":"import"}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "imported-partial", IdempotencyKey: "partial-fixture"}, false, domain.Audit{Action: "test", ResourceType: "task", RequestID: "imported-partial", Detail: []byte(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	exec := func(sql string, args ...any) {
		t.Helper()
		if err := db.Exec(sql, args...).Error; err != nil {
			t.Fatal(err)
		}
	}
	exec("UPDATE tasks SET collection_status='COLLECTED',analysis_status='FAILED' WHERE id=?", task.ID)
	raw := domain.Artifact{TaskID: task.ID, AttemptID: attempt.ID, Kind: "RAW", Name: "synthetic.folded", ObjectKey: "imported/partial/synthetic.folded", ContentType: "text/plain", Size: 4, SHA256: strings.Repeat("a", 64), Status: "ready"}
	if err := db.Create(&raw).Error; err != nil {
		t.Fatal(err)
	}
	var job struct{ ID int64 }
	if err := db.Raw("INSERT INTO analysis_jobs(task_id,attempt_id,status,attempt) VALUES(?,?,'failed',2) RETURNING id", task.ID, attempt.ID).Scan(&job).Error; err != nil {
		t.Fatal(err)
	}
	catalog := os.Getenv("TEST_TASK_KINDS_FILE")
	if catalog == "" {
		catalog = filepath.Join("..", "..", "..", "contracts", "task-kinds.json")
	}
	input, err := os.ReadFile(filepath.Join(filepath.Dir(catalog), "fixtures", "partial-result-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var fixture map[string]any
	if json.Unmarshal(input, &fixture) != nil {
		t.Fatal("invalid imported contract fixture")
	}
	fixture["task_id"] = fmt.Sprint(task.ID)
	fixture["attempt_id"] = fmt.Sprint(attempt.ID)
	fixture["analysis_job_id"] = fmt.Sprint(job.ID)
	fixture["analysis_run"] = 2
	fixture["input_artifact_id"] = fmt.Sprint(raw.ID)
	fixture["input_digest"] = raw.SHA256
	original, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	reset := func() {
		exec("UPDATE analysis_jobs SET status='failed',attempt=2,lease_owner=NULL,lease_expires_at=NULL,result=?::jsonb WHERE id=?", string(original), job.ID)
		exec("UPDATE tasks SET collection_status='COLLECTED',analysis_status='FAILED',deleted_at=NULL WHERE id=?", task.ID)
	}
	reset()
	server := &Server{Repo: r, Tasks: &service.Tasks{Repo: r}, Telemetry: telemetry.New(db, nil)}
	router := server.Router()
	request := func(uid, suffix string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("GET", fmt.Sprintf("/api/v1/tasks/%d%s", task.ID, suffix), nil)
		if token := tokens[uid]; token != "" {
			req.AddCookie(&http.Cookie{Name: "drop_session", Value: token})
		}
		w := httptest.NewRecorder()
		router.ServeHTTP(w, req)
		return w
	}
	expect := func(uid, suffix string, status int) {
		t.Helper()
		w := request(uid, suffix)
		if w.Code != status {
			t.Fatalf("partial fixture %s: got %d expected %d", suffix, w.Code, status)
		}
	}
	expect(owner.ID, "/partial-result", 200)
	t.Run("bounded UTF8 JSON wire output", func(t *testing.T) {
		changed := map[string]any{}
		_ = json.Unmarshal(original, &changed)
		rows := make([]map[string]any, 20)
		for i := range rows {
			rows[i] = map[string]any{"name": strings.Repeat("&<>", 53), "symbol_sha256": strings.Repeat("b", 64), "self": 1, "total": 1, "self_percent": 25, "total_percent": 25}
		}
		changed["top_functions"] = rows
		body, _ := json.Marshal(changed)
		exec("UPDATE analysis_jobs SET result=?::jsonb WHERE id=?", string(body), job.ID)
		w := request(owner.ID, "/partial-result")
		if w.Code != 200 || w.Body.Len() > domain.MaxPartialResultBytes+1024 || !strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") || !strings.Contains(w.Body.String(), "&<>") {
			t.Fatal("valid bounded symbols expanded or escaped outside the JSON wire contract")
		}
		reset()
	})
	expect(owner.ID, "/result", 409)
	expect(other.ID, "/partial-result", 404)
	expect("", "/partial-result", 401)
	detail := request(owner.ID, "")
	if detail.Code != 200 {
		t.Fatal("task detail failed")
	}
	var envelope struct {
		Data struct {
			Identity *domain.AnalysisIdentity `json:"analysis_identity"`
		}
	}
	if json.Unmarshal(detail.Body.Bytes(), &envelope) != nil || envelope.Data.Identity == nil || envelope.Data.Identity.AnalysisRun != 2 || envelope.Data.Identity.InputArtifactID != fmt.Sprint(raw.ID) {
		t.Fatal("current analysis identity absent or mismatched")
	}
	uid := viewer.ID
	if _, err := r.CreateShare(ctx, owner, task.ID, &uid, nil, "read"); err != nil {
		t.Fatal(err)
	}
	expect(viewer.ID, "/partial-result", 200)
	exec("DELETE FROM task_shares WHERE task_id=?", task.ID)
	expect(viewer.ID, "/partial-result", 404)
	for _, state := range []string{"pending", "running", "retry", "success"} {
		t.Run("hidden job "+state, func(t *testing.T) {
			reset()
			exec("UPDATE analysis_jobs SET status=? WHERE id=?", state, job.ID)
			expect(owner.ID, "/partial-result", 409)
		})
	}
	t.Run("old run", func(t *testing.T) {
		reset()
		exec("UPDATE analysis_jobs SET attempt=3 WHERE id=?", job.ID)
		expect(owner.ID, "/partial-result", 409)
	})
	t.Run("cancel", func(t *testing.T) {
		reset()
		exec("UPDATE tasks SET collection_status='CANCELED' WHERE id=?", task.ID)
		expect(owner.ID, "/partial-result", 409)
	})
	t.Run("deleted task", func(t *testing.T) {
		reset()
		exec("UPDATE tasks SET deleted_at=now() WHERE id=?", task.ID)
		expect(owner.ID, "/partial-result", 404)
	})
	t.Run("lease still owned", func(t *testing.T) {
		reset()
		exec("UPDATE analysis_jobs SET lease_owner='synthetic-owner',lease_expires_at=now()+interval '1 minute' WHERE id=?", job.ID)
		expect(owner.ID, "/partial-result", 409)
	})
	for _, key := range []string{"input_digest", "input_artifact_id", "attempt_id", "analysis_job_id", "task_id", "kind"} {
		t.Run("mismatched "+key, func(t *testing.T) {
			reset()
			changed := map[string]any{}
			_ = json.Unmarshal(original, &changed)
			changed[key] = "synthetic-mismatch"
			body, _ := json.Marshal(changed)
			exec("UPDATE analysis_jobs SET result=?::jsonb WHERE id=?", string(body), job.ID)
			expect(owner.ID, "/partial-result", 409)
		})
	}
	t.Run("malformed snapshot", func(t *testing.T) {
		reset()
		changed := map[string]any{}
		_ = json.Unmarshal(original, &changed)
		changed["metadata"] = map[string]any{"private": "synthetic"}
		body, _ := json.Marshal(changed)
		exec("UPDATE analysis_jobs SET result=?::jsonb WHERE id=?", string(body), job.ID)
		expect(owner.ID, "/partial-result", 409)
	})
	t.Run("new raw", func(t *testing.T) {
		reset()
		newRaw := raw
		newRaw.ID = 0
		newRaw.ObjectKey = "imported/partial/new-synthetic.folded"
		if err := db.Create(&newRaw).Error; err != nil {
			t.Fatal(err)
		}
		expect(owner.ID, "/partial-result", 409)
		exec("DELETE FROM artifacts WHERE id=?", newRaw.ID)
	})
	t.Run("new collection", func(t *testing.T) {
		reset()
		exec("INSERT INTO task_attempts(task_id,attempt,status,raw_object_key) VALUES(?,2,'COLLECTED','imported/partial/new-attempt.raw')", task.ID)
		expect(owner.ID, "/partial-result", 409)
	})
}
