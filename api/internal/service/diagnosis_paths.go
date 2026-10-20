// Package service：在有界调用树内提取某个热点的祖先、子调用和出现位置。
// 遍历截断与局部上下文省略分别标记，避免把“返回范围有限”误写成“原始采集缺失”。
package service

import (
	"encoding/json"
	"sort"

	"drop/api/internal/repository"
)

type diagnosisTree struct {
	Name     string           `json:"name"`
	Value    float64          `json:"value"`
	Self     float64          `json:"self"`
	Children []*diagnosisTree `json:"children"`
}

// diagnosisCallPaths 在当前任务内有界展开热点前后的调用上下文。
// 不查询文件系统，也不让原始栈帧离开平台；逐次返回函数出现位置，
// 不累加递归调用的包含权重，更不把采样权重推断为请求耗时。
func diagnosisCallPaths(raw json.RawMessage, name string) (json.RawMessage, error) {
	unavailable := func(reason string) (json.RawMessage, error) {
		return json.Marshal(map[string]any{"available": false, "reason": reason})
	}
	if len(raw) > 8<<20 {
		return unavailable("call_tree_exceeds_8MiB_interactive_budget")
	}
	var result struct {
		Flamegraph *diagnosisTree `json:"flamegraph"`
	}
	if json.Unmarshal(raw, &result) != nil || result.Flamegraph == nil {
		return unavailable("call_tree_not_present")
	}
	symbol := func(n *diagnosisTree) map[string]any {
		return map[string]any{"symbol": "symbol_" + repository.Digest(n.Name)[:12], "symbol_hint": diagnosisSymbolHint(n.Name)}
	}
	type item struct {
		node      *diagnosisTree
		ancestors []map[string]any
		depth     int
	}
	queue := []item{{node: result.Flamegraph}}
	paths := []map[string]any{}
	examined, scheduled := 0, 1
	matchedOccurrences := 0
	treeTruncated, contextTruncated := false, false
	for len(queue) > 0 {
		current := queue[len(queue)-1]
		queue = queue[:len(queue)-1]
		n := current.node
		if n == nil {
			continue
		}
		if n.Self < 0 || n.Value < 0 || n.Self > n.Value {
			return unavailable("call_tree_has_invalid_weights")
		}
		examined++
		if n.Name == name {
			matchedOccurrences++
		}
		if n.Name == name && len(paths) < 5 {
			children := append([]*diagnosisTree(nil), n.Children...)
			sort.SliceStable(children, func(i, j int) bool {
				if children[i] == nil {
					return false
				}
				if children[j] == nil {
					return true
				}
				return children[i].Value > children[j].Value
			})
			callees := []map[string]any{}
			calleesTruncated := false
			for _, child := range children {
				if child == nil {
					continue
				}
				if len(callees) >= 3 {
					calleesTruncated = true
					break
				}
				v := symbol(child)
				v["inclusive_weight"] = child.Value
				v["self_weight"] = child.Self
				callees = append(callees, v)
			}
			ancestorsTruncated := current.depth > len(current.ancestors)
			contextTruncated = contextTruncated || ancestorsTruncated || calleesTruncated
			paths = append(paths, map[string]any{"ancestors": current.ancestors, "ancestors_truncated": ancestorsTruncated, "selected": symbol(n), "self_weight": n.Self, "inclusive_weight": n.Value, "top_callees": callees, "callees_truncated": calleesTruncated})
		}
		if current.depth >= 64 {
			if len(n.Children) > 0 {
				treeTruncated = true
			}
			continue
		}
		ancestors := append(append([]map[string]any(nil), current.ancestors...), symbol(n))
		if len(ancestors) > 6 {
			ancestors = ancestors[len(ancestors)-6:]
		}
		for i := len(n.Children) - 1; i >= 0; i-- {
			if scheduled >= 4096 {
				treeTruncated = true
				break
			}
			queue = append(queue, item{node: n.Children[i], ancestors: ancestors, depth: current.depth + 1})
			scheduled++
		}
	}
	return json.Marshal(map[string]any{"available": true, "symbol": "symbol_" + repository.Digest(name)[:12], "occurrences": paths, "matched_occurrences": matchedOccurrences, "occurrences_truncated": matchedOccurrences > 5, "examined_nodes": examined, "tree_truncated": treeTruncated, "truncated": treeTruncated || contextTruncated || matchedOccurrences > 5, "limits": map[string]int{"nodes": 4096, "depth": 64, "occurrences": 5, "ancestors": 6, "callees": 3}, "interpretation": "每项是调用树中的独立出现位置；递归inclusive不可相加。权重单位沿用原profile，不能当作请求延迟。tree_truncated表示遍历预算耗尽；ancestors_truncated/callees_truncated/occurrences_truncated分别表示返回上下文省略。matched_occurrences只统计已遍历树。单次采集不证明其他时段或负载没有其他路径。"})
}
