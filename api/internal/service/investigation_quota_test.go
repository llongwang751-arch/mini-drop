// 本文件使用明确的合成进程、调查状态夹具和真实 PostgreSQL 验证每账号调查准入。
// 不启动规划/采集工作器，不请求模型，也不把手工推进的状态当作真实采集或性能验收。
package service

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"testing"
	"time"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

func investigationQuotaFixture(t *testing.T) (*gorm.DB, *Suggestions, domain.User, InvestigationInput) {
	t.Helper()
	db, s, u, task := investigationFixture(t)
	s.Config = AIConfig{Endpoint: "https://explicit-unused-quota-fixture.invalid", Model: "explicit-unused-quota-fixture"}
	s.Client = &http.Client{Transport: fixtureRoundTripper(func(*http.Request) (*http.Response, error) {
		t.Error("quota admission must not call the model")
		return nil, errors.New("model calls are disabled in the synthetic quota fixture")
	})}
	in := InvestigationInput{
		Goal:       "Explicit synthetic quota fixture; no collection or model execution",
		AgentID:    *task.AgentID,
		Parameters: domain.Parameters{PID: 7},
		Consent:    true,
	}
	return db, s, u, in
}

func requireInvestigationQuotaError(t *testing.T, err error, code string) {
	t.Helper()
	var actual *domain.Error
	if !errors.As(err, &actual) || actual.Code != code {
		t.Fatalf("want %s, got %v", code, err)
	}
}

func requireActiveInvestigationCount(t *testing.T, db *gorm.DB, userID string, want int64) {
	t.Helper()
	var actual int64
	if err := db.Model(&Investigation{}).
		Where("requested_by=? AND status NOT IN ('success','failed','canceled')", userID).
		Count(&actual).Error; err != nil {
		t.Fatal(err)
	}
	if actual != want {
		t.Fatalf("active investigations for synthetic user %q: want %d, got %d", userID, want, actual)
	}
}

func TestPostgresInvestigationQuotaTenIncludesAwaitingConfirmationAndReplay(t *testing.T) {
	db, s, u, in := investigationQuotaFixture(t)
	ctx := context.Background()
	var first Investigation
	for i := 1; i <= 10; i++ {
		run, err := s.RequestInvestigation(ctx, u, in, fmt.Sprintf("quota-awaiting-%d", i), "explicit-quota-fixture")
		if err != nil {
			t.Fatalf("request %d must be admitted, including requests 4 and 10: %v", i, err)
		}
		// 显式状态夹具：没有调用规划器，专门检验等确认也占用名额。
		if err := db.Model(&Investigation{}).Where("id=?", run.ID).Update("status", "awaiting_confirmation").Error; err != nil {
			t.Fatal(err)
		}
		if i == 1 {
			first = run
		}
	}
	requireActiveInvestigationCount(t, db, u.ID, 10)

	replayed, err := s.RequestInvestigation(ctx, u, in, "quota-awaiting-1", "explicit-quota-replay-fixture")
	if err != nil || replayed.ID != first.ID || replayed.Status != "awaiting_confirmation" {
		t.Fatalf("same-key replay must still work at the quota: got %+v, %v", replayed, err)
	}
	changed := in
	changed.Goal = "Explicit different synthetic request"
	_, err = s.RequestInvestigation(ctx, u, changed, "quota-awaiting-1", "explicit-quota-conflict-fixture")
	requireInvestigationQuotaError(t, err, "IDEMPOTENCY_CONFLICT")
	_, err = s.RequestInvestigation(ctx, u, in, "quota-awaiting-11", "explicit-quota-rejected-fixture")
	requireInvestigationQuotaError(t, err, "DIAGNOSIS_BUSY")
	requireActiveInvestigationCount(t, db, u.ID, 10)

	var count int64
	if err := db.Model(&Investigation{}).Where("requested_by=?", u.ID).Count(&count).Error; err != nil || count != 10 {
		t.Fatalf("replay/conflict/rejection created an extra investigation: count=%d, err=%v", count, err)
	}
	if err := db.Model(&domain.Task{}).Count(&count).Error; err != nil || count != 1 {
		t.Fatalf("admission started collection rather than preserving the single imported fixture: count=%d, err=%v", count, err)
	}
}

