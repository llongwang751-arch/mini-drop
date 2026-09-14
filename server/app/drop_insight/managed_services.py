"""Operator-owned service inventory resolved against current Agent observations.

The catalog contains selectors, never PIDs or executable commands. Discovery and
clarification issue and validate the same opaque identity bindings as the normal
diagnosis flow. A catalog entry alone is not proof of availability or health.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..database import new_session
from ..models import AgentModel, ProcessCandidateModel, ProcessCandidateSnapshotModel
from . import service
from .business_observations import recent_observations, resolve_observation, diagnosis_context
from .schemas import CreateDiagnosisRequestV2, ClarifyDiagnosisRequest, ClarificationTarget

CATALOG = Path(__file__).with_name("managed_services.json")


class StartServiceDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=2000)
    mode: Literal["AUTONOMOUS", "ASSISTED"] = "AUTONOMOUS"
    request_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


def catalog() -> list[dict]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))["services"]


def _process_matches(entry: dict, process: dict) -> bool:
    if entry.get("process_name") and process.get("process", process.get("comm")) != entry["process_name"]:
        return False
    if entry.get("exclude_container_init") and not (isinstance(process.get("namespace_pid"), int) and process["namespace_pid"]>1):
        return False
    return True


def list_managed_services() -> dict:
    timestamp = service.now_utc()
    result = []
    session = new_session()
    try:
        for entry in catalog():
            item = dict(entry, status="UNAVAILABLE", instances=[], observed_at=None)
            item["business_requests"] = recent_observations(entry)
            agent = session.get(AgentModel, entry["agent_id"])
            if agent is None or agent.status != "ONLINE" or not (
                timedelta(0) <= timestamp - service._as_utc(agent.last_heartbeat_at)
                <= service._AGENT_HEARTBEAT_MAX_AGE
            ):
                result.append(item)
                continue
            snapshot = session.query(ProcessCandidateSnapshotModel).filter_by(
                agent_id=agent.id
            ).order_by(ProcessCandidateSnapshotModel.received_at.desc(),
                       ProcessCandidateSnapshotModel.id.desc()).first()
            if snapshot is None:
                result.append(item)
                continue
            item["observed_at"] = service._as_utc(snapshot.received_at).isoformat()
            if not (timedelta(0) <= timestamp - service._as_utc(snapshot.received_at)
                    <= service.PROCESS_SNAPSHOT_MAX_AGE):
                item["status"] = "STALE"
            elif not snapshot.authoritative or not snapshot.complete or snapshot.truncated:
                item["status"] = "UNAVAILABLE"
            else:
                rows = session.query(ProcessCandidateModel).filter_by(
                    snapshot_id=snapshot.id, agent_id=agent.id,
                    service_hint=entry["service_hint"],
                ).order_by(ProcessCandidateModel.pid).all()
                rows = [row for row in rows if _process_matches(entry, {"comm":row.comm,"namespace_pid":row.namespace_pid})]
                item["instances"] = [dict(
                    process=row.comm, pid=row.pid,
                    capabilities=row.collector_capabilities or [],
                ) for row in rows]
                item["status"] = "OBSERVED" if rows else "OFFLINE"
            result.append(item)
        return {"items": result, "checked_at": timestamp.isoformat()}
    finally:
        session.close()


def start_service_diagnosis(service_id: str, payload: StartServiceDiagnosis, *, principal: str) -> dict:
    entry = next((row for row in catalog() if row["id"] == service_id), None)
    if entry is None:
        raise KeyError(service_id)
    if not payload.query.strip():
        raise ValueError("请描述服务的性能现象")
    observation = resolve_observation(entry, payload.request_id) if payload.request_id else None
    query = payload.query.strip() + (diagnosis_context(observation) if observation else "")
    if len(query)>2000:
        raise ValueError("附带业务请求时，请将现象描述缩短至 1000 字以内")
    live = next(row for row in list_managed_services()["items"] if row["id"] == service_id)
    if live["status"] != "OBSERVED":
        raise ValueError("服务没有新鲜的可信进程快照，请确认后台与 Agent 正在运行后刷新")
    diagnosis = service.create_diagnosis(CreateDiagnosisRequestV2(
        query=query, mode=payload.mode, auto_scope=False,
        target={"service": entry["service_hint"]},
    ), created_by=principal, business_observation=observation)
    # Never let an autonomous fallback pick a different service. If a restart
    # races with selection, keep the existing case and retry fresh discovery.
    for _ in range(3):
        discovery = service.discover_target_candidates(
            diagnosis.id, service=entry["service_hint"], agent_id=entry["agent_id"],
        )
        matches = [row for row in (discovery or {}).get("candidates", [])
                   if row.get("service") == entry["service_hint"] and row.get("eligible") and _process_matches(entry,row)]
        if len(matches) != 1:
            break
        try:
            service.clarify_diagnosis(diagnosis.id, ClarifyDiagnosisRequest(
                expected_version=discovery["diagnosis_version"],
                target=ClarificationTarget(
                    service=entry["service_hint"], environment=entry["environment"],
                    discovery_id=discovery["discovery_id"], binding_id=matches[0]["binding_id"],
                ),
                time_range=service._default_auto_scope_range(payload.query, timestamp=service.now_utc()),
            ), actor=principal)
            break
        except service._DiscoveryInvalidationError:
            continue
    return service.get_diagnosis(diagnosis.id).to_dict()
