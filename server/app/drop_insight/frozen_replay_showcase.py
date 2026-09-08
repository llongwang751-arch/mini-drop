"""Durable bridge from allow-listed frozen LATS fixtures to diagnosis sessions.

This module is intentionally separate from the live Fault Plaza.  A replay run
never invokes a collector or a shell command and never creates Task, Artifact,
ToolCall, Evidence or Report rows.  Its only durable facts are the immutable
fixture manifest, ordinary ``lats.*`` events and the hypothesis tree projected
from those events.  The existing diagnosis outbox therefore makes every frame
visible over SSE without pretending that cached observations are live evidence.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

from sqlalchemy.exc import IntegrityError

from server.app.database import new_session
from server.app.models import (
    DropInsightEventModel,
    DropInsightHypothesisModel,
    DropInsightSessionModel,
)

from .lats import LATSConfig, FrozenReplayObservationProvider, run_frozen_replay_lats
from .service import _append_event, _cas_session_update


SNAPSHOT_EVENT = "lats.replay_snapshot_frozen"
SHOWCASE_KIND = "FROZEN_LATS_SHOWCASE"
ACTIVE_STATUSES = ("PLANNING", "HYPOTHESIZING", "COLLECTING_EVIDENCE")


class FrozenReplayShowcaseNotFound(ValueError):
    """Raised when a caller names anything outside the server allow-list."""


class FrozenReplayManifestError(ValueError):
    """Raised when a persisted immutable replay manifest fails validation."""


def _python_hotspot_manifest() -> dict[str, Any]:
    """Return a fresh JSON-owned copy of the curated, deterministic fixture."""

    return {
        "format_version": "mini-drop-frozen-lats-v1",
        "scenario_id": "python-hotspot-tree-v1",
        "snapshot_id": "python-hotspot-tree-snapshot-v1",
        "title": "Python CPU 热点：从系统基线回溯到源码函数",
        "query": (
            "对冻结的 python-hotspot 演示快照执行完整 LATS：排除 I/O 等待和"
            "噪声邻居，定位到 Python 源码级 CPU 热点。"
        ),
        "service": "python-hotspot",
        "environment": "frozen-replay",
        "captured_window": {
            "start": "2026-09-06T00:15:00Z",
            "end": "2026-09-06T00:20:00Z",
            "timezone": "UTC",
        },
        "provenance": {
            "kind": "CONTROLLED_REPLAY_FIXTURE",
            "producer": "Mini-Drop deterministic showcase fixture",
            "live_collection": False,
            "mutable_runtime": False,
            "claim": (
                "这些观测仅用于可重复算法演示；不是本次会话现场采集的生产证据。"
            ),
        },
        "config": {
            "top_k": 3,
            "max_iterations": 6,
            "max_simulations": 6,
            "selection_policy": "UCT",
            "exploration_constant": 1.4142135623730951,
            "value_lambda": 0.5,
        },
        "initial_candidates": [
            {
                "statement": "请求变慢主要由块设备 I/O 等待造成",
                "expected_observations": ["iowait 与块设备延迟同时升高"],
                "falsification_criteria": ["iowait 低且块设备延迟稳定"],
                "reason": "先验证常见的系统级等待路径。",
                "observation_key": "root-io-wait",
                "estimated_value": 0.72,
            },
            {
                "statement": "python-hotspot 进程存在用户态 CPU 热点",
                "expected_observations": ["目标 PID 的用户态 CPU 与采样栈集中"],
                "falsification_criteria": ["目标 PID CPU 正常且采样栈分散"],
                "reason": "系统基线指向计算资源，需要继续下钻运行时。",
                "observation_key": "root-python-cpu",
                "estimated_value": 0.83,
            },
            {
                "statement": "同宿主机噪声邻居抢占 CPU 导致目标服务变慢",
                "expected_observations": ["宿主机饱和但目标进程没有主导热点"],
                "falsification_criteria": ["目标进程自身占用 CPU 且存在集中调用栈"],
                "reason": "显式保留资源争抢这一替代解释。",
                "observation_key": "root-noisy-neighbor",
                "estimated_value": 0.61,
            },
        ],
        "observations": {
            "root-io-wait": {
                "summary": "冻结系统基线显示 iowait 仅 0.6%，块设备 p95 延迟稳定。",
                "verification_status": "FALSIFIED",
                "confidence": 0.91,
                "support_count": 0,
                "counter_count": 2,
                "counter_evidence_refs": [
                    "replay-observation:sysstat-iowait-low",
                    "replay-observation:block-latency-stable",
                ],
            },
            "root-python-cpu": {
                "summary": (
                    "冻结系统基线确认目标 PID 用户态 CPU 达 87%，但尚不足以定位源码函数。"
                ),
                "verification_status": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.58,
                "support_count": 1,
                "counter_count": 0,
                "evidence_refs": ["replay-observation:target-pid-user-cpu"],
                "next_candidates": [
                    {
                        "statement": "业务函数 source_hot_function 是主要 CPU 热点",
                        "expected_observations": [
                            "冻结 Python 栈样本集中于 source_hot_function"
                        ],
                        "falsification_criteria": [
                            "热点落在其他函数或采样分布没有显著集中"
                        ],
                        "reason": "上一轮确认进程级 CPU 异常，反思后下钻到源码函数。",
                        "observation_key": "leaf-source-hot-function",
                        "estimated_value": 0.94,
                    },
                    {
                        "statement": "Python GC 频繁运行是主要 CPU 热点",
                        "expected_observations": ["GC 帧与回收计数占主要比例"],
                        "falsification_criteria": ["GC 帧占比低且回收计数稳定"],
                        "reason": "保留运行时 GC 作为可证伪替代分支。",
                        "observation_key": "leaf-python-gc",
                        "estimated_value": 0.45,
                    },
                ],
            },
            "root-noisy-neighbor": {
                "summary": "冻结快照未发现同机 peer CPU ticks 增长，目标 PID 自身占用主导。",
                "verification_status": "FALSIFIED",
                "confidence": 0.88,
                "support_count": 0,
                "counter_count": 2,
                "counter_evidence_refs": [
                    "replay-observation:peer-cpu-flat",
                    "replay-observation:target-cpu-dominant",
                ],
            },
            "leaf-source-hot-function": {
                "summary": (
                    "冻结 Python 栈中 source_hot_function 占 78.4%，并可映射到 app.py。"
                ),
                "verification_status": "VERIFIED",
                "confidence": 0.94,
                "support_count": 2,
                "counter_count": 0,
                "evidence_refs": [
                    "replay-observation:pyspy-stack-share",
                    "replay-observation:source-map-app-py",
                ],
            },
            "leaf-python-gc": {
                "summary": "冻结 Python 栈中的 GC 帧占比不足 1%，回收计数没有异常突增。",
                "verification_status": "FALSIFIED",
                "confidence": 0.86,
                "support_count": 0,
                "counter_count": 2,
                "counter_evidence_refs": [
                    "replay-observation:gc-frame-share-low",
                    "replay-observation:gc-counter-stable",
                ],
            },
        },
    }


_SCENARIO_FACTORIES = {"python-hotspot-tree-v1": _python_hotspot_manifest}


def _canonical_digest(value: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _load_allowlisted_manifest(scenario_id: str) -> dict[str, Any]:
    factory = _SCENARIO_FACTORIES.get(str(scenario_id or ""))
    if factory is None:
        raise FrozenReplayShowcaseNotFound("frozen LATS replay scenario not found")
    # JSON round-tripping makes the returned object independent from the
    # process-level catalog and proves it can be persisted as one snapshot.
    return json.loads(json.dumps(factory(), ensure_ascii=False))


def _provider_for_manifest(manifest: Mapping[str, Any]) -> FrozenReplayObservationProvider:
    observations = manifest.get("observations")
    if not isinstance(observations, Mapping):
        raise FrozenReplayManifestError("frozen replay observations must be an object")
    return FrozenReplayObservationProvider(
        snapshot_id=str(manifest.get("snapshot_id") or ""),
        observations=observations,
    )


def get_frozen_replay_catalog() -> dict[str, Any]:
    scenarios = []
    for scenario_id, factory in _SCENARIO_FACTORIES.items():
        manifest = factory()
        provider = _provider_for_manifest(manifest)
        result = run_frozen_replay_lats(
            manifest["initial_candidates"],
            provider,
            config=_config_for_manifest(manifest),
            node_namespace=f"catalog:{scenario_id}",
        )
        scenarios.append(
            {
                "scenario_id": scenario_id,
                "title": manifest["title"],
                "description": manifest["query"],
                "service": manifest["service"],
                "environment": manifest["environment"],
                "algorithm": "LATS-UCT",
                "selection_policy": str(manifest["config"]["selection_policy"]),
                "execution_mode": "FULL_LATS",
                "environment_semantics": "FROZEN_REPLAY",
                "estimated_frames": len(_event_frames(result["events"])),
                "snapshot": {
                    "snapshot_id": provider.snapshot_id,
                    "snapshot_digest": provider.snapshot_digest,
                    "manifest_digest": _canonical_digest(manifest),
                    "immutable": True,
                },
                "safety": {
                    "allow_listed": True,
                    "live_collection": False,
                    "creates_operational_tasks": False,
                },
                "truth_boundary": {
                    "observation_source": "FROZEN_REPLAY_PROVIDER",
                    "live_collection": False,
                    "creates_evidence_rows": False,
                    "creates_reports": False,
                    "label": "受控冻结观测，不是本次现场采集证据",
                },
            }
        )
    return {
        "status": "READY",
        "showcase_kind": SHOWCASE_KIND,
        "scenarios": scenarios,
    }


def start_frozen_replay_run(
    scenario_id: str,
    client_run_id: str,
    *,
    principal: str,
) -> dict[str, Any]:
    """Create or return one idempotent, restart-safe replay diagnosis."""

    manifest = _load_allowlisted_manifest(scenario_id)
    provider = _provider_for_manifest(manifest)
    manifest_digest = _canonical_digest(manifest)
    normalized_principal = str(principal or "local-anonymous").strip() or "local-anonymous"
    identity = hashlib.sha256(
        f"{normalized_principal}\0{scenario_id}\0{client_run_id}".encode("utf-8")
    ).hexdigest()
    diagnosis_id = f"insight_replay_{identity[:40]}"
    timestamp = datetime.now(UTC)

    session = new_session()
    try:
        existing = session.get(DropInsightSessionModel, diagnosis_id)
        if existing is not None:
            return _start_response(existing, scenario_id=scenario_id, created=False)
        window = deepcopy(manifest["captured_window"])
        budget = {
            "max_duration_seconds": 120,
            "max_tool_calls": 1,
            "max_diagnosis_rounds": int(manifest["config"]["max_iterations"]),
            "max_concurrent_tasks": 1,
            "max_hosts": 1,
            "max_artifact_bytes": 1,
            "max_risk_level": "R0",
            "lats_top_k": int(manifest["config"]["top_k"]),
            "max_lats_iterations": int(manifest["config"]["max_iterations"]),
            "lats_exploration_constant": float(
                manifest["config"]["exploration_constant"]
            ),
            "lats_selection_policy": str(manifest["config"]["selection_policy"]),
            "lats_value_lambda": float(manifest["config"]["value_lambda"]),
            "max_simulations": int(manifest["config"]["max_simulations"]),
        }
        diagnosis = DropInsightSessionModel(
            id=diagnosis_id,
            query=str(manifest["query"]),
            target_json={
                "kind": SHOWCASE_KIND,
                "scenario_id": scenario_id,
                "service": manifest["service"],
                "environment": manifest["environment"],
                "snapshot_id": provider.snapshot_id,
                "snapshot_digest": provider.snapshot_digest,
                "manifest_digest": manifest_digest,
            },
            time_range_json=window,
            requested_time_range_json=window,
            effective_time_range_json=window,
            mode="REPLAY",
            skill_policy="DISABLED",
            budget_json=budget,
            status="PLANNING",
            version=1,
            clarification_questions_json=[],
            created_at=timestamp,
            updated_at=timestamp,
        )
        session.add(diagnosis)
        session.flush()
        _append_event(
            session,
            diagnosis_id,
            SNAPSHOT_EVENT,
            "SYSTEM",
            {
                "scenario_id": scenario_id,
                "client_run_id": client_run_id,
                "manifest": manifest,
                "manifest_digest": manifest_digest,
                "snapshot_id": provider.snapshot_id,
                "snapshot_digest": provider.snapshot_digest,
                "immutable": True,
                "live_collection": False,
            },
            timestamp,
            effect_key="frozen-replay:snapshot",
        )
        session.commit()
        session.refresh(diagnosis)
        return _start_response(diagnosis, scenario_id=scenario_id, created=True)
    except IntegrityError:
        # The deterministic primary key is also the cross-process idempotency
        # boundary.  A concurrent creator may win; return its durable session.
        session.rollback()
        existing = session.get(DropInsightSessionModel, diagnosis_id)
        if existing is None:
            raise
        return _start_response(existing, scenario_id=scenario_id, created=False)
    finally:
        session.close()


def advance_frozen_replay_showcases(*, limit: int = 32) -> int:
    """Advance at most one visible frame for each active replay session."""

    with new_session() as session:
        rows = (
            session.query(DropInsightSessionModel)
            .filter(
                DropInsightSessionModel.mode == "REPLAY",
                DropInsightSessionModel.deleted_at.is_(None),
                DropInsightSessionModel.status.in_(ACTIVE_STATUSES),
            )
            .order_by(DropInsightSessionModel.created_at.asc())
            .limit(max(1, min(int(limit), 256)))
            .all()
        )
        diagnosis_ids = [
            row.id
            for row in rows
            if (row.target_json or {}).get("kind") == SHOWCASE_KIND
        ]

    advanced = 0
    for diagnosis_id in diagnosis_ids:
        advanced += int(advance_frozen_replay_run(diagnosis_id))
    return advanced


def advance_frozen_replay_run(diagnosis_id: str) -> bool:
    """Persist one atomic LATS frame, resuming solely from the frozen event."""

    session = new_session()
    try:
        diagnosis = (
            session.query(DropInsightSessionModel)
            .filter(DropInsightSessionModel.id == diagnosis_id)
            .with_for_update()
            .first()
        )
        if (
            diagnosis is None
            or diagnosis.mode != "REPLAY"
            or diagnosis.status not in ACTIVE_STATUSES
            or (diagnosis.target_json or {}).get("kind") != SHOWCASE_KIND
        ):
            return False
        snapshot_event = (
            session.query(DropInsightEventModel)
            .filter(
                DropInsightEventModel.diagnosis_id == diagnosis_id,
                DropInsightEventModel.event_type == SNAPSHOT_EVENT,
            )
            .order_by(DropInsightEventModel.sequence.asc())
            .first()
        )
        if snapshot_event is None:
            return _terminate_integrity_failure(
                session, diagnosis, "persisted frozen replay snapshot is missing"
            )
        payload = snapshot_event.payload_json or {}
        try:
            manifest = _validated_persisted_manifest(diagnosis, payload)
            provider = _provider_for_manifest(manifest)
            result = run_frozen_replay_lats(
                manifest["initial_candidates"],
                provider,
                config=_config_for_manifest(manifest),
                node_namespace=diagnosis_id,
            )
        except (KeyError, TypeError, ValueError) as exc:
            return _terminate_integrity_failure(session, diagnosis, str(exc))

        frames = _event_frames(result["events"])
        persisted_effects = {
            value
            for (value,) in (
                session.query(DropInsightEventModel.effect_key)
                .filter(
                    DropInsightEventModel.diagnosis_id == diagnosis_id,
                    DropInsightEventModel.effect_key.is_not(None),
                )
                .all()
            )
            if value
        }
        frame = next(
            (
                candidate
                for candidate in frames
                if any(event.get("effect_key") not in persisted_effects for event in candidate)
            ),
            None,
        )
        if frame is None:
            if diagnosis.status != "COMPLETED":
                _complete_session(session, diagnosis, datetime.now(UTC))
                session.commit()
                return True
            return False

        timestamp = datetime.now(UTC)
        for event in frame:
            event_payload = deepcopy(dict(event.get("payload") or {}))
            _separate_replay_simulation_budget(event["event_type"], event_payload)
            _materialize_hypothesis_effect(session, diagnosis, event["event_type"], event_payload, timestamp)
            _append_event(
                session,
                diagnosis_id,
                str(event["event_type"]),
                str(event.get("actor") or "SYSTEM"),
                event_payload,
                timestamp,
                effect_key=str(event["effect_key"]),
            )

        if any(event["event_type"] == "lats.search_terminated" for event in frame):
            _complete_session(session, diagnosis, timestamp)
        elif diagnosis.status == "PLANNING":
            _cas_session_update(
                session, diagnosis, status="HYPOTHESIZING", timestamp=timestamp
            )
        elif diagnosis.status == "HYPOTHESIZING":
            _cas_session_update(
                session, diagnosis, status="COLLECTING_EVIDENCE", timestamp=timestamp
            )
        else:
            _cas_session_update(
                session, diagnosis, status="COLLECTING_EVIDENCE", timestamp=timestamp
            )
        session.commit()
        return True
    finally:
        session.close()


def _validated_persisted_manifest(
    diagnosis: DropInsightSessionModel, payload: Mapping[str, Any]
) -> dict[str, Any]:
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping):
        raise FrozenReplayManifestError("persisted frozen replay manifest is invalid")
    owned = json.loads(json.dumps(dict(manifest), ensure_ascii=False))
    digest = _canonical_digest(owned)
    target = diagnosis.target_json or {}
    if digest != payload.get("manifest_digest") or digest != target.get("manifest_digest"):
        raise FrozenReplayManifestError("persisted frozen replay manifest digest mismatch")
    if owned.get("scenario_id") != target.get("scenario_id"):
        raise FrozenReplayManifestError("persisted replay scenario identity mismatch")
    if owned.get("format_version") != "mini-drop-frozen-lats-v1":
        raise FrozenReplayManifestError("unsupported frozen replay manifest version")
    initial = owned.get("initial_candidates")
    if not isinstance(initial, list) or not initial:
        raise FrozenReplayManifestError("frozen replay candidates must be a non-empty array")
    provider = _provider_for_manifest(owned)
    if (
        provider.snapshot_digest != payload.get("snapshot_digest")
        or provider.snapshot_digest != target.get("snapshot_digest")
    ):
        raise FrozenReplayManifestError("persisted frozen replay snapshot digest mismatch")
    return owned


def _config_for_manifest(manifest: Mapping[str, Any]) -> LATSConfig:
    raw = manifest.get("config")
    if not isinstance(raw, Mapping):
        raise FrozenReplayManifestError("frozen replay config must be an object")
    return LATSConfig(
        top_k=max(1, min(int(raw.get("top_k", 3)), 8)),
        exploration_constant=max(
            0.0, min(float(raw.get("exploration_constant", 1.4142135623730951)), 8.0)
        ),
        max_iterations=max(1, min(int(raw.get("max_iterations", 6)), 12)),
        # The pure runner historically names its rollout bound max_tool_calls;
        # the bridge exposes and persists it only as a simulation budget.
        max_tool_calls=max(1, min(int(raw.get("max_simulations", 6)), 12)),
        selection_policy=str(raw.get("selection_policy") or "UCT").upper(),
        value_lambda=max(0.0, min(float(raw.get("value_lambda", 0.5)), 1.0)),
    )


def _event_frames(events: Sequence[Mapping[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group deterministic runner output into initial + one-rollout frames."""

    frames: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_iteration: int | None = None
    for raw_event in events:
        event = deepcopy(dict(raw_event))
        payload = event.get("payload") or {}
        if event.get("event_type") == "lats.node_selected":
            iteration = int(payload.get("iteration") or 0)
            if current and current_iteration is not None and iteration != current_iteration:
                frames.append(current)
                current = []
            if current_iteration is None and current:
                frames.append(current)
                current = []
            current_iteration = iteration
        current.append(event)
    if current:
        frames.append(current)
    return frames