func TestPostgresInvestigationQuotaTerminalStatesReleaseSlots(t *testing.T) {
	db, s, u, in := investigationQuotaFixture(t)
	ctx := context.Background()
	runs := make([]Investigation, 10)
	for i := range runs {
		var err error
		runs[i], err = s.RequestInvestigation(ctx, u, in, fmt.Sprintf("quota-terminal-initial-%d", i), "explicit-quota-fixture")
		if err != nil {
			t.Fatal(err)
		}
	}
	for i, status := range []string{"success", "failed", "canceled"} {
		if status == "canceled" {
			if err := s.CancelInvestigation(ctx, u, runs[i].ID, "explicit-quota-cancellation-fixture"); err != nil {
				t.Fatal(err)
			}
		} else {
			// 成功和失败是显式生命周期夹具，不代表实际完成过模型/采集。
			if err := db.Model(&Investigation{}).Where("id=?", runs[i].ID).Update("status", status).Error; err != nil {
				t.Fatal(err)
			}
		}
		requireActiveInvestigationCount(t, db, u.ID, 9)
		if _, err := s.RequestInvestigation(ctx, u, in, "quota-terminal-replacement-"+status, "explicit-quota-replacement-fixture"); err != nil {
			t.Fatalf("terminal state %s did not release a slot: %v", status, err)
		}
		requireActiveInvestigationCount(t, db, u.ID, 10)
		_, err := s.RequestInvestigation(ctx, u, in, "quota-terminal-overflow-"+status, "explicit-quota-rejected-fixture")
		requireInvestigationQuotaError(t, err, "DIAGNOSIS_BUSY")
	}
	var total int64
	if err := db.Model(&Investigation{}).Where("requested_by=?", u.ID).Count(&total).Error; err != nil || total != 13 {
		t.Fatalf("terminal history must remain persisted: count=%d, err=%v", total, err)
	}
}

func TestPostgresInvestigationQuotaIsPerAccount(t *testing.T) {
	db, s, owner, in := investigationQuotaFixture(t)
	other := domain.User{ID: "quota-other-synthetic-owner", Username: "quota-other-synthetic-owner", Role: "platform_admin"}
	if err := db.Create(&other).Error; err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	for _, u := range []domain.User{owner, other} {
		for i := 1; i <= 10; i++ {
			// 两个账号刻意复用相同幂等键，证明键和名额均按账号隔离。
			if _, err := s.RequestInvestigation(ctx, u, in, fmt.Sprintf("quota-per-account-%d", i), "explicit-quota-account-fixture"); err != nil {
				t.Fatalf("synthetic account %s request %d must be admitted: %v", u.ID, i, err)
			}
		}
		_, err := s.RequestInvestigation(ctx, u, in, "quota-per-account-11", "explicit-quota-rejected-fixture")
		requireInvestigationQuotaError(t, err, "DIAGNOSIS_BUSY")
		requireActiveInvestigationCount(t, db, u.ID, 10)
	}
}

func TestPostgresInvestigationQuotaConcurrentAdmission(t *testing.T) {
	db, s, u, in := investigationQuotaFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	type admission struct {
		id  int64
		err error
	}
	const contenders = 24
	start := make(chan struct{})
	results := make(chan admission, contenders)
	var wg sync.WaitGroup
	for i := 0; i < contenders; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			<-start
			run, err := s.RequestInvestigation(ctx, u, in, fmt.Sprintf("quota-concurrent-%d", i), "explicit-quota-concurrent-fixture")
			results <- admission{id: run.ID, err: err}
		}(i)
	}
	close(start)
	wg.Wait()
	close(results)
	accepted := make(map[int64]bool)
	rejected := 0
	for result := range results {
		if result.err != nil {
			requireInvestigationQuotaError(t, result.err, "DIAGNOSIS_BUSY")
			rejected++
			continue
		}
		if result.id == 0 || accepted[result.id] {
			t.Fatalf("distinct concurrent keys must receive distinct persisted IDs, got %d", result.id)
		}
		accepted[result.id] = true
	}
	if len(accepted) != 10 || rejected != contenders-10 {
		t.Fatalf("concurrent requests bypassed or underfilled quota: accepted=%d rejected=%d", len(accepted), rejected)
	}
	requireActiveInvestigationCount(t, db, u.ID, 10)
}
