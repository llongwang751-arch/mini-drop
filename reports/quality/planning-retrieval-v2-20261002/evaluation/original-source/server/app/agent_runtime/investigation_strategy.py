"""Comparable next-action policies sharing the existing execution authority."""


def select_react_candidate(candidates, metrics):
    # Follow the freshly replanned order; no UCT bonus or simulated reward.
    eligible = [c for c in candidates if c.get("node_id")
                and not (metrics.get(c["node_id"]) or {}).get("pruned")
                and not (metrics.get(c["node_id"]) or {}).get("visits")]
    eligible.sort(key=lambda c: (c.get("expansion_origin") == "EXISTING_UNVISITED_FRONTIER",
                                 int(c.get("rank", 0)), c["node_id"]))
    if not eligible:
        return None
    candidate = eligible[0]
    return {"node_id": candidate["node_id"], "candidate_key": candidate.get("candidate_key"),
            "score": None, "components": {}, "rank": candidate.get("rank", 0),
            "selection_policy": "REACT", "reason": "按当前观察后的规划顺序选择下一探针，不使用 UCT 或模拟评分。"}
