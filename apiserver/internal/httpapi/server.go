package httpapi

import (
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"path"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"mini-drop/apiserver/internal/config"
	"mini-drop/apiserver/internal/objectstore"
	"mini-drop/apiserver/internal/repository"
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
	cfg       config.Config
	logger    *slog.Logger
	proxy     *httputil.ReverseProxy
	requestID atomic.Uint64
	client    *http.Client
	repo      *repository.Postgres
	store     objectstore.Store
}

func New(cfg config.Config, logger *slog.Logger, repositories ...*repository.Postgres) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(cfg.LegacyAPIURL)
	originalDirector := proxy.Director
	proxy.Director = func(req *http.Request) {
		originalDirector(req)
		// This header authenticates the private Go -> Python hop. Never forward a
		// client supplied value across the trust boundary.
		req.Header.Del("X-Mini-Drop-Gateway-Token")
		if cfg.InternalGatewayToken != "" {
			req.Header.Set("X-Mini-Drop-Gateway-Token", cfg.InternalGatewayToken)
		}
	}
	proxy.FlushInterval = -1
	proxy.ModifyResponse = func(resp *http.Response) error {
		resp.Header.Set("X-Request-ID", resp.Request.Header.Get("X-Request-ID"))
		if owner := resp.Request.Header.Get("X-Mini-Drop-Write-Owner"); owner != "" {
			resp.Header.Set("X-Mini-Drop-Write-Owner", owner)
		}
		return nil
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		logger.Error("legacy api unavailable",
			"request_id", r.Header.Get("X-Request-ID"),
			"path", r.URL.Path,
			"error", err,
		)
		writeAPI(w, http.StatusBadGateway, 1502, "Python 分析服务暂不可用", nil)
	}
	s := &Server{
		cfg:    cfg,
		logger: logger,
		proxy:  proxy,
		client: &http.Client{Timeout: 3 * time.Second},
	}
	if len(repositories) > 0 {
		s.repo = repositories[0]
		store, err := objectstore.New(
			cfg.MinIOEndpoint, cfg.MinIOAccessKey, cfg.MinIOSecretKey, cfg.MinIOSecure,
		)
		if err != nil {
			logger.Error("object storage initialization failed", "error", err)
		} else {
			s.store = store
		}
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.health)
	mux.HandleFunc("GET /api/healthz", s.health)
	mux.HandleFunc("GET /api/me", s.me)
	if s.repo != nil {
		mux.HandleFunc("GET /api/agents", s.listAgents)
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
		mux.HandleFunc("GET /api/v1/diagnoses", s.listDiagnosisSessions)
		mux.HandleFunc("GET /api/v1/continuous-diagnosis-triggers", s.listContinuousDiagnosisTriggers)
		mux.HandleFunc("GET /api/diagnostic-cases", s.listDiagnosticCases)
		mux.HandleFunc("GET /api/diagnostic-cases/{case_id}", s.getDiagnosticCase)
		// Schedules are now handled natively in Go (cron port + DB CRUD); the
		// Python reverse-proxy routes were removed below.
		(&scheduleHandlers{store: s.repo}).register(mux)
	}
	// AI write commands are always owned and policy-validated by Go. The
	// Python engine behind the reverse proxy performs the reasoning workflow.
	mux.HandleFunc("POST /api/v1/diagnoses", s.createDiagnosisSession)
	mux.HandleFunc("POST /api/v1/diagnoses/{diagnosis_id}/approvals", s.approveDiagnosisProbe)
	// Explicit compatibility surface.  Unknown /api paths must not silently
	// cross the Go/Python trust boundary through a catch-all reverse proxy.
	mux.Handle("/api/auth/{rest...}", proxy)
	mux.Handle("GET /api/metrics", proxy)
	mux.Handle("GET /api/ai-config", proxy)
	mux.Handle("POST /api/ai-validation/runs", proxy)
	mux.Handle("GET /api/analysis-jobs", proxy)
	mux.Handle("/api/analysis-jobs/{rest...}", proxy)
	mux.Handle("POST /api/tasks/{task_id}/diagnose", proxy)
	mux.Handle("GET /api/tasks/{task_id}/diagnoses", proxy)
	mux.Handle("/api/diagnoses/{rest...}", proxy)
	mux.Handle("GET /api/v1/probes", proxy)
	mux.Handle("/api/v1/diagnosis-evaluations/{rest...}", proxy)
	mux.Handle("/api/v1/diagnosis-campaigns/{rest...}", proxy)
	mux.Handle("/api/v1/real-world-benchmarks/{rest...}", proxy)
	mux.Handle("/api/nlp/{rest...}", proxy)
	mux.Handle("/api/v2/{rest...}", proxy)
	// Schedules are now native Go handlers (registered in the repo block).
	// Composite tasks remain on the Python engine; proxy their full surface.
	mux.Handle("/api/composite-tasks", proxy)
	mux.Handle("/api/composite-tasks/{rest...}", proxy)
	// Host process discovery for the quick-collection preset PID dropdown.
	mux.Handle("GET /api/top-processes", proxy)
	return s.accessLog(s.requestTrace(s.auth(mux)))
}

