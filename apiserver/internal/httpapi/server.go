// Package httpapi is Mini-Drop's only public backend surface.
//
// Browser requests terminate here. The package authenticates and authorizes
// them, persists task intent, delegates collection to C++ Control over gRPC,
// delegates diagnosis to the private Python worker, and streams durable events
// back to the browser. It deliberately does not run collectors itself.
package httpapi

import (
	"context"
	"crypto/rand"
	"crypto/subtle"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	grpc_health_v1 "google.golang.org/grpc/health/grpc_health_v1"
	"google.golang.org/grpc/metadata"
	"mini-drop/apiserver/internal/config"
	mini_drop "mini-drop/apiserver/internal/gen/mini_drop"
	"mini-drop/apiserver/internal/objectstore"
	"mini-drop/apiserver/internal/repository"
	"mini-drop/apiserver/internal/taskkind"
)

type principalContextKey struct{}

type requestPrincipal struct {
	ID           string
	Roles        []string
	AgentIDs     []string
	ServiceIDs   []string
	Environments []string
}

type Server struct {
	cfg             config.Config
	logger          *slog.Logger
	requestID       atomic.Uint64
	httpRequests    atomic.Uint64
	httpErrors      atomic.Uint64
	httpInFlight    atomic.Int64
	httpDurationUS  atomic.Uint64
	httpResponses   [6]atomic.Uint64
	repo            *repository.Postgres
	store           objectstore.Store
	control         mini_drop.ControlClient
	controlHealth   grpc_health_v1.HealthClient
	controlInitErr  error
	diagnosticAI    mini_drop.DiagnosticAIClient
	diagnosticAIErr error
}

func grpcTransportCredentials(cfg config.Config, serverName string) (credentials.TransportCredentials, error) {
	if !cfg.ControlGRPCTLS {
		return insecure.NewCredentials(), nil
	}
	if cfg.ControlGRPCCAFile == "" {
		return nil, errors.New("control gRPC CA file is required when TLS is enabled")
	}
	caPEM, err := os.ReadFile(cfg.ControlGRPCCAFile)
	if err != nil {
		return nil, fmt.Errorf("read control gRPC CA: %w", err)
	}
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(caPEM) {
		return nil, errors.New("control gRPC CA file does not contain a certificate")
	}
	tlsConfig := &tls.Config{
		MinVersion: tls.VersionTLS12,
		RootCAs:    roots,
		ServerName: serverName,
	}
	hasCert := cfg.ControlGRPCClientCertFile != ""
	hasKey := cfg.ControlGRPCClientKeyFile != ""
	if hasCert != hasKey {
		return nil, errors.New("control gRPC client certificate and key must be configured together")
	}
	if hasCert {
		certificate, err := tls.LoadX509KeyPair(
			cfg.ControlGRPCClientCertFile, cfg.ControlGRPCClientKeyFile,
		)
		if err != nil {
			return nil, fmt.Errorf("load control gRPC client certificate: %w", err)
		}
		tlsConfig.Certificates = []tls.Certificate{certificate}
	}
	return credentials.NewTLS(tlsConfig), nil
}

func New(cfg config.Config, logger *slog.Logger, repositories ...*repository.Postgres) http.Handler {
	s := &Server{
		cfg:    cfg,
		logger: logger,
	}
	if cfg.ControlGRPCAddress != "" {
		transportCredentials, credentialErr := grpcTransportCredentials(cfg, cfg.ControlGRPCServerName)
		if credentialErr != nil {
			s.controlInitErr = credentialErr
			logger.Error("control grpc credentials initialization failed", "error", credentialErr)
		} else {
			connection, err := grpc.NewClient(
				cfg.ControlGRPCAddress,
				grpc.WithTransportCredentials(transportCredentials),
			)
			if err != nil {
				s.controlInitErr = err
				logger.Error("control grpc client initialization failed", "error", err)
			} else {
				s.control = mini_drop.NewControlClient(connection)
				s.controlHealth = grpc_health_v1.NewHealthClient(connection)
			}
		}
	}
	if cfg.DiagnosticAIGRPCAddress != "" {
		transportCredentials, credentialErr := grpcTransportCredentials(cfg, cfg.DiagnosticAIGRPCServerName)
		if credentialErr != nil {
			s.diagnosticAIErr = credentialErr
			logger.Error("diagnostic AI gRPC credentials initialization failed", "error", credentialErr)
		} else {
			connection, err := grpc.NewClient(
				cfg.DiagnosticAIGRPCAddress,
				grpc.WithTransportCredentials(transportCredentials),
			)
			if err != nil {
				s.diagnosticAIErr = err
				logger.Error("diagnostic AI gRPC initialization failed", "error", err)
			} else {
				s.diagnosticAI = mini_drop.NewDiagnosticAIClient(connection)
			}
		}
	}
	if len(repositories) > 0 {
		s.repo = repositories[0]
		store, err := objectstore.NewWithSignerEndpoint(
			cfg.MinIOEndpoint, cfg.MinIOAgentEndpoint,
			cfg.MinIOAccessKey, cfg.MinIOSecretKey,
			cfg.MinIOSecure, cfg.MinIOAgentSecure,
		)
		if err != nil {
			logger.Error("object storage initialization failed", "error", err)
		} else {
			s.store = store
		}
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /livez", s.liveness)
	mux.HandleFunc("GET /readyz", s.readiness)
	mux.HandleFunc("GET /healthz", s.readiness)
	mux.HandleFunc("GET /api/healthz", s.readiness)
	mux.HandleFunc("POST /api/auth/session", s.createBrowserSession)
	mux.HandleFunc("DELETE /api/auth/session", s.deleteBrowserSession)
	mux.HandleFunc("GET /api/me", s.me)
	mux.HandleFunc("GET /api/metrics", s.metrics)
	mux.HandleFunc("GET /api/task-kinds", s.listTaskKinds)
	if s.repo != nil {
		mux.HandleFunc("GET /api/agents", s.listAgents)
		mux.HandleFunc("GET /api/top-processes", s.listTopProcesses)
		mux.HandleFunc("POST /api/tasks", s.createTask)
		mux.HandleFunc("GET /api/tasks", s.listTasks)
		mux.HandleFunc("GET /api/tasks/{task_id}", s.getTask)
		mux.HandleFunc("DELETE /api/tasks/{task_id}", s.deleteTask)
		mux.HandleFunc("POST /api/tasks/{task_id}/cancel", s.cancelTask)
		mux.HandleFunc("GET /api/tasks/{task_id}/events", s.getTaskEvents)
		mux.HandleFunc("GET /api/tasks/{task_id}/attempts", s.getTaskAttempts)
		mux.HandleFunc("GET /api/tasks/{task_id}/artifacts", s.getTaskArtifacts)
		mux.HandleFunc("GET /api/tasks/{task_id}/artifacts/{artifact_type}/content", s.getTaskArtifactContent)
		mux.HandleFunc("GET /api/tasks/{task_id}/artifacts/{artifact_type}/download", s.downloadTaskArtifact)
		mux.HandleFunc("GET /api/audit-logs", s.listAuditLogs)
		mux.HandleFunc("GET /api/events/stream", s.eventStream)
		// Schedules are now handled natively in Go (cron port + DB CRUD); the
		// Python reverse-proxy routes were removed below.
		(&scheduleHandlers{store: s.repo}).register(mux)
	}
	// Drop Insight V2 is the only AI diagnosis surface. Go remains the only
	// public HTTP server and invokes the Python diagnosis worker over private
	// gRPC. The durable SSE projection is served directly from PostgreSQL.
	mux.HandleFunc("GET /api/v2/diagnoses/{diagnosis_id}/events/stream", s.streamDropInsightEvents)
	mux.HandleFunc("/api/v2", s.invokeDiagnosticAI)
	mux.HandleFunc("/api/v2/{rest...}", s.invokeDiagnosticAI)
	return s.securityHeaders(s.accessLog(s.requestTrace(s.auth(mux))))
}

func browserSessionCookie(r *http.Request, value string, maxAge int) *http.Cookie {
	secure := r.TLS != nil || strings.EqualFold(
		strings.TrimSpace(r.Header.Get("X-Forwarded-Proto")), "https",
	)
	return &http.Cookie{
		Name:     "mini_drop_api_key",
		Value:    value,
		Path:     "/api",
		MaxAge:   maxAge,
		HttpOnly: true,
		Secure:   secure,
		SameSite: http.SameSiteStrictMode,
	}
}

// createBrowserSession converts an already authenticated API request into a
// short-lived, HttpOnly same-origin session. Native EventSource cannot attach
// X-API-Key, so SSE uses this cookie while ordinary REST requests keep using
// the explicit header.
func (s *Server) createBrowserSession(w http.ResponseWriter, r *http.Request) {
	provided := credentialFromRequest(r)
	if provided == "" || s.authenticatePrincipal(provided) == nil {
		writeAPI(w, http.StatusUnauthorized, 1401, "访问认证失败", nil)
		return
	}
	http.SetCookie(w, browserSessionCookie(r, provided, 8*60*60))
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{"expires_in": 8 * 60 * 60})
}

