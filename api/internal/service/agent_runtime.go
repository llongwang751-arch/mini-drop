// Package service：Go 与独立 Python AgentScope 之间的控制协议和授权网关。
// 每次运行使用随机 capability、阶段工具白名单与硬预算；Python 发出模型和工具回调，Go 重新核验租约、目标、权限和最终结果。
// 结果按原始 JSON 数值词法严格比对，不能用浮点重编码或自报运行证明掩盖内容变化。
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
	"drop/api/internal/repository"
	"github.com/google/uuid"
	"github.com/santhosh-tekuri/jsonschema/v6"
)

// AgentRuntime 是控制平面生成的运行证明，不是模型可以撰写或覆盖的报告字段。
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
		return agentBudget{9, 14, 2, 150000}, true
	case "repair":
		return agentBudget{8, 12, 2, 420000}, true
	}
	return agentBudget{}, false
}

func phaseTools(phase string) map[string]bool {
	names := map[string][]string{
		"planning":  {"list_collectors", "propose_collection"},
		"diagnosis": {"read_profile_summary", "inspect_hotspots", "check_collection_quality", "inspect_call_paths", "propose_verification", "compare_profiles", "read_diagnostic_knowledge", "query_business_signals", "inspect_service_topology", "propose_followup_collection"},
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
	Checkpoint   *AgentCheckpointStore
	Budget       *agentBudget
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

// agentSession 持有一次 runner 调用的临时能力、阶段 schema、预算、回放缓存和 Go 已接受结果。
// 互斥锁串行化回调，防止两个模型动作同时通过旧计数；会话注销后 capability 立即失效。
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

// AgentRunner 将真正的模型和工具循环交给 AgentScope。
// 只有本次运行的临时 capability 能访问已登记且绑定阶段的 Go 工具闭包；模型没有数据库或执行器凭据。
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

// NewAgentRunner 创建两条独立 Unix socket 通路，并核验 Python UID 与网关目录边界。
// 模型密钥只属于 runner 配置；Go 在这里注册临时工具会话，不向 Python 提供数据库或执行器连接。
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

// canonicalAgentJSON 规范化对象键和空白，同时用 UseNumber 保留数值词法与精度。
// 0.000001、1e-6、-0 或超 float64 精度的小数不能在 Python 重编码后悄悄改变，再被宽松浮点比较掩盖。
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

// ServeHTTP 处理私有工具回调，先验证 capability、请求上限和固定字段，再在会话锁内校验回放、轮次、schema 与业务栅栏。
// 相同 call_id 与相同规范请求重放只返回缓存；相同 ID 携带不同内容会撤销整次运行，不能重复执行副作用。
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
	raw, err := io.ReadAll(io.LimitReader(request.Body, 262145))
	if err != nil || len(raw) > 262144 {
		deny(http.StatusRequestEntityTooLarge, "AI_BUDGET_EXCEEDED")
		return
	}
	var call agentCallback
	canonical, ok := canonicalAgentJSON(raw)
	if !ok || !decodeAgentJSON(raw, &call) || len(call.CallID) < 1 || len(call.CallID) > 128 || strings.ContainsAny(call.CallID, "\r\n\x00") {
		deny(http.StatusBadRequest, "AGENT_CALLBACK_REJECTED")
		return
	}
	if call.Type != "checkpoint" && len(raw) > 65536 {
		deny(http.StatusRequestEntityTooLarge, "AI_BUDGET_EXCEEDED")
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
	if call.Type == "tool" && session.spec.Checkpoint != nil {
		replayed, found, err := session.spec.Checkpoint.replay(session.ctx, call)
		if err != nil {
			session.cancel(err)
			deny(http.StatusConflict, safeAgentError(err))
			return
		}
		if found {
			var saved map[string]json.RawMessage
			if json.Unmarshal(replayed, &saved) == nil && bytes.Equal(saved["terminal"], []byte("true")) && json.Valid(saved["result"]) {
				session.accepted = append(json.RawMessage(nil), saved["result"]...)
				if err := session.spec.Checkpoint.finish(session.ctx, session.accepted, session.formatOnly); err != nil {
					session.cancel(err)
					deny(http.StatusConflict, safeAgentError(err))
					return
				}
			}
			w.Header().Set("Content-Length", strconv.Itoa(len(replayed)))
			_, _ = w.Write(replayed)
			return
		}
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

// callback 在 Go 侧保留模型调用、工具调用和最终报告的最后决定权。
// model_start 先预留连续轮次，tool 独立校验阶段与参数，finish 通过后封存 accepted 结果；封存后不能追加模型或工具。
func (s *agentSession) callback(call agentCallback) map[string]any {
	fail := func(code string) map[string]any {
		s.cancel(domain.Fail(code, "AgentScope运行协议或预算无效"))
		return agentFailure(code, true)
	}
	if len(s.accepted) != 0 || call.Round < 0 || call.Round == 0 && call.Type != "checkpoint" || len(s.cache) >= 128 {
		return fail("AGENT_CALLBACK_REJECTED")
	}
	if s.spec.Fence != nil {
		if err := s.spec.Fence(s.ctx); err != nil {
			s.cancel(err)
			return agentFailure(safeAgentError(err), true)
		}
	}
	if call.Type != "tool" && (call.Name != "" || len(call.Arguments) != 0) || call.Type != "finish" && call.Type != "checkpoint" && call.Content != "" || call.Type != "model_end" && len(call.Usage) != 0 {
		return fail("AGENT_CALLBACK_REJECTED")
	}
	switch call.Type {
	case "checkpoint":
		var checkpoint struct {
			Sequence int64           `json:"sequence"`
			State    json.RawMessage `json:"state"`
		}
		if s.spec.Checkpoint == nil || call.Round != s.round || !decodeAgentJSON([]byte(call.Content), &checkpoint) {
			return fail("AGENT_CHECKPOINT_REJECTED")
		}
		if err := s.spec.Checkpoint.saveSDK(s.ctx, checkpoint.Sequence, checkpoint.State); err != nil {
			s.cancel(err)
			return agentFailure(safeAgentError(err), true)
		}
		return map[string]any{"ok": true}
	case "model_start":
		if call.Round != s.round+1 || call.Round > s.budget.MaxModelCalls || s.spec.Phase == "diagnosis" && call.Round == s.budget.MaxModelCalls && !s.formatOnly {
			return fail("AI_BUDGET_EXCEEDED")
		}
		if s.spec.Checkpoint != nil {
			if err := s.spec.Checkpoint.reserveModel(s.ctx, call.Round, s.budget, s.spec.Phase); err != nil {
				s.cancel(err)
				return agentFailure(safeAgentError(err), true)
			}
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
		if s.spec.Checkpoint != nil {
			if err := s.spec.Checkpoint.endModel(s.ctx, call.Round, call.Usage); err != nil {
				s.cancel(err)
				return agentFailure(safeAgentError(err), true)
			}
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
		if s.spec.Checkpoint != nil {
			if err := s.spec.Checkpoint.beginTool(s.ctx, call, s.budget); err != nil {
				s.cancel(err)
				return agentFailure(safeAgentError(err), true)
			}
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
		if s.spec.Checkpoint != nil {
			if err := s.spec.Checkpoint.completeTool(s.ctx, call, response); err != nil {
				s.cancel(err)
				return agentFailure(safeAgentError(err), true)
			}
			if len(s.accepted) > 0 {
				if err := s.spec.Checkpoint.finish(s.ctx, s.accepted, s.formatOnly); err != nil {
					s.cancel(err)
					return agentFailure(safeAgentError(err), true)
				}
			}
		}
		return response
	case "finish":
		if call.Round != s.round || len(call.Content) > 12000 || s.spec.Finish == nil {
			return fail("AGENT_CALLBACK_REJECTED")
		}
		accepted, err := s.spec.Finish(s.ctx, call.Content)
		if err != nil {
			response := agentFailure(safeAgentError(err), false)
			if s.spec.Phase == "diagnosis" && call.Round == s.budget.MaxModelCalls-1 && safeAgentError(err) == "AI_INVALID_REPORT" {
				s.formatOnly = true
				response["format_only"] = true
			}
			if s.spec.Checkpoint != nil {
				if persistErr := s.spec.Checkpoint.finish(s.ctx, nil, s.formatOnly); persistErr != nil {
					s.cancel(persistErr)
					return agentFailure(safeAgentError(persistErr), true)
				}
			}
			return response
		}
		if !json.Valid(accepted) {
			return fail("AGENT_CALLBACK_REJECTED")
		}
		s.accepted = append(json.RawMessage(nil), accepted...)
		if s.spec.Checkpoint != nil {
			if err := s.spec.Checkpoint.finish(s.ctx, s.accepted, s.formatOnly); err != nil {
				s.cancel(err)
				return agentFailure(safeAgentError(err), true)
			}
		}
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

// Run 为一次阶段生成随机 capability 和公开 run_id，启动来源栅栏轮询并调用独立 runner。
// 取消或撤权会关闭上游 context 并注销能力；返回时严格核对 accepted JSON、框架证明和独立计数，不能信任 Python 单方面宣称成功。
func (r *AgentRunner) Run(ctx context.Context, spec agentRunSpec) (json.RawMessage, *AgentRuntime, error) {
	if r == nil {
		return nil, nil, domain.Fail("AGENT_RUNTIME_UNAVAILABLE", "AgentScope未配置")
	}
	budget, valid := phaseBudget(spec.Phase)
	if !valid || spec.Invoke == nil || spec.Finish == nil {
		return nil, nil, domain.Fail("AGENT_RUNTIME_ERROR", "AgentScope阶段配置无效")
	}
	if spec.Budget != nil {
		requested := *spec.Budget
		if requested.MaxModelCalls < 1 || requested.MaxModelCalls > budget.MaxModelCalls || requested.MaxToolCalls < 1 || requested.MaxToolCalls > budget.MaxToolCalls || requested.MaxToolsPerRound < 1 || requested.MaxToolsPerRound > budget.MaxToolsPerRound || requested.TimeoutMS < 1 || requested.TimeoutMS > budget.TimeoutMS {
			return nil, nil, domain.Fail("AI_BUDGET_EXCEEDED", "AgentScope请求预算越界")
		}
		budget = requested
	}
	schemas, err := compileAgentTools(spec.Phase, spec.Tools)
	if err != nil {
		return nil, nil, err
	}
	var restored *agentCheckpointSnapshot
	if spec.Checkpoint != nil {
		encoded, _ := json.Marshal(map[string]any{"phase": spec.Phase, "model": spec.Model, "system_prompt": spec.SystemPrompt, "messages": spec.Messages, "tools": spec.Tools, "budget": budget})
		snapshot, loadErr := spec.Checkpoint.load(ctx, repository.DigestBytes(encoded), budget)
		if loadErr != nil {
			return nil, nil, loadErr
		}
		restored = &snapshot
		remaining := budget.TimeoutMS - snapshot.ActiveMS
		if remaining < 1 && len(snapshot.Accepted) == 0 {
			return nil, nil, agentCheckpointError("AI_BUDGET_EXCEEDED")
		}
		if remaining < 1 {
			remaining = 1000
		}
		if remaining < budget.TimeoutMS {
			budget.TimeoutMS = remaining
		}
	}
	work, cancel := context.WithTimeout(ctx, time.Duration(budget.TimeoutMS)*time.Millisecond)
	defer cancel()
	work, revoke := context.WithCancelCause(work)
	defer revoke(context.Canceled)
	if spec.Checkpoint != nil && len(restored.Accepted) == 0 {
		originalBudget, _ := phaseBudget(spec.Phase)
		if spec.Budget != nil {
			originalBudget = *spec.Budget
		}
		granted, reserveErr := spec.Checkpoint.reserveActive(work, originalBudget.TimeoutMS)
		if reserveErr != nil {
			return nil, nil, reserveErr
		}
		// 独立截止计时器与续片分开，数据库续片变慢不能让会话越过已经持久预留的时间。
		watchdog := time.AfterFunc(time.Duration(granted)*time.Millisecond, func() { revoke(context.DeadlineExceeded) })
		defer watchdog.Stop()
		go func() {
			reservedActive := restored.ActiveMS + granted
			for {
				if reservedActive >= originalBudget.TimeoutMS {
					return
				}
				wait := time.Duration(granted)*time.Millisecond - 250*time.Millisecond
				if wait < time.Millisecond {
					wait = time.Millisecond
				}
				timer := time.NewTimer(wait)
				select {
				case <-work.Done():
					timer.Stop()
					return
				case <-timer.C:
				}
				reserveCtx, stop := context.WithTimeout(work, 200*time.Millisecond)
				next, err := spec.Checkpoint.reserveActive(reserveCtx, originalBudget.TimeoutMS)
				stop()
				if err != nil {
					revoke(err)
					return
				}
				if !watchdog.Reset(time.Duration(next) * time.Millisecond) {
					revoke(context.DeadlineExceeded)
					return
				}
				granted = next
				reservedActive += next
			}
		}()
	}
	id, capability := uuid.NewString(), RandomToken()
	session := &agentSession{ctx: work, cancel: revoke, capability: capability, spec: spec, budget: budget, cache: map[string]cachedAgentCallback{}, endedRounds: map[int]bool{}, schemas: schemas}
	if restored != nil {
		session.round = restored.ModelCalls
		session.toolCalls = restored.ToolCalls
		session.roundTools = restored.RoundTools
		session.formatOnly = restored.FormatOnly
		session.accepted = append(json.RawMessage(nil), restored.Accepted...)
		for _, round := range restored.EndedRounds {
			session.endedRounds[round] = true
		}
	}
	r.mu.Lock()
	r.sessions[id] = session
	r.mu.Unlock()
	defer func() {
		revoke(context.Canceled)
		r.mu.Lock()
		delete(r.sessions, id)
		r.mu.Unlock()
		// 工作器收尾前，等待仍在执行的工具回调退出，避免终态落库后旧回调继续写入。
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
	payload := map[string]any{"version": 1, "run_id": id, "capability": capability, "phase": spec.Phase, "model": spec.Model, "system_prompt": spec.SystemPrompt, "messages": spec.Messages, "tools": spec.Tools, "budget": budget}
	if restored != nil {
		payload["checkpoint"] = restored
	}
	body, err := json.Marshal(payload)
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

// 私有 runner 失败时只返回固定的应用错误枚举。
// 不得保存响应正文、SDK 异常原文、任意枚举或额外字段；诊断字段也必须单独经过白名单校验。
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
		"AGENT_CHECKPOINT_REJECTED", "AGENT_CHECKPOINT_AMBIGUOUS", "AGENT_CHECKPOINT_TOO_LARGE", "AGENT_CHECKPOINT_STALE", "AGENT_PROVIDER_CIRCUIT_OPEN",
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

// compileAgentTools 把 Go 权威工具定义编译为独立 JSON Schema，确保 Python 和 Go 两侧都验证参数。
// 工具名称限定本阶段，不能从模型输出扩展；先转换 Go 容器类型再编译，避免 required 列表被误解。
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
		// JSON Schema 编译器只接受标准 JSON 值类型。Go 中手写的 required 使用 []string，
		// 必须先序列化再反序列化为 JSON 的 []any，才能正确编译 planning 与 repair 工具约束。
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
