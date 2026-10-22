// 固定种子的随机操作序列走真实 Repository 和 PostgreSQL 事务，不在测试中实现另一套状态机。
// 故障时打印 seed 和最短失败前缀；把该 seed 放入 DROP_LIFECYCLE_SEED 即可独立复现。
// 所有任务均为 synthetic fixture，没有调用采集器、对象存储或模型。
package repository

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"strconv"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/testutil"
	"gorm.io/gorm"
)

func TestPostgresLifecycleRandomizedProperties(t *testing.T) {
	seeds := []int64{7, 1984, 20261023, 922337}
	if override := os.Getenv("DROP_LIFECYCLE_SEED"); override != "" {
		seed, err := strconv.ParseInt(override, 10, 64)
		if err != nil {
			t.Fatal("DROP_LIFECYCLE_SEED must be a signed 64-bit decimal")
		}
		seeds = []int64{seed}
	}
	for _, seed := range seeds {
		t.Run(fmt.Sprintf("seed_%d", seed), func(t *testing.T) {
			db, ctx := testutil.Postgres(t), context.Background()
			r := New(db)
			owner := domain.User{ID: "synthetic-owner", Username: "synthetic-owner", Role: "operator"}
			outsider := domain.User{ID: "synthetic-outsider", Username: "synthetic-outsider", Role: "operator"}
			for _, user := range []domain.User{owner, outsider} {
				if err := db.Create(&user).Error; err != nil {
					t.Fatal(err)
				}
			}
			agent := domain.Agent{ID: "synthetic-node", Hostname: "synthetic", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)}
			if err := db.Create(&agent).Error; err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(time.Hour)
			input := domain.Task{Name: "synthetic lifecycle sequence", Kind: "PROC_CPU", AgentID: &agent.ID, OwnerID: owner.ID, Parameters: []byte(`{"pid":7,"duration_seconds":1,"frequency_hz":1}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", RequestID: "synthetic", IdempotencyKey: "synthetic-create", RequestHash: "synthetic-hash", DeadlineAt: &deadline}
			audit := domain.Audit{UserID: owner.ID, Action: "synthetic.quality", ResourceType: "task", RequestID: "synthetic", Detail: []byte(`{}`)}
			task, _, _, err := r.CreateTask(ctx, input, true, audit)
			if err != nil {
				t.Fatal(err)
			}
			// 每一步比较数据库事实；失败操作、幂等重放不应留下半条事件或新 attempt。
			snapshot := func() string {
				var state struct {
					Task     domain.Task
					Attempts []domain.Attempt
					Events   []domain.Event
					Outbox   []domain.Outbox
				}
				if err := db.First(&state.Task, task.ID).Error; err != nil {
					t.Fatal(err)
				}
				if err := db.Where("task_id=?", task.ID).Order("attempt").Find(&state.Attempts).Error; err != nil {
					t.Fatal(err)
				}
				if err := db.Where("task_id=?", task.ID).Order("sequence").Find(&state.Events).Error; err != nil {
					t.Fatal(err)
				}
				if err := db.Where("task_id=?", task.ID).Order("id").Find(&state.Outbox).Error; err != nil {
					t.Fatal(err)
				}
				raw, err := json.Marshal(state)
				if err != nil {
					t.Fatal(err)
				}
				return string(raw)
			}
			rng := rand.New(rand.NewSource(seed))
			prefix, lastRetry := []string{}, ""
			fail := func(message string) {
				t.Helper()
				t.Fatalf("%s; seed=%d minimal_failure_prefix=%v", message, seed, prefix)
			}
			for step := 0; step < 96; step++ {
				// 开头覆盖每类操作，此后随机组合；固定上限防止测试成为无界压力任务。
				op := step
				if op >= 7 {
					op = rng.Intn(7)
				}
				prefix = append(prefix, []string{"cancel", "retry", "retry_replay", "foreign_mutation", "create_replay", "dispatch", "lost_ack"}[op])
				before := snapshot()
				current, err := r.Task(ctx, owner, task.ID)
				if err != nil {
					t.Fatal(err)
				}
				switch op {
				case 0:
					if _, err := r.Cancel(ctx, owner, task.ID, audit); err != nil {
						fail("cancel rejected owner")
					}
					if current.Terminal() && snapshot() != before {
						fail("terminal cancellation was not idempotent")
					}
				case 1:
					key := fmt.Sprintf("synthetic-retry-%d", step)
					_, replayed, err := r.Retry(ctx, owner, task.ID, key, "synthetic")
					if current.Terminal() {
						if err != nil || replayed {
							fail("fresh retry did not create exactly one current attempt")
						}
						lastRetry = key
					} else {
						var typed *domain.Error
						if !errors.As(err, &typed) || typed.Code != "INVALID_STATE" || snapshot() != before {
							fail("active retry mutated state or failed incorrectly")
						}
					}
				case 2:
					if lastRetry != "" {
						if _, replayed, err := r.Retry(ctx, owner, task.ID, lastRetry, "synthetic"); err != nil || !replayed || snapshot() != before {
							fail("retry replay changed persisted state")
						}
					}
				case 3:
					_, readErr := r.Task(ctx, outsider, task.ID)
					_, cancelErr := r.Cancel(ctx, outsider, task.ID, audit)
					_, _, retryErr := r.Retry(ctx, outsider, task.ID, "synthetic-foreign", "synthetic")
					if !errors.Is(readErr, gorm.ErrRecordNotFound) || !errors.Is(cancelErr, gorm.ErrRecordNotFound) || !errors.Is(retryErr, gorm.ErrRecordNotFound) || snapshot() != before {
						fail("foreign owner observed or mutated task")
					}
				case 4:
					replayedTask, _, replayed, err := r.CreateTask(ctx, input, true, audit)
					if err != nil || !replayed || replayedTask.ID != task.ID || snapshot() != before {
						fail("creation replay resurrected or duplicated task")
					}
				case 5, 6:
					err := r.DispatchOne(ctx, func(out domain.Outbox, state domain.Task, attempt domain.Attempt) error {
						latest, err := r.LatestAttempt(ctx, task.ID)
						if err != nil {
							return err
						}
						if attempt.ID != latest.ID || out.AttemptID != latest.ID || state.ID != task.ID {
							fail("stale outbox reached external dispatch")
						}
						if op == 6 {
							return errors.New("synthetic lost acknowledgement")
						}
						return nil
					})
					if err != nil {
						fail("outbox transaction failed")
					}
				}
				attempts, err := r.Attempts(ctx, task.ID)
				if err != nil {
					t.Fatal(err)
				}
				keys := map[string]bool{}
				for index, attempt := range attempts {
					if attempt.Attempt != index+1 || keys[attempt.RawObjectKey] {
						fail("attempt sequence or raw object namespace was reused")
					}
					keys[attempt.RawObjectKey] = true
					if index < len(attempts)-1 && (attempt.Status != "CANCELED" || attempt.RawUploadURL != "" || string(attempt.UploadTargets) != "[]") {
						fail("historical attempt retained execution or upload authority")
					}
					var cancelEvents int64
					if err := db.Model(&domain.Event{}).Where("task_id=? AND attempt_id=? AND type='TASK_CANCELED'", task.ID, attempt.ID).Count(&cancelEvents).Error; err != nil {
						t.Fatal(err)
					}
					if cancelEvents > 1 {
						fail("duplicate terminal event")
					}
				}
				var retries int64
				if err := db.Model(&domain.Event{}).Where("task_id=? AND type='TASK_RETRIED'", task.ID).Count(&retries).Error; err != nil {
					t.Fatal(err)
				}
				if retries != int64(len(attempts)-1) {
					fail("attempt and event were not committed atomically")
				}
			}
		})
	}
}
