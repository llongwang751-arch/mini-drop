// 本文件验证采集上传清单的有限授权，以及终止后的授权撤销和固定诊断文件边界。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	pb "drop/api/gen/dropv1"
	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/testutil"
)

// 固定诊断制品与各采集器 RAW 文件名相互独立，
// 不能借此将 Agent 授权扩大到任务前缀下的任意附件。
func assertCollectionUploadTargets(t *testing.T, desc *pb.TaskDesc, taskID int64, attempt int) {
	t.Helper()
	if len(desc.UploadTargets) != 2 {
		t.Fatal("expected fixed LOG and collection MANIFEST task-scoped grants")
	}
	seen := map[string]bool{}
	for _, target := range desc.UploadTargets {
		if seen[target.Name] {
			t.Fatal("duplicate task-scoped upload target")
		}
		seen[target.Name] = true
		prefix := "raw"
		if target.Kind == "MANIFEST" {
			prefix = "manifest"
		}
		if target.ObjectKey != fmt.Sprintf("%s/tasks/%d/attempts/%d/%s", prefix, taskID, attempt, target.Name) || target.PutUrl == "" {
			t.Fatal("diagnostic upload target escaped its current attempt or lacks its temporary URL")
		}
		switch target.Name {
		case "runner.log":
			if target.Kind != "LOG" || target.MaxFileBytes != 16384 {
				t.Fatal("public LOG grant identity or bound changed")
			}
		case "collection-manifest.json":
			if target.Kind != "MANIFEST" || target.MaxFileBytes != 65536 {
				t.Fatal("collection MANIFEST grant identity or bound changed")
			}
		default:
			t.Fatal("unexpected task-scoped diagnostic attachment")
		}
	}
}

func TestPostgresCollectionManifestGrantIsBoundedAndRevokedAtTermination(t *testing.T) {
	db := testutil.Postgres(t)
	ctx := context.Background()
	owner := domain.User{ID: "manifest-owner", Username: "manifest-owner", DisplayName: "Imported manifest authorization fixture", Role: "platform_admin"}
	if err := db.Create(&owner).Error; err != nil {
		t.Fatal(err)
	}
	agent := domain.Agent{ID: "manifest-agent", Hostname: "fixture", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`["PROC_CPU"]`), Labels: []byte(`{}`), Resources: []byte(`{"processes":[{"pid":7}]}`), LastSeenAt: time.Now()}
	if err := db.Create(&agent).Error; err != nil {
		t.Fatal(err)
	}
	recorder := &dispatchRecorder{}
	repo := repository.New(db)
	svc := &Tasks{Repo: repo, Storage: &fixtureStore{}, Control: recorder}
	task, _, err := svc.Create(ctx, owner, domain.CreateTask{Name: "Imported collection grant fixture", Kind: "PROC_CPU", AgentID: agent.ID, Parameters: domain.Parameters{PID: 7, DurationSeconds: 1, FrequencyHz: 5}}, "collection-manifest-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	if err := svc.dispatch(ctx); err != nil {
		t.Fatal(err)
	}
	if len(recorder.tasks) != 1 {
		t.Fatal("collection fixture was not dispatched")
	}
	desc := recorder.tasks[0]
	assertCollectionUploadTargets(t, desc, task.ID, 1)
	var attempt domain.Attempt
	if err := db.First(&attempt, desc.AttemptId).Error; err != nil {
		t.Fatal(err)
	}
	var grants []map[string]any
	if err := json.Unmarshal(attempt.UploadTargets, &grants); err != nil {
		t.Fatal(err)
	}
	var manifest map[string]any
	for _, grant := range grants {
		if grant["name"] == "collection-manifest.json" {
			manifest = grant
		}
	}
	if len(manifest) != 7 || manifest["kind"] != "MANIFEST" || manifest["format"] != "collection_manifest_v1" || manifest["content_type"] != "application/json" || manifest["max_file_bytes"] != float64(65536) || manifest["object_key"] != fmt.Sprintf("manifest/tasks/%d/attempts/1/collection-manifest.json", task.ID) || manifest["put_url"] == "" {
		t.Fatal("persisted collection MANIFEST grant is not exact, canonical and bounded")
	}
	if _, err := svc.Cancel(ctx, owner, task.ID, "manifest-cancel"); err != nil {
		t.Fatal(err)
	}
	if err := db.First(&attempt, desc.AttemptId).Error; err != nil {
		t.Fatal(err)
	}
	grants = nil
	if err := json.Unmarshal(attempt.UploadTargets, &grants); err != nil || len(grants) != 1 || len(grants[0]) != 6 || grants[0]["kind"] != "LOG" || grants[0]["name"] != "runner.log" || attempt.RawUploadURL != "" || strings.Contains(string(attempt.UploadTargets), "put_url") || strings.Contains(string(attempt.UploadTargets), "MANIFEST") {
		t.Fatal("terminal attempt retained MANIFEST authorization or lost its bounded URL-free LOG grant")
	}
}
