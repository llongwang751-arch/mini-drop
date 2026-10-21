// 资源快照只向诊断器提供分析器已聚合的有限计数，不把原始采样、进程身份或内核日志送给模型。
// 空函数栈是该采集器的正常语义；资源峰值也不等于时间加权负载、瓶颈证明或修复收益。
package service

import (
	"encoding/json"
	"math/big"
	"strings"

	"drop/api/internal/domain"
)

const diagnosisResourceSource = "analyzer_resource_aggregate_v1"
const diagnosisResourceMaxInteger int64 = 1<<53 - 1

var diagnosisResourceFields = [...]struct {
	name string
	unit string
}{
	{"cpu_percent", "percent"},
	{"rss_bytes", "bytes"},
	{"vms_bytes", "bytes"},
	{"threads", "count"},
	{"fd_count", "count"},
	{"memory_used_bytes", "bytes"},
	{"memory_total_bytes", "bytes"},
}

func invalidDiagnosisResource() error {
	return domain.Fail("INVALID_EVIDENCE", "资源聚合证据的结构、数值或采集来源无效")
}

// 使用有界有理数检查，避免 float64 把超范围或带小数的计数四舍五入成合法整数。
// CPU 百分比沿用现有解析器的多核口径，不能借此放宽函数热点占比的 0..100 限制。
func diagnosisResourceNumber(value any, maximum int64, integer bool) bool {
	number, ok := diagnosisNumeric(value)
	return ok && number.Sign() >= 0 && number.Cmp(big.NewRat(maximum, 1)) <= 0 && (!integer || number.IsInt())
}

func diagnosisResourcePeak(name string, value any) bool {
	for _, field := range diagnosisResourceFields {
		if field.name == name {
			if name == "cpu_percent" {
				return diagnosisResourceNumber(value, 100000, false)
			}
			return diagnosisResourceNumber(value, diagnosisResourceMaxInteger, true)
		}
	}
	return false
}

func diagnosisResourceUnits() map[string]any {
	units := map[string]any{"sample_count": "count"}
	for _, field := range diagnosisResourceFields {
		units[field.name] = field.unit
	}
	return units
}

