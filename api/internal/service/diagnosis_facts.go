// 本模块把模型选择的观察主张与真正的工具返回逐字段核对，再由程序生成权威事实描述。
// 不用中文关键词判断一句话真假，也不让另一个模型给原模型背书。自由解释另存为未核验内容。
package service

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"drop/api/internal/domain"
)

// DiagnosisVerifiedFact 保存来源工具、有限字段路径、声明单位与程序复算值。
// Description 只由 Go 模板生成；模型输入不能自报验证通过或提供任意事实正文。
type DiagnosisVerifiedFact struct {
	ID          string          `json:"id"`
	EvidenceID  string          `json:"evidence_id"`
	Tool        string          `json:"tool"`
	Path        string          `json:"path"`
	Operator    string          `json:"operator"`
	Value       json.RawMessage `json:"value"`
	Unit        string          `json:"unit"`
	Description string          `json:"description"`
}

type diagnosisFactRequest struct {
	ID         string          `json:"id"`
	EvidenceID string          `json:"evidence_id"`
	Path       string          `json:"path"`
	Operator   string          `json:"operator"`
	Value      json.RawMessage `json:"value"`
	Unit       string          `json:"unit"`
}

type diagnosisHypothesisV2 struct {
	Kind                     string   `json:"kind"`
	FactIDs                  []string `json:"fact_ids"`
	EvidenceIDs              []string `json:"evidence_ids"`
	UnverifiedInterpretation string   `json:"unverified_interpretation,omitempty"`
}

// 来源为模型建议时不接受 source_evidence_id，避免把自行建议的数字装成工具产物。
// 工具计划和人工导入指令分别验证，缺少计划字段不能按默认值补成“已校验的计划”。
type diagnosisVerificationRequest struct {
	Origin           string `json:"origin"`
	Kind             string `json:"kind"`
	SourceEvidenceID string `json:"source_evidence_id,omitempty"`
	DurationSeconds  *int   `json:"duration_seconds,omitempty"`
	FrequencyHz      *int   `json:"frequency_hz,omitempty"`
}

type diagnosisModelReportV2 struct {
	ReportVersion        int                            `json:"report_version"`
	Facts                []diagnosisFactRequest         `json:"facts"`
	Hypotheses           []diagnosisHypothesisV2        `json:"hypotheses"`
	VerificationRequests []diagnosisVerificationRequest `json:"verification_requests"`
}

const diagnosisFactReportInstructions = `最终回复必须是report_version=2的纯JSON，只允许下列顶层字段：report_version、facts、hypotheses、verification_requests。不要输出自由summary、claim、verification、limitations、plan、facts核验标签、runtime或knowledge；权威描述由程序生成。
facts为1至24项，通常只选3至8项与问题和假设直接相关的事实，不要为了填满24项而加入无关字段。每项只含{id,evidence_id,path,operator,value,unit}；id为F1等唯一编号。evidence_id必须是实际返回的当前观测E编号，历史知识不能作事实来源。path使用下面列出的有限JSON Pointer，数组下标从0开始。value必须与该来源的实际值完全一致；不能换单位、跨E编号借字段或补默认值。operator只有eq、count、count_positive_self，后两者仅在明确列出的数组使用；node_count等已返回数字是标量，必须eq，不能count。
可用字段：read_profile_summary的/total_weight(单位取E1.measurement_unit)、/node_count(count)、/kind(category)、/measurement_unit(unit)、/verification_available(boolean)；inspect_hotspots的/top_functions/0/self_percent与total_percent(percent)、rank(rank)、symbol_hint(category)，索引最多9；/top_functions可用count_positive_self，unit=count，只代表已返回Top表正self项目数，不代表整个树唯一叶帧。check_collection_quality的/duration_seconds(seconds)、/frequency_hz(hertz)、/kind(category)，/issues与/unknowns可count(count)。
inspect_call_paths的/available、/tree_truncated、/occurrences_truncated、/truncated为boolean；/matched_occurrences与/examined_nodes为count；/occurrences/0/self_weight与inclusive_weight沿用观测单位，callees_truncated与ancestors_truncated为boolean；/occurrences/0/top_callees与ancestors可count(count)，索引最多4。propose_verification只可引用实际返回的/duration_seconds(seconds)、/frequency_hz(hertz)、/kind(category)、/requires_manual_import(boolean)、/requires_manual_configuration(boolean)，没有返回就不能填值。
compare_profiles的/comparable(boolean)、/unit(unit)、/baseline_weight与/candidate_weight(沿用观测单位)、/outcome与/root_cause_status(category)；/differences/0/baseline_self_percent与candidate_self_percent(percent)、delta_percentage_points(percentage_points)，索引最多9。
仅RESOURCE_SNAPSHOT的read_profile_summary实际返回resources时可引用：/resources/sample_count(count)，/resources/peaks/cpu_percent(percent，多核可超过100)、rss_bytes(bytes)、vms_bytes(bytes)、threads(count)、fd_count(count)、memory_used_bytes(bytes)、memory_total_bytes(bytes)。这些路径均为eq；缺失/null峰值不能补0。资源峰值不是时间加权平均或函数热点，memory_*主机口径不能冒充进程RSS；total_weight=0与空函数栈不代表资源采集失败。引用真实资源事实后，不能仅凭一个峰值断言资源瓶颈，应结合负载与基线；无法满足某类假设前提时使用insufficient_evidence。
业务工具只可引用固定结构：query_business_signals的/available(boolean)，/metrics/0/value按name映射单位（latency_p95_ms→milliseconds、error_rate→ratio、throughput_rps→requests_per_second，实际row.unit必须相同）；/metrics/0/name(category)、/metrics/0/unit(unit)；/logs/0/duration_ms(milliseconds)、status_code(status_code)；/traces/0/duration_ms(milliseconds)、span_count与error_count(count)，以上索引最多9。query_business_signals的/changes/0/timestamp(timestamp，RFC3339时间)、/changes/0/change_type(category，仅deployment/configuration/feature_flag)可eq；inspect_service_topology的/nodes、/edges可count(count)，/causality_proven(boolean)只描述程序标记；登记依赖关系不是故障因果。不能把自由日志正文、任意标签或变更描述用作已核验事实。
hypotheses为1至4项，只保留有事实支持的方向，无需填满4项。每项只含kind、fact_ids、evidence_ids及可选unverified_interpretation。kind只允许cpu_hotspot、allocation_gc、lock_io、normal_load、insufficient_evidence、mixed_signals、business_latency、downstream_dependency、deployment_regression；这些是待验证方向，不能自报根因确认。fact_ids引用本报告已核验事实，每项1至8个；evidence_ids须包含这些事实来源，每项1至8个。allocation_gc须引用allocation/garbage_collection提示或内存采集kind事实；lock_io须引用等待/IO提示或IO_TRACE kind；mixed_signals须在所引用事实中包含allocation/GC、lock/IO、业务毫秒时长、change_type这四类提示中的至少两类，CPU占比、质量、未知项和拓扑本身不算这四类提示；cpu_hotspot须引用inspect_hotspots正self占比或正self计数，资源cpu_percent不替代函数热点；business_latency须引用实际业务时长事实；downstream_dependency须引用正的登记edges计数；deployment_regression须引用实际query_business_signals固定change_type事实，空changes或拓扑不支持它。没有对应证据时移除该方向或使用insufficient_evidence，不要用mixed_signals汇总任意字段。unverified_interpretation如填写最多600字节，是明确未核验的模型解释，不会作为权威事实。不要在其中泄露敏感信息。
verification_requests为1至3项，只允许origin、kind、source_evidence_id、duration_seconds、frequency_hz。origin=tool_plan时kind=repeat_collection，source_evidence_id必须指向真正返回时长和频率的propose_verification；提供参数时必须逐项等于该工具返回。origin=tool_instruction时只允许kind=manual_import和source_evidence_id，且工具确实requires_manual_import=true，不得加时长频率。origin=model_proposal时禁止source_evidence_id；kind可repeat_collection、collect_memory、collect_wait_io、inspect_source、correlate_business_signals、controlled_intervention、manual_import，时长可选1至60秒、频率可选0至999Hz，仅为待审建议，不称作工具已生成或校验的计划。
示例值只示意结构，必须替换成实际观测：{"report_version":2,"facts":[{"id":"F1","evidence_id":"E1","path":"/measurement_unit","operator":"eq","value":"samples","unit":"unit"}],"hypotheses":[{"kind":"insufficient_evidence","fact_ids":["F1"],"evidence_ids":["E1"]}],"verification_requests":[{"origin":"model_proposal","kind":"correlate_business_signals"}]}。不带Markdown或额外字段。`

