// 本文件用真实 PostgreSQL 验证追加分支的取消、过期与撤权清理。
// 性能结果均为明确导入的协议夹具；不执行采集器、外部模型或生产请求。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

type followupCancelFixture struct {
	db             *gorm.DB
	s              *Suggestions
	u              domain.User
	root           Investigation
	baseline       domain.Task
	collecting     domain.Task
	diagnosing     domain.Task
	run            DiagnosisRun
	rows           []InvestigationFollowup
	collectAttempt domain.Attempt
	lease          string
}

func newFollowupCancelFixture(t *testing.T, rootStatus string) followupCancelFixture {
	t.Helper()
	db, s, u, baseline := investigationFixture(t)
	ctx := context.Background()
	source := requestDiagnosisFixture(t, s, u, baseline, "evidence", "followup-cancel-source")
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	root := Investigation{RequestedBy: u.ID, AgentID: *baseline.AgentID, Goal: "明确导入补证取消夹具", Parameters: baseline.Parameters, Consent: true,
		RequestKey: "followup-cancel-root", InputDigest: repository.Digest("followup-cancel-root"), Status: rootStatus, Trace: json.RawMessage(`[]`), BaselineTaskID: &baseline.ID, DiagnosisID: &source.ID}
	if err := db.Create(&root).Error; err != nil {
		t.Fatal(err)
	}
	var parameters domain.Parameters
	if err := json.Unmarshal(baseline.Parameters, &parameters); err != nil {
		t.Fatal(err)
	}
	create := func(name string) domain.Task {
		t.Helper()
		task, _, err := s.Tasks.Create(ctx, u, domain.CreateTask{Name: name, Kind: baseline.Kind, AgentID: *baseline.AgentID, Parameters: parameters, Visibility: "private"}, "followup-cancel-"+name, "fixture")
		if err != nil {
			t.Fatal(err)
		}
		return task
	}
	collecting, diagnosing := create("collecting"), create("diagnosing")
	if err := db.Model(&collecting).Updates(map[string]any{"collection_status": "RUNNING", "analysis_status": "PENDING"}).Error; err != nil {
		t.Fatal(err)
	}
	attempt, err := s.Repo.LatestAttempt(ctx, collecting.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&attempt).Updates(map[string]any{"status": "RUNNING", "started_at": time.Now().Add(-time.Second), "raw_upload_url": "fixture-upload-grant-not-a-real-url"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,attempt,lease_owner,lease_expires_at) VALUES(?,?,'folded_v1','running',1,'fixture-analyzer',now()+interval '5 minutes')", collecting.ID, attempt.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&diagnosing).Updates(map[string]any{"collection_status": "COLLECTED", "analysis_status": "SUCCESS"}).Error; err != nil {
		t.Fatal(err)
	}
	diagnosisAttempt, err := s.Repo.LatestAttempt(ctx, diagnosing.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,attempt,result) SELECT ?,?,'folded_v1','success',1,result FROM analysis_jobs WHERE task_id=?", diagnosing.ID, diagnosisAttempt.ID, baseline.ID).Error; err != nil {
		t.Fatal(err)
	}
	run := requestDiagnosisFixture(t, s, u, diagnosing, "evidence", "followup-cancel-child-diagnosis")
	lease := "followup-cancel-old-worker"
	if err := db.Model(&run).Updates(map[string]any{"status": "running", "lease_owner": lease, "lease_expires_at": time.Now().Add(5 * time.Minute), "attempts": 1}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.First(&run, run.ID).Error; err != nil {
		t.Fatal(err)
	}
	rows := []InvestigationFollowup{
		{InvestigationID: root.ID, ParentTaskID: baseline.ID, SourceDiagnosisID: source.ID, Plan: json.RawMessage(`{}`), PlanDigest: repository.Digest("collecting"), Status: "collecting", TaskID: &collecting.ID},
		{InvestigationID: root.ID, ParentTaskID: baseline.ID, SourceDiagnosisID: source.ID, Plan: json.RawMessage(`{}`), PlanDigest: repository.Digest("diagnosing"), Status: "diagnosing", TaskID: &diagnosing.ID, DiagnosisID: &run.ID},
	}
	for n := range rows {
		if err := db.Create(&rows[n]).Error; err != nil {
			t.Fatal(err)
		}
	}
	return followupCancelFixture{db, s, u, root, baseline, collecting, diagnosing, run, rows, attempt, lease}
}

