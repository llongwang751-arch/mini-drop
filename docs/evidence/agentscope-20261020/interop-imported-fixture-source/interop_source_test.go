//go:build linux

package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"drop/api/internal/domain"
)

// External imported-provider interop fixture. This test is injected only into
// a copied Go module; it is never part of the production API or its source hash.
func TestExternalAgentScopeInterop(t *testing.T) {
	if os.Getuid() != 10001 {
		t.Fatal("fixture requires actual Go UID 10001")
	}
	runner, err := NewAgentRunner(os.Getenv("INTEROP_RUNNER_SOCKET"), os.Getenv("INTEROP_GATEWAY_SOCKET"))
	if err != nil {
		t.Fatal(err)
	}
	defer runner.Close()
	for _, scenario := range []string{"diagnosis", "planning", "repair", "format_retry", "budget", "cancel", "post_cancel", "numeric_roundtrip"} {
		t.Run(scenario, func(t *testing.T) {
			phase := scenario
			if scenario == "numeric_roundtrip" {
				phase = "repair"
			}
			if scenario == "format_retry" || scenario == "budget" || scenario == "cancel" || scenario == "post_cancel" {
				phase = "diagnosis"
			}
			trace := []DiagnosisStep{{EvidenceID: "E1", Tool: "read_profile_summary", Observation: json.RawMessage(`{"imported_fixture":true,"measurement_unit":"samples"}`)}}
			prefix := "E"
			if phase == "planning" {
				prefix = "P"
				trace = nil
			}
			if phase == "repair" {
				prefix = "S"
				trace = nil
			}
			profiled, read, tested := false, false, false
			tools := diagnosisDefinitions()
			if phase == "repair" {
				tools = repairDefinitions()
			}
			if phase == "planning" {
				tools = []map[string]any{
					{"type": "function", "function": map[string]any{"name": "list_collectors", "description": "Imported interop fixture catalog.", "parameters": map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false}}},
					{"type": "function", "function": map[string]any{"name": "propose_collection", "description": "Imported interop fixture plan.", "parameters": map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{"kind": map[string]any{"type": "string"}, "duration_seconds": map[string]any{"type": "integer", "minimum": 1, "maximum": 60}, "frequency_hz": map[string]any{"type": "integer", "minimum": 0, "maximum": 999}, "rationale": map[string]any{"type": "string"}}, "required": []string{"kind", "duration_seconds", "frequency_hz", "rationale"}}}},
				}
			}
			invoked := 0
			ctx, stop := context.WithTimeout(context.Background(), 20*time.Second)
			defer stop()
			spec := agentRunSpec{Phase: phase, Model: "deepseek-flash", SystemPrompt: "Imported synthetic interop fixture. Execute only the listed tools and return the fixture JSON. No real workload or production model is used.", Messages: []diagnosisMessage{{Role: "user", Content: "imported-interop:" + scenario}}, Tools: tools}
			spec.Fence = func(context.Context) error {
				if scenario == "cancel" {
					if _, err := os.Stat(filepath.Join(os.Getenv("INTEROP_MARKER_DIR"), "provider_started")); err == nil {
						return domain.Fail("STALE_LEASE", "imported lease cancellation")
					}
				}
				return nil
			}
			spec.Invoke = func(_ context.Context, name string, args json.RawMessage) (agentToolResult, error) {
				invoked++
				observation := map[string]any{"imported_fixture": true, "padding": strings.Repeat("bounded imported observation ", 160)}
				var terminal json.RawMessage
				switch name {
				case "profile_business_flow":
					profiled = true
					observation["hotspots"] = []any{map[string]any{"path": "fixture.py", "line": 31}}
				case "read_source":
					read = true
					observation["path"] = "fixture.py"
				case "test_patch":
					if !profiled || !read {
						return agentToolResult{}, errors.New("source evidence missing")
					}
					tested = true
					observation["success"] = true
					observation["accepted"] = true
				case "list_collectors":
					observation["collectors"] = []any{map[string]any{"kind": "PERF_CPU"}}
				case "propose_collection":
					var plan CollectionPlan
					if !decodeAgentJSON(args, &plan) || plan.Kind != "PERF_CPU" || plan.DurationSeconds != 4 || plan.FrequencyHz != 49 {
						return agentToolResult{}, errors.New("invalid imported plan")
					}
					terminal, _ = json.Marshal(plan)
				}
				observed, _ := json.Marshal(observation)
				step := DiagnosisStep{EvidenceID: fmt.Sprintf("%s%d", prefix, len(trace)+1), Tool: name, Observation: observed}
				trace = append(trace, step)
				encoded, _ := json.Marshal(step)
				return agentToolResult{Observation: encoded, Terminal: terminal}, nil
			}
			spec.Finish = func(_ context.Context, content string) (json.RawMessage, error) {
				if phase == "planning" {
					return nil, domain.Fail("AI_INVALID_REPORT", "planning requires actual terminal tool")
				}
				if phase == "repair" {
					if !profiled || !read || !tested {
						return nil, domain.Fail("AI_INSUFFICIENT_EVIDENCE", "repair tools incomplete")
					}
					var candidate struct {
						Summary     string   `json:"summary"`
						EvidenceIDs []string `json:"evidence_ids"`
						Locations   []struct {
							Path   string `json:"path"`
							Line   int    `json:"line"`
							Reason string `json:"reason"`
						} `json:"locations"`
					}
					if !decodeAgentJSON([]byte(content), &candidate) || len(candidate.Locations) != 1 || candidate.Locations[0].Path != "fixture.py" || candidate.Locations[0].Line != 31 {
						return nil, domain.Fail("AI_INVALID_REPORT", "source location was not observed")
					}
					if scenario == "numeric_roundtrip" {
						normalized, _ := json.Marshal(candidate)
						enriched := string(normalized[:len(normalized)-1]) + `,"validation":{"fixed":0.000001,"exponent":1e-6,"negzero":-0,"precise":0.12345678901234567890123456789},"padding":"` + strings.Repeat("x", 24000) + `"}`
						return json.RawMessage(enriched), nil
					}
					return json.Marshal(candidate)
				}
				used := map[string]bool{}
				for _, step := range trace {
					used[step.Tool] = true
				}
				if !used["inspect_hotspots"] || !used["check_collection_quality"] {
					return nil, domain.Fail("AI_INSUFFICIENT_EVIDENCE", "required tools absent")
				}
				report, err := validateDiagnosisReport(content, trace)
				if err != nil {
					return nil, err
				}
				return json.Marshal(report)
			}
			result, runtime, err := runner.Run(ctx, spec)
			if scenario == "budget" {
				if err == nil || invoked != 0 {
					t.Fatal("SDK accepted three tools in one model reply")
				}
				return
			}
			if scenario == "cancel" {
				if safeAgentError(err) != "STALE_LEASE" {
					t.Fatal("lease cancellation failed", err)
				}
				deadline := time.Now().Add(3 * time.Second)
				for {
					if _, err := os.Stat(filepath.Join(os.Getenv("INTEROP_MARKER_DIR"), "provider_canceled")); err == nil {
						break
					}
					if time.Now().After(deadline) {
						t.Fatal("actual SDK provider task survived Go UDS cancellation")
					}
					time.Sleep(20 * time.Millisecond)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			if !json.Valid(result) || runtime == nil || runtime.FrameworkVersion != "2.0.10" || runtime.AgentClass != "agentscope.agent.Agent" || runtime.ToolCalls != invoked {
				t.Fatal("actual runtime proof invalid")
			}
			models, wantTools := 2, 2
			if phase == "repair" {
				models, wantTools = 3, 3
			}
			if scenario == "format_retry" {
				models = 5
			}
			if runtime.ModelCalls != models || runtime.ToolCalls != wantTools {
				t.Fatal("unexpected actual framework counters", runtime.ModelCalls, runtime.ToolCalls)
			}
			if scenario == "post_cancel" && len(runner.sessions) != 0 {
				t.Fatal("completed session capability remains registered")
			}
		})
	}
}
