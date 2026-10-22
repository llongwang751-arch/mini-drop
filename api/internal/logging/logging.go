// Package logging 输出有界 JSON 日志，只接收允许的排障字段。
// 调用方不能借日志处理器附带任意请求数据、令牌、原始性能文件或模型响应。
// Package logging：将允许的服务、阶段、请求和任务标识输出为有界 JSON 记录。
// 未知键与敏感值在处理器边界过滤，避免调用方一次疏忽就把凭据、URL 或原始数据写入日志。
package logging

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode"

	"drop/api/internal/config"
)

type handler struct {
	output  io.Writer
	mutex   *sync.Mutex
	service string
	version string
	attrs   []slog.Attr
	groups  []string
}

func New(output io.Writer, service, version string) *slog.Logger {
	return slog.New(&handler{output: output, mutex: &sync.Mutex{}, service: service, version: version})
}

func (h *handler) Enabled(_ context.Context, level slog.Level) bool { return level >= slog.LevelInfo }

func identifier(value string, maximum int) bool {
	if value == "" || len(value) > maximum {
		return false
	}
	for _, character := range value {
		if !(character >= 'a' && character <= 'z') && !(character >= 'A' && character <= 'Z') && !(character >= '0' && character <= '9') && !strings.ContainsRune("._:-", character) {
			return false
		}
	}
	return true
}

func (h *handler) Handle(_ context.Context, record slog.Record) error {
	message := record.Message
	if len(message) > 256 || strings.ContainsFunc(message, unicode.IsControl) {
		message = "API event"
	}
	fields := map[string]any{
		"time": record.Time.UTC().Format(time.RFC3339Nano), "level": strings.ToLower(record.Level.String()),
		"service": h.service, "version": h.version, "request_id": "", "task_id": "", "attempt_id": "", "agent_id": "",
		"stage": "", "duration_ms": nil, "error_code": nil, "message": message,
	}
	assign := func(attribute slog.Attr) {
		value := attribute.Value.Resolve()
		key := attribute.Key
		if len(h.groups) != 0 {
			key = strings.Join(h.groups, ".") + "." + key
		}
		switch key {
		case "reason":
			// 只保留控制面预定义的失败位置；通用字符串白名单仍可能把上游内容带入日志。
			if value.Kind() == slog.KindString {
				switch value.String() {
				case "response_protocol_invalid", "accepted_result_mismatch", "runtime_metadata_mismatch", "runner_non_200", "report_rejected":
					fields[key] = value.String()
				}
			}
		case "exception_kind":
			if value.Kind() == slog.KindString {
				switch value.String() {
				case "protocol", "timeout", "transport", "sdk", "validation", "internal":
					fields[key] = value.String()
				}
			}
		case "model_calls", "tool_calls":
			// 日志层另设小上限，拒绝字符串/浮点/负数；真实阶段预算仍由Runner校验。
			if value.Kind() == slog.KindInt64 && value.Int64() >= 0 && value.Int64() <= 32 {
				fields[key] = value.Int64()
			}
		case "request_id", "agent_id", "stage", "trace_id", "config_hash":
			if value.Kind() == slog.KindString && identifier(value.String(), 128) {
				fields[key] = value.String()
			}
		case "error_code", "code":
			if value.Kind() == slog.KindString && identifier(value.String(), 64) {
				fields["error_code"] = value.String()
			}
		case "task_id", "attempt_id":
			text := ""
			if value.Kind() == slog.KindString {
				text = value.String()
			} else if value.Kind() == slog.KindInt64 && value.Int64() > 0 {
				text = strconv.FormatInt(value.Int64(), 10)
			}
			if len(text) <= 19 && strings.Trim(text, "0123456789") == "" {
				fields[key] = text
			}
		case "duration_ms", "status", "schema_version", "minimum_schema_version":
			if value.Kind() == slog.KindInt64 && value.Int64() >= 0 {
				fields[key] = value.Int64()
			}
		case "method":
			if value.Kind() == slog.KindString && identifier(value.String(), 16) {
				fields[key] = value.String()
			}
		case "route":
			if value.Kind() == slog.KindString && len(value.String()) <= 256 && !strings.ContainsAny(value.String(), "?@#") && !strings.ContainsFunc(value.String(), unicode.IsControl) {
				fields[key] = value.String()
			}
		case "effective_config":
			if safe, ok := value.Any().(config.SafeValues); ok {
				fields[key] = safe
			}
		}
	}
	for _, attribute := range h.attrs {
		assign(attribute)
	}
	record.Attrs(func(attribute slog.Attr) bool { assign(attribute); return true })
	encoded, err := json.Marshal(fields)
	if err != nil {
		return err
	}
	h.mutex.Lock()
	defer h.mutex.Unlock()
	_, err = h.output.Write(append(encoded, '\n'))
	return err
}

func (h *handler) WithAttrs(attrs []slog.Attr) slog.Handler {
	copy := *h
	copy.attrs = append(append([]slog.Attr{}, h.attrs...), attrs...)
	return &copy
}

func (h *handler) WithGroup(name string) slog.Handler {
	if name == "" {
		return h
	}
	copy := *h
	copy.groups = append(append([]string{}, h.groups...), name)
	return &copy
}
