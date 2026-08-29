from __future__ import annotations

from pathlib import Path

import yaml

from server.app.diagnosis_worker import DiagnosisWorker
from server.app.grpc_main import ControlPlaneMaintenance, _tcp_healthcheck
from server.app import main as main_module


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

    def expire_stale_task_leases(self):
        return ["task-expired"]


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
    assert result == {
        "offline_agents": 1,
        "expired_task_leases": 1,
        "metric_snapshots": 2,
    }


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


def test_server_maintenance_reconciles_after_advance_failure(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        main_module.repo,
        "mark_offline_agents",
        lambda timeout_sec: calls.append("offline"),
    )
    monkeypatch.setattr(
        main_module.repo,
        "persist_agent_metric_snapshots",
        lambda: calls.append("metrics"),
    )
    monkeypatch.setattr(
        main_module.diagnosis_orchestrator,
        "advance_active",
        lambda: (_ for _ in ()).throw(RuntimeError("advance failed")),
    )
    monkeypatch.setattr(
        main_module.diagnosis_orchestrator,
        "reconcile_terminal_artifacts",
        lambda: calls.append("reconcile"),
    )
    monkeypatch.setattr(main_module, "log_event", lambda *args, **kwargs: None)

    main_module._run_maintenance_once()

    assert calls == ["offline", "metrics", "reconcile"]


def _compose(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_primary_compose_activates_outbox_only_in_api_server() -> None:
    services = _compose("docker-compose.yml")["services"]

    assert services["server"]["environment"]["MINI_DROP_OUTBOX_DISPATCH_ENABLED"] == "1"
    assert services["diagnosis-worker"]["environment"]["MINI_DROP_OUTBOX_DISPATCH_ENABLED"] == "0"


def test_primary_compose_follows_replication_guide_control_path() -> None:
    services = _compose("docker-compose.yml")["services"]

    assert services["control-plane"]["build"]["dockerfile"] == "deploy/dockerfiles/native-control.Dockerfile"
    assert "profiles" not in services["native-agent"]
    assert services["native-agent"]["environment"]["AGENT_GRPC_ADDR"].endswith("control-plane:50051}")
    assert services["agent"]["profiles"] == ["python-agent"]
    assert services["apiserver"]["build"]["dockerfile"] == "deploy/dockerfiles/apiserver.Dockerfile"
    assert services["apiserver"]["environment"]["MINI_DROP_CONTROL_GRPC_ADDRESS"] == "control-plane:50051"
    assert "control-plane" in services["apiserver"]["depends_on"]
    assert services["server"]["environment"]["MINI_DROP_EMBED_GRPC"] == "0"
    assert "pid" not in services["server"]
    assert services["web"]["depends_on"] == {"apiserver": {"condition": "service_healthy"}}


def test_cloud_compose_worker_overrides_server_outbox_environment() -> None:
    services = _compose("docker-compose.cloud-control.yml")["services"]

    assert services["server"]["environment"]["MINI_DROP_OUTBOX_DISPATCH_ENABLED"] == "1"
    assert services["diagnosis-worker"]["environment"]["MINI_DROP_OUTBOX_DISPATCH_ENABLED"] == "0"


def test_cloud_compose_uses_cpp_control_with_mutual_tls() -> None:
    services = _compose("docker-compose.cloud-control.yml")["services"]

    control = services["control-plane"]
    assert control["build"]["dockerfile"] == "deploy/dockerfiles/native-control.Dockerfile"
    assert control["environment"]["MINI_DROP_GRPC_SECURE"] == "1"
    assert control["environment"]["MINI_DROP_GRPC_REQUIRE_CLIENT_CERT"] == "1"
    assert services["server"]["environment"]["MINI_DROP_EMBED_GRPC"] == "0"
    assert services["apiserver"]["environment"]["MINI_DROP_CONTROL_GRPC_ADDRESS"] == "control-plane:50051"
    assert services["apiserver"]["environment"]["MINI_DROP_CONTROL_GRPC_CLIENT_CERT_FILE"] == "/certs/client.crt"
    assert "pid" not in services["server"]
    assert services["campaign-agent"]["profiles"] == ["showcase"]
    assert services["campaign-agent"]["build"]["dockerfile"] == "deploy/dockerfiles/native-agent.Dockerfile"
    assert services["campaign-agent"]["environment"]["AGENT_GRPC_ADDR"] == "control-plane:50051"
    assert services["campaign-agent"]["environment"]["AGENT_GRPC_CLIENT_CERT"] == "/certs/client.crt"


def test_worker_compose_uses_native_cpp_agent_with_mutual_tls() -> None:
    services = _compose("docker-compose.worker.yml")["services"]
    agent = services["agent"]

    assert agent["build"]["dockerfile"] == "deploy/dockerfiles/native-agent.Dockerfile"
    assert agent["environment"]["AGENT_GRPC_SECURE"] == "1"
    assert agent["environment"]["AGENT_GRPC_CLIENT_CERT"] == "/certs/client.crt"
    assert agent["environment"]["AGENT_GRPC_CLIENT_KEY"] == "/certs/client.key"


def test_three_host_control_compose_matches_the_same_four_module_topology() -> None:
    services = _compose("docker-compose.control.yml")["services"]

    assert services["control-plane"]["build"]["dockerfile"] == "deploy/dockerfiles/native-control.Dockerfile"
    assert services["server"]["environment"]["MINI_DROP_EMBED_GRPC"] == "0"
    assert services["apiserver"]["environment"]["MINI_DROP_CONTROL_GRPC_ADDRESS"] == "control-plane:50051"
    assert services["web"]["depends_on"] == {"apiserver": {"condition": "service_healthy"}}
    assert "diagnosis-worker" in services
    assert "analyzer" in services


def test_cpp_control_persists_agent_process_snapshots() -> None:
    source = Path("native/control/src/main.cpp").read_text(encoding="utf-8")

    assert "persist_process_snapshot" in source
    assert "INSERT INTO process_candidate_snapshots" in source
    assert "INSERT INTO process_candidates" in source
    assert "request->has_process_candidate_snapshot()" in source
    assert "process_binding_matches_latest" in source
    assert "TARGET_IDENTITY_CHANGED" in source


def test_go_owns_process_candidate_read_api() -> None:
    go_server = Path("apiserver/internal/httpapi/server.go").read_text(encoding="utf-8")
    python_server = Path("server/app/main.py").read_text(encoding="utf-8")
    web_input = Path("web/src/components/NLPTaskInput.jsx").read_text(encoding="utf-8")

    assert 'mux.HandleFunc("GET /api/top-processes", s.listTopProcesses)' in go_server
    assert 'mux.Handle("GET /api/top-processes", proxy)' not in go_server
    assert '@app.get("/api/top-processes")' not in python_server
    assert "resolve_pid(intent.process_name)" not in python_server
    assert '"process_candidates_source": "agent_snapshot"' in python_server
    assert "listTopProcesses(agentId, 20)" in web_input
    assert "result.candidate_pids.map" not in web_input


def test_go_task_creation_uses_agent_capability_and_process_attestation() -> None:
    go_server = Path("apiserver/internal/httpapi/server.go").read_text(encoding="utf-8")
    repository = Path("apiserver/internal/repository/postgres.go").read_text(encoding="utf-8")

    assert "AgentSupportsCollector" in go_server
    assert "ResolveFreshProcessCandidate" in go_server
    assert "process_snapshot_id, process_binding_json" in repository


def test_grpc_tcp_healthcheck_fails_for_closed_port() -> None:
    assert _tcp_healthcheck("127.0.0.1", 1, timeout=0.05) == 1
