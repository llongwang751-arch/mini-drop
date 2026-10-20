package service

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
	"time"

	pb "drop/api/gen/dropv1"
	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/testutil"
)

type dispatchRecorder struct{ tasks []*pb.TaskDesc }

func (r *dispatchRecorder) Dispatch(_ context.Context, task *pb.TaskDesc) error {
	r.tasks = append(r.tasks, task)
	return nil
}
func (*dispatchRecorder) Cancel(context.Context, int64, int64, string) error { return nil }
func (*dispatchRecorder) Ready(context.Context) error                        { return nil }

func TestPostgresCollectionPriorityAndPublicLogGrant(t *testing.T) {
	db := testutil.Postgres(t)
	ctx := context.Background()
	owner := domain.User{ID: "priority-owner", Username: "priority-owner", DisplayName: "Imported test fixture", Role: "platform_admin"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	agent := domain.Agent{ID: "priority-agent", Hostname: "fixture", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`["PROC_CPU"]`), Labels: []byte(`{}`), Resources: []byte(`{"processes":[{"pid":7}]}`), LastSeenAt: time.Now()}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	recorder := &dispatchRecorder{}
	svc := &Tasks{Repo: repository.New(db), Storage: &fixtureStore{}, Control: recorder}
	created := []domain.Task{}
	for i, priority := range []int{-10, 10, 10} {
		in := domain.CreateTask{Name: "Explicit priority test fixture", Kind: "PROC_CPU", AgentID: agent.ID, Priority: priority, Parameters: domain.Parameters{PID: 7, DurationSeconds: 1, FrequencyHz: 5}}
		task, _, err := svc.Create(ctx, owner, in, fmt.Sprintf("priority-fixture-%d", i), "priority-fixture")
		if err != nil || task.Priority != priority {
			t.Fatalf("priority was not persisted: %d, %v", task.Priority, err)
		}
		created = append(created, task)
	}
	for range created {
		if err := svc.dispatch(ctx); err != nil {
			t.Fatal(err)
		}
	}
	if len(recorder.tasks) != 3 {
		t.Fatalf("dispatched %d tasks, want 3", len(recorder.tasks))
	}
	for index, expected := range []domain.Task{created[1], created[2], created[0]} {
		actual := recorder.tasks[index]
		if actual.TaskId != expected.ID || actual.Priority != int32(expected.Priority) {
			t.Fatalf("priority/FIFO order mismatch at %d: task %d", index, actual.TaskId)
		}
		assertCollectionUploadTargets(t, actual, expected.ID, 1)
		var attempt domain.Attempt
		if err := db.First(&attempt, actual.AttemptId).Error; err != nil {
			t.Fatal(err)
		}
		var grants []map[string]any
		if err := json.Unmarshal(attempt.UploadTargets, &grants); err != nil || len(grants) != 2 {
			t.Fatal("persisted log grant contract mismatch")
		}
		byName := map[string]map[string]any{}
		for _, grant := range grants {
			byName[grant["name"].(string)] = grant
		}
		if byName["runner.log"]["format"] != "log_jsonl" || byName["runner.log"]["content_type"] != "text/plain" || byName["collection-manifest.json"]["format"] != "collection_manifest_v1" || byName["collection-manifest.json"]["content_type"] != "application/json" {
			t.Fatal("persisted LOG and collection MANIFEST formats or content types changed")
		}
	}
}
