// v2评测复用真实Skill匹配、真实工具和严格事实验证入口。
// 正常/不足/复合/错误归因是明确合成情境；控制器报告只验证程序边界，不冒充模型效果。
package service

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"

	"drop/api/internal/domain"
)

func TestDiagnosisFactEvaluationFixedControls(t *testing.T) {
	loadDiagnosisCatalog(t)
	body, err := os.ReadFile(filepath.Join("..", "..", "..", "scripts", "evaluation", "diagnostic_cases_v2.json"))
	if err != nil {
		t.Fatal(err)
	}
	var dataset struct {
		Cases []struct {
			ID     string `json:"id"`
			Family string `json:"family"`
			Task   struct {
				Kind       string          `json:"kind"`
				Parameters json.RawMessage `json:"parameters"`
			} `json:"task"`
			Profile     json.RawMessage `json:"profile"`
			GroundTruth struct {
				SkillIDs []string `json:"skill_ids"`
			} `json:"ground_truth"`
			Controls struct {
				PositiveSelfItems *int `json:"positive_self_items"`
			} `json:"fact_protocol_controls"`
		} `json:"cases"`
	}
	if json.Unmarshal(body, &dataset) != nil || len(dataset.Cases) != 6 {
		t.Fatal("v2固定集版本或数量错误")
	}
	observations := []map[string]any{}
	for _, c := range dataset.Cases {
		t.Run(c.ID, func(t *testing.T) {
			task := domain.Task{Kind: c.Task.Kind, Parameters: c.Task.Parameters}
			skills, err := MatchDiagnosticSkills(task, c.Profile)
			if err != nil {
				t.Fatal(err)
			}
			ids := []string{}
			for _, skill := range skills {
				ids = append(ids, skill.ID)
			}
			slices.Sort(ids)
			if !slices.Equal(ids, c.GroundTruth.SkillIDs) {
				t.Fatalf("Skill实际%v，期望%v", ids, c.GroundTruth.SkillIDs)
			}
			trace := []DiagnosisStep{}
			for i, tool := range []string{"read_profile_summary", "inspect_hotspots", "check_collection_quality", "inspect_call_paths", "propose_verification"} {
				observation, _, err := diagnosisTool(tool, json.RawMessage(`{}`), c.Profile, task)
				if err != nil {
					t.Fatal(err)
				}
				trace = append(trace, DiagnosisStep{EvidenceID: "E" + string(rune('1'+i)), Tool: tool, Observation: observation})
			}
			var top struct {
				Top []struct {
					Self float64 `json:"self_percent"`
					Hint string  `json:"symbol_hint"`
				} `json:"top_functions"`
			}
			if json.Unmarshal(trace[1].Observation, &top) != nil || len(top.Top) == 0 {
				t.Fatal("缺少实际热点观测")
			}
			expectedPositive := 2 // 两个原样复用的dev种子均有两个正self项目。
			if c.Controls.PositiveSelfItems != nil {
				expectedPositive = *c.Controls.PositiveSelfItems
			}
			facts := []any{
				map[string]any{"id": "F1", "evidence_id": "E2", "path": "/top_functions", "operator": "count_positive_self", "value": expectedPositive, "unit": "count"},
				map[string]any{"id": "F2", "evidence_id": "E2", "path": "/top_functions/0/self_percent", "operator": "eq", "value": top.Top[0].Self, "unit": "percent"},
			}
			factIDs := []string{"F1", "F2"}
			for i, row := range top.Top {
				if row.Hint == "unknown" {
					continue
				}
				id := "F" + string(rune('3'+i))
				facts = append(facts, map[string]any{"id": id, "evidence_id": "E2", "path": "/top_functions/" + string(rune('0'+i)) + "/symbol_hint", "operator": "eq", "value": row.Hint, "unit": "category"})
				factIDs = append(factIDs, id)
			}
			content := func(kind string, mode string, mutate func(map[string]any)) string {
				v := map[string]any{"report_version": 2, "facts": facts, "hypotheses": []any{map[string]any{"kind": kind, "fact_ids": factIDs, "evidence_ids": []string{"E2"}}}, "verification_requests": []any{map[string]any{"origin": "tool_instruction", "kind": "manual_import", "source_evidence_id": "E5"}}}
				// 克隆控制输入，负控不能修改后续试验共享的数据。
				encoded, _ := json.Marshal(v)
				var copy map[string]any
				_ = json.Unmarshal(encoded, &copy)
				if mutate != nil {
					mutate(copy)
				}
				encoded, _ = json.Marshal(copy)
				return string(encoded)
			}
			guards := []diagnosticEvaluationGuard{}
			for _, mode := range []string{"off", "skills"} {
				steps := append([]DiagnosisStep{}, trace...)
				if mode == "skills" {
					knowledge, _ := json.Marshal(DiagnosticKnowledge{Skills: skills, Cases: []DiagnosticCasePrior{}, PriorOnly: true})
					steps = append(steps, DiagnosisStep{EvidenceID: "E6", Tool: "read_diagnostic_knowledge", Observation: knowledge})
				}
				check := func(name string, candidate string, accepted bool) {
					_, err := validateDiagnosisModelReportV2(candidate, steps)
					actual := err == nil
					guards = append(guards, diagnosticEvaluationGuard{Name: mode + ":" + name, ExpectedAccepted: accepted, ActualAccepted: actual})
					if actual != accepted {
						t.Errorf("%s %s 期望接受%v，错误%v", mode, name, accepted, err)
					}
				}
				check("typed-current-facts", content(c.Family, mode, nil), true)
				check("wrong-positive-self-count", content(c.Family, mode, func(v map[string]any) { v["facts"].([]any)[0].(map[string]any)["value"] = expectedPositive + 1 }), false)
				check("wrong-unit", content(c.Family, mode, func(v map[string]any) { v["facts"].([]any)[1].(map[string]any)["unit"] = "milliseconds" }), false)
				check("manual-import-is-not-tool-plan", content(c.Family, mode, func(v map[string]any) {
					v["verification_requests"] = []any{map[string]any{"origin": "tool_plan", "kind": "repeat_collection", "source_evidence_id": "E5", "duration_seconds": 60, "frequency_hz": 99}}
				}), false)
				if c.Family == "normal_load" {
					check("allocation-without-current-signal", content("allocation_gc", mode, nil), false)
				}
			}
			observations = append(observations, map[string]any{"case_id": c.ID, "skill_ids": ids, "guards": guards})
		})
	}
	if !t.Failed() {
		if output := os.Getenv("DROP_DIAGNOSTIC_FACT_EVAL_OUTPUT"); output != "" {
			encoded, _ := json.MarshalIndent(map[string]any{"schema_version": 1, "mode": "deterministic_offline_v2", "controller_oracle": true, "model_calls": 0, "observations": observations}, "", "  ")
			if err := os.WriteFile(output, append(encoded, '\n'), 0600); err != nil {
				t.Fatal(err)
			}
		}
	}
}
