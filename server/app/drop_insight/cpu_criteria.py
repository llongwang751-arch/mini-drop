"""Shared, deliberately narrow process CPU observation contract."""
from __future__ import annotations

import re
from decimal import Decimal


EVIDENCE_PLANNING_REQUIREMENT = (
    "falsification_criteria 必须描述采集成功时与假设相反的可观察结果；"
    "采集失败、权限不足、无法附加、样本不足只能表示不可观测，不能作为反证。"
    "若假设明确宣称进程 CPU 升高、饱和或 CPU 计算主导，必须规划独立的操作系统 CPU 数值反证，"
    "例如‘目标进程 CPU 占用率低于 50%’或‘Target process CPU usage is below 50%’。"
    "百分比按单核100%计，阈值须符合当前假设，不能把其他域或额外命题塞进同一条件。"
    "运行时 Profile 用于定位函数，collect_sys_metrics 用于同一目标的独立 /proc CPU 计数；"
    "重复 Profile 不是独立 OS 对照。GIL、锁、等待或普通源码定位不要求凭空添加 CPU 高占用断言。"
    "仅补采原假设尚未覆盖的真实判据，不为了得到通过而删除或改写反证。"
    "Python/Go CPU 观察只能使用注册的分别采样合同，不能声称同窗、跨窗稳定或函数导致全部延迟；"
    "如计划校验返回unsupported槽位，应提出含义准确的新假设或明确不可验证，不保留强主张仅删除判据。"
)


def process_cpu_thresholds(entries: list) -> list[tuple[int, float]]:
    """Return only complete single-domain, strict-less-than CPU criteria."""
    patterns = (
        r"(?:target )?process cpu(?: usage)? (?:is |remains )?(?:below|less than) (\d+(?:\.\d+)?)\s*%[.]?",
        r"(?:目标)?进程\s*cpu(?:\s*占用(?:率)?)?\s*(?:低于|小于)\s*(\d+(?:\.\d+)?)\s*%[。]?",
    )
    result = []
    for index, entry in enumerate(entries):
        for pattern in patterns:
            match = re.fullmatch(pattern, str(entry).strip().casefold())
            if match and 0 < float(match[1]) <= 10000:
                result.append((index, float(match[1])))
                break
    return result


def cpu_utilization_hypothesis(statement: str) -> bool:
    """Recognize explicit high-CPU assertions, not all profiler hypotheses."""
    text = str(statement).casefold()
    patterns = (
        r"cpu\s*(?:占用率?|利用率)?\s*(?:持续|明显|显著)?(?:升高|飙升|饱和|过高)",
        r"cpu[^。；;]{0,60}(?:计算主导|函数主导|热点主导)",
        r"(?:high|elevated|saturated)\s+(?:process\s+)?cpu",
        r"cpu\s+(?:usage\s+|utilization\s+)?(?:is\s+|remains\s+)?(?:high|elevated|saturated)",
        r"cpu[- ]bound",
    )
    return any(re.search(pattern, text) for pattern in patterns)


CPU_OBSERVATION_CONTRACTS = {
    "PYTHON": {"id": "python-profile-and-os-cpu.v1", "profile_threshold": 70.0,
               "expected": "Python 源码采样中最多三个具备源码位置的函数合计占比至少 70%",
               "expected_en": "At most three source-mapped Python functions account for at least 70% of profile samples"},
    "GO": {"id": "go-profile-and-os-cpu.v1", "profile_threshold": 20.0,
           "expected": "Go pprof 中一个具备源码位置的业务函数累计占比至少 20%",
           "expected_en": "One source-mapped Go application function has at least 20% inclusive pprof sample share"},
}


def cpu_observation_plan(runtime: str, threshold: float = 50, *, language: str = "zh-CN") -> dict:
    runtime = runtime.upper()
    contract = CPU_OBSERVATION_CONTRACTS[runtime]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0 < threshold <= 10000:
        raise ValueError("CPU threshold must be finite, positive and at most 10000")
    number = format(Decimal(str(threshold)).normalize(), "f")
    name = "Python" if runtime == "PYTHON" else "Go"
    if language == "en-US":
        statement = (f"In this diagnosis, the {name} process CPU is at least {number}% of one core in the system-metrics window; "
                     "a separately collected profile contains an attributable execution path")
        expected = contract["expected_en"]
        counter = f"Target process CPU usage is below {number}%"
    else:
        statement = (f"本次诊断中，{name} 进程在系统指标采集窗口的 CPU 占用率至少 {number}%（单核口径），"
                     "另一次独立采样观察到可归属的执行路径")
        expected = contract["expected"]
        counter = f"目标进程 CPU 占用率低于 {number}%"
    return {"statement": statement, "expected_observations": [expected], "falsification_criteria": [counter]}


