package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"drop/api/internal/domain"
)

// These are imported protocol fixtures. They exercise Go's authority boundary;
// they do not claim to execute a real model or the AgentScope SDK.
type importedAgentRequest struct {
	Version      int                `json:"version"`
	RunID        string             `json:"run_id"`
	Capability   string             `json:"capability"`
	Phase        string             `json:"phase"`
	Model        string             `json:"model"`
	SystemPrompt string             `json:"system_prompt"`
	Messages     []diagnosisMessage `json:"messages"`
	Tools        []map[string]any   `json:"tools"`
	Budget       agentBudget        `json:"budget"`
}

func importedAgentCallback(t *testing.T, runner *AgentRunner, run importedAgentRequest, call agentCallback) (int, map[string]json.RawMessage) {
	t.Helper()
	body, err := json.Marshal(call)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/v1/callback/"+run.RunID, bytes.NewReader(body))
	request.Header.Set("Authorization", "Bearer "+run.Capability)
	recorder := httptest.NewRecorder()
	runner.ServeHTTP(recorder, request)
	var response map[string]json.RawMessage
	if json.Unmarshal(recorder.Body.Bytes(), &response) != nil {
		t.Fatal("invalid fixture callback response")
	}
	if recorder.Header().Get("Content-Length") == "" {
		t.Fatal("callback missing bounded HTTP Content-Length")
	}
	return recorder.Code, response
}

func importedAgentSession(t *testing.T, phase string, invoke func(context.Context, string, json.RawMessage) (agentToolResult, error), finish func(context.Context, string) (json.RawMessage, error)) (*AgentRunner, importedAgentRequest, *agentSession) {
	t.Helper()
	ctx, cancel := context.WithCancelCause(context.Background())
	t.Cleanup(func() { cancel(context.Canceled) })
	budget, _ := phaseBudget(phase)
	definitions := diagnosisDefinitions()
	if phase == "repair" {
		definitions = repairDefinitions()
	}
	if phase == "planning" {
		definitions = []map[string]any{{"type": "function", "function": map[string]any{"name": "list_collectors", "parameters": map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false}}}}
	}
	schemas, err := compileAgentTools(phase, definitions)
	if err != nil {
		t.Fatal(err)
	}
	session := &agentSession{ctx: ctx, cancel: cancel, capability: "IMPORTED_PRIVATE_CAPABILITY_24_BYTES", spec: agentRunSpec{Phase: phase, Invoke: invoke, Finish: finish}, budget: budget, cache: map[string]cachedAgentCallback{}, endedRounds: map[int]bool{}, schemas: schemas}
	run := importedAgentRequest{RunID: "imported-public-run", Capability: session.capability, Phase: phase}
	return &AgentRunner{sessions: map[string]*agentSession{run.RunID: session}}, run, session
}

func TestAgentGatewayCapabilityReplayAndCanonicalIdentity(t *testing.T) {
	var calls atomic.Int32
	runner, run, session := importedAgentSession(t, "diagnosis", func(context.Context, string, json.RawMessage) (agentToolResult, error) {
		calls.Add(1)
		return agentToolResult{Observation: json.RawMessage(`{"imported_fixture":true}`)}, nil
	}, nil)
	bad := run
	bad.Capability = "IMPORTED_WRONG_CAPABILITY"
	if status, _ := importedAgentCallback(t, runner, bad, agentCallback{Type: "model_start", Round: 1, CallID: "start"}); status != 401 {
		t.Fatal("wrong capability admitted")
	}
	if _, response := importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 1, CallID: "start"}); string(response["ok"]) != "true" {
		t.Fatal(response)
	}
	call := agentCallback{Type: "tool", Round: 1, CallID: "imported-tool", Name: "inspect_call_paths", Arguments: json.RawMessage(`{"rank":1}`)}
	_, first := importedAgentCallback(t, runner, run, call)
	call.Arguments = json.RawMessage(`{ "rank" : 1 }`)
	_, replay := importedAgentCallback(t, runner, run, call)
	if calls.Load() != 1 || !bytes.Equal(first["observation"], replay["observation"]) {
		t.Fatal("replay executed a second tool")
	}
	call.Arguments = json.RawMessage(`{"rank":2}`)
	status, response := importedAgentCallback(t, runner, run, call)
	if status != 409 || string(response["error_code"]) != `"AGENT_CALLBACK_REPLAY"` || calls.Load() != 1 || context.Cause(session.ctx) == nil {
		t.Fatal("changed callback replay was accepted", response)
	}
}

