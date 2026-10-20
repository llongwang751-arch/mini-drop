package service

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"drop/api/internal/domain"
	"github.com/google/uuid"
	"github.com/santhosh-tekuri/jsonschema/v6"
)

// AgentRuntime is control-plane metadata, never a model-authored report field.
type AgentRuntime struct {
	Framework        string          `json:"framework"`
	FrameworkVersion string          `json:"framework_version"`
	AgentClass       string          `json:"agent_class"`
	RunID            string          `json:"run_id"`
	ModelCalls       int             `json:"model_calls"`
	ToolCalls        int             `json:"tool_calls"`
	ElapsedMS        int64           `json:"elapsed_ms"`
	Usage            json.RawMessage `json:"usage,omitempty"`
}

type agentBudget struct {
	MaxModelCalls    int   `json:"max_model_calls"`
	MaxToolCalls     int   `json:"max_tool_calls"`
	MaxToolsPerRound int   `json:"max_tools_per_round"`
	TimeoutMS        int64 `json:"timeout_ms"`
}

func phaseBudget(phase string) (agentBudget, bool) {
	switch phase {
	case "planning":
		return agentBudget{6, 10, 2, 150000}, true
	case "diagnosis":
		return agentBudget{5, 7, 2, 150000}, true
	case "repair":
		return agentBudget{8, 12, 2, 420000}, true
	}
	return agentBudget{}, false
}

func phaseTools(phase string) map[string]bool {
	names := map[string][]string{
		"planning":  {"list_collectors", "propose_collection"},
		"diagnosis": {"read_profile_summary", "inspect_hotspots", "check_collection_quality", "inspect_call_paths", "propose_verification", "compare_profiles"},
		"repair":    {"profile_business_flow", "read_source", "test_patch"},
	}
	allowed := map[string]bool{}
	for _, name := range names[phase] {
		allowed[name] = true
	}
	return allowed
}

type agentToolResult struct {
	Observation json.RawMessage
	Terminal    json.RawMessage
}

type agentRunSpec struct {
	Phase        string
	Model        string
	SystemPrompt string
	Messages     []diagnosisMessage
	Tools        []map[string]any
	Fence        func(context.Context) error
	Invoke       func(context.Context, string, json.RawMessage) (agentToolResult, error)
	Finish       func(context.Context, string) (json.RawMessage, error)
}

type agentCallback struct {
	Type      string          `json:"type"`
	Round     int             `json:"round"`
	CallID    string          `json:"call_id"`
	Name      string          `json:"name,omitempty"`
	Arguments json.RawMessage `json:"arguments,omitempty"`
	Content   string          `json:"content,omitempty"`
	Usage     json.RawMessage `json:"usage,omitempty"`
}

type cachedAgentCallback struct {
	Request  []byte
	Response []byte
}

type agentSession struct {
	mu          sync.Mutex
	ctx         context.Context
	cancel      context.CancelCauseFunc
	capability  string
	spec        agentRunSpec
	budget      agentBudget
	round       int
	toolCalls   int
	roundTools  int
	formatOnly  bool
	accepted    json.RawMessage
	cache       map[string]cachedAgentCallback
	endedRounds map[int]bool
	schemas     map[string]*jsonschema.Schema
}

// AgentRunner delegates the real model loop to AgentScope. Only its temporary
// callback capability can reach a registered, phase-bound Go tool closure.
type AgentRunner struct {
	mu       sync.Mutex
	sessions map[string]*agentSession
	client   *http.Client
	server   *http.Server
	listener net.Listener
}

func validAgentSocket(socket string) bool {
	return filepath.IsAbs(socket) && len(socket) <= 200 && !strings.ContainsAny(socket, "\x00\r\n")
}

