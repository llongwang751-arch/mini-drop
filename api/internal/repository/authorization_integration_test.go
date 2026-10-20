// 本文件验证资源、共享、组和跨 API 实例限流在真实 PostgreSQL 中采用一致授权范围。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package repository

import (
	"context"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/testutil"
	"gorm.io/gorm"
)

func TestPostgresResourceAuthorizationAndSharedLimit(t *testing.T) {
	db := testutil.Postgres(t)
	r := New(db)
	ctx := context.Background()
	owner := domain.User{ID: "owner", Username: "owner", DisplayName: "Owner", Role: "operator"}
	reader := domain.User{ID: "reader", Username: "reader", DisplayName: "Reader", Role: "viewer"}
	operator := domain.User{ID: "operator", Username: "operator", DisplayName: "Operator", Role: "operator"}
	stranger := domain.User{ID: "stranger", Username: "stranger", DisplayName: "Stranger", Role: "operator"}
	admin := domain.User{ID: "admin", Username: "admin", DisplayName: "Admin", Role: "platform_admin"}
	for _, u := range []domain.User{owner, reader, operator, stranger, admin} {
		if err := db.Create(&u).Error; err != nil {
			t.Fatal(err)
		}
	}
	g := domain.Group{ID: "g", Name: "Authorized"}
	db.Create(&g)
	db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES('g','reader','viewer'),('g','operator','operator')")
	aid := "a"
	db.Create(&domain.Agent{ID: aid, Hostname: "a", Version: "test", Platform: "linux", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`), Status: "online"})
	db.Exec("INSERT INTO group_agents(group_id,agent_id,permission) VALUES('g','a','operate')")
	gid := "g"
	task := domain.Task{Name: "authorization", Kind: "PROC_CPU", OwnerID: owner.ID, GroupID: &gid, Visibility: "group", AgentID: &aid, CollectionStatus: "CREATED", AnalysisStatus: "PENDING", Parameters: []byte(`{}`), RequestID: "test", IdempotencyKey: "auth-task-key"}
	task, _, _, err := r.CreateTask(ctx, task, true, domain.Audit{Action: "test", ResourceType: "task", RequestID: "test", Detail: []byte(`{}`)})
	if err != nil {
		t.Fatal(err)
	}
	t.Run("group read operate and stranger isolation", func(t *testing.T) {
		for _, u := range []domain.User{owner, reader, operator, admin} {
			if _, err := r.Task(ctx, u, task.ID); err != nil {
				t.Fatal(u.ID, err)
			}
		}
		if _, err := r.Task(ctx, stranger, task.ID); err == nil {
			t.Fatal("stranger read permitted")
		}
		if _, err := r.AuthorizeTaskWrite(ctx, reader, task.ID); err == nil {
			t.Fatal("viewer operate permitted")
		}
		if _, err := r.AuthorizeTaskWrite(ctx, operator, task.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := r.AuthorizedAgent(ctx, reader, aid, true); err == nil {
			t.Fatal("viewer agent operation permitted")
		}
		if _, err := r.AuthorizedAgent(ctx, operator, aid, true); err != nil {
			t.Fatal(err)
		}
		a, err := r.VisibleAgents(ctx, stranger)
		if err != nil || len(a) != 0 {
			t.Fatal(a, err)
		}
	})
	t.Run("private shares do not escalate read to operate", func(t *testing.T) {
		db.Model(&task).Update("visibility", "private")
		if _, err := r.Task(ctx, operator, task.ID); err == nil {
			t.Fatal("private group task leaked")
		}
		uid := stranger.ID
		if _, err := r.CreateShare(ctx, owner, task.ID, &uid, nil, "read"); err != nil {
			t.Fatal(err)
		}
		if _, err := r.Task(ctx, stranger, task.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := r.AuthorizeTaskWrite(ctx, stranger, task.ID); err == nil {
			t.Fatal("read share escalated")
		}
		db.Exec("UPDATE task_shares SET permission='operate' WHERE task_id=?", task.ID)
		if _, err := r.AuthorizeTaskWrite(ctx, stranger, task.ID); err != nil {
			t.Fatal(err)
		}
		if _, err := r.CreateShare(ctx, stranger, task.ID, &uid, nil, "operate"); err == nil {
			t.Fatal("shared operator managed sharing")
		}
		db.Exec("DELETE FROM task_shares WHERE task_id=?", task.ID)
		if _, err := r.Task(ctx, stranger, task.ID); err == nil {
			t.Fatal("revocation ineffective")
		}
	})
	t.Run("shared limiter accepts exactly ten concurrent requests", func(t *testing.T) {
		var accepted atomic.Int32
		var wg sync.WaitGroup
		errs := make(chan error, 40)
		for i := 0; i < 40; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				ok, err := New(db.Session(&gorm.Session{})).AllowRate(ctx, "shared-limit", 10, time.Hour)
				if ok {
					accepted.Add(1)
				}
				errs <- err
			}()
		}
		wg.Wait()
		close(errs)
		for err := range errs {
			if err != nil {
				t.Fatal(err)
			}
		}
		if accepted.Load() != 10 {
			t.Fatal(fmt.Sprint(accepted.Load()))
		}
		var n int64
		db.Table("rate_limits").Count(&n)
		if n != 1 {
			t.Fatal(n)
		}
	})
	t.Run("group administrator can delegate only existing Agent rights", func(t *testing.T) {
		manager := domain.User{ID: "manager", Username: "manager", DisplayName: "Manager", Role: "group_admin"}
		db.Create(&manager)
		db.Create(&domain.Group{ID: "h", Name: "Delegated"})
		db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES('g','manager','group_admin'),('h','manager','group_admin')")
		hidden := domain.Agent{ID: "hidden", Hostname: "hidden", Version: "test", Platform: "linux", Status: "online", Capabilities: []byte(`[]`), Labels: []byte(`{}`), Resources: []byte(`{}`)}
		db.Create(&hidden)
		if err := r.PutGroupAgent(ctx, manager, "h", aid, "operate"); err != nil {
			t.Fatal(err)
		}
		if err := r.PutGroupAgent(ctx, manager, "h", "hidden", "read"); err == nil {
			t.Fatal("unknown Agent right delegated")
		}
		db.Exec("DELETE FROM group_agents WHERE group_id='h'")
		db.Exec("UPDATE group_agents SET permission='read' WHERE group_id='g'")
		if err := r.PutGroupAgent(ctx, manager, "h", aid, "operate"); err == nil {
			t.Fatal("read permission escalated to operate")
		}
		if err := r.PutGroupAgent(ctx, manager, "h", aid, "read"); err != nil {
			t.Fatal(err)
		}
	})
	t.Run("different shared operators cannot bypass owner retry quota", func(t *testing.T) {
		db.Model(&task).Updates(map[string]any{"collection_status": "FAILED", "analysis_status": "FAILED"})
		ids := []int64{}
		for i := 0; i < 12; i++ {
			input := domain.Task{Name: "Shared retry quota fixture", Kind: "PROC_CPU", AgentID: &aid, OwnerID: owner.ID, Visibility: "private", Parameters: []byte(`{}`), CollectionStatus: "CANCELED", AnalysisStatus: "CANCELED", RequestID: "fixture", IdempotencyKey: fmt.Sprintf("shared-retry-%d", i)}
			created, _, _, err := r.CreateTask(ctx, input, false, domain.Audit{Action: "test", ResourceType: "task", RequestID: "test", Detail: []byte(`{}`)})
			if err != nil {
				t.Fatal(err)
			}
			uid := stranger.ID
			if _, err := r.CreateShare(ctx, owner, created.ID, &uid, nil, "operate"); err != nil {
				t.Fatal(err)
			}
			ids = append(ids, created.ID)
		}
		var accepted atomic.Int32
		var wg sync.WaitGroup
		errs := make(chan error, 12)
		for i, id := range ids {
			wg.Add(1)
			go func(i int, id int64) {
				defer wg.Done()
				_, _, err := r.Retry(ctx, stranger, id, fmt.Sprintf("shared-retry-action-%d", i), "fixture")
				if err == nil {
					accepted.Add(1)
				} else if de, ok := err.(*domain.Error); !ok || de.Code != "QUOTA_EXCEEDED" {
					errs <- err
				}
			}(i, id)
		}
		wg.Wait()
		close(errs)
		for err := range errs {
			t.Fatal(err)
		}
		if accepted.Load() != 10 {
			t.Fatal(accepted.Load())
		}
	})
}