func (s *Server) deleteBrowserSession(w http.ResponseWriter, r *http.Request) {
	http.SetCookie(w, browserSessionCookie(r, "", -1))
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{"cleared": true})
}

func (s *Server) invokeDiagnosticAI(w http.ResponseWriter, r *http.Request) {
	if s.diagnosticAIErr != nil || s.diagnosticAI == nil {
		writeAPI(w, http.StatusServiceUnavailable, 1503, "AI 诊断 Worker 暂不可用", nil)
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 2<<20))
	if err != nil {
		writeAPI(w, http.StatusRequestEntityTooLarge, 1413, "AI 请求体过大", nil)
		return
	}
	principal := principalFromRequest(r)
	principalID := "local-anonymous"
	if principal != nil && strings.TrimSpace(principal.ID) != "" {
		principalID = principal.ID
	}
	ctx, cancel := context.WithTimeout(r.Context(), 180*time.Second)
	defer cancel()
	if token := strings.TrimSpace(s.cfg.ControlGRPCToken); token != "" {
		ctx = metadata.AppendToOutgoingContext(ctx, "x-mini-drop-grpc-token", token)
	}
	if requestID := strings.TrimSpace(r.Header.Get("X-Request-ID")); requestID != "" {
		ctx = metadata.AppendToOutgoingContext(ctx, "x-request-id", requestID)
	}
	if traceparent := strings.TrimSpace(r.Header.Get("Traceparent")); traceparent != "" {
		ctx = metadata.AppendToOutgoingContext(ctx, "traceparent", traceparent)
	}
	response, err := s.diagnosticAI.Invoke(ctx, &mini_drop.DiagnosticAIRequest{
		Method:    r.Method,
		Path:      strings.TrimPrefix(r.URL.Path, "/api/v2"),
		Query:     r.URL.RawQuery,
		BodyJson:  string(body),
		Principal: principalID,
	})
	if err != nil {
		s.logger.Error("diagnostic AI RPC failed", "path", r.URL.Path, "error", err)
		writeAPI(w, http.StatusBadGateway, 1502, "AI 诊断 Worker 调用失败", nil)
		return
	}
	status := int(response.GetStatusCode())
	if status < 100 || status > 599 {
		status = http.StatusBadGateway
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("X-Mini-Drop-AI-Transport", "grpc")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, response.GetBodyJson())
}

func (s *Server) streamDropInsightEvents(w http.ResponseWriter, r *http.Request) {
	if s.repo == nil {
		writeAPI(w, http.StatusServiceUnavailable, 1503, "数据库暂时不可用", nil)
		return
	}
	diagnosisID := strings.TrimSpace(r.PathValue("diagnosis_id"))
	if diagnosisID == "" || len(diagnosisID) > 128 {
		writeAPI(w, http.StatusBadRequest, 1400, "诊断 ID 不合法", nil)
		return
	}
	after, _ := strconv.ParseInt(r.URL.Query().Get("after"), 10, 64)
	if header := strings.TrimSpace(r.Header.Get("Last-Event-ID")); header != "" {
		if value, err := strconv.ParseInt(header, 10, 64); err == nil && value > after {
			after = value
		}
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeAPI(w, http.StatusInternalServerError, 1500, "当前连接不支持事件流", nil)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Accel-Buffering", "no")
	_, _ = io.WriteString(w, ":connected\n\n")
	flusher.Flush()
	ticker := time.NewTicker(time.Second)
	keepalive := time.NewTicker(20 * time.Second)
	defer ticker.Stop()
	defer keepalive.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
			events, err := s.repo.ListDropInsightEventsAfter(r.Context(), diagnosisID, after, 200)
			if err != nil {
				s.logger.Warn("drop insight SSE poll failed", "diagnosis_id", diagnosisID, "error", err)
				continue
			}
			for _, event := range events {
				after = event.Sequence
				data, _ := json.Marshal(event.Payload)
				fmt.Fprintf(w, "id: %d\nevent: diagnosis_progress\ndata: %s\n\n", event.Sequence, data)
			}
			if len(events) > 0 {
				flusher.Flush()
			}
		case <-keepalive.C:
			_, _ = io.WriteString(w, ":keepalive\n\n")
			flusher.Flush()
		}
	}
}

type createTaskRequest struct {
	Name           string              `json:"name"`
	AgentID        string              `json:"agent_id"`
	TargetPID      int                 `json:"target_pid"`
	CollectorType  string              `json:"collector_type"`
	SampleRate     int                 `json:"sample_rate"`
	DurationSec    int                 `json:"duration_sec"`
	Options        map[string]any      `json:"options"`
	ResourceBudget *taskResourceBudget `json:"resource_budget,omitempty"`
}

type taskResourceBudget struct {
	MaxCPUPercent  int `json:"max_cpu_percent"`
	MaxMemoryMB    int `json:"max_memory_mb"`
	MaxOutputMB    int `json:"max_output_mb"`
	MaxDurationSec int `json:"max_duration_sec"`
}

type cancelTaskRequest struct {
	Reason string `json:"reason"`
}

const (
	idempotencyKeyMinLen = 8
	idempotencyKeyMaxLen = 128
)

// readIdempotencyKey returns the normalized Idempotency-Key header value, or
// an error when the header is present but violates the format contract.
func readIdempotencyKey(r *http.Request) (string, error) {
	key := strings.TrimSpace(r.Header.Get("Idempotency-Key"))
	if key == "" {
		return "", nil
	}
	return key, validateIdempotencyKey(key)
}

func validateIdempotencyKey(key string) error {
	if len(key) < idempotencyKeyMinLen || len(key) > idempotencyKeyMaxLen {
		return errors.New("idempotency key length out of range")
	}
	if strings.ContainsAny(key, " \t\r\n") {
		return errors.New("idempotency key must not contain whitespace")
	}
	return nil
}

func normalizeTaskResourceBudget(
	budget *taskResourceBudget,
	durationSec int,
	maxDurationSec int,
) (*taskResourceBudget, error) {
	if budget == nil {
		budget = &taskResourceBudget{}
	}
	normalized := *budget
	if normalized.MaxCPUPercent == 0 {
		normalized.MaxCPUPercent = 50
	}
	if normalized.MaxMemoryMB == 0 {
		normalized.MaxMemoryMB = 1024
	}
	if normalized.MaxOutputMB == 0 {
		normalized.MaxOutputMB = 256
	}
	if normalized.MaxDurationSec == 0 {
		normalized.MaxDurationSec = durationSec
	}
	if normalized.MaxCPUPercent < 1 || normalized.MaxCPUPercent > 100 {
		return nil, errors.New("max_cpu_percent 必须在 1 到 100 之间")
	}
	if normalized.MaxMemoryMB < 64 || normalized.MaxMemoryMB > 65536 {
		return nil, errors.New("max_memory_mb 必须在 64 到 65536 之间")
	}
	if normalized.MaxOutputMB < 1 || normalized.MaxOutputMB > 4096 {
		return nil, errors.New("max_output_mb 必须在 1 到 4096 之间")
	}
	if normalized.MaxDurationSec < durationSec || normalized.MaxDurationSec > maxDurationSec {
		return nil, errors.New("max_duration_sec 必须覆盖采集时长且不超过 TaskKind 上限")
	}
	return &normalized, nil
}

