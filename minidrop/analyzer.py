import hashlib
import json
import re


RULES = [
    (re.compile(r"(malloc|alloc|gc)", re.I), "内存分配较热，建议检查对象生命周期、垃圾回收和对象池策略。"),
    (re.compile(r"(read|write|io|disk)", re.I), "I/O 操作占比较高，建议检查延迟分布、批量处理和磁盘性能。"),
    (re.compile(r"(lock|mutex|futex)", re.I), "检测到锁竞争，建议检查临界区大小和并发访问方式。"),
    (re.compile(r"(sleep|wait|poll)", re.I), "等待操作占比较高，建议结合上游调用延迟进一步分析。"),
]


def _flame_tree(stacks):
    root = {"name": "all", "value": 0, "children": []}
    for item in stacks:
        value = int(item.get("value", 1))
        root["value"] += value
        node = root
        for name in item.get("stack", ["unknown"]):
            child = next((x for x in node["children"] if x["name"] == name), None)
            if not child:
                child = {"name": name, "value": 0, "children": []}
                node["children"].append(child)
            child["value"] += value
            node = child
    return root


def analyze(raw):
    stacks = raw.get("stacks") or [{"stack": ["unknown"], "value": 1}]
    totals = {}
    for item in stacks:
        for frame in item["stack"]:
            totals[frame] = totals.get(frame, 0) + int(item.get("value", 1))
    top = [{"name": k, "samples": v} for k, v in sorted(totals.items(), key=lambda x: x[1], reverse=True)[:10]]
    advice = []
    for item in top:
        for pattern, message in RULES:
            if pattern.search(item["name"]):
                advice.append({"evidence": item, "conclusion": message, "rule": pattern.pattern})
    if not advice:
        advice.append(
            {
                "evidence": top[0] if top else {},
                "conclusion": "未匹配到已知性能模式，建议与历史基线进行对比分析。",
                "rule": "fallback",
            }
        )
    fingerprint = hashlib.sha256(json.dumps(top, sort_keys=True).encode()).hexdigest()[:12]
    return {
        "flamegraph": _flame_tree(stacks),
        "top": top,
        "series": raw.get("samples", []),
        "histogram": raw.get("histogram", []),
        "attribution": advice,
        "fingerprint": fingerprint,
        "collector_meta": raw.get("meta", {}),
    }
