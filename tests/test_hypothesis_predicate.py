from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from server.app.drop_insight.artifact_evidence import assess_artifact_evidence
from server.app.drop_insight.service import _compute_hypothesis_predicate
from server.app.drop_insight.service import _derive_imported_evidence_role
from server.app.models import DropInsightHypothesisModel
from server.app.state_machine import now_utc


def _hypothesis(expected=None, falsification=None) -> DropInsightHypothesisModel:
    return DropInsightHypothesisModel(
        id="h1",
        diagnosis_id="d1",
        statement="calculate_price 热点",
        expected_observations_json=expected or [],
        falsification_criteria_json=falsification or [],
        status="OPEN",
        created_at=now_utc(),
        updated_at=now_utc(),
    )


def test_predicate_support_when_top_function_matches_expected():
    hypothesis = _hypothesis(
        expected=["perf samples concentrate in calculate_price"],
        falsification=["CPU samples remain evenly distributed"],
    )
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "calculate_price", "percent": 75.0}]},
    )
    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["version"] == "hypothesis-predicate-v2"


def test_predicate_counter_when_top_function_matches_falsification():
    hypothesis = _hypothesis(
        expected=["readdir 出现在 top functions"],
        falsification=["calculate_price 出现在 top functions"],
    )
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "calculate_price", "percent": 80.0}]},
    )
    assert result is not None
    assert result["outcome"] == "COUNTER"


def test_predicate_none_without_claimable_signal():
    hypothesis = _hypothesis(
        expected=["perf samples concentrate in calculate_price"],
        falsification=["CPU samples remain evenly distributed"],
    )
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "readdir", "percent": 40.0}]},
    )
    assert result is None


def test_predicate_none_without_top_functions():
    hypothesis = _hypothesis(expected=["cpu usage"], falsification=["no cpu"])
    assert (
        _compute_hypothesis_predicate(
            hypothesis, {"summary": {"avg_cpu_user_pct": 85.5}}
        )
        is None
    )


def test_structured_analyzer_signal_supports_matching_hypothesis():
    hypothesis = _hypothesis(
        expected=["生产速率高于消费速率，且队列深度持续增长"],
        falsification=["生产消费速率平衡且队列没有积压"],
    )
    hypothesis.statement = "吞吐下降可能由生产速率超过消费速率并形成队列积压引起"

    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "sys_metrics_analysis.v2",
            "signals": {
                "queue_backlog": {
                    "detected": True,
                    "reason": "producer rate exceeded consumer rate",
                    "metrics": {
                        "producer_rate": 120,
                        "consumer_rate": 70,
                        "queue_lag": 50,
                    },
                }
            },
        },
    )

    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["version"] == "hypothesis-predicate-v3"
    assert result["signal"] == "queue_backlog"
    assert result["criterion_indexes"] == [0]


def test_structured_signal_does_not_support_unrelated_hypothesis():
    hypothesis = _hypothesis(
        expected=["RSS/PSS 持续增长"],
        falsification=["内存足迹保持稳定"],
    )
    hypothesis.statement = "目标进程可能存在内存增长"

    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "sys_metrics_analysis.v2",
            "signals": {
                "queue_backlog": {
                    "detected": True,
                    "reason": "producer rate exceeded consumer rate",
                    "metrics": {"queue_lag": 50},
                }
            },
        },
    )

    assert result is None


def test_downstream_latency_signal_supports_dependency_hypothesis():
    hypothesis = _hypothesis(
        expected=["下游请求量与响应延迟在同一观测窗口内同步升高"],
        falsification=["下游响应平稳且本实例存在独立热点"],
    )
    hypothesis.statement = "入口服务变慢可能由下游依赖响应延迟传播引起"

    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "sys_metrics_analysis.v2",
            "signals": {
                "downstream_latency": {
                    "detected": True,
                    "reason": "controlled downstream latency remained elevated",
                    "metrics": {
                        "average_latency_ms": 263.0,
                        "request_count_delta": 200,
                    },
                }
            },
        },
    )

    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["signal"] == "downstream_latency"
    assert result["criterion_indexes"] == [0]