func optionString(options map[string]any, name, fallback string) string {
	value, ok := options[name].(string)
	if !ok || strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func optionInt(options map[string]any, name string, fallback int) int {
	switch value := options[name].(type) {
	case float64:
		return int(value)
	case int:
		return value
	default:
		return fallback
	}
}

func optionBool(options map[string]any, name string) bool {
	value, _ := options[name].(bool)
	return value
}

func applyTypedTaskPayload(desc *mini_drop.TaskDesc, input createTaskRequest) {
	pid := int32(input.TargetPID)
	duration := uint32(input.DurationSec)
	hz := uint32(input.SampleRate)
	switch input.CollectorType {
	case "perf_cpu":
		desc.Payload = &mini_drop.TaskDesc_Perf{Perf: &mini_drop.PerfTask{
			Pid: pid, Hz: hz, DurationSec: duration,
			Callgraph:  optionString(input.Options, "callgraph", "fp"),
			Event:      optionString(input.Options, "event", "cpu-cycles"),
			Subprocess: optionBool(input.Options, "subprocess"),
		}}
	case "java_async":
		desc.Payload = &mini_drop.TaskDesc_AsyncProfiler{AsyncProfiler: &mini_drop.AsyncProfilerTask{
			Pid: pid, DurationSec: duration,
			Event: optionString(input.Options, "event", "cpu"),
		}}
	case "go_pprof":
		desc.Payload = &mini_drop.TaskDesc_Pprof{Pprof: &mini_drop.PprofTask{
			Pid: pid, DurationSec: duration,
			Endpoint: optionString(input.Options, "pprof_url", "http://go-hotspot:6060/debug/pprof/profile"),
		}}
	case "ebpf_io":
		desc.Payload = &mini_drop.TaskDesc_Ebpf{Ebpf: &mini_drop.EbpfTask{
			Pid: pid, DurationSec: duration,
			Device: optionString(input.Options, "device", ""),
		}}
	case "pyspy":
		desc.Payload = &mini_drop.TaskDesc_Pyspy{Pyspy: &mini_drop.PySpyTask{
			Pid: pid, Hz: hz, DurationSec: duration,
			Subprocess: optionBool(input.Options, "subprocess"),
		}}
	case "memory_smaps":
		desc.Payload = &mini_drop.TaskDesc_MemorySmaps{MemorySmaps: &mini_drop.MemorySmapsTask{
			Pid: pid, DurationSec: duration,
			IntervalMs: uint32(optionInt(input.Options, "interval_ms", 1000)),
		}}
	case "sys_metrics":
		desc.Payload = &mini_drop.TaskDesc_SystemMetrics{SystemMetrics: &mini_drop.SystemMetricsTask{
			Pid: pid, DurationSec: duration,
			IntervalMs: uint32(optionInt(input.Options, "interval_ms", 1000)),
		}}
	case "continuous_perf":
		desc.Payload = &mini_drop.TaskDesc_ContinuousPerf{ContinuousPerf: &mini_drop.ContinuousPerfTask{
			Pid: pid, Hz: hz, DurationSec: duration,
			WindowSeconds:             uint32(optionInt(input.Options, "window_seconds", input.DurationSec)),
			Callgraph:                 optionString(input.Options, "callgraph", "fp"),
			Event:                     optionString(input.Options, "event", "cpu-cycles"),
			TriggerCpuPercent:         uint32(optionInt(input.Options, "trigger_cpu_percent", 0)),
			TriggerConsecutiveSamples: uint32(optionInt(input.Options, "trigger_consecutive_samples", 3)),
			TriggerWaitSeconds:        uint32(optionInt(input.Options, "trigger_wait_seconds", 0)),
			RetentionTier:             optionString(input.Options, "retention_tier", "standard"),
		}}
	}
}

func (s *Server) createTask(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if !requireAnyRole(w, principal, "operator", "admin") {
		return
	}
	idempotencyKey, idemErr := readIdempotencyKey(r)
	if idemErr != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "Idempotency-Key 格式不合法", nil)
		return
	}
	var input createTaskRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "任务参数不是有效 JSON", nil)
		return
	}
	input.Name = strings.TrimSpace(input.Name)
	input.AgentID = strings.TrimSpace(input.AgentID)
	input.CollectorType = strings.TrimSpace(input.CollectorType)
	if input.Name == "" || input.AgentID == "" || input.CollectorType == "" {
		writeAPI(w, http.StatusBadRequest, 1400, "任务名称、Agent 和采集器不能为空", nil)
		return
	}
	if !scopeAllows(principal.AgentIDs, input.AgentID) {
		writeAPI(w, http.StatusForbidden, 1403, "agent is outside the principal resource scope", nil)
		return
	}
	kind, supportedKind := taskkind.Lookup(input.CollectorType)
	if !supportedKind {
		writeAPI(w, http.StatusBadRequest, 1400, "未知或未启用的采集器", nil)
		return
	}
	agentCapability, err := s.repo.AgentSupportsCollector(
		r.Context(), input.AgentID, input.CollectorType,
	)
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	if !agentCapability.Exists {
		writeAPI(w, http.StatusNotFound, 1404, "目标 Agent 不存在", nil)
		return
	}
	if !agentCapability.Online {
		writeAPI(w, http.StatusConflict, 1409, "目标 Agent 当前不在线", nil)
		return
	}
	if !agentCapability.Supported {
		writeAPI(w, http.StatusConflict, 1409, "目标 Agent 不支持该采集器", nil)
		return
	}
	if input.TargetPID < 1 || input.TargetPID > 4194304 {
		writeAPI(w, http.StatusBadRequest, 1400, "target_pid 超出有效范围", nil)
		return
	}
	processBinding, err := s.repo.ResolveFreshProcessCandidate(
		r.Context(), input.AgentID, input.TargetPID,
		time.Duration(max(s.cfg.ProcessSnapshotMaxAgeSec, 1))*time.Second,
	)
	if errors.Is(err, repository.ErrTargetUnavailable) {
		writeAPI(w, http.StatusConflict, 1409, "目标 PID 不在 Agent 的最新可信进程快照中，请刷新后重试", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	if input.SampleRate == 0 {
		input.SampleRate = kind.DefaultSampleRate
	}
	if input.DurationSec == 0 {
		input.DurationSec = kind.DefaultDurationSec
	}
	if input.SampleRate < 1 || input.SampleRate > kind.MaxSampleRate ||
		input.DurationSec < 1 || input.DurationSec > kind.MaxDurationSec {
		writeAPI(w, http.StatusBadRequest, 1400, "采样率或采样时长超出策略范围", nil)
		return
	}
	if input.Options == nil {
		input.Options = map[string]any{}
	}
	if err := taskkind.ValidateOptions(kind, input.Options); err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "采集器 options 不符合参数契约: "+err.Error(), nil)
		return
	}
	budget, err := normalizeTaskResourceBudget(input.ResourceBudget, input.DurationSec, kind.MaxDurationSec)
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "resource_budget 不符合策略: "+err.Error(), nil)
		return
	}
	input.ResourceBudget = budget
	taskID, replayed, err := s.repo.CreateTask(r.Context(), repository.CreateTask{
		Name: input.Name, AgentID: input.AgentID, TargetPID: input.TargetPID,
		CollectorType: input.CollectorType, SampleRate: input.SampleRate,
		DurationSec: input.DurationSec, Options: input.Options,
		ResourceBudget: map[string]any{
			"max_cpu_percent":  budget.MaxCPUPercent,
			"max_memory_mb":    budget.MaxMemoryMB,
			"max_output_mb":    budget.MaxOutputMB,
			"max_duration_sec": budget.MaxDurationSec,
		},
		CreatorID: principal.ID, IdempotencyKey: idempotencyKey,
		ProcessBinding: processBinding,
		TraceParent:    r.Header.Get("Traceparent"),
		TraceID:        r.Header.Get("X-Trace-ID"),
	})
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "目标 Agent 不存在", nil)
		return
	}
	if errors.Is(err, repository.ErrIdempotencyConflict) {
		writeAPI(w, http.StatusConflict, 1409, "Idempotency-Key 已用于不同参数的请求", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	dispatchStatus := "PENDING"
	shouldIssueUploadAuthorization := true
	if replayed {
		persisted, getErr := s.repo.GetTask(r.Context(), taskID)
		if getErr != nil {
			s.databaseError(w, r, getErr)
			return
		}
		if status, ok := persisted["status"].(string); ok && status != "" {
			dispatchStatus = status
			shouldIssueUploadAuthorization = status == "PENDING"
		}
	}
	if shouldIssueUploadAuthorization {
		err = s.issueTaskUploadAuthorizations(
			r.Context(), taskID, kind, input.DurationSec,
		)
	}
	if err != nil {
		_ = s.repo.RecordControlCommand(
			r.Context(), "TASK_UPLOAD_AUTHORIZATION_FAILED",
			"任务已持久化，但短时上传授权生成失败",
			map[string]any{"task_id": taskID, "error": err.Error()},
		)
		writeAPI(w, http.StatusServiceUnavailable, 1503,
			"任务已持久化，但短时上传授权生成失败；请使用相同 Idempotency-Key 重试",
			map[string]any{"task_id": taskID, "status": "PENDING", "retryable": true},
		)
		return
	}
	shouldDispatch := !replayed || dispatchStatus == "PENDING"
	if shouldDispatch && s.controlInitErr != nil {
		writeAPI(w, http.StatusServiceUnavailable, 1503, "C++ 控制面客户端初始化失败", map[string]any{
			"task_id": taskID, "status": "PENDING", "retryable": true,
		})
		return
	}
	if shouldDispatch && s.control != nil {
		ctx, cancel := context.WithTimeout(
			r.Context(),
			time.Duration(max(s.cfg.ControlGRPCTimeoutMS, 1))*time.Millisecond,
		)
		defer cancel()
		if s.cfg.ControlGRPCToken != "" {
			ctx = metadata.AppendToOutgoingContext(
				ctx, "x-mini-drop-grpc-token", s.cfg.ControlGRPCToken,
			)
		}
		if requestID := r.Header.Get("X-Request-ID"); requestID != "" {
			ctx = metadata.AppendToOutgoingContext(ctx, "x-request-id", requestID)
		}
		if traceparent := r.Header.Get("Traceparent"); traceparent != "" {
			ctx = metadata.AppendToOutgoingContext(ctx, "traceparent", traceparent)
		}
		desc := &mini_drop.TaskDesc{
			TaskId:       taskID,
			ProfilerType: mini_drop.TaskKindProfiler(kind.ProfilerType),
			SampleArgv: &mini_drop.RecordArgv{
				Hz:       uint32(input.SampleRate),
				Duration: uint64(input.DurationSec),
				Pid:      int32(input.TargetPID),
			},
			TimeoutSec: uint32(input.DurationSec + 30),
			ResourceBudget: &mini_drop.ResourceBudget{
				MaxCpuPercent:  uint32(budget.MaxCPUPercent),
				MaxMemoryMb:    uint32(budget.MaxMemoryMB),
				MaxOutputMb:    uint32(budget.MaxOutputMB),
				MaxDurationSec: uint32(budget.MaxDurationSec),
			},
		}
		applyTypedTaskPayload(desc, input)
		response, dispatchErr := s.control.CreateTask(ctx, &mini_drop.CreateTaskRequest{
			TargetIp: input.AgentID,
			TaskId:   taskID,
			TaskDesc: desc,
		})
		if dispatchErr != nil {
			_ = s.repo.RecordControlCommand(
				r.Context(), "TASK_DISPATCH_DEFERRED", "C++ 控制面暂未确认任务",
				map[string]any{"task_id": taskID, "error": dispatchErr.Error()},
			)
			writeAPI(w, http.StatusServiceUnavailable, 1503, "任务已持久化，但 C++ 控制面暂未确认，请使用相同 Idempotency-Key 重试", map[string]any{
				"task_id": taskID, "status": "PENDING", "retryable": true,
			})
			return
		}
		dispatchStatus = response.GetStatus()
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"task_id": taskID, "status": dispatchStatus, "served_by": "go-apiserver", "replayed": replayed,
	})
}