func (f followupCancelFixture) assertCleaned(t *testing.T, status, code string) {
	t.Helper()
	var collecting, complete domain.Task
	if err := f.db.First(&collecting, f.collecting.ID).Error; err != nil {
		t.Fatal(err)
	}
	if collecting.CollectionStatus != "CANCELED" || collecting.AnalysisStatus != "CANCELED" {
		t.Fatal("linked collecting task still active", collecting.CollectionStatus, collecting.AnalysisStatus)
	}
	if err := f.db.First(&complete, f.diagnosing.ID).Error; err != nil {
		t.Fatal(err)
	}
	if complete.CollectionStatus != "COLLECTED" || complete.AnalysisStatus != "SUCCESS" {
		t.Fatal("completed measurement was overwritten", complete.CollectionStatus, complete.AnalysisStatus)
	}
	var attempt domain.Attempt
	if err := f.db.First(&attempt, f.collectAttempt.ID).Error; err != nil {
		t.Fatal(err)
	}
	if attempt.Status != "CANCELED" || attempt.FinishedAt == nil || attempt.RawUploadURL != "" {
		t.Fatal("active attempt/upload grant not retired")
	}
	var job struct {
		Status     string
		LeaseOwner *string
		ErrorCode  *string
	}
	if err := f.db.Table("analysis_jobs").Where("task_id=?", f.collecting.ID).First(&job).Error; err != nil {
		t.Fatal(err)
	}
	if job.Status != "failed" || job.LeaseOwner != nil || job.ErrorCode == nil || *job.ErrorCode != "TASK_CANCELED" {
		t.Fatal("analyzer lease not retired", job)
	}
	var run DiagnosisRun
	if err := f.db.First(&run, f.run.ID).Error; err != nil {
		t.Fatal(err)
	}
	if run.Status != "failed" || run.LeaseOwner != nil || run.LeaseExpiresAt != nil || run.ErrorCode == nil || *run.ErrorCode != code {
		t.Fatal("diagnosis lease not fenced", run.Status, run.ErrorCode)
	}
	var rows []InvestigationFollowup
	if err := f.db.Where("investigation_id=?", f.root.ID).Order("id").Find(&rows).Error; err != nil {
		t.Fatal(err)
	}
	for _, row := range rows {
		if row.Status != status || row.ErrorCode == nil || *row.ErrorCode != code {
			t.Fatal("branch terminal does not match cleanup", row.Status, row.ErrorCode)
		}
	}
	for _, table := range []string{"task_events", "outbox"} {
		q := f.db.Table(table).Where("task_id=?", f.collecting.ID)
		if table == "task_events" {
			q = q.Where("type='TASK_CANCELED'")
		} else {
			q = q.Where("event_type='CANCEL'")
		}
		var count int64
		if err := q.Count(&count).Error; err != nil || count != 1 {
			t.Fatal("cleanup did not produce exactly one normal cancellation effect", table, count, err)
		}
	}
	var baseline domain.Task
	if err := f.db.First(&baseline, f.baseline.ID).Error; err != nil {
		t.Fatal(err)
	}
	if baseline.CollectionStatus != "COLLECTED" || baseline.AnalysisStatus != "SUCCESS" {
		t.Fatal("baseline result was overwritten")
	}
}

func TestPostgresFollowupCancelSuccessfulRootIsIdempotentAndPreservesPublicSuccess(t *testing.T) {
	f := newFollowupCancelFixture(t, "success")
	ctx := context.Background()
	before, err := f.s.Repo.Overview(ctx, f.u)
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	errors := make(chan error, 4)
	for n := 0; n < 4; n++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			errors <- f.s.CancelInvestigation(ctx, f.u, f.root.ID, "followup-cancel-replay")
		}()
	}
	wg.Wait()
	close(errors)
	for err := range errors {
		if err != nil {
			t.Fatal(err)
		}
	}
	f.assertCleaned(t, "canceled", "DIAGNOSIS_CANCELED")
	var root Investigation
	if err := f.db.First(&root, f.root.ID).Error; err != nil || root.Status != "success" {
		t.Fatal("completed root terminal was rewritten", root.Status, err)
	}
	after, err := f.s.Repo.Overview(ctx, f.u)
	if err != nil {
		t.Fatal(err)
	}
	if after.TasksSuccess != before.TasksSuccess || after.AnalysisCounts["SUCCESS"] != before.AnalysisCounts["SUCCESS"] || after.TasksRunning != 0 || after.CollectionCounts["CANCELED"] != 1 {
		t.Fatal("public counters lost completed facts", before, after)
	}
	var audits int64
	if err := f.db.Table("audit_logs").Where("action='investigation.followup_cleanup' AND resource_type='investigation'").Count(&audits).Error; err != nil || audits != 1 {
		t.Fatal("idempotent cleanup duplicated root audit", audits, err)
	}
	// 已撤销租约的旧协程即使完成计算，也不能追加工具轨迹或重新写成功报告。
	trace := []DiagnosisStep{{EvidenceID: "E1", Tool: "inspect_hotspots", Observation: json.RawMessage(`{}`)}}
	if err := f.s.persistDiagnosisStep(ctx, f.run, f.lease, trace); err == nil {
		t.Fatal("late tool result crossed canceled child lease")
	}
	if err := f.s.finishDiagnosis(ctx, f.run, f.lease, trace, &DiagnosisReport{Summary: "late synthetic answer must not win"}, nil); err != nil {
		t.Fatal(err)
	}
	var run DiagnosisRun
	if err := f.db.First(&run, f.run.ID).Error; err != nil {
		t.Fatal(err)
	}
	if run.Status != "failed" || len(run.Report) != 0 || strings.Contains(string(run.Trace), "inspect_hotspots") {
		t.Fatal("late worker replaced canceled child result")
	}
	var completed int64
	if err := f.db.Model(&domain.Event{}).Where("task_id=? AND type='DIAGNOSIS_COMPLETED'", f.diagnosing.ID).Count(&completed).Error; err != nil || completed != 0 {
		t.Fatal("late completion emitted a success event", completed, err)
	}
}