def test_java_allocation_profile_supports_gc_pressure_hypothesis():
    hypothesis = _hypothesis(
        expected=["JVM 分配调用栈指向稳定的对象创建路径"],
        falsification=["分配采样没有业务路径"],
    )
    hypothesis.statement = "GC 停顿由堆内存分配压力导致"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "java_async_profile.v1",
            "profile_event": "alloc",
            "sample_count": 120,
            "top_functions": [
                {"name": "Hotspot.lambda$startWorkers$1", "percent": 75.0}
            ],
        },
    )

    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["metrics"]["profile_event"] == "alloc"
    assert "Hotspot" in result["metrics"]["dominant_function"]


def test_same_window_jvm_counters_are_independent_control_evidence():
    hypothesis = _hypothesis(
        expected=["JVM 分配调用栈指向稳定的对象创建路径"],
        falsification=["GC 计数和耗时没有增长"],
    )
    hypothesis.statement = "GC 停顿由堆内存分配压力导致"

    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "jvm_gc_metrics.v1",
            "sample_count": 2,
            "window_duration_ms": 15000,
            "delta": {
                "allocated_bytes": 64 * 1024 * 1024,
                "gc_count": 11,
                "gc_time_ms": 83,
                "heap_used_bytes": -1024,
            },
        },
    )

    assert result["outcome"] == "CONTROL"
    assert result["criterion_indexes"] == [0]
    assert result["metrics"]["gc_count_delta"] == 11




def test_predicate_supports_generic_user_space_hotspot_hypothesis():
    hypothesis = _hypothesis(
        expected=[
            "user-space samples exceed 60 percent",
            "one to three high-ratio hot functions exceed 20 percent",
            "hot function belongs to business code or a common library",
        ],
        falsification=["user-space samples are below 40 percent"],
    )
    hypothesis.statement = "CPU spike is caused by a user-space hot function"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [
            {"name": "go-hotspot", "percent": 100.0},
            {"name": "[[vdso]]", "percent": 3.1},
        ]},
    )
    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["criterion_indexes"] == [0, 1]
    assert result["metrics"]["dominant_function"] == "go-hotspot"


def test_predicate_counters_kernel_hypothesis_with_dominant_user_hotspot():
    hypothesis = _hypothesis(
        expected=["kernel samples exceed 40 percent"],
        falsification=["kernel samples are below 20 percent"],
    )
    hypothesis.statement = "CPU spike is caused by a kernel syscall path"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "go-hotspot", "percent": 100.0}]},
    )
    assert result is not None
    assert result["outcome"] == "COUNTER"


def test_predicate_counters_gil_contention_with_single_dominant_hotspot():
    hypothesis = _hypothesis(
        expected=["multiple threads compete for GIL", "samples are spread across threads"],
        falsification=["a single Python function dominates samples"],
    )
    hypothesis.statement = "CPU spike is caused by GIL contention rather than one hotspot"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "source_hot_function", "percent": 81.4}]},
    )
    assert result is not None
    assert result["outcome"] == "COUNTER"


def test_go_pprof_supports_source_mapped_application_hotspot():
    hypothesis = _hypothesis(
        expected=["pprof 样本集中在少数 Go 函数或运行时路径"],
        falsification=["Go CPU 样本分散且系统资源处于基线"],
    )
    hypothesis.statement = "目标 Go 服务可能存在 CPU 热点或 goroutine 执行路径异常"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "go_pprof_analysis.v1",
            "top_functions": [
                {
                    "name": "main.goCPUHotFunction",
                    "file": "/app/demo/go-hotspot/main.go",
                    "line": 92,
                    "percent": 84.0,
                    "samples": 463,
                },
                {
                    "name": "main.runCPUFault",
                    "file": "/app/demo/go-hotspot/main.go",
                    "line": 78,
                    "percent": 84.0,
                    "samples": 463,
                },
                {
                    "name": "crypto/sha256.block",
                    "file": "/usr/local/go/src/crypto/sha256/sha256block_amd64.s",
                    "line": 1,
                    "percent": 67.9,
                    "samples": 374,
                },
                {
                    "name": "runtime.goexit",
                    "file": "/usr/local/go/src/runtime/asm_amd64.s",
                    "line": 1700,
                    "percent": 84.0,
                    "samples": 463,
                },
            ],
        },
    )

    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["metrics"]["dominant_function"] == "main.goCPUHotFunction"
    assert result["metrics"]["profile_semantics"] == "inclusive"