func (s *Server) issueTaskUploadAuthorizations(
	ctx context.Context, taskID string, kind taskkind.Kind, durationSec int,
) error {
	if s.store == nil {
		return errors.New("object storage is unavailable")
	}
	ttlSeconds := max(s.cfg.MinIOUploadAuthTTLSeconds, durationSec+300)
	// MinIO/S3 presigned operations are intentionally bounded. The default is
	// 30 minutes and the hard cap prevents configuration drift into long-lived
	// credentials while still covering the longest supported collection.
	ttlSeconds = min(ttlSeconds, 3600)
	expiresAt := time.Now().UTC().Add(time.Duration(ttlSeconds) * time.Second)
	attemptID, err := newTaskAttemptID()
	if err != nil {
		return err
	}
	items := make([]repository.TaskUploadAuthorization, 0, len(kind.ArtifactFilenames))
	for _, filename := range kind.ArtifactFilenames {
		attemptPrefix := "tasks/" + taskID + "/attempts/" + attemptID + "/"
		objectKey := attemptPrefix + "raw/" + filename
		if filename == "manifest.json" {
			objectKey = attemptPrefix + filename
		}
		putURL, err := s.store.PresignPut(
			ctx, s.cfg.MinIOBucket, objectKey, time.Duration(ttlSeconds)*time.Second,
		)
		if err != nil {
			return fmt.Errorf("presign %s: %w", filename, err)
		}
		items = append(items, repository.TaskUploadAuthorization{
			TaskAttemptID: attemptID, ObjectKey: objectKey,
			PutURL: putURL.String(), ExpiresAt: expiresAt,
		})
	}
	return s.repo.ReplaceTaskUploadAuthorizations(ctx, taskID, items)
}

func newTaskAttemptID() (string, error) {
	var entropy [16]byte
	if _, err := rand.Read(entropy[:]); err != nil {
		return "", fmt.Errorf("generate task attempt id: %w", err)
	}
	return "attempt_" + hex.EncodeToString(entropy[:]), nil
}

func (s *Server) listAgents(w http.ResponseWriter, r *http.Request) {
	limit, offset := repository.ParsePage(r.URL.Query().Get("limit"), r.URL.Query().Get("offset"))
	queryPage := repository.Page{Limit: limit, Offset: offset}
	principal := principalFromRequest(r)
	if principal != nil && !scopeAllows(principal.AgentIDs, "*") {
		queryPage = repository.Page{Limit: 1000, Offset: 0}
	}
	items, total, err := s.repo.ListAgents(r.Context(), queryPage)
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	if queryPage.Limit == 1000 {
		items, total = filterResourceItems(items, "id", principal.AgentIDs, limit, offset)
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"items": items, "total": total, "offset": offset, "limit": limit,
	})
}

func (s *Server) listTaskKinds(w http.ResponseWriter, _ *http.Request) {
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"items": taskkind.List(), "served_by": "go-apiserver",
	})
}