var diagnosisFactID = regexp.MustCompile(`^F[1-9][0-9]{0,2}$`)
var diagnosisEvidenceNumber = regexp.MustCompile(`^E[1-9][0-9]{0,2}$`)
var diagnosisHotspotPath = regexp.MustCompile(`^/top_functions/([0-9])/(rank|self_percent|total_percent|symbol_hint)$`)
var diagnosisOccurrencePath = regexp.MustCompile(`^/occurrences/([0-4])/(self_weight|inclusive_weight|callees_truncated|ancestors_truncated|top_callees|ancestors)$`)
var diagnosisCalleePath = regexp.MustCompile(`^/occurrences/([0-4])/top_callees/([0-2])/(self_weight|inclusive_weight)$`)
var diagnosisDifferencePath = regexp.MustCompile(`^/differences/([0-9])/(baseline_self_percent|candidate_self_percent|delta_percentage_points)$`)
var diagnosisSignalMetricPath = regexp.MustCompile(`^/metrics/([0-9])/(value|name|unit)$`)
var diagnosisSignalLogPath = regexp.MustCompile(`^/logs/([0-9])/(duration_ms|status_code)$`)
var diagnosisSignalTracePath = regexp.MustCompile(`^/traces/([0-9])/(duration_ms|span_count|error_count)$`)
var diagnosisSignalChangePath = regexp.MustCompile(`^/changes/([0-9])/(timestamp|change_type)$`)

func invalidDiagnosisFact() error {
	return domain.Fail("AI_INVALID_REPORT", "结构化事实、单位、数值或工具来源未通过核验")
}

// 专用反馈只携带程序枚举与已验证的定位信息，不携带候选值、任意字段路径、上游异常或观测正文。
// 非导出方法把实现限制在本服务包；运行时仍在序列化前重验白名单，普通domain错误不会自动获得详细回显。
type diagnosisReportFeedbackProvider interface {
	safeDiagnosisReportFeedback() diagnosisReportFeedback
}

type diagnosisReportFeedback struct {
	Reason       string `json:"reason"`
	Section      string `json:"section"`
	Index        *int   `json:"index,omitempty"`
	Field        string `json:"field,omitempty"`
	EvidenceID   string `json:"evidence_id,omitempty"`
	ExpectedTool string `json:"expected_tool,omitempty"`
	ActualTool   string `json:"actual_tool,omitempty"`
	ExpectedUnit string `json:"expected_unit,omitempty"`
}

type diagnosisReportValidationError struct{ feedback diagnosisReportFeedback }

func (e *diagnosisReportValidationError) Error() string {
	return "AI_INVALID_REPORT: 结构化报告未通过当前观测核验"
}
func (e *diagnosisReportValidationError) Unwrap() error { return invalidDiagnosisFact() }
func (e *diagnosisReportValidationError) safeDiagnosisReportFeedback() diagnosisReportFeedback {
	return e.feedback
}

