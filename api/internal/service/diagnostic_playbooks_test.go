// 方法库测试直接调用真实服务方法并使用独立 PostgreSQL schema；不调用模型、采集器或生产服务。
// 并发用例先通过 pg_blocking_pids 确认真实行锁等待，再改变权限或释放锁，避免靠短暂 sleep 猜测调度顺序。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

func playbookTestInput(key string, version int) DiagnosticPlaybookInput {
	return DiagnosticPlaybookInput{LogicalKey: key, Version: version, ExpiresDays: 30, Skill: DiagnosticSkill{
		Title: "明确合成方法夹具", TaskKinds: []string{"PERF_CPU"}, RequiredTools: []string{"inspect_hotspots"},
		Steps: []string{"先检查实际热点"}, CounterEvidence: []string{"高负载可能属于正常工作"},
		StopConditions: []string{"证据不足时停止定因"}, Verification: []string{"相同口径复测并审查业务行为"},
	}}
}

func playbookTestPublish(t *testing.T, s *Suggestions, owner, admin domain.User, in DiagnosticPlaybookInput) DiagnosticPlaybook {
	t.Helper()
	row, err := s.CreateDiagnosticPlaybook(context.Background(), owner, in, "explicit-playbook-fixture")
	if err != nil {
		t.Fatal(err)
	}
	row, err = s.ReviewDiagnosticPlaybook(context.Background(), admin, row.ID, "publish", "explicit-playbook-review")
	if err != nil {
		t.Fatal(err)
	}
	return row
}

func playbookTestSkill(t *testing.T, row DiagnosticPlaybook) DiagnosticSkill {
	t.Helper()
	var skill DiagnosticSkill
	if err := json.Unmarshal(row.Skill, &skill); err != nil {
		t.Fatal(err)
	}
	skill.ID = fmt.Sprint("playbook_", row.ID)
	skill.MatchReasons = []string{"reviewed_playbook", "scope_and_kind_match"}
	return skill
}

func playbookTestPin(ctx context.Context, s *Suggestions, db *gorm.DB, run DiagnosisRun, skills ...DiagnosticSkill) error {
	data, _ := json.Marshal(DiagnosticKnowledge{PriorOnly: true, Skills: skills})
	trace := []DiagnosisStep{{EvidenceID: "E2", Tool: "read_diagnostic_knowledge", Observation: data}}
	return db.WithContext(ctx).Transaction(func(tx *gorm.DB) error { return s.PinDiagnosticKnowledge(ctx, tx, run, trace) })
}

