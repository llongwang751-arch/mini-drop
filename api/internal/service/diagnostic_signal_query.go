// 查询结果只保留固定业务指标、日志事件类别和 Trace 数量/时长。
// 原日志正文、属性、真实 Trace ID、自由标签和部署说明不进入模型、检查点或持久化诊断轨迹。
package service

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"math"
	"net/url"
	"slices"
	"strconv"
	"strings"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

type SignalWindow struct {
	Start time.Time `json:"start"`
	End   time.Time `json:"end"`
}
type TelemetryMetric struct {
	Name       string     `json:"name"`
	Value      float64    `json:"value"`
	Unit       string     `json:"unit"`
	ObservedAt *time.Time `json:"observed_at,omitempty"`
}
type TelemetryLog struct {
	Timestamp  time.Time `json:"timestamp"`
	Level      string    `json:"level"`
	EventCode  string    `json:"event_code"`
	DurationMS *float64  `json:"duration_ms,omitempty"`
	StatusCode *int      `json:"status_code,omitempty"`
	TraceRef   string    `json:"trace_ref,omitempty"`
}
type TelemetryTrace struct {
	TraceRef   string     `json:"trace_ref"`
	DurationMS float64    `json:"duration_ms"`
	SpanCount  *int64     `json:"span_count,omitempty"`
	ErrorCount *int64     `json:"error_count,omitempty"`
	StartedAt  *time.Time `json:"started_at,omitempty"`
}
type TelemetryChange struct {
	Timestamp   time.Time `json:"timestamp"`
	ChangeType  string    `json:"change_type"`
	RevisionRef string    `json:"revision_ref"`
}
type BusinessSignalObservation struct {
	Available      bool              `json:"available"`
	Reason         string            `json:"reason,omitempty"`
	ServiceRef     string            `json:"service_ref"`
	Signal         string            `json:"signal"`
	Window         SignalWindow      `json:"window,omitzero"`
	WindowScope    string            `json:"window_scope,omitempty"`
	ContextWindow  *SignalWindow     `json:"context_window,omitempty"`
	ContextQueried bool              `json:"context_queried"`
	SourceKind     string            `json:"source_kind,omitempty"`
	QueryDigest    string            `json:"query_digest,omitempty"`
	Metrics        []TelemetryMetric `json:"metrics,omitempty"`
	Logs           []TelemetryLog    `json:"logs,omitempty"`
	Traces         []TelemetryTrace  `json:"traces,omitempty"`
	Changes        []TelemetryChange `json:"changes,omitempty"`
	Truncated      bool              `json:"truncated"`
	Limitations    []string          `json:"limitations"`
}

func finiteSignal(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0 && value <= 1e15
}
func metricUnit(name string) string {
	return map[string]string{"latency_p95_ms": "milliseconds", "error_rate": "ratio", "throughput_rps": "requests_per_second"}[name]
}

type diagnosticSignalWindows struct {
	Collection SignalWindow
	Context    SignalWindow
	Scope      string
}

