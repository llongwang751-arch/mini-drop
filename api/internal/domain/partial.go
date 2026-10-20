// Package domain：定义不完整分析结果的独立协议，保留来源身份、已完成范围和受限原因。
// 部分结果可以帮助排障，但不能修改完整结果的成功状态，也不能接纳任意解析器内部元数据。
package domain

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"regexp"
	"strconv"
	"strings"
	"unicode"
	"unicode/utf8"
)

const MaxPartialResultBytes = 16 << 10
const partialSafeNumber = 9007199254740991

type AnalysisIdentity struct {
	AnalysisJobID   string `json:"analysis_job_id"`
	AnalysisRun     int64  `json:"analysis_run"`
	InputArtifactID string `json:"input_artifact_id"`
	InputDigest     string `json:"input_digest"`
}

type PartialSummary struct {
	Source       string  `json:"source"`
	Unit         string  `json:"unit"`
	TotalSamples float64 `json:"total_samples"`
	UniqueStacks int64   `json:"unique_stacks"`
	NodeCount    int64   `json:"node_count"`
	MaxDepth     int64   `json:"max_depth"`
}
type PartialFunction struct {
	Name         string  `json:"name"`
	SymbolSHA256 string  `json:"symbol_sha256"`
	Self         float64 `json:"self"`
	Total        float64 `json:"total"`
	SelfPercent  float64 `json:"self_percent"`
	TotalPercent float64 `json:"total_percent"`
}
type PartialResult struct {
	SchemaVersion   int               `json:"schema_version"`
	Completeness    string            `json:"completeness"`
	TaskID          string            `json:"task_id"`
	AttemptID       string            `json:"attempt_id"`
	AnalysisJobID   string            `json:"analysis_job_id"`
	AnalysisRun     int64             `json:"analysis_run"`
	InputArtifactID string            `json:"input_artifact_id"`
	InputDigest     string            `json:"input_digest"`
	Kind            string            `json:"kind"`
	CompletedStages []string          `json:"completed_stages"`
	MissingStages   []string          `json:"missing_stages"`
	Summary         PartialSummary    `json:"summary"`
	TopFunctions    []PartialFunction `json:"top_functions"`
}

var partialUnit = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,32}$`)
var partialSources = map[string]bool{"import": true, "perf": true, "procfs": true, "async-profiler": true, "py-spy": true, "go-pprof": true, "hotspot-hprof": true, "memray": true, "linux-trace": true, "gperftools": true, "jemalloc": true, "llvm-bolt": true, "fixed-template": true, "jfr": true, "unknown": true}

func partialID(value string) bool {
	n, err := strconv.ParseInt(value, 10, 64)
	return err == nil && n > 0 && strconv.FormatInt(n, 10) == value
}
func partialDigest(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 32 && strings.ToLower(value) == value
}
func partialNumber(value float64, maximum float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0) && value >= 0 && value <= maximum
}
func partialFields(payload []byte, fields ...string) bool {
	var values map[string]json.RawMessage
	if json.Unmarshal(payload, &values) != nil || len(values) != len(fields) {
		return false
	}
	for _, key := range fields {
		v, ok := values[key]
		if !ok || bytes.Equal(bytes.TrimSpace(v), []byte("null")) {
			return false
		}
	}
	return true
}

// DecodePartialResult 校验独立且有界的不完整结果协议。
// 它拒绝完整结果和任意解析器或工具元数据；调用方必须通过当前分析身份确认这份结果没有过期。
func DecodePartialResult(payload []byte) (PartialResult, error) {
	var result PartialResult
	invalid := errors.New("invalid partial analysis result")
	if len(payload) == 0 || len(payload) > MaxPartialResultBytes || !utf8.Valid(payload) || !partialFields(payload, "schema_version", "completeness", "task_id", "attempt_id", "analysis_job_id", "analysis_run", "input_artifact_id", "input_digest", "kind", "completed_stages", "missing_stages", "summary", "top_functions") {
		return result, invalid
	}
	decoder := json.NewDecoder(bytes.NewReader(payload))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&result) != nil || decoder.Decode(new(any)) != io.EOF {
		return PartialResult{}, invalid
	}
	_, knownKind := Kind(result.Kind)
	if result.SchemaVersion != 1 || result.Completeness != "partial" || !knownKind || result.Kind == "COMPOSITE" || !partialID(result.TaskID) || !partialID(result.AttemptID) || !partialID(result.AnalysisJobID) || !partialID(result.InputArtifactID) || result.AnalysisRun < 1 || result.AnalysisRun > 2147483647 || !partialDigest(result.InputDigest) || len(result.CompletedStages) != 2 || strings.Join(result.CompletedStages, ",") != "input_verified,parsed" || len(result.MissingStages) != 3 || strings.Join(result.MissingStages, ",") != "diagnostic_rules,result_artifacts,final_commit" {
		return PartialResult{}, invalid
	}
	var raw struct {
		Summary   json.RawMessage   `json:"summary"`
		Functions []json.RawMessage `json:"top_functions"`
	}
	if json.Unmarshal(payload, &raw) != nil || !partialFields(raw.Summary, "source", "unit", "total_samples", "unique_stacks", "node_count", "max_depth") {
		return PartialResult{}, invalid
	}
	s := result.Summary
	if !partialSources[s.Source] || !partialUnit.MatchString(s.Unit) || !partialNumber(s.TotalSamples, partialSafeNumber) || s.UniqueStacks < 0 || s.UniqueStacks > 100000 || s.NodeCount < 0 || s.NodeCount > 100000 || s.MaxDepth < 0 || s.MaxDepth > 128 || result.TopFunctions == nil || len(result.TopFunctions) > 20 {
		return PartialResult{}, invalid
	}
	for i, f := range result.TopFunctions {
		if !partialFields(raw.Functions[i], "name", "symbol_sha256", "self", "total", "self_percent", "total_percent") || len(f.Name) == 0 || len(f.Name) > 160 || strings.IndexFunc(f.Name, func(r rune) bool { return unicode.Is(unicode.Categories["C"], r) }) >= 0 || !partialDigest(f.SymbolSHA256) || !partialNumber(f.Self, partialSafeNumber) || !partialNumber(f.Total, partialSafeNumber) || !partialNumber(f.SelfPercent, 100) || !partialNumber(f.TotalPercent, 100) {
			return PartialResult{}, invalid
		}
	}
	return result, nil
}