func TestAgentGatewayPhaseSchemaRoundAndToolBudgets(t *testing.T) {
	for _, test := range []struct {
		name  string
		calls []agentCallback
		want  string
	}{
		{"phase", []agentCallback{{Type: "model_start", Round: 1, CallID: "s"}, {Type: "tool", Round: 1, CallID: "t", Name: "test_patch", Arguments: json.RawMessage(`{}`)}}, "AI_BUDGET_EXCEEDED"},
		{"schema", []agentCallback{{Type: "model_start", Round: 1, CallID: "s"}, {Type: "tool", Round: 1, CallID: "t", Name: "inspect_hotspots", Arguments: json.RawMessage(`{"task_id":999}`)}}, "AI_TOOL_REJECTED"},
		{"round", []agentCallback{{Type: "model_start", Round: 2, CallID: "s"}}, "AI_BUDGET_EXCEEDED"},
		{"unreserved", []agentCallback{{Type: "tool", Round: 1, CallID: "t", Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)}}, "AI_BUDGET_EXCEEDED"},
		{"per-round", []agentCallback{{Type: "model_start", Round: 1, CallID: "s"}, {Type: "tool", Round: 1, CallID: "t1", Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)}, {Type: "tool", Round: 1, CallID: "t2", Name: "check_collection_quality", Arguments: json.RawMessage(`{}`)}, {Type: "tool", Round: 1, CallID: "t3", Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)}}, "AI_BUDGET_EXCEEDED"},
	} {
		t.Run(test.name, func(t *testing.T) {
			runner, run, _ := importedAgentSession(t, "diagnosis", func(context.Context, string, json.RawMessage) (agentToolResult, error) {
				return agentToolResult{Observation: json.RawMessage(`{}`)}, nil
			}, nil)
			var response map[string]json.RawMessage
			for _, call := range test.calls {
				_, response = importedAgentCallback(t, runner, run, call)
			}
			if string(response["error_code"]) != `"`+test.want+`"` || string(response["fatal"]) != "true" {
				t.Fatal(response)
			}
		})
	}
	runner, run, session := importedAgentSession(t, "diagnosis", func(context.Context, string, json.RawMessage) (agentToolResult, error) {
		return agentToolResult{Observation: json.RawMessage(`{}`)}, nil
	}, nil)
	for round := 1; round <= 4; round++ {
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: round, CallID: string(rune('a' + round))})
		for n := 0; n < 2; n++ {
			_, response := importedAgentCallback(t, runner, run, agentCallback{Type: "tool", Round: round, CallID: string(rune('m' + round*2 + n)), Name: "inspect_hotspots", Arguments: json.RawMessage(`{}`)})
			if round == 4 && n == 1 && string(response["error_code"]) != `"AI_BUDGET_EXCEEDED"` {
				t.Fatal("diagnosis exceeded seven callbacks")
			}
		}
	}
	if session.toolCalls != 7 {
		t.Fatal(session.toolCalls)
	}
}

