// Package domain：从权威 task-kinds.json 装载采集能力目录，并按任务类型校验参数。
// HTTP、Go 编排与 Core 必须共享目录语义；新增工具参数不能靠宽松 map 绕过目录约束。
package domain

import (
	"encoding/json"
	"fmt"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

type AuxiliaryArtifact struct {
	Name         string `json:"name"`
	Kind         string `json:"kind"`
	Format       string `json:"format"`
	Required     bool   `json:"required"`
	MaxFileBytes int64  `json:"max_file_bytes"`
}
type TaskKind struct {
	ID               string              `json:"id"`
	RawName          string              `json:"raw_name"`
	RawContentType   string              `json:"raw_content_type"`
	InputFormat      string              `json:"input_format"`
	InputFormats     []string            `json:"input_formats"`
	Pipeline         string              `json:"analysis_pipeline"`
	DefaultDuration  int                 `json:"default_duration_seconds"`
	DefaultFrequency int                 `json:"default_frequency_hz"`
	MaxFileBytes     int64               `json:"max_file_bytes"`
	AgentRunner      bool                `json:"agent_runner"`
	RequiresPID      bool                `json:"requires_pid"`
	Targets          []string            `json:"supported_targets"`
	Schema           map[string]any      `json:"parameter_schema"`
	Auxiliary        []AuxiliaryArtifact `json:"auxiliary_artifacts"`
	Compiled         *jsonschema.Schema  `json:"-"`
}

var kindsMu sync.RWMutex
var taskKinds = map[string]TaskKind{
	"PERF_CPU":         {ID: "PERF_CPU", RawName: "profile.folded", RawContentType: "text/plain", InputFormat: "folded", Pipeline: "folded_v1", DefaultDuration: 10, DefaultFrequency: 99, MaxFileBytes: 32 << 20, AgentRunner: true, RequiresPID: true},
	"PROC_CPU":         {ID: "PROC_CPU", RawName: "profile.folded", RawContentType: "text/plain", InputFormat: "folded", Pipeline: "folded_v1", DefaultDuration: 10, DefaultFrequency: 10, MaxFileBytes: 32 << 20, AgentRunner: true, RequiresPID: true},
	"IMPORT_COLLAPSED": {ID: "IMPORT_COLLAPSED", RawName: "profile.folded", RawContentType: "text/plain", InputFormat: "folded", Pipeline: "folded_v1", MaxFileBytes: 32 << 20},
}

// LoadKinds 载入权威能力目录并替换进程内只读索引。
// 重复 ID、非法字段或不支持的参数组合在启动时失败，避免同一任务在浏览器、API 和 Core 中含义不同。
func LoadKinds(b []byte) error {
	var defs []TaskKind
	if err := json.Unmarshal(b, &defs); err != nil {
		return err
	}
	next := map[string]TaskKind{}
	for _, k := range defs {
		if k.ID != "COMPOSITE" && k.ID != "IMPORT_COLLAPSED" {
			k.AgentRunner = true
		}
		if k.ID == "" || next[k.ID].ID != "" {
			return fmt.Errorf("invalid TaskKind catalog")
		}
		if k.Schema != nil {
			c := jsonschema.NewCompiler()
			uri := "https://drop.invalid/kinds/" + k.ID
			if err := c.AddResource(uri, k.Schema); err != nil {
				return err
			}
			schema, err := c.Compile(uri)
			if err != nil {
				return err
			}
			k.Compiled = schema
		}
		next[k.ID] = k
	}
	kindsMu.Lock()
	taskKinds = next
	kindsMu.Unlock()
	return nil
}
func Kind(id string) (TaskKind, bool) {
	kindsMu.RLock()
	defer kindsMu.RUnlock()
	k, ok := taskKinds[id]
	return k, ok
}
func PipelineFor(id string) string {
	k, _ := Kind(id)
	if k.Pipeline == "" {
		return "folded_v1"
	}
	return k.Pipeline
}

// ValidateKindParameters 按当前 TaskKind 的类型化规则检查参数，通用字段通过不代表具体采集器参数已经合法。
func ValidateKindParameters(kind string, p Parameters) *Error {
	k, ok := Kind(kind)
	if !ok || kind == "COMPOSITE" {
		return Fail("UNSUPPORTED_KIND", "任务类型不受支持")
	}
	p.Source = ""
	b, _ := json.Marshal(p)
	var value any
	_ = json.Unmarshal(b, &value)
	if k.Compiled != nil {
		if err := k.Compiled.Validate(value); err != nil {
			return Fail("INVALID_PARAMETERS", "参数不符合TaskKind schema")
		}
	}
	return nil
}