func parseProcessCandidateLimit(value string) int {
	limit, err := strconv.Atoi(value)
	if err != nil || limit < 1 {
		return 20
	}
	if limit > 100 {
		return 100
	}
	return limit
}

func (s *Server) listTopProcesses(w http.ResponseWriter, r *http.Request) {
	agentID := strings.TrimSpace(r.URL.Query().Get("agent_id"))
	if agentID == "" || len(agentID) > 128 {
		writeAPI(w, http.StatusBadRequest, 1400, "agent_id is required", nil)
		return
	}
	principal := principalFromRequest(r)
	if principal == nil || !scopeAllows(principal.AgentIDs, agentID) {
		writeAPI(w, http.StatusNotFound, 1404, "Agent 不存在", nil)
		return
	}
	maxAge := time.Duration(s.cfg.ProcessSnapshotMaxAgeSec) * time.Second
	if maxAge <= 0 {
		maxAge = 30 * time.Second
	}
	snapshot, err := s.repo.LatestProcessCandidates(
		r.Context(), agentID,
		parseProcessCandidateLimit(r.URL.Query().Get("limit")), maxAge,
	)
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	items := snapshot.Items
	if !snapshot.Authoritative || !snapshot.Fresh {
		items = []map[string]any{}
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"items": items, "agent_id": agentID,
		"snapshot_id": snapshot.SnapshotID, "snapshot_state": snapshot.State,
		"snapshot_received_at": snapshot.ReceivedAt,
		"authoritative":        snapshot.Authoritative, "fresh": snapshot.Fresh,
		"served_by": "go-apiserver",
	})
}

func (s *Server) listTasks(w http.ResponseWriter, r *http.Request) {
	limit, offset := repository.ParsePage(r.URL.Query().Get("limit"), r.URL.Query().Get("offset"))
	queryPage := repository.Page{
		Limit: limit, Offset: offset, Search: r.URL.Query().Get("search"),
		SortBy: r.URL.Query().Get("sort_by"), SortOrder: r.URL.Query().Get("sort_order"),
	}
	principal := principalFromRequest(r)
	if principal != nil && !scopeAllows(principal.AgentIDs, "*") {
		queryPage.Limit, queryPage.Offset = 1000, 0
	}
	items, total, err := s.repo.ListTasks(r.Context(), queryPage)
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	if queryPage.Limit == 1000 && (limit != 1000 || offset != 0) {
		items, total = filterResourceItems(items, "agent_id", principal.AgentIDs, limit, offset)
	} else if principal != nil && !scopeAllows(principal.AgentIDs, "*") {
		items, total = filterResourceItems(items, "agent_id", principal.AgentIDs, limit, offset)
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"items": items, "total": total, "offset": offset, "limit": limit,
		"served_by": "go-apiserver",
	})
}

func parseBoundedPage(r *http.Request, maxLimit int) (int, int) {
	limit, err := strconv.Atoi(r.URL.Query().Get("limit"))
	if err != nil || limit < 1 {
		limit = 100
	}
	offset, err := strconv.Atoi(r.URL.Query().Get("offset"))
	if err != nil || offset < 0 {
		offset = 0
	}
	if limit > maxLimit {
		limit = maxLimit
	}
	return limit, offset
}

func (s *Server) getTask(w http.ResponseWriter, r *http.Request) {
	item, err := s.repo.GetTask(r.Context(), r.PathValue("task_id"))
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	if !taskItemAllowed(principalFromRequest(r), item) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在", nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", item)
}

func (s *Server) deleteTask(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeTaskResource(w, r, r.PathValue("task_id")) {
		return
	}
	item, err := s.repo.DeleteTask(
		r.Context(), r.PathValue("task_id"), r.URL.Query().Get("reason"),
	)
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在或已经归档", nil)
		return
	}
	if errors.Is(err, repository.ErrConflict) {
		writeAPI(w, http.StatusConflict, 1409, "运行中的任务请先取消或等待结束", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", item)
}

func (s *Server) cancelTask(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeTaskResource(w, r, r.PathValue("task_id")) {
		return
	}
	var input cancelTaskRequest
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10))
	if err := decoder.Decode(&input); err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "取消原因不是有效 JSON", nil)
		return
	}
	item, err := s.repo.CancelTask(r.Context(), r.PathValue("task_id"), input.Reason)
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在", nil)
		return
	}
	if errors.Is(err, repository.ErrConflict) {
		writeAPI(w, http.StatusConflict, 1409, "当前任务状态不允许取消", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", item)
}

func (s *Server) getTaskEvents(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeTaskResource(w, r, r.PathValue("task_id")) {
		return
	}
	items, err := s.repo.ListTaskEvents(r.Context(), r.PathValue("task_id"))
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", items)
}

func (s *Server) getTaskAttempts(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeTaskResource(w, r, r.PathValue("task_id")) {
		return
	}
	items, err := s.repo.ListTaskAttempts(r.Context(), r.PathValue("task_id"))
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", items)
}

func (s *Server) getTaskArtifacts(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeTaskResource(w, r, r.PathValue("task_id")) {
		return
	}
	items, err := s.repo.ListTaskArtifacts(r.Context(), r.PathValue("task_id"))
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", items)
}

const maxArtifactContentBytes int64 = 16 << 20

func (s *Server) getTaskArtifactContent(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeTaskResource(w, r, r.PathValue("task_id")) {
		return
	}
	artifact, ok := s.resolveTaskArtifact(w, r)
	if !ok {
		return
	}
	if artifact.ObjectKey == "" && artifact.LocalPath != "" {
		writeAPI(w, http.StatusUnprocessableEntity, 1422, "旧版本地制品未迁移到对象存储", nil)
		return
	}
	bucket, key, err := s.validateArtifactLocation(artifact)
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, err.Error(), nil)
		return
	}
	if s.store == nil {
		writeAPI(w, http.StatusServiceUnavailable, 1503, "对象存储暂时不可用", nil)
		return
	}
	object, err := s.store.Open(r.Context(), bucket, key)
	if err != nil {
		s.artifactStorageError(w, r, err)
		return
	}
	defer object.Body.Close()
	if object.Size > maxArtifactContentBytes {
		writeAPI(w, http.StatusRequestEntityTooLarge, 1413, "产物过大，请使用下载接口", nil)
		return
	}
	payload, err := io.ReadAll(io.LimitReader(object.Body, maxArtifactContentBytes+1))
	if err != nil {
		s.artifactStorageError(w, r, err)
		return
	}
	if int64(len(payload)) > maxArtifactContentBytes {
		writeAPI(w, http.StatusRequestEntityTooLarge, 1413, "产物过大，请使用下载接口", nil)
		return
	}
	if strings.HasSuffix(artifact.ArtifactType, "_json") || artifact.ContentType == "application/json" {
		var value any
		if err := json.Unmarshal(payload, &value); err != nil {
			writeAPI(w, http.StatusUnprocessableEntity, 1422, "JSON 产物格式不合法", nil)
			return
		}
		writeAPI(w, http.StatusOK, 0, "ok", value)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{"text": string(payload)})
}