// collectionSignalWindows 只为真实执行区间聚合指标。上下文扩窗只提供元数据，
// 不能将此前正常请求混进本轮P95，也不能把attempt包含的准备/上传时间称为精确采样时钟。
func collectionSignalWindows(attempt domain.Attempt, now time.Time) (diagnosticSignalWindows, error) {
	fail := func() (diagnosticSignalWindows, error) {
		return diagnosticSignalWindows{}, domain.Fail("SIGNAL_WINDOW_UNKNOWN", "没有可核实且有界的采集执行时钟")
	}
	if attempt.StartedAt == nil || attempt.FinishedAt == nil || !attempt.FinishedAt.After(*attempt.StartedAt) || attempt.FinishedAt.Sub(*attempt.StartedAt) > 13*time.Minute || attempt.FinishedAt.After(now.Add(time.Minute)) {
		return fail()
	}
	result := diagnosticSignalWindows{Collection: SignalWindow{attempt.StartedAt.UTC(), attempt.FinishedAt.UTC()}, Context: SignalWindow{attempt.StartedAt.Add(-time.Minute).UTC(), attempt.FinishedAt.Add(time.Minute).UTC()}, Scope: "attempt_execution"}
	var metadata map[string]json.RawMessage
	raw := bytes.TrimSpace(attempt.RunnerMetadata)
	if len(attempt.RunnerMetadata) > 32768 || len(attempt.RunnerMetadata) != 0 && len(raw) == 0 || len(raw) != 0 && (json.Unmarshal(raw, &metadata) != nil || metadata == nil) {
		return fail()
	}
	startRaw, hasStart := metadata["collection_started_unix_ms"]
	endRaw, hasEnd := metadata["collection_finished_unix_ms"]
	if hasStart != hasEnd {
		return fail()
	}
	if !hasStart {
		return result, nil
	}
	var startMS, endMS *int64
	if json.Unmarshal(startRaw, &startMS) != nil || json.Unmarshal(endRaw, &endMS) != nil || startMS == nil || endMS == nil || *startMS <= 0 || *endMS <= *startMS {
		return fail()
	}
	start, end := time.UnixMilli(*startMS).UTC(), time.UnixMilli(*endMS).UTC()
	if *startMS < attempt.StartedAt.UnixMilli() || *endMS > attempt.FinishedAt.UnixMilli() {
		return fail()
	}
	// Agent/helper的时钟为毫秒精度；首毫秒与精确attempt起点取交集，
	// 不因量化误差读到attempt以前，也不引入任意的时钟漂移容差。
	if start.Before(*attempt.StartedAt) {
		start = attempt.StartedAt.UTC()
	}
	if !end.After(start) {
		return fail()
	}
	result.Collection, result.Scope = SignalWindow{start, end}, "collector_execution"
	return result, nil
}

func (s *Suggestions) diagnosticSignalWindows(ctx context.Context, run DiagnosisRun) (diagnosticSignalWindows, error) {
	var attempt domain.Attempt
	if err := s.Repo.Context(ctx).Where("id=? AND task_id=?", run.AttemptID, run.TaskID).First(&attempt).Error; err != nil {
		return diagnosticSignalWindows{}, err
	}
	return collectionSignalWindows(attempt, time.Now())
}

func (s *Suggestions) signalWindow(ctx context.Context, run DiagnosisRun) (SignalWindow, error) {
	windows, err := s.diagnosticSignalWindows(ctx, run)
	return windows.Collection, err
}

