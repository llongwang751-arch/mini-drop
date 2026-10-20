// 本文件验证RPC 返回与 ACK、取消、重试竞争时，旧快照不能覆盖新状态；取消重放不能重复写事件。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package repository

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/testutil"
)

func TestPostgresExhaustedDispatchCannotOverwriteConcurrentLifecycle(t *testing.T) {
	for _, mode := range []string{"accepted", "canceled", "retried"} {
		t.Run(mode, func(t *testing.T) {
			db := testutil.Postgres(t)
			r, ctx := New(db), context.Background()
			owner := domain.User{ID: "recovery-owner", Username: "recovery-owner", DisplayName: "Imported recovery fixture", Role: "operator"}
			if err := db.Create(&owner).Error; err != nil {
				t.Fatal(err)
			}
			agent := domain.Agent{ID: "recovery-agent", Hostname: "fixture", Version: "fixture", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)}
			if err := db.Create(&agent).Error; err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(time.Hour)
			audit := domain.Audit{Action: "fixture.recovery", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)}
			task, attempt, _, err := r.CreateTask(ctx, domain.Task{Name: "Imported exhausted-dispatch race fixture", Kind: "PROC_CPU", OwnerID: owner.ID, AgentID: &agent.ID, CollectionStatus: "CREATED", AnalysisStatus: "PENDING", Parameters: []byte(`{}`), DeadlineAt: &deadline, RequestID: "fixture", IdempotencyKey: "recovery-fixture"}, true, audit)
			if err != nil {
				t.Fatal(err)
			}
			if err := db.Model(&domain.Outbox{}).Where("task_id=? AND event_type='DISPATCH'", task.ID).Update("attempt", 9).Error; err != nil {
				t.Fatal(err)
			}
			err = r.DispatchOne(ctx, func(_ domain.Outbox, _ domain.Task, _ domain.Attempt) error {
				// RPC 尚未返回时，另一个事务已经提交新的任务状态。
				// 即使 ACK 丢失，也不能让旧 Outbox 快照覆盖这份已提交的事实。
				if mode == "accepted" {
					if err := db.Model(&domain.Task{}).Where("id=?", task.ID).Update("collection_status", "QUEUED").Error; err != nil {
						return err
					}
				} else {
					if _, err := r.Cancel(ctx, owner, task.ID, audit); err != nil {
						return err
					}
					if mode == "retried" {
						if _, _, err := r.Retry(ctx, owner, task.ID, "recovery-retry", "retry-fixture"); err != nil {
							return err
						}
					}
				}
				return errors.New("explicit lost RPC response fixture")
			})
			if err != nil {
				t.Fatal(err)
			}
			if err := db.First(&task, task.ID).Error; err != nil {
				t.Fatal(err)
			}
			want := map[string]string{"accepted": "QUEUED", "canceled": "CANCELED", "retried": "CREATED"}[mode]
			if task.CollectionStatus != want {
				t.Fatalf("stale dispatch overwrote committed lifecycle: got %s, want %s", task.CollectionStatus, want)
			}
			if err := db.First(&attempt, attempt.ID).Error; err != nil {
				t.Fatal(err)
			}
			wantAttempt := "CANCELED"
			if mode == "accepted" {
				wantAttempt = "CREATED"
			}
			if attempt.Status != wantAttempt {
				t.Fatalf("stale dispatch overwrote attempt: %s", attempt.Status)
			}
			var failed int64
			if err := db.Model(&domain.Event{}).Where("task_id=? AND type='TASK_FAILED'", task.ID).Count(&failed).Error; err != nil || failed != 0 {
				t.Fatal("a stale dispatch emitted a terminal failure event")
			}
			var out domain.Outbox
			if err := db.Where("task_id=? AND attempt_id=? AND event_type='DISPATCH'", task.ID, attempt.ID).First(&out).Error; err != nil || out.PublishedAt == nil {
				t.Fatal("obsolete dispatch was not retired")
			}
			if mode == "retried" {
				var current domain.Attempt
				if err := db.Where("task_id=?", task.ID).Order("attempt DESC").First(&current).Error; err != nil || current.Attempt != 2 || current.Status != "CREATED" {
					t.Fatal("new attempt was modified by exhausted old dispatch")
				}
			}
		})
	}
}