def test_go_pprof_does_not_match_cpu_word_inside_a_symbol_as_counterevidence():
    hypothesis = _hypothesis(
        expected=["等待路径应在 pprof 中可见"],
        falsification=["CPU 热点函数不应出现"],
    )
    hypothesis.statement = "检查 Go 等待路径"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "go_pprof_analysis.v1",
            "top_functions": [
                {
                    "name": "main.goCPUHotFunction",
                    "file": "/app/main.go",
                    "line": 92,
                    "percent": 84.0,
                }
            ],
        },
    )

    assert result is not None
    assert result["outcome"] == "NEUTRAL"


def test_predicate_aggregates_source_lines_for_one_hot_function():
    hypothesis = _hypothesis(
        expected=["py-spy samples concentrate in one Python hotspot function"],
        falsification=["Python samples are evenly distributed"],
    )
    hypothesis.statement = "CPU 飙高由单个 Python 热点函数集中占用导致"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [
            {"name": "source_hot_function", "file": "/app/app.py", "line": 139, "percent": 40.7},
            {"name": "source_hot_function", "file": "/app/app.py", "line": 141, "percent": 24.7},
            {"name": "source_hot_function", "file": "/app/app.py", "line": 140, "percent": 9.6},
            {"name": "source_hot_function", "file": "/app/app.py", "line": 138, "percent": 5.2},
            {"name": "_sample", "file": "/app/app.py", "line": 224, "percent": 19.5},
        ]},
    )
    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["metrics"]["dominant_function"] == "source_hot_function"
    assert result["metrics"]["dominant_percent"] == 80.2


def test_predicate_recognizes_model_wording_about_concentrated_python_functions():
    hypothesis = _hypothesis(
        expected=["py-spy 样本集中在少数（1-3 个）Python 函数"],
        falsification=["Python 栈样本均匀分布"],
    )
    hypothesis.statement = "进程存在少数 Python 函数集中占用 CPU，形成明显热点"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [
            {"name": "source_hot_function", "line": 139, "percent": 55.0},
            {"name": "source_hot_function", "line": 141, "percent": 25.0},
            {"name": "sampler", "line": 200, "percent": 20.0},
        ]},
    )
    assert result is not None and result["outcome"] == "SUPPORT"


def test_predicate_recognizes_python_functions_with_concentration_word_order():
    hypothesis = _hypothesis(
        expected=["前几个函数累计占比显著（如 >50%）"],
        falsification=["样本在所有函数间均匀分布"],
    )
    hypothesis.statement = "CPU 时间集中在少数 Python 函数，形成明显热点栈"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [
            {"name": "source_hot_function", "line": 139, "percent": 41.4},
            {"name": "source_hot_function", "line": 141, "percent": 25.0},
            {"name": "source_hot_function", "line": 140, "percent": 10.7},
            {"name": "source_hot_function", "line": 138, "percent": 5.7},
            {"name": "sampler", "percent": 16.9},
        ]},
    )
    assert result is not None and result["outcome"] == "SUPPORT"


def test_pyspy_predicate_uses_cumulative_source_function_concentration():
    hypothesis = _hypothesis(
        expected=[
            "py-spy 采样样本集中在少数几个 Python 函数",
            "存在单个或少数线程占用大部分 CPU 时间",
            "热点函数可映射到具体源码文件与行号",
        ],
        falsification=[
            "py-spy 样本在大量 Python 函数间均匀分布",
            "所有线程 CPU 占用均低且无明显热点",
            "采样期间进程几乎无 CPU 活动",
        ],
    )
    hypothesis.statement = (
        "python-hotspot 进程存在用户态 CPU 热点，"
        "py-spy 样本集中在少数 Python 函数"
    )
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "pyspy_analysis.v1",
            "sample_count": 2973,
            "top_functions": [
                {"name": "_sample", "file": "/app/app.py", "line": 248, "percent": 49.8},
                {"name": "source_hot_function", "file": "/app/app.py", "line": 165, "percent": 32.3},
                {"name": "source_hot_function", "file": "/app/app.py", "line": 166, "percent": 8.2},
                {"name": "source_hot_function", "file": "/app/app.py", "line": 164, "percent": 4.9},
                {"name": "source_hot_function", "file": "/app/app.py", "line": 167, "percent": 4.4},
                {"name": "noise", "file": "/app/app.py", "line": 20, "percent": 0.4},
            ],
        },
    )

    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["criterion_indexes"] == [0, 2]
    assert result["metrics"]["dominant_function"] == "source_hot_function"
    assert result["metrics"]["concentrated_percent"] == 99.6
    assert {row["name"] for row in result["metrics"]["concentrated_functions"]} == {
        "_sample",
        "source_hot_function",
    }


