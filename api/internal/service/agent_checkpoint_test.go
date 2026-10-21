// 明确导入的持久化协议夹具；真实 PostgreSQL 事务验证计数、租约、游标与不确定副作用边界，不调用模型。
package service

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

func checkpointFixture(t *testing.T) (*gorm.DB, *AgentCheckpointStore, DiagnosisRun, *string) {
	t.Helper()
	db, s, u, task := diagnosisFixture(t)
	run := requestDiagnosisFixture(t, s, u, task, "evidence", "checkpoint-imported-fixture")
	owner := "checkpoint-owner"
	if err := db.Model(&DiagnosisRun{}).Where("id=?", run.ID).Updates(map[string]any{"status": "running", "lease_owner": owner, "lease_expires_at": time.Now().Add(time.Hour)}).Error; err != nil {
		t.Fatal(err)
	}
	store := NewAgentCheckpointStore(s.Repo, AgentCheckpointBinding{"diagnosis", run.ID, run.InputDigest}, func(ctx context.Context, tx *gorm.DB) error {
		_, err := s.diagnosisFence(ctx, tx, run, owner)
		return err
	})
	return db, store, run, &owner
}

func importedCheckpointState() json.RawMessage {
	return json.RawMessage(`{"context":[{"id":"imported-message","name":"user","role":"user","content":[{"type":"text","text":"Explicit imported checkpoint fixture"}]}],"cursor":"model","pending_text":"","seen_ids":[]}`)
}

func TestAgentCheckpointProjectionRejectsHiddenAndControlFields(t *testing.T) {
	if !validAgentCheckpointSDK(importedCheckpointState()) {
		t.Fatal("合法可见投影被拒绝")
	}
	for _, raw := range []string{
		strings.Replace(string(importedCheckpointState()), `"type":"text","text":"Explicit imported checkpoint fixture"`, `"type":"thinking","text":"hidden"`, 1),
		strings.Replace(string(importedCheckpointState()), `"cursor":"model"`, `"cursor":"model","capability":"not-persistable"`, 1),
		strings.Replace(string(importedCheckpointState()), `"role":"user"`, `"role":"system"`, 1),
		string(importedCheckpointState()) + ` {}`,
	} {
		if validAgentCheckpointSDK(json.RawMessage(raw)) {
			t.Fatal("不安全状态获准写入")
		}
	}
}