func (s *Suggestions) queryBusinessSignals(ctx context.Context, run DiagnosisRun, args json.RawMessage) (json.RawMessage, error) {
	var in struct {
		ServiceRef string `json:"service_ref"`
		Signal     string `json:"signal"`
	}
	if !decodeAgentJSON(args, &in) || len(in.ServiceRef) != 2 || in.ServiceRef[0] != 'S' || in.ServiceRef[1] < '1' || in.ServiceRef[1] > '8' || !slices.Contains([]string{"metrics", "logs", "traces", "changes"}, in.Signal) {
		return nil, domain.Fail("AI_TOOL_REJECTED", "业务信号参数无效")
	}
	services, err := s.telemetryScope(ctx, run)
	if err != nil {
		return nil, err
	}
	out := BusinessSignalObservation{ServiceRef: in.ServiceRef, Signal: in.Signal, Limitations: []string{"关联窗口内同时出现不证明因果；缺失和截断不能解释为正常。"}}
	index := int(in.ServiceRef[1] - '1')
	if index >= len(services) {
		out.Reason = "service_not_bound"
		return json.Marshal(out)
	}
	if services[index].ID == "" {
		out.Reason = "service_not_authorized"
		return json.Marshal(out)
	}
	connector, exists := services[index].Connectors[in.Signal]
	if !exists {
		out.Reason = "signal_not_configured"
		return json.Marshal(out)
	}
	windows, err := s.diagnosticSignalWindows(ctx, run)
	if err != nil {
		out.Reason = "collection_window_unknown"
		out.Limitations = append(out.Limitations, "缺少有效采集执行起止时钟，不使用创建时间或分析完成时间替代。")
		return json.Marshal(out)
	}
	out.Window, out.WindowScope, out.ContextWindow = windows.Collection, windows.Scope, &windows.Context
	out.Limitations = append(out.Limitations, "指标只按window内的采集执行区间聚合；context_window是旧扩展上下文范围，仅供说明，本次未查询或混入其指标。")
	if windows.Scope == "attempt_execution" {
		out.Limitations = append(out.Limitations, "旧任务缺少collector起止元数据，使用attempt执行区间；它可能含准备与上传，不是精确采样时钟。")
	} else {
		out.Limitations = append(out.Limitations, "collector_execution来自Agent/helper记录的有界执行起止；它仍不是逐个sample的采样时间戳。")
	}
	parent := ctx
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	out.SourceKind = connector.Kind
	switch connector.Kind {
	case "prometheus":
		err = s.queryPrometheus(ctx, connector, &out)
	case "loki":
		err = s.queryLoki(ctx, connector, &out)
	case "tempo":
		err = s.queryTempo(ctx, connector, &out)
	case "drop-adapter":
		err = s.querySignalAdapter(ctx, connector, services[index].ID, &out)
	default:
		err = domain.Fail("SIGNAL_SOURCE_INVALID", "数据源类型无效")
	}
	if err != nil {
		if parent.Err() != nil {
			return nil, parent.Err()
		}
		out.Available = false
		out.Metrics = nil
		out.Logs = nil
		out.Traces = nil
		out.Changes = nil
		out.Reason = safeAgentError(err)
		out.Limitations = append(out.Limitations, "本数据源本次未提供可用观测，不能据此判断正常。")
		return json.Marshal(out)
	}
	out.Available = true
	if len(out.Metrics)+len(out.Logs)+len(out.Traces)+len(out.Changes) == 0 {
		out.Reason = "no_matching_data"
		out.Limitations = append(out.Limitations, "当前窗口未返回记录，不表示业务没有异常。")
	}
	return json.Marshal(out)
}

func (s *Suggestions) inspectServiceTopology(ctx context.Context, run DiagnosisRun, args json.RawMessage) (json.RawMessage, error) {
	if err := emptyDiagnosticKnowledgeArgs(args); err != nil {
		return nil, err
	}
	services, err := s.telemetryScope(ctx, run)
	if err != nil {
		return nil, err
	}
	nodes := []map[string]any{}
	edges := []map[string]string{}
	refs := map[string]string{}
	for index, service := range services {
		if service.ID == "" {
			continue
		}
		refs[service.ID] = fmt.Sprintf("S%d", index+1)
	}
	for _, service := range services {
		if service.ID == "" {
			continue
		}
		types := []string{}
		for signal := range service.Connectors {
			types = append(types, signal)
		}
		slices.Sort(types)
		nodes = append(nodes, map[string]any{"service_ref": refs[service.ID], "signal_types": types})
		for _, dependency := range service.Dependencies {
			if to, exists := refs[dependency]; exists {
				edges = append(edges, map[string]string{"from": refs[service.ID], "to": to, "source": "registered_dependency"})
			}
		}
	}
	return json.Marshal(map[string]any{"available": len(nodes) > 0, "nodes": nodes, "edges": edges, "current_service_ref": "S1", "causality_proven": false, "limitations": []string{"登记的一跳依赖用于选择调查方向；必须再用同时段信号验证，不能仅由拓扑确认故障传播。"}})
}