func playbookTestWaitBlocked(t *testing.T, db *gorm.DB, blocker int, done <-chan error) {
	t.Helper()
	deadline := time.Now().Add(4 * time.Second)
	for time.Now().Before(deadline) {
		var waiting bool
		if err := db.Raw("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE ?=ANY(pg_blocking_pids(pid)))", blocker).Scan(&waiting).Error; err != nil {
			t.Fatal(err)
		}
		if waiting {
			return
		}
		select {
		case err := <-done:
			t.Fatal("操作没有等待实际授权行锁", err)
		default:
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("未观察到预期的数据库锁等待")
}

func playbookTestBlockRow(t *testing.T, db *gorm.DB, id int64) (*gorm.DB, int) {
	t.Helper()
	tx := db.Begin()
	t.Cleanup(func() { tx.Rollback() })
	var pid int
	if err := tx.Raw("SELECT pg_backend_pid()").Scan(&pid).Error; err != nil {
		t.Fatal(err)
	}
	if err := tx.Exec("SELECT id FROM diagnostic_playbooks WHERE id=? FOR UPDATE", id).Error; err != nil {
		t.Fatal(err)
	}
	return tx, pid
}

func TestDiagnosticPlaybooksRejectInvalidTextAndDuplicateArrays(t *testing.T) {
	loadDiagnosisCatalog(t)
	if err := validateDiagnosticPlaybook(playbookTestInput("valid-baseline", 1)); err != nil {
		t.Fatal("正样本未通过，不能把契约未加载当成负控有效", err)
	}
	for _, tc := range []struct {
		name   string
		mutate func(*DiagnosticPlaybookInput)
	}{
		{"invalid-utf8", func(in *DiagnosticPlaybookInput) { in.Skill.Title = string([]byte{0xff}) }},
		{"ascii-control", func(in *DiagnosticPlaybookInput) { in.Skill.Steps = []string{"说明\x1b[31m"} }},
		{"unicode-bidi-control", func(in *DiagnosticPlaybookInput) { in.Skill.Title = "说明\u202e反向" }},
		{"whitespace-only", func(in *DiagnosticPlaybookInput) { in.Skill.Title = "   " }},
		{"url", func(in *DiagnosticPlaybookInput) { in.Skill.Verification = []string{"读取 HTTPS://example.test"} }},
		{"duplicate-kind", func(in *DiagnosticPlaybookInput) { in.Skill.TaskKinds = []string{"PERF_CPU", "PERF_CPU"} }},
		{"duplicate-tool", func(in *DiagnosticPlaybookInput) {
			in.Skill.RequiredTools = []string{"inspect_hotspots", "inspect_hotspots"}
		}},
		{"duplicate-step", func(in *DiagnosticPlaybookInput) { in.Skill.Steps = []string{"重复", "重复"} }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			in := playbookTestInput("invalid", 1)
			tc.mutate(&in)
			if err := validateDiagnosticPlaybook(in); err == nil {
				t.Fatal("非法方法输入被接受")
			}
		})
	}
}

func TestPostgresDiagnosticPlaybooksVersionScopeAndPublicationCutoff(t *testing.T) {
	db, s, admin, task := investigationFixture(t)
	ctx := context.Background()
	in := playbookTestInput("same-method", 1)
	v1 := playbookTestPublish(t, s, admin, admin, in)
	in.Version = 2
	in.Skill.TaskKinds = []string{"IO_TRACE"}
	v2 := playbookTestPublish(t, s, admin, admin, in)
	run := requestDiagnosisFixture(t, s, admin, task, "evidence", "playbook-version-fixture")
	match := func(want int64) {
		t.Helper()
		got, err := s.matchedDiagnosticPlaybooks(ctx, admin, task, run)
		if err != nil {
			t.Fatal(err)
		}
		if want == 0 && len(got) != 0 || want != 0 && (len(got) != 1 || got[0].ID != fmt.Sprint("playbook_", want)) {
			t.Fatalf("版本匹配=%v，希望id=%d", got, want)
		}
	}
	match(0) // 最高有效发布版本整体替代旧版；类型不适配时不回退。
	if _, err := s.ReviewDiagnosticPlaybook(ctx, admin, v2.ID, "retire", "fixture"); err != nil {
		t.Fatal(err)
	}
	match(v1.ID)
	if _, err := s.ReviewDiagnosticPlaybook(ctx, admin, v2.ID, "publish", "fixture"); err == nil {
		t.Fatal("退役版本被重新发布")
	}
	// 草稿已存在但申请之后才发布，不得加入这个诊断；其版本也不遮蔽申请前的有效方法。
	in.Version = 3
	in.Skill.TaskKinds = []string{"PERF_CPU"}
	v3, err := s.CreateDiagnosticPlaybook(ctx, admin, in, "late-draft")
	if err != nil {
		t.Fatal(err)
	}
	run = requestDiagnosisFixture(t, s, admin, task, "evidence", "playbook-late-review-fixture")
	v3, err = s.ReviewDiagnosticPlaybook(ctx, admin, v3.ID, "publish", "late-publish")
	if err != nil {
		t.Fatal(err)
	}
	match(v1.ID)
	if err = playbookTestPin(ctx, s, db, run, playbookTestSkill(t, v3)); err == nil {
		t.Fatal("终态接受申请后发布的方法")
	}
	for _, change := range []struct {
		name   string
		mutate func(*DiagnosticPlaybookInput)
	}{
		{"group", func(in *DiagnosticPlaybookInput) { gid := "changed-scope"; in.GroupID = &gid }},
		{"project", func(in *DiagnosticPlaybookInput) { in.ProjectID = "changed-project" }},
	} {
		t.Run(change.name, func(t *testing.T) {
			next := playbookTestInput("same-method", 4)
			change.mutate(&next)
			_, err := s.CreateDiagnosticPlaybook(ctx, admin, next, "scope-conflict")
			if e, ok := err.(*domain.Error); !ok || e.Code != "IDEMPOTENCY_CONFLICT" {
				t.Fatal("跨版本作用域改变未确定拒绝", err)
			}
		})
	}
	// 同内容重试返回已有版本且不改变原有效期；不能借重试复活或延长发布版本。
	again, err := s.CreateDiagnosticPlaybook(ctx, admin, playbookTestInput("same-method", 1), "retry")
	if err != nil || again.ID != v1.ID || !again.ExpiresAt.Equal(v1.ExpiresAt) {
		t.Fatal("幂等重试改变版本或有效期", again, err)
	}
}

func TestPostgresDiagnosticPlaybooksMatchingIsNotTruncatedByListPage(t *testing.T) {
	db, s, admin, task := investigationFixture(t)
	ctx := context.Background()
	// 直接批量登记明确的无关合成版本，用真实 SQL 查询验证列表分页不能吞掉后面的适配项。
	for i := 0; i < 105; i++ {
		in := playbookTestInput(fmt.Sprintf("a-unrelated-%03d", i), 1)
		in.Skill.TaskKinds = []string{"IO_TRACE"}
		skill := in.Skill
		skill.ID = "library_" + in.LogicalKey
		skill.Version = "1"
		raw, _ := json.Marshal(skill)
		now := time.Now().UTC().Add(-time.Second)
		row := DiagnosticPlaybook{OwnerID: admin.ID, LogicalKey: in.LogicalKey, Version: 1, Skill: raw, ContentDigest: "explicit-synthetic-fixture", Status: "published", ReviewerID: &admin.ID, ReviewedAt: &now, ExpiresAt: now.Add(time.Hour), CreatedAt: now}
		if err := db.Create(&row).Error; err != nil {
			t.Fatal(err)
		}
	}
	want := playbookTestPublish(t, s, admin, admin, playbookTestInput("z-applicable", 1))
	run := requestDiagnosisFixture(t, s, admin, task, "evidence", "playbook-pagination-fixture")
	got, err := s.matchedDiagnosticPlaybooks(ctx, admin, task, run)
	if err != nil || len(got) != 1 || got[0].ID != fmt.Sprint("playbook_", want.ID) {
		t.Fatal("列表前100条吞掉有效方法", got, err)
	}
}

func TestPostgresDiagnosticPlaybooksReviewRechecksRoleAfterBookLock(t *testing.T) {
	db, s, admin, _ := investigationFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	row, err := s.CreateDiagnosticPlaybook(ctx, admin, playbookTestInput("review-role", 1), "fixture")
	if err != nil {
		t.Fatal(err)
	}
	blocker, pid := playbookTestBlockRow(t, db, row.ID)
	done := make(chan error, 1)
	go func() { _, err := s.ReviewDiagnosticPlaybook(ctx, admin, row.ID, "publish", "fixture"); done <- err }()
	playbookTestWaitBlocked(t, db, pid, done)
	if err := db.Model(&domain.User{}).Where("id=?", admin.ID).Update("role", "operator").Error; err != nil {
		t.Fatal(err)
	}
	if err := blocker.Commit().Error; err != nil {
		t.Fatal(err)
	}
	if err := <-done; err == nil {
		t.Fatal("等锁期间被降权的旧管理员仍可发布")
	}
	var fresh DiagnosticPlaybook
	if err := db.First(&fresh, row.ID).Error; err != nil {
		t.Fatal(err)
	}
	if fresh.Status != "draft" || fresh.ReviewedAt != nil {
		t.Fatal("失败发布仍修改草稿", fresh.Status)
	}
}

func TestPostgresDiagnosticPlaybooksPinsMethodsWithoutCases(t *testing.T) {
	for _, operation := range []string{"retire", "membership", "user-role"} {
		t.Run(operation, func(t *testing.T) {
			db, s, admin, task := investigationFixture(t)
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			reader := domain.User{ID: "playbook-reader", Username: "playbook-reader", Role: "operator"}
			if err := db.Create(&reader).Error; err != nil {
				t.Fatal(err)
			}
			gid := "method-team"
			if err := db.Exec("INSERT INTO groups(id,name) VALUES(?,?)", gid, gid).Error; err != nil {
				t.Fatal(err)
			}
			if err := db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES(?,?,'operator')", gid, reader.ID).Error; err != nil {
				t.Fatal(err)
			}
			in := playbookTestInput("team-method", 1)
			in.GroupID = &gid
			row := playbookTestPublish(t, s, admin, admin, in)
			var requestedAt time.Time
			if err := db.Raw("SELECT clock_timestamp()").Scan(&requestedAt).Error; err != nil {
				t.Fatal(err)
			}
			run := DiagnosisRun{TaskID: task.ID, RequestedBy: reader.ID, CreatedAt: requestedAt}
			skill := playbookTestSkill(t, row)
			// 只测方法接口的授权锁，不把此手工 run 当作完整诊断生命周期验收。
			matched, err := s.matchedDiagnosticPlaybooks(ctx, reader, task, run)
			if err != nil || len(matched) != 1 {
				t.Fatal("团队方法不可见", matched, err)
			}
			data, _ := json.Marshal(DiagnosticKnowledge{PriorOnly: true, Skills: []DiagnosticSkill{skill}})
			trace := []DiagnosisStep{{EvidenceID: "E2", Tool: "read_diagnostic_knowledge", Observation: data}}
			pin := db.WithContext(ctx).Begin()
			defer pin.Rollback()
			var pid int
			if err := pin.Raw("SELECT pg_backend_pid()").Scan(&pid).Error; err != nil {
				t.Fatal(err)
			}
			if err := s.PinDiagnosticKnowledge(ctx, pin, run, trace); err != nil {
				t.Fatal(err)
			}
			done := make(chan error, 1)
			go func() {
				var err error
				switch operation {
				case "retire":
					_, err = s.ReviewDiagnosticPlaybook(ctx, admin, row.ID, "retire", "fixture")
				case "membership":
					err = db.WithContext(ctx).Exec("DELETE FROM group_members WHERE group_id=? AND user_id=?", gid, reader.ID).Error
				case "user-role":
					err = db.WithContext(ctx).Model(&domain.User{}).Where("id=?", reader.ID).Update("role", "viewer").Error
				}
				done <- err
			}()
			playbookTestWaitBlocked(t, db, pid, done)
			if err := pin.Commit().Error; err != nil {
				t.Fatal(err)
			}
			if err := <-done; err != nil {
				t.Fatal(err)
			}
			if err := playbookTestPin(ctx, s, db, run, skill); err == nil {
				t.Fatal("撤销之后新终态仍接受方法")
			}
			if operation == "membership" {
				got, err := s.matchedDiagnosticPlaybooks(ctx, reader, task, run)
				if err != nil || len(got) != 0 {
					t.Fatal("跨用户团队撤权后仍匹配", got, err)
				}
			}
		})
	}
}

