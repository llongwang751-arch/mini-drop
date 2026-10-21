// 知识层接入回归使用显式导入的合成数据与真实隔离数据库，不调用模型。
// 验证开关、幂等、当前测量证据约束，以及一次诊断保存的经验能被下一次有界检索并撤销。
package service

import (
	"context"
	"encoding/json"
	"testing"
)

func TestDiagnosisKnowledgeDigestPreservesLegacyAndSeparatesModes(t *testing.T) {
	raw := json.RawMessage(`{"imported_synthetic_fixture":true}`)
	old := diagnosisInputDigest(raw, "evidence", "检查", "")
	if old != diagnosisInputDigest(raw, "evidence", "检查", "off") {
		t.Fatal("迁移改变了旧记录的摘要")
	}
	skills := diagnosisInputDigest(raw, "evidence", "检查", "skills")
	experience := diagnosisInputDigest(raw, "evidence", "检查", "experience")
	if skills == old || experience == old || skills == experience {
		t.Fatal("不同知识模式共用了输入摘要")
	}
}

func TestDiagnosisKnowledgeRepeatedReadCannotHideRevokedPrior(t *testing.T) {
	old := DiagnosticKnowledge{Skills: []DiagnosticSkill{}, Cases: []DiagnosticCasePrior{{ID: 1, PriorOnly: true}}, PriorOnly: true, HistoryEnabled: true}
	raw, _ := json.Marshal(old)
	trace := []DiagnosisStep{{EvidenceID: "E2", Tool: "read_diagnostic_knowledge", Observation: raw}}
	if err := validateDiagnosticKnowledgeSnapshot(trace, old); err != nil {
		t.Fatal(err)
	}
	fresh := old
	fresh.Cases = []DiagnosticCasePrior{}
	if validateDiagnosticKnowledgeSnapshot(trace, fresh) == nil {
		t.Fatal("后一次空读取掩盖了本次已加载案例的撤销")
	}
}

func TestDiagnosisKnowledgeRequiresObservedToolsAndCurrentEvidence(t *testing.T) {
	catalog, err := BuiltinDiagnosticSkills()
	if err != nil {
		t.Fatal(err)
	}
	knowledge := DiagnosticKnowledge{Skills: []DiagnosticSkill{catalog[0]}, Cases: []DiagnosticCasePrior{}, PriorOnly: true}
	raw, _ := json.Marshal(knowledge)
	trace := []DiagnosisStep{{EvidenceID: "E1", Tool: "read_profile_summary"}, {EvidenceID: "E2", Tool: "read_diagnostic_knowledge", Observation: raw}}
	if validateDiagnosticSkillUse(trace) == nil {
		t.Fatal("Skill必需工具未运行仍验收通过")
	}
	for _, name := range catalog[0].RequiredTools {
		trace = append(trace, DiagnosisStep{EvidenceID: "E" + string(rune('1'+len(trace))), Tool: name})
	}
	if err := validateDiagnosticSkillUse(trace); err != nil {
		t.Fatal(err)
	}
	for _, ref := range []string{"E2", "E1"} {
		content := `{"summary":"合成验证报告","hypotheses":[{"claim":"仍需当前证据验证","evidence_ids":["` + ref + `"],"verification":"同负载受控复测"}],"limitations":["导入夹具"]}`
		_, err := validateDiagnosisReport(content, trace)
		if (err == nil) != (ref == "E1") {
			t.Fatal("历史先验被当成本次测量", ref, err)
		}
	}
}

func TestPostgresDiagnosisKnowledgeDefaultReuseAndRevocation(t *testing.T) {
	db, s, u, task := diagnosisFixture(t)
	ctx := context.Background()
	request := func(key, mode string) DiagnosisRun {
		t.Helper()
		run, err := s.RequestDiagnosis(ctx, u, task.ID, DiagnosisInput{Goal: "合成数据的知识接入验收", Mode: "evidence", KnowledgeMode: mode}, "knowledge-fixture", key)
		if err != nil {
			t.Fatal(err)
		}
		return run
	}
	complete := func(run DiagnosisRun) DiagnosisReport {
		t.Helper()
		if err := s.processDiagnosis(ctx); err != nil {
			t.Fatal(err)
		}
		if err := db.First(&run, run.ID).Error; err != nil || run.Status != "success" {
			t.Fatal("知识诊断未完成", err, run.Status, run.ErrorCode)
		}
		var report DiagnosisReport
		if err := json.Unmarshal(run.Report, &report); err != nil {
			t.Fatal(err)
		}
		return report
	}
	first := request("knowledge-first-fixture", "")
	if first.KnowledgeMode != "experience" {
		t.Fatal("新请求未默认启用知识")
	}
	if _, err := s.RequestDiagnosis(ctx, u, task.ID, DiagnosisInput{Goal: first.Goal, Mode: "evidence", KnowledgeMode: "off"}, "fixture", first.RequestKey); err == nil {
		t.Fatal("改变知识模式却复用了幂等键")
	}
	report := complete(first)
	if report.Knowledge == nil || len(report.Knowledge.Skills) < 1 || len(report.Knowledge.Cases) != 0 {
		t.Fatal("默认知识未真正进入报告")
	}
	c, err := s.CreateDiagnosticCase(ctx, u, task.ID, first.ID, DiagnosticCaseInput{Outcome: "reviewed_correction", HumanConfirmed: true, CorrectionCode: "causality_not_established"}, "knowledge-case-fixture", "fixture")
	if err != nil {
		t.Fatal(err)
	}
	report = complete(request("knowledge-reuse-fixture", "experience"))
	if report.Knowledge == nil || len(report.Knowledge.Cases) != 1 || report.Knowledge.Cases[0].ID != c.ID {
		t.Fatal("下一会话未检索有效经验")
	}
	if err := s.RevokeDiagnosticCase(ctx, u, c.ID, "fixture"); err != nil {
		t.Fatal(err)
	}
	report = complete(request("knowledge-revoked-fixture", "experience"))
	if report.Knowledge == nil || len(report.Knowledge.Cases) != 0 {
		t.Fatal("已撤销案例仍被复用")
	}
	report = complete(request("knowledge-disabled-fixture", "off"))
	if report.Knowledge != nil {
		t.Fatal("关闭模式仍加载了知识")
	}
}
