// 通过本机HTTP控制器替身驱动实际模型传输与纠错流程；没有外部模型调用。
// 验证旧传输使用v2时也必须拒绝错误事实，然后在原预算内重交正确结构，不能回退自由报告。
package service

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestDiagnosisFactsV2ModelTransportActuallyRejectsAndRepairs(t *testing.T) {
	fixture := diagnosisFactFixture(t, "allocation")
	var requests atomic.Int64
	var tools atomic.Int64
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Messages []diagnosisMessage `json:"messages"`
		}
		if json.NewDecoder(r.Body).Decode(&body) != nil || len(body.Messages) == 0 {
			t.Error("模型传输请求不可读")
			w.WriteHeader(400)
			return
		}
		if !strings.Contains(body.Messages[0].Content, "report_version=2") || !strings.Contains(body.Messages[0].Content, "最多4轮普通") || strings.Contains(body.Messages[0].Content, "最多8轮普通") {
			t.Error("旧传输没有使用正确的v2协议与四轮预算")
		}
		index := requests.Add(1)
		message := map[string]any{}
		finish := "stop"
		if index == 1 {
			finish = "tool_calls"
			message["tool_calls"] = []any{
				map[string]any{"id": "fixture-call-hotspots", "type": "function", "function": map[string]any{"name": "inspect_hotspots", "arguments": "{}"}},
				map[string]any{"id": "fixture-call-quality", "type": "function", "function": map[string]any{"name": "check_collection_quality", "arguments": "{}"}},
			}
		} else {
			candidate := diagnosisFactContent(t, func(v map[string]any) {
				v["verification_requests"] = []any{map[string]any{"origin": "model_proposal", "kind": "repeat_collection", "duration_seconds": 60, "frequency_hz": 99}}
				if index == 2 {
					v["facts"].([]any)[0].(map[string]any)["value"] = 1
				}
			})
			message["content"] = candidate
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"choices": []any{map[string]any{"finish_reason": finish, "message": message}}})
	}))
	defer server.Close()
	s := &Suggestions{Config: AIConfig{Endpoint: server.URL, Model: "explicit-http-controller-fixture"}, Client: server.Client()}
	trace := append([]DiagnosisStep{}, fixture[:1]...)
	first, _ := json.Marshal(trace[0])
	invoke := func(name string, args json.RawMessage) (json.RawMessage, error) {
		var observation json.RawMessage
		for _, step := range fixture {
			if step.Tool == name {
				observation = step.Observation
				break
			}
		}
		tools.Add(1)
		step := DiagnosisStep{EvidenceID: "E" + string(rune('1'+len(trace))), Tool: name, Observation: observation}
		trace = append(trace, step)
		return json.Marshal(step)
	}
	report, err := s.diagnosisModelWithProtocol(context.Background(), "明确合成HTTP控制器回归", first, invoke, &trace, 2)
	if err != nil {
		t.Fatal(err)
	}
	if requests.Load() != 3 || tools.Load() != 2 || report.ReportVersion != 2 || !strings.Contains(report.Summary, "2项具有正self权重") {
		t.Fatalf("事实错误未触发真实纠正流程: requests=%d tools=%d report=%+v", requests.Load(), tools.Load(), report)
	}
}
