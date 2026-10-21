// 采样格式适配产生的质量计数必须与当前真实分析来源一致。
// 这里只投影固定计数；不转发原始栈、采集元数据全文或由输入携带的自由说明。
package service

import (
	"bytes"
	"encoding/json"
	"strings"

	"drop/api/internal/domain"
)

type diagnosisPyspyQuality struct {
	SchemaVersion          int    `json:"schema_version"`
	Source                 string `json:"source"`
	Normalized             bool   `json:"normalized"`
	UnresolvedStackLines   int64  `json:"unresolved_stack_lines"`
	PlaceholderCount       int64  `json:"placeholder_count"`
	UnresolvedSampleWeight int64  `json:"unresolved_sample_weight"`
	TotalSampleWeight      int64  `json:"total_sample_weight"`
}

func decodeDiagnosisPyspyQuality(raw json.RawMessage) (diagnosisPyspyQuality, bool) {
	var quality diagnosisPyspyQuality
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil || len(fields) > 16 {
		return quality, false
	}
	for _, key := range []string{"schema_version", "source", "normalized", "unresolved_stack_lines", "placeholder_count", "unresolved_sample_weight", "total_sample_weight"} {
		value, exists := fields[key]
		if !exists || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return quality, false
		}
	}
	return quality, json.Unmarshal(raw, &quality) == nil
}

// 未知占位符的正权重只证明采样尚未解析，不能支撑业务函数热点方向。
// 历史工具未包含unresolved时仍按原协议处理；新标记只能由来源已核验的工具产生。
func diagnosisResolvedHotspotEvidence(facts []DiagnosisVerifiedFact, steps map[string]DiagnosisStep) bool {
	for _, fact := range facts {
		if fact.Tool != "inspect_hotspots" {
			continue
		}
		step, ok := steps[fact.EvidenceID]
		if !ok || step.Tool != "inspect_hotspots" {
			continue
		}
		body, err := diagnosisDecodeObject(step.Observation)
		if err != nil {
			continue
		}
		rows, ok := body["top_functions"].([]any)
		if !ok || len(rows) > 10 {
			continue
		}
		for index, value := range rows {
			row, ok := value.(map[string]any)
			if !ok {
				continue
			}
			if unresolved, exists := row["unresolved"]; exists && unresolved != false {
				continue
			}
			weight, positive := diagnosisNumeric(row["self_percent"])
			if !positive || weight.Sign() <= 0 {
				continue
			}
			if fact.Operator == "count_positive_self" && fact.Path == "/top_functions" {
				return true
			}
			if index < 10 && strings.HasSuffix(fact.Path, "/self_percent") && len(fact.Path) == len("/top_functions/0/self_percent") && fact.Path[len("/top_functions/")] == byte('0'+index) {
				return true
			}
		}
	}
	return false
}

func diagnosisSamplingQuality(raw json.RawMessage, task domain.Task, total int64) (map[string]any, error) {
	if task.Kind != "PYTHON_CPU" {
		return nil, nil
	}
	bad := func() (map[string]any, error) {
		return nil, domain.Fail("INVALID_EVIDENCE", "py-spy采样质量的计数、来源或总权重不一致")
	}
	if len(raw) == 0 || len(raw) > 8<<20 {
		return bad()
	}
	var result struct {
		Kind    string `json:"kind"`
		Summary struct {
			Source string `json:"source"`
			Unit   string `json:"unit"`
		} `json:"summary"`
		Sections struct {
			Quality json.RawMessage `json:"sampling_quality"`
		} `json:"sections"`
		Metadata struct {
			SampleCount int64           `json:"sample_count"`
			Unit        string          `json:"unit"`
			Source      string          `json:"source"`
			Provenance  string          `json:"provenance"`
			Quality     json.RawMessage `json:"pyspy_raw_quality"`
			InputFormat string          `json:"input_format"`
			Pipeline    string          `json:"pipeline"`
			Collector   struct {
				SchemaVersion int    `json:"schema_version"`
				Kind          string `json:"kind"`
				Runner        string `json:"runner"`
				Source        string `json:"source"`
				Provenance    string `json:"provenance"`
			} `json:"collector"`
		} `json:"metadata"`
	}
	if json.Unmarshal(raw, &result) != nil {
		return bad()
	}
	if len(result.Sections.Quality) == 0 && len(result.Metadata.Quality) == 0 {
		return nil, nil // 旧分析没有适配元信息，保持未知，不补出“零丢样”。
	}
	q, valid := decodeDiagnosisPyspyQuality(result.Sections.Quality)
	metadataQuality, metadataValid := decodeDiagnosisPyspyQuality(result.Metadata.Quality)
	var parameters domain.Parameters
	if json.Unmarshal(task.Parameters, &parameters) != nil || parameters.Source == "import" || result.Kind != task.Kind || result.Summary.Source != "py-spy" || result.Summary.Unit != "samples" || result.Metadata.Unit != "samples" || result.Metadata.SampleCount != total || result.Metadata.Source != "py-spy" || result.Metadata.Provenance != "collector_artifact" || result.Metadata.InputFormat != "folded" || result.Metadata.Pipeline != "python_cpu_v1" || result.Metadata.Collector.SchemaVersion != 1 || result.Metadata.Collector.Kind != task.Kind || result.Metadata.Collector.Runner != "py-spy" || result.Metadata.Collector.Source == "import" || result.Metadata.Collector.Provenance == "import" || !valid || !metadataValid || q != metadataQuality {
		return bad()
	}
	if q.SchemaVersion != 1 || q.Source != "collector_pyspy_raw_v1" || total < 1 || total > 1<<53-1 || q.TotalSampleWeight != total || q.UnresolvedSampleWeight < 0 || q.UnresolvedSampleWeight > total || q.UnresolvedStackLines < 0 || q.UnresolvedStackLines > 500000 || q.UnresolvedStackLines > q.UnresolvedSampleWeight || q.PlaceholderCount < q.UnresolvedStackLines || q.PlaceholderCount > q.UnresolvedStackLines*128 {
		return bad()
	}
	if q.Normalized != (q.UnresolvedStackLines > 0) || (q.UnresolvedStackLines == 0 && (q.PlaceholderCount != 0 || q.UnresolvedSampleWeight != 0)) {
		return bad()
	}
	return map[string]any{
		"schema_version": 1, "source": q.Source, "normalized": q.Normalized,
		"unresolved_stack_lines": q.UnresolvedStackLines, "placeholder_count": q.PlaceholderCount,
		"unresolved_sample_weight": q.UnresolvedSampleWeight, "total_sample_weight": q.TotalSampleWeight,
	}, nil
}
