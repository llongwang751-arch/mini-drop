// 这些种子和变异数据都是显式合成协议输入，不代表生产请求。
// Fuzz 的目标是长期守住归一化、字段边界与类型安全，发现的问题会由 Go 保存为可重放语料。
package domain

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func FuzzTaskNormalizeAndValidate(f *testing.F) {
	catalog, err := os.ReadFile(filepath.Join("..", "..", "..", "contracts", "task-kinds.json"))
	if err != nil {
		f.Fatal(err)
	}
	if err = LoadKinds(catalog); err != nil {
		f.Fatal(err)
	}
	for _, seed := range []string{
		`{"kind":"PROC_CPU","target_agent_id":"synthetic-node","parameters":{"pid":7,"duration_seconds":2,"frequency_hz":10}}`,
		`{"kind":"PERF_CPU","target_agent_id":"synthetic-node","priority":11,"parameters":{"pid":1}}`,
		`{"kind":"IMPORT_COLLAPSED","collapsed_text":"synthetic;root 1"}`,
		`{"kind":"RESOURCE_SNAPSHOT","target_agent_id":"synthetic-node","parameters":{"scope":"host"}}`,
		`null`, `[]`, `{"kind":"PROC_CPU","parameters":{"duration_seconds":1e300}}`,
	} {
		f.Add([]byte(seed))
	}
	f.Fuzz(func(t *testing.T, raw []byte) {
		if len(raw) > 64<<10 {
			return
		}
		var request CreateTask
		if json.Unmarshal(raw, &request) != nil {
			return
		}
		request.Normalize()
		first, err := json.Marshal(request)
		if err != nil {
			t.Fatal(err)
		}
		request.Normalize()
		second, err := json.Marshal(request)
		if err != nil || !bytes.Equal(first, second) {
			t.Fatal("Normalize is not idempotent")
		}
		if request.Validate() != nil {
			return
		}
		// 独立安全断言，不复制完整目录 schema：成功请求必须满足跨任务通用的不变量。
		if request.Priority < -10 || request.Priority > 10 || len(request.Name) > 120 || request.Parameters.Source != "" || request.Parameters.Traceparent != "" {
			t.Fatal("accepted request escaped common protocol bounds")
		}
		if request.Import == nil && (request.Kind == "PROC_CPU" || request.Kind == "PERF_CPU") {
			p := request.Parameters
			if p.PID < 2 || p.DurationSeconds < 1 || p.DurationSeconds > 60 || p.FrequencyHz < 1 || p.FrequencyHz > 199 || request.AgentID == "" {
				t.Fatal("accepted CPU request escaped target or sampling bounds")
			}
		}
	})
}
