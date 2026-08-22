"""Integrity-protected mature-product comparison submissions.

The public benchmark input is shared with every product.  The hidden Oracle is
only opened by the server-side evaluator after a product has returned a frozen
normalized result.  Receiving a JSON file is therefore not the same as earning
a score: without an evaluator commitment key the submission remains visibly
``RECEIVED_AWAITING_EVALUATOR``.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any
from uuid import uuid4

from scripts.real_world_benchmark import score_results

from server.app.diagnosis.real_world_runs import (
    ROOT,
    SUITE,
    _atomic_write_json,
    _canonical_hash,
    _read_json,
)


SCHEMA_VERSION = 1
_SUBMISSION_ID = re.compile(r"^cmp_[0-9a-f]{32}$")
_INPUT_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_ORACLE_KEYS = {
    "oracle",
    "oracle_root_cause_id",
    "expected_root_cause_id",
    "ground_truth",
    "golden_answer",
}

COMPARISON_PROTOCOL_VERSION = "1.0"
DEFAULT_TOOL_BUDGET = 12
DEFAULT_TIMEOUT_SECONDS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registered_comparators() -> dict[str, dict[str, Any]]:
    payload = _read_json(SUITE / "comparators.json")
    return {item["id"]: item for item in payload.get("comparators", [])}


def _assert_no_oracle_leak(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_ORACLE_KEYS:
                raise ValueError(f"提交中禁止包含评测 Oracle 字段：{path}.{key}")
            _assert_no_oracle_leak(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_oracle_leak(nested, f"{path}[{index}]")


def _validate_submission(comparator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    comparators = _registered_comparators()
    if comparator_id not in comparators:
        raise ValueError(f"未登记的成熟产品：{comparator_id}")
    if not isinstance(payload, dict):
        raise ValueError("对照结果必须是 JSON 对象")
    product = str(payload.get("product") or "").strip()
    if product != comparator_id:
        raise ValueError("结果 product 必须与所选成熟产品一致")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("结果必须包含至少一条 runs")
    if len(runs) > 7:
        raise ValueError("单次提交最多包含当前测试集的 7 个案例")
    _assert_no_oracle_leak(payload)
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError("对照结果不能超过 2 MiB")
    return deepcopy(payload)


def build_comparison_input_bundle(run: dict[str, Any]) -> dict[str, Any]:
    """Build an Oracle-free, hash-addressed input for a fair product comparison.

    A completed Mini-Drop mechanism run contains both evidence and a local
    prediction.  Competitors must only see the public incident contract and the
    frozen telemetry; exposing Mini-Drop's prediction would turn the comparison
    into answer copying.  The output contract is shared, while the hidden Oracle
    remains server-side until every product output has been frozen.
    """

    if run.get("status") != "COMPLETED":
        raise ValueError("只有已完成的真实缺陷运行才能导出同条件对照输入")
    case_id = str(run.get("case_id") or "")
    public_cases = _read_json(SUITE / "public" / "cases.json").get("cases", [])
    case = next((item for item in public_cases if item.get("case_id") == case_id), None)
    if case is None:
        raise ValueError(f"真实测试集不存在案例：{case_id}")

    evidence = []
    for item in run.get("evidence", []):
        # Ownership and integrity fields are retained so every product consumes
        # the same immutable observation.  Diagnosis result fields are omitted.
        evidence.append(deepcopy(item))

    public_input = {
        "protocol_version": COMPARISON_PROTOCOL_VERSION,
        "dataset": "mini-drop-real-world-pr-benchmark",
        "case": {
            key: deepcopy(case.get(key))
            for key in (
                "case_id",
                "source_url",
                "title",
                "project",
                "language",
                "business_scenario",
                "query",
                "observable_symptoms",
                "required_evidence",
                "workload_contract",
                "execution_track",
                "minimum_repetitions",
            )
        },
        "source_run": {
            "run_id": run.get("run_id"),
            "execution_fidelity": run.get("execution_fidelity"),
            "created_at": run.get("created_at"),
            "completed_at": run.get("updated_at"),
        },
        "frozen_telemetry": evidence,
        "fairness_budget": {
            "same_incident_window": True,
            "same_telemetry_snapshot": True,
            "llm_model": os.getenv(
                "MINI_DROP_COMPARISON_MODEL",
                "operator-must-use-the-same-model-for-all-products",
            ),
            "max_tool_calls": int(
                os.getenv("MINI_DROP_COMPARISON_MAX_TOOL_CALLS", DEFAULT_TOOL_BUDGET)
            ),
            "timeout_seconds": int(
                os.getenv("MINI_DROP_COMPARISON_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)
            ),
        },
        "required_output": {
            "predicted_root_cause_id": "string|null",
            "predicted_locations": ["path-or-symbol"],
            "evidence_refs": ["evidence_id"],
            "counter_evidence_refs": ["evidence_id"],
            "abstained": "boolean",
            "confidence": "number-in-[0,1]",
            "duration_seconds": "number|null",
            "tool_calls": "integer|null",
        },
        "oracle_policy": "The Oracle is intentionally absent and is opened only after output freezing.",
    }
    _assert_no_oracle_leak(public_input)
    return {
        **public_input,
        "input_hash": _canonical_hash(public_input),
    }


class ComparisonInputStore:
    """Registry of the exact Oracle-free inputs released for comparison.

    Comparator outputs are accepted only when they bind to an input previously
    exported by this server.  This prevents a result from another incident,
    telemetry window or budget being presented as a same-condition run.
    """

    def __init__(self, storage_dir: Path | None = None) -> None:
        configured = os.getenv("MINI_DROP_COMPARISON_INPUT_DIR")
        self._storage_dir = Path(
            storage_dir or configured or ROOT / "var" / "comparison_inputs"
        )
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def register(self, bundle: dict[str, Any]) -> dict[str, Any]:
        input_hash = str(bundle.get("input_hash") or "")
        if not _INPUT_HASH.fullmatch(input_hash):
            raise ValueError("同条件输入缺少有效 SHA-256")
        meaningful = {key: value for key, value in bundle.items() if key != "input_hash"}
        if _canonical_hash(meaningful) != input_hash:
            raise ValueError("同条件输入完整性校验失败")
        path = self._storage_dir / f"{input_hash.removeprefix('sha256:')}.json"
        with self._lock:
            _atomic_write_json(path, bundle)
        return deepcopy(bundle)

    def get(self, input_hash: str) -> dict[str, Any] | None:
        if not _INPUT_HASH.fullmatch(input_hash):
            return None
        path = self._storage_dir / f"{input_hash.removeprefix('sha256:')}.json"
        if not path.exists():
            return None
        try:
            bundle = _read_json(path)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        meaningful = {key: value for key, value in bundle.items() if key != "input_hash"}
        if bundle.get("input_hash") != input_hash or _canonical_hash(meaningful) != input_hash:
            return None
        return bundle


class ComparatorSubmissionStore:
    """Persist frozen comparator outputs and optional evaluator reports."""

    def __init__(
        self,
        storage_dir: Path | None = None,
        *,
        input_store: ComparisonInputStore | None = None,
    ) -> None:
        configured = os.getenv("MINI_DROP_COMPARATOR_RESULT_DIR")
        self._storage_dir = Path(
            storage_dir or configured or ROOT / "var" / "comparator_results"
        )
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._input_store = input_store or get_comparison_input_store()

    def submit(self, comparator_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        frozen = _validate_submission(comparator_id, payload)
        seen_cases: set[str] = set()
        bound_input_hashes: list[str] = []
        for run in frozen["runs"]:
            case_id = str(run.get("case_id") or "").strip()
            if not case_id or case_id in seen_cases:
                raise ValueError("每条对照结果必须包含唯一 case_id")
            seen_cases.add(case_id)
            comparison_input_hash = str(run.get("comparison_input_hash") or "").strip()
            bundle = self._input_store.get(comparison_input_hash)
            if bundle is None:
                raise ValueError("comparison_input_hash 未由本服务导出或完整性已失效")
            if bundle.get("case", {}).get("case_id") != case_id:
                raise ValueError("结果 case_id 与同条件输入不一致")
            source_run_id = str(run.get("source_run_id") or "").strip()
            if source_run_id != str(bundle.get("source_run", {}).get("run_id") or ""):
                raise ValueError("source_run_id 与同条件输入不一致")
            budget = bundle.get("fairness_budget", {})
            if run.get("tool_calls") is not None and int(run["tool_calls"]) > int(budget["max_tool_calls"]):
                raise ValueError("工具调用数超过同条件预算")
            if run.get("duration_seconds") is not None and float(run["duration_seconds"]) > float(budget["timeout_seconds"]):
                raise ValueError("运行时长超过同条件预算")
            evidence = deepcopy(bundle.get("frozen_telemetry", []))
            evidence_ids = {item.get("evidence_id") for item in evidence}
            for field in ("evidence_refs", "counter_evidence_refs"):
                refs = run.get(field) or []
                if any(ref not in evidence_ids for ref in refs):
                    raise ValueError(f"{field} 引用了同条件输入之外的证据")
            # Never trust a product-provided copy of telemetry.  The scorer sees
            # the immutable evidence registered by this server.
            run["evidence"] = evidence
            bound_input_hashes.append(comparison_input_hash)
        submission_id = f"cmp_{uuid4().hex}"
        created_at = _now()
        input_hash = _canonical_hash(frozen)
        key = os.getenv("MINI_DROP_REAL_WORLD_COMMITMENT_KEY", "").strip()
        report = None
        status = "RECEIVED_AWAITING_EVALUATOR"
        scoring_error = None
        if key:
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    suffix=".json",
                    delete=False,
                ) as handle:
                    json.dump(frozen, handle, ensure_ascii=False, allow_nan=False)
                    temporary_path = Path(handle.name)
                report = score_results(temporary_path, commitment_key=key.encode("utf-8"))
                status = "SCORED" if report.get("evaluated_cases", 0) else "VALIDATED_UNSCORED"
            except (TypeError, ValueError) as exc:
                status = "REJECTED_BY_EVALUATOR"
                scoring_error = str(exc)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

        record = {
            "schema_version": SCHEMA_VERSION,
            "submission_id": submission_id,
            "comparator_id": comparator_id,
            "created_at": created_at,
            "status": status,
            "submitted_cases": len(frozen["runs"]),
            "comparison_input_hashes": bound_input_hashes,
            "input_hash": input_hash,
            "scoring_error": scoring_error,
            "report": report,
        }
        envelope = {
            "record": record,
            "frozen_input": frozen,
            "integrity": {"record_hash": _canonical_hash(record)},
        }
        with self._lock:
            _atomic_write_json(self._storage_dir / f"{submission_id}.json", envelope)
        return deepcopy(record)

    def list(self) -> dict[str, Any]:
        records: list[dict[str, Any]] = []
        with self._lock:
            paths = list(self._storage_dir.glob("cmp_*.json"))
        for path in paths:
            if not _SUBMISSION_ID.fullmatch(path.stem):
                continue
            try:
                envelope = _read_json(path)
                record = envelope["record"]
                if envelope.get("integrity", {}).get("record_hash") != _canonical_hash(record):
                    continue
                records.append(record)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        records.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        latest: dict[str, dict[str, Any]] = {}
        for record in records:
            latest.setdefault(record["comparator_id"], record)
        return {
            "items": records,
            "latest_by_comparator": latest,
            "actual_submission_count": len(records),
            "scored_submission_count": sum(item["status"] == "SCORED" for item in records),
            "evaluator_ready": bool(
                os.getenv("MINI_DROP_REAL_WORLD_COMMITMENT_KEY", "").strip()
            ),
        }


_STORE: ComparatorSubmissionStore | None = None
_INPUT_STORE: ComparisonInputStore | None = None


def get_comparison_input_store() -> ComparisonInputStore:
    global _INPUT_STORE
    if _INPUT_STORE is None:
        _INPUT_STORE = ComparisonInputStore()
    return _INPUT_STORE


def get_comparator_submission_store() -> ComparatorSubmissionStore:
    global _STORE
    if _STORE is None:
        _STORE = ComparatorSubmissionStore()
    return _STORE
