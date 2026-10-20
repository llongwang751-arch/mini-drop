// 本文件验证AgentScope 协议与 PostgreSQL 持久化状态的联动，涵盖诊断、人工确认规划和源码修复验收。
// 模型与工具数据为导入夹具；数据库事务真实执行，真实 SDK 互通另有独立验收。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
)

func importedToolStep(t *testing.T, runner *AgentRunner, run importedAgentRequest, round int, id, name, args string) map[string]json.RawMessage {
	t.Helper()
	_, response := importedAgentCallback(t, runner, run, agentCallback{Type: "tool", Round: round, CallID: id, Name: name, Arguments: json.RawMessage(args)})
	if string(response["ok"]) != "true" {
		t.Fatal("imported fixture tool rejected", response)
	}
	return response
}

func TestPostgresAgentScopeDiagnosisAutomaticVerification(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	ctx := context.Background()
	s.Config = AIConfig{AgentBackend: "agentscope", Model: "imported-scope-fixture"}
	s.Runner = importedAgentRunner(t, func(runner *AgentRunner, _ *http.Request, run importedAgentRequest) (json.RawMessage, error) {
		if run.Phase != "diagnosis" || run.Budget.MaxToolCalls != 7 {
			t.Fatal("wrong diagnosis boundary")
		}
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 1, CallID: "s1"})
		importedToolStep(t, runner, run, 1, "t1", "inspect_hotspots", `{}`)
		importedToolStep(t, runner, run, 1, "t2", "check_collection_quality", `{}`)
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 2, CallID: "s2"})
		importedToolStep(t, runner, run, 2, "t3", "propose_verification", `{}`)
		tools := 3
		if strings.Contains(run.Messages[0].Content, `"verification_available":true`) {
			importedToolStep(t, runner, run, 2, "t4", "compare_profiles", `{}`)
			tools++
		}
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 3, CallID: "s3"})
		content := `{"summary":"显式导入协议fixture，未执行真实模型。","hypotheses":[{"claim":"待现场验证的fixture假设","evidence_ids":["E1","E2"],"verification":"同目标同配置受控复测并记录业务指标"}],"limitations":["仅协议fixture"]}`
		_, accepted := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: 3, CallID: "finish", Content: content})
		if string(accepted["accepted"]) != "true" {
			t.Fatal(accepted)
		}
		return importedAgentAnswer(run, accepted["result"], 3, tools), nil
	})
	baseline := requestDiagnosisFixture(t, s, u, task, "model", "scope-baseline-imported-fixture")
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	if err := db.First(&baseline, baseline.ID).Error; err != nil {
		t.Fatal(err)
	}
	if baseline.Status != "success" {
		t.Fatal(baseline.Status, baseline.ErrorCode)
	}
	var baselineReport DiagnosisReport
	_ = json.Unmarshal(baseline.Report, &baselineReport)
	if baselineReport.AgentRuntime == nil || baselineReport.AgentRuntime.ToolCalls != 3 || baselineReport.Plan == nil {
		t.Fatal("missing Go-accepted runtime or verification plan")
	}
	candidate, _, err := s.CollectDiagnosis(ctx, u, task.ID, baseline.ID, "scope-verification-imported-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	attempt, err := s.Repo.LatestAttempt(ctx, candidate.ID)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&domain.Task{}).Where("id=?", candidate.ID).Updates(map[string]any{"collection_status": "COLLECTED", "analysis_status": "SUCCESS"}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status,attempt,result) SELECT ?,?,'folded_v1','success',1,result FROM analysis_jobs WHERE task_id=?", candidate.ID, attempt.ID, task.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := s.processVerification(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.processDiagnosis(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.processVerification(ctx); err != nil {
		t.Fatal(err)
	}
	var verification VerificationRun
	db.First(&verification)
	if verification.Status != "success" || verification.CandidateDiagnosisID == nil {
		t.Fatal("scope verification lifecycle failed", verification.Status, verification.ErrorCode)
	}
	var checked DiagnosisRun
	db.First(&checked, *verification.CandidateDiagnosisID)
	var candidateReport DiagnosisReport
	_ = json.Unmarshal(checked.Report, &candidateReport)
	if candidateReport.AgentRuntime == nil || candidateReport.AgentRuntime.RunID == baselineReport.AgentRuntime.RunID || candidateReport.AgentRuntime.ToolCalls != 4 || !strings.Contains(string(checked.Trace), "compare_profiles") {
		t.Fatal("candidate lacked its own actual comparison/runtime")
	}
}

func TestPostgresAgentScopePlanningRepairsPlanBeforeConfirmation(t *testing.T) {
	db, s, u, task := investigationFixture(t)
	s.Config = AIConfig{AgentBackend: "agentscope", Model: "imported-scope-fixture"}
	s.Runner = importedAgentRunner(t, func(runner *AgentRunner, _ *http.Request, run importedAgentRequest) (json.RawMessage, error) {
		if run.Phase != "planning" {
			t.Fatal(run.Phase)
		}
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 1, CallID: "s1"})
		importedToolStep(t, runner, run, 1, "t1", "list_collectors", `{}`)
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 2, CallID: "s2"})
		invalid := importedToolStep(t, runner, run, 2, "t2", "propose_collection", `{"kind":"PERF_CPU","duration_seconds":4,"frequency_hz":999,"rationale":"显式导入无效计划fixture"}`)
		if len(invalid["terminal"]) != 0 {
			t.Fatal("invalid plan accepted")
		}
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 3, CallID: "s3"})
		valid := importedToolStep(t, runner, run, 3, "t3", "propose_collection", `{"kind":"PERF_CPU","duration_seconds":4,"frequency_hz":49,"rationale":"显式导入有效计划fixture"}`)
		if string(valid["terminal"]) != "true" {
			t.Fatal("validated plan not terminal")
		}
		return importedAgentAnswer(run, valid["result"], 3, 3), nil
	})
	in := InvestigationInput{Goal: "导入协议fixture规划", AgentID: *task.AgentID, Parameters: domain.Parameters{PID: 7}, Consent: true}
	run, err := s.RequestInvestigation(context.Background(), u, in, "scope-plan-imported-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.planNextInvestigation(context.Background()); err != nil {
		t.Fatal(err)
	}
	db.First(&run, run.ID)
	if run.Status != "awaiting_confirmation" {
		code := ""
		if run.ErrorCode != nil {
			code = *run.ErrorCode
		}
		t.Fatal(run.Status, code)
	}
	var trace []DiagnosisStep
	_ = json.Unmarshal(run.Trace, &trace)
	if len(trace) != 4 || trace[3].Tool != "agent_runtime" {
		t.Fatal("planning proof was not durable")
	}
	var runtime AgentRuntime
	_ = json.Unmarshal(trace[3].Observation, &runtime)
	if runtime.Framework != "agentscope" || runtime.ToolCalls != 3 {
		t.Fatal(runtime)
	}
	var count int64
	db.Model(&domain.Task{}).Count(&count)
	if count != 1 {
		t.Fatal("planning collected before confirmation")
	}
	if err := s.ConfirmInvestigation(context.Background(), u, run.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	if err := s.advanceInvestigations(context.Background()); err != nil {
		t.Fatal(err)
	}
	db.First(&run, run.ID)
	if run.BaselineTaskID == nil || run.Status != "collecting" {
		t.Fatal("accepted scope plan not executed after confirmation")
	}
}

