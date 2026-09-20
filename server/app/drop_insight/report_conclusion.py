"""报告结论渲染：从已接受的证据渲染根因结论与具体发现，不发明数据。

从 service.py 拆出的叶子模块；service 命名空间继续 re-export，
作为调用方与测试的唯一补丁点。
"""

from __future__ import annotations

from .evidence import EvidenceEnvelope


def _derive_report_conclusion(
    hypothesis_statement: str,
    *,
    support_refs: list[str],
    counter_refs: list[str],
    supporting: list[EvidenceEnvelope] | None = None,
    verification_status: str | None = None,
) -> str:
    """Create a root-cause statement from accepted immutable evidence.

    A hypothesis is only a question posed by the planner.  Repeating that
    question after a SUPPORT predicate produced misleading reports such as
    "JVM may have a hotspot, GC pressure or lock contention".  The report
    instead names the concrete function/resource observed by the Analyzer and
    keeps unverified causal alternatives outside the conclusion.
    """

    if not support_refs:
        if counter_refs:
            return (
                "本轮判断：现有可信证据未支持该假设，且存在反证；"
                f"暂不接受假设“{hypothesis_statement}”。"
            )
        return (
            "本轮判断：当前没有能够支持该假设的可信证据；"
            f"假设“{hypothesis_statement}”仍待验证。"
        )

    concrete_finding = _concrete_report_finding(supporting or [])
    if counter_refs:
        if concrete_finding:
            return (
                f"阶段性根因：{concrete_finding}但同一诊断中仍存在反证，"
                "暂不能把它提升为最终根因。"
            )
        return (
            "本轮判断：可信证据部分支持该假设，同时存在反证；"
            f"假设“{hypothesis_statement}”需要继续证伪。"
        )

    if concrete_finding:
        title = "根因结论" if verification_status == "VERIFIED" else "阶段性根因"
        return f"{title}：{concrete_finding}"

    return (
        "阶段性判断：证据与候选假设一致，但尚未定位到具体函数、资源或依赖；"
        f"不能把假设“{hypothesis_statement}”直接写成最终根因，需要继续取证。"
    )


