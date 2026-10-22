// 本文件验证日志字段白名单、秘密脱敏和并发写入时每行一个完整 JSON 对象。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package logging

import (
	"bytes"
	"encoding/json"
	"log/slog"
	"strings"
	"sync"
	"testing"

	"drop/api/internal/config"
)

func TestGuideFieldsAndSecretRedaction(t *testing.T) {
	var output bytes.Buffer
	logger := New(&output, "drop-api", "0.2.0")
	settings, err := config.Load(nil, func(key string) (string, bool) {
		return map[string]string{"DATABASE_URL": "postgres://fixture:IMPORTED_SECRET@db/private", "AI_API_KEY": "IMPORTED_SECRET"}[key], key == "DATABASE_URL" || key == "AI_API_KEY"
	})
	if err != nil {
		t.Fatal(err)
	}
	logger.With("request_id", "imported-request").Error("request failed", "task_id", "9007199254740993", "attempt_id", int64(42), "agent_id", "agent-fixture", "stage", "http.request", "duration_ms", int64(9), "code", "INTERNAL_ERROR", "error", "https://private/?IMPORTED_SECRET", "cookie", "IMPORTED_SECRET", "argv", "IMPORTED_SECRET", "effective_config", settings.Effective(), "config_hash", settings.Hash())
	var record map[string]any
	if json.Unmarshal(output.Bytes(), &record) != nil {
		t.Fatal("Log is not valid JSON")
	}
	for _, key := range []string{"time", "level", "service", "version", "request_id", "task_id", "attempt_id", "agent_id", "stage", "duration_ms", "error_code", "message"} {
		if _, exists := record[key]; !exists {
			t.Fatalf("Missing guide log field %s", key)
		}
	}
	if record["task_id"] != "9007199254740993" || record["attempt_id"] != "42" || record["error_code"] != "INTERNAL_ERROR" || record["level"] != "error" || strings.Contains(output.String(), "IMPORTED_SECRET") || strings.Contains(output.String(), "private/?") {
		t.Fatal("Log lost safe context or disclosed an unregistered field")
	}
}

// 实际Runner已经产生这四个安全字段；此前处理器静默丢弃它们，导致失败诊断无法核对预算。
// 负控覆盖看似合法的任意原因、秘密文本与非整数计数，防止为排障放开任意模型内容。
func TestAgentFailureDiagnosticsPreservedAndBounded(t *testing.T) {
	var output bytes.Buffer
	logger := New(&output, "drop-api", "fixture")
	logger.Error("AgentScope execution failed", "reason", "runner_non_200", "exception_kind", "validation", "model_calls", 9, "tool_calls", 14)
	var row map[string]any
	if err := json.Unmarshal(output.Bytes(), &row); err != nil {
		t.Fatal(err)
	}
	if row["reason"] != "runner_non_200" || row["exception_kind"] != "validation" || row["model_calls"] != float64(9) || row["tool_calls"] != float64(14) {
		t.Fatal("安全失败字段不应被日志白名单静默丢弃")
	}
	for _, attribute := range []slog.Attr{
		slog.String("reason", "IMPORTED_SECRET"), slog.String("exception_kind", "IMPORTED_SECRET"),
		slog.String("reason", "arbitrary_identifier"), slog.String("model_calls", "9"),
		slog.Int("model_calls", -1), slog.Int("tool_calls", 33), slog.Float64("tool_calls", 1),
	} {
		output.Reset()
		logger.LogAttrs(t.Context(), slog.LevelError, "AgentScope execution failed", attribute)
		row = nil
		if json.Unmarshal(output.Bytes(), &row) != nil || row[attribute.Key] != nil || strings.Contains(output.String(), "IMPORTED_SECRET") {
			t.Fatalf("未拒绝不安全的失败诊断字段：%s", attribute.Key)
		}
	}
}

func TestConcurrentRecordsRemainOneJSONObjectPerLine(t *testing.T) {
	var output bytes.Buffer
	logger := New(&output, "drop-api", "fixture")
	var wait sync.WaitGroup
	for index := 0; index < 30; index++ {
		wait.Add(1)
		go func() {
			defer wait.Done()
			logger.Info("http request", "duration_ms", int64(3), "route", "/api/v1/tasks/:id", "request_id", "bad\ncontrol", "effective_config", map[string]string{"raw": "IMPORTED_SECRET"})
		}()
	}
	wait.Wait()
	lines := bytes.Split(bytes.TrimSpace(output.Bytes()), []byte{'\n'})
	if len(lines) != 30 {
		t.Fatal("Concurrent log records interleaved")
	}
	for _, line := range lines {
		var record map[string]any
		if json.Unmarshal(line, &record) != nil || record["request_id"] != "" || record["effective_config"] != nil || record["attempt_id"] != "" || record["duration_ms"] != float64(3) {
			t.Fatal("Log accepted unsafe context or lost a complete record")
		}
	}
}