func TestPostgresAgentScopePlanningCancellationAndFreshAuthorization(t *testing.T) {
	for _, scenario := range []string{"cancel", "revoke"} {
		t.Run(scenario, func(t *testing.T) {
			db, s, u, task := investigationFixture(t)
			s.Config = AIConfig{AgentBackend: "agentscope", Model: "imported-scope-fixture"}
			started := make(chan struct{}, 1)
			s.Runner = importedAgentRunner(t, func(runner *AgentRunner, request *http.Request, run importedAgentRequest) (json.RawMessage, error) {
				importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 1, CallID: "s1"})
				started <- struct{}{}
				<-request.Context().Done()
				return nil, request.Context().Err()
			})
			run, err := s.RequestInvestigation(context.Background(), u, InvestigationInput{Goal: "显式取消fixture", AgentID: *task.AgentID, Parameters: domain.Parameters{PID: 7}, Consent: true}, "scope-cancel-"+scenario, "fixture")
			if err != nil {
				t.Fatal(err)
			}
			finished := make(chan error, 1)
			go func() { finished <- s.planNextInvestigation(context.Background()) }()
			select {
			case <-started:
			case err := <-finished:
				db.First(&run, run.ID)
				code := ""
				if run.ErrorCode != nil {
					code = *run.ErrorCode
				}
				t.Fatal("planning exited before imported provider started", err, run.Status, code)
			case <-time.After(5 * time.Second):
				t.Fatal("planning did not reach imported provider within bound")
			}
			if scenario == "cancel" {
				if err := s.CancelInvestigation(context.Background(), u, run.ID, "fixture"); err != nil {
					t.Fatal(err)
				}
			} else {
				if err := db.Model(&domain.User{}).Where("id=?", u.ID).Update("role", "viewer").Error; err != nil {
					t.Fatal(err)
				}
			}
			select {
			case err := <-finished:
				if err != nil {
					t.Fatal(err)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("domain cancellation did not stop scope model")
			}
			db.First(&run, run.ID)
			if scenario == "cancel" && run.Status != "canceled" {
				t.Fatal("worker overwrote cancellation")
			}
			if scenario == "revoke" && (run.Status != "failed" || run.ErrorCode == nil || *run.ErrorCode != "AUTHORIZATION_REVOKED") {
				t.Fatal("captured administrator survived role revocation", run.Status, run.ErrorCode)
			}
		})
	}
}