func NewAgentRunner(runnerSocket, gatewaySocket string) (*AgentRunner, error) {
	if !validAgentSocket(runnerSocket) || !validAgentSocket(gatewaySocket) || runnerSocket == gatewaySocket {
		return nil, errors.New("invalid AgentScope socket configuration")
	}
	if !validAgentDirectory(runnerSocket, 10002) {
		return nil, errors.New("AgentScope runner socket directory rejected")
	}
	listener, err := listenAgentGateway(gatewaySocket)
	if err != nil {
		return nil, errors.New("AgentScope gateway socket unavailable")
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		conn, err := (&net.Dialer{Timeout: 5 * time.Second}).DialContext(ctx, "unix", runnerSocket)
		if err != nil {
			return nil, err
		}
		if err := verifyAgentPeer(conn, 10002); err != nil {
			conn.Close()
			return nil, err
		}
		return conn, nil
	}, MaxConnsPerHost: 4, MaxIdleConns: 4, ResponseHeaderTimeout: 425 * time.Second}
	r := &AgentRunner{sessions: map[string]*agentSession{}, listener: listener, client: &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	r.server = &http.Server{Handler: r, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 4096}
	go func() { _ = r.server.Serve(listener) }()
	return r, nil
}

func (r *AgentRunner) Close() error {
	r.mu.Lock()
	for _, session := range r.sessions {
		session.cancel(context.Canceled)
	}
	r.mu.Unlock()
	r.client.CloseIdleConnections()
	return r.server.Close()
}

func decodeAgentJSON(data []byte, out any) bool {
	d := json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	var tail any
	return d.Decode(out) == nil && d.Decode(&tail) == io.EOF
}

func canonicalAgentJSON(data []byte) ([]byte, bool) {
	var value any
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	var tail any
	if d.Decode(&value) != nil || d.Decode(&tail) != io.EOF {
		return nil, false
	}
	encoded, err := json.Marshal(value)
	return encoded, err == nil
}

func agentFailure(code string, fatal bool) map[string]any {
	return map[string]any{"ok": false, "error_code": code, "fatal": fatal, "feedback": "请遵守本阶段工具与证据约束，使用实际返回的证据重新提交规定JSON。"}
}

func safeAgentError(err error) string {
	var e *domain.Error
	if errors.As(err, &e) && len(e.Code) > 0 && len(e.Code) <= 80 && strings.IndexFunc(e.Code, func(r rune) bool { return !(r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '_') }) < 0 {
		return e.Code
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return "DIAGNOSIS_CANCELED"
	}
	return "AGENT_RUNTIME_ERROR"
}