func TestPostgresCancelIsIdempotentForConcurrentAndTerminalRequests(t *testing.T) {
	db := testutil.Postgres(t)
	r, ctx := New(db), context.Background()
	owner := domain.User{ID: "cancel-replay-owner", Username: "cancel-replay-owner", DisplayName: "Imported cancellation fixture", Role: "operator"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	audit := domain.Audit{Action: "fixture.cancel", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)}
	create := func(index int, collection, analysis string) domain.Task {
		task, _, _, err := r.CreateTask(ctx, domain.Task{Name: "Imported cancellation replay fixture", Kind: "IMPORT_COLLAPSED", OwnerID: owner.ID, CollectionStatus: collection, AnalysisStatus: analysis, Parameters: []byte(`{}`), RequestID: "fixture", IdempotencyKey: fmt.Sprintf("cancel-replay-%d", index)}, false, domain.Audit{Action: "fixture.create", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)})
		if err != nil {
			t.Fatal(err)
		}
		return task
	}
	task := create(0, "CREATED", "PENDING")
	var group sync.WaitGroup
	failures := make(chan error, 6)
	for range 6 {
		group.Add(1)
		go func() { defer group.Done(); _, err := r.Cancel(ctx, owner, task.ID, audit); failures <- err }()
	}
	group.Wait()
	close(failures)
	for err := range failures {
		if err != nil {
			t.Errorf("concurrent repeated cancel failed: %v", err)
		}
	}
	for _, table := range []string{"task_events", "outbox", "audit_logs"} {
		query := db.Table(table)
		if table == "audit_logs" {
			query = query.Where("action='fixture.cancel'")
		} else if table == "outbox" {
			query = query.Where("task_id=? AND event_type='CANCEL'", task.ID)
		} else {
			query = query.Where("task_id=? AND type='TASK_CANCELED'", task.ID)
		}
		var count int64
		if err := query.Count(&count).Error; err != nil || count != 1 {
			t.Errorf("repeated cancel wrote duplicate %s: %d, %v", table, count, err)
		}
	}
	for index, state := range [][2]string{{"FAILED", "FAILED"}, {"COLLECTED", "SUCCESS"}, {"COLLECTED", "FAILED"}, {"CANCELED", "CANCELED"}} {
		terminal := create(index+1, state[0], state[1])
		for range 2 {
			actual, err := r.Cancel(ctx, owner, terminal.ID, audit)
			if err != nil || actual.CollectionStatus != state[0] || actual.AnalysisStatus != state[1] || actual.Version != terminal.Version {
				t.Errorf("terminal cancel changed current state or failed: %s/%s: %v", state[0], state[1], err)
			}
		}
		var count int64
		if err := db.Model(&domain.Event{}).Where("task_id=? AND type='TASK_CANCELED'", terminal.ID).Count(&count).Error; err != nil || count != 0 {
			t.Error("terminal replay created a cancellation event")
		}
	}
}

func TestPostgresRuntimeRefusesMissingDiagnosisMigration(t *testing.T) {
	db := testutil.Postgres(t)
	r, ctx := New(db), context.Background()
	if err := r.CheckSchema(ctx); err != nil {
		t.Fatal("fully migrated schema was rejected", err)
	}
	if err := db.Exec("DELETE FROM schema_migrations WHERE version=6").Error; err != nil {
		t.Fatal(err)
	}
	if err := r.CheckSchema(ctx); err == nil {
		t.Fatal("runtime accepted a database without its required diagnosis migration")
	}
	if err := db.Exec("INSERT INTO schema_migrations(version) VALUES(6)").Error; err != nil {
		t.Fatal(err)
	}
	if err := r.CheckSchema(ctx); err != nil {
		t.Fatal("restored required migration was rejected", err)
	}
}
