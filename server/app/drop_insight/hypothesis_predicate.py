"""假设谓词计算：把 Analyzer 产物确定性地映射为 SUPPORT/COUNTER/CONTROL/NEUTRAL。

从 service.py 拆出的叶子模块：覆盖槽位由判据文本与证据域的真实匹配产生，
不硬编码槽位。service 命名空间继续 re-export，作为调用方与测试的唯一补丁点。
"""

from __future__ import annotations

import re

from server.app.models import ArtifactModel, DropInsightHypothesisModel
from .evidence import observed_count, observed_nonnegative
from .cpu_criteria import process_cpu_thresholds, compile_cpu_observation_contract
from .performance_criteria import evaluate_performance_criterion


def _invalid_numeric_observation(reason: str = "Required numeric observation is missing, invalid or outside its domain") -> dict:
    return {"outcome": "NEUTRAL", "version": "hypothesis-predicate-v3",
            "reason": reason,
            "criterion_indexes": [], "metrics": {}}


def _structured_signal_predicate(
    hypothesis: DropInsightHypothesisModel,
    metadata: dict,
) -> dict | None:
    """Evaluate exact slots. A domain keyword cannot prove an arbitrary claim."""

    signals = metadata.get("signals")
    if not isinstance(signals, dict):
        return None
    expected = hypothesis.expected_observations_json or []
    for direction, entries in (("COUNTER", hypothesis.falsification_criteria_json or []), ("SUPPORT", expected)):
        evaluations = [(index, evaluate_performance_criterion(item, signals)) for index, item in enumerate(entries)]
        matched = [(index, value) for index, value in evaluations if value and value["matches"]]
        if matched:
            signal_name = matched[0][1]["signal"]
            matched = [(index, value) for index, value in matched if value["signal"] == signal_name]
            return {"outcome": direction, "version": "performance-criterion-v1",
                    "reason": "Exact numeric performance observation matched declared criteria",
                    "criterion_indexes": [index for index, _ in matched],
                    "metrics": {value["field"]: value["value"] for _, value in matched},
                    "signal": signal_name, "claim_scope": "BOUNDED_OBSERVATION"}
    # Legacy prose can still select an investigation domain, but cannot earn
    # coverage: queue growth, packet loss and fsync latency require actual data.
    return None


def _criterion_text_indexes(
    entries: list,
    tokens: tuple[str, ...],
) -> list[int]:
    """Map evidence-domain tokens to the criterion slots whose text matches.

    A coverage slot is earned only when the criterion text actually mentions
    the observed evidence domain; an empty result means the evidence covers
    no specific slot and must not fabricate one.
    """
    return [
        index
        for index, item in enumerate(entries)
        if isinstance(item, str)
        and any(token in item.casefold() for token in tokens)
    ]


def _process_cpu_control(hypothesis, metadata: dict) -> dict | None:
    """Evaluate only an explicit process-CPU threshold, never an I/O proxy.

    Linux /proc counters are independent of function profile samples. This
    observation does not prove that a particular function caused the CPU use.
    The usual signed artifact, target identity and time scope checks still
    apply when the predicate is imported as Evidence.
    """
    if metadata.get("schema_version") != "sys_metrics_analysis.v2":
        return None
    if not any(token in str(hypothesis.statement).casefold()
               for token in ("cpu", "热点", "hotspot", "计算")):
        return None
    criteria = process_cpu_thresholds(hypothesis.falsification_criteria_json or [])
    if not criteria:
        return None
    identity = metadata.get("process_identity") or {}
    window = metadata.get("process_cpu_window") or {}
    if not isinstance(identity, dict) or not isinstance(window, dict):
        return _invalid_numeric_observation()
    counts = {key: observed_count(window.get(key)) for key in
              ("clock_ticks_per_second", "start_cpu_ticks", "end_cpu_ticks", "sample_count")}
    start = observed_nonnegative(window.get("start_unix_ms"))
    end = observed_nonnegative(window.get("end_unix_ms"))
    usage = observed_nonnegative(window.get("process_cpu_core_usage"))
    if (metadata.get("source_schema_version") != "sys_metrics.v2"
        or identity.get("verified") is not True
        or not observed_count(identity.get("pid"))
        or not observed_count(identity.get("start_ticks"))
        or window.get("source") != "linux_proc_stat"
        or None in (*counts.values(), start, end, usage)
        or not counts["clock_ticks_per_second"] or counts["sample_count"] < 2
        or start == 0 or end - start < 1000 or counts["end_cpu_ticks"] < counts["start_cpu_ticks"]):
        return _invalid_numeric_observation("Independent process CPU counter window is incomplete")
    recomputed = (counts["end_cpu_ticks"] - counts["start_cpu_ticks"]) / counts["clock_ticks_per_second"] / ((end - start) / 1000) * 100
    if abs(usage - recomputed) > 1e-9:
        return _invalid_numeric_observation("Process CPU value disagrees with its counter window")
    counters = [index for index, threshold in criteria if usage < threshold]
    controls = [index for index, threshold in criteria if usage >= threshold]
    # A refuting observation wins; mixed thresholds cannot erase a counter.
    outcome = "COUNTER" if counters else "CONTROL"
    return {
        "outcome": outcome, "version": "hypothesis-predicate-v3",
        "reason": f"Independent Linux process CPU counters measured {usage:.3f}% of one core over {(end - start) / 1000:.3f}s; "
                  + ("the specified low-CPU falsification was observed" if counters else "the specified low-CPU falsification was not observed"),
        "criterion_indexes": counters if counters else controls,
        "metrics": {**window, "pid": identity["pid"], "start_ticks": identity["start_ticks"]},
    }


