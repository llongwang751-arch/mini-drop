// 本文件验证二进制制品对象键与保留策略的匹配，路由字节为导入夹具，不是性能采样。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package service

import (
	"context"
	"encoding/base64"
	"fmt"
	"testing"
	"time"

	pb "drop/api/gen/dropv1"
	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/testutil"
)

func TestPostgresBinaryObjectKeysMatchNinetyDayRetention(t *testing.T) {
	db := testutil.Postgres(t)
	ctx := context.Background()
	owner := domain.User{ID: "binary-retention-owner", Username: "binary-retention-owner", DisplayName: "Imported storage routing fixture", Role: "platform_admin"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	store, repo := &fixtureStore{}, repository.New(db)
	svc := &Tasks{Repo: repo, Storage: store}
	// 明确导入的路由字节夹具，用于校验制品路径与保留策略，不是采集器或解析器验收。
	input := &domain.ImportInput{Filename: "bolt.fdata", Format: "bolt_fdata", DataBase64: base64.StdEncoding.EncodeToString([]byte("no_lbr\n")), Auxiliary: []domain.ImportFile{{Filename: "target.elf", Format: "elf", DataBase64: base64.StdEncoding.EncodeToString([]byte("\x7fELF imported storage routing fixture"))}}}
	task, _, err := svc.Create(ctx, owner, domain.CreateTask{Name: "Imported binary retention fixture", Kind: "BOLT_PROFILE", Import: input}, "binary-retention-import", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	artifacts, err := repo.Artifacts(ctx, task.ID)
	if err != nil {
		t.Fatal(err)
	}
	var found bool
	for _, artifact := range artifacts {
		if artifact.Kind != "BINARY" {
			continue
		}
		found = true
		want := fmt.Sprintf("binary/tasks/%d/attempts/1/target.elf", task.ID)
		if artifact.ObjectKey != want || len(store.objects[want]) == 0 {
			t.Errorf("binary stored below a shorter-lived prefix: %s", artifact.ObjectKey)
		}
		if artifact.ExpiresAt == nil || artifact.ExpiresAt.Sub(artifact.CreatedAt) < 90*24*time.Hour-time.Second {
			t.Error("binary retention is not ninety days")
		}
	}
	if !found {
		t.Fatal("imported binary attachment was not persisted")
	}
	grantTask, attempt, _, err := repo.CreateTask(ctx, domain.Task{Name: "Imported collection grant routing fixture", Kind: "BOLT_PROFILE", OwnerID: owner.ID, CollectionStatus: "CREATED", AnalysisStatus: "PENDING", Parameters: []byte(`{}`), RequestID: "fixture", IdempotencyKey: "binary-retention-grant"}, false, domain.Audit{Action: "fixture.create", ResourceType: "task", RequestID: "fixture", Detail: []byte(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	kind, ok := domain.Kind("BOLT_PROFILE")
	if !ok {
		t.Fatal("BOLT catalog is missing")
	}
	desc := &pb.TaskDesc{TaskId: grantTask.ID, AttemptId: attempt.ID}
	if err := svc.enrichDispatch(ctx, desc, domain.Parameters{}, kind, attempt); err != nil {
		t.Fatal(err)
	}
	found = false
	for _, target := range desc.UploadTargets {
		if target.Kind == "BINARY" {
			found = true
			if target.ObjectKey != fmt.Sprintf("binary/tasks/%d/attempts/1/target.elf", grantTask.ID) {
				t.Error("collection binary PUT grant uses a shorter-lived prefix")
			}
		}
	}
	if !found {
		t.Fatal("collection binary PUT grant is missing")
	}
}
