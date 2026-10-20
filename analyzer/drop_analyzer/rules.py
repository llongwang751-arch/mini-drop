"""Conservative rules; findings are evidence pointers, not causal diagnoses."""

RULE_VERSION = "1.0.0"


def suggestions(top: dict, settings: list[dict] | None = None) -> list[dict]:
    findings = []
    total = top["total_samples"]
    observations = top.get("observation_count", total)
    if observations < 50:
        return findings
    categories = [
        (
            "ALLOCATION_HOTSPOT",
            ("malloc", "calloc", "realloc", "operator new", "runtime.mallocgc"),
            "分配函数占比较高",
            "核对分配速率和对象生命周期；用堆或分配采样确认后，再评估复用对象或减少临时对象。",
        ),
        (
            "LOCK_HOTSPOT",
            ("pthread_mutex", "futex", "runtime.lock", "monitorenter"),
            "同步相关函数占比较高",
            "结合线程状态和锁等待指标核对竞争；检查临界区时长及锁粒度。CPU 样本不能单独证明锁等待时间。",
        ),
        (
            "GC_HOTSPOT",
            ("runtime.gc", "g1par", "garbagecollect", "gcworker", "gc_mark"),
            "垃圾回收相关函数占比较高",
            "结合 GC 日志、暂停时间和分配速率核对回收开销，再评估对象生命周期。",
        ),
    ]
    for code, patterns, title, action in categories:
        key = {
            "ALLOCATION_HOTSPOT": "allocation",
            "LOCK_HOTSPOT": "lock",
            "GC_HOTSPOT": "gc",
        }[code]
        configuration = next(
            (setting for setting in settings or [] if setting["key"] == key), {}
        )
        minimum = int(configuration.get("min_samples", 50))
        threshold = float(configuration.get("threshold", 20)) / 100
        if not configuration.get("enabled", True) or observations < minimum:
            continue
        matches = [
            row
            for row in top["self"]
            if row["self_ratio"] >= threshold
            and any(pattern in row["name"].lower() for pattern in patterns)
        ]
        for row in matches[:3]:
            findings.append(
                {
                    "code": code,
                    "severity": configuration.get("severity", "info"),
                    "title": title,
                    "action": action,
                    "rule_version": str(configuration.get("version", RULE_VERSION)),
                    "confidence": "moderate",
                    "evidence": {
                        "function": row["name"],
                        "self_samples": row["self"],
                        "total_samples": total,
                        "self_ratio": row["self_ratio"],
                        "observation_count": observations,
                        "minimum_samples": minimum,
                        "threshold": threshold,
                        "unit": top.get("unit", "samples"),
                    },
                }
            )
    return findings
