from __future__ import annotations

from datetime import datetime, timezone

from server.app.drop_insight.service import _compute_hypothesis_predicate
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
            "lock_wait_ms": 1840.5,
        },
    )
    assert result is not None
    assert result["outcome"] == "SUPPORT"
    assert result["metrics"]["lock_wait_count"] == 2
    assert result["metrics"]["blocker_count"] == 1


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