def _compute_hypothesis_predicate(
    hypothesis: DropInsightHypothesisModel,
    metadata: dict,
) -> dict | None:
    """Deterministically evaluate analyzer output against the hypothesis plan.

    Produces a normalized predicate: a top function matching an expected
    observation -> SUPPORT; matching a falsification criterion -> COUNTER;
    otherwise None (the caller keeps the artifact NEUTRAL). This is what makes
    the counter-evidence gate reachable: without a COUNTER path, no imported
    artifact can ever satisfy ``has_independent_counter_or_control``.
    """
    expected = hypothesis.expected_observations_json or []
    falsification = hypothesis.falsification_criteria_json or []
    statement = str(hypothesis.statement or "").casefold()

    if metadata.get("scope_semantics") == "HOST_BLOCK_DEVICE" and metadata.get("target_attributed") is not True:
        return {"outcome": "NEUTRAL", "version": "hypothesis-predicate-v3", "reason": "仅观察到宿主机块设备 I/O，未归属目标进程；需要同窗口的进程读写与等待栈关联", "criterion_indexes": [], "metrics": {}}

    schema = str(metadata.get("schema_version") or "")
    profile_runtime = "PYTHON" if schema.startswith("pyspy_analysis.") else "GO" if schema.startswith("go_pprof_analysis.") else None
    observation_contract = compile_cpu_observation_contract(statement, expected, falsification, runtime=profile_runtime)
    if observation_contract["status"] == "UNSUPPORTED":
        return _invalid_numeric_observation("CPU observation contract has unsupported slots: " + str(observation_contract["unsupported_slots"]))
    if observation_contract["status"] == "SUPPORTED" and schema != "sys_metrics_analysis.v2" and profile_runtime != observation_contract["runtime"]:
        return _invalid_numeric_observation("This artifact does not implement the registered CPU observation domain")

    cpu_control = _process_cpu_control(hypothesis, metadata)
    if cpu_control is not None:
        return cpu_control
    structured_predicate = _structured_signal_predicate(hypothesis, metadata)
    if structured_predicate is not None:
        if not (profile_runtime and structured_predicate.get("signal") == "cpu_hotspot"):
            return structured_predicate
        # A CPU signal label is not an evaluator for a profile coverage slot.
        # Continue through the registered evaluator or legacy SUPPORT guard.

    if str(metadata.get("schema_version") or "") == "jvm_gc_metrics.v1":
        delta = metadata.get("delta")
        delta = delta if isinstance(delta, dict) else {}
        hypothesis_text = " ".join(
            [statement, *(str(item).casefold() for item in expected)]
        )
        gc_hypothesis = any(
            token in hypothesis_text
            for token in ("gc", "垃圾回收", "分配", "allocation", "堆")
        )
        if gc_hypothesis:
            gc_count_delta = observed_count(delta.get("gc_count"))
            gc_time_delta = observed_count(delta.get("gc_time_ms"))
            allocated_delta = observed_count(delta.get("allocated_bytes"))
            duration = observed_nonnegative(metadata.get("window_duration_ms"))
            if None in (gc_count_delta, gc_time_delta, allocated_delta, duration) or duration == 0:
                return _invalid_numeric_observation()
            metrics = {
                "gc_count_delta": gc_count_delta,
                "gc_time_ms_delta": gc_time_delta,
                "allocated_bytes_delta": allocated_delta,
                "window_duration_ms": duration,
            }
            gc_falsification_hits = _criterion_text_indexes(
                falsification,
                ("gc", "垃圾回收", "堆", "分配", "allocation"),
            )
            if allocated_delta > 0 and (gc_count_delta > 0 or gc_time_delta > 0):
                return {
                    "outcome": "CONTROL",
                    "version": "hypothesis-predicate-v2",
                    "reason": (
                        "same-window JVM counters independently observed "
                        f"{gc_count_delta} GC cycle(s), {gc_time_delta} ms GC time, "
                        f"and {allocated_delta} allocated bytes"
                    ),
                    "criterion_indexes": gc_falsification_hits,
                    "metrics": metrics,
                }
            if gc_count_delta > 0 or gc_time_delta > 0:
                # GC was observed, but an allocation relationship was not.
                return _invalid_numeric_observation("GC activity was observed without a positive allocation delta; the proposed relationship is unverified")
            return {
                "outcome": "COUNTER",
                "version": "hypothesis-predicate-v2",
                "reason": (
                    "same-window JVM counters did not observe GC activity "
                    "under allocation profiling"
                ),
                "criterion_indexes": gc_falsification_hits,
                "metrics": metrics,
            }

    if str(metadata.get("schema_version") or "").startswith("database_lock."):
        lock_wait_count = observed_count(metadata.get("lock_wait_count"))
        blocker_count = observed_count(metadata.get("blocker_count"))
        if lock_wait_count is None or blocker_count is None:
            return _invalid_numeric_observation()
        blocking_edge_count = observed_count(metadata.get("blocking_edge_count"))
        wait_key = "lock_wait_ms" if "lock_wait_ms" in metadata else "max_wait_ms"
        max_wait_ms = observed_nonnegative(metadata.get(wait_key))
        if ("blocking_edge_count" in metadata and blocking_edge_count is None) or (
            wait_key in metadata and max_wait_ms is None
        ):
            return _invalid_numeric_observation()
        if "lock_wait_ms" in metadata and "max_wait_ms" in metadata and (
            observed_nonnegative(metadata["max_wait_ms"]) != max_wait_ms
        ):
            return _invalid_numeric_observation()
        database_hypothesis = any(token in statement for token in (
            "数据库", "锁等待", "阻塞", "deadlock", "database lock", "db lock",
        ))
        if database_hypothesis and lock_wait_count > 0 and blocker_count > 0:
            covered = _criterion_text_indexes(
                expected,
                ("锁", "lock", "阻塞", "block", "等待", "wait"),
            )
            return {
                "outcome": "SUPPORT",
                "version": "hypothesis-predicate-v2",
                "reason": (
                    f"observed {lock_wait_count} lock-waiting session(s), "
                    f"{blocker_count} blocker(s)"
                    + (f", max wait {max_wait_ms:.1f} ms" if max_wait_ms is not None else "")
                ),
                "criterion_indexes": covered,
                "metrics": {
                    "lock_wait_count": lock_wait_count,
                    "blocker_count": blocker_count,
                    "blocking_edge_count": blocking_edge_count,
                    "lock_wait_ms": max_wait_ms,
                },
            }
        if database_hypothesis and lock_wait_count == 0:
            return {
                "outcome": "COUNTER",
                "version": "hypothesis-predicate-v2",
                "reason": "bounded database snapshots contained no lock-waiting sessions",
                "criterion_indexes": _criterion_text_indexes(
                    falsification,
                    ("锁", "lock", "阻塞", "block", "等待", "wait"),
                ),
                "metrics": {
                    "lock_wait_count": 0,
                    "blocker_count": blocker_count,
                    "lock_wait_ms": max_wait_ms,
                },
            }

    top_functions = metadata.get("top_functions")
    if not isinstance(top_functions, list):
        return None
    raw_named = [
        row
        for row in top_functions
        if isinstance(row, dict)
        and isinstance(row.get("name"), str)
        and row["name"].strip()
    ]
    if not raw_named:
        return None
    # Missing or malformed ratios cannot prove either dominance or its absence.
    if any(observed_nonnegative(row.get("percent"), maximum=100) is None
           or ("self_percent" in row and observed_nonnegative(row["self_percent"], maximum=100) is None)
           for row in raw_named):
        return _invalid_numeric_observation()
    # Source-aware analyzers intentionally keep one TopN row per file/line.
    # Hypothesis scoring, however, reasons about functions.  A hot function
    # sampled on several executable lines must not be mistaken for several
    # unrelated weak hotspots (for example 40% + 25% + 10% in one loop).
    aggregated: dict[str, dict] = {}
    for row in raw_named:
        name = row["name"].strip()
        current = aggregated.setdefault(
            name,
            {
                "name": name,
                "percent": 0.0,
                "samples": 0,
                "self_percent": 0.0,
                "self_samples": 0,
                "locations": [],
            },
        )
        current["percent"] += _safe_percent(row.get("percent"))
        current["self_percent"] += _safe_percent(row.get("self_percent"))
        try:
            current["samples"] += max(0, int(row.get("samples") or 0))
            current["self_samples"] += max(0, int(row.get("self_samples") or 0))
        except (TypeError, ValueError, OverflowError):
            pass
        if row.get("file") or row.get("line"):
            current["locations"].append({
                "file": row.get("file"),
                "line": row.get("line"),
                "percent": _safe_percent(row.get("percent")),
            })
    named = list(aggregated.values())
    perf_profile = str(metadata.get("schema_version") or "").casefold().startswith(
        ("perf_analysis.", "continuous_perf_analysis.")
    )

    def _percent(row: dict) -> float:
        return _safe_percent(row.get("percent"))

    def _actionable_percent(row: dict) -> float:
        if perf_profile and "self_percent" in row:
            return _safe_percent(row.get("self_percent"))
        return _percent(row)

    def _is_kernel(name: str) -> bool:
        value = name.casefold().strip()
        markers = (
            "[kernel", "vmlinux", "__x64_sys_", "do_syscall_", "entry_syscall_",
            "schedule", "finish_task_switch", "irq", "softirq", "kworker",
        )
        return any(marker in value for marker in markers)

    def _is_lock(name: str) -> bool:
        value = name.casefold()
        return any(marker in value for marker in (
            "pthread_mutex", "futex", "spin_lock", "spinlock", "mutex_lock",
            "rwsem", "sem_wait", "lock_slowpath",
        ))

    def _is_runtime_container(name: str) -> bool:
        """Return whether a TopN row is a runtime/container frame, not code.

        perf's folded-stack TopN is inclusive, so loader/runtime containers can
        legitimately account for 100% of samples.  Treating ``[libpython]`` or
        the ``python`` executable as a business function turns a useful profile
        into a false source-hotspot predicate.
        """

        value = name.casefold().strip()
        if value.startswith("[") and value.endswith("]"):
            return True
        return bool(re.fullmatch(
            r"(?:python(?:\d+(?:\.\d+)*)?|java|node|ruby|php|perl)",
            value,
        ))

    def _has_source_location(row: dict) -> bool:
        for location in row.get("locations", []):
            if not isinstance(location, dict):
                continue
            try:
                line = int(location.get("line") or 0)
            except (TypeError, ValueError):
                line = 0
            if (
                isinstance(location.get("file"), str)
                and bool(location["file"].strip())
                and line > 0
            ):
                return True
        return False

    def _is_go_standard_frame(name: str) -> bool:
        value = name.casefold().strip()
        prefixes = (
            "runtime.", "internal/", "internal.", "crypto/", "crypto.",
            "sync.", "syscall.", "net/", "net.", "os.", "time.", "bytes.",
            "hash/", "hash.", "encoding/", "encoding.", "reflect.",
            "vendor/", "golang.org/",
        )
        return value in {"main.main", "runtime.main"} or value.startswith(prefixes)

    def _predicate(outcome: str, reason: str, indexes: list[int], **metrics):
        if outcome == "SUPPORT" and profile_runtime and observation_contract["status"] != "SUPPORTED":
            return _invalid_numeric_observation("Unregistered profile observation cannot cover a free-text expected condition")
        return {
            "outcome": outcome,
            "version": "hypothesis-predicate-v2",
            "reason": reason,
            "criterion_indexes": indexes,
            "metrics": metrics,
        }

    significant = [row for row in named if _percent(row) >= 20.0]
    user_rows = [row for row in named if not _is_kernel(str(row["name"]))]
    actionable_user_rows = [
        row for row in user_rows
        if not _is_runtime_container(str(row["name"]))
        and _actionable_percent(row) > 0.0
    ]
    kernel_rows = [row for row in named if _is_kernel(str(row["name"]))]
    lock_rows = [row for row in named if _is_lock(str(row["name"]))]
    dominant_user = max(user_rows, key=_percent, default=None)
    dominant_actionable_user = max(
        actionable_user_rows,
        key=_actionable_percent,
        default=None,
    )
    dominant_kernel = max(kernel_rows, key=_percent, default=None)
    dominant_user_pct = _percent(dominant_user) if dominant_user else 0.0
    dominant_actionable_user_pct = (
        _actionable_percent(dominant_actionable_user)
        if dominant_actionable_user
        else 0.0
    )
    dominant_kernel_pct = _percent(dominant_kernel) if dominant_kernel else 0.0

    hypothesis_text = " ".join(
        [statement, *(str(item).casefold() for item in expected if isinstance(item, str))]
    )
    user_hypothesis = any(token in hypothesis_text for token in (
        "用户态", "业务代码", "热点函数", "python hotspot",
        "hot function", "user-space", "userspace", "函数集中", "样本集中",
    )) or (
        "python" in hypothesis_text
        and "函数" in hypothesis_text
        and any(token in hypothesis_text for token in ("集中", "热点", "占比"))
    )
    # A pure GIL causal claim often mentions a single hotspot in its
    # falsification wording and must be scored before the generic user-hotspot
    # branch.  A planner may also emit a disjunctive candidate such as
    # ``热点函数或 GIL 竞争``.  That sentence intentionally keeps both causes
    # open, so real hotspot evidence must be allowed through the user-space
    # predicate instead of being swallowed by the GIL-only branch.
    gil_mentioned = "gil" in statement
    hotspot_mentioned = any(token in statement for token in (
        "热点函数", "函数热点", "hot function", "source hotspot",
    ))
    disjunction_pattern = r"(?:或(?:者)?|/|\bor\b)"
    mixed_gil_hotspot_hypothesis = bool(
        gil_mentioned
        and hotspot_mentioned
        and (
            re.search(
                rf"(?:热点函数|函数热点|hot\s+function|source\s+hotspot)"
                rf".{{0,32}}{disjunction_pattern}.{{0,32}}gil",
                statement,
            )
            or re.search(
                rf"gil.{{0,32}}{disjunction_pattern}.{{0,32}}"
                rf"(?:热点函数|函数热点|hot\s+function|source\s+hotspot)",
                statement,
            )
        )
    )
    gil_hypothesis = gil_mentioned and not mixed_gil_hotspot_hypothesis
    kernel_hypothesis = any(token in statement for token in (
        "内核态", "系统调用", "中断", "kernel", "syscall",
    ))
    lock_hypothesis = any(token in statement for token in (
        "锁竞争", "自旋", "lock contention", "spin",
    ))
    source_mapping_expected = any(
        any(token in str(item).casefold() for token in (
            "源码", "文件", "行号", "source file", "source line",
        ))
        for item in expected
        if isinstance(item, str)
    )
    pyspy_profile = str(metadata.get("schema_version") or "").casefold().startswith(
        "pyspy_analysis."
    )
    go_pprof_profile = str(metadata.get("schema_version") or "").casefold().startswith(
        "go_pprof_analysis."
    )
    java_profile = str(metadata.get("schema_version") or "").casefold().startswith(
        "java_async_profile."
    )

    # py-spy reports self samples at individual source lines.  Aggregate those
    # rows by function above, then evaluate the hypothesis' actual "one or a
    # few functions" criterion by cumulative concentration.  Requiring real
    # file+line locations keeps this separate from perf's inclusive runtime
    # containers and makes the emitted function names evidence-derived.
    source_functions = sorted(
        (
            row for row in actionable_user_rows
            if _has_source_location(row) and _percent(row) > 0
        ),
        # Percentages are rounded in analyzer metadata.  For an apparent tie,
        # prefer the function observed across more executable source lines;
        # this is a structural signal from the profile rather than a special
        # case for any demo function name.
        key=lambda row: (
            round(_percent(row), 1),
            len(row.get("locations", [])),
        ),
        reverse=True,
    )
    significant_source_functions = [
        row for row in source_functions if _percent(row) >= 10.0
    ]
    concentrated_source_functions = significant_source_functions[:3]
    concentrated_source_pct = min(
        100.0,
        sum(_percent(row) for row in concentrated_source_functions),
    )

    if observation_contract["status"] == "SUPPORTED":
        if observation_contract["runtime"] == "PYTHON":
            if (1 <= len(concentrated_source_functions) <= 3
                and len(significant_source_functions) <= 3
                and concentrated_source_pct >= observation_contract["profile_threshold"]):
                dominant = concentrated_source_functions[0]
                return _predicate("SUPPORT", "Registered Python source-profile concentration was observed in its own collection window",
                    observation_contract["expected_indexes"], dominant_function=dominant["name"], dominant_percent=_percent(dominant),
                    concentrated_percent=concentrated_source_pct,
                    concentrated_functions=[{"name": row["name"], "percent": _percent(row), "locations": row.get("locations", [])} for row in concentrated_source_functions],
                    profile_semantics="source_function_share", observation_contract=observation_contract["contract_id"])
        else:
            applications = [row for row in source_functions if not _is_go_standard_frame(str(row["name"]))]
            dominant = max(applications, key=_percent, default=None)
            if dominant is not None and _percent(dominant) >= observation_contract["profile_threshold"]:
                return _predicate("SUPPORT", "Registered Go source-mapped application path was observed in its own inclusive profile window",
                    observation_contract["expected_indexes"], dominant_function=dominant["name"], dominant_percent=_percent(dominant),
                    source_locations=dominant.get("locations", []), profile_semantics="inclusive",
                    observation_contract=observation_contract["contract_id"])
        return _invalid_numeric_observation("Profile did not establish the registered source-path observation")

    # Go pprof TopN is inclusive: every frame in one stack receives the same
    # sample weight, so a hot application path can legitimately produce more
    # than three high-percentage rows. Evaluate source-mapped application
    # frames directly and stop here; the generic token matcher below must not
    # mistake the word ``CPU`` for the ``goCPUHotFunction`` symbol.
    if go_pprof_profile:
        go_application_rows = [
            row
            for row in source_functions
            if not _is_go_standard_frame(str(row["name"]))
        ]
        dominant_go = max(go_application_rows, key=_percent, default=None)
        # Runtime words alone only describe the probe.  They do not prove a
        # waiting, networking or goroutine-contention hypothesis.  Claim a
        # CPU hotspot only when the candidate itself asks about a hotspot or
        # concentrated/high CPU execution.
        go_hotspot_hypothesis = any(
            token in hypothesis_text
            for token in ("热点", "hotspot", "hot function")
        ) or (
            "cpu" in hypothesis_text
            and any(
                token in hypothesis_text
                for token in (
                    "集中", "升高", "持续", "占用", "主导", "dominant",
                    "concentrat", "high", "saturat",
                )
            )
        )
        if (
            go_hotspot_hypothesis
            and dominant_go is not None
            and _percent(dominant_go) >= 20.0
        ):
            covered_indexes = [
                index
                for index, item in enumerate(expected)
                if isinstance(item, str)
                and any(
                    token in item.casefold()
                    for token in (
                        "pprof", "go ", "函数", "热点", "样本", "路径",
                        "function", "hot", "sample", "path",
                    )
                )
            ]
            return _predicate(
                "SUPPORT",
                f"Go pprof captured source-mapped application hotspot "
                f"{dominant_go['name']} at {_percent(dominant_go):.1f}%",
                covered_indexes,
                dominant_function=dominant_go["name"],
                dominant_percent=_percent(dominant_go),
                source_locations=dominant_go.get("locations", []),
                profile_semantics="inclusive",
            )
        return _predicate(
            "NEUTRAL",
            "Go pprof contains samples but no source-mapped application hotspot supports this hypothesis",
            [],
            profile_semantics="inclusive",
        )

    if java_profile:
        profile_event = str(metadata.get("profile_event") or "unknown").casefold()
        java_rows = sorted(actionable_user_rows, key=_percent, reverse=True)
        application_rows = [
            row
            for row in java_rows
            if any(
                token in str(row["name"]).casefold()
                for token in ("hotspot", "allocate", "reentrantlock", "filechannel")
            )
        ]
        dominant_java = application_rows[0] if application_rows else None
        gc_hypothesis = any(
            token in hypothesis_text
            for token in ("gc", "垃圾回收", "堆", "分配", "allocation")
        )
        lock_java_hypothesis = any(
            token in hypothesis_text
            for token in ("锁竞争", "reentrantlock", "lock contention")
        )
        wait_java_hypothesis = any(
            token in hypothesis_text
            for token in ("下游", "等待", "响应", "latency")
        )
        supported_event = (
            (profile_event == "alloc" and gc_hypothesis)
            or (profile_event == "lock" and lock_java_hypothesis)
            or (profile_event == "wall" and wait_java_hypothesis)
            or (profile_event == "cpu" and user_hypothesis)
        )
        if supported_event and dominant_java is not None:
            covered_indexes = [
                index
                for index, item in enumerate(expected)
                if isinstance(item, str)
                and any(
                    token in item.casefold()
                    for token in (
                        "jvm", "gc", "堆", "分配", "热点", "锁", "等待",
                        "profile", "allocation", "lock", "wall",
                    )
                )
            ]
            return _predicate(
                "SUPPORT",
                f"async-profiler {profile_event} profile captured Java path "
                f"{dominant_java['name']} at {_percent(dominant_java):.1f}%",
                covered_indexes,
                dominant_function=dominant_java["name"],
                dominant_percent=_percent(dominant_java),
                profile_event=profile_event,
                profile_semantics="inclusive",
            )
        return _predicate(
            "NEUTRAL",
            "Java profile contains real frames but its event/path does not support this hypothesis",
            [],
            profile_event=profile_event,
            profile_semantics="inclusive",
        )

    # Planner prose describes signal classes rather than concrete symbols.
    # Turn the Analyzer's TopN distribution into an explicit, auditable
    # predicate so high-quality data is not incorrectly left neutral.
    if gil_hypothesis:
        if (
            dominant_actionable_user
            and dominant_actionable_user_pct >= 60.0
            and 1 <= len(significant) <= 3
        ):
            return _predicate(
                "COUNTER",
                f"single dominant hotspot {dominant_actionable_user['name']} at "
                f"{dominant_actionable_user_pct:.1f}% contradicts a "
                "GIL-contention explanation",
                _criterion_text_indexes(
                    falsification,
                    ("gil", "热点", "hot", "用户态", "user"),
                ),
                dominant_function=dominant_actionable_user["name"],
                dominant_percent=dominant_actionable_user_pct,
                significant_hotspot_count=len(significant),
            )
        return _predicate(
            "NEUTRAL",
            "TopN function distribution alone does not establish GIL contention",
            [],
        )
    if user_hypothesis:
        if (
            pyspy_profile
            and 1 <= len(concentrated_source_functions) <= 3
            and len(significant_source_functions) <= 3
            and concentrated_source_pct >= 70.0
        ):
            covered_indexes = [
                index
                for index, item in enumerate(expected)
                if isinstance(item, str)
                and (
                    (
                        any(token in item.casefold() for token in (
                            "集中", "少数", "热点", "concentrat", "hot",
                        ))
                        and any(token in item.casefold() for token in (
                            "函数", "function", "样本", "sample",
                        ))
                    )
                    or (
                        _has_source_location(concentrated_source_functions[0])
                        and any(token in item.casefold() for token in (
                            "源码", "文件", "行号", "source file", "source line",
                        ))
                    )
                )
            ]
            return _predicate(
                "SUPPORT",
                f"{len(concentrated_source_functions)} source-mapped Python "
                f"function(s) account for {concentrated_source_pct:.1f}% of "
                "py-spy self samples",
                covered_indexes,
                dominant_function=concentrated_source_functions[0]["name"],
                dominant_percent=_percent(concentrated_source_functions[0]),
                concentrated_percent=concentrated_source_pct,
                concentrated_functions=[
                    {
                        "name": row["name"],
                        "percent": _percent(row),
                        "locations": row.get("locations", []),
                    }
                    for row in concentrated_source_functions
                ],
                source_mapped=True,
            )
        # Native perf TopN percentages are inclusive: every frame in a hot
        # stack can appear near 100%, so counting those rows as independent
        # hotspots incorrectly rejects a single hot leaf. The Analyzer's call
        # graph records self samples, which identify where CPU time actually
        # lands without relying on demo-specific function names.
        native_self_hotspots = [
            row
            for row in actionable_user_rows
            if _safe_percent(row.get("self_percent")) >= 20.0
        ]
        dominant_native_self = max(
            native_self_hotspots,
            key=lambda row: _safe_percent(row.get("self_percent")),
            default=None,
        )
        if (
            perf_profile
            and dominant_native_self is not None
            and _safe_percent(dominant_native_self.get("self_percent")) >= 60.0
            and not source_mapping_expected
        ):
            return _predicate(
                "SUPPORT",
                f"native perf self samples identify hotspot "
                f"{dominant_native_self['name']} at "
                f"{_safe_percent(dominant_native_self.get('self_percent')):.1f}%",
                _criterion_text_indexes(
                    expected,
                    ("热点", "hot", "函数", "function", "集中", "concentrat",
                     "样本", "sample", "用户态", "user"),
                ),
                dominant_function=dominant_native_self["name"],
                dominant_percent=_safe_percent(
                    dominant_native_self.get("self_percent")
                ),
                self_samples=dominant_native_self.get("self_samples", 0),
                profile_semantics="self",
            )
        significant_actionable = [
            row for row in actionable_user_rows if _percent(row) >= 20.0
        ]
        if (
            dominant_actionable_user
            and dominant_actionable_user_pct >= 60.0
            and 1 <= len(significant_actionable) <= 3
            # With native self weights available, a 100% ancestor/process
            # wrapper cannot replace a missing identifiable hot leaf.
            and not (perf_profile and any("self_percent" in row for row in named))
            and (
                not source_mapping_expected
                or _has_source_location(dominant_actionable_user)
            )
        ):
            return _predicate(
                "SUPPORT",
                f"dominant user-space hotspot {dominant_actionable_user['name']} accounts for "
                f"{dominant_actionable_user_pct:.1f}% with "
                f"{len(significant_actionable)} significant hotspot(s)",
                _criterion_text_indexes(
                    expected,
                    ("热点", "hot", "函数", "function", "集中", "concentrat",
                     "样本", "sample", "用户态", "user"),
                ),
                dominant_function=dominant_actionable_user["name"],
                dominant_percent=dominant_actionable_user_pct,
                significant_hotspot_count=len(significant_actionable),
            )
        if dominant_kernel and dominant_kernel_pct >= 40.0 and dominant_user_pct < 40.0:
            return _predicate(
                "COUNTER",
                f"kernel hotspot {dominant_kernel['name']} dominates at {dominant_kernel_pct:.1f}%",
                _criterion_text_indexes(
                    falsification,
                    ("内核", "kernel", "系统调用", "syscall", "中断", "interrupt"),
                ),
                dominant_function=dominant_kernel["name"],
                dominant_percent=dominant_kernel_pct,
            )

    if kernel_hypothesis:
        if dominant_kernel and dominant_kernel_pct >= 40.0:
            return _predicate(
                "SUPPORT",
                f"kernel/syscall hotspot {dominant_kernel['name']} accounts for {dominant_kernel_pct:.1f}%",
                _criterion_text_indexes(
                    expected,
                    ("内核", "kernel", "系统调用", "syscall", "中断", "interrupt"),
                ),
                dominant_function=dominant_kernel["name"],
                dominant_percent=dominant_kernel_pct,
            )
        # A DSO/process placeholder is not an attributable function. Treating
        # [libpython...] as counterproof sends the next round back to the same
        # unresolved runtime container instead of gathering independent data.
        counter_hotspot = dominant_actionable_user
        counter_percent = dominant_actionable_user_pct
        if perf_profile and any("self_percent" in row for row in named):
            counter_hotspot = max(actionable_user_rows, key=lambda row: _safe_percent(row.get("self_percent")), default=None)
            counter_percent = _safe_percent(counter_hotspot.get("self_percent")) if counter_hotspot else 0.0
        if (counter_hotspot and counter_percent >= 60.0
                and dominant_kernel_pct < 20.0):
            return _predicate(
                "COUNTER",
                f"user-space hotspot {counter_hotspot['name']} dominates while no kernel hotspot reaches 20%",
                _criterion_text_indexes(
                    falsification,
                    ("内核", "kernel", "系统调用", "syscall", "中断", "interrupt"),
                ),
                dominant_function=counter_hotspot["name"],
                dominant_percent=counter_percent,
            )

    if lock_hypothesis:
        dominant_lock = max(lock_rows, key=_percent, default=None)
        if dominant_lock and _percent(dominant_lock) >= 5.0:
            return _predicate(
                "SUPPORT",
                f"lock-related hotspot {dominant_lock['name']} accounts for {_percent(dominant_lock):.1f}%",
                _criterion_text_indexes(
                    expected,
                    ("锁", "lock", "自旋", "spin"),
                ),
                dominant_function=dominant_lock["name"],
                dominant_percent=_percent(dominant_lock),
            )
        if (
            dominant_actionable_user
            and dominant_actionable_user_pct >= 60.0
            and not lock_rows
        ):
            return _predicate(
                "COUNTER",
                "a strong non-lock user-space hotspot exists and no lock-related symbol was sampled",
                _criterion_text_indexes(
                    falsification,
                    ("锁", "lock", "自旋", "spin"),
                ),
                dominant_function=dominant_actionable_user["name"],
                dominant_percent=dominant_actionable_user_pct,
            )

    def _matches(text_entries, name):
        lowered = name.casefold()
        for entry in text_entries:
            if not isinstance(entry, str):
                continue
            tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", entry.casefold())
            for token in tokens:
                if len(token) < 3:
                    continue
                if token in lowered or lowered in token:
                    return True
        return False

    if profile_runtime and observation_contract["status"] != "SUPPORTED":
        return _invalid_numeric_observation("Unregistered profile observation cannot cover a free-text expected condition")

    for row in named:
        name = str(row["name"])
        if _is_runtime_container(name) or (
            perf_profile and "self_percent" in row and _actionable_percent(row) <= 0.0
        ):
            continue
        expected_hits = [
            index
            for index, entry in enumerate(expected)
            if isinstance(entry, str) and _matches([entry], name)
        ]
        if expected_hits:
            return {
                "outcome": "SUPPORT",
                "version": "hypothesis-predicate-v2",
                "reason": f"top function {name} matches an expected observation",
                "criterion_indexes": expected_hits,
            }
        falsification_hits = [
            index
            for index, entry in enumerate(falsification)
            if isinstance(entry, str) and _matches([entry], name)
        ]
        if falsification_hits:
            return {
                "outcome": "COUNTER",
                "version": "hypothesis-predicate-v2",
                "reason": f"top function {name} matches a falsification criterion",
                "criterion_indexes": falsification_hits,
            }
    return None


