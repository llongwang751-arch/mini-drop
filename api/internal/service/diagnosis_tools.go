// Package service：定义有限诊断工具及其确定性观测，模型只能选择工具和提出待验证假设。
// 返回的采样单位、哈希符号、局限和 E 编号构成证据；启发式提示不能替代实测，更不能承诺根因或收益。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"strings"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

type DiagnosisStep struct {
	EvidenceID  string          `json:"evidence_id"`
	Tool        string          `json:"tool"`
	Observation json.RawMessage `json:"observation"`
}
type DiagnosisHypothesis struct {
	Claim                    string   `json:"claim"`
	EvidenceIDs              []string `json:"evidence_ids"`
	Verification             string   `json:"verification"`
	Kind                     string   `json:"kind,omitempty"`
	FactIDs                  []string `json:"fact_ids,omitempty"`
	UnverifiedInterpretation string   `json:"unverified_interpretation,omitempty"`
}
type DiagnosisPlan struct {
	Kind            string `json:"kind"`
	DurationSeconds int    `json:"duration_seconds"`
	FrequencyHz     int    `json:"frequency_hz"`
	Reason          string `json:"reason"`
}
type DiagnosisReport struct {
	ReportVersion int                     `json:"report_version,omitempty"`
	Facts         []DiagnosisVerifiedFact `json:"facts,omitempty"`
	FollowupPlans []FollowupProposal      `json:"followup_plans,omitempty"`
	Summary       string                  `json:"summary"`
	Hypotheses    []DiagnosisHypothesis   `json:"hypotheses"`
	Limitations   []string                `json:"limitations"`
	Plan          *DiagnosisPlan          `json:"plan,omitempty"`
	Engine        string                  `json:"engine"`
	Model         string                  `json:"model,omitempty"`
	AgentRuntime  *AgentRuntime           `json:"agent_runtime,omitempty"`
	Knowledge     *DiagnosticKnowledge    `json:"knowledge,omitempty"`
}

func diagnosisToolNames() []string {
	return []string{"read_profile_summary", "inspect_hotspots", "check_collection_quality", "inspect_call_paths", "propose_verification"}
}

// diagnosisSymbolHint 只产生固定枚举内的语义提示，属于本地启发式。
// 栈符号本身不能证明阻塞、内存压力或真实缺陷，模型报告必须保留这个不确定边界。
func diagnosisSymbolHint(name string) string {
	name = strings.ToLower(name)
	for _, group := range []struct {
		hint    string
		matches []string
	}{
		{"lock_or_wait", []string{"futex", "mutex", "pthread_cond", "locksupport", "park", "semaphore"}},
		{"garbage_collection", []string{"gcworker", "garbage", "g1collect", "zgc", "gc.collect", "runtime.gc"}},
		{"allocation", []string{"malloc", "calloc", "realloc", "operator new", "allocobject", "runtime.mallocgc"}},
		{"io_or_network", []string{"epoll", "poll_wait", "recv", "sendmsg", "read_syscall", "sys_read", "sys_write", "socket"}},
		{"scheduler", []string{"schedule", "context_switch", "sched_yield", "runtime.gosched"}},
	} {
		for _, match := range group.matches {
			if strings.Contains(name, match) {
				return group.hint
			}
		}
	}
	return "unknown"
}

