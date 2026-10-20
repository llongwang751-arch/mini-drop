package logging

import (
	"bytes"
	"encoding/json"
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
