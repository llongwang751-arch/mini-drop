// 本文件验证删除任务撤销公开日志授权，历史 RAW 文件名变化不能扩大或破坏 attempt 级上传范围。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
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

func TestPostgresDeleteRevokesPublicLogUploadAuthorization(t *testing.T) {
	db := testutil.Postgres(t)
	ctx := context.Background()
	owner := domain.User{ID: "deleted-log-owner", Username: "deleted-log-owner", DisplayName: "Imported deletion fixture", Role: "operator"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	agent := domain.Agent{ID: "deleted-log-agent", Hostname: "fixture", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	r := repository.New(db)
	task, attempt, _, err := r.CreateTask(ctx, domain.Task{Name: "Imported terminal LOG fixture", Kind: "PROC_CPU", AgentID: &agent.ID, OwnerID: owner.ID, CollectionStatus: "FAILED", AnalysisStatus: "FAILED", Parameters: []byte(`{}`), RequestID: "fixture", IdempotencyKey: "deleted-log-fixture"}, false, Audit(owner, "fixture.create", "task", "", "fixture"))
	if err != nil {
		t.Fatal(err)
	}
	metadata, _ := json.Marshal([]map[string]any{{"name": "runner.log", "kind": "LOG", "object_key": fmt.Sprintf("raw/tasks/%d/attempts/1/runner.log", task.ID), "format": "log_jsonl", "content_type": "text/plain", "max_file_bytes": 16384, "put_url": "fixture-only-url"}})
	if err := db.Model(&attempt).Updates(map[string]any{"raw_upload_url": "fixture-only-url", "upload_targets": string(metadata), "finished_at": time.Now()}).Error; err != nil {
		t.Fatal(err)
	}
	s := &Tasks{Repo: r}
	if err := s.Delete(ctx, owner, task.ID, "fixture-delete"); err != nil {
		t.Fatal(err)
	}
	if err := db.First(&attempt, attempt.ID).Error; err != nil {
		t.Fatal(err)
	}
	if attempt.RawUploadURL != "" || string(attempt.UploadTargets) != "[]" {
		t.Fatal("soft-deleted task retained a late LOG upload permission or URL")
	}
	var events int64
	if err := db.Model(&domain.Event{}).Where("task_id=? AND type='TASK_DELETED'", task.ID).Count(&events).Error; err != nil || events != 1 {
		t.Fatal("deletion and grant revocation did not persist with the event")
	}
}

func TestPostgresPublicLogGrantUsesAttemptPrefixAcrossCatalogRawNameChange(t *testing.T) {
	db := testutil.Postgres(t)
	ctx := context.Background()
	owner := domain.User{ID: "catalog-log-owner", Username: "catalog-log-owner", DisplayName: "Imported catalog upgrade fixture", Role: "platform_admin"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	agent := domain.Agent{ID: "catalog-log-agent", Hostname: "fixture", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	r := repository.New(db)
	task, attempt, _, err := r.CreateTask(ctx, domain.Task{Name: "Imported RAW basename migration fixture", Kind: "PERF_CPU", AgentID: &agent.ID, OwnerID: owner.ID, CollectionStatus: "CREATED", AnalysisStatus: "PENDING", Parameters: []byte(`{}`), RequestID: "fixture", IdempotencyKey: "catalog-log-fixture"}, false, Audit(owner, "fixture.create", "task", "", "fixture"))
	if err != nil {
		t.Fatal(err)
	}
	kind, ok := domain.Kind("PERF_CPU")
	if !ok {
		t.Fatal("PERF_CPU catalog required")
	}
	if attempt.RawObjectKey != fmt.Sprintf("raw/tasks/%d/attempts/1/%s", task.ID, kind.RawName) {
		t.Fatal("new attempt did not use the authoritative RAW basename")
	}
	// 明确标记的历史导入夹具：旧目录中已排队的 attempt 保留原对象键，
	// 新任务描述则使用当前 RAW 格式，以验证升级不会破坏已有授权。
	attempt.RawObjectKey = domain.RawKey(task.ID, 1)
	if err := db.Model(&attempt).Update("raw_object_key", attempt.RawObjectKey).Error; err != nil {
		t.Fatal(err)
	}
	s := &Tasks{Repo: r, Storage: &fixtureStore{}}
	desc := &pb.TaskDesc{TaskId: task.ID, AttemptId: attempt.ID}
	if err := s.enrichDispatch(ctx, desc, domain.Parameters{}, kind, attempt); err != nil {
		t.Fatal(err)
	}
	assertCollectionUploadTargets(t, desc, task.ID, 1)
	if desc.RawName != kind.RawName || desc.RawFormat != kind.InputFormat {
		t.Fatal("LOG authorization escaped its attempt directory after a catalog RAW name change")
	}
	var stored domain.Attempt
	if err := db.First(&stored, attempt.ID).Error; err != nil {
		t.Fatal(err)
	}
	if string(repository.TerminalUploadMetadata(stored)) == "[]" {
		t.Fatal("upgraded log grant cannot be safely retained at termination")
	}
}
