// 本文件验证无会话请求失败关闭、固定窗口限流和不依赖外部服务的存活探针。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestMissingSessionFailsClosedOnAllResources(t *testing.T) {
	s := &Server{}
	router := s.Router()
	for _, path := range []string{"/api/v1/tasks", "/api/v1/agents", "/api/v1/overview", "/api/v1/tasks/1/artifacts/1/download", "/api/v1/audit-logs", "/api/v1/users"} {
		r := httptest.NewRequest(http.MethodGet, path, nil)
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		if w.Code != 401 {
			t.Fatalf("%s: got %d", path, w.Code)
		}
		var body map[string]any
		if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
			t.Fatal(err)
		}
		if body["request_id"] == "" || body["data"] != nil {
			t.Fatal(body)
		}
	}
}
func TestLoginLimiterHasBoundedWindow(t *testing.T) {
	l := NewLimiter(2, time.Minute)
	now := time.Now()
	if !l.Allow("ip", now) || !l.Allow("ip", now) || l.Allow("ip", now) {
		t.Fatal("limit failed")
	}
	if !l.Allow("ip", now.Add(time.Minute)) {
		t.Fatal("window did not expire")
	}
}
func TestLiveHealthDoesNotRequireDependencies(t *testing.T) {
	s := &Server{}
	w := httptest.NewRecorder()
	s.Router().ServeHTTP(w, httptest.NewRequest("GET", "/health/live", nil))
	if w.Code != 200 {
		t.Fatal(w.Code)
	}
}