func TestPostgresAgentCheckpointBudgetReceiptsAndLeaseRecovery(t *testing.T) {
	db, store, run, owner := checkpointFixture(t)
	ctx := context.Background()
	budget := agentBudget{5, 7, 2, 150000}
	spec := strings.Repeat("b", 64)
	snapshot, err := store.load(ctx, spec, budget)
	if err != nil || snapshot.ModelCalls != 0 {
		t.Fatal(err)
	}
	if err = store.saveSDK(ctx, 1, importedCheckpointState()); err != nil {
		t.Fatal(err)
	}
	if err = store.reserveModel(ctx, 1, budget, "diagnosis"); err != nil {
		t.Fatal(err)
	}
	if err = store.endModel(ctx, 1, json.RawMessage(`{"input_tokens":10,"output_tokens":5}`)); err != nil {
		t.Fatal(err)
	}
	call := agentCallback{Type: "tool", Round: 1, CallID: "imported-durable-tool", Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)}
	if err = store.beginTool(ctx, call, budget); err != nil {
		t.Fatal(err)
	}
	response := map[string]any{"ok": true, "observation": json.RawMessage(`{"evidence_id":"E2","source":"imported"}`)}
	if err = store.completeTool(ctx, call, response); err != nil {
		t.Fatal(err)
	}
	// 模拟新进程、新租约领取；账本不依赖旧进程的 callback cache 或 capability。
	*owner = "checkpoint-next-owner"
	if err = db.Model(&DiagnosisRun{}).Where("id=?", run.ID).Update("lease_owner", *owner).Error; err != nil {
		t.Fatal(err)
	}
	resumed := NewAgentCheckpointStore(store.Repo, store.Binding, store.Fence)
	snapshot, err = resumed.load(ctx, spec, budget)
	if err != nil || snapshot.Sequence != 1 || snapshot.ModelCalls != 1 || snapshot.ToolCalls != 1 || snapshot.RoundTools != 1 || len(snapshot.CompletedCallIDs) != 1 {
		t.Fatal("恢复重置了账本", snapshot, err)
	}
	if string(snapshot.Usage) != `{"input_tokens": 10, "output_tokens": 5}` && string(snapshot.Usage) != `{"input_tokens":10,"output_tokens":5}` {
		var usage map[string]int
		json.Unmarshal(snapshot.Usage, &usage)
		if usage["input_tokens"] != 10 || usage["output_tokens"] != 5 {
			t.Fatal("真实用量丢失")
		}
	}
	replay, found, err := resumed.replay(ctx, call)
	if err != nil || !found || !json.Valid(replay) {
		t.Fatal("完成回执不可重用", err)
	}
	call.Arguments = json.RawMessage(`{"rank":2}`)
	if _, _, err = resumed.replay(ctx, call); safeAgentError(err) != "AGENT_CALLBACK_REPLAY" {
		t.Fatal("更改参数复用了回执", err)
	}
	if err = resumed.saveSDK(ctx, 1, importedCheckpointState()); safeAgentError(err) != "AGENT_CHECKPOINT_STALE" {
		t.Fatal("旧游标覆盖了状态", err)
	}
	if _, err = resumed.load(ctx, strings.Repeat("c", 64), budget); safeAgentError(err) != "INPUT_CHANGED" {
		t.Fatal("新模型输入继承旧检查点", err)
	}
	if err = db.Model(&DiagnosisRun{}).Where("id=?", run.ID).Update("lease_owner", "new-unrelated-worker").Error; err != nil {
		t.Fatal(err)
	}
	if err = resumed.saveSDK(ctx, 2, importedCheckpointState()); err == nil {
		t.Fatal("过期worker提交了游标")
	}
}

func TestPostgresAgentCheckpointPendingSideEffectNeverBlindlyReplayed(t *testing.T) {
	_, store, _, _ := checkpointFixture(t)
	ctx := context.Background()
	budget, _ := phaseBudget("diagnosis")
	spec := strings.Repeat("b", 64)
	if _, err := store.load(ctx, spec, budget); err != nil {
		t.Fatal(err)
	}
	if err := store.reserveModel(ctx, 1, budget, "diagnosis"); err != nil {
		t.Fatal(err)
	}
	call := agentCallback{Type: "tool", Round: 1, CallID: "unconfirmed-imported-tool", Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)}
	if err := store.beginTool(ctx, call, budget); err != nil {
		t.Fatal(err)
	}
	if _, err := store.load(ctx, spec, budget); safeAgentError(err) != "AGENT_CHECKPOINT_AMBIGUOUS" {
		t.Fatal("未确认动作在新进程被自动重放", err)
	}
	if _, _, err := store.replay(ctx, call); safeAgentError(err) != "AGENT_CHECKPOINT_AMBIGUOUS" {
		t.Fatal("pending 被当成成功回执", err)
	}
}

func TestPostgresAgentCheckpointActiveTimeIsReservedBeforeWorkAndNotRefunded(t *testing.T) {
	_, store, _, _ := checkpointFixture(t)
	ctx := context.Background()
	budget, _ := phaseBudget("diagnosis")
	spec := strings.Repeat("b", 64)
	if _, err := store.load(ctx, spec, budget); err != nil {
		t.Fatal(err)
	}
	for _, want := range []int64{5000, 5000, 1000} {
		granted, err := store.reserveActive(ctx, 11000)
		if err != nil || granted != want {
			t.Fatal(granted, err)
		}
	}
	resumed := NewAgentCheckpointStore(store.Repo, store.Binding, store.Fence)
	snapshot, err := resumed.load(ctx, spec, budget)
	if err != nil || snapshot.ActiveMS != 11000 {
		t.Fatal("崩溃未使用时间片被退款", err)
	}
	if _, err = resumed.reserveActive(ctx, 11000); safeAgentError(err) != "AI_BUDGET_EXCEEDED" {
		t.Fatal("耗尽后又获得免费执行时间", err)
	}
}

