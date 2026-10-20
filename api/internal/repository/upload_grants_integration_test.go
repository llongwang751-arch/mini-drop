package repository

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/testutil"
)

func TestPostgresTerminalUploadGrantFencing(t *testing.T) {
	db := testutil.Postgres(t)
	r, ctx := New(db), context.Background()
	owner := domain.User{ID: "log-fixture-owner", Username: "log-fixture-owner", DisplayName: "Imported authorization fixture", Role: "operator"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	agent := domain.Agent{ID: "log-fixture-agent", Hostname: "fixture", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	sequence := 0
	create := func(state string, imported bool) (domain.Task, domain.Attempt) {
		sequence++
		deadline := time.Now().Add(time.Minute)
		task := domain.Task{Name: "Imported grant lifecycle fixture", Kind: "PROC_CPU", OwnerID: owner.ID, AgentID: &agent.ID, CollectionStatus: state, AnalysisStatus: "PENDING", Parameters: []byte(`{}`), DeadlineAt: &deadline, RequestID: "log-fixture", IdempotencyKey: fmt.Sprintf("log-grant-fixture-%d", sequence)}
		if imported {
			task.Kind = "IMPORT_COLLAPSED"
			task.AgentID = nil
		}
		result, attempt, _, err := r.CreateTask(ctx, task, false, domain.Audit{Action: "fixture.create", ResourceType: "task", RequestID: "log-fixture", Detail: []byte(`{}`)})
		if err != nil {
			t.Fatal(err)
		}
		return result, attempt
	}
	grants := func(a domain.Attempt) json.RawMessage {
		prefix := fmt.Sprintf("raw/tasks/%d/attempts/%d/", a.TaskID, a.Attempt)
		value, err := json.Marshal([]map[string]any{
			{"name": "target.elf", "kind": "BINARY", "object_key": prefix + "target.elf", "put_url": "fixture-only-upload-url", "max_file_bytes": 1024},
			{"name": "collection-manifest.json", "kind": "MANIFEST", "object_key": fmt.Sprintf("manifest/tasks/%d/attempts/%d/collection-manifest.json", a.TaskID, a.Attempt), "put_url": "fixture-only-upload-url", "format": "collection_manifest_v1", "content_type": "application/json", "max_file_bytes": 65536},
			{"name": "runner.log", "kind": "LOG", "object_key": prefix + "runner.log", "put_url": "fixture-only-upload-url", "format": "log_jsonl", "content_type": "text/plain", "max_file_bytes": 16384},
		})
		if err != nil {
			t.Fatal(err)
		}
		return value
	}
	seed := func(a domain.Attempt, changes map[string]any) {
		if changes == nil {
			changes = map[string]any{}
		}
		changes["raw_upload_url"] = "fixture-only-raw-upload-url"
		changes["upload_targets"] = string(grants(a))
		if err := db.Model(&domain.Attempt{}).Where("id=?", a.ID).Updates(changes).Error; err != nil {
			t.Fatal(err)
		}
	}
	check := func(id int64, keepLog bool) domain.Attempt {
		var actual domain.Attempt
		if err := db.First(&actual, id).Error; err != nil {
			t.Fatal(err)
		}
		if actual.RawUploadURL != "" || strings.Contains(string(actual.UploadTargets), "put_url") || strings.Contains(string(actual.UploadTargets), "fixture-only") {
			t.Fatal("terminal attempt retained a transport URL")
		}
		if keepLog {
			var stored, expected any
			if json.Unmarshal(actual.UploadTargets, &stored) != nil || json.Unmarshal(TerminalUploadMetadata(actual), &expected) != nil || string(actual.UploadTargets) == "[]" || !reflect.DeepEqual(stored, expected) {
				t.Fatal("current terminal attempt lost its exact bounded LOG authorization")
			}
		} else if string(actual.UploadTargets) != "[]" {
			t.Fatal("revoked attempt retained upload authorization")
		}
		return actual
	}
	t.Run("late presign blocked by an in-flight cancel cannot recreate upload permission", func(t *testing.T) {
		task, attempt := create("CREATED", false)
		seed(attempt, nil)
		tx := db.Begin()
		if tx.Error != nil {
			t.Fatal(tx.Error)
		}
		defer tx.Rollback()
		if _, err := New(tx).Cancel(ctx, owner, task.ID, domain.Audit{Action: "fixture.cancel", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)}); err != nil {
			t.Fatal(err)
		}
		raceCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		completed := make(chan error, 1)
		go func() { completed <- r.SetUploadURL(raceCtx, attempt.ID, "fixture-racing-url") }()
		select {
		case <-completed:
			t.Fatal("presign bypassed the uncommitted attempt lock")
		case <-time.After(50 * time.Millisecond):
		}
		if err := tx.Commit().Error; err != nil {
			t.Fatal(err)
		}
		select {
		case err := <-completed:
			if err == nil {
				t.Fatal("late presign recreated canceled upload URL")
			}
		case <-raceCtx.Done():
			t.Fatal("late presign did not unblock after cancellation commit")
		}
		check(attempt.ID, true)
	})

	t.Run("cancel preserves only current log and retry clears superseded authorization", func(t *testing.T) {
		task, attempt := create("RUNNING", false)
		seed(attempt, nil)
		if _, err := r.Cancel(ctx, owner, task.ID, domain.Audit{Action: "fixture.cancel", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)}); err != nil {
			t.Fatal(err)
		}
		actual := check(attempt.ID, true)
		if actual.FinishedAt == nil || actual.Status != "CANCELED" {
			t.Fatal("cancel did not atomically finish the LOG grace anchor")
		}
		if err := r.SetUploadURL(ctx, attempt.ID, "fixture-late-url"); err == nil {
			t.Fatal("late presign overwrote canceled attempt")
		}
		if err := r.SetUploadTargets(ctx, attempt.ID, grants(attempt)); err == nil {
			t.Fatal("late grants overwrote canceled attempt")
		}
		check(attempt.ID, true)
		if _, _, err := r.Retry(ctx, owner, task.ID, "log-retry-fixture-key", "fixture"); err != nil {
			t.Fatal(err)
		}
		check(attempt.ID, false)
		var current domain.Attempt
		if err := db.Where("task_id=?", task.ID).Order("attempt DESC").First(&current).Error; err != nil {
			t.Fatal(err)
		}
		if current.ID == attempt.ID || current.Attempt != 2 {
			t.Fatal("retry did not create a fenced new attempt")
		}
		if err := r.SetUploadURL(ctx, attempt.ID, "fixture-old-attempt-url"); err == nil {
			t.Fatal("superseded attempt regained a URL")
		}
		if err := r.SetUploadTargets(ctx, attempt.ID, grants(attempt)); err == nil {
			t.Fatal("superseded attempt regained grants")
		}
		if err := r.SetUploadURL(ctx, current.ID, "fixture-current-url"); err != nil {
			t.Fatal(err)
		}
		if err := r.SetUploadTargets(ctx, current.ID, grants(current)); err != nil {
			t.Fatal(err)
		}
		if err := r.CleanupUploadGrants(ctx); err != nil {
			t.Fatal(err)
		}
		if err := db.First(&current, current.ID).Error; err != nil || current.RawUploadURL != "fixture-current-url" {
			t.Fatal("cleanup revoked a live current attempt")
		}
		if _, err := r.Cancel(ctx, owner, task.ID, domain.Audit{Action: "fixture.cancel", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)}); err != nil {
			t.Fatal(err)
		}
	})
	t.Run("cleanup enforces grace and superseded deleted and legacy attempt fencing", func(t *testing.T) {
		_, expired := create("FAILED", false)
		seed(expired, map[string]any{"finished_at": time.Now().Add(-TerminalLogGrace - time.Second)})
		_, fresh := create("FAILED", false)
		seed(fresh, map[string]any{"finished_at": time.Now().Add(-time.Minute)})
		fallbackTask, fallback := create("FAILED", false)
		seed(fallback, nil)
		if err := db.Model(&fallbackTask).Update("collection_deadline", time.Now().Add(-TerminalLogGrace-time.Second)).Error; err != nil {
			t.Fatal(err)
		}
		deletedTask, deleted := create("FAILED", false)
		seed(deleted, map[string]any{"finished_at": time.Now()})
		if err := db.Model(&deletedTask).Update("deleted_at", time.Now()).Error; err != nil {
			t.Fatal(err)
		}
		_, superseded := create("FAILED", false)
		seed(superseded, map[string]any{"finished_at": time.Now()})
		newAttempt := domain.Attempt{TaskID: superseded.TaskID, Attempt: 2, AgentID: &agent.ID, Status: "FAILED", RawObjectKey: domain.RawKey(superseded.TaskID, 2)}
		if err := db.Create(&newAttempt).Error; err != nil {
			t.Fatal(err)
		}
		if err := r.CleanupUploadGrants(ctx); err != nil {
			t.Fatal(err)
		}
		check(expired.ID, false)
		check(fresh.ID, true)
		check(fallback.ID, false)
		check(deleted.ID, false)
		check(superseded.ID, false)
		if err := r.CleanupUploadGrants(ctx); err != nil {
			t.Fatal(err)
		}
		check(fresh.ID, true)
	})
	t.Run("import completion and import timeout never retain Agent grants", func(t *testing.T) {
		task, attempt := create("CREATED", true)
		seed(attempt, nil)
		artifact := domain.Artifact{TaskID: task.ID, AttemptID: attempt.ID, Kind: "RAW", Name: "profile.folded", ObjectKey: attempt.RawObjectKey, Size: 1, SHA256: strings.Repeat("a", 64), ContentType: "text/plain", Status: "ready"}
		if err := r.ImportComplete(ctx, task, artifact); err != nil {
			t.Fatal(err)
		}
		check(attempt.ID, false)
		stale, staleAttempt := create("CREATED", true)
		seed(staleAttempt, nil)
		if err := db.Model(&stale).Update("collection_deadline", time.Now().Add(-time.Minute)).Error; err != nil {
			t.Fatal(err)
		}
		if err := r.SweepStaleImport(ctx); err != nil {
			t.Fatal(err)
		}
		check(staleAttempt.ID, false)
	})
}
