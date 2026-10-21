// 明确导入的受控资源聚合用于回归线上child92的来源缺口。
// 本文件只测试程序提案/数据库状态，不调用模型、采集器或生产接口。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"testing"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

func resourceFollowupStep(t *testing.T) DiagnosisStep {
	t.Helper()
	return DiagnosisStep{EvidenceID: "E1", Tool: "read_profile_summary", Observation: samplingRaw(t, resourceFixtureObservation(t))}
}

func TestFollowupResourceMeasurementRequiresActualPeaksAndFixedSource(t *testing.T) {
	step := resourceFollowupStep(t)
	if !followupResourceMeasurement(step) {
		t.Fatal("受控资源实际峰值未被识别")
	}
	for _, tc := range []struct {
		name string
		edit func(map[string]any, *DiagnosisStep)
	}{
		{"ordinary_cpu", func(v map[string]any, _ *DiagnosisStep) { v["kind"] = "PERF_CPU" }},
		{"bare_summary", func(v map[string]any, _ *DiagnosisStep) { delete(v, "resources") }},
		{"empty_peaks", func(v map[string]any, _ *DiagnosisStep) { v["resources"].(map[string]any)["peaks"] = map[string]any{} }},
		{"wrong_source", func(v map[string]any, _ *DiagnosisStep) { v["resources"].(map[string]any)["source"] = "model_claim" }},
		{"wrong_schema", func(v map[string]any, _ *DiagnosisStep) { v["resources"].(map[string]any)["schema_version"] = 2 }},
		{"zero_samples", func(v map[string]any, _ *DiagnosisStep) { v["resources"].(map[string]any)["sample_count"] = 0 }},
		{"wrong_unit", func(v map[string]any, _ *DiagnosisStep) {
			v["resources"].(map[string]any)["units"].(map[string]any)["rss_bytes"] = "milliseconds"
		}},
		{"null_peak", func(v map[string]any, _ *DiagnosisStep) {
			v["resources"].(map[string]any)["peaks"].(map[string]any)["rss_bytes"] = nil
		}},
		{"negative_peak", func(v map[string]any, _ *DiagnosisStep) {
			v["resources"].(map[string]any)["peaks"].(map[string]any)["rss_bytes"] = -1
		}},
		{"unknown_peak", func(v map[string]any, _ *DiagnosisStep) {
			v["resources"].(map[string]any)["peaks"].(map[string]any)["PRIVATE_PEAK"] = 1
		}},
		{"knowledge", func(_ map[string]any, s *DiagnosisStep) { s.Tool = "read_diagnostic_knowledge" }},
		{"topology", func(_ map[string]any, s *DiagnosisStep) { s.Tool = "inspect_service_topology" }},
	} {
		t.Run(tc.name, func(t *testing.T) {
			body, _ := diagnosisDecodeObject(step.Observation)
			candidate := step
			tc.edit(body, &candidate)
			candidate.Observation = samplingRaw(t, body)
			if followupResourceMeasurement(candidate) {
				t.Fatal("无实际资源测量的来源被当成测量证据")
			}
		})
	}
	zero, _ := diagnosisDecodeObject(step.Observation)
	zero["resources"].(map[string]any)["peaks"] = map[string]any{"rss_bytes": 0}
	step.Observation = samplingRaw(t, zero)
	if !followupResourceMeasurement(step) {
		t.Fatal("真实零峰值被错误当成缺失")
	}
}

