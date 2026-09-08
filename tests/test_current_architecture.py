from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_removed_implementations_do_not_return() -> None:
    removed_paths = (
        "server/app/main.py",
        "server/app/grpc_server.py",
        "server/app/diagnosis",
        "server/app/rca",
        "agent/mini_drop_agent",
        "agent_runtime/pi-sidecar",
        "docker-compose.python-control.yml",
    )
    for relative_path in removed_paths:
        path = ROOT / relative_path
        assert not path.is_file(), relative_path
        assert not path.is_dir() or not any(item.is_file() for item in path.rglob("*")), relative_path

    dependencies = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    assert "fast" + "api" not in dependencies
    assert "uvicorn" not in dependencies


def test_replication_topology_has_one_public_control_plane() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    for service in (
        "apiserver:",
        "control-plane:",
        "native-agent:",
        "analyzer:",
        "diagnosis-worker:",
        "postgres:",
        "minio:",
        "web:",
    ):
        assert service in compose

    assert "MINI_DROP_DIAGNOSTIC_AI_GRPC_ADDRESS" in compose
    assert "server.app.diagnosis_worker" in compose
    assert "server.app.analysis_jobs" in compose
    assert "server.app.main" not in compose


def test_ai_worker_contract_and_skill_pipeline_are_present() -> None:
    required_paths = (
        "proto/diagnostic_ai.proto",
        "server/app/diagnostic_ai_rpc.py",
        "server/app/diagnosis_worker.py",
        "server/app/drop_insight/evidence.py",
        "server/app/drop_insight/exploration_tree.py",
        "server/app/drop_insight/skill_evolution.py",
        "server/app/agent_runtime/runtime.py",
        "server/app/agent_runtime/harness.py",
        "server/app/agent_runtime/themes.py",
        "server/app/agent_runtime/context.py",
        "server/app/agent_runtime/memory.py",
    )
    for relative_path in required_paths:
        assert (ROOT / relative_path).is_file(), relative_path

    from server.app.generated import diagnostic_ai_pb2_grpc

    assert diagnostic_ai_pb2_grpc.DiagnosticAIStub is not None