def compile_cpu_observation_contract(statement: str, expected: list, falsification: list, *, runtime: str | None = None) -> dict:
    """Compile a new bounded observation claim; never rewrite persisted prose.

    The profile and OS readings may come from different collection windows.
    These contracts assert two observations, not causation or temporal stability.
    """
    text = " ".join([str(statement), *(str(x) for x in expected)]).casefold()
    inferred = runtime.upper() if isinstance(runtime, str) else None
    if inferred is None:
        if "python" in text or "py-spy" in text:
            inferred = "PYTHON"
        elif re.search(r"\bgo\b", text, flags=re.ASCII) or "pprof" in text:
            inferred = "GO"
    claim = str(statement).casefold()
    cpu_observation = (bool(re.search(r"\bcpu\b", claim, flags=re.ASCII)) and not bool(re.search(r"cpu\s*(?:未升高|不高|没有升高|is not high|is low)", claim)) or cpu_utilization_hypothesis(statement)
        or any(token in claim.replace(" ", "") for token in ("cpu计算热点", "cpu热点", "cpu异常", "cpu占用率至少"))
        or "process cpu is at least" in claim or "cpu hotspot" in claim
        or any(str(item).strip().casefold() in {definition["expected"].casefold(), definition["expected_en"].casefold()}
               for item in expected for definition in CPU_OBSERVATION_CONTRACTS.values()))
    if inferred not in CPU_OBSERVATION_CONTRACTS or not cpu_observation:
        return {"status": "NOT_APPLICABLE", "executable": False, "runtime": inferred, "unsupported_slots": []}
    contract = CPU_OBSERVATION_CONTRACTS[inferred]
    unsupported = []
    indexes = []
    allowed_expected = {contract["expected"].casefold(), contract["expected_en"].casefold()}
    for index, value in enumerate(expected):
        if str(value).strip().casefold() not in allowed_expected or index > 0:
            unsupported.append({"kind": "expected", "index": index,
                                "reason": "No registered exact evaluator for this observation; use the bounded profile contract or a separate hypothesis"})
        else:
            indexes.append(index)
    if not expected:
        unsupported.append({"kind": "expected", "index": None, "reason": "A registered profile observation is required"})
    thresholds = process_cpu_thresholds(falsification)
    for index, value in enumerate(falsification):
        if index not in {i for i, _ in thresholds} or index > 0:
            unsupported.append({"kind": "falsification", "index": index,
                                "reason": "This contract evaluates exactly one independent OS CPU threshold, not distribution, stability or another domain"})
    if not falsification:
        unsupported.append({"kind": "falsification", "index": None, "reason": "An independent OS CPU threshold is required"})
    threshold = thresholds[0][1] if thresholds else None
    allowed_statements = {
        cpu_observation_plan(inferred, threshold, language=language)["statement"].casefold()
        for language in ("zh-CN", "en-US")
    } if threshold is not None else set()
    if str(statement).strip().casefold() not in allowed_statements:
        unsupported.append({"kind": "statement", "index": None,
                            "reason": "State separately collected CPU and profile observations; causation, dominance, same-window and cross-window stability are not established"})
    return {"status": "UNSUPPORTED" if unsupported else "SUPPORTED", "executable": not unsupported,
            "runtime": inferred, "contract_id": contract["id"], "cpu_threshold": threshold,
            "profile_threshold": contract["profile_threshold"], "expected_indexes": indexes,
            "unsupported_slots": unsupported}


def cpu_plan_validation_error(hypotheses: list) -> str | None:
    for row in hypotheses:
        item = row.model_dump() if hasattr(row, "model_dump") else row
        if not isinstance(item, dict):
            continue
        compiled = compile_cpu_observation_contract(item.get("statement", ""),
                    item.get("expected_observations") or [], item.get("falsification_criteria") or [])
        if compiled["status"] == "UNSUPPORTED":
            slots = "; ".join(f"{x['kind']}[{x['index']}]: {x['reason']}" for x in compiled["unsupported_slots"])
            example = cpu_observation_plan(compiled["runtime"], compiled.get("cpu_threshold") or 50)
            import json
            return ("计划包含当前验证器不能执行的槽位：" + slots +
                    "。不得保留原因果主张却删除判据换取通过。可将新假设明确收窄到分别采集的两个观察，"
                    "或拆分另一个尚不可验证的假设；不要修改已持久化计划。可执行合同示例（阈值须与新假设相符）：" +
                    json.dumps(example, ensure_ascii=False) + "；独立OS工具为 collect_sys_metrics。")
        if (cpu_utilization_hypothesis(item.get("statement", ""))
            and not process_cpu_thresholds(item.get("falsification_criteria") or [])):
            return "CPU 高占用假设需要独立 OS 数值反证及 collect_sys_metrics；不得用其他证据域或复合命题替代。"
    return None


# Expose the actual supported language to the planner before its first call.
import json as _json
EVIDENCE_PLANNING_REQUIREMENT += " 注册合同范例：" + _json.dumps(
    [cpu_observation_plan(runtime, language=language)
     for runtime in ("PYTHON", "GO") for language in ("zh-CN", "en-US")],
    ensure_ascii=False,
)