func TestPostgresDiagnosticPlaybooksExpiryRecheckedAfterLockWait(t *testing.T) {
	db, s, admin, task := investigationFixture(t)
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	row := playbookTestPublish(t, s, admin, admin, playbookTestInput("expiry-wait", 1))
	run := requestDiagnosisFixture(t, s, admin, task, "evidence", "playbook-expiry-fixture")
	// 先提交短有效期，再持有不更新内容的行锁，让“WHERE 时间条件等锁前已真”的实现暴露出来。
	if err := db.Exec("UPDATE diagnostic_playbooks SET expires_at=clock_timestamp()+interval '400 milliseconds' WHERE id=?", row.ID).Error; err != nil {
		t.Fatal(err)
	}
	var expiry time.Time
	if err := db.Model(&DiagnosticPlaybook{}).Where("id=?", row.ID).Pluck("expires_at", &expiry).Error; err != nil {
		t.Fatal(err)
	}
	blocker, pid := playbookTestBlockRow(t, db, row.ID)
	done := make(chan error, 1)
	skill := playbookTestSkill(t, row)
	go func() { done <- playbookTestPin(ctx, s, db, run, skill) }()
	playbookTestWaitBlocked(t, db, pid, done)
	for {
		var expired bool
		if err := db.Raw("SELECT clock_timestamp()>?", expiry).Scan(&expired).Error; err != nil {
			t.Fatal(err)
		}
		if expired {
			break
		}
		if ctx.Err() != nil {
			t.Fatal(ctx.Err())
		}
		time.Sleep(10 * time.Millisecond)
	}
	if err := blocker.Commit().Error; err != nil {
		t.Fatal(err)
	}
	if err := <-done; err == nil {
		t.Fatal("锁等待后已过期的方法仍被接受")
	}
	if _, err := s.ReviewDiagnosticPlaybook(ctx, admin, row.ID, "publish", "expired-retry"); err == nil {
		t.Fatal("幂等发布绕过有效期")
	}
	got, err := s.matchedDiagnosticPlaybooks(ctx, admin, task, run)
	if err != nil || len(got) != 0 {
		t.Fatal("过期方法仍参与匹配", got, err)
	}
}