def _concrete_report_finding(supporting: list[EvidenceEnvelope]) -> str | None:
    """Render the strongest evidence-derived finding without inventing data."""

    candidates: list[tuple[int, EvidenceEnvelope, dict, dict]] = []
    for envelope in supporting:
        observation = envelope.observation if isinstance(envelope.observation, dict) else {}
        metadata = observation.get("metadata")
        if not isinstance(metadata, dict):
            continue
        predicate = metadata.get("hypothesis_predicate")
        if not isinstance(predicate, dict) or predicate.get("outcome") != "SUPPORT":
            continue
        metrics = predicate.get("metrics")
        if not isinstance(metrics, dict):
            metrics = {}
        function_name = str(metrics.get("dominant_function") or "").strip()
        try:
            dominant_percent = float(metrics.get("dominant_percent") or 0.0)
        except (TypeError, ValueError):
            dominant_percent = 0.0
        score = (100 if function_name else 0) + int(dominant_percent)
        candidates.append((score, envelope, metadata, metrics))
    if not candidates:
        return None

    _, envelope, metadata, metrics = max(candidates, key=lambda item: item[0])
    function_name = str(metrics.get("dominant_function") or "").strip()
    try:
        dominant_percent = float(metrics.get("dominant_percent") or 0.0)
    except (TypeError, ValueError):
        dominant_percent = 0.0
    percent_text = f"，占有效样本的 {dominant_percent:.1f}%" if dominant_percent > 0 else ""
    sample_count = envelope.quality.sample_count if envelope.quality.sample_count_known else 0
    sample_text = f"在 {sample_count} 个有效样本中，" if sample_count > 0 else ""
    schema_version = str(metadata.get("schema_version") or "").casefold()
    profile_event = str(
        metrics.get("profile_event") or metadata.get("profile_event") or ""
    ).casefold()
    top_functions = metadata.get("top_functions")
    top_functions = top_functions if isinstance(top_functions, list) else []

    if schema_version.startswith("java_async_profile.") and function_name:
        # async-profiler commonly places a generated ``$$Lambda...run``
        # adapter above the actual application method. Prefer the first real
        # Java business frame while keeping the exact observed symbol.
        business_function = next(
            (
                str(row.get("name") or "").strip()
                for row in top_functions
                if isinstance(row, dict)
                and str(row.get("name") or "").strip()
                and "$$Lambda" not in str(row.get("name") or "")
                and not str(row.get("name") or "").strip().endswith("[]")
                and not str(row.get("name") or "").strip().startswith("java/")
                and not str(row.get("name") or "").strip().startswith("jdk/")
            ),
            function_name,
        )
        event_labels = {
            "alloc": "Java 对象分配热点",
            "lock": "Java 锁等待热点",
            "wall": "Java 阻塞/等待热点",
            "cpu": "Java CPU 执行热点",
        }
        event_label = event_labels.get(profile_event, "Java 性能热点")
        allocated_types = []
        if profile_event == "alloc":
            for row in top_functions:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "").strip()
                if name.endswith("[]") and name not in allocated_types:
                    allocated_types.append(name)
        type_text = (
            f"，主要分配对象为 {'、'.join(allocated_types[:3])}"
            if allocated_types
            else ""
        )
        gc_counters = metadata.get("jvm_gc_counters")
        gc_counters = gc_counters if isinstance(gc_counters, dict) else {}
        gc_delta = gc_counters.get("delta")
        gc_delta = gc_delta if isinstance(gc_delta, dict) else {}
        gc_count_delta = max(0, int(gc_delta.get("gc_count") or 0))
        gc_time_delta = max(0, int(gc_delta.get("gc_time_ms") or 0))
        allocated_delta = max(0, int(gc_delta.get("allocated_bytes") or 0))
        allocation_boundary = (
            f"同一采集窗口的独立 JVM 计数器同时记录到 GC {gc_count_delta} 次、"
            f"GC 耗时增加 {gc_time_delta} ms、累计分配增加 {allocated_delta} 字节；"
            "这确认了分配与 GC 活动相关，但仍不能冒充 Full GC 次数或停顿分位数。"
            if gc_counters and (gc_count_delta > 0 or gc_time_delta > 0)
            else "该证据确认了集中对象分配路径，但没有独立证明 GC 暂停或锁竞争是主瓶颈。"
        )
        boundary = {
            "alloc": allocation_boundary,
            "lock": "该证据确认了锁等待路径，但仍需修复前后对照证明它对整体延迟的因果贡献。",
            "wall": "该证据确认了阻塞路径，但仍需依赖侧或系统侧证据区分具体等待来源。",
            "cpu": "该证据确认了 CPU 热路径，但仍需修复前后对照确认其因果贡献。",
        }.get(profile_event, "该证据定位了具体热路径，仍需修复前后对照完成因果验证。")
        return (
            f"{sample_text}{event_label}定位在业务调用路径 `{business_function}`"
            f"{percent_text}{type_text}。{boundary}"
        )

    if function_name:
        if schema_version.startswith("go_pprof_analysis."):
            profile_label = "Go CPU 热点"
        elif schema_version.startswith("pyspy_analysis."):
            profile_label = "Python 源码热点"
        else:
            profile_label = "性能热点"
        return (
            f"{sample_text}{profile_label}定位在 `{function_name}`{percent_text}。"
            "该函数是当前证据窗口内最集中的执行路径；仍需修复前后对照确认因果贡献。"
        )

    lock_wait_count = metrics.get("lock_wait_count")
    blocker_count = metrics.get("blocker_count")
    if lock_wait_count is not None or blocker_count is not None:
        return (
            f"数据库锁等待链已被结构化证据确认：等待会话 {int(lock_wait_count or 0)} 个，"
            f"阻塞会话 {int(blocker_count or 0)} 个。需要解除阻塞并复测事务延迟。"
        )
    return None


def _derive_next_actions(
    *,
    support_refs: list[str],
    counter_refs: list[str],
) -> list[str]:
    if not support_refs:
        return ["补充同一目标、同一时间窗口且经过 Analyzer 验证的结构化证据"]
    if counter_refs:
        return ["针对冲突证据执行独立的证伪采集，并比较同窗口结果"]
    return ["在相同负载下执行修复前后复测，确认热点和副作用变化"]
