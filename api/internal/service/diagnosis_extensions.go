// 扩展工具只读受授权的业务信号，或提交等待确认的补证草案。
// 这些 schema 不包含 URL、任意查询、目标进程身份和命令，不能借自然语言扩权。
package service

func diagnosisExtensionDefinitions() []map[string]any {
	tool := func(name, description string, properties map[string]any, required []string) map[string]any {
		parameters := map[string]any{"type": "object", "properties": properties, "additionalProperties": false}
		if len(required) > 0 {
			parameters["required"] = required
		}
		return map[string]any{"type": "function", "function": map[string]any{"name": name, "description": description, "parameters": parameters}}
	}
	return []map[string]any{
		tool("query_business_signals", "查询当前绑定服务及获授权的一跳关联服务的固定业务信号。只接受S1等服务别名和信号类型，时间窗口由本次采集确定；无数据不代表正常。", map[string]any{
			"service_ref": map[string]any{"type": "string", "pattern": "^S[1-8]$"},
			"signal":      map[string]any{"type": "string", "enum": []string{"metrics", "logs", "traces", "changes"}},
		}, []string{"service_ref", "signal"}),
		tool("inspect_service_topology", "读取当前服务与获授权的一跳上下游登记关系，仅用于选择取证方向。拓扑不证明故障传播或因果。", map[string]any{}, nil),
		tool("propose_followup_collection", "根据当前观测提出同一已授权目标的追加采集计划，支持改选实际采集器；需要用户另行确认。evidence_ids只能引用inspect_hotspots、inspect_call_paths、check_collection_quality或query_business_signals返回的本轮E编号，不引用拓扑、知识或其他计划。最多两个补证分支，不能自动执行或改变目标。", map[string]any{
			"kind":             map[string]any{"type": "string", "maxLength": 40},
			"duration_seconds": map[string]any{"type": "integer", "minimum": 1, "maximum": 60},
			"frequency_hz":     map[string]any{"type": "integer", "minimum": 0, "maximum": 999},
			"options":          map[string]any{"type": "object"},
			"rationale":        map[string]any{"type": "string", "maxLength": 600},
			"evidence_ids":     map[string]any{"type": "array", "minItems": 1, "maxItems": 4, "uniqueItems": true, "items": map[string]any{"type": "string", "pattern": "^E[1-9][0-9]?$"}},
		}, []string{"kind", "duration_seconds", "frequency_hz", "rationale", "evidence_ids"}),
	}
}