var diagnosisFeedbackRepairs = map[string]string{
	"fact_id_invalid":                        "facts.id须为F1等唯一有效编号。",
	"fact_id_duplicate":                      "为每条facts分配唯一编号，并同步hypotheses.fact_ids。",
	"fact_source_not_found":                  "只能引用实际trace返回的当前观测E编号，不能补造来源。",
	"fact_operator_invalid":                  "operator只允许eq、count或count_positive_self，并遵守对应路径白名单。",
	"fact_path_not_allowed":                  "该工具的path/operator组合不在白名单；按系统协议选择已返回的固定字段，或移除此事实并同步假设引用。",
	"fact_scalar_operator_invalid":           "该路径返回标量，operator必须是eq；count与count_positive_self只能用于协议明确允许的数组。",
	"fact_path_missing":                      "实际观测没有该字段；不能补默认值，可移除此事实并同步假设引用。",
	"fact_unit_mismatch":                     "使用expected_unit标明的原观测单位，不能换算或给不同来源套用单位。",
	"fact_value_mismatch":                    "value必须逐项等于实际观测或程序聚合结果，不能使用近似或推测值。",
	"fact_observation_invalid":               "来源结构、标量或范围不满足事实协议；不要补造缺失值，改用其他有效观测。",
	"fact_observation_unavailable":           "available=false的业务源不允许引用其数值；可保留/available=false事实或使用其他有效来源。",
	"fact_measurement_not_comparable":        "对照未证明同口径，不能给两侧权重套用同一单位。",
	"verification_parameters_out_of_bounds":  "时长只允许1至60秒、频率只允许0至999Hz；工具计划参数还必须逐项等于实际返回。",
	"verification_source_not_found":          "source_evidence_id只能指向trace中实际返回的propose_verification编号。",
	"verification_source_tool_mismatch":      "改为origin=model_proposal并删除source_evidence_id，或在普通轮内调用真正propose_verification后引用其返回；不得增加权限或预算",
	"verification_source_invalid":            "来源观测没有合法的复测计划结构，不能用默认值补成已校验计划。",
	"verification_instruction_mismatch":      "tool_instruction仅允许实际requires_manual_import=true的manual_import，且不接受时长或频率。",
	"verification_plan_unavailable":          "tool_plan仅允许propose_verification真实返回的repeat_collection草案，人工导入/独立配置不能冒充可执行计划。",
	"verification_plan_parameters_missing":   "来源没有返回完整合法的时长/频率，不能补默认值；改为model_proposal并删除source_evidence_id，或引用真正可用的复测草案。",
	"verification_parameter_mismatch":        "工具计划的时长/频率必须与所引用propose_verification实际返回逐项相等；自行建议须改为model_proposal并删除source_evidence_id。",
	"model_proposal_source_forbidden":        "origin=model_proposal时必须删除source_evidence_id，不把模型建议归因给任何工具。",
	"verification_kind_invalid":              "仅使用系统协议列出的kind，不得自造执行动作。",
	"verification_origin_invalid":            "origin只允许tool_plan、tool_instruction或model_proposal。",
	"hypothesis_shape_invalid":               "fact_ids与evidence_ids分别须有1至8项，解释须遵守长度与敏感信息限制；不能用空引用补成假设。",
	"hypothesis_evidence_invalid":            "evidence_ids只能包含当前trace真实且互不重复的E编号，不能补造观测。",
	"hypothesis_fact_reference_invalid":      "fact_ids只能包含本报告已核验且互不重复的事实，并在evidence_ids中包含每条事实的真实来源。",
	"hypothesis_kind_invalid":                "只使用系统协议列出的假设kind；证据不足时可使用insufficient_evidence，不能自造根因类型。",
	"hypothesis_allocation_evidence_missing": "allocation_gc需要本假设引用实际allocation/garbage_collection提示或内存采集kind事实；没有时移除此假设或改为insufficient_evidence。",
	"hypothesis_lock_evidence_missing":       "lock_io需要本假设引用实际等待/IO提示或IO_TRACE kind事实；不能用纯热点数值替代。没有时移除或改为insufficient_evidence。",
	"hypothesis_mixed_evidence_missing":      "mixed_signals需要本假设引用至少两类实际提示；多个同类字段不是不同信号类别。没有时移除或改为insufficient_evidence。",
	"hypothesis_cpu_evidence_missing":        "cpu_hotspot需要本假设引用inspect_hotspots中正self占比或正self计数；不能用总权重、资源CPU百分比或拓扑替代。",
	"hypothesis_latency_evidence_missing":    "business_latency需要本假设引用实际业务毫秒时长事实；不能用CPU采集时长或登记拓扑替代。",
	"hypothesis_dependency_evidence_missing": "downstream_dependency需要本假设引用登记拓扑中正的edges计数；依赖方向仍不是故障因果。",
	"hypothesis_change_evidence_missing":     "deployment_regression必须引用query_business_signals实际返回的change_type事实；空changes、拓扑nodes/edges或时间重合均不能替代。无变更事实时移除该假设或改为insufficient_evidence。",
}

func diagnosisFeedbackKnownTool(tool string) bool {
	return slices.Contains([]string{"read_profile_summary", "inspect_hotspots", "check_collection_quality", "inspect_call_paths", "propose_verification", "compare_profiles", "read_diagnostic_knowledge", "query_business_signals", "inspect_service_topology", "propose_followup_collection"}, tool)
}

func (f diagnosisReportFeedback) safeJSON() (string, bool) {
	repair, ok := diagnosisFeedbackRepairs[f.Reason]
	if !ok || f.Index == nil {
		return "", false
	}
	if f.Section == "facts" {
		if *f.Index < 0 || *f.Index >= 24 {
			return "", false
		}
	} else if f.Section == "verification_requests" {
		if *f.Index < 0 || *f.Index >= 3 {
			return "", false
		}
	} else if f.Section == "hypotheses" {
		if *f.Index < 0 || *f.Index >= 4 {
			return "", false
		}
	} else {
		return "", false
	}
	if !slices.Contains([]string{"id", "evidence_id", "path", "operator", "unit", "value", "origin", "kind", "source_evidence_id", "duration_seconds", "frequency_hz", "fact_ids", "evidence_ids", "unverified_interpretation"}, f.Field) {
		return "", false
	}
	if f.EvidenceID != "" && !diagnosisEvidenceNumber.MatchString(f.EvidenceID) {
		return "", false
	}
	if f.ExpectedTool != "" && f.ExpectedTool != "propose_verification" || f.ActualTool != "" && !diagnosisFeedbackKnownTool(f.ActualTool) {
		return "", false
	}
	if f.ExpectedUnit != "" && !slices.Contains([]string{"samples", "cpu_ticks", "bytes", "objects", "allocations", "operations", "nanoseconds", "milliseconds", "count", "unknown", "seconds", "hertz", "percent", "percentage_points", "ratio", "requests_per_second", "category", "unit", "boolean", "rank", "status_code", "timestamp"}, f.ExpectedUnit) {
		return "", false
	}
	raw, err := json.Marshal(struct {
		diagnosisReportFeedback
		Repair string `json:"repair"`
	}{f, repair})
	return string(raw), err == nil && len(raw) <= 1024
}

func diagnosisFeedbackError(reason, field string, step DiagnosisStep) *diagnosisReportValidationError {
	f := diagnosisReportFeedback{Reason: reason, Field: field}
	if diagnosisEvidenceNumber.MatchString(step.EvidenceID) {
		f.EvidenceID = step.EvidenceID
	}
	if diagnosisFeedbackKnownTool(step.Tool) {
		f.ActualTool = step.Tool
	}
	return &diagnosisReportValidationError{feedback: f}
}

// 索引来自校验器循环而非模型值；复制错误对象，防止并发回调或缓存复用改变既有定位。
func diagnosisFeedbackAt(err error, section string, index int) error {
	if typed, ok := err.(*diagnosisReportValidationError); ok {
		copied := *typed
		copied.feedback.Section = section
		copied.feedback.Index = &index
		return &copied
	}
	return err
}

