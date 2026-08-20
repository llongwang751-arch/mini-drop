from __future__ import annotations

from server.app.diagnosis_worker import DiagnosisWorker
from server.app.grpc_main import ControlPlaneMaintenance, _tcp_healthcheck


class _ControlRepo:
    def __init__(self) -> None:
        self.timeout = None
        self.persisted = 0

    def mark_offline_agents(self, timeout_sec: int):
        self.timeout = timeout_sec
        return ["agent-a"]

    def persist_agent_metric_snapshots(self) -> int:
        self.persisted += 1
        return 2


class _Orchestrator:
    def __init__(self) -> None:
        self.calls = 0

    def advance_active(self) -> None:
        self.calls += 1


def test_control_plane_maintenance_keeps_metric_cache_with_grpc_repo() -> None:
    repo = _ControlRepo()
    result = ControlPlaneMaintenance(repo, timeout_sec=31, interval_sec=1).run_once()

    assert repo.timeout == 31
    assert repo.persisted == 1
    assert result == {"offline_agents": 1, "metric_snapshots": 2}


def test_diagnosis_worker_advances_persisted_sessions_once() -> None:
    orchestrator = _Orchestrator()
    worker = DiagnosisWorker(orchestrator)  # type: ignore[arg-type]

    assert worker.process_once() == 0
    assert orchestrator.calls == 1


def test_diagnosis_worker_also_advances_drop_insight_v2() -> None:
    orchestrator = _Orchestrator()
    calls = []
    worker = DiagnosisWorker(
        orchestrator,  # type: ignore[arg-type]
        drop_insight_advancer=lambda: calls.append("v2") or 2,
    )

    assert worker.process_once() == 2
    assert orchestrator.calls == 1
    assert calls == ["v2"]


def test_grpc_tcp_healthcheck_fails_for_closed_port() -> None:
    assert _tcp_healthcheck("127.0.0.1", 1, timeout=0.05) == 1