// diagnosisCreateCommand is the Go control-plane boundary for AI writes.  The
// Python diagnosis engine still owns reasoning, but malformed, over-budget or
// unknown commands are rejected here before they can reach the orchestrator.
type diagnosisCreateCommand struct {
	Query              string          `json:"query"`
	CaseID             string          `json:"case_id,omitempty"`
	Context            json.RawMessage `json:"context,omitempty"`
	BudgetProfile      string          `json:"budget_profile,omitempty"`
	Budget             json.RawMessage `json:"budget,omitempty"`
	DiagnosisMode      string          `json:"diagnosis_mode,omitempty"`
	AnalysisStrategy   string          `json:"analysis_strategy,omitempty"`
	EvidenceTimePolicy json.RawMessage `json:"evidence_time_policy,omitempty"`
	BaselineTaskIDs    []string        `json:"baseline_task_ids,omitempty"`
}

type diagnosisApprovalCommand struct {
	StepID     string `json:"step_id"`
	Decision   string `json:"decision"`
	Scope      string `json:"scope,omitempty"`
	ApproverID string `json:"approver_id,omitempty"`
}

const (
	diagnosisCommandAcceptedEvent = "AI_DIAGNOSIS_COMMAND_ACCEPTED"
	probeApprovalAcceptedEvent    = "AI_PROBE_APPROVAL_ACCEPTED"
)

func decodeStrictBody(w http.ResponseWriter, r *http.Request, target any) ([]byte, error) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return nil, err
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return nil, errors.New("request body must contain one JSON object")
	}
	return body, nil
}

func (s *Server) forwardValidatedAIWrite(w http.ResponseWriter, r *http.Request, body []byte) {
	r.Body = io.NopCloser(bytes.NewReader(body))
	r.ContentLength = int64(len(body))
	r.Header.Set("X-Mini-Drop-Write-Owner", "go-apiserver")
	r.Header.Set("X-Mini-Drop-Policy-Validated", "true")
	if principal := principalFromRequest(r); principal != nil {
		r.Header.Set("X-Mini-Drop-Principal", principal.ID)
		r.Header.Set("X-Mini-Drop-Roles", strings.Join(principal.Roles, ","))
		r.Header.Set("X-Mini-Drop-Agent-Scope", strings.Join(principal.AgentIDs, ","))
		r.Header.Set("X-Mini-Drop-Service-Scope", strings.Join(principal.ServiceIDs, ","))
		r.Header.Set("X-Mini-Drop-Environment-Scope", strings.Join(principal.Environments, ","))
	}
	s.proxy.ServeHTTP(w, r)
}