func diagnosisDecodeObject(raw json.RawMessage) (map[string]any, error) {
	if len(raw) == 0 || len(raw) > 64<<10 {
		return nil, invalidDiagnosisFact()
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value map[string]any
	if decoder.Decode(&value) != nil || value == nil {
		return nil, invalidDiagnosisFact()
	}
	var tail any
	if decoder.Decode(&tail) != io.EOF {
		return nil, invalidDiagnosisFact()
	}
	return value, nil
}

// diagnosisFactValue 只按预先审核过的短路径取值。调用者先核对白名单，
// 不支持通配符、递归、JSONPath 表达式或把路径当代码执行。
func diagnosisFactValue(value any, path string) (any, bool) {
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	if len(path) > 160 || !strings.HasPrefix(path, "/") || len(parts) > 5 {
		return nil, false
	}
	for _, part := range parts {
		switch current := value.(type) {
		case map[string]any:
			var ok bool
			value, ok = current[part]
			if !ok {
				return nil, false
			}
		case []any:
			index, err := strconv.Atoi(part)
			if err != nil || index < 0 || index >= len(current) {
				return nil, false
			}
			value = current[index]
		default:
			return nil, false
		}
	}
	return value, value != nil
}

func diagnosisMeasurementUnit(trace []DiagnosisStep) string {
	for _, step := range trace {
		if step.Tool != "read_profile_summary" {
			continue
		}
		body, err := diagnosisDecodeObject(step.Observation)
		if err != nil {
			continue
		}
		if unit, ok := body["measurement_unit"].(string); ok && slices.Contains([]string{"samples", "cpu_ticks", "bytes", "objects", "allocations", "operations", "nanoseconds", "milliseconds", "count", "unknown"}, unit) {
			return unit
		}
	}
	return "unknown"
}

func diagnosisNumeric(value any) (*big.Rat, bool) {
	number, ok := value.(json.Number)
	if !ok || len(number.String()) > 80 {
		return nil, false
	}
	// 短JSON也能携带巨大指数，先限制幅度再构造大数，避免事实核验自身被放大内存。
	if position := strings.IndexAny(number.String(), "eE"); position >= 0 {
		exponentText := number.String()[position+1:]
		if len(exponentText) > 4 {
			return nil, false
		}
		exponent, err := strconv.Atoi(exponentText)
		if err != nil || exponent < -100 || exponent > 100 {
			return nil, false
		}
	}
	rational, valid := new(big.Rat).SetString(number.String())
	return rational, valid
}

// 精确有理数比较避免浮点舍入把 70.000000000000000001 误当成 70。
func diagnosisSameScalar(expected any, actual json.RawMessage) bool {
	if len(actual) == 0 || len(actual) > 256 {
		return false
	}
	decoder := json.NewDecoder(bytes.NewReader(actual))
	decoder.UseNumber()
	var submitted any
	if decoder.Decode(&submitted) != nil {
		return false
	}
	var tail any
	if decoder.Decode(&tail) != io.EOF {
		return false
	}
	switch value := expected.(type) {
	case json.Number:
		a, okA := diagnosisNumeric(value)
		b, okB := diagnosisNumeric(submitted)
		return okA && okB && a.Cmp(b) == 0
	case string:
		other, ok := submitted.(string)
		return ok && value == other
	case bool:
		other, ok := submitted.(bool)
		return ok && value == other
	}
	return false
}

// 每个工具只暴露有限标量或固定聚合操作。字段不存在时拒绝，不能从其他 E 编号
// 借一个同名值补齐，也不能从历史案例、解释文本或任意嵌套对象制造“事实”。
func diagnosisAllowedFact(tool, path, operator, measurementUnit string) (unit, label string, valid bool) {
	if operator == "count_positive_self" && tool == "inspect_hotspots" && path == "/top_functions" {
		return "count", "已返回热点表的正 self 权重项目数（不是全树叶帧数）", true
	}
	if operator == "count" {
		if tool == "inspect_service_topology" && (path == "/nodes" || path == "/edges") {
			return "count", "登记拓扑的节点或边数量（不证明故障传播）", true
		}
		if tool == "check_collection_quality" && (path == "/issues" || path == "/unknowns") {
			return "count", "工具返回的质量问题或未知项数量", true
		}
		if tool == "inspect_call_paths" && diagnosisOccurrencePath.MatchString(path) && (strings.HasSuffix(path, "/top_callees") || strings.HasSuffix(path, "/ancestors")) {
			return "count", "该出现位置已返回的局部上下文项目数（受截断标记约束）", true
		}
		return "", "", false
	}
	if operator != "eq" {
		return "", "", false
	}
	paths := map[string]map[string][2]string{
		"read_profile_summary": {
			"/total_weight": {measurementUnit, "本次观测总权重"}, "/node_count": {"count", "本次树节点数"},
			"/kind": {"category", "观测类型"}, "/measurement_unit": {"unit", "观测单位"}, "/verification_available": {"boolean", "复测比较可用状态"},
			"/resources/sample_count":             {"count", "本次资源观测样本数"},
			"/resources/peaks/cpu_percent":        {"percent", "资源观测CPU峰值（多核可超过100%，不是时间加权均值）"},
			"/resources/peaks/rss_bytes":          {"bytes", "资源观测RSS峰值"},
			"/resources/peaks/vms_bytes":          {"bytes", "资源观测虚拟内存峰值"},
			"/resources/peaks/threads":            {"count", "资源观测线程数峰值"},
			"/resources/peaks/fd_count":           {"count", "资源观测文件描述符数峰值"},
			"/resources/peaks/memory_used_bytes":  {"bytes", "主机可见已用内存峰值（不是进程RSS）"},
			"/resources/peaks/memory_total_bytes": {"bytes", "主机可见总内存值（不是进程RSS）"},
		},
		"check_collection_quality": {"/kind": {"category", "采集类型"}, "/duration_seconds": {"seconds", "工具记录的采集时长（0 表示未记录或不适用）"}, "/frequency_hz": {"hertz", "工具记录的频率（0 表示未记录或不适用）"}},
		"inspect_call_paths":       {"/available": {"boolean", "调用路径可用状态"}, "/tree_truncated": {"boolean", "树遍历是否截断"}, "/occurrences_truncated": {"boolean", "出现位置是否省略"}, "/truncated": {"boolean", "调用上下文是否截断"}, "/matched_occurrences": {"count", "已遍历树中的匹配位置数"}, "/examined_nodes": {"count", "已遍历节点数"}},
		"propose_verification":     {"/duration_seconds": {"seconds", "工具实际返回的复测时长"}, "/frequency_hz": {"hertz", "工具实际返回的复测频率"}, "/kind": {"category", "工具实际返回的复测类型"}, "/requires_manual_import": {"boolean", "工具要求人工导入"}, "/requires_manual_configuration": {"boolean", "工具要求人工配置"}},
		"compare_profiles":         {"/comparable": {"boolean", "程序判定的可比状态"}, "/unit": {"unit", "对照单位"}, "/baseline_weight": {measurementUnit, "基线权重"}, "/candidate_weight": {measurementUnit, "候选权重"}, "/root_cause_status": {"category", "程序记录的因果验证状态"}, "/outcome": {"category", "程序记录的对照状态"}},
		"query_business_signals":   {"/available": {"boolean", "业务观测可用状态"}},
		"inspect_service_topology": {"/causality_proven": {"boolean", "登记拓扑的因果证明状态"}},
	}
	if item, ok := paths[tool][path]; ok {
		return item[0], item[1], true
	}
	if tool == "inspect_hotspots" && diagnosisHotspotPath.MatchString(path) {
		if strings.HasSuffix(path, "/rank") {
			return "rank", "返回热点排名", true
		}
		if strings.HasSuffix(path, "/symbol_hint") {
			return "category", "本地词形提示（不是根因证明）", true
		}
		return "percent", "已返回热点的 self/total 权重占比（不是耗时比例）", true
	}
	if tool == "inspect_call_paths" && (diagnosisOccurrencePath.MatchString(path) || diagnosisCalleePath.MatchString(path)) {
		if strings.HasSuffix(path, "_truncated") {
			return "boolean", "局部上下文截断状态", true
		}
		if strings.HasSuffix(path, "_weight") {
			return measurementUnit, "已返回调用位置或子调用的权重", true
		}
	}
	if tool == "compare_profiles" && diagnosisDifferencePath.MatchString(path) {
		if strings.HasSuffix(path, "/delta_percentage_points") {
			return "percentage_points", "热点占比差值（百分点，不是请求性能收益）", true
		}
		return "percent", "对照热点占比", true
	}
	if tool == "query_business_signals" {
		if diagnosisSignalMetricPath.MatchString(path) {
			if strings.HasSuffix(path, "/name") {
				return "category", "业务指标固定类型", true
			}
			if strings.HasSuffix(path, "/unit") {
				return "unit", "业务指标单位", true
			}
			return "signal_metric", "业务指标实际数值（相关信号，不证明根因）", true
		}
		if diagnosisSignalLogPath.MatchString(path) {
			if strings.HasSuffix(path, "/status_code") {
				return "status_code", "已返回日志状态码", true
			}
			return "milliseconds", "已返回日志时长", true
		}
		if diagnosisSignalTracePath.MatchString(path) {
			if strings.HasSuffix(path, "/duration_ms") {
				return "milliseconds", "已返回链路时长", true
			}
			return "count", "已返回链路span或错误数量", true
		}
		if diagnosisSignalChangePath.MatchString(path) {
			if strings.HasSuffix(path, "/timestamp") {
				return "timestamp", "已返回变更时间（时间重合不证明因果）", true
			}
			return "category", "已返回固定变更类型（不证明性能回退）", true
		}
	}
	return "", "", false
}

func diagnosisFactScalarType(value any, unit string) bool {
	if unit == "boolean" {
		_, ok := value.(bool)
		return ok
	}
	if unit == "timestamp" {
		text, ok := value.(string)
		if !ok || len(text) > 40 {
			return false
		}
		_, err := time.Parse(time.RFC3339Nano, text)
		return err == nil
	}
	if unit == "category" || unit == "unit" {
		text, ok := value.(string)
		return ok && len(text) > 0 && len(text) <= 100 && !strings.ContainsAny(text, "<>\x00\r\n") && !strings.Contains(text, "://")
	}
	number, ok := diagnosisNumeric(value)
	if !ok {
		return false
	}
	if unit == "percentage_points" {
		return number.Cmp(big.NewRat(-100, 1)) >= 0 && number.Cmp(big.NewRat(100, 1)) <= 0
	}
	if number.Sign() < 0 {
		return false
	}
	if unit == "percent" {
		return number.Cmp(big.NewRat(100, 1)) <= 0
	}
	if unit == "ratio" {
		return number.Cmp(big.NewRat(1, 1)) <= 0
	}
	if slices.Contains([]string{"count", "rank", "status_code", "hertz"}, unit) && !number.IsInt() {
		return false
	}
	if unit == "status_code" && number.Cmp(big.NewRat(599, 1)) > 0 {
		return false
	}
	return true
}

func diagnosisResolveFact(request diagnosisFactRequest, step DiagnosisStep, measurementUnit string) (DiagnosisVerifiedFact, error) {
	bad := func(reason, field string) (DiagnosisVerifiedFact, error) {
		return DiagnosisVerifiedFact{}, diagnosisFeedbackError(reason, field, step)
	}
	if !slices.Contains([]string{"eq", "count", "count_positive_self"}, request.Operator) {
		return bad("fact_operator_invalid", "operator")
	}
	unit, label, allowed := diagnosisAllowedFact(step.Tool, request.Path, request.Operator, measurementUnit)
	if !allowed {
		if _, _, scalarAllowed := diagnosisAllowedFact(step.Tool, request.Path, "eq", measurementUnit); scalarAllowed && request.Operator != "eq" {
			return bad("fact_scalar_operator_invalid", "operator")
		}
		return bad("fact_path_not_allowed", "path")
	}
	body, err := diagnosisDecodeObject(step.Observation)
	if err != nil {
		return bad("fact_observation_invalid", "evidence_id")
	}
	if step.Tool == "query_business_signals" && request.Path != "/available" && body["available"] != true {
		return bad("fact_observation_unavailable", "evidence_id")
	}
	if unit == "signal_metric" {
		prefix := strings.TrimSuffix(request.Path, "/value")
		name, okName := diagnosisFactValue(body, prefix+"/name")
		actualUnit, okUnit := diagnosisFactValue(body, prefix+"/unit")
		units := map[string]string{"latency_p95_ms": "milliseconds", "error_rate": "ratio", "throughput_rps": "requests_per_second"}
		metricName, validName := name.(string)
		unit = units[metricName]
		if !okName || !okUnit || !validName || unit == "" || actualUnit != unit {
			return bad("fact_observation_invalid", "unit")
		}
	}
	if step.Tool == "compare_profiles" && (request.Path == "/baseline_weight" || request.Path == "/candidate_weight") {
		// 不可比结果的两侧可能使用不同单位，不能把基线单位套到候选数值上。
		observedUnit, ok := body["unit"].(string)
		if body["comparable"] != true || !ok || observedUnit != measurementUnit {
			return bad("fact_measurement_not_comparable", "evidence_id")
		}
	}
	if request.Unit != unit {
		err := diagnosisFeedbackError("fact_unit_mismatch", "unit", step)
		err.feedback.ExpectedUnit = unit
		return DiagnosisVerifiedFact{}, err
	}
	value, exists := diagnosisFactValue(body, request.Path)
	if !exists {
		return bad("fact_path_missing", "path")
	}
	if step.Tool == "query_business_signals" && strings.HasSuffix(request.Path, "/change_type") {
		changeType, ok := value.(string)
		if !ok || !slices.Contains([]string{"deployment", "configuration", "feature_flag"}, changeType) {
			return bad("fact_observation_invalid", "value")
		}
	}
	if request.Operator == "count" || request.Operator == "count_positive_self" {
		rows, ok := value.([]any)
		if !ok || len(rows) > 64 {
			return bad("fact_observation_invalid", "operator")
		}
		count := len(rows)
		if request.Operator == "count_positive_self" {
			count = 0
			for _, row := range rows {
				item, valid := row.(map[string]any)
				if !valid {
					return bad("fact_observation_invalid", "operator")
				}
				number, valid := diagnosisNumeric(item["self_percent"])
				if !valid || number.Sign() < 0 || number.Cmp(big.NewRat(100, 1)) > 0 {
					return bad("fact_observation_invalid", "value")
				}
				if number.Sign() > 0 {
					count++
				}
			}
		}
		value = json.Number(strconv.Itoa(count))
	}
	validScalar := diagnosisFactScalarType(value, unit)
	if step.Tool == "read_profile_summary" && strings.HasPrefix(request.Path, "/resources/") {
		// 资源CPU是多核计数口径，独立有界验证；不放宽函数self/total占比的0至100约束。
		validScalar = body["kind"] == "RESOURCE_SNAPSHOT" && diagnosisResourceFactScalar(body, request.Path, value)
	}
	if !validScalar {
		return bad("fact_observation_invalid", "value")
	}
	if !diagnosisSameScalar(value, request.Value) {
		return bad("fact_value_mismatch", "value")
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		return bad("fact_observation_invalid", "value")
	}
	return DiagnosisVerifiedFact{ID: request.ID, EvidenceID: step.EvidenceID, Tool: step.Tool, Path: request.Path, Operator: request.Operator, Value: encoded, Unit: unit, Description: fmt.Sprintf("%s：%s=%s（%s）", step.EvidenceID, label, string(encoded), unit)}, nil
}

func diagnosisV2Trace(trace []DiagnosisStep) (map[string]DiagnosisStep, error) {
	if len(trace) == 0 || len(trace) > 64 {
		return nil, invalidDiagnosisFact()
	}
	steps := map[string]DiagnosisStep{}
	for _, step := range trace {
		if !diagnosisEvidenceNumber.MatchString(step.EvidenceID) {
			return nil, invalidDiagnosisFact()
		}
		if _, exists := steps[step.EvidenceID]; exists {
			return nil, invalidDiagnosisFact()
		}
		steps[step.EvidenceID] = step
	}
	return steps, nil
}

func diagnosisSafeInterpretation(text string) bool {
	return len(text) <= 600 && !diagnosisMarkup.MatchString(text) && !strings.Contains(text, "https://") && !strings.Contains(text, "http://") && !strings.ContainsAny(text, "\x00\r")
}

func diagnosisVerificationText(request diagnosisVerificationRequest, steps map[string]DiagnosisStep) (string, error) {
	bad := func(reason, field string, step DiagnosisStep) (string, error) {
		return "", diagnosisFeedbackError(reason, field, step)
	}
	if request.DurationSeconds != nil && (*request.DurationSeconds < 1 || *request.DurationSeconds > 60) {
		return bad("verification_parameters_out_of_bounds", "duration_seconds", DiagnosisStep{})
	}
	if request.FrequencyHz != nil && (*request.FrequencyHz < 0 || *request.FrequencyHz > 999) {
		return bad("verification_parameters_out_of_bounds", "frequency_hz", DiagnosisStep{})
	}
	switch request.Origin {
	case "tool_plan", "tool_instruction":
		step, ok := steps[request.SourceEvidenceID]
		if !ok {
			return bad("verification_source_not_found", "source_evidence_id", DiagnosisStep{})
		}
		if step.Tool != "propose_verification" {
			err := diagnosisFeedbackError("verification_source_tool_mismatch", "source_evidence_id", step)
			err.feedback.ExpectedTool = "propose_verification"
			return "", err
		}
		body, err := diagnosisDecodeObject(step.Observation)
		if err != nil {
			return bad("verification_source_invalid", "source_evidence_id", step)
		}
		if request.Origin == "tool_instruction" {
			if request.Kind != "manual_import" || body["requires_manual_import"] != true || request.DurationSeconds != nil || request.FrequencyHz != nil {
				return bad("verification_instruction_mismatch", "kind", step)
			}
			return fmt.Sprintf("工具%s仅要求人工导入同条件对照数据，没有返回可执行采集计划或时长/频率；后续采集参数需要另外审阅。", step.EvidenceID), nil
		}
		if request.Kind != "repeat_collection" || body["requires_manual_import"] == true || body["requires_manual_configuration"] == true {
			return bad("verification_plan_unavailable", "kind", step)
		}
		duration, okD := body["duration_seconds"].(json.Number)
		frequency, okF := body["frequency_hz"].(json.Number)
		if !okD || !okF {
			return bad("verification_plan_parameters_missing", "source_evidence_id", step)
		}
		d, errD := strconv.Atoi(duration.String())
		f, errF := strconv.Atoi(frequency.String())
		if errD != nil || errF != nil || d < 1 || d > 60 || f < 0 || f > 999 {
			return bad("verification_plan_parameters_missing", "source_evidence_id", step)
		}
		if request.DurationSeconds != nil && *request.DurationSeconds != d {
			return bad("verification_parameter_mismatch", "duration_seconds", step)
		}
		if request.FrequencyHz != nil && *request.FrequencyHz != f {
			return bad("verification_parameter_mismatch", "frequency_hz", step)
		}
		kind, validKind := body["kind"].(string)
		if !validKind || kind == "" || len(kind) > 64 {
			return bad("verification_source_invalid", "source_evidence_id", step)
		}
		return fmt.Sprintf("工具%s实际返回同类复测草案：%d秒、%dHz；草案仍需审阅，未由报告自动执行，也未完成因果验证。", step.EvidenceID, d, f), nil
	case "model_proposal":
		if request.SourceEvidenceID != "" {
			return bad("model_proposal_source_forbidden", "source_evidence_id", DiagnosisStep{})
		}
		actions := map[string]string{"repeat_collection": "在相同目标、单位与稳定负载下补充对照采集", "collect_memory": "另行授权内存、分配或GC观测", "collect_wait_io": "另行授权线程等待或IO观测", "inspect_source": "通过授权源码映射核对业务语义", "correlate_business_signals": "对齐请求延迟、吞吐、错误率与采样窗口", "controlled_intervention": "实施受控干预并进行功能和性能对照", "manual_import": "人工导入可比的对照数据"}
		action, ok := actions[request.Kind]
		if !ok {
			return bad("verification_kind_invalid", "kind", DiagnosisStep{})
		}
		parameters := ""
		if request.DurationSeconds != nil {
			parameters += fmt.Sprintf("；建议时长%d秒", *request.DurationSeconds)
		}
		if request.FrequencyHz != nil {
			parameters += fmt.Sprintf("；建议频率%dHz", *request.FrequencyHz)
		}
		return "模型待审建议（不是工具返回或已校验计划）：" + action + parameters + "。执行前须独立核验采集器契约和权限；本报告不执行该建议。", nil
	}
	return bad("verification_origin_invalid", "origin", DiagnosisStep{})
}

func diagnosisV2Summary(trace []DiagnosisStep) (string, []string, error) {
	unit := diagnosisMeasurementUnit(trace)
	var summary map[string]any
	var hotspots []any
	for _, step := range trace {
		if step.Tool != "read_profile_summary" && step.Tool != "inspect_hotspots" {
			continue
		}
		body, err := diagnosisDecodeObject(step.Observation)
		if err != nil {
			return "", nil, err
		}
		if step.Tool == "read_profile_summary" && summary == nil {
			summary = body
		}
		if step.Tool == "inspect_hotspots" && hotspots == nil {
			hotspots, _ = body["top_functions"].([]any)
		}
	}
	if summary == nil || hotspots == nil || len(hotspots) > 10 {
		return "", nil, invalidDiagnosisFact()
	}
	weight, validWeight := diagnosisNumeric(summary["total_weight"])
	kind, validKind := summary["kind"].(string)
	if !validWeight || weight.Sign() < 0 || !validKind || len(kind) > 64 || !regexp.MustCompile(`^[A-Z_0-9]+$`).MatchString(kind) {
		return "", nil, invalidDiagnosisFact()
	}
	if kind == "RESOURCE_SNAPSHOT" {
		return diagnosisResourceV2Summary(summary)
	}
	positive := 0
	parts := []string{}
	for index, row := range hotspots {
		item, ok := row.(map[string]any)
		if !ok {
			return "", nil, invalidDiagnosisFact()
		}
		number, valid := diagnosisNumeric(item["self_percent"])
		if !valid || number.Sign() < 0 || number.Cmp(big.NewRat(100, 1)) > 0 {
			return "", nil, invalidDiagnosisFact()
		}
		if number.Sign() > 0 {
			positive++
		}
		if index < 3 {
			parts = append(parts, fmt.Sprintf("排名%d self占比%s%%", index+1, item["self_percent"].(json.Number).String()))
		}
	}
	text := fmt.Sprintf("本次%s观测单位为%s，总权重%s。已返回热点表中%d项具有正self权重", kind, unit, summary["total_weight"].(json.Number).String(), positive)
	if len(parts) > 0 {
		text += "；" + strings.Join(parts, "，")
	}
	text += "。这些是当前工具范围内的描述，不构成已确认业务根因或优化收益。"
	limitations := []string{"程序核验事实只保证所列字段、数值、单位和出处与本次工具返回一致，不证明因果或自由模型解释。", "权重与占比不能直接换算为请求延迟、CPU利用率或性能收益；正常高负载也可能产生热点。"}
	if kind == "IMPORT_COLLAPSED" {
		limitations = append(limitations, "导入数据的现场来源、采样时钟、负载与丢样未核实；树结构及权重不能证明真实CPU执行或分配行为。")
	}
	if unit == "unknown" {
		limitations = append(limitations, "测量单位未知，停止跨单位与耗时收益判断。")
	}
	if weight.Sign() == 0 {
		limitations = append(limitations, "没有正观测权重，应补充证据，不能给出热点根因。")
	}
	for _, step := range trace {
		if step.Tool == "query_business_signals" {
			body, err := diagnosisDecodeObject(step.Observation)
			if err == nil && body["available"] == false {
				limitations = append(limitations, "本次存在不可用的业务观测源；缺失数值不能补零或当作正常证据。")
				break
			}
		}
	}
	return text, limitations, nil
}

// 资源计数与调用栈使用不同口径。没有函数权重不等于没有采样，峰值也不等于持续压力或根因。
func diagnosisResourceV2Summary(summary map[string]any) (string, []string, error) {
	limitations := []string{
		"程序核验只保证资源字段、数值、单位和出处与当前聚合一致，不证明因果或自由模型解释。",
		"资源快照没有函数调用栈；CPU与内存峰值不是时间加权平均，不能直接认定瓶颈或优化收益。",
		"资源作用域与负载基线需独立核对；主机可见memory字段不等同目标进程RSS，多核CPU百分比可超过100%。",
	}
	resources, exists := summary["resources"].(map[string]any)
	if !exists {
		limitations = append(limitations, "当前报告没有可用的资源聚合；不能把空热点表或零函数权重解释为CPU空闲。")
		return "本次为资源快照，当前工具未返回可引用的资源聚合；函数权重和空热点表不用于判断资源状态。", limitations, nil
	}
	count := resources["sample_count"]
	if !diagnosisResourceFactScalar(summary, "/resources/sample_count", count) {
		return "", nil, invalidDiagnosisFact()
	}
	countJSON, _ := json.Marshal(count)
	parts := []string{}
	peaks, _ := resources["peaks"].(map[string]any)
	for _, field := range []struct{ key, label, unit string }{
		{"cpu_percent", "CPU峰值", "%"}, {"rss_bytes", "RSS峰值", "bytes"},
		{"vms_bytes", "虚拟内存峰值", "bytes"}, {"threads", "线程数峰值", "count"},
		{"fd_count", "文件描述符数峰值", "count"},
		{"memory_used_bytes", "主机可见已用内存峰值", "bytes"},
		{"memory_total_bytes", "主机可见总内存值", "bytes"},
	} {
		value, exists := peaks[field.key]
		if !exists {
			continue
		}
		if !diagnosisResourceFactScalar(summary, "/resources/peaks/"+field.key, value) {
			return "", nil, invalidDiagnosisFact()
		}
		encoded, _ := json.Marshal(value)
		parts = append(parts, field.label+"="+string(encoded)+field.unit)
	}
	text := "本次资源快照包含" + string(countJSON) + "个实际样本"
	if len(parts) > 0 {
		text += "；" + strings.Join(parts, "，")
	}
	return text + "。这些是窗口内资源聚合，尚未证明资源瓶颈或业务根因。", limitations, nil
}

func diagnosisV2HypothesisText(kind string, facts []DiagnosisVerifiedFact) (string, error) {
	templates := map[string]string{"cpu_hotspot": "热点权重分布需要结合负载与调用路径核验，当前尚不能认定业务瓶颈。", "allocation_gc": "当前存在内存或分配相关线索；对象生命周期、分配速率及GC影响仍需独立观测。", "lock_io": "当前存在等待或IO相关线索；锁竞争、阻塞时长及因果仍待交叉验证。", "normal_load": "当前证据尚不能把热点认定为缺陷，应与正常负载基线核对。", "insufficient_evidence": "当前观测不足以确认性能缺陷或根因，应先补充可比证据。", "mixed_signals": "当前存在多类线索；应分别核验，不能将多个相关信号直接合并成已确认根因。", "business_latency": "当前业务时长观测可供核验；单次指标或时间重合不足以确认异常原因，需要基线、负载与窗口对照。", "downstream_dependency": "登记依赖提供下游调查方向，尚未证明下游导致当前症状；需要跨服务同窗口观测核验。", "deployment_regression": "当前已返回变更事件，可作为调查方向；时间重合不能确认变更导致回退，需要受控对照与功能验证。"}
	text, ok := templates[kind]
	if !ok {
		return "", diagnosisFeedbackError("hypothesis_kind_invalid", "kind", DiagnosisStep{})
	}
	hints := map[string]bool{}
	memoryKind := false
	positive := false
	latency, dependency, change := false, false, false
	for _, fact := range facts {
		if fact.Tool == "query_business_signals" {
			if fact.Unit == "milliseconds" {
				latency = true
				hints["business_latency"] = true
			}
			if strings.HasSuffix(fact.Path, "/change_type") {
				change = true
				hints["change_event"] = true
			}
		}
		if fact.Tool == "inspect_service_topology" && fact.Path == "/edges" && fact.Operator == "count" {
			decoder := json.NewDecoder(bytes.NewReader(fact.Value))
			decoder.UseNumber()
			var value any
			_ = decoder.Decode(&value)
			if n, ok := diagnosisNumeric(value); ok && n.Sign() > 0 {
				dependency = true
			}
		}
		if strings.HasSuffix(fact.Path, "/symbol_hint") {
			var hint string
			_ = json.Unmarshal(fact.Value, &hint)
			if hint == "allocation" || hint == "garbage_collection" {
				hints["allocation_gc"] = true
			}
			if hint == "lock_or_wait" || hint == "io_or_network" {
				hints["lock_io"] = true
			}
		}
		if fact.Tool == "read_profile_summary" && fact.Path == "/kind" {
			var taskKind string
			_ = json.Unmarshal(fact.Value, &taskKind)
			memoryKind = slices.Contains([]string{"GO_HEAP", "JAVA_HEAP", "PYTHON_MEMORY", "GPERF_HEAP", "JEPROF_HEAP", "MEMORY_TRACE"}, taskKind)
			if taskKind == "IO_TRACE" {
				hints["lock_io"] = true
			}
		}
		if fact.Tool == "inspect_hotspots" && (strings.HasSuffix(fact.Path, "/self_percent") || fact.Operator == "count_positive_self") {
			decoder := json.NewDecoder(bytes.NewReader(fact.Value))
			decoder.UseNumber()
			var value any
			_ = decoder.Decode(&value)
			if number, valid := diagnosisNumeric(value); valid && number.Sign() > 0 {
				positive = true
			}
		}
	}
	// 每条假设仅由它实际引用的事实支持。反馈解释固定前提，不替模型补事实、不降级错误为成功。
	missing := ""
	switch {
	case kind == "allocation_gc" && !memoryKind && !hints["allocation_gc"]:
		missing = "allocation"
	case kind == "lock_io" && !hints["lock_io"]:
		missing = "lock"
	case kind == "mixed_signals" && len(hints) < 2:
		missing = "mixed"
	case kind == "cpu_hotspot" && !positive:
		missing = "cpu"
	case kind == "business_latency" && !latency:
		missing = "latency"
	case kind == "downstream_dependency" && !dependency:
		missing = "dependency"
	case kind == "deployment_regression" && !change:
		missing = "change"
	}
	if missing != "" {
		return "", diagnosisFeedbackError("hypothesis_"+missing+"_evidence_missing", "fact_ids", DiagnosisStep{})
	}
	ids := []string{}
	for _, fact := range facts {
		ids = append(ids, fact.ID)
	}
	return text + "依据已核验事实：" + strings.Join(ids, "、") + "。", nil
}

// validateDiagnosisModelReportV2 是新模型运行唯一接受入口。v1 只保留读取与明确旧运行兼容，
// 不能在新运行缺少结构化主张时静默回退。模型不能直接提交权威摘要或伪造核验结果。
func validateDiagnosisModelReportV2(content string, trace []DiagnosisStep) (*DiagnosisReport, error) {
	if len(content) > 12000 {
		return nil, invalidDiagnosisFact()
	}
	var submitted diagnosisModelReportV2
	decoder := json.NewDecoder(strings.NewReader(content))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&submitted) != nil {
		return nil, invalidDiagnosisFact()
	}
	var tail any
	if decoder.Decode(&tail) != io.EOF || submitted.ReportVersion != 2 || len(submitted.Facts) < 1 || len(submitted.Facts) > 24 || len(submitted.Hypotheses) < 1 || len(submitted.Hypotheses) > 4 || len(submitted.VerificationRequests) < 1 || len(submitted.VerificationRequests) > 3 {
		return nil, invalidDiagnosisFact()
	}
	steps, err := diagnosisV2Trace(trace)
	if err != nil {
		return nil, err
	}
	unit := diagnosisMeasurementUnit(trace)
	report := &DiagnosisReport{ReportVersion: 2, Facts: []DiagnosisVerifiedFact{}, Hypotheses: []DiagnosisHypothesis{}}
	indexed := map[string]DiagnosisVerifiedFact{}
	for index, request := range submitted.Facts {
		if !diagnosisFactID.MatchString(request.ID) {
			return nil, diagnosisFeedbackAt(diagnosisFeedbackError("fact_id_invalid", "id", DiagnosisStep{}), "facts", index)
		}
		if _, exists := indexed[request.ID]; exists {
			return nil, diagnosisFeedbackAt(diagnosisFeedbackError("fact_id_duplicate", "id", DiagnosisStep{}), "facts", index)
		}
		step, exists := steps[request.EvidenceID]
		if !exists {
			return nil, diagnosisFeedbackAt(diagnosisFeedbackError("fact_source_not_found", "evidence_id", DiagnosisStep{}), "facts", index)
		}
		fact, err := diagnosisResolveFact(request, step, unit)
		if err != nil {
			return nil, diagnosisFeedbackAt(err, "facts", index)
		}
		indexed[fact.ID] = fact
		report.Facts = append(report.Facts, fact)
	}
	verification := []string{}
	for index, request := range submitted.VerificationRequests {
		text, err := diagnosisVerificationText(request, steps)
		if err != nil {
			return nil, diagnosisFeedbackAt(err, "verification_requests", index)
		}
		verification = append(verification, text)
	}
	for index, hypothesis := range submitted.Hypotheses {
		if len(hypothesis.FactIDs) < 1 || len(hypothesis.FactIDs) > 8 || len(hypothesis.EvidenceIDs) < 1 || len(hypothesis.EvidenceIDs) > 8 || !diagnosisSafeInterpretation(hypothesis.UnverifiedInterpretation) {
			return nil, diagnosisFeedbackAt(diagnosisFeedbackError("hypothesis_shape_invalid", "fact_ids", DiagnosisStep{}), "hypotheses", index)
		}
		facts := []DiagnosisVerifiedFact{}
		used := map[string]bool{}
		usedFacts := map[string]bool{}
		for _, id := range hypothesis.EvidenceIDs {
			if _, exists := steps[id]; !exists || used[id] {
				return nil, diagnosisFeedbackAt(diagnosisFeedbackError("hypothesis_evidence_invalid", "evidence_ids", DiagnosisStep{}), "hypotheses", index)
			}
			used[id] = true
		}
		for _, id := range hypothesis.FactIDs {
			fact, exists := indexed[id]
			if !exists || !used[fact.EvidenceID] || usedFacts[id] {
				return nil, diagnosisFeedbackAt(diagnosisFeedbackError("hypothesis_fact_reference_invalid", "fact_ids", DiagnosisStep{}), "hypotheses", index)
			}
			usedFacts[id] = true
			facts = append(facts, fact)
		}
		claim, err := diagnosisV2HypothesisText(hypothesis.Kind, facts)
		if err != nil {
			return nil, diagnosisFeedbackAt(err, "hypotheses", index)
		}
		report.Hypotheses = append(report.Hypotheses, DiagnosisHypothesis{Kind: hypothesis.Kind, Claim: claim, FactIDs: hypothesis.FactIDs, EvidenceIDs: hypothesis.EvidenceIDs, Verification: strings.Join(verification, "\n"), UnverifiedInterpretation: hypothesis.UnverifiedInterpretation})
	}
	report.Summary, report.Limitations, err = diagnosisV2Summary(trace)
	if err != nil {
		return nil, err
	}
	if err := validateDiagnosticSkillUse(trace); err != nil {
		return nil, err
	}
	return report, nil
}
