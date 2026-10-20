package repository

import (
	"context"
	"encoding/json"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"

	"drop/api/internal/domain"
)

// Uses an isolated PostgreSQL schema and leaves the application's schema untouched.
func TestPostgresAtomicWorkflow(t *testing.T) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if p := os.Getenv("TEST_DATABASE_URL_FILE"); p != "" {
		b, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		dsn = strings.TrimSpace(string(b))
	}
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL is required for real PostgreSQL integration")
	}
	open := func(dsn string) *gorm.DB {
		db, err := gorm.Open(postgres.New(postgres.Config{DSN: dsn, PreferSimpleProtocol: true}), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
		if err != nil {
			t.Fatal("cannot connect to integration database")
		}
		return db
	}
	base := open(dsn)
	sqlBase, _ := base.DB()
	defer sqlBase.Close()
	schema := "drop_test_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if err := base.Exec("CREATE SCHEMA " + schema).Error; err != nil {
		t.Fatal(err)
	}
	defer base.Exec("DROP SCHEMA " + schema + " CASCADE")
	parsed, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	q := parsed.Query()
	q.Set("search_path", schema)
	parsed.RawQuery = q.Encode()
	db := open(parsed.String())
	sqlDB, _ := db.DB()
	defer sqlDB.Close()
	sqlDB.SetMaxOpenConns(12)
	migration := os.Getenv("TEST_MIGRATION_FILE")
	if migration == "" {
		migration = filepath.Join("..", "..", "..", "db", "migrations", "001_initial.sql")
	}
	migrations, err := filepath.Glob(filepath.Join(filepath.Dir(migration), "*.sql"))
	if err != nil || len(migrations) == 0 {
		t.Fatal("all authoritative migrations are required")
	}
	for _, file := range migrations {
		body, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		if err := db.Exec(string(body)).Error; err != nil {
			t.Fatal(err)
		}
	}
	r := New(db)
	ctx := context.Background()
	u := domain.User{ID: "owner", Username: "owner", DisplayName: "Owner", Role: "operator", CreatedAt: time.Now()}
	if err := r.Bootstrap(ctx, u); err != nil {
		t.Fatal(err)
	}
	agentID := "integration-agent"
	agent := domain.Agent{ID: agentID, Hostname: "test", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`["PROC_CPU"]`), Labels: []byte(`{}`), Resources: []byte(`{"processes":[{"pid":1}]}`), LastSeenAt: time.Now(), CreatedAt: time.Now()}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	var task domain.Task
	t.Run("concurrent idempotency preserves atomic task attempt event outbox", func(t *testing.T) {
		var group sync.WaitGroup
		errs := make(chan error, 8)
		for i := 0; i < 8; i++ {
			group.Add(1)
			go func() {
				defer group.Done()
				deadline := time.Now().Add(time.Minute)
				in := domain.Task{Name: "transaction test", Kind: "PROC_CPU", AgentID: &agentID, OwnerID: u.ID, Parameters: []byte(`{"pid":1,"duration_seconds":1,"frequency_hz":1}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "integration", IdempotencyKey: "same-key-12345", RequestHash: "same-hash", DeadlineAt: &deadline, CreatedAt: time.Now(), UpdatedAt: time.Now()}
				_, _, _, err := r.CreateTask(ctx, in, true, domain.Audit{UserID: u.ID, Action: "task.create", ResourceType: "task", RequestID: "integration", Detail: []byte(`{}`), CreatedAt: time.Now()})
				errs <- err
			}()
		}
		group.Wait()
		close(errs)
		for err := range errs {
			if err != nil {
				t.Fatal(err)
			}
		}
		for _, table := range []string{"tasks", "task_attempts", "task_events", "outbox", "audit_logs"} {
			var n int64
			if err := db.Table(table).Count(&n).Error; err != nil || n != 1 {
				t.Fatalf("%s count %d err %v", table, n, err)
			}
		}
		var err error
		task, err = r.Existing(ctx, u.ID, "same-key-12345")
		if err != nil {
			t.Fatal(err)
		}
		changed := task
		changed.RequestHash = "different"
		if _, _, _, err := r.CreateTask(ctx, changed, true, domain.Audit{}); err == nil {
			t.Fatal("idempotency conflict accepted")
		}
	})
	t.Run("owner isolation blocks another operator", func(t *testing.T) {
		if _, err := r.Task(ctx, domain.User{ID: "other", Role: "operator"}, task.ID); !errors.Is(err, gorm.ErrRecordNotFound) {
			t.Fatal("cross owner task was exposed", err)
		}
	})
	t.Run("cancel retry and delayed outbox are fenced by attempt", func(t *testing.T) {
		audit := domain.Audit{UserID: u.ID, Action: "task.cancel", ResourceType: "task", RequestID: "integration", Detail: []byte(`{}`), CreatedAt: time.Now()}
		if _, err := r.Cancel(ctx, u, task.ID, audit); err != nil {
			t.Fatal(err)
		}
		if _, _, err := r.Retry(ctx, u, task.ID, "retry-key-1234", "integration"); err != nil {
			t.Fatal(err)
		}
		if _, replayed, err := r.Retry(ctx, u, task.ID, "retry-key-1234", "integration"); err != nil || !replayed {
			t.Fatal("retry replay failed", err)
		}
		var calls atomic.Int64
		for i := 0; i < 3; i++ {
			if err := r.DispatchOne(ctx, func(o domain.Outbox, task domain.Task, a domain.Attempt) error {
				calls.Add(1)
				if o.EventType != "DISPATCH" || a.Attempt != 2 {
					t.Fatal("old attempt reached dispatch")
				}
				return nil
			}); err != nil {
				t.Fatal(err)
			}
		}
		if calls.Load() != 1 {
			t.Fatalf("dispatch calls %d", calls.Load())
		}
	})
	t.Run("schedule concurrent ticks create a single record", func(t *testing.T) {
		template, _ := json.Marshal(domain.CreateTask{Name: "scheduled", Kind: "PROC_CPU", AgentID: agentID, Parameters: domain.Parameters{PID: 1, DurationSeconds: 1, FrequencyHz: 1}})
		sc := domain.Schedule{Name: "every minute", OwnerID: u.ID, IntervalMinutes: 1, Timezone: "UTC", Template: template, Enabled: true, NextRunAt: time.Now().Add(-time.Second), CreatedAt: time.Now(), UpdatedAt: time.Now()}
		if err := r.CreateSchedule(ctx, &sc); err != nil {
			t.Fatal(err)
		}
		var calls atomic.Int64
		var group sync.WaitGroup
		errs := make(chan error, 6)
		for i := 0; i < 6; i++ {
			group.Add(1)
			go func() {
				defer group.Done()
				errs <- r.TickSchedule(ctx, func(r *Repository, s domain.Schedule) (int64, error) { calls.Add(1); return task.ID, nil })
			}()
		}
		group.Wait()
		close(errs)
		for err := range errs {
			if err != nil {
				t.Fatal(err)
			}
		}
		var count int64
		if err := db.Table("schedule_records").Where("schedule_id = ?", sc.ID).Count(&count).Error; err != nil {
			t.Fatal(err)
		}
		if calls.Load() != 1 || count != 1 {
			t.Fatalf("schedule repeated: calls=%d records=%d", calls.Load(), count)
		}
	})
	t.Run("distinct concurrent keys cannot exceed owner quota", func(t *testing.T) {
		quotaOwner := domain.User{ID: "quota-owner", Username: "quota-owner", DisplayName: "Quota", Role: "operator", CreatedAt: time.Now()}
		if err := r.Bootstrap(ctx, quotaOwner); err != nil {
			t.Fatal(err)
		}
		var group sync.WaitGroup
		var accepted, rejected atomic.Int64
		errs := make(chan error, 12)
		for i := 0; i < 12; i++ {
			group.Add(1)
			go func(i int) {
				defer group.Done()
				deadline := time.Now().Add(time.Minute)
				in := domain.Task{Name: "quota", Kind: "PROC_CPU", AgentID: &agentID, OwnerID: quotaOwner.ID, Parameters: []byte(`{}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "quota", IdempotencyKey: uuid.NewString(), RequestHash: "quota", DeadlineAt: &deadline, CreatedAt: time.Now(), UpdatedAt: time.Now()}
				_, _, _, err := r.CreateTask(ctx, in, true, domain.Audit{UserID: quotaOwner.ID, Action: "task.create", ResourceType: "task", RequestID: "quota", Detail: []byte(`{}`), CreatedAt: time.Now()})
				if err == nil {
					accepted.Add(1)
					return
				}
				var de *domain.Error
				if errors.As(err, &de) && de.Code == "QUOTA_EXCEEDED" {
					rejected.Add(1)
					return
				}
				errs <- err
			}(i)
		}
		group.Wait()
		close(errs)
		for err := range errs {
			t.Fatal(err)
		}
		if accepted.Load() != 10 || rejected.Load() != 2 {
			t.Fatalf("quota race: accepted=%d rejected=%d", accepted.Load(), rejected.Load())
		}
	})
	t.Run("overview includes active analysis and excludes canceled pending tasks", func(t *testing.T) {
		overviewUser := domain.User{ID: "overview-owner", Username: "overview-owner", DisplayName: "Overview", Role: "operator", CreatedAt: time.Now()}
		if err := r.Bootstrap(ctx, overviewUser); err != nil {
			t.Fatal(err)
		}
		for _, pair := range [][2]string{{"COLLECTED", "PENDING"}, {"COLLECTED", "RUNNING"}, {"COLLECTED", "RETRY"}, {"CANCELED", "PENDING"}, {"FAILED", "PENDING"}, {"COLLECTED", "SUCCESS"}} {
			row := domain.Task{Name: "overview", Kind: "IMPORT_COLLAPSED", OwnerID: overviewUser.ID, Parameters: []byte(`{}`), CollectionStatus: pair[0], AnalysisStatus: pair[1], RequestID: "overview", IdempotencyKey: uuid.NewString(), CreatedAt: time.Now(), UpdatedAt: time.Now()}
			if err := db.Create(&row).Error; err != nil {
				t.Fatal(err)
			}
		}
		view, err := r.Overview(ctx, overviewUser)
		if err != nil {
			t.Fatal(err)
		}
		if view.TasksTotal != 6 || view.TasksRunning != 3 || view.TasksSuccess != 1 {
			t.Fatalf("incorrect actual aggregation: %+v", view)
		}
	})
	t.Run("interrupted import becomes an explicit failed terminal", func(t *testing.T) {
		deadline := time.Now().Add(-time.Minute)
		in := domain.Task{Name: "interrupted import", Kind: "IMPORT_COLLAPSED", OwnerID: u.ID, Parameters: []byte(`{}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "interrupted", IdempotencyKey: "interrupted-import-key", RequestHash: "import", DeadlineAt: &deadline, CreatedAt: time.Now(), UpdatedAt: time.Now()}
		task, attempt, _, err := r.CreateTask(ctx, in, false, domain.Audit{UserID: u.ID, Action: "task.create", ResourceType: "task", RequestID: "interrupted", Detail: []byte(`{}`), CreatedAt: time.Now()})
		if err != nil {
			t.Fatal(err)
		}
		if err := r.SweepStaleImport(ctx); err != nil {
			t.Fatal(err)
		}
		failed, err := r.Task(ctx, u, task.ID)
		if err != nil {
			t.Fatal(err)
		}
		if !failed.Terminal() || failed.ErrorCode == nil || *failed.ErrorCode != "IMPORT_TIMEOUT" {
			t.Fatalf("incomplete import was not failed: %+v", failed)
		}
		var stored domain.Attempt
		if err := db.First(&stored, attempt.ID).Error; err != nil {
			t.Fatal(err)
		}
		if stored.Status != "FAILED" {
			t.Fatal(stored.Status)
		}
	})
}