func TestPostgresFollowupCancelWorkerCleansTerminalExpiredAndRevokedRoots(t *testing.T) {
	for _, scenario := range []string{"failed", "canceled", "expired", "agent_revoked", "viewer_downgraded"} {
		t.Run(scenario, func(t *testing.T) {
			f := newFollowupCancelFixture(t, "success")
			wantStatus, wantCode := "canceled", "DIAGNOSIS_CANCELED"
			switch scenario {
			case "failed", "canceled":
				if err := f.db.Model(&f.root).Update("status", scenario).Error; err != nil {
					t.Fatal(err)
				}
			case "expired":
				wantStatus, wantCode = "failed", "DIAGNOSIS_TIMEOUT"
				if err := f.db.Model(&f.root).Update("created_at", time.Now().Add(-21*time.Minute)).Error; err != nil {
					t.Fatal(err)
				}
			case "agent_revoked", "viewer_downgraded":
				wantStatus, wantCode = "failed", "AUTHORIZATION_REVOKED"
				role := "operator"
				if scenario == "viewer_downgraded" {
					role = "viewer"
				}
				if err := f.db.Model(&domain.User{}).Where("id=?", f.u.ID).Update("role", role).Error; err != nil {
					t.Fatal(err)
				}
				if scenario == "viewer_downgraded" {
					// 故意保留组内operator与Agent operate授权，只撤销全局写角色，防止把Agent可见误当成可继续采集。
					if err := f.db.Exec("INSERT INTO groups(id,name) VALUES('followup-retained-agent','followup-retained-agent')").Error; err != nil {
						t.Fatal(err)
					}
					if err := f.db.Exec("INSERT INTO group_members(group_id,user_id,role) VALUES('followup-retained-agent',?,'operator')", f.u.ID).Error; err != nil {
						t.Fatal(err)
					}
					if err := f.db.Exec("INSERT INTO group_agents(group_id,agent_id,permission) VALUES('followup-retained-agent',?,'operate')", f.root.AgentID).Error; err != nil {
						t.Fatal(err)
					}
				}
			}
			if err := f.s.advanceInvestigationFollowups(context.Background()); err != nil {
				t.Fatal(err)
			}
			if err := f.s.advanceInvestigationFollowups(context.Background()); err != nil {
				t.Fatal(err)
			}
			f.assertCleaned(t, wantStatus, wantCode)
		})
	}
}

