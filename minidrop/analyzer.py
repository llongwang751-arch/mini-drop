import hashlib
import json
import re


RULES = [
    (re.compile(r"(malloc|alloc|gc)", re.I), "Allocation is hot; inspect object lifetime and pooling."),
    (re.compile(r"(read|write|io|disk)", re.I), "I/O dominates; validate latency distribution and batching."),
    (re.compile(r"(lock|mutex|futex)", re.I), "Locking is visible; inspect contention and critical-section size."),
    (re.compile(r"(sleep|wait|poll)", re.I), "Waiting dominates samples; correlate with upstream latency."),
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
                "conclusion": "No known anti-pattern matched; compare this profile with a historical baseline.",
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