// diagnosisTool 的工具始终绑定已授权运行的 task 和 attempt。
// 调度器不接受模型指定的任务、URL、shell、路径、SQL、PID 或 Agent 标识，模型不能借参数换目标。
func diagnosisTool(name string, args json.RawMessage, raw json.RawMessage, task domain.Task) (json.RawMessage, *DiagnosisPlan, error) {
	var params struct {
		Kind            string `json:"kind,omitempty"`
		DurationSeconds int    `json:"duration_seconds,omitempty"`
		FrequencyHz     int    `json:"frequency_hz,omitempty"`
		Rank            int    `json:"rank,omitempty"`
	}
	if len(args) > 1024 {
		return nil, nil, domain.Fail("AI_TOOL_REJECTED", "工具参数过大")
	}
	dec := json.NewDecoder(strings.NewReader(string(args)))
	dec.DisallowUnknownFields()
	if len(args) == 0 || strings.TrimSpace(string(args)) == "null" || dec.Decode(&params) != nil {
		return nil, nil, domain.Fail("AI_TOOL_REJECTED", "工具参数无效")
	}
	var extra any
	if dec.Decode(&extra) != io.EOF {
		return nil, nil, domain.Fail("AI_TOOL_REJECTED", "工具参数包含尾部数据")
	}
	if name != "propose_verification" && (params.Kind != "" || params.DurationSeconds != 0 || params.FrequencyHz != 0) {
		return nil, nil, domain.Fail("AI_TOOL_REJECTED", "只读工具不接受采集参数")
	}
	if name != "inspect_call_paths" && params.Rank != 0 {
		return nil, nil, domain.Fail("AI_TOOL_REJECTED", "该工具不接受热点排名")
	}
	var profile struct {
		Kind         string `json:"kind"`
		TotalSamples int64  `json:"total_samples"`
		NodeCount    int64  `json:"node_count"`
		Top          []struct {
			Name         string  `json:"name"`
			SelfPercent  float64 `json:"self_percent"`
			TotalPercent float64 `json:"total_percent"`
		} `json:"top_functions"`
	}
	if json.Unmarshal(raw, &profile) != nil {
		return nil, nil, domain.Fail("RESULT_NOT_READY", "结果无法解析")
	}
	for _, f := range profile.Top {
		if math.IsNaN(f.SelfPercent) || math.IsInf(f.SelfPercent, 0) || f.SelfPercent < 0 || f.SelfPercent > 100 || math.IsNaN(f.TotalPercent) || math.IsInf(f.TotalPercent, 0) || f.TotalPercent < 0 || f.TotalPercent > 100 {
			return nil, nil, domain.Fail("INVALID_EVIDENCE", "热点比例超出范围")
		}
	}
	marshal := func(v any) (json.RawMessage, *DiagnosisPlan, error) { b, e := json.Marshal(v); return b, nil, e }
	switch name {
	case "read_profile_summary":
		b, e := RedactedSummary(raw)
		if e != nil {
			return nil, nil, e
		}
		var summary map[string]any
		json.Unmarshal(b, &summary)
		var meta struct {
			Summary struct {
				Unit string `json:"unit"`
			} `json:"summary"`
		}
		json.Unmarshal(raw, &meta)
		unit := "unknown"
		for _, known := range []string{"samples", "cpu_ticks", "bytes", "objects", "allocations", "operations", "nanoseconds", "milliseconds", "count"} {
			if meta.Summary.Unit == known {
				unit = known
			}
		}
		summary["measurement_unit"] = unit
		summary["stack_scope"] = "profile_dependent"
		if task.Kind == "PROC_CPU" {
			summary["stack_scope"] = "process_and_thread_counters_only_no_function_stacks"
		}
		if task.Kind == "RESOURCE_SNAPSHOT" {
			resources, err := diagnosisResourceProjection(raw, task)
			if err != nil {
				return nil, nil, err
			}
			summary["stack_scope"] = "resource_counters_only_no_function_stacks"
			if resources != nil {
				summary["resources"] = resources
			}
		}
		return marshal(summary)
	case "inspect_call_paths":
		rank := params.Rank
		if rank == 0 {
			rank = 1
		}
		if rank < 1 || rank > 10 {
			return nil, nil, domain.Fail("AI_TOOL_REJECTED", "热点排名须为1–10")
		}
		if rank > len(profile.Top) {
			return marshal(map[string]any{"available": false, "reason": "selected_hotspot_not_present"})
		}
		b, e := diagnosisCallPaths(raw, profile.Top[rank-1].Name)
		return b, nil, e
	case "inspect_hotspots":
		quality, err := diagnosisSamplingQuality(raw, task, profile.TotalSamples)
		if err != nil {
			return nil, nil, err
		}
		top := []map[string]any{}
		for i, f := range profile.Top {
			if i >= 10 {
				break
			}
			row := map[string]any{"rank": i + 1, "symbol": "symbol_" + repository.Digest(f.Name)[:12], "symbol_hint": diagnosisSymbolHint(f.Name), "self_percent": f.SelfPercent, "total_percent": f.TotalPercent}
			if quality != nil && (f.Name == "[py-spy unresolved stack]" || f.Name == "[py-spy unresolved frame]") {
				row["unresolved"] = true
			}
			top = append(top, row)
		}
		return marshal(map[string]any{"top_functions": top, "hint_source": "local_symbol_heuristic_v1", "interpretation": "self为叶帧权重，total为包含子调用权重；symbol_hint为本地符号词形分类，仅作线索，不能证明锁等待或根因。比例不证明请求延迟或因果。原符号仅保留在授权结果页面。"})
	case "check_collection_quality":
		var p domain.Parameters
		if err := json.Unmarshal(task.Parameters, &p); err != nil {
			return nil, nil, err
		}
		issues := []string{}
		if task.Kind == "PROC_CPU" {
			issues = append(issues, "procfs仅提供进程/线程CPU tick，不提供函数调用栈；不能用线程名定位源码函数")
		}
		if profile.TotalSamples <= 0 && task.Kind != "RESOURCE_SNAPSHOT" {
			issues = append(issues, "没有正采样权重，无法支持热点判断")
		}
		if task.Kind == "RESOURCE_SNAPSHOT" {
			issues = append(issues, "资源快照不产生函数调用栈；请引用实际resources聚合计数和峰值，零profile权重不能用来判定资源压力或采集失败")
		}
		if p.DurationSeconds > 0 && p.DurationSeconds < 10 {
			issues = append(issues, "采集窗口短于10秒，需在稳定负载下复测")
		}
		if p.Source == "import" || task.Kind == "IMPORT_COLLAPSED" {
			issues = append(issues, "离线导入数据：无法核实现场负载、采集时钟和丢样")
		}
		quality, err := diagnosisSamplingQuality(raw, task, profile.TotalSamples)
		if err != nil {
			return nil, nil, err
		}
		if quality != nil && quality["normalized"] == true {
			issues = append(issues, "py-spy包含无法解析的栈或帧，已用明确未知占位符保留全部样本权重；占位符不是业务函数，不能用于源码定位或证明根因")
		}
		observation := map[string]any{"kind": task.Kind, "duration_seconds": p.DurationSeconds, "frequency_hz": p.FrequencyHz, "issues": issues, "unknowns": []string{"未关联业务请求延迟、吞吐和错误率", "单次快照不能证明性能回退或修复收益", "未采集源码、符号地址映射及丢样率，不输出文件行号"}}
		if quality != nil {
			observation["sampling_quality"] = quality
		}
		return marshal(observation)
	case "propose_verification":
		if params.Kind != "" && params.Kind != task.Kind {
			return nil, nil, domain.Fail("AI_TOOL_REJECTED", "复测采集类型不能跨越当前任务")
		}
		kind := params.Kind
		var original domain.Parameters
		if json.Unmarshal(task.Parameters, &original) != nil {
			return nil, nil, domain.Fail("INVALID_EVIDENCE", "参数不可读")
		}
		if kind == "" {
			kind = task.Kind
		}
		k, ok := domain.Kind(kind)
		if original.Source == "import" || kind == "IMPORT_COLLAPSED" {
			return marshal(map[string]any{"requires_manual_import": true, "next": "以同一负载和采集配置重新导入对照文件，再使用结果对比验证；不执行导入文件。"})
		}
		if !ok || !k.AgentRunner || kind == "COMPOSITE" || kind == "SCRIPT" || kind == "BOLT" {
			return marshal(map[string]any{"requires_manual_configuration": true, "next": "请在任务表单审阅工具、依赖和完整配置，本轮不生成可自动提交的采集草案。"})
		}
		// 保留原运行器和目标策略；跨采集类型的操作必须回到普通任务表单，
		// 由用户审阅该语言或工具所需的参数，不能让复测建议偷偷改变采集语义。
		if kind != task.Kind {
			return nil, nil, domain.Fail("AI_TOOL_REJECTED", "本轮复测仅允许相同采集类型，其他类型请在任务表单配置")
		}
		duration := params.DurationSeconds
		if duration == 0 {
			duration = k.DefaultDuration
			if duration < 10 {
				duration = 10
			}
		}
		frequency := params.FrequencyHz
		if duration < 1 || duration > 60 || frequency < 0 || frequency > 999 {
			return nil, nil, domain.Fail("AI_TOOL_REJECTED", "复测预算超限")
		}
		if frequency == 0 {
			frequency = k.DefaultFrequency
		}
		var p domain.Parameters
		if json.Unmarshal(task.Parameters, &p) != nil {
			return nil, nil, domain.Fail("INVALID_EVIDENCE", "参数不可读")
		}
		p.Source = ""
		p.Traceparent = ""
		p.DurationSeconds = duration
		p.FrequencyHz = frequency
		if err := domain.ValidateKindParameters(kind, p); err != nil {
			return nil, nil, domain.Fail("AI_TOOL_REJECTED", "复测参数不符合采集契约")
		}
		plan := &DiagnosisPlan{Kind: kind, DurationSeconds: duration, FrequencyHz: frequency, Reason: "相同目标、稳定负载下复测，使用结果对比检查热点分布；请同时记录业务P95/P99、吞吐、错误率及采集开销。"}
		b, e := json.Marshal(plan)
		return b, plan, e
	}
	return nil, nil, domain.Fail("AI_TOOL_REJECTED", "未知诊断工具")
}