func (s *Server) downloadTaskArtifact(w http.ResponseWriter, r *http.Request) {
	if !s.authorizeTaskResource(w, r, r.PathValue("task_id")) {
		return
	}
	artifact, ok := s.resolveTaskArtifact(w, r)
	if !ok {
		return
	}
	if artifact.ObjectKey == "" && artifact.LocalPath != "" {
		writeAPI(w, http.StatusUnprocessableEntity, 1422, "旧版本地制品未迁移到对象存储", nil)
		return
	}
	bucket, key, err := s.validateArtifactLocation(artifact)
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, err.Error(), nil)
		return
	}
	if s.store == nil {
		writeAPI(w, http.StatusServiceUnavailable, 1503, "对象存储暂时不可用", nil)
		return
	}
	object, err := s.store.Open(r.Context(), bucket, key)
	if err != nil {
		s.artifactStorageError(w, r, err)
		return
	}
	defer object.Body.Close()
	filename := safeDownloadFilename(artifact.Filename)
	if filename == "artifact.bin" {
		filename = safeDownloadFilename(path.Base(key))
	}
	contentType := artifact.ContentType
	if contentType == "" {
		contentType = object.ContentType
	}
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Content-Disposition", "attachment; filename*=UTF-8''"+url.PathEscape(filename))
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if object.Size >= 0 {
		w.Header().Set("Content-Length", strconv.FormatInt(object.Size, 10))
	}
	w.WriteHeader(http.StatusOK)
	if _, err := io.Copy(w, object.Body); err != nil {
		s.logger.Warn("artifact stream interrupted", "request_id", r.Header.Get("X-Request-ID"), "error", err)
	}
}

func (s *Server) resolveTaskArtifact(w http.ResponseWriter, r *http.Request) (repository.Artifact, bool) {
	var windowIndex *int
	if raw := strings.TrimSpace(r.URL.Query().Get("index")); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 0 {
			writeAPI(w, http.StatusBadRequest, 1400, "index 必须是非负整数", nil)
			return repository.Artifact{}, false
		}
		windowIndex = &parsed
	}
	artifact, err := s.repo.GetTaskArtifact(
		r.Context(), r.PathValue("task_id"), r.PathValue("artifact_type"), windowIndex,
	)
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "任务或产物不存在", nil)
		return repository.Artifact{}, false
	}
	if err != nil {
		s.databaseError(w, r, err)
		return repository.Artifact{}, false
	}
	return artifact, true
}

func (s *Server) validateArtifactLocation(artifact repository.Artifact) (string, string, error) {
	bucket := artifact.Bucket
	if bucket == "" {
		bucket = s.cfg.MinIOBucket
	}
	if bucket != s.cfg.MinIOBucket {
		return "", "", errors.New("bucket 不在允许范围内")
	}
	key := strings.ReplaceAll(strings.TrimSpace(artifact.ObjectKey), "\\", "/")
	if key == "" {
		return "", "", errors.New("对象存储 key 不能为空")
	}
	parts := strings.Split(key, "/")
	for _, part := range parts {
		if part == "" || part == "." || part == ".." {
			return "", "", errors.New("对象存储 key 路径不合法")
		}
	}
	expectedPrefix := "tasks/" + artifact.TaskID + "/"
	if !strings.HasPrefix(key, expectedPrefix) {
		return "", "", errors.New("对象存储 key 不属于当前任务")
	}
	return bucket, key, nil
}

func safeDownloadFilename(value string) string {
	value = path.Base(strings.ReplaceAll(value, "\\", "/"))
	var b strings.Builder
	for _, r := range value {
		if r >= 32 && r != 127 && r != '"' && r != ';' {
			b.WriteRune(r)
		}
	}
	result := b.String()
	if len(result) > 255 {
		result = result[:255]
	}
	if result == "" || result == "." {
		return "artifact.bin"
	}
	return result
}

func (s *Server) artifactStorageError(w http.ResponseWriter, r *http.Request, err error) {
	s.logger.Warn("artifact object read failed", "request_id", r.Header.Get("X-Request-ID"), "path", r.URL.Path, "error", err)
	writeAPI(w, http.StatusNotFound, 1404, "对象存储产物不存在", nil)
}

func (s *Server) listAuditLogs(w http.ResponseWriter, r *http.Request) {
	limit, offset := repository.ParsePage(r.URL.Query().Get("limit"), r.URL.Query().Get("offset"))
	items, total, err := s.repo.ListAuditLogs(
		r.Context(), repository.Page{Limit: limit, Offset: offset},
	)
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"items": items, "total": total, "offset": offset, "limit": limit,
		"served_by": "go-apiserver",
	})
}

func (s *Server) eventStream(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeAPI(w, http.StatusInternalServerError, 1500, "当前连接不支持事件流", nil)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")

	taskCursor, auditCursor := int64(0), int64(0)
	if raw := strings.TrimSpace(r.Header.Get("Last-Event-ID")); raw != "" {
		taskCursor, auditCursor = parseEventCursor(raw)
	} else if raw := strings.TrimSpace(r.URL.Query().Get("since")); raw != "" {
		taskCursor, auditCursor = parseEventCursor(raw)
	} else {
		var err error
		taskCursor, err = s.repo.LatestStatusEventID(r.Context())
		if err == nil {
			auditCursor, err = s.repo.LatestAuditEventID(r.Context())
		}
		if err != nil {
			s.databaseError(w, r, err)
			return
		}
	}

	fmt.Fprint(w, "retry: 3000\n: connected to go-apiserver\n\n")
	flusher.Flush()
	pollTicker := time.NewTicker(time.Second)
	keepaliveTicker := time.NewTicker(15 * time.Second)
	defer pollTicker.Stop()
	defer keepaliveTicker.Stop()

	for {
		select {
		case <-r.Context().Done():
			return
		case <-keepaliveTicker.C:
			fmt.Fprint(w, ": keepalive\n\n")
			flusher.Flush()
		case <-pollTicker.C:
			wrote := false
			events, err := s.repo.ListStatusEventsAfter(r.Context(), taskCursor, 200)
			if err != nil {
				s.logger.Error("sse task poll failed", "error", err, "cursor", taskCursor)
				fmt.Fprint(w, "event: server_error\ndata: {\"message\":\"database poll failed\"}\n\n")
				flusher.Flush()
				continue
			}
			for _, event := range events {
				data, _ := json.Marshal(map[string]any{
					"task_id": event.TaskID, "from_status": event.FromStatus,
					"to_status": event.ToStatus, "reason": event.Reason,
					"actor": event.Actor, "metadata": event.Metadata,
				})
				taskCursor = event.ID
				fmt.Fprintf(w, "id: %s\nevent: task_changed\ndata: %s\n\n",
					formatEventCursor(taskCursor, auditCursor), data)
				wrote = true
			}

			auditEvents, err := s.repo.ListAuditEventsAfter(r.Context(), auditCursor, 200)
			if err != nil {
				s.logger.Error("sse audit poll failed", "error", err, "cursor", auditCursor)
				continue
			}
			for _, event := range auditEvents {
				auditCursor = event.ID
				if event.EventType != "AGENT_ONLINE" && event.EventType != "AGENT_OFFLINE" {
					continue
				}
				status := "OFFLINE"
				if event.EventType == "AGENT_ONLINE" {
					status = "ONLINE"
				}
				data, _ := json.Marshal(map[string]any{
					"agent_id": event.AgentID, "status": status,
					"message": event.Message, "metadata": event.Metadata,
				})
				fmt.Fprintf(w, "id: %s\nevent: agent_status\ndata: %s\n\n",
					formatEventCursor(taskCursor, auditCursor), data)
				wrote = true
			}
			if wrote {
				flusher.Flush()
			}
		}
	}
}

func parseEventCursor(raw string) (int64, int64) {
	if value, err := strconv.ParseInt(raw, 10, 64); err == nil {
		return value, 0
	}
	var taskID, auditID int64
	for _, part := range strings.Split(raw, ";") {
		keyValue := strings.SplitN(part, ":", 2)
		if len(keyValue) != 2 {
			continue
		}
		value, _ := strconv.ParseInt(keyValue[1], 10, 64)
		switch keyValue[0] {
		case "t":
			taskID = value
		case "a":
			auditID = value
		}
	}
	return taskID, auditID
}

func formatEventCursor(taskID, auditID int64) string {
	return fmt.Sprintf("t:%d;a:%d", taskID, auditID)
}

func (s *Server) databaseError(w http.ResponseWriter, r *http.Request, err error) {
	s.logger.Error("database query failed",
		"request_id", r.Header.Get("X-Request-ID"), "path", r.URL.Path, "error", err)
	writeAPI(w, http.StatusServiceUnavailable, 1503, "数据库暂时不可用", nil)
}