func resourceFollowupFixture(t *testing.T) (*gorm.DB, *Suggestions, domain.User, domain.Task, DiagnosisRun, Investigation) {
	t.Helper()
	db, s, u, task := investigationFixture(t)
	var parameters domain.Parameters
	if err := json.Unmarshal(task.Parameters, &parameters); err != nil {
		t.Fatal(err)
	}
	parameters.FrequencyHz = 2
	params, _ := json.Marshal(parameters)
	if err := db.Model(&task).Updates(map[string]any{"kind": "RESOURCE_SNAPSHOT", "params": string(params)}).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("UPDATE analysis_jobs SET result=? WHERE task_id=?", string(resourceFixtureBytes(t, resourceFixture(t))), task.ID).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.First(&task, task.ID).Error; err != nil {
		t.Fatal(err)
	}
	run := requestDiagnosisFixture(t, s, u, task, "evidence", "resource-followup-source")
	if err := s.processDiagnosis(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := db.First(&run, run.ID).Error; err != nil || run.Status != "success" {
		t.Fatal("来源诊断夹具未完成", err, run.Status)
	}
	root := Investigation{RequestedBy: u.ID, AgentID: *task.AgentID, Goal: "明确合成资源补证提案", Parameters: task.Parameters, Consent: true, RequestKey: "resource-followup-root", InputDigest: repository.Digest("resource-followup-root"), Status: "diagnosing", Trace: json.RawMessage(`[]`), BaselineTaskID: &task.ID, DiagnosisID: &run.ID}
	if err := db.Create(&root).Error; err != nil {
		t.Fatal(err)
	}
	return db, s, u, task, run, root
}

func resourceFollowupArgs(kind string) json.RawMessage {
	frequency := 99
	if kind == "RESOURCE_SNAPSHOT" {
		frequency = 2
	}
	raw, _ := json.Marshal(map[string]any{"kind": kind, "duration_seconds": 5, "frequency_hz": frequency, "rationale": "根据当前真实测量补充其他信号", "evidence_ids": []string{"E1", "E4"}})
	return raw
}

func TestPostgresFollowupResourceMeasurementProposalAndSameKindNoStaging(t *testing.T) {
	db, s, _, task, run, root := resourceFollowupFixture(t)
	ctx := context.Background()
	trace := []DiagnosisStep{resourceFollowupStep(t), {EvidenceID: "E4", Tool: "check_collection_quality", Observation: json.RawMessage(`{"kind":"RESOURCE_SNAPSHOT","issues":[]}`)}}
	var initialTasks int64
	db.Model(&domain.Task{}).Count(&initialTasks)
	// 使用真实资源E1并提出其他采集器，仍只返回需人工确认的草稿。
	observation, err := s.proposeFollowup(ctx, run, resourceFollowupArgs("PERF_CPU"), trace)
	if err != nil {
		t.Fatal("真实E1资源测量被拒绝", err)
	}
	var proposal FollowupProposal
	if json.Unmarshal(observation, &proposal) != nil || proposal.Plan.Kind != "PERF_CPU" || !proposal.RequiresConfirmation {
		t.Fatal("跨信号草稿未保留确认边界", string(observation))
	}
	// 同一资源采集器重复请求不是工具错误，也不能以计划形态进入报告或后台。
	for index := 0; index < 2; index++ {
		same, err := s.proposeFollowup(ctx, run, resourceFollowupArgs("RESOURCE_SNAPSHOT"), trace)
		if err != nil {
			t.Fatal("重复采集没有获得非执行指导", err)
		}
		body, _ := diagnosisDecodeObject(same)
		if body["available"] != true || body["validated"] != false || body["reason"] != "current_collector_already_observed" || !strings.Contains(body["next"].(string), "propose_verification") {
			t.Fatal("重复采集指导不准确", body)
		}
		for _, key := range []string{"plan", "requires_confirmation"} {
			if _, exists := body[key]; exists {
				t.Fatal("重复请求仍携带可stage计划", body)
			}
		}
		candidateTrace := append(append([]DiagnosisStep{}, trace...), DiagnosisStep{EvidenceID: "E5", Tool: "propose_followup_collection", Observation: same})
		var report DiagnosisReport
		if json.Unmarshal(run.Report, &report) != nil {
			t.Fatal("已有报告无法读取")
		}
		report.FollowupPlans = followupProposals(candidateTrace)
		if len(report.FollowupPlans) != 0 {
			t.Fatal("重复采集仍进入followupProposals")
		}
		raw, _ := json.Marshal(report)
		if err := db.Model(&run).Update("report", string(raw)).Error; err != nil {
			t.Fatal(err)
		}
		if err := s.advanceInvestigationFollowups(ctx); err != nil {
			t.Fatal(err)
		}
		var n, tasks int64
		db.Model(&InvestigationFollowup{}).Where("investigation_id=?", root.ID).Count(&n)
		db.Model(&domain.Task{}).Count(&tasks)
		if n != 0 || tasks != initialTasks {
			t.Fatal("重复采集被实际stage或创建任务", n, tasks)
		}
	}
	// 对真正的跨采集器草稿走实际stage工作器，重复发现只产生一个待确认分支。
	var report DiagnosisReport
	_ = json.Unmarshal(run.Report, &report)
	report.FollowupPlans = followupProposals(append(trace, DiagnosisStep{EvidenceID: "E6", Tool: "propose_followup_collection", Observation: observation}))
	raw, _ := json.Marshal(report)
	if err := db.Model(&run).Update("report", string(raw)).Error; err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 2; index++ {
		if err := s.advanceInvestigationFollowups(ctx); err != nil {
			t.Fatal(err)
		}
	}
	var rows []InvestigationFollowup
	db.Where("investigation_id=?", root.ID).Find(&rows)
	if len(rows) != 1 || rows[0].Status != "proposed" || rows[0].TaskID != nil || rows[0].ParentTaskID != task.ID {
		t.Fatal("其他采集器草稿被自动确认、重复stage或换目标", rows)
	}
}

func TestPostgresFollowupResourceMeasurementRejectsTaskMismatchAndKeepsBudgets(t *testing.T) {
	db, s, _, task, run, root := resourceFollowupFixture(t)
	ctx := context.Background()
	trace := []DiagnosisStep{resourceFollowupStep(t), {EvidenceID: "E4", Tool: "check_collection_quality", Observation: json.RawMessage(`{}`)}}
	if err := db.Model(&task).Update("kind", "PERF_CPU").Error; err != nil {
		t.Fatal(err)
	}
	if _, err := s.proposeFollowup(ctx, run, resourceFollowupArgs("RESOURCE_SNAPSHOT"), trace); err == nil {
		t.Fatal("E1假资源kind盖过真实Repo任务类型")
	}
	if err := db.Model(&task).Update("kind", "RESOURCE_SNAPSHOT").Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Model(&domain.Agent{}).Where("id=?", *task.AgentID).Update("capabilities", `[]`).Error; err != nil {
		t.Fatal(err)
	}
	observation, err := s.proposeFollowup(ctx, run, resourceFollowupArgs("RESOURCE_SNAPSHOT"), trace)
	if err != nil {
		t.Fatal(err)
	}
	denied, _ := diagnosisDecodeObject(observation)
	if denied["error_code"] != "CAPABILITY_UNAVAILABLE" || denied["reason"] == "current_collector_already_observed" {
		t.Fatal("重复种类绕过实际能力核验", denied)
	}
	if err := db.Model(&domain.Agent{}).Where("id=?", *task.AgentID).Update("capabilities", `["RESOURCE_SNAPSHOT","PERF_CPU"]`).Error; err != nil {
		t.Fatal(err)
	}
	for index := 0; index < 2; index++ {
		row := InvestigationFollowup{InvestigationID: root.ID, ParentTaskID: task.ID, SourceDiagnosisID: run.ID, Plan: json.RawMessage(`{}`), PlanDigest: fmt.Sprint("prior-", index), Status: "rejected"}
		if err := db.Create(&row).Error; err != nil {
			t.Fatal(err)
		}
	}
	observation, err = s.proposeFollowup(ctx, run, resourceFollowupArgs("RESOURCE_SNAPSHOT"), trace)
	if err != nil {
		t.Fatal(err)
	}
	denied, _ = diagnosisDecodeObject(observation)
	if denied["available"] != false || denied["reason"] != "followup_budget_exhausted" {
		t.Fatal("重复种类改变了根调查预算", denied)
	}
}