// diagnose 把已授权结果映射为编号证据和受限诊断流程；调用方提供 invoke 时，工具观测必须先通过持久化与权限栅栏。
func (s *Suggestions) diagnose(ctx context.Context, run DiagnosisRun, owner string, trace *[]DiagnosisStep) (*DiagnosisReport, error) {
	var task domain.Task
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error { var e error; task, e = s.diagnosisFence(ctx, tx, run, owner); return e })
	if err != nil {
		return nil, err
	}
	if run.TelemetryServiceID != "" {
		for _, step := range *trace {
			if step.Tool == "read_profile_summary" {
				var metadata struct {
					Revision string `json:"observability_revision"`
				}
				if s.Observability == nil || json.Unmarshal(step.Observation, &metadata) != nil || metadata.Revision != s.Observability.Digest {
					return nil, domain.Fail("INPUT_CHANGED", "恢复时业务数据源登记版本已改变")
				}
			}
		}
	}
	raw, err := s.Repo.Result(ctx, run.TaskID)
	if err != nil {
		return nil, err
	}
	var plan *DiagnosisPlan
	for _, step := range *trace {
		if step.Tool == "propose_verification" {
			var restored DiagnosisPlan
			if json.Unmarshal(step.Observation, &restored) == nil && restored.Kind != "" {
				plan = &restored
			}
		}
	}
	var verification VerificationRun
	if err := s.Repo.Context(ctx).Where("candidate_diagnosis_id=?", run.ID).Limit(1).Find(&verification).Error; err != nil {
		return nil, err
	}
	invoke := func(name string, args json.RawMessage) (json.RawMessage, error) {
		if err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error { _, err := s.diagnosisFence(ctx, tx, run, owner); return err }); err != nil {
			return nil, err
		}
		limit := 8
		if run.ReportProtocol >= 2 {
			limit = 15
		}
		if len(*trace) >= limit {
			return nil, domain.Fail("AI_BUDGET_EXCEEDED", "诊断工具总数超过本协议预算")
		}
		var observation json.RawMessage
		var p *DiagnosisPlan
		var err error
		if name == "query_business_signals" {
			observation, err = s.queryBusinessSignals(ctx, run, args)
		} else if name == "inspect_service_topology" {
			observation, err = s.inspectServiceTopology(ctx, run, args)
		} else if name == "propose_followup_collection" {
			observation, err = s.proposeFollowup(ctx, run, args, *trace)
		} else if name == "read_diagnostic_knowledge" {
			u, e := s.Repo.User(ctx, run.RequestedBy)
			if e != nil {
				return nil, e
			}
			var knowledge DiagnosticKnowledge
			knowledge, err = s.ReadDiagnosticKnowledge(ctx, u, run, args, run.KnowledgeMode == "experience")
			if err == nil {
				// 重复读取不能用“新的空结果”覆盖模型已经看过的旧案例。
				// 本次运行固定第一次加载的快照；任何漂移都要求重新诊断。
				err = validateDiagnosticKnowledgeSnapshot(*trace, knowledge)
			}
			if err == nil {
				observation, err = json.Marshal(knowledge)
			}
		} else if name == "compare_profiles" {
			var empty struct{}
			decoder := json.NewDecoder(strings.NewReader(string(args)))
			decoder.DisallowUnknownFields()
			var tail any
			if decoder.Decode(&empty) != nil || decoder.Decode(&tail) != io.EOF || strings.TrimSpace(string(args)) == "null" {
				return nil, domain.Fail("AI_TOOL_REJECTED", "对比工具不接受参数")
			}
			if verification.ID == 0 {
				observation = json.RawMessage(`{"available":false,"reason":"no_bound_verification"}`)
			} else {
				var comparison ProfileComparison
				comparison, err = verificationComparison(ctx, s.Repo.Context(ctx), verification)
				if err == nil {
					observation, err = json.Marshal(comparison)
				}
			}
		} else {
			observation, p, err = diagnosisTool(name, args, raw, task)
		}
		if err != nil {
			return nil, err
		}
		if name == "read_profile_summary" {
			var summary map[string]any
			_ = json.Unmarshal(observation, &summary)
			summary["verification_available"] = verification.ID > 0
			collectors, catalogErr := s.followupCatalog(ctx, run)
			if catalogErr != nil {
				return nil, catalogErr
			}
			summary["available_followup_collectors"] = collectors
			summary["observability_available"] = run.TelemetryServiceID != ""
			if run.TelemetryServiceID != "" && s.Observability != nil {
				summary["observability_revision"] = s.Observability.Digest
			}
			observation, _ = json.Marshal(summary)
		}
		if len(observation) > 16<<10 {
			return nil, domain.Fail("AI_BUDGET_EXCEEDED", "工具输出过大")
		}
		step := DiagnosisStep{EvidenceID: fmt.Sprintf("E%d", len(*trace)+1), Tool: name, Observation: observation}
		*trace = append(*trace, step)
		if err := s.persistDiagnosisStep(ctx, run, owner, *trace); err != nil {
			return nil, err
		}
		if p != nil {
			plan = p
		}
		b, _ := json.Marshal(step)
		return b, nil
	}
	var first json.RawMessage
	for _, step := range *trace {
		if step.Tool == "read_profile_summary" {
			first, _ = json.Marshal(step)
			break
		}
	}
	if len(first) == 0 {
		first, err = invoke("read_profile_summary", json.RawMessage(`{}`))
		if err != nil {
			return nil, err
		}
	}
	if run.KnowledgeMode == "skills" || run.KnowledgeMode == "experience" {
		if diagnosisEvidenceID(*trace, "read_diagnostic_knowledge") == "" {
			if _, err := invoke("read_diagnostic_knowledge", json.RawMessage(`{}`)); err != nil {
				return nil, err
			}
		}
	}
	var report *DiagnosisReport
	if run.Mode == "model" {
		if !s.AgentAvailable() {
			return nil, domain.Fail("AI_UNAVAILABLE", "未配置外部模型")
		}
		if s.Config.AgentBackend == "agentscope" {
			report, err = s.diagnosisAgentScope(ctx, run, owner, first, invoke, trace)
		} else {
			report, err = s.diagnosisModelWithProtocol(ctx, run.Goal, first, invoke, trace, run.ReportProtocol)
		}
		if err != nil {
			return nil, err
		}
		report.Engine = "model"
		report.Model = s.Config.Model
	} else {
		for _, tool := range []string{"inspect_hotspots", "check_collection_quality", "inspect_call_paths", "propose_verification"} {
			if _, err := invoke(tool, json.RawMessage(`{}`)); err != nil {
				return nil, err
			}
		}
		if verification.ID > 0 {
			if _, err := invoke("compare_profiles", json.RawMessage(`{}`)); err != nil {
				return nil, err
			}
		}
		report = &DiagnosisReport{Engine: "evidence", Summary: "已完成本地证据检查。请结合热点与采集质量审阅定位线索；本模式不调用模型。", Hypotheses: []DiagnosisHypothesis{{Claim: "优先检查当前结果中self占比较高的叶函数及total占比较高的调用路径。", EvidenceIDs: []string{diagnosisEvidenceID(*trace, "read_profile_summary"), diagnosisEvidenceID(*trace, "inspect_hotspots")}, Verification: "在热点视图核对原符号及调用关系，再按相同负载复测，禁止把样本权重当作请求延迟。"}, {Claim: "当前证据不能独立证明业务瓶颈或修复收益。", EvidenceIDs: []string{diagnosisEvidenceID(*trace, "check_collection_quality")}, Verification: "补齐业务P95/P99、吞吐、错误率、负载与采集开销；对照采集后使用结果对比。"}}, Limitations: []string{"本地规则检查，无模型推理、无因果验证。"}}
	}
	if err := validateDiagnosticSkillUse(*trace); err != nil {
		return nil, err
	}
	report.Knowledge, err = s.validateLoadedDiagnosticKnowledge(ctx, run, *trace)
	if err != nil {
		return nil, err
	}
	report.Plan = plan
	report.FollowupPlans = followupProposals(*trace)
	report.Limitations = append(report.Limitations, "模型结论为待验证假设，不代表已确认缺陷；不提供未经符号映射验证的源码行号。", "原始profile、原符号、主机、PID、路径和凭据不发送模型；问题文本会发送，请勿填入敏感信息。")
	return report, nil
}