func (s *Server) liveness(w http.ResponseWriter, _ *http.Request) {
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"service": "mini-drop-apiserver", "status": "alive",
	})
}

func (s *Server) readiness(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	dependencies := map[string]string{}
	if s.repo != nil {
		if err := s.repo.Ping(ctx); err != nil {
			dependencies["database"] = "unhealthy"
		} else {
			dependencies["database"] = "healthy"
		}
	}
	if s.controlInitErr != nil || (s.cfg.ControlGRPCAddress != "" && s.controlHealth == nil) {
		dependencies["control_plane"] = "unhealthy"
	} else if s.controlHealth != nil {
		response, err := s.controlHealth.Check(ctx, &grpc_health_v1.HealthCheckRequest{})
		if err != nil || response.GetStatus() != grpc_health_v1.HealthCheckResponse_SERVING {
			dependencies["control_plane"] = "unhealthy"
		} else {
			dependencies["control_plane"] = "healthy"
		}
	}
	if s.cfg.DiagnosticAIGRPCAddress != "" && (s.diagnosticAIErr != nil || s.diagnosticAI == nil) {
		dependencies["diagnostic_ai"] = "unhealthy"
	} else if s.diagnosticAI != nil {
		diagnosticContext := ctx
		if token := strings.TrimSpace(s.cfg.ControlGRPCToken); token != "" {
			diagnosticContext = metadata.AppendToOutgoingContext(
				diagnosticContext, "x-mini-drop-grpc-token", token,
			)
		}
		response, err := s.diagnosticAI.Invoke(diagnosticContext, &mini_drop.DiagnosticAIRequest{
			Method: "GET", Path: "/diagnostic-tools", Principal: "readiness",
		})
		if err != nil || response.GetStatusCode() >= 500 {
			dependencies["diagnostic_ai"] = "unhealthy"
		} else {
			dependencies["diagnostic_ai"] = "healthy"
		}
	}
	for _, status := range dependencies {
		if status != "healthy" {
			writeAPI(w, http.StatusServiceUnavailable, 1503, "依赖服务异常", map[string]any{
				"service": "mini-drop-apiserver", "language": "go", "dependencies": dependencies,
			})
			return
		}
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"service": "mini-drop-apiserver", "language": "go", "dependencies": dependencies,
	})
}

func (s *Server) me(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if principal == nil {
		writeAPI(w, http.StatusUnauthorized, 1401, "authentication context is missing", nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"user_id": principal.ID,
		"roles":   principal.Roles,
		"resource_scope": map[string]any{
			"agent_ids": principal.AgentIDs, "service_ids": principal.ServiceIDs,
			"environments": principal.Environments,
		},
		"served_by": "go-apiserver",
	})
}

func (s *Server) metrics(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = fmt.Fprintf(
		w,
		"# HELP mini_drop_http_requests_total Total HTTP requests observed by this API process.\n"+
			"# TYPE mini_drop_http_requests_total counter\n"+
			"mini_drop_http_requests_total %d\n"+
			"# HELP mini_drop_http_errors_total HTTP responses with status 500 or greater.\n"+
			"# TYPE mini_drop_http_errors_total counter\n"+
			"mini_drop_http_errors_total %d\n"+
			"# HELP mini_drop_http_responses_total HTTP responses grouped by status class.\n"+
			"# TYPE mini_drop_http_responses_total counter\n"+
			"mini_drop_http_responses_total{class=\"2xx\"} %d\n"+
			"mini_drop_http_responses_total{class=\"3xx\"} %d\n"+
			"mini_drop_http_responses_total{class=\"4xx\"} %d\n"+
			"mini_drop_http_responses_total{class=\"5xx\"} %d\n"+
			"# HELP mini_drop_http_request_duration_seconds Total request duration observed by this API process.\n"+
			"# TYPE mini_drop_http_request_duration_seconds summary\n"+
			"mini_drop_http_request_duration_seconds_sum %.6f\n"+
			"mini_drop_http_request_duration_seconds_count %d\n"+
			"# HELP mini_drop_http_in_flight Requests currently executing in this API process.\n"+
			"# TYPE mini_drop_http_in_flight gauge\n"+
			"mini_drop_http_in_flight %d\n",
		s.httpRequests.Load(),
		s.httpErrors.Load(),
		s.httpResponses[2].Load(),
		s.httpResponses[3].Load(),
		s.httpResponses[4].Load(),
		s.httpResponses[5].Load(),
		float64(s.httpDurationUS.Load())/1_000_000,
		s.httpRequests.Load(),
		s.httpInFlight.Load(),
	)
}

func (s *Server) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/livez" || r.URL.Path == "/readyz" ||
			r.URL.Path == "/healthz" || r.URL.Path == "/api/healthz" {
			next.ServeHTTP(w, r.WithContext(context.WithValue(
				r.Context(), principalContextKey{}, developmentPrincipal(),
			)))
			return
		}
		if !s.cfg.AuthEnabled {
			attachPrincipalHeaders(r, developmentPrincipal())
			next.ServeHTTP(w, r.WithContext(context.WithValue(
				r.Context(), principalContextKey{}, developmentPrincipal(),
			)))
			return
		}
		provided := credentialFromRequest(r)
		principal := s.authenticatePrincipal(provided)
		if principal == nil {
			writeAPI(w, http.StatusUnauthorized, 1401, "访问认证失败", nil)
			return
		}
		if isMutatingMethod(r.Method) && !strings.HasPrefix(r.URL.Path, "/api/auth/") {
			approvalPath := strings.HasSuffix(r.URL.Path, "/approvals")
			skillGovernancePath := strings.HasPrefix(r.URL.Path, "/api/v2/diagnostic-skills/") &&
				(strings.HasSuffix(r.URL.Path, "/campaign") ||
					strings.HasSuffix(r.URL.Path, "/publish"))
			experimentApprovalPath := strings.HasPrefix(
				r.URL.Path, "/api/v2/diagnostic-experiments/",
			) && strings.HasSuffix(r.URL.Path, "/approve")
			allowed := hasAnyRole(principal, "operator", "admin")
			if approvalPath || skillGovernancePath || experimentApprovalPath {
				allowed = hasAnyRole(principal, "approver", "admin")
			}
			if !allowed {
				writeAPI(w, http.StatusForbidden, 1403, "principal role is not permitted for this operation", nil)
				return
			}
		}
		attachPrincipalHeaders(r, principal)
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), principalContextKey{}, principal)))
	})
}

func credentialFromRequest(r *http.Request) string {
	provided := strings.TrimSpace(r.Header.Get("X-API-Key"))
	if provided == "" {
		if cookie, err := r.Cookie("mini_drop_api_key"); err == nil {
			provided = strings.TrimSpace(cookie.Value)
		}
	}
	if provided == "" {
		if bearer := r.Header.Get("Authorization"); strings.HasPrefix(bearer, "Bearer ") {
			provided = strings.TrimSpace(strings.TrimPrefix(bearer, "Bearer "))
		}
	}
	return provided
}

func (s *Server) authenticatePrincipal(provided string) *requestPrincipal {
	for _, candidate := range s.cfg.Principals {
		if subtle.ConstantTimeCompare([]byte(provided), []byte(candidate.APIKey)) == 1 {
			return &requestPrincipal{
				ID: candidate.ID, Roles: append([]string(nil), candidate.Roles...),
				AgentIDs:     append([]string(nil), candidate.AgentIDs...),
				ServiceIDs:   append([]string(nil), candidate.ServiceIDs...),
				Environments: append([]string(nil), candidate.Environments...),
			}
		}
	}
	if len(s.cfg.Principals) == 0 && subtle.ConstantTimeCompare([]byte(provided), []byte(s.cfg.APIKey)) == 1 {
		return developmentPrincipal()
	}
	return nil
}