func TestPostgresAgentScopeRepairRequiresMeasuredSourceLineAndAcceptedTest(t *testing.T) {
	db, s, u, _ := investigationFixture(t)
	s.Config = AIConfig{AgentBackend: "agentscope", Model: "imported-scope-fixture"}
	revision := strings.Repeat("a", 64)
	var tests atomic.Int32
	s.Code = &CodeExecutor{client: &http.Client{Transport: fixtureRoundTripper(func(request *http.Request) (*http.Response, error) {
		var input struct {
			Action      string `json:"action"`
			ExecutionID string `json:"execution_id"`
		}
		_ = json.NewDecoder(request.Body).Decode(&input)
		if input.Action != "describe" && input.ExecutionID == "" {
			t.Fatal("unscoped executor action")
		}
		var observation any
		switch input.Action {
		case "describe":
			observation = map[string]any{"id": "crm-python", "source_revision": revision, "editable_files": []string{"palette.py"}}
		case "profile_business_flow":
			observation = map[string]any{"success": true, "hotspots": []any{map[string]any{"path": "palette.py", "line": 31}}}
		case "read_source":
			observation = map[string]any{"path": "palette.py", "sha256": revision, "content": "imported fixture source"}
		case "test_patch":
			tests.Add(1)
			observation = map[string]any{"success": true, "accepted": true, "functional_regression": "passed", "root_cause_status": "validated_in_controlled_crm_benchmark"}
		default:
			t.Fatal(input.Action)
		}
		body, _ := json.Marshal(map[string]any{"data": observation})
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(string(body))), Header: make(http.Header)}, nil
	})}}
	s.Runner = importedAgentRunner(t, func(runner *AgentRunner, _ *http.Request, run importedAgentRequest) (json.RawMessage, error) {
		for round, name := range []string{"profile_business_flow", "read_source", "test_patch"} {
			index := round + 1
			importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: index, CallID: fmt.Sprintf("s%d", index)})
			args := `{}`
			if name == "read_source" {
				args = `{"path":"palette.py","start_line":1,"end_line":32}`
			}
			if name == "test_patch" {
				args = `{"edits":[{"path":"palette.py","sha256":"` + revision + `","old":"fixture","new":"fixed"}]}`
			}
			importedToolStep(t, runner, run, index, fmt.Sprintf("t%d", index), name, args)
		}
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 4, CallID: "s4"})
		_, rejected := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: 4, CallID: "invented-line", Content: `{"summary":"导入fixture","evidence_ids":["S1","S2","S3"],"locations":[{"path":"palette.py","line":999,"reason":"导入fixture"}]}`})
		if string(rejected["error_code"]) != `"AI_INVALID_REPORT"` {
			t.Fatal("invented source line accepted")
		}
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 5, CallID: "s5"})
		_, accepted := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: 5, CallID: "actual-line", Content: `{"summary":"导入fixture","evidence_ids":["S1","S2","S3"],"locations":[{"path":"palette.py","line":31,"reason":"导入fixture热点"}]}`})
		if string(accepted["accepted"]) != "true" {
			t.Fatal(accepted)
		}
		return importedAgentAnswer(run, accepted["result"], 5, 3), nil
	})
	run, err := s.RequestCodeRepair(context.Background(), u, CodeRepairInput{ProjectID: "crm-python", Goal: "显式导入scope修复fixture", Consent: true}, "scope-repair-imported-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.processCodeRepair(context.Background()); err != nil {
		t.Fatal(err)
	}
	db.First(&run, run.ID)
	if run.Status != "success" || tests.Load() != 1 {
		t.Fatal(run.Status, run.ErrorCode, tests.Load())
	}
	var report struct {
		Runtime    AgentRuntime `json:"agent_runtime"`
		Validation struct {
			Accepted bool `json:"accepted"`
		} `json:"validation"`
	}
	_ = json.Unmarshal(run.Report, &report)
	if report.Runtime.Framework != "agentscope" || report.Runtime.ModelCalls != 5 || !report.Validation.Accepted {
		t.Fatal("repair lacked actual accepted tool proof")
	}
}