func TestAgentGatewayFinalFormatRepairAndAcceptedSeal(t *testing.T) {
	runner, run, _ := importedAgentSession(t, "diagnosis", nil, func(_ context.Context, content string) (json.RawMessage, error) {
		if content != "imported-valid" {
			return nil, domain.Fail("AI_INVALID_REPORT", "fixture rejection")
		}
		return json.RawMessage(`{"summary":"imported fixture"}`), nil
	})
	for round := 1; round <= 4; round++ {
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: round, CallID: string(rune('a' + round))})
	}
	_, response := importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: 4, CallID: "invalid", Content: "bad"})
	if string(response["format_only"]) != "true" {
		t.Fatal(response)
	}
	importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 5, CallID: "format-start"})
	_, response = importedAgentCallback(t, runner, run, agentCallback{Type: "finish", Round: 5, CallID: "valid", Content: "imported-valid"})
	if string(response["accepted"]) != "true" {
		t.Fatal(response)
	}
	_, response = importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 6, CallID: "after-accept"})
	if string(response["fatal"]) != "true" {
		t.Fatal("accepted run was not sealed")
	}
	runner, run, _ = importedAgentSession(t, "diagnosis", nil, func(context.Context, string) (json.RawMessage, error) {
		return nil, domain.Fail("AI_INVALID_REPORT", "fixture")
	})
	for round := 1; round <= 4; round++ {
		importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: round, CallID: string(rune('a' + round))})
	}
	_, response = importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 5, CallID: "unearned-repair"})
	if string(response["fatal"]) != "true" {
		t.Fatal("unearned fifth model call admitted")
	}
}

func importedAgentRunner(t *testing.T, serve func(*AgentRunner, *http.Request, importedAgentRequest) (json.RawMessage, error)) *AgentRunner {
	t.Helper()
	runner := &AgentRunner{sessions: map[string]*agentSession{}}
	runner.client = &http.Client{Transport: fixtureRoundTripper(func(request *http.Request) (*http.Response, error) {
		body, _ := io.ReadAll(request.Body)
		var run importedAgentRequest
		if !decodeAgentJSON(body, &run) {
			t.Fatal("invalid imported run request")
		}
		if run.Capability == run.RunID || len(run.Capability) < 32 {
			t.Fatal("capability is not distinct from public run")
		}
		for _, message := range run.Messages {
			if strings.Contains(message.Content, run.Capability) {
				t.Fatal("capability leaked to model input")
			}
		}
		result, err := serve(runner, request, run)
		if err != nil {
			return nil, err
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(bytes.NewReader(result)), Header: make(http.Header)}, nil
	})}
	return runner
}

func importedAgentAnswer(run importedAgentRequest, result json.RawMessage, models, tools int) json.RawMessage {
	answer, _ := json.Marshal(map[string]any{"version": 1, "run_id": run.RunID, "result": result, "runtime": AgentRuntime{Framework: "agentscope", FrameworkVersion: "2.0.10", AgentClass: "agentscope.agent.Agent", RunID: run.RunID, ModelCalls: models, ToolCalls: tools, ElapsedMS: 1}})
	return answer
}

func TestAgentRunnerRequiresAcceptedResultAndExactRuntimeCounts(t *testing.T) {
	for _, mode := range []string{"accepted", "unaccepted", "changed-result", "wrong-count"} {
		t.Run(mode, func(t *testing.T) {
			runner := importedAgentRunner(t, func(r *AgentRunner, _ *http.Request, run importedAgentRequest) (json.RawMessage, error) {
				importedAgentCallback(t, r, run, agentCallback{Type: "model_start", Round: 1, CallID: "start"})
				if mode != "unaccepted" {
					importedAgentCallback(t, r, run, agentCallback{Type: "finish", Round: 1, CallID: "finish", Content: `{"summary":"imported fixture"}`})
				}
				result := json.RawMessage(`{"summary":"imported fixture"}`)
				models := 1
				if mode == "changed-result" {
					result = json.RawMessage(`{"summary":"forged"}`)
				}
				if mode == "wrong-count" {
					models = 2
				}
				return importedAgentAnswer(run, result, models, 0), nil
			})
			_, metadata, err := runner.Run(context.Background(), agentRunSpec{Phase: "diagnosis", Model: "imported-fixture-model", Tools: diagnosisDefinitions(), Invoke: func(context.Context, string, json.RawMessage) (agentToolResult, error) {
				t.Fatal("unexpected tool")
				return agentToolResult{}, nil
			}, Finish: func(_ context.Context, content string) (json.RawMessage, error) { return json.RawMessage(content), nil }})
			if mode == "accepted" {
				if err != nil || metadata == nil {
					t.Fatal(err)
				}
			} else if err == nil {
				t.Fatal("unaccepted result/count was trusted")
			} else if mode == "wrong-count" && safeAgentError(err) != "AGENT_RUNTIME_METADATA_MISMATCH" || (mode == "changed-result" || mode == "unaccepted") && safeAgentError(err) != "AGENT_RUNTIME_RESULT_MISMATCH" {
				t.Fatal("runtime failure reason was not distinguished", mode, err)
			}
			if len(runner.sessions) != 0 {
				t.Fatal("capability was retained after run")
			}
		})
	}
}

