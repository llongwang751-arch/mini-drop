// 本模块将可版本化的诊断方法与跨会话案例分开：Skill 是人工维护的方法，案例只是有来源和时效的先验。
// 匹配只依据当前已授权任务的采集类型、真实结果与质量条件；用户问题和历史文本不能改变工具或目标权限。
// 模型只获得固定中文方法、枚举结论与哈希匹配原因，不读取历史问题、原符号、源码或人工自由备注。
package service

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"slices"
	"sort"
	"strings"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

//go:embed diagnostic_skills.json
var diagnosticSkillCatalog []byte

// DiagnosticSkill 是可信、版本化的方法定义；RequiredTools 仅描述已有诊断能力，不授予额外工具权限。
// MatchReasons 来自当前证据的固定枚举，不包含用户问题或符号名称。
type DiagnosticSkill struct {
	ID              string   `json:"id"`
	Version         string   `json:"version"`
	Title           string   `json:"title"`
	TaskKinds       []string `json:"task_kinds"`
	RequiredTools   []string `json:"required_tools"`
	Steps           []string `json:"steps"`
	CounterEvidence []string `json:"counter_evidence"`
	StopConditions  []string `json:"stop_conditions"`
	Verification    []string `json:"verification"`
	MatchReasons    []string `json:"match_reasons,omitempty"`
}

// DiagnosticSkillRef 记录当时使用的方法版本，避免目录升级后把旧案例解释为遵循新方法。
type DiagnosticSkillRef struct {
	ID      string `json:"id"`
	Version string `json:"version"`
}

// DiagnosticCasePrior 是唯一允许进入模型的历史视图；来源 ID 仅供审计，结论不是当前因果证据。
type DiagnosticCasePrior struct {
	ID                int64                `json:"id,string"`
	SourceDiagnosisID int64                `json:"source_diagnosis_id,string"`
	Outcome           string               `json:"outcome"`
	Assurance         string               `json:"assurance"`
	Conclusion        string               `json:"conclusion"`
	SkillRefs         []DiagnosticSkillRef `json:"skill_refs"`
	MatchReasons      []string             `json:"match_reasons"`
	PriorOnly         bool                 `json:"prior_only"`
}

// DiagnosticKnowledge 将本次匹配方法与个人历史先验明确分栏；HistoryEnabled=false 时不读取案例表。
type DiagnosticKnowledge struct {
	Skills         []DiagnosticSkill     `json:"skills"`
	Cases          []DiagnosticCasePrior `json:"cases"`
	PriorOnly      bool                  `json:"prior_only"`
	HistoryEnabled bool                  `json:"history_enabled"`
}

// BuiltinDiagnosticSkills 每次解析可信内置目录并校验必需字段与工具白名单。
// 返回独立切片，调用方不能修改全局目录；目录错误应阻止提供知识，而不是静默退化。
func BuiltinDiagnosticSkills() ([]DiagnosticSkill, error) {
	var skills []DiagnosticSkill
	if err := json.Unmarshal(diagnosticSkillCatalog, &skills); err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	for _, skill := range skills {
		if skill.ID == "" || seen[skill.ID] || skill.Version == "" || skill.Title == "" || len(skill.TaskKinds) == 0 || len(skill.RequiredTools) == 0 || len(skill.Steps) == 0 || len(skill.CounterEvidence) == 0 || len(skill.StopConditions) == 0 || len(skill.Verification) == 0 {
			return nil, fmt.Errorf("诊断方法目录字段无效")
		}
		seen[skill.ID] = true
		for _, tool := range skill.RequiredTools {
			if !slices.Contains(diagnosisToolNames(), tool) && tool != "compare_profiles" {
				return nil, fmt.Errorf("诊断方法引用未知工具")
			}
		}
	}
	return skills, nil
}

// diagnosticFeatures 只在服务端保存脱敏指纹与固定条件，不保存函数名称、目标标识或用户叙述。
// 同一符号指纹只表示词形相同；不能据此认为负载、版本或因果关系相同。
type diagnosticFeatures struct {
	Symbols []string `json:"symbols"`
	Hints   []string `json:"hints"`
	Quality []string `json:"quality"`
	Unit    string   `json:"unit"`
}