func TestPostgresAgentCheckpointAcceptedResultPreservesNumericLexemes(t *testing.T) {
	_, store, _, _ := checkpointFixture(t)
	ctx := context.Background()
	budget, _ := phaseBudget("diagnosis")
	spec := strings.Repeat("b", 64)
	if _, err := store.load(ctx, spec, budget); err != nil {
		t.Fatal(err)
	}
	// JSONB 会规范化裸数值，所以 accepted 必须作为字符串保存，而不能直接嵌入 JSONB 对象。
	accepted := json.RawMessage(`{"report_version":2,"facts":[{"value":0.000001},{"value":1e-6},{"value":-0},{"value":0.12345678901234567890123456789},{"value":90071992547409931234567890123456789}],"summary":"` + strings.Repeat("x", 24576) + `"}`)
	if err := store.finish(ctx, accepted, false); err != nil {
		t.Fatal(err)
	}
	resumed := NewAgentCheckpointStore(store.Repo, store.Binding, store.Fence)
	snapshot, err := resumed.load(ctx, spec, budget)
	if err != nil || string(snapshot.Accepted) != string(accepted) || snapshot.AcceptedJSON != string(accepted) {
		t.Fatal("JSONB 恢复改变已接受结果的数值词法", err)
	}
}

func TestPostgresAgentCheckpointWatchdogCancelsAndRestartCannotRefundTime(t *testing.T) {
	_, store, _, _ := checkpointFixture(t)
	requests := 0
	runner := &AgentRunner{sessions: map[string]*agentSession{}, client: &http.Client{Transport: fixtureRoundTripper(func(request *http.Request) (*http.Response, error) {
		requests++
		<-request.Context().Done()
		return nil, request.Context().Err()
	})}}
	budget := agentBudget{5, 7, 2, 350}
	spec := agentRunSpec{Phase: "diagnosis", Model: "imported-fixture", Tools: diagnosisDefinitions(), Checkpoint: store, Budget: &budget, Invoke: func(context.Context, string, json.RawMessage) (agentToolResult, error) {
		t.Fatal("超时夹具不应执行工具")
		return agentToolResult{}, nil
	}, Finish: func(context.Context, string) (json.RawMessage, error) {
		t.Fatal("超时夹具不应验收结果")
		return nil, nil
	}}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if _, _, err := runner.Run(ctx, spec); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("未在已预付时间片截止时取消", err)
	}
	if _, _, err := runner.Run(ctx, spec); safeAgentError(err) != "AI_BUDGET_EXCEEDED" {
		t.Fatal("新会话退还了上一会话预付预算", err)
	}
	if requests != 1 {
		t.Fatal("耗尽后仍发送了上游请求", requests)
	}
}

func TestPostgresAgentCheckpointPermissionAndAnalysisFence(t *testing.T) {
	db, store, run, _ := checkpointFixture(t)
	ctx := context.Background()
	budget, _ := phaseBudget("diagnosis")
	spec := strings.Repeat("b", 64)
	if _, err := store.load(ctx, spec, budget); err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("UPDATE analysis_jobs SET attempt=attempt+1 WHERE id=?", run.AnalysisJobID).Error; err != nil {
		t.Fatal(err)
	}
	if err := store.saveSDK(ctx, 1, importedCheckpointState()); err == nil {
		t.Fatal("过期分析身份提交检查点")
	}
	if err := db.Exec("UPDATE analysis_jobs SET attempt=attempt-1 WHERE id=?", run.AnalysisJobID).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&domain.User{}).Where("id=?", run.RequestedBy).Update("role", "viewer").Error; err != nil {
		t.Fatal(err)
	}
	if _, err := store.load(ctx, spec, budget); safeAgentError(err) != "AUTHORIZATION_REVOKED" {
		t.Fatal("撤权后可恢复", err)
	}
}
