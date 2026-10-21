// 扩展接口的HTTP表面回归：模型工具与人类管理接口必须分开，所有管理动作仍受会话、CSRF和闭合JSON约束。
// 夹具均为明确导入的合成标识；此处不调用模型、外部信号源或采集器。
package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"drop/api/internal/testutil"
)

func TestAgentExtensionHTTPMissingSessionFailsClosed(t *testing.T) {
	router := (&Server{}).Router()
	for _, route := range []struct{ method, path string }{
		{"GET", "/api/v1/diagnostic-data-sources?agent_id=imported-agent"},
		{"GET", "/api/v1/diagnostic-playbooks"},
		{"POST", "/api/v1/diagnostic-playbooks"},
		{"POST", "/api/v1/diagnostic-playbooks/1/review"},
		{"GET", "/api/v1/investigations/1/followups"},
		{"POST", "/api/v1/investigations/1/followups/1/decision"},
	} {
		t.Run(route.method+route.path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			router.ServeHTTP(recorder, httptest.NewRequest(route.method, route.path, strings.NewReader(`{}`)))
			if recorder.Code != http.StatusUnauthorized {
				t.Fatal("未认证请求进入扩展接口", recorder.Code)
			}
		})
	}
}

func TestPostgresAgentExtensionHTTPRequiresReviewCSRFAndClosedInput(t *testing.T) {
	db := testutil.Postgres(t)
	repo := repository.New(db)
	u := domain.User{ID: "imported-extension-user", Username: "imported-extension-user", Role: "operator"}
	if err := db.Create(&u).Error; err != nil {
		t.Fatal(err)
	}
	token, csrf := strings.Repeat("a", 64), strings.Repeat("b", 64)
	if err := repo.NewSession(context.Background(), repository.Session{TokenHash: repository.Digest(token), UserID: u.ID, CSRFToken: csrf, ExpiresAt: time.Now().Add(time.Hour)}); err != nil {
		t.Fatal(err)
	}
	server := &Server{Repo: repo, Tasks: &service.Tasks{Repo: repo}}
	server.Suggestions, _ = service.NewSuggestions(repo, server.Tasks, service.AIConfig{})
	router := server.Router()
	request := func(method, path, body, suppliedCSRF string) (int, string) {
		t.Helper()
		req := httptest.NewRequest(method, path, strings.NewReader(body))
		req.AddCookie(&http.Cookie{Name: "drop_session", Value: token})
		req.Header.Set("X-CSRF-Token", suppliedCSRF)
		req.Header.Set("Content-Type", "application/json")
		recorder := httptest.NewRecorder()
		router.ServeHTTP(recorder, req)
		var envelope struct {
			Error struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		if err := json.Unmarshal(recorder.Body.Bytes(), &envelope); err != nil {
			t.Fatal("扩展接口没有返回JSON信封", err)
		}
		return recorder.Code, envelope.Error.Code
	}
	for _, path := range []string{"/api/v1/diagnostic-playbooks", "/api/v1/diagnostic-playbooks/1/review", "/api/v1/investigations/1/followups/1/decision"} {
		if status, _ := request("POST", path, `{}`, ""); status != http.StatusForbidden {
			t.Fatal("扩展写接口绕过CSRF", path, status)
		}
	}
	for _, item := range []struct{ path, body, code string }{
		{"/api/v1/diagnostic-playbooks/1/review", `{"action":"publish","review_confirmed":false}`, "CONSENT_REQUIRED"},
		{"/api/v1/diagnostic-playbooks/1/review", `{"action":"publish","review_confirmed":true,"accepted":true}`, "INVALID_PARAMETERS"},
		{"/api/v1/diagnostic-playbooks/1/review", `{"action":"publish","review_confirmed":true} {}`, "INVALID_PARAMETERS"},
		{"/api/v1/investigations/1/followups/1/decision", `{"confirm":true,"reviewed":false}`, "CONSENT_REQUIRED"},
		{"/api/v1/investigations/1/followups/1/decision", `{"confirm":true,"reviewed":true,"target":{"pid":1}}`, "INVALID_PARAMETERS"},
	} {
		if status, code := request("POST", item.path, item.body, csrf); status != http.StatusBadRequest || code != item.code {
			t.Fatal("审阅标记或闭合输入边界失效", status, code, item.code)
		}
	}
	if status, code := request("POST", "/api/v1/diagnostic-playbooks/1/review", `{"action":"publish","review_confirmed":true}`, csrf); status != http.StatusForbidden || code != "FORBIDDEN" {
		t.Fatal("普通操作员获得方法发布权限", status, code)
	}
	if status, code := request("GET", "/api/v1/diagnostic-data-sources", "", csrf); status != http.StatusBadRequest || code != "INVALID_PARAMETERS" {
		t.Fatal("目录没有要求明确节点", status, code)
	}
}