func TestPostgresFollowupCancelRejectsCrossOwnerTaskAtomically(t *testing.T) {
	f := newFollowupCancelFixture(t, "success")
	other := domain.User{ID: "foreign-followup-owner", Username: "foreign-followup-owner", Role: "operator"}
	if err := f.db.Create(&other).Error; err != nil {
		t.Fatal(err)
	}
	foreign, _, _, err := f.s.Repo.CreateTask(context.Background(), domain.Task{Name: "explicit foreign imported cancellation fixture", Kind: "IMPORT_COLLAPSED", OwnerID: other.ID, Parameters: json.RawMessage(`{}`), CollectionStatus: "CREATED", AnalysisStatus: "PENDING", IdempotencyKey: "foreign-followup-task", RequestID: "fixture"}, false, Audit(other, "fixture.create", "task", "0", "fixture"))
	if err != nil {
		t.Fatal(err)
	}
	if err := f.db.Model(&InvestigationFollowup{}).Where("id=?", f.rows[1].ID).Update("task_id", foreign.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := f.s.CancelInvestigation(context.Background(), f.u, f.root.ID, "cross-owner-fixture"); err == nil {
		t.Fatal("cross-owner branch accepted")
	}
	var own, actual domain.Task
	if err := f.db.First(&actual, foreign.ID).Error; err != nil || actual.CollectionStatus != "CREATED" {
		t.Fatal("another owner's task was canceled", err)
	}
	if err := f.db.First(&own, f.collecting.ID).Error; err != nil || own.CollectionStatus != "RUNNING" {
		t.Fatal("partial cleanup committed before detecting malformed owner", err)
	}
	var cancellations int64
	if err := f.db.Table("outbox").Where("event_type='CANCEL'").Count(&cancellations).Error; err != nil || cancellations != 0 {
		t.Fatal("rejected transaction emitted cancellation side effects", cancellations, err)
	}
}

func TestPostgresFollowupCancelLeavesSuccessfulBranchUntouched(t *testing.T) {
	f := newFollowupCancelFixture(t, "success")
	if err := f.db.Model(&InvestigationFollowup{}).Where("id=?", f.rows[1].ID).Update("status", "success").Error; err != nil {
		t.Fatal(err)
	}
	if err := f.db.Model(&DiagnosisRun{}).Where("id=?", f.run.ID).Updates(map[string]any{"status": "success", "lease_owner": nil, "lease_expires_at": nil, "report": `{"summary":"explicit completed imported fixture"}`}).Error; err != nil {
		t.Fatal(err)
	}
	if err := f.s.CancelInvestigation(context.Background(), f.u, f.root.ID, "preserve-successful-child"); err != nil {
		t.Fatal(err)
	}
	var row InvestigationFollowup
	if err := f.db.First(&row, f.rows[1].ID).Error; err != nil || row.Status != "success" || row.ErrorCode != nil {
		t.Fatal("successful followup was rewritten", row.Status, err)
	}
	var run DiagnosisRun
	if err := f.db.First(&run, f.run.ID).Error; err != nil || run.Status != "success" || !strings.Contains(string(run.Report), "explicit completed imported fixture") {
		t.Fatal("successful diagnosis was rewritten", err)
	}
}

func TestPostgresFollowupCancelVerificationLockPrecedesTasks(t *testing.T) {
	f := newFollowupCancelFixture(t, "verifying")
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	verification := VerificationRun{DiagnosisID: *f.root.DiagnosisID, BaselineTaskID: f.baseline.ID, CandidateTaskID: f.diagnosing.ID, RequestedBy: f.u.ID, Status: "waiting"}
	if err := f.db.Create(&verification).Error; err != nil {
		t.Fatal(err)
	}
	if err := f.db.Model(&f.root).Update("verification_id", verification.ID).Error; err != nil {
		t.Fatal(err)
	}
	blocker := f.db.WithContext(ctx).Begin()
	defer blocker.Rollback()
	if err := blocker.Exec("SELECT id FROM verification_runs WHERE id=? FOR UPDATE", verification.ID).Error; err != nil {
		t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- f.s.CancelInvestigation(ctx, f.u, f.root.ID, "verification-cancel-lock-order") }()
	// 按活动语句识别本次唯一调查取消事务是否在等verification锁；不凭sleep推测锁顺序。
	waiting := false
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if err := f.db.Raw("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND wait_event_type='Lock' AND query LIKE '%verification_runs%')").Scan(&waiting).Error; err != nil {
			t.Fatal(err)
		}
		if waiting {
			break
		}
		select {
		case err := <-done:
			t.Fatal("cancel did not wait for verification", err)
		default:
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !waiting {
		t.Fatal("cancel did not reach verification lock")
	}
	probe := f.db.WithContext(ctx).Begin()
	if err := probe.Exec("SELECT id FROM tasks WHERE id IN (?,?,?) ORDER BY id FOR UPDATE NOWAIT", f.baseline.ID, f.collecting.ID, f.diagnosing.ID).Error; err != nil {
		probe.Rollback()
		t.Fatal("cancel occupied tasks before verification, reversing worker lock order", err)
	}
	if err := probe.Rollback().Error; err != nil {
		t.Fatal(err)
	}
	if err := blocker.Commit().Error; err != nil {
		t.Fatal(err)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-ctx.Done():
		t.Fatal(fmt.Errorf("cancellation failed to resume: %w", ctx.Err()))
	}
}