func TestAgentRunnerFenceCancellationRevokesCapability(t *testing.T) {
	var revoked atomic.Bool
	started := make(chan importedAgentRequest, 1)
	runner := importedAgentRunner(t, func(r *AgentRunner, request *http.Request, run importedAgentRequest) (json.RawMessage, error) {
		importedAgentCallback(t, r, run, agentCallback{Type: "model_start", Round: 1, CallID: "start"})
		started <- run
		<-request.Context().Done()
		return nil, request.Context().Err()
	})
	finished := make(chan error, 1)
	go func() {
		_, _, err := runner.Run(context.Background(), agentRunSpec{Phase: "diagnosis", Tools: diagnosisDefinitions(), Fence: func(context.Context) error {
			if revoked.Load() {
				return domain.Fail("STALE_LEASE", "imported cancellation")
			}
			return nil
		}, Invoke: func(context.Context, string, json.RawMessage) (agentToolResult, error) { return agentToolResult{}, nil }, Finish: func(context.Context, string) (json.RawMessage, error) { return nil, errors.New("unused") }})
		finished <- err
	}()
	run := <-started
	revoked.Store(true)
	select {
	case err := <-finished:
		if safeAgentError(err) != "STALE_LEASE" {
			t.Fatal(err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("runner did not cancel model transport after lease loss")
	}
	if status, _ := importedAgentCallback(t, runner, run, agentCallback{Type: "model_start", Round: 2, CallID: "after-cancel"}); status != 401 {
		t.Fatal("capability survived cancellation")
	}
}

func TestDiagnosisRejectsForgedAgentRuntime(t *testing.T) {
	for _, runtime := range []string{`null`, `{"framework":"agentscope"}`} {
		_, err := validateDiagnosisReport(`{"summary":"imported fixture","hypotheses":[{"claim":"fixture","evidence_ids":["E1"],"verification":"fixture"}],"limitations":[],"agent_runtime":`+runtime+`}`, []DiagnosisStep{{EvidenceID: "E1"}})
		if err == nil {
			t.Fatal("model authored runtime field accepted")
		}
	}
}

func TestAgentToolSchemasCompileGoAuthoredRequiredLists(t *testing.T) {
	if _, err := compileAgentTools("repair", repairDefinitions()); err != nil {
		t.Fatal("Go []string required lists must compile", err)
	}
	definition := []map[string]any{{"type": "function", "function": map[string]any{"name": "propose_collection", "parameters": map[string]any{"type": "object", "properties": map[string]any{"kind": map[string]any{"type": "string"}}, "required": []string{"kind"}, "additionalProperties": false}}}}
	schemas, err := compileAgentTools("planning", definition)
	if err != nil {
		t.Fatal(err)
	}
	if schemas["propose_collection"].Validate(map[string]any{}) == nil {
		t.Fatal("normalization discarded required argument validation")
	}
}

func TestAgentRuntimeFailurePreservesOnlyFixedSafeEnums(t *testing.T) {
	budget, _ := phaseBudget("repair")
	for _, code := range []string{"AI_BUDGET_EXCEEDED", "AI_INVALID_REPORT", "AI_UPSTREAM_ERROR", "AGENT_RUNTIME_FAILED", "AGENT_RUNTIME_PROTOCOL_ERROR", "AGENT_RUNTIME_BODY_TOO_LARGE", "AGENT_RUNTIME_GATEWAY_UNAVAILABLE"} {
		err := agentRuntimeFailure(http.StatusBadRequest, []byte(`{"error_code":"`+code+`"}`), budget)
		if safeAgentError(err) != code {
			t.Fatal("safe runtime enum was hidden", code, err)
		}
	}
	for _, body := range []string{
		`{"error_code":"IMPORTED_PRIVATE_TOKEN"}`,
		`{"error_code":"AI_UPSTREAM_ERROR","message":"IMPORTED_PRIVATE_TOKEN"}`,
		`{"error_code":"AI_UPSTREAM_ERROR"} {}`,
		`{"error_code":null}`,
		`{"error_code":"https://IMPORTED_PRIVATE_TOKEN.invalid"}`,
		strings.Repeat("IMPORTED_PRIVATE_TOKEN", 64),
	} {
		err := agentRuntimeFailure(http.StatusBadRequest, []byte(body), budget)
		if safeAgentError(err) != "AGENT_RUNTIME_ERROR" || strings.Contains(err.Error(), "IMPORTED_PRIVATE_TOKEN") {
			t.Fatal("untrusted runtime error body escaped enum boundary")
		}
	}
	if safeAgentError(agentRuntimeFailure(http.StatusFound, []byte(`{"error_code":"AI_UPSTREAM_ERROR"}`), budget)) != "AGENT_RUNTIME_ERROR" {
		t.Fatal("redirect body trusted as runtime failure")
	}
}

func TestAgentRunnerNon200UsesSafeFailureEnumWithoutProviderBody(t *testing.T) {
	runner := &AgentRunner{sessions: map[string]*agentSession{}, client: &http.Client{Transport: fixtureRoundTripper(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: http.StatusBadRequest, Body: io.NopCloser(strings.NewReader(`{"error_code":"AI_BUDGET_EXCEEDED"}`)), Header: make(http.Header)}, nil
	})}}
	_, _, err := runner.Run(context.Background(), agentRunSpec{Phase: "diagnosis", Model: "imported-fixture", Tools: diagnosisDefinitions(), Invoke: func(context.Context, string, json.RawMessage) (agentToolResult, error) {
		t.Fatal("failed runtime executed a tool")
		return agentToolResult{}, nil
	}, Finish: func(context.Context, string) (json.RawMessage, error) {
		t.Fatal("failed runtime accepted a report")
		return nil, nil
	}})
	if safeAgentError(err) != "AI_BUDGET_EXCEEDED" || len(runner.sessions) != 0 {
		t.Fatal("runtime error lost or capability not revoked", err)
	}
}

