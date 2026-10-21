// 轻量适配协议面向已有业务遥测网关，便于接入尚未部署Loki/Tempo的小型环境。
// 仍只接受受控字段；适配器声明不等于独立证明，生产准确率需另行评测。
package service

import (
	"context"
	"encoding/json"
	"net/url"
	"strconv"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

func (s *Suggestions) querySignalAdapter(ctx context.Context, c TelemetryConnector, serviceID string, out *BusinessSignalObservation) error {
	parameters := url.Values{"service": {serviceID}, "signal": {out.Signal}, "start": {out.Window.Start.Format(time.RFC3339Nano)}, "end": {out.Window.End.Format(time.RFC3339Nano)}, "limit": {"30"}}
	data, err := s.Observability.fetch(ctx, c, "/v1/signals", parameters)
	if err != nil {
		return err
	}
	var response struct {
		SchemaVersion int               `json:"schema_version"`
		ServiceID     string            `json:"service_id"`
		Signal        string            `json:"signal"`
		Truncated     bool              `json:"truncated"`
		Metrics       []TelemetryMetric `json:"metrics"`
		Logs          []json.RawMessage `json:"logs"`
		Traces        []struct {
			TraceID    string    `json:"trace_id"`
			DurationMS float64   `json:"duration_ms"`
			SpanCount  *int64    `json:"span_count"`
			ErrorCount *int64    `json:"error_count"`
			StartedAt  time.Time `json:"started_at"`
		} `json:"traces"`
		Changes []struct {
			Timestamp  time.Time `json:"timestamp"`
			ChangeType string    `json:"change_type"`
			Revision   string    `json:"revision"`
		} `json:"changes"`
	}
	if json.Unmarshal(data, &response) != nil || response.SchemaVersion != 1 || response.ServiceID != serviceID || response.Signal != out.Signal || len(response.Metrics) > 3 || len(response.Logs) > 30 || len(response.Traces) > 20 || len(response.Changes) > 20 {
		return domain.Fail("INVALID_EVIDENCE", "业务适配器响应身份、格式或数量无效")
	}
	out.Truncated = response.Truncated
	seen := map[string]bool{}
	if out.Signal == "metrics" {
		for _, metric := range response.Metrics {
			if seen[metric.Name] || metricUnit(metric.Name) == "" || metric.Unit != metricUnit(metric.Name) || !finiteSignal(metric.Value) || metric.Name == "error_rate" && metric.Value > 1 || metric.ObservedAt == nil || metric.ObservedAt.Before(out.Window.Start) || metric.ObservedAt.After(out.Window.End) {
				return domain.Fail("INVALID_EVIDENCE", "业务适配器指标单位或值无效")
			}
			seen[metric.Name] = true
			out.Metrics = append(out.Metrics, metric)
		}
	}
	if out.Signal == "logs" {
		for _, raw := range response.Logs {
			var stamp struct {
				Timestamp time.Time `json:"timestamp"`
			}
			if json.Unmarshal(raw, &stamp) != nil {
				return domain.Fail("INVALID_EVIDENCE", "业务日志时间无效")
			}
			if row, ok := normalizeTelemetryLog(stamp.Timestamp, raw, out.Window); ok {
				out.Logs = append(out.Logs, row)
			}
		}
	}
	if out.Signal == "traces" {
		for _, row := range response.Traces {
			if len(row.TraceID) < 16 || len(row.TraceID) > 64 || !finiteSignal(row.DurationMS) || row.SpanCount != nil && (*row.SpanCount < 0 || *row.SpanCount > 100000) || row.ErrorCount != nil && (*row.ErrorCount < 0 || *row.ErrorCount > 100000) || row.ErrorCount != nil && row.SpanCount != nil && *row.ErrorCount > *row.SpanCount {
				return domain.Fail("INVALID_EVIDENCE", "业务Trace记录无效")
			}
			if row.StartedAt.Before(out.Window.Start) || row.StartedAt.After(out.Window.End) {
				continue
			}
			stamp := row.StartedAt.UTC()
			out.Traces = append(out.Traces, TelemetryTrace{TraceRef: "trace_" + repository.Digest(row.TraceID)[:12], DurationMS: row.DurationMS, SpanCount: row.SpanCount, ErrorCount: row.ErrorCount, StartedAt: &stamp})
		}
	}
	if out.Signal == "changes" {
		for _, row := range response.Changes {
			if row.Timestamp.Before(out.Window.Start) || row.Timestamp.After(out.Window.End) {
				continue
			}
			if row.ChangeType != "deployment" && row.ChangeType != "configuration" && row.ChangeType != "feature_flag" {
				return domain.Fail("INVALID_EVIDENCE", "业务变更类型无效")
			}
			if len(row.Revision) < 1 || len(row.Revision) > 128 {
				return domain.Fail("INVALID_EVIDENCE", "业务变更版本无效")
			}
			out.Changes = append(out.Changes, TelemetryChange{row.Timestamp.UTC(), row.ChangeType, "revision_" + repository.Digest(row.Revision)[:12]})
		}
	}
	out.QueryDigest = repository.Digest(serviceID + "\x00" + out.Signal + "\x00" + strconv.FormatInt(out.Window.Start.Unix(), 10) + "\x00" + strconv.FormatInt(out.Window.End.Unix(), 10))
	out.Limitations = append(out.Limitations, "数据来自管理员登记的适配器；未验证的业务因果、原始日志和属性不在此观测内。")
	return nil
}