def _materialize_hypothesis_effect(
    session,
    diagnosis: DropInsightSessionModel,
    event_type: str,
    payload: Mapping[str, Any],
    timestamp: datetime,
) -> None:
    if event_type == "lats.candidates_expanded":
        for candidate in payload.get("candidates") or []:
            if not isinstance(candidate, Mapping):
                continue
            raw_id = _raw_hypothesis_id(candidate.get("node_id"))
            if not raw_id or session.get(DropInsightHypothesisModel, raw_id) is not None:
                continue
            parent_id = _raw_hypothesis_id(candidate.get("parent_node_id"))
            reason = str(candidate.get("reason") or "冻结回放的 LATS 候选扩展")
            session.add(
                DropInsightHypothesisModel(
                    id=raw_id,
                    diagnosis_id=diagnosis.id,
                    statement=str(candidate.get("statement") or "冻结回放候选假设"),
                    expected_observations_json=list(
                        candidate.get("expected_observations") or []
                    ),
                    falsification_criteria_json=list(
                        candidate.get("falsification_criteria") or []
                    ),
                    status="OPEN",
                    source="FROZEN_REPLAY",
                    round_index=max(1, int(candidate.get("depth") or 0) + 1),
                    parent_hypothesis_id=parent_id,
                    generation_reason=(
                        f"{reason}（受控冻结快照；非本次现场采集证据）"
                    ),
                    effect_key=f"frozen-replay:hypothesis:{raw_id}",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        session.flush()
        return

    if event_type == "lats.backpropagated":
        model = session.get(
            DropInsightHypothesisModel, _raw_hypothesis_id(payload.get("node_id"))
        )
        if model is not None:
            outcome = str(payload.get("outcome") or "").upper()
            if outcome == "FALSIFIED":
                model.status = "FALSIFIED"
            elif outcome in {"VERIFIED", "PARTIAL_WITHOUT_COUNTER"}:
                model.status = "SUPPORTED"
            else:
                model.status = "INCONCLUSIVE"
            model.updated_at = timestamp
        return

    if event_type == "lats.node_pruned":
        model = session.get(
            DropInsightHypothesisModel, _raw_hypothesis_id(payload.get("node_id"))
        )
        if model is not None:
            model.status = "FALSIFIED"
            model.updated_at = timestamp


def _raw_hypothesis_id(value: Any) -> str | None:
    text = str(value or "")
    prefix = "hypothesis:"
    return text[len(prefix) :] if text.startswith(prefix) and len(text) > len(prefix) else None


def _separate_replay_simulation_budget(event_type: str, payload: dict[str, Any]) -> None:
    if event_type != "lats.search_started":
        return
    config = dict(payload.get("config") or {})
    simulation_budget = int(
        config.get("max_simulations", config.get("max_tool_calls", 0)) or 0
    )
    config["max_tool_calls"] = 0
    config["max_simulations"] = simulation_budget
    payload["config"] = config


def _complete_session(session, diagnosis: DropInsightSessionModel, timestamp: datetime) -> None:
    if diagnosis.status == "PLANNING":
        _cas_session_update(session, diagnosis, status="HYPOTHESIZING", timestamp=timestamp)
    if diagnosis.status == "HYPOTHESIZING":
        _cas_session_update(
            session, diagnosis, status="COLLECTING_EVIDENCE", timestamp=timestamp
        )
    if diagnosis.status == "COLLECTING_EVIDENCE":
        _cas_session_update(session, diagnosis, status="COMPLETED", timestamp=timestamp)


def _terminate_integrity_failure(session, diagnosis, detail: str) -> bool:
    timestamp = datetime.now(UTC)
    _append_event(
        session,
        diagnosis.id,
        "lats.search_terminated",
        "SYSTEM",
        {
            "reason": "REPLAY_MANIFEST_INTEGRITY_FAILED",
            "detail": str(detail)[:1000],
            "frozen_replay": True,
        },
        timestamp,
        effect_key="frozen-replay:integrity-failed",
    )
    _cas_session_update(
        session, diagnosis, status="INSUFFICIENT_EVIDENCE", timestamp=timestamp
    )
    session.commit()
    return True


def _start_response(
    diagnosis: DropInsightSessionModel, *, scenario_id: str, created: bool
) -> dict[str, Any]:
    return {
        **diagnosis.to_dict(),
        "created": created,
        "execution_mode": "FULL_LATS",
        "snapshot": {
            "snapshot_id": (diagnosis.target_json or {}).get("snapshot_id"),
            "snapshot_digest": (diagnosis.target_json or {}).get("snapshot_digest"),
            "manifest_digest": (diagnosis.target_json or {}).get("manifest_digest"),
            "immutable": True,
        },
        "showcase": {
            "kind": SHOWCASE_KIND,
            "scenario_id": scenario_id,
            "live_collection": False,
            "stream": f"/api/v2/diagnoses/{diagnosis.id}/events/stream",
            "exploration_tree": (
                f"/api/v2/diagnoses/{diagnosis.id}/exploration-tree"
            ),
        },
    }