func (s *Server) createDiagnosisSession(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if !requireAnyRole(w, principal, "operator", "admin") {
		return
	}
	var input diagnosisCreateCommand
	body, err := decodeStrictBody(w, r, &input)
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "diagnosis command is not valid JSON", nil)
		return
	}
	input.Query = strings.TrimSpace(input.Query)
	if len(input.Query) < 3 || len(input.Query) > 2000 {
		writeAPI(w, http.StatusBadRequest, 1400, "query length must be between 3 and 2000", nil)
		return
	}
	input.CaseID = strings.TrimSpace(input.CaseID)
	if len(input.CaseID) > 128 {
		writeAPI(w, http.StatusBadRequest, 1400, "case_id exceeds 128 characters", nil)
		return
	}
	if input.BudgetProfile == "" {
		input.BudgetProfile = "production_safe"
	}
	if !map[string]bool{"production_safe": true, "staging": true, "development": true}[input.BudgetProfile] {
		writeAPI(w, http.StatusBadRequest, 1400, "unsupported budget_profile", nil)
		return
	}
	if input.DiagnosisMode != "" && !map[string]bool{
		"AUTO": true, "LIVE": true, "HISTORICAL": true, "REPRODUCTION": true,
	}[input.DiagnosisMode] {
		writeAPI(w, http.StatusBadRequest, 1400, "unsupported diagnosis_mode", nil)
		return
	}
	if input.AnalysisStrategy != "" && !map[string]bool{
		"CONSTRAINED_HYBRID": true, "DECISION_TREE": true, "EXPLORATORY": true,
	}[input.AnalysisStrategy] {
		writeAPI(w, http.StatusBadRequest, 1400, "unsupported analysis_strategy", nil)
		return
	}
	if len(input.BaselineTaskIDs) > 20 {
		writeAPI(w, http.StatusBadRequest, 1400, "baseline_task_ids exceeds 20 items", nil)
		return
	}
	if !diagnosisContextAllowed(principal, input.Context) {
		writeAPI(w, http.StatusForbidden, 1403, "diagnosis target is outside the principal resource scope", nil)
		return
	}
	seenBaselineTasks := map[string]bool{}
	for _, taskID := range input.BaselineTaskIDs {
		taskID = strings.TrimSpace(taskID)
		if taskID == "" || len(taskID) > 128 || strings.ContainsAny(taskID, "/\\") || seenBaselineTasks[taskID] {
			writeAPI(w, http.StatusBadRequest, 1400, "baseline_task_ids contains an invalid or duplicate task id", nil)
			return
		}
		seenBaselineTasks[taskID] = true
	}
	body, err = json.Marshal(input)
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	if s.repo != nil {
		if err := s.repo.RecordControlCommand(
			r.Context(), diagnosisCommandAcceptedEvent,
			"Go control plane accepted an AI diagnosis command",
			map[string]any{
				"query_length": len(input.Query), "budget_profile": input.BudgetProfile,
				"diagnosis_mode":    input.DiagnosisMode,
				"analysis_strategy": input.AnalysisStrategy,
				"request_id":        r.Header.Get("X-Request-ID"), "served_by": "go-apiserver",
			},
		); err != nil {
			s.databaseError(w, r, err)
			return
		}
	}
	s.forwardValidatedAIWrite(w, r, body)
}

func (s *Server) approveDiagnosisProbe(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if !requireAnyRole(w, principal, "approver", "admin") {
		return
	}
	diagnosisID := strings.TrimSpace(r.PathValue("diagnosis_id"))
	if diagnosisID == "" || len(diagnosisID) > 128 || strings.ContainsAny(diagnosisID, "/\\") {
		writeAPI(w, http.StatusBadRequest, 1400, "diagnosis_id is invalid", nil)
		return
	}
	var input diagnosisApprovalCommand
	body, err := decodeStrictBody(w, r, &input)
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "approval command is not valid JSON", nil)
		return
	}
	input.StepID = strings.TrimSpace(input.StepID)
	if input.StepID == "" || len(input.StepID) > 128 {
		writeAPI(w, http.StatusBadRequest, 1400, "step_id is invalid", nil)
		return
	}
	if input.Decision != "approve" && input.Decision != "reject" {
		writeAPI(w, http.StatusBadRequest, 1400, "decision must be approve or reject", nil)
		return
	}
	if input.Scope != "" && input.Scope != "single_execution" {
		writeAPI(w, http.StatusBadRequest, 1400, "only single_execution approval is allowed", nil)
		return
	}
	if input.Scope == "" {
		input.Scope = "single_execution"
	}
	input.ApproverID = principal.ID
	body, err = json.Marshal(input)
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	if s.repo != nil {
		if err := s.repo.RecordControlCommand(
			r.Context(), probeApprovalAcceptedEvent,
			"Go control plane accepted a single-execution probe decision",
			map[string]any{
				"diagnosis_id": diagnosisID, "step_id": input.StepID,
				"decision": input.Decision, "approver_id": input.ApproverID,
				"request_id": r.Header.Get("X-Request-ID"), "served_by": "go-apiserver",
			},
		); err != nil {
			s.databaseError(w, r, err)
			return
		}
	}
	s.forwardValidatedAIWrite(w, r, body)
}