func (r *AgentRunner) ServeHTTP(w http.ResponseWriter, request *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	deny := func(status int, code string) {
		body, _ := json.Marshal(agentFailure(code, true))
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		w.WriteHeader(status)
		_, _ = w.Write(body)
	}
	if request.Method != http.MethodPost || !strings.HasPrefix(request.URL.Path, "/v1/callback/") || request.URL.RawQuery != "" {
		deny(http.StatusNotFound, "AGENT_CALLBACK_REJECTED")
		return
	}
	id := strings.TrimPrefix(request.URL.Path, "/v1/callback/")
	r.mu.Lock()
	session := r.sessions[id]
	r.mu.Unlock()
	auth := request.Header.Get("Authorization")
	if session == nil || subtle.ConstantTimeCompare([]byte(auth), []byte("Bearer "+session.capability)) != 1 {
		deny(http.StatusUnauthorized, "AGENT_CAPABILITY_REJECTED")
		return
	}
	raw, err := io.ReadAll(io.LimitReader(request.Body, 65537))
	if err != nil || len(raw) > 65536 {
		deny(http.StatusRequestEntityTooLarge, "AI_BUDGET_EXCEEDED")
		return
	}
	var call agentCallback
	canonical, ok := canonicalAgentJSON(raw)
	if !ok || !decodeAgentJSON(raw, &call) || len(call.CallID) < 1 || len(call.CallID) > 128 || strings.ContainsAny(call.CallID, "\r\n\x00") {
		deny(http.StatusBadRequest, "AGENT_CALLBACK_REJECTED")
		return
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	if cause := context.Cause(session.ctx); cause != nil {
		deny(http.StatusGone, safeAgentError(cause))
		return
	}
	if previous, exists := session.cache[call.CallID]; exists {
		if !bytes.Equal(canonical, previous.Request) {
			session.cancel(domain.Fail("AGENT_CALLBACK_REPLAY", "工具回调标识被重复用于不同请求"))
			deny(http.StatusConflict, "AGENT_CALLBACK_REPLAY")
			return
		}
		w.Header().Set("Content-Length", strconv.Itoa(len(previous.Response)))
		_, _ = w.Write(previous.Response)
		return
	}
	response := session.callback(call)
	encoded, marshalErr := json.Marshal(response)
	if marshalErr != nil || len(encoded) > 65536 {
		session.cancel(domain.Fail("AI_BUDGET_EXCEEDED", "工具回调输出超限"))
		encoded, _ = json.Marshal(agentFailure("AI_BUDGET_EXCEEDED", true))
	}
	session.cache[call.CallID] = cachedAgentCallback{Request: canonical, Response: encoded}
	w.Header().Set("Content-Length", strconv.Itoa(len(encoded)))
	_, _ = w.Write(encoded)
}

func (s *agentSession) callback(call agentCallback) map[string]any {
	fail := func(code string) map[string]any {
		s.cancel(domain.Fail(code, "AgentScope运行协议或预算无效"))
		return agentFailure(code, true)
	}
	if len(s.accepted) != 0 || call.Round < 1 || len(s.cache) >= 64 {
		return fail("AGENT_CALLBACK_REJECTED")
	}
	if s.spec.Fence != nil {
		if err := s.spec.Fence(s.ctx); err != nil {
			s.cancel(err)
			return agentFailure(safeAgentError(err), true)
		}
	}
	if call.Type != "tool" && (call.Name != "" || len(call.Arguments) != 0) || call.Type != "finish" && call.Content != "" || call.Type != "model_end" && len(call.Usage) != 0 {
		return fail("AGENT_CALLBACK_REJECTED")
	}
	switch call.Type {
	case "model_start":
		if call.Round != s.round+1 || call.Round > s.budget.MaxModelCalls || s.spec.Phase == "diagnosis" && call.Round == 5 && !s.formatOnly {
			return fail("AI_BUDGET_EXCEEDED")
		}
		s.round = call.Round
		s.roundTools = 0
		return map[string]any{"ok": true}
	case "model_end":
		if call.Round != s.round || s.endedRounds[call.Round] || len(call.Usage) > 4096 {
			return fail("AGENT_CALLBACK_REJECTED")
		}
		if len(call.Usage) > 0 && !validAgentUsage(call.Usage) {
			return fail("AGENT_CALLBACK_REJECTED")
		}
		s.endedRounds[call.Round] = true
		return map[string]any{"ok": true}
	case "tool":
		maxArgs := 1024
		if s.spec.Phase == "planning" {
			maxArgs = 4096
		}
		if s.spec.Phase == "repair" {
			maxArgs = 32768
		}
		if call.Round != s.round || s.formatOnly || !phaseTools(s.spec.Phase)[call.Name] || s.toolCalls >= s.budget.MaxToolCalls || s.roundTools >= s.budget.MaxToolsPerRound || len(call.Arguments) > maxArgs {
			return fail("AI_BUDGET_EXCEEDED")
		}
		var args map[string]any
		if !decodeAgentJSON(call.Arguments, &args) || args == nil {
			return fail("AI_TOOL_REJECTED")
		}
		if schema := s.schemas[call.Name]; schema == nil || schema.Validate(args) != nil {
			return fail("AI_TOOL_REJECTED")
		}
		s.toolCalls++
		s.roundTools++
		result, err := s.spec.Invoke(s.ctx, call.Name, call.Arguments)
		if err != nil {
			s.cancel(err)
			return agentFailure(safeAgentError(err), true)
		}
		response := map[string]any{"ok": true, "observation": result.Observation}
		if len(result.Terminal) > 0 {
			if s.spec.Phase != "planning" || call.Name != "propose_collection" || !json.Valid(result.Terminal) {
				return fail("AGENT_CALLBACK_REJECTED")
			}
			s.accepted = append(json.RawMessage(nil), result.Terminal...)
			response["terminal"] = true
			response["result"] = s.accepted
		}
		return response
	case "finish":
		if call.Round != s.round || len(call.Content) > 12000 || s.spec.Finish == nil {
			return fail("AGENT_CALLBACK_REJECTED")
		}
		accepted, err := s.spec.Finish(s.ctx, call.Content)
		if err != nil {
			response := agentFailure(safeAgentError(err), false)
			if s.spec.Phase == "diagnosis" && call.Round == 4 && safeAgentError(err) == "AI_INVALID_REPORT" {
				s.formatOnly = true
				response["format_only"] = true
			}
			return response
		}
		if !json.Valid(accepted) {
			return fail("AGENT_CALLBACK_REJECTED")
		}
		s.accepted = append(json.RawMessage(nil), accepted...)
		return map[string]any{"ok": true, "accepted": true, "result": s.accepted}
	}
	return fail("AGENT_CALLBACK_REJECTED")
}

func validAgentUsage(raw json.RawMessage) bool {
	var value map[string]json.RawMessage
	if !decodeAgentJSON(raw, &value) || value == nil || len(value) > 16 {
		return false
	}
	for name, number := range value {
		if len(name) > 64 || strings.IndexFunc(name, func(r rune) bool { return !(r >= 'a' && r <= 'z' || r == '_') }) >= 0 {
			return false
		}
		var n int64
		if json.Unmarshal(number, &n) != nil || n < 0 || n > 1000000000 {
			return false
		}
	}
	return true
}

func (r *AgentRunner) Run(ctx context.Context, spec agentRunSpec) (json.RawMessage, *AgentRuntime, error) {
	if r == nil {
		return nil, nil, domain.Fail("AGENT_RUNTIME_UNAVAILABLE", "AgentScope未配置")
	}
	budget, valid := phaseBudget(spec.Phase)
	if !valid || spec.Invoke == nil || spec.Finish == nil {
		return nil, nil, domain.Fail("AGENT_RUNTIME_ERROR", "AgentScope阶段配置无效")
	}
	schemas, err := compileAgentTools(spec.Phase, spec.Tools)
	if err != nil {
		return nil, nil, err
	}
	work, cancel := context.WithTimeout(ctx, time.Duration(budget.TimeoutMS)*time.Millisecond)
	defer cancel()
	work, revoke := context.WithCancelCause(work)
	defer revoke(context.Canceled)
	id, capability := uuid.NewString(), RandomToken()
	session := &agentSession{ctx: work, cancel: revoke, capability: capability, spec: spec, budget: budget, cache: map[string]cachedAgentCallback{}, endedRounds: map[int]bool{}, schemas: schemas}
	r.mu.Lock()
	r.sessions[id] = session
	r.mu.Unlock()
	defer func() {
		revoke(context.Canceled)
		r.mu.Lock()
		delete(r.sessions, id)
		r.mu.Unlock()
		// Wait for an executor callback to unwind before worker finalization.
		session.mu.Lock()
		session.mu.Unlock()
	}()
	if spec.Fence != nil {
		if err := spec.Fence(work); err != nil {
			return nil, nil, err
		}
		go func() {
			ticker := time.NewTicker(500 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-work.Done():
					return
				case <-ticker.C:
					if err := spec.Fence(work); err != nil {
						revoke(err)
						return
					}
				}
			}
		}()
	}
	body, err := json.Marshal(map[string]any{"version": 1, "run_id": id, "capability": capability, "phase": spec.Phase, "model": spec.Model, "system_prompt": spec.SystemPrompt, "messages": spec.Messages, "tools": spec.Tools, "budget": budget})
	if err != nil || len(body) > 262144 {
		return nil, nil, domain.Fail("AI_BUDGET_EXCEEDED", "AgentScope输入超限")
	}
	request, _ := http.NewRequestWithContext(work, http.MethodPost, "http://agentscope/v1/run", bytes.NewReader(body))
	request.Header.Set("Content-Type", "application/json")
	response, err := r.client.Do(request)
	if err != nil {
		if cause := context.Cause(work); cause != nil {
			return nil, nil, cause
		}
		return nil, nil, domain.Fail("AGENT_RUNTIME_UNAVAILABLE", "AgentScope服务调用失败")
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, 131073))
	if cause := context.Cause(work); cause != nil {
		return nil, nil, cause
	}
	if err != nil || len(raw) > 131072 {
		return nil, nil, domain.Fail("AGENT_RUNTIME_ERROR", "AgentScope响应无效")
	}
	if response.StatusCode != http.StatusOK {
		return nil, nil, agentRuntimeFailure(response.StatusCode, raw, budget)
	}
	var answer struct {
		Version int             `json:"version"`
		RunID   string          `json:"run_id"`
		Result  json.RawMessage `json:"result"`
		Runtime AgentRuntime    `json:"runtime"`
	}
	if !decodeAgentJSON(raw, &answer) {
		slog.Error("AgentScope execution failed", "reason", "response_protocol_invalid")
		return nil, nil, domain.Fail("AGENT_RUNTIME_PROTOCOL_ERROR", "AgentScope响应协议无效")
	}
	session.mu.Lock()
	defer session.mu.Unlock()
	a, aOK := canonicalAgentJSON(answer.Result)
	b, bOK := canonicalAgentJSON(session.accepted)
	m := answer.Runtime
	if !aOK || !bOK || !bytes.Equal(a, b) {
		slog.Error("AgentScope execution failed", "reason", "accepted_result_mismatch", "model_calls", session.round, "tool_calls", session.toolCalls)
		return nil, nil, domain.Fail("AGENT_RUNTIME_RESULT_MISMATCH", "AgentScope结果与Go验收内容不一致")
	}
	if answer.Version != 1 || answer.RunID != id || m.Framework != "agentscope" || m.FrameworkVersion != "2.0.10" || m.AgentClass != "agentscope.agent.Agent" || m.RunID != id || m.ModelCalls != session.round || m.ToolCalls != session.toolCalls || m.ElapsedMS < 0 || m.ElapsedMS > budget.TimeoutMS+5000 || len(m.Usage) > 0 && !validAgentUsage(m.Usage) {
		slog.Error("AgentScope execution failed", "reason", "runtime_metadata_mismatch", "model_calls", session.round, "tool_calls", session.toolCalls)
		return nil, nil, domain.Fail("AGENT_RUNTIME_METADATA_MISMATCH", "AgentScope运行身份或计数无效")
	}
	return append(json.RawMessage(nil), session.accepted...), &m, nil
}

