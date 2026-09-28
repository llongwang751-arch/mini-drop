"""Shared, deliberately narrow process CPU observation contract."""
from __future__ import annotations

import re


EVIDENCE_PLANNING_REQUIREMENT = (
    "falsification_criteria 必须描述采集成功时与假设相反的可观察结果；"
    "采集失败、权限不足、无法附加、样本不足只能表示不可观测，不能作为反证。"
    "若假设明确宣称进程 CPU 升高、饱和或 CPU 计算主导，必须规划独立的操作系统 CPU 数值反证，"
    "例如‘目标进程 CPU 占用率低于 50%’或‘Target process CPU usage is below 50%’。"
    "百分比按单核100%计，阈值须符合当前假设，不能把其他域或额外命题塞进同一条件。"
    "运行时 Profile 用于定位函数，collect_sys_metrics 用于同一目标的独立 /proc CPU 计数；"
    "重复 Profile 不是独立 OS 对照。GIL、锁、等待或普通源码定位不要求凭空添加 CPU 高占用断言。"
    "仅补采原假设尚未覆盖的真实判据，不为了得到通过而删除或改写反证。"
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


def cpu_plan_validation_error(hypotheses: list) -> str | None:
    for row in hypotheses:
        item = row.model_dump() if hasattr(row, "model_dump") else row
        if (isinstance(item, dict)
            and cpu_utilization_hypothesis(item.get("statement", ""))
            and not process_cpu_thresholds(item.get("falsification_criteria") or [])):
            return (
                "该假设明确断言进程 CPU 高占用，但没有可由独立 OS 计数验证的数值反证。"
                "请声明与假设相符的单核 CPU 阈值，例如‘目标进程 CPU 占用率低于 50%’，"
                "并用 collect_sys_metrics 检查；函数分布、解释器占比或复合‘平稳且低于’不能替代。"
                "若并未主张 CPU 高占用，应缩小假设本身的含义；不要删除其他仍必要的反证以获得通过。"
            )
    return None