def test_pyspy_concentration_predicate_is_not_function_name_specific():
    hypothesis = _hypothesis(
        expected=[
            "py-spy 样本集中在少数 Python 函数",
            "热点函数映射到源码文件和行号",
        ],
        falsification=["样本在大量函数间均匀分布"],
    )
    hypothesis.statement = "用户态 CPU 时间集中在少数 Python 热点函数"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "pyspy_analysis.v1",
            "top_functions": [
                {"name": "parse_payload", "file": "/srv/worker.py", "line": 81, "percent": 44.0},
                {"name": "dispatch_request", "file": "/srv/worker.py", "line": 120, "percent": 39.0},
                {"name": "poll", "file": "/srv/worker.py", "line": 12, "percent": 17.0},
            ],
        },
    )

    assert result is not None and result["outcome"] == "SUPPORT"
    assert result["metrics"]["concentrated_percent"] == 100.0
    assert result["metrics"]["dominant_function"] == "parse_payload"


def test_perf_runtime_container_is_not_business_source_hotspot():
    hypothesis = _hypothesis(
        expected=[
            "用户态样本集中在少数 Python 业务函数",
            "热点函数可映射到源码文件与行号",
        ],
        falsification=["内核态等待路径占主导"],
    )
    hypothesis.statement = "业务源码中的少数 Python 函数形成 CPU 热点"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "perf_analysis.v1",
            "top_functions": [
                {"name": "[libpython3.12.so.1.0]", "percent": 100.0},
                {"name": "[libc.so.6]", "percent": 100.0},
                {"name": "python", "percent": 100.0},
            ],
        },
    )

    assert result is None


def test_native_perf_uses_leaf_self_samples_for_disjunctive_cpu_hotspot():
    hypothesis = _hypothesis(
        expected=["perf 样本集中在少数热点函数或内核调用链"],
        falsification=["CPU 样本均匀且没有显著热点"],
    )
    hypothesis.statement = "目标进程可能存在 CPU 热点函数或系统调用开销"

    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "perf_analysis.v1",
            "sample_count": 943,
            "top_functions": [
                {
                    "name": "cpp-service",
                    "samples": 943,
                    "percent": 100.0,
                    "self_samples": 0,
                    "self_percent": 0.0,
                },
                {
                    "name": "execute_native_thread_routine",
                    "samples": 941,
                    "percent": 99.8,
                    "self_samples": 0,
                    "self_percent": 0.0,
                },
                {
                    "name": "std::thread::_State_impl",
                    "samples": 941,
                    "percent": 99.8,
                    "self_samples": 0,
                    "self_percent": 0.0,
                },
                {
                    "name": "calculate_batch",
                    "samples": 941,
                    "percent": 99.8,
                    "self_samples": 941,
                    "self_percent": 99.8,
                },
            ],
        },
    )

    assert result is not None and result["outcome"] == "SUPPORT"
    assert result["metrics"]["dominant_function"] == "calculate_batch"
    assert result["metrics"]["profile_semantics"] == "self"


def test_pyspy_even_distribution_across_many_source_functions_stays_neutral():
    hypothesis = _hypothesis(
        expected=["py-spy 样本集中在一到三个 Python 函数"],
        falsification=["样本在大量 Python 函数间均匀分布"],
    )
    hypothesis.statement = "少数 Python 业务函数形成用户态 CPU 热点"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "pyspy_analysis.v1",
            "top_functions": [
                {
                    "name": f"worker_{index}",
                    "file": "/srv/worker.py",
                    "line": index,
                    "percent": 25.0,
                }
                for index in range(1, 5)
            ],
        },
    )

    assert result is None