// The private runner emits a fixed application enum on failure. Never retain
// its response body, SDK exception text, arbitrary enum, or additional fields.
func agentRuntimeFailure(status int, raw []byte, budget agentBudget) error {
	fallback := domain.Fail("AGENT_RUNTIME_ERROR", "AgentScope响应无效")
	if status < 400 || status > 599 || len(raw) > 512 {
		return fallback
	}
	var response struct {
		ErrorCode   string                   `json:"error_code"`
		Diagnostics *agentFailureDiagnostics `json:"diagnostics,omitempty"`
	}
	if !decodeAgentJSON(raw, &response) {
		return fallback
	}
	var fields map[string]json.RawMessage
	_ = json.Unmarshal(raw, &fields)
	if diagnostic, present := fields["diagnostics"]; present {
		var members map[string]json.RawMessage
		if response.Diagnostics == nil || !response.Diagnostics.valid(budget) || json.Unmarshal(diagnostic, &members) != nil || len(members) != 4 {
			return fallback
		}
		for _, key := range []string{"stage", "exception_kind", "model_calls", "tool_calls"} {
			if value, ok := members[key]; !ok || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
				return fallback
			}
		}
	}
	switch response.ErrorCode {
	case "AI_CONFIGURATION_ERROR", "AI_TOOL_REJECTED", "AI_BUDGET_EXCEEDED", "AI_INVALID_RESPONSE", "AI_UPSTREAM_ERROR", "AI_INVALID_REPORT", "AI_INSUFFICIENT_EVIDENCE",
		"AGENT_RUNTIME_BUSY", "AGENT_RUNTIME_DUPLICATE_RUN", "AGENT_RUNTIME_VERSION_MISMATCH", "AGENT_RUNTIME_PROTOCOL_ERROR", "AGENT_RUNTIME_BODY_TOO_LARGE", "AGENT_RUNTIME_PEER_REJECTED", "AGENT_RUNTIME_GATEWAY_REJECTED", "AGENT_RUNTIME_GATEWAY_UNAVAILABLE", "AGENT_RUNTIME_FAILED",
		"STALE_LEASE", "STALE_ATTEMPT", "AUTHORIZATION_REVOKED", "DIAGNOSIS_CANCELED", "INPUT_CHANGED":
		if d := response.Diagnostics; d != nil {
			slog.Error("AgentScope execution failed", "reason", "runner_non_200", "error_code", response.ErrorCode, "stage", d.Stage, "exception_kind", d.ExceptionKind, "model_calls", d.ModelCalls, "tool_calls", d.ToolCalls)
		} else {
			slog.Error("AgentScope execution failed", "reason", "runner_non_200", "error_code", response.ErrorCode)
		}
		return domain.Fail(response.ErrorCode, "AgentScope阶段执行失败，请检查受控工具证据")
	}
	return fallback
}

