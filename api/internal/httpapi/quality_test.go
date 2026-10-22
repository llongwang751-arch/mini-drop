package httpapi

import (
	"context"
	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/testutil"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestQualityRoutesRequireSession(t *testing.T) {
	router := (&Server{}).Router()
	for _, path := range []string{"/api/v1/quality/runs", "/api/v1/quality/runs/fixture-run-001"} {
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("GET", path, nil))
		if w.Code != 401 {
			t.Fatal("quality route missing authentication")
		}
	}
}

// 真实PG会话/实时角色授权；受测目录为空，非管理员须在读取文件前拒绝。
func TestPostgresQualityRoutesRespectRoleRevocation(t *testing.T) {
	db := testutil.Postgres(t)
	repo := repository.New(db)
	u := domain.User{ID: "quality-admin", Username: "quality-admin", DisplayName: "Quality", Role: "platform_admin"}
	if err := db.Create(&u).Error; err != nil {
		t.Fatal(err)
	}
	token := strings.Repeat("c", 64)
	if err := repo.NewSession(context.Background(), repository.Session{TokenHash: repository.Digest(token), UserID: u.ID, CSRFToken: strings.Repeat("d", 64), ExpiresAt: time.Now().Add(time.Hour), CreatedAt: time.Now()}); err != nil {
		t.Fatal(err)
	}
	s := &Server{Repo: repo, QualityReportsDir: ""}
	router := s.Router()
	get := func(path string) int {
		r := httptest.NewRequest("GET", path, nil)
		r.AddCookie(&http.Cookie{Name: "drop_session", Value: token})
		w := httptest.NewRecorder()
		router.ServeHTTP(w, r)
		return w.Code
	}
	if get("/api/v1/quality/runs") != 200 {
		t.Fatal("admin empty catalog")
	}
	if err := db.Model(&domain.User{}).Where("id = ?", u.ID).Update("role", "operator").Error; err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"/api/v1/quality/runs", "/api/v1/quality/runs/fixture-run-001"} {
		if get(path) != 403 {
			t.Fatal("revoked role accessed platform quality evidence")
		}
	}
}