def test_pyspy_analyzed_artifacts_accept_evidence_derived_predicate():
    hypothesis = _hypothesis(
        expected=[
            "py-spy 样本集中在少数 Python 函数",
            "热点函数可映射到源码文件与行号",
        ],
        falsification=["样本在大量 Python 函数间均匀分布"],
    )
    hypothesis.statement = "用户态 CPU 热点集中在少数 Python 业务函数"
    metadata = {
        "schema_version": "pyspy_analysis.v1",
        "sample_count": 200,
        "top_functions": [
            {"name": "decode", "file": "/srv/app.py", "line": 20, "percent": 46.0},
            {"name": "transform", "file": "/srv/app.py", "line": 41, "percent": 42.0},
            {"name": "poll", "file": "/srv/app.py", "line": 8, "percent": 12.0},
        ],
    }
    predicate = _compute_hypothesis_predicate(hypothesis, metadata)

    assert predicate is not None and predicate["outcome"] == "SUPPORT"
    for artifact_type in ("flamegraph_json", "top_json"):
        assessment = assess_artifact_evidence(
            "pyspy",
            artifact_type,
            metadata,
            analyzer_validated=True,
        )
        assert assessment.schema_valid is True
        assert assessment.sample_count_known is True
        assert assessment.sample_count >= assessment.minimum_samples
        artifact = SimpleNamespace(meta_json=metadata)
        assert _derive_imported_evidence_role(
            hypothesis,
            artifact,
            assessment,
            predicate=predicate,
        ) == "SUPPORT"


def test_gil_causal_claim_is_not_misread_as_hotspot_support():
    hypothesis = _hypothesis(
        expected=["多个线程竞争 GIL"],
        falsification=["单一热点函数占据多数样本"],
    )
    hypothesis.statement = "CPU 消耗由 GIL 竞争导致，而非单一热点函数"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {"top_functions": [{"name": "source_hot_function", "percent": 82.0}]},
    )
    assert result is not None and result["outcome"] == "COUNTER"


def test_disjunctive_hotspot_or_gil_candidate_accepts_real_pyspy_hotspot():
    hypothesis = _hypothesis(
        expected=[
            "py-spy 样本集中在少数 Python 函数",
            "热点函数可映射到源码文件与行号",
        ],
        falsification=["样本在大量 Python 函数间均匀分布"],
    )
    # This is wording emitted by the real cloud planner.  It is a compound
    # candidate, not a claim that GIL contention alone is the root cause.
    hypothesis.statement = "目标 Python 进程可能存在用户态热点函数或 GIL 竞争"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "pyspy_analysis.v1",
            "sample_count": 2973,
            "top_functions": [
                {
                    "name": "_sample",
                    "file": "/app/app.py",
                    "line": 248,
                    "percent": 49.8,
                },
                {
                    "name": "source_hot_function",
                    "file": "/app/app.py",
                    "line": 165,
                    "percent": 32.3,
                },
                {
                    "name": "source_hot_function",
                    "file": "/app/app.py",
                    "line": 166,
                    "percent": 8.2,
                },
                {
                    "name": "source_hot_function",
                    "file": "/app/app.py",
                    "line": 164,
                    "percent": 4.9,
                },
                {
                    "name": "source_hot_function",
                    "file": "/app/app.py",
                    "line": 167,
                    "percent": 4.4,
                },
            ],
        },
    )

    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["metrics"]["concentrated_percent"] == 99.6


def test_predicate_supports_redacted_database_lock_evidence():
    hypothesis = _hypothesis(
        expected=["存在等待会话", "存在阻塞会话", "锁等待持续发生"],
        falsification=["没有数据库锁等待"],
    )
    hypothesis.statement = "数据库请求变慢由锁等待和阻塞会话导致"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "database_lock.v1",
            "sample_count": 5,
            "lock_wait_count": 2,
            "blocker_count": 1,
            "blocking_edge_count": 2,
            "lock_wait_ms": 1840.5,
        },
    )
    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["metrics"]["lock_wait_count"] == 2
    assert result["metrics"]["blocker_count"] == 1
    assert result["metrics"]["blocking_edge_count"] == 2
    assert result["criterion_indexes"] == [0, 1, 2]


def test_predicate_counters_database_lock_hypothesis_without_waiters():
    hypothesis = _hypothesis(
        expected=["存在等待会话"],
        falsification=["没有数据库锁等待"],
    )
    hypothesis.statement = "数据库请求变慢由锁等待导致"
    result = _compute_hypothesis_predicate(
        hypothesis,
        {
            "schema_version": "database_lock.v1",
            "sample_count": 5,
            "lock_wait_count": 0,
            "blocker_count": 0,
            "lock_wait_ms": 0,
        },
    )
    assert result is not None
    assert result["outcome"] == "COUNTER"