// 只读取 sections.resources 的采样数和七个聚合峰值；JSON RawMessage 会跳过 samples、
// dmesg 等未授权字段的对象构造。输入仍设上限，未知字段也不能绕过解析成本边界。
func diagnosisResourceProjection(raw json.RawMessage, task domain.Task) (map[string]any, error) {
	if task.Kind != "RESOURCE_SNAPSHOT" {
		return nil, nil
	}
	if len(raw) == 0 || len(raw) > 8<<20 {
		return nil, invalidDiagnosisResource()
	}
	var result struct {
		Kind     string `json:"kind"`
		Sections struct {
			Resources json.RawMessage `json:"resources"`
		} `json:"sections"`
	}
	if json.Unmarshal(raw, &result) != nil || result.Kind != task.Kind {
		return nil, invalidDiagnosisResource()
	}
	if len(result.Sections.Resources) == 0 || strings.TrimSpace(string(result.Sections.Resources)) == "null" {
		return nil, nil
	}
	var aggregate struct {
		SampleCount json.RawMessage            `json:"sample_count"`
		Peaks       map[string]json.RawMessage `json:"peaks"`
	}
	if json.Unmarshal(result.Sections.Resources, &aggregate) != nil || len(aggregate.Peaks) > 32 {
		return nil, invalidDiagnosisResource()
	}
	if len(aggregate.SampleCount) == 0 || strings.TrimSpace(string(aggregate.SampleCount)) == "null" {
		return nil, nil
	}
	decodeNumber := func(raw json.RawMessage) (any, bool) {
		if len(raw) == 0 || len(raw) > 80 {
			return nil, false
		}
		var value any
		decoder := json.NewDecoder(strings.NewReader(string(raw)))
		decoder.UseNumber()
		if decoder.Decode(&value) != nil {
			return nil, false
		}
		_, ok := value.(json.Number)
		return value, ok
	}
	count, ok := decodeNumber(aggregate.SampleCount)
	if !ok || !diagnosisResourceNumber(count, 3600, true) {
		return nil, invalidDiagnosisResource()
	}
	countNumber, _ := diagnosisNumeric(count)
	if countNumber.Sign() == 0 {
		// 没有实际观测时不输出合成的零峰值，也不把未知数据当作正常资源基线。
		return nil, nil
	}
	if aggregate.Peaks == nil {
		return nil, invalidDiagnosisResource()
	}
	peaks := map[string]any{}
	for _, field := range diagnosisResourceFields {
		rawValue, exists := aggregate.Peaks[field.name]
		if !exists || strings.TrimSpace(string(rawValue)) == "null" {
			continue
		}
		value, valid := decodeNumber(rawValue)
		if !valid || !diagnosisResourcePeak(field.name, value) {
			return nil, invalidDiagnosisResource()
		}
		peaks[field.name] = value
	}
	var parameters domain.Parameters
	if len(task.Parameters) > 64<<10 || len(task.Parameters) > 0 && json.Unmarshal(task.Parameters, &parameters) != nil {
		return nil, invalidDiagnosisResource()
	}
	scope := parameters.Scope
	switch scope {
	case "process", "container", "host":
	default:
		scope = "unknown"
	}
	return map[string]any{
		"schema_version": json.Number("1"),
		"source":         diagnosisResourceSource,
		"sample_count":   count,
		"scope":          scope,
		"peaks":          peaks,
		"units":          diagnosisResourceUnits(),
		"limitations": []string{
			"这里只返回实际采样点中的最大值，可能遗漏采样间隔内的峰值，不是时间加权负载或瓶颈证明。",
			"process或container目标的PID计数不代表整台主机或整个容器总量；memory_*为主机可见内存口径，不等同目标进程RSS。",
			"空函数栈和零profile权重不代表资源采集失败；缺失的资源字段保持未知，不能补零或据此确认根因。",
		},
	}, nil
}

// 供 v2 事实解析调用：验证的是程序投影的固定来源与标量，而不是模型自报的单位。
// 数值与具体工具观察的相等校验仍由事实解析器完成；这里不产生新的事实或根因判断。
func diagnosisResourceFactScalar(body map[string]any, path string, value any) bool {
	if body["kind"] != "RESOURCE_SNAPSHOT" {
		return false
	}
	resources, ok := body["resources"].(map[string]any)
	if !ok || resources["source"] != diagnosisResourceSource || !diagnosisResourceNumber(resources["schema_version"], 1, true) {
		return false
	}
	version, _ := diagnosisNumeric(resources["schema_version"])
	if version.Sign() != 1 || !diagnosisResourceNumber(resources["sample_count"], 3600, true) {
		return false
	}
	count, _ := diagnosisNumeric(resources["sample_count"])
	if count.Sign() <= 0 {
		return false
	}
	switch resources["scope"] {
	case "process", "container", "host", "unknown":
	default:
		return false
	}
	units, ok := resources["units"].(map[string]any)
	if !ok || len(units) != len(diagnosisResourceFields)+1 || units["sample_count"] != "count" {
		return false
	}
	for _, field := range diagnosisResourceFields {
		if units[field.name] != field.unit {
			return false
		}
	}
	if path == "/resources/sample_count" {
		if !diagnosisResourceNumber(value, 3600, true) {
			return false
		}
		number, _ := diagnosisNumeric(value)
		return number.Sign() > 0
	}
	const prefix = "/resources/peaks/"
	if !strings.HasPrefix(path, prefix) {
		return false
	}
	field := strings.TrimPrefix(path, prefix)
	peaks, ok := resources["peaks"].(map[string]any)
	if !ok || len(peaks) > len(diagnosisResourceFields) || peaks[field] == nil {
		return false
	}
	return diagnosisResourcePeak(field, value) && diagnosisResourcePeak(field, peaks[field])
}