func TestPostgresDiagnosticPlaybooksPrivateOwnershipAndToolScope(t *testing.T) {
	db, s, admin, task := investigationFixture(t)
	ctx := context.Background()
	other := domain.User{ID: "unrelated-owner", Username: "unrelated-owner", Role: "operator"}
	if err := db.Create(&other).Error; err != nil {
		t.Fatal(err)
	}
	private := playbookTestPublish(t, s, other, admin, playbookTestInput("private-method", 1))
	in := playbookTestInput("requires-telemetry", 1)
	in.Skill.RequiredTools = []string{"query_business_signals"}
	_ = playbookTestPublish(t, s, admin, admin, in)
	run := requestDiagnosisFixture(t, s, admin, task, "evidence", "playbook-owner-fixture")
	got, err := s.matchedDiagnosticPlaybooks(ctx, admin, task, run)
	if err != nil || len(got) != 0 {
		t.Fatal("管理员诊断混入他人私有方法或无来源工具方法", got, err)
	}
	if err := playbookTestPin(ctx, s, db, run, playbookTestSkill(t, private)); err == nil {
		t.Fatal("终态混入他人私有方法")
	}
	run.TelemetryServiceID = "explicit-test-context"
	got, err = s.matchedDiagnosticPlaybooks(ctx, admin, task, run)
	if err != nil || len(got) != 1 || !strings.HasPrefix(got[0].ID, "playbook_") {
		t.Fatal("已绑定遥测的方法不匹配", got, err)
	}
}

