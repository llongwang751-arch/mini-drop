// 本文件把公开固定合成夹具送入真正的 Skill 匹配、诊断工具和报告验证入口。
// 离线结果只衡量路由与协议防线，不启动模型，不以作者写出的报告充当模型准确率。
package service

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"drop/api/internal/domain"
)

type diagnosticEvaluationFixture struct {
	SchemaVersion int `json:"schema_version"`
	Cases         []struct {
		ID         string `json:"id"`
		Split      string `json:"split"`
		Provenance struct {
			Source         string `json:"source"`
			Imported       bool   `json:"imported"`
			Synthetic      bool   `json:"synthetic"`
			ProductionData bool   `json:"production_data"`
		} `json:"provenance"`
		Task struct {
			Kind       string          `json:"kind"`
			Parameters json.RawMessage `json:"parameters"`
		} `json:"task"`
		Profile         json.RawMessage `json:"profile"`
		PriorCandidates []struct {
			UntrustedText string `json:"untrusted_text"`
		} `json:"prior_candidates"`
		GroundTruth struct {
			SkillIDs       []string `json:"skill_ids"`
			MatcherRejects bool     `json:"matcher_rejects"`
		} `json:"ground_truth"`
	} `json:"cases"`
}

type diagnosticEvaluationGuard struct {
	Name             string `json:"name"`
	ExpectedAccepted bool   `json:"expected_accepted"`
	ActualAccepted   bool   `json:"actual_accepted"`
}

type diagnosticEvaluationVariant struct {
	KnowledgeMode string                      `json:"knowledge_mode"`
	ElapsedMS     float64                     `json:"elapsed_ms"`
	Guards        []diagnosticEvaluationGuard `json:"guards"`
}

type diagnosticEvaluationObservation struct {
	CaseID           string                        `json:"case_id"`
	Split            string                        `json:"split"`
	SkillIDs         []string                      `json:"skill_ids"`
	MatcherRejected  bool                          `json:"matcher_rejected"`
	MatcherElapsedMS float64                       `json:"matcher_elapsed_ms"`
	Variants         []diagnosticEvaluationVariant `json:"variants"`
}

func diagnosticEvaluationLoad(t *testing.T) diagnosticEvaluationFixture {
	t.Helper()
	path := filepath.Join("..", "..", "..", "scripts", "evaluation", "diagnostic_cases.json")
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var fixture diagnosticEvaluationFixture
	if err = json.Unmarshal(body, &fixture); err != nil {
		t.Fatal(err)
	}
	if fixture.SchemaVersion != 1 || len(fixture.Cases) == 0 {
		t.Fatal("固定评测夹具协议无效")
	}
	return fixture
}

func diagnosticEvaluationReport(refs []string, forged string) string {
	// 这是明确的控制器样例，专门探测引用边界，不是假装模型生成的根因报告。
	body := map[string]any{
		"summary": "合成观测只支持待验证线索",
		"hypotheses": []any{map[string]any{
			"claim":        "当前观测需要同负载对照，不能单凭历史确认根因",
			"evidence_ids": refs,
			"verification": "先核对采样质量和调用路径，再在相同负载下复测",
		}},
		"limitations": []string{"固定导入夹具没有真实业务延迟、模型推理或受控修复"},
	}
	if forged != "" {
		body[forged] = map[string]any{}
	}
	encoded, _ := json.Marshal(body)
	return string(encoded)
}