type agentFailureDiagnostics struct {
	Stage         string `json:"stage"`
	ExceptionKind string `json:"exception_kind"`
	ModelCalls    int    `json:"model_calls"`
	ToolCalls     int    `json:"tool_calls"`
}

func (d agentFailureDiagnostics) valid(budget agentBudget) bool {
	if d.ModelCalls < 0 || d.ModelCalls > budget.MaxModelCalls || d.ToolCalls < 0 || d.ToolCalls > budget.MaxToolCalls {
		return false
	}
	switch d.ExceptionKind {
	case "protocol", "timeout", "transport", "sdk", "validation", "internal":
	default:
		return false
	}
	switch d.Stage {
	case "setup", "model_start", "provider_request", "provider_response", "model_parse", "model_end", "tool_callback", "agent_reply", "finish_callback", "result_encode", "response_write":
		return true
	}
	return false
}

func cleanAgentSocket(socket string) bool {
	info, err := os.Lstat(filepath.Dir(socket))
	return err == nil && info.IsDir() && info.Mode()&os.ModeSymlink == 0 && info.Mode().Perm()&0022 == 0
}

func compileAgentTools(phase string, tools []map[string]any) (map[string]*jsonschema.Schema, error) {
	schemas := map[string]*jsonschema.Schema{}
	for _, tool := range tools {
		function, ok := tool["function"].(map[string]any)
		if !ok {
			return nil, domain.Fail("AGENT_RUNTIME_ERROR", "工具定义无效")
		}
		name, _ := function["name"].(string)
		if !phaseTools(phase)[name] || schemas[name] != nil {
			return nil, domain.Fail("AGENT_RUNTIME_ERROR", "工具定义越过阶段边界")
		}
		compiler := jsonschema.NewCompiler()
		uri := "https://drop.invalid/agent-tools/" + name
		// The compiler accepts JSON value types. Definitions authored in Go
		// contain []string for required, which must become JSON's []any first.
		raw, err := json.Marshal(function["parameters"])
		var document any
		if err != nil || json.Unmarshal(raw, &document) != nil || compiler.AddResource(uri, document) != nil {
			return nil, domain.Fail("AGENT_RUNTIME_ERROR", "工具参数定义无效")
		}
		schema, err := compiler.Compile(uri)
		if err != nil {
			return nil, domain.Fail("AGENT_RUNTIME_ERROR", "工具参数定义无效")
		}
		schemas[name] = schema
	}
	if len(schemas) == 0 {
		return nil, domain.Fail("AGENT_RUNTIME_ERROR", "工具定义为空")
	}
	return schemas, nil
}