type createTaskRequest struct {
	Name          string         `json:"name"`
	AgentID       string         `json:"agent_id"`
	TargetPID     int            `json:"target_pid"`
	CollectorType string         `json:"collector_type"`
	SampleRate    int            `json:"sample_rate"`
	DurationSec   int            `json:"duration_sec"`
	Options       map[string]any `json:"options"`
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
	if input.TargetPID < 1 || input.TargetPID > 4194304 {
		writeAPI(w, http.StatusBadRequest, 1400, "target_pid 超出有效范围", nil)
		return
	}
	if input.SampleRate == 0 {
		input.SampleRate = 99
	}
	if input.DurationSec == 0 {
		input.DurationSec = 15
	}
	if input.SampleRate < 1 || input.SampleRate > 10000 ||
		input.DurationSec < 1 || input.DurationSec > 600 {
		writeAPI(w, http.StatusBadRequest, 1400, "采样率或采样时长超出策略范围", nil)
		return
	}
	if input.Options == nil {
		input.Options = map[string]any{}
	}
	taskID, replayed, err := s.repo.CreateTask(r.Context(), repository.CreateTask{
		Name: input.Name, AgentID: input.AgentID, TargetPID: input.TargetPID,
		CollectorType: input.CollectorType, SampleRate: input.SampleRate,
		DurationSec: input.DurationSec, Options: input.Options,
		CreatorID: principal.ID, IdempotencyKey: idempotencyKey,
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
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"task_id": taskID, "status": "PENDING", "served_by": "go-apiserver", "replayed": replayed,
	})
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

func (s *Server) listDiagnosisSessions(w http.ResponseWriter, r *http.Request) {
	limit, offset := parseBoundedPage(r, 1000)
	items, total, err := s.repo.ListDiagnosisSessions(
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

func (s *Server) listContinuousDiagnosisTriggers(w http.ResponseWriter, r *http.Request) {
	limit, offset := parseBoundedPage(r, 1000)
	items, total, err := s.repo.ListContinuousDiagnosisTriggers(
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

func (s *Server) listDiagnosticCases(w http.ResponseWriter, r *http.Request) {
	limit, offset := parseBoundedPage(r, 500)
	items, err := s.repo.ListDiagnosticCases(r.Context())
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	sort.SliceStable(items, func(i, j int) bool {
		left, right := items[i], items[j]
		leftTime, rightTime := left.UpdatedAt, right.UpdatedAt
		if leftTime.IsZero() {
			leftTime = left.CreatedAt
		}
		if rightTime.IsZero() {
			rightTime = right.CreatedAt
		}
		return leftTime.After(rightTime)
	})
	total := len(items)
	start := offset
	if start > total {
		start = total
	}
	end := start + limit
	if end > total {
		end = total
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"items": items[start:end], "total": total, "limit": limit, "offset": offset,
		"compatibility": map[string]any{
			"v1_preserved": true, "v2_preserved": true,
			"legacy_rca_preserved": true, "write_mode": "native_api_only",
		},
		"served_by": "go-apiserver",
	})
}

func (s *Server) getDiagnosticCase(w http.ResponseWriter, r *http.Request) {
	caseID := strings.TrimSpace(r.PathValue("case_id"))
	if caseID == "" || len(caseID) > 128 {
		writeAPI(w, http.StatusBadRequest, 1400, "诊断案例 ID 不合法", nil)
		return
	}
	item, err := s.repo.GetDiagnosticCase(r.Context(), caseID)
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "diagnostic case not found", nil)
		return
	}
	if err != nil {
		s.databaseError(w, r, err)
		return
	}
	item["served_by"] = "go-apiserver"
	writeAPI(w, http.StatusOK, 0, "ok", item)
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
		s.proxy.ServeHTTP(w, r)
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
		s.proxy.ServeHTTP(w, r)
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

	taskCursor, auditCursor, diagnosisCursor := int64(0), int64(0), int64(0)
	if raw := strings.TrimSpace(r.Header.Get("Last-Event-ID")); raw != "" {
		taskCursor, auditCursor, diagnosisCursor = parseEventCursor(raw)
	} else if raw := strings.TrimSpace(r.URL.Query().Get("since")); raw != "" {
		taskCursor, auditCursor, diagnosisCursor = parseEventCursor(raw)
	} else {
		var err error
		taskCursor, err = s.repo.LatestStatusEventID(r.Context())
		if err == nil {
			auditCursor, err = s.repo.LatestAuditEventID(r.Context())
		}
		if err == nil {
			diagnosisCursor, err = s.repo.LatestDiagnosisEventID(r.Context())
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
					formatEventCursor(taskCursor, auditCursor, diagnosisCursor), data)
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
					formatEventCursor(taskCursor, auditCursor, diagnosisCursor), data)
				wrote = true
			}

			diagnosisEvents, err := s.repo.ListDiagnosisEventsAfter(r.Context(), diagnosisCursor, 200)
			if err != nil {
				s.logger.Error("sse diagnosis poll failed", "error", err, "cursor", diagnosisCursor)
				continue
			}
			for _, event := range diagnosisEvents {
				diagnosisCursor = event.ID
				if event.EventType != "diagnosis_completed" {
					continue
				}
				data, _ := json.Marshal(map[string]any{
					"diagnosis_id": event.DiagnosisID, "status": event.ToStatus,
					"payload": event.Payload,
				})
				fmt.Fprintf(w, "id: %s\nevent: diagnosis_complete\ndata: %s\n\n",
					formatEventCursor(taskCursor, auditCursor, diagnosisCursor), data)
				wrote = true
			}
			if wrote {
				flusher.Flush()
			}
		}
	}
}

