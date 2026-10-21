// 终态栅栏测试使用真实 PostgreSQL 行锁验证撤销/来源变化与提交串行，不调用模型。
// 所有任务和知识均为明确导入夹具；缺少 TEST_DATABASE_URL 时由共用测试工具显式跳过。
package service

import (
	"context"
	"encoding/json"
	"testing"
	"time"

	"gorm.io/gorm"
)

func TestPostgresDiagnosticKnowledgeCommitRejectsRevokedAndExpired(t *testing.T) {
	db, s, u, task, source := diagnosticKnowledgeSuccessfulFixture(t)
	ctx := context.Background()
	c, err := s.CreateDiagnosticCase(ctx, u, task.ID, source.ID, correctionFixtureInput(), "commit-save-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	run := diagnosticKnowledgeNextRun(t, s, u, task, "commit-run-fixture")
	knowledge, err := s.ReadDiagnosticKnowledge(ctx, u, run, json.RawMessage(`{}`), true)
	if err != nil || len(knowledge.Cases) != 1 {
		t.Fatal("夹具未加载来源", err)
	}
	raw, _ := json.Marshal(knowledge)
	trace := []DiagnosisStep{{EvidenceID: "E2", Tool: "read_diagnostic_knowledge", Observation: raw}}
	pin := func() error {
		return db.Transaction(func(tx *gorm.DB) error { return s.PinDiagnosticKnowledge(ctx, tx, run, trace) })
	}
	if err = pin(); err != nil {
		t.Fatal("有效知识无法锁定", err)
	}
	if err = db.Model(&DiagnosticCase{}).Where("id=?", c.ID).Update("expires_at", time.Now().Add(-time.Second)).Error; err != nil {
		t.Fatal(err)
	}
	if err = pin(); err == nil {
		t.Fatal("结束前重读后的过期仍被接受")
	}
	if err = db.Model(&DiagnosticCase{}).Where("id=?", c.ID).Update("expires_at", time.Now().Add(time.Hour)).Error; err != nil {
		t.Fatal(err)
	}
	if err = s.RevokeDiagnosticCase(ctx, u, c.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	if err = pin(); err == nil {
		t.Fatal("结束前重读后的撤销仍被接受")
	}
}

func TestPostgresDiagnosticKnowledgeCommitPinsConcurrentRevocation(t *testing.T) {
	db, s, u, task, source := diagnosticKnowledgeSuccessfulFixture(t)
	ctx := context.Background()
	c, err := s.CreateDiagnosticCase(ctx, u, task.ID, source.ID, correctionFixtureInput(), "pin-save-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	run := diagnosticKnowledgeNextRun(t, s, u, task, "pin-current-fixture")
	knowledge, err := s.ReadDiagnosticKnowledge(ctx, u, run, json.RawMessage(`{}`), true)
	if err != nil || len(knowledge.Cases) != 1 {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(knowledge)
	trace := []DiagnosisStep{{EvidenceID: "E2", Tool: "read_diagnostic_knowledge", Observation: raw}}
	pinned := make(chan struct{})
	release := make(chan struct{})
	pinDone := make(chan error, 1)
	go func() {
		pinDone <- db.Transaction(func(tx *gorm.DB) error {
			if e := s.PinDiagnosticKnowledge(ctx, tx, run, trace); e != nil {
				return e
			}
			close(pinned)
			<-release
			return nil
		})
	}()
	select {
	case <-pinned:
	case e := <-pinDone:
		t.Fatal("锁定失败", e)
	case <-time.After(5 * time.Second):
		t.Fatal("锁定超时")
	}
	revokeDone := make(chan error, 1)
	go func() { revokeDone <- s.RevokeDiagnosticCase(ctx, u, c.ID, "fixture") }()
	select {
	case e := <-revokeDone:
		close(release)
		<-pinDone
		t.Fatal("撤销穿过终态锁", e)
	case <-time.After(150 * time.Millisecond):
	}
	close(release)
	if err = <-pinDone; err != nil {
		t.Fatal(err)
	}
	select {
	case err = <-revokeDone:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("提交释放锁后撤销未完成")
	}
}

func TestPostgresDiagnosticKnowledgeCommitRechecksSourceAndRole(t *testing.T) {
	db, s, u, task, source := diagnosticKnowledgeSuccessfulFixture(t)
	ctx := context.Background()
	if _, err := s.CreateDiagnosticCase(ctx, u, task.ID, source.ID, correctionFixtureInput(), "source-pin-save-fixture", "fixture"); err != nil {
		t.Fatal(err)
	}
	run := diagnosticKnowledgeNextRun(t, s, u, task, "source-pin-run-fixture")
	knowledge, err := s.ReadDiagnosticKnowledge(ctx, u, run, json.RawMessage(`{}`), true)
	if err != nil || len(knowledge.Cases) != 1 {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(knowledge)
	trace := []DiagnosisStep{{EvidenceID: "E2", Tool: "read_diagnostic_knowledge", Observation: raw}}
	pin := func() error {
		return db.Transaction(func(tx *gorm.DB) error { return s.PinDiagnosticKnowledge(ctx, tx, run, trace) })
	}
	if err = db.Exec("UPDATE analysis_jobs SET attempt=attempt+1 WHERE id=?", source.AnalysisJobID).Error; err != nil {
		t.Fatal(err)
	}
	if err = pin(); err == nil {
		t.Fatal("分析身份变化未被终态栅栏发现")
	}
	if err = db.Exec("UPDATE analysis_jobs SET attempt=attempt-1 WHERE id=?", source.AnalysisJobID).Error; err != nil {
		t.Fatal(err)
	}
	if err = db.Model(&u).Update("role", "viewer").Error; err != nil {
		t.Fatal(err)
	}
	if err = pin(); err == nil {
		t.Fatal("角色撤销未被终态栅栏发现")
	}
}