func TestAgentRuntimeFailureDiagnosticsAreFixedBoundedAndPrivate(t *testing.T) {
	budget, _ := phaseBudget("diagnosis")
	valid := `{"error_code":"AI_BUDGET_EXCEEDED","diagnostics":{"stage":"provider_request","exception_kind":"protocol","model_calls":5,"tool_calls":7}}`
	err := agentRuntimeFailure(http.StatusBadRequest, []byte(valid), budget)
	var domainError *domain.Error
	if !errors.As(err, &domainError) || domainError.Code != "AI_BUDGET_EXCEEDED" || len(domainError.Details) != 0 {
		t.Fatal("private diagnostics escaped domain error")
	}
	for _, body := range []string{
		strings.Replace(valid, `"provider_request"`, `"IMPORTED_PRIVATE_TOKEN"`, 1),
		strings.Replace(valid, `"protocol"`, `"IMPORTED_PRIVATE_TOKEN"`, 1),
		strings.Replace(valid, `"model_calls":5`, `"model_calls":6`, 1),
		strings.Replace(valid, `"tool_calls":7`, `"tool_calls":8`, 1),
		strings.Replace(valid, `"tool_calls":7`, `"tool_calls":-1`, 1),
		strings.Replace(valid, `"tool_calls":7`, `"tool_calls":null`, 1),
		strings.Replace(valid, `,"tool_calls":7`, ``, 1),
		strings.Replace(valid, `"tool_calls":7`, `"tool_calls":7,"message":"IMPORTED_PRIVATE_TOKEN"`, 1),
		`{"error_code":"AI_BUDGET_EXCEEDED","diagnostics":null}`,
	} {
		if err := agentRuntimeFailure(http.StatusBadRequest, []byte(body), budget); safeAgentError(err) != "AGENT_RUNTIME_ERROR" || strings.Contains(err.Error(), "IMPORTED_PRIVATE_TOKEN") {
			t.Fatal("invalid private diagnostics trusted")
		}
	}
}