func developmentPrincipal() *requestPrincipal {
	return &requestPrincipal{
		ID: "development_admin", Roles: []string{"admin"}, AgentIDs: []string{"*"},
		ServiceIDs: []string{"*"}, Environments: []string{"*"},
	}
}

func attachPrincipalHeaders(r *http.Request, principal *requestPrincipal) {
	r.Header.Del("X-Mini-Drop-Principal")
	r.Header.Del("X-Mini-Drop-Roles")
	r.Header.Del("X-Mini-Drop-Agent-Scope")
	r.Header.Del("X-Mini-Drop-Service-Scope")
	r.Header.Del("X-Mini-Drop-Environment-Scope")
	if principal == nil {
		return
	}
	r.Header.Set("X-Mini-Drop-Principal", principal.ID)
	r.Header.Set("X-Mini-Drop-Roles", strings.Join(principal.Roles, ","))
	r.Header.Set("X-Mini-Drop-Agent-Scope", strings.Join(principal.AgentIDs, ","))
	r.Header.Set("X-Mini-Drop-Service-Scope", strings.Join(principal.ServiceIDs, ","))
	r.Header.Set("X-Mini-Drop-Environment-Scope", strings.Join(principal.Environments, ","))
}

func principalFromRequest(r *http.Request) *requestPrincipal {
	principal, _ := r.Context().Value(principalContextKey{}).(*requestPrincipal)
	return principal
}

func hasAnyRole(principal *requestPrincipal, roles ...string) bool {
	if principal == nil {
		return false
	}
	for _, assigned := range principal.Roles {
		for _, required := range roles {
			if strings.EqualFold(strings.TrimSpace(assigned), required) {
				return true
			}
		}
	}
	return false
}

func requireAnyRole(w http.ResponseWriter, principal *requestPrincipal, roles ...string) bool {
	if hasAnyRole(principal, roles...) {
		return true
	}
	writeAPI(w, http.StatusForbidden, 1403, "principal role is not permitted for this operation", nil)
	return false
}

func isMutatingMethod(method string) bool {
	return method != http.MethodGet && method != http.MethodHead && method != http.MethodOptions
}

func scopeAllows(scope []string, value string) bool {
	value = strings.TrimSpace(value)
	if value == "" {
		return true
	}
	for _, allowed := range scope {
		if allowed == "*" || allowed == value {
			return true
		}
	}
	return false
}

func filterResourceItems(
	items []map[string]any, field string, scope []string, limit, offset int,
) ([]map[string]any, int) {
	filtered := make([]map[string]any, 0, len(items))
	for _, item := range items {
		value, _ := item[field].(string)
		if scopeAllows(scope, value) {
			filtered = append(filtered, item)
		}
	}
	total := len(filtered)
	start := offset
	if start > total {
		start = total
	}
	end := start + limit
	if end > total {
		end = total
	}
	return filtered[start:end], total
}

func taskItemAllowed(principal *requestPrincipal, item map[string]any) bool {
	if principal == nil {
		return false
	}
	agentID, _ := item["agent_id"].(string)
	return scopeAllows(principal.AgentIDs, agentID)
}

func (s *Server) authorizeTaskResource(w http.ResponseWriter, r *http.Request, taskID string) bool {
	item, err := s.repo.GetTask(r.Context(), taskID)
	if errors.Is(err, repository.ErrNotFound) || (err == nil && !taskItemAllowed(principalFromRequest(r), item)) {
		writeAPI(w, http.StatusNotFound, 1404, "任务不存在", nil)
		return false
	}
	if err != nil {
		s.databaseError(w, r, err)
		return false
	}
	return true
}

func (s *Server) requestTrace(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestID := strings.TrimSpace(r.Header.Get("X-Request-ID"))
		if requestID == "" {
			requestID = time.Now().UTC().Format("20060102T150405.000000000") +
				"-" + itoa(s.requestID.Add(1))
		}
		r.Header.Set("X-Request-ID", requestID)
		w.Header().Set("X-Request-ID", requestID)

		traceID, flags, valid := parseTraceparent(r.Header.Get("Traceparent"))
		if !valid {
			traceID = randomTraceHex(16)
			flags = "01"
		}
		traceparent := "00-" + traceID + "-" + randomTraceHex(8) + "-" + flags
		r.Header.Set("Traceparent", traceparent)
		r.Header.Set("X-Trace-ID", traceID)
		w.Header().Set("Traceparent", traceparent)
		w.Header().Set("X-Trace-ID", traceID)
		next.ServeHTTP(w, r)
	})
}

func parseTraceparent(value string) (traceID, flags string, ok bool) {
	parts := strings.Split(strings.ToLower(strings.TrimSpace(value)), "-")
	if len(parts) != 4 || parts[0] != "00" || len(parts[1]) != 32 ||
		len(parts[2]) != 16 || len(parts[3]) != 2 {
		return "", "", false
	}
	traceBytes, traceErr := hex.DecodeString(parts[1])
	spanBytes, spanErr := hex.DecodeString(parts[2])
	_, flagsErr := hex.DecodeString(parts[3])
	if traceErr != nil || spanErr != nil || flagsErr != nil ||
		allZero(traceBytes) || allZero(spanBytes) {
		return "", "", false
	}
	return parts[1], parts[3], true
}

func allZero(value []byte) bool {
	for _, item := range value {
		if item != 0 {
			return false
		}
	}
	return true
}

func randomTraceHex(size int) string {
	value := make([]byte, size)
	if _, err := rand.Read(value); err == nil && !allZero(value) {
		return hex.EncodeToString(value)
	}
	// Correlation must never make a valid request unavailable. The normal path
	// above is cryptographic; this fallback only handles entropy-source failure.
	fallback := fmt.Sprintf("%032x%016x", time.Now().UTC().UnixNano(), size)
	return fallback[len(fallback)-size*2:]
}

// securityHeaders protects clients that connect to the Go API directly rather
// than through the production web proxy. API responses can contain sensitive
// diagnostic evidence, so they must not be cached by browsers or intermediaries.
func (s *Server) securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Referrer-Policy", "no-referrer")
		if strings.HasPrefix(r.URL.Path, "/api/") || r.URL.Path == "/api" {
			w.Header().Set("Cache-Control", "no-store")
		}
		next.ServeHTTP(w, r)
	})
}

func (s *Server) accessLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.httpInFlight.Add(1)
		defer s.httpInFlight.Add(-1)
		started := time.Now()
		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, r)
		duration := time.Since(started)
		s.httpRequests.Add(1)
		if class := recorder.status / 100; class >= 1 && class <= 5 {
			s.httpResponses[class].Add(1)
		}
		if micros := duration.Microseconds(); micros > 0 {
			s.httpDurationUS.Add(uint64(micros))
		}
		if recorder.status >= http.StatusInternalServerError {
			s.httpErrors.Add(1)
		}
		s.logger.Info("http request",
			"request_id", r.Header.Get("X-Request-ID"),
			"trace_id", r.Header.Get("X-Trace-ID"),
			"method", r.Method,
			"path", r.URL.Path,
			"status", recorder.status,
			"duration_ms", duration.Milliseconds(),
		)
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (w *statusRecorder) Unwrap() http.ResponseWriter {
	return w.ResponseWriter
}

func (w *statusRecorder) Flush() {
	if flusher, ok := w.ResponseWriter.(http.Flusher); ok {
		flusher.Flush()
	}
}

func (w *statusRecorder) WriteHeader(status int) {
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func writeAPI(w http.ResponseWriter, status, code int, message string, data any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"code":    code,
		"message": message,
		"data":    data,
	})
}

func itoa(value uint64) string {
	if value == 0 {
		return "0"
	}
	var buf [20]byte
	pos := len(buf)
	for value > 0 {
		pos--
		buf[pos] = byte('0' + value%10)
		value /= 10
	}
	return string(buf[pos:])
}