func (s *Suggestions) queryPrometheus(ctx context.Context, c TelemetryConnector, out *BusinessSignalObservation) error {
	names := []string{}
	for name := range c.Queries {
		names = append(names, name)
	}
	slices.Sort(names)
	for _, name := range names {
		query := c.Queries[name]
		parameters := url.Values{"query": {query}, "start": {strconv.FormatInt(out.Window.Start.Unix(), 10)}, "end": {strconv.FormatInt(out.Window.End.Unix(), 10)}, "step": {"15"}, "timeout": {"3s"}}
		data, err := s.Observability.fetch(ctx, c, "/api/v1/query_range", parameters)
		if err != nil {
			return err
		}
		var response struct {
			Status string `json:"status"`
			Data   struct {
				ResultType string `json:"resultType"`
				Result     []struct {
					Values [][]json.RawMessage `json:"values"`
				} `json:"result"`
			} `json:"data"`
		}
		if json.Unmarshal(data, &response) != nil || response.Status != "success" || response.Data.ResultType != "matrix" || len(response.Data.Result) > 1 {
			return domain.Fail("INVALID_EVIDENCE", "业务指标必须返回已聚合的单一时序")
		}
		if len(response.Data.Result) == 0 {
			continue
		}
		values := response.Data.Result[0].Values
		if len(values) > 64 {
			return domain.Fail("INVALID_EVIDENCE", "业务指标点数超出预算")
		}
		if len(values) == 0 {
			continue
		}
		last := values[len(values)-1]
		if len(last) != 2 {
			return domain.Fail("INVALID_EVIDENCE", "业务指标格式无效")
		}
		var raw string
		if json.Unmarshal(last[1], &raw) != nil {
			return domain.Fail("INVALID_EVIDENCE", "业务指标格式无效")
		}
		value, err := strconv.ParseFloat(raw, 64)
		if err != nil || !finiteSignal(value) || name == "error_rate" && value > 1 {
			return domain.Fail("INVALID_EVIDENCE", "业务指标单位或范围无效")
		}
		var seconds float64
		if json.Unmarshal(last[0], &seconds) != nil || !finiteSignal(seconds) {
			return domain.Fail("INVALID_EVIDENCE", "业务指标时间无效")
		}
		stamp := time.UnixMilli(int64(seconds * 1000)).UTC()
		if stamp.Before(out.Window.Start) || stamp.After(out.Window.End) {
			return domain.Fail("INVALID_EVIDENCE", "业务指标不在当前时间窗口")
		}
		out.Metrics = append(out.Metrics, TelemetryMetric{Name: name, Value: value, Unit: metricUnit(name), ObservedAt: &stamp})
	}
	out.QueryDigest = repository.DigestBytes(mustSignalJSON(c.Queries))
	out.Limitations = append(out.Limitations, "指标为管理员登记聚合查询在窗口末端返回的值，不与profile权重直接换算。")
	return nil
}

func mustSignalJSON(value any) []byte { data, _ := json.Marshal(value); return data }

func normalizeTelemetryLog(timestamp time.Time, raw []byte, window SignalWindow) (TelemetryLog, bool) {
	if timestamp.Before(window.Start) || timestamp.After(window.End) || len(raw) > 8192 {
		return TelemetryLog{}, false
	}
	var input struct {
		Level      string   `json:"level"`
		EventCode  string   `json:"event_code"`
		DurationMS *float64 `json:"duration_ms"`
		StatusCode *int     `json:"status_code"`
		TraceID    string   `json:"trace_id"`
	}
	if json.Unmarshal(raw, &input) != nil {
		return TelemetryLog{}, false
	}
	level := strings.ToLower(input.Level)
	if !slices.Contains([]string{"debug", "info", "warn", "error", "fatal"}, level) {
		level = "unknown"
	}
	code := input.EventCode
	if !slices.Contains([]string{"REQUEST_COMPLETE", "UPSTREAM_TIMEOUT", "DB_SLOW_QUERY", "CACHE_MISS", "GC_PRESSURE", "DEPLOYMENT", "UPSTREAM_ERROR"}, code) {
		code = "UNKNOWN"
	}
	out := TelemetryLog{Timestamp: timestamp.UTC(), Level: level, EventCode: code}
	if input.DurationMS != nil && finiteSignal(*input.DurationMS) {
		out.DurationMS = input.DurationMS
	}
	if input.StatusCode != nil && *input.StatusCode >= 100 && *input.StatusCode <= 599 {
		out.StatusCode = input.StatusCode
	}
	if len(input.TraceID) >= 16 && len(input.TraceID) <= 64 {
		out.TraceRef = "trace_" + repository.Digest(input.TraceID)[:12]
	}
	return out, true
}