func diagnosticFeatureSet(task domain.Task, raw json.RawMessage) (diagnosticFeatures, error) {
	f := diagnosticFeatures{Symbols: []string{}, Hints: []string{}, Quality: []string{}}
	var p struct {
		Kind             string `json:"kind"`
		TotalSamples     int64  `json:"total_samples"`
		ObservationCount *int64 `json:"observation_count"`
		Summary          struct {
			Unit string `json:"unit"`
		} `json:"summary"`
		Top []struct {
			Name  string  `json:"name"`
			Self  float64 `json:"self_percent"`
			Total float64 `json:"total_percent"`
		} `json:"top_functions"`
	}
	if len(raw) == 0 || len(raw) > 8<<20 || json.Unmarshal(raw, &p) != nil || (p.Kind != "" && p.Kind != task.Kind) || p.TotalSamples < 0 {
		return f, domain.Fail("INVALID_EVIDENCE", "诊断知识的当前结果无效")
	}
	catalog, err := BuiltinDiagnosticSkills()
	if err != nil {
		return f, err
	}
	known := false
	for _, skill := range catalog {
		known = known || slices.Contains(skill.TaskKinds, task.Kind)
	}
	if !known {
		return f, domain.Fail("INVALID_EVIDENCE", "诊断知识不支持此采集类型")
	}
	var params domain.Parameters
	if json.Unmarshal(task.Parameters, &params) != nil {
		return f, domain.Fail("INVALID_EVIDENCE", "采集条件不可读")
	}
	if p.TotalSamples == 0 {
		f.Quality = append(f.Quality, "no_weight")
	}
	observations := p.TotalSamples
	if p.ObservationCount != nil {
		observations = *p.ObservationCount
		if observations < 0 {
			return f, domain.Fail("INVALID_EVIDENCE", "观测数无效")
		}
	}
	if observations < 50 {
		f.Quality = append(f.Quality, "low_samples")
	}
	if params.DurationSeconds > 0 && params.DurationSeconds < 10 {
		f.Quality = append(f.Quality, "short_window")
	}
	if params.Source == "import" || task.Kind == "IMPORT_COLLAPSED" {
		f.Quality = append(f.Quality, "imported")
	}
	if task.Kind == "PROC_CPU" {
		f.Quality = append(f.Quality, "counters_only")
	}
	f.Unit = p.Summary.Unit
	if !slices.Contains([]string{"samples", "cpu_ticks", "bytes", "objects", "allocations", "operations", "nanoseconds", "milliseconds", "count"}, f.Unit) {
		f.Unit = "unknown"
		f.Quality = append(f.Quality, "unknown_unit")
	}
	for i, row := range p.Top {
		if math.IsNaN(row.Self) || math.IsInf(row.Self, 0) || row.Self < 0 || row.Self > 100 || math.IsNaN(row.Total) || math.IsInf(row.Total, 0) || row.Total < 0 || row.Total > 100 {
			return f, domain.Fail("INVALID_EVIDENCE", "热点比例超出范围")
		}
		if i >= 10 || row.Name == "" || row.Self <= 0 {
			continue
		}
		f.Symbols = append(f.Symbols, repository.Digest(row.Name))
		hint := diagnosisSymbolHint(row.Name)
		if hint != "unknown" {
			f.Hints = append(f.Hints, hint)
		}
	}
	for _, items := range []*[]string{&f.Symbols, &f.Hints, &f.Quality} {
		sort.Strings(*items)
		*items = slices.Compact(*items)
	}
	return f, nil
}

// MatchDiagnosticSkills 是确定性的匹配入口，评测和实际诊断共用；没有模型选择或用户文本驱动路由。
// 命中方法只建议检查顺序，RequiredTools 仍受本次运行原有白名单与总预算约束。
func MatchDiagnosticSkills(task domain.Task, raw json.RawMessage) ([]DiagnosticSkill, error) {
	f, err := diagnosticFeatureSet(task, raw)
	if err != nil {
		return nil, err
	}
	catalog, err := BuiltinDiagnosticSkills()
	if err != nil {
		return nil, err
	}
	selected := []DiagnosticSkill{}
	for _, skill := range catalog {
		if !slices.Contains(skill.TaskKinds, task.Kind) {
			continue
		}
		if slices.Contains(f.Quality, "no_weight") && skill.ID != "sampling_quality" {
			continue
		}
		reasons := []string{}
		switch skill.ID {
		case "sampling_quality":
			reasons = append(reasons, "quality_review_required")
			reasons = append(reasons, f.Quality...)
		case "cpu_hotspot":
			if len(f.Symbols) > 0 && !slices.Contains(f.Quality, "no_weight") {
				reasons = append(reasons, "cpu_kind_with_hotspots")
			}
		case "allocation_gc":
			if slices.Contains([]string{"GO_HEAP", "JAVA_HEAP", "PYTHON_MEMORY", "GPERF_HEAP", "JEPROF_HEAP", "MEMORY_TRACE"}, task.Kind) {
				reasons = append(reasons, "memory_collector")
			}
			for _, hint := range []string{"allocation", "garbage_collection"} {
				if slices.Contains(f.Hints, hint) {
					reasons = append(reasons, hint)
				}
			}
		case "lock_io":
			if task.Kind == "IO_TRACE" {
				reasons = append(reasons, "io_collector")
			}
			for _, hint := range []string{"lock_or_wait", "io_or_network"} {
				if slices.Contains(f.Hints, hint) {
					reasons = append(reasons, hint)
				}
			}
		}
		if len(reasons) > 0 {
			skill.MatchReasons = reasons
			selected = append(selected, skill)
		}
	}
	// 同时出现分配与等待线索时，具体方法优先于通用 CPU 方法，始终保留质量核验。
	if len(selected) > 3 {
		selected = slices.DeleteFunc(selected, func(skill DiagnosticSkill) bool { return skill.ID == "cpu_hotspot" })
	}
	return selected, nil
}

// emptyDiagnosticKnowledgeArgs 接受唯一空对象，拒绝 null、未知键、目标覆盖和尾随 JSON。
func emptyDiagnosticKnowledgeArgs(args json.RawMessage) error {
	if len(args) == 0 || len(args) > 1024 || strings.TrimSpace(string(args)) == "null" {
		return domain.Fail("AI_TOOL_REJECTED", "知识工具只接受空对象")
	}
	var empty struct{}
	dec := json.NewDecoder(bytes.NewReader(args))
	dec.DisallowUnknownFields()
	if dec.Decode(&empty) != nil {
		return domain.Fail("AI_TOOL_REJECTED", "知识工具参数无效")
	}
	var tail any
	if dec.Decode(&tail) != io.EOF {
		return domain.Fail("AI_TOOL_REJECTED", "知识工具参数含尾部数据")
	}
	return nil
}