def _derive_imported_evidence_role(
    hypothesis: DropInsightHypothesisModel,
    artifact: ArtifactModel,
    assessment,
    predicate: dict | None = None,
) -> str:
    """Derive polarity from analyzer-produced predicates, never request data.

    Analyzer outputs may expose a normalized ``hypothesis_predicate``.  For
    perf TopN output we also accept the analyzer-produced top-functions list
    and compare it with the hypothesis text.  Other artifacts remain NEUTRAL
    instead of being optimistically labelled SUPPORT.
    """

    if not (assessment.schema_valid and assessment.analyzer_validated):
        return "NEUTRAL"
    metadata = artifact.meta_json or {}
    if predicate is None:
        predicate = metadata.get("hypothesis_predicate")
    if isinstance(predicate, dict):
        outcome = str(predicate.get("outcome") or "").upper()
        if outcome in {"SUPPORT", "COUNTER", "CONTROL", "NEUTRAL"}:
            return outcome

    top_functions = metadata.get("top_functions")
    if isinstance(top_functions, list):
        statement = hypothesis.statement.casefold()
        valid_rows = [row for row in top_functions if isinstance(row, dict)]
        named = [
            row for row in valid_rows
            if isinstance(row.get("name"), str) and row["name"].strip()
        ]
        if any(row["name"].casefold() in statement for row in named):
            return "SUPPORT"
        if named and max(_safe_percent(row.get("percent")) for row in named) >= 30:
            # A strong hotspot exists, but it does not substantiate this
            # particular hypothesis.  It is useful context, not counterproof.
            return "NEUTRAL"
    return "NEUTRAL"


def _safe_percent(value) -> float:
    # Aggregated valid rows can exceed 100 through inclusive frames; cap only
    # that sum. Raw observations are domain-validated before aggregation.
    number = observed_nonnegative(value)
    return min(100.0, number) if number is not None else 0.0