func (s *Suggestions) queryLoki(ctx context.Context, c TelemetryConnector, out *BusinessSignalObservation) error {
	parameters := url.Values{"query": {c.Selector}, "start": {strconv.FormatInt(out.Window.Start.UnixNano(), 10)}, "end": {strconv.FormatInt(out.Window.End.UnixNano(), 10)}, "limit": {"30"}, "direction": {"backward"}}
	data, err := s.Observability.fetch(ctx, c, "/loki/api/v1/query_range", parameters)
	if err != nil {
		return err
	}
	var response struct {
		Status string `json:"status"`
		Data   struct {
			ResultType string `json:"resultType"`
			Result     []struct {
				Values [][]string `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if json.Unmarshal(data, &response) != nil || response.Status != "success" || response.Data.ResultType != "streams" || len(response.Data.Result) > 30 {
		return domain.Fail("INVALID_EVIDENCE", "业务日志格式无效")
	}
	for _, stream := range response.Data.Result {
		for _, value := range stream.Values {
			if len(value) != 2 {
				return domain.Fail("INVALID_EVIDENCE", "业务日志记录无效")
			}
			if len(out.Logs) >= 30 {
				out.Truncated = true
				break
			}
			ns, err := strconv.ParseInt(value[0], 10, 64)
			if err != nil {
				continue
			}
			if row, ok := normalizeTelemetryLog(time.Unix(0, ns), []byte(value[1]), out.Window); ok {
				out.Logs = append(out.Logs, row)
			}
		}
	}
	out.Truncated = out.Truncated || len(out.Logs) == 30
	out.QueryDigest = repository.Digest(c.Selector)
	out.Limitations = append(out.Limitations, "仅解析JSON日志中的固定结构字段；原日志正文及自由属性不发送模型。")
	return nil
}

func (s *Suggestions) queryTempo(ctx context.Context, c TelemetryConnector, out *BusinessSignalObservation) error {
	parameters := url.Values{"tags": {"service.name=" + strconv.Quote(c.Service)}, "start": {strconv.FormatInt(out.Window.Start.Unix(), 10)}, "end": {strconv.FormatInt(out.Window.End.Unix(), 10)}, "limit": {"20"}}
	data, err := s.Observability.fetch(ctx, c, "/api/search", parameters)
	if err != nil {
		return err
	}
	var response struct {
		Traces []struct {
			ID                string  `json:"traceID"`
			DurationMS        float64 `json:"durationMs"`
			StartTimeUnixNano string  `json:"startTimeUnixNano"`
		} `json:"traces"`
	}
	if json.Unmarshal(data, &response) != nil || len(response.Traces) > 20 {
		return domain.Fail("INVALID_EVIDENCE", "业务Trace格式无效")
	}
	for _, row := range response.Traces {
		if len(row.ID) < 16 || len(row.ID) > 64 || !finiteSignal(row.DurationMS) {
			return domain.Fail("INVALID_EVIDENCE", "业务Trace记录无效")
		}
		ns, stampErr := strconv.ParseInt(row.StartTimeUnixNano, 10, 64)
		if stampErr != nil {
			return domain.Fail("INVALID_EVIDENCE", "业务Trace时间无效")
		}
		stamp := time.Unix(0, ns).UTC()
		if stamp.Before(out.Window.Start) || stamp.After(out.Window.End) {
			continue
		}
		out.Traces = append(out.Traces, TelemetryTrace{TraceRef: "trace_" + repository.Digest(row.ID)[:12], DurationMS: row.DurationMS, StartedAt: &stamp})
	}
	out.Truncated = len(response.Traces) == 20
	out.QueryDigest = repository.Digest(c.Service)
	out.Limitations = append(out.Limitations, "Trace搜索摘要没有提供span总数或错误数时保持未知；搜索结果不证明下游根因。")
	return nil
}