func TestPostgresDiagnosticPlaybooksPublishThenRequestUsesDatabaseClock(t *testing.T) {
	_, s, admin, task := investigationFixture(t)
	ctx := context.Background()
	// 不插入等待：真实连续发布/申请必须保持先后关系，不能依靠sleep掩盖Go与PG时钟量化差异。
	for version := 1; version <= 12; version++ {
		row := playbookTestPublish(t, s, admin, admin, playbookTestInput("immediate-publish", version))
		run := requestDiagnosisFixture(t, s, admin, task, "evidence", fmt.Sprintf("immediate-publish-%d", version))
		if row.ReviewedAt == nil || run.CreatedAt.Before(*row.ReviewedAt) {
			t.Fatalf("发布后申请的时钟倒置：review=%v request=%v", row.ReviewedAt, run.CreatedAt)
		}
		got, err := s.matchedDiagnosticPlaybooks(ctx, admin, task, run)
		if err != nil || len(got) != 1 || got[0].ID != fmt.Sprint("playbook_", row.ID) {
			t.Fatal("立即申请没有匹配最新已发布方法", version, got, err)
		}
		if err := s.CancelDiagnosis(ctx, admin, task.ID, run.ID, "explicit-clock-fixture"); err != nil {
			t.Fatal(err)
		}
	}
}