func TestDiagnosticEvaluation(t *testing.T) {
	loadDiagnosisCatalog(t)
	fixture := diagnosticEvaluationLoad(t)
	observations := []diagnosticEvaluationObservation{}
	seen := map[string]bool{}
	for _, c := range fixture.Cases {
		t.Run(c.ID, func(t *testing.T) {
			if seen[c.ID] || c.ID == "" || (c.Split != "dev" && c.Split != "holdout") {
				t.Fatal("评测案例 ID 或分区无效")
			}
			seen[c.ID] = true
			if c.Provenance.Source != "imported_synthetic" || !c.Provenance.Imported || !c.Provenance.Synthetic || c.Provenance.ProductionData {
				t.Fatal("必须明确标识为非生产的导入合成夹具")
			}
			for _, prior := range c.PriorCandidates {
				if prior.UntrustedText == "" {
					continue
				}
				// 用户历史自由文本不能借纠正类型进入案例库；这是入参边界，不是模型抗注入效果。
				if err := validateDiagnosticCaseInput(DiagnosticCaseInput{Outcome: "reviewed_correction", HumanConfirmed: true, CorrectionCode: prior.UntrustedText}); err == nil {
					t.Fatal("恶意历史自由文本不应作为固定纠正类型被接纳")
				}
			}
			task := domain.Task{Kind: c.Task.Kind, Parameters: c.Task.Parameters}
			started := time.Now()
			skills, err := MatchDiagnosticSkills(task, c.Profile)
			observation := diagnosticEvaluationObservation{
				CaseID: c.ID, Split: c.Split, SkillIDs: []string{},
				MatcherRejected:  err != nil,
				MatcherElapsedMS: float64(time.Since(started).Microseconds()) / 1000,
				Variants:         []diagnosticEvaluationVariant{},
			}
			if (err != nil) != c.GroundTruth.MatcherRejects {
				t.Fatalf("实际输入拒绝=%v，期望=%v", err != nil, c.GroundTruth.MatcherRejects)
			}
			for _, skill := range skills {
				observation.SkillIDs = append(observation.SkillIDs, skill.ID)
				if skill.Version != "1.0.0" || len(skill.MatchReasons) == 0 {
					t.Fatal("缺少可追溯方法版本或匹配原因")
				}
			}
			slices.Sort(observation.SkillIDs)
			if !slices.Equal(observation.SkillIDs, c.GroundTruth.SkillIDs) {
				t.Fatalf("实际 Skill=%v，期望=%v", observation.SkillIDs, c.GroundTruth.SkillIDs)
			}
			if err != nil {
				observations = append(observations, observation)
				return
			}

			// 两组使用同一份当前观测；off 只禁用知识，不把“没加载 Skill”记成根因错误。
			for _, mode := range []string{"off", "skills"} {
				started = time.Now()
				trace := []DiagnosisStep{}
				for i, tool := range []string{"read_profile_summary", "inspect_hotspots", "check_collection_quality", "inspect_call_paths"} {
					body, _, toolErr := diagnosisTool(tool, json.RawMessage(`{}`), c.Profile, task)
					if toolErr != nil {
						t.Fatalf("实际工具 %s 失败: %v", tool, toolErr)
					}
					trace = append(trace, DiagnosisStep{EvidenceID: []string{"E1", "E2", "E3", "E4"}[i], Tool: tool, Observation: body})
				}
				if mode == "skills" {
					knowledge, marshalErr := json.Marshal(DiagnosticKnowledge{Skills: skills, Cases: []DiagnosticCasePrior{}, PriorOnly: true, HistoryEnabled: false})
					if marshalErr != nil {
						t.Fatal(marshalErr)
					}
					trace = append(trace, DiagnosisStep{EvidenceID: "E5", Tool: "read_diagnostic_knowledge", Observation: knowledge})
				}
				variant := diagnosticEvaluationVariant{KnowledgeMode: mode, Guards: []diagnosticEvaluationGuard{}}
				check := func(name string, content string, steps []DiagnosisStep, accepted bool) {
					_, validationErr := validateDiagnosisReport(content, steps)
					actual := validationErr == nil
					variant.Guards = append(variant.Guards, diagnosticEvaluationGuard{Name: name, ExpectedAccepted: accepted, ActualAccepted: actual})
					if actual != accepted {
						t.Errorf("模式 %s 协议 %s 实际接受=%v，期望=%v，错误=%v", mode, name, actual, accepted, validationErr)
					}
				}
				check("current-observation", diagnosticEvaluationReport([]string{"E2", "E3"}, ""), trace, true)
				check("unknown-reference", diagnosticEvaluationReport([]string{"E999"}, ""), trace, false)
				check("forged-knowledge", diagnosticEvaluationReport([]string{"E2"}, "knowledge"), trace, false)
				check("forged-runtime", diagnosticEvaluationReport([]string{"E2"}, "agent_runtime"), trace, false)
				if mode == "skills" {
					check("prior-only-is-not-current-evidence", diagnosticEvaluationReport([]string{"E5"}, ""), trace, false)
					check("current-plus-prior", diagnosticEvaluationReport([]string{"E2", "E5"}, ""), trace, true)
					// 已匹配方法要求质量检查；删除真实工具观测后不能凭报告话术绕过。
					withoutQuality := slices.DeleteFunc(slices.Clone(trace), func(s DiagnosisStep) bool { return s.Tool == "check_collection_quality" })
					check("missing-required-quality-tool", diagnosticEvaluationReport([]string{"E2"}, ""), withoutQuality, false)
				}
				variant.ElapsedMS = float64(time.Since(started).Microseconds()) / 1000
				observation.Variants = append(observation.Variants, variant)
			}
			observations = append(observations, observation)
		})
	}
	if t.Failed() {
		return
	}
	if path := os.Getenv("DROP_DIAGNOSTIC_EVAL_OUTPUT"); path != "" {
		// 仅在调用方显式提供输出路径时保存安全计数，不导出原始观测或报告正文。
		body, err := json.MarshalIndent(map[string]any{
			"schema_version": 1, "mode": "deterministic_offline",
			"controller_oracle": true, "model_calls": 0,
			"observations": observations,
		}, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if err = os.WriteFile(path, append(body, '\n'), 0600); err != nil {
			t.Fatal(err)
		}
	}
}

func TestDiagnosticEvaluationExpiredOrForeignPriorFailsBeforeStorage(t *testing.T) {
	// 真正的来源重验入口先挡住过期和异主案例；其他权限/版本过滤由独立 PostgreSQL 集成测试覆盖。
	// 此处故意不提供 Repo，证明这两种早期拒绝不会访问存储，也不冒充完整数据库实测。
	s := &Suggestions{}
	u := domain.User{ID: "evaluation-owner"}
	for _, c := range []DiagnosticCase{
		{OwnerID: u.ID, ExpiresAt: time.Unix(1, 0)},
		{OwnerID: "another-owner", ExpiresAt: time.Now().Add(time.Hour)},
	} {
		if s.diagnosticCaseFresh(context.Background(), u, c, map[string]string{}) {
			t.Fatal("过期或异主先验不应有效")
		}
	}
}