func parseEventCursor(raw string) (int64, int64, int64) {
	if value, err := strconv.ParseInt(raw, 10, 64); err == nil {
		return value, 0, 0
	}
	var taskID, auditID, diagnosisID int64
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
		case "d":
			diagnosisID = value
		}
	}
	return taskID, auditID, diagnosisID
}

func formatEventCursor(taskID, auditID, diagnosisID int64) string {
	return fmt.Sprintf("t:%d;a:%d;d:%d", taskID, auditID, diagnosisID)
}

func (s *Server) databaseError(w http.ResponseWriter, r *http.Request, err error) {
	s.logger.Error("database query failed",
		"request_id", r.Header.Get("X-Request-ID"), "path", r.URL.Path, "error", err)
	writeAPI(w, http.StatusServiceUnavailable, 1503, "数据库暂时不可用", nil)
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	req, _ := http.NewRequest(http.MethodGet, s.cfg.LegacyAPIURL.String()+"/api/healthz", nil)
	resp, err := s.client.Do(req)
	if err != nil || resp.StatusCode >= 500 {
		if resp != nil {
			_ = resp.Body.Close()
		}
		writeAPI(w, http.StatusServiceUnavailable, 1503, "依赖服务异常", map[string]any{
			"service":    "mini-drop-apiserver",
			"language":   "go",
			"legacy_api": "unhealthy",
		})
		return
	}
	_ = resp.Body.Close()
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{
		"service":    "mini-drop-apiserver",
		"language":   "go",
		"legacy_api": "healthy",
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

func (s *Server) auth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/healthz" || r.URL.Path == "/api/healthz" {
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
		provided := strings.TrimSpace(r.Header.Get("X-API-Key"))
		if provided == "" {
			if cookie, err := r.Cookie("mini_drop_api_key"); err == nil {
				provided = cookie.Value
			}
		}
		if provided == "" {
			if bearer := r.Header.Get("Authorization"); strings.HasPrefix(bearer, "Bearer ") {
				provided = strings.TrimSpace(strings.TrimPrefix(bearer, "Bearer "))
			}
		}
		principal := s.authenticatePrincipal(provided)
		if principal == nil {
			writeAPI(w, http.StatusUnauthorized, 1401, "访问认证失败", nil)
			return
		}
		if isMutatingMethod(r.Method) && !strings.HasPrefix(r.URL.Path, "/api/auth/") {
			approvalPath := strings.HasSuffix(r.URL.Path, "/approvals")
			allowed := hasAnyRole(principal, "operator", "admin")
			if approvalPath {
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

func diagnosisContextAllowed(principal *requestPrincipal, raw json.RawMessage) bool {
	if principal == nil || len(raw) == 0 || string(raw) == "null" {
		return true
	}
	var value any
	if json.Unmarshal(raw, &value) != nil {
		return false
	}
	return resourceTreeAllowed(principal, value)
}

func resourceTreeAllowed(principal *requestPrincipal, value any) bool {
	switch item := value.(type) {
	case map[string]any:
		for key, child := range item {
			if text, ok := child.(string); ok {
				switch strings.ToLower(key) {
				case "agent_id":
					if !scopeAllows(principal.AgentIDs, text) {
						return false
					}
				case "service_id", "target_service":
					if !scopeAllows(principal.ServiceIDs, text) {
						return false
					}
				case "environment":
					if !scopeAllows(principal.Environments, text) {
						return false
					}
				}
			}
			if !resourceTreeAllowed(principal, child) {
				return false
			}
		}
	case []any:
		for _, child := range item {
			if !resourceTreeAllowed(principal, child) {
				return false
			}
		}
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
		next.ServeHTTP(w, r)
	})
}

func (s *Server) accessLog(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, r)
		s.logger.Info("http request",
			"request_id", r.Header.Get("X-Request-ID"),
			"method", r.Method,
			"path", r.URL.Path,
			"status", recorder.status,
			"duration_ms", time.Since(started).Milliseconds(),
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
