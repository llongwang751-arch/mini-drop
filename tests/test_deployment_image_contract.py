from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_python_worker_image_contains_runtime_import_roots() -> None:
    """The private Python worker image packages analysis and AI modules only."""

    dockerfile = (ROOT / "deploy" / "dockerfiles" / "python-worker.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "COPY server/ ./server/" in dockerfile
    assert "COPY analyzer/ ./analyzer/" in dockerfile
    assert "COPY scripts/ ./scripts/" in dockerfile
    assert "COPY skills/ ./skills/" in dockerfile
    assert "COPY knowledge/ ./knowledge/" in dockerfile
    assert "FastAPI" not in dockerfile


def test_compose_migration_bootstraps_object_store_after_minio_is_healthy() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "python scripts/bootstrap_object_store.py" in compose
    assert "minio: {condition: service_healthy}" in compose


def test_multi_node_control_compose_exposes_only_required_host_ports() -> None:
    compose = (ROOT / "docker-compose.control.yml").read_text(encoding="utf-8")

    assert "${CONTROL_GRPC_PORT:-50051}:50051" in compose
    assert "${CONTROL_MINIO_PORT:-9000}:9000" in compose
    assert '"127.0.0.1:${INTERVIEW_DEMO_MINIO_PORT:-19000}:9000"' in compose
    assert "${CONTROL_HTTPS_PORT:-443}:443" in compose
    assert 'MINI_DROP_GRPC_REQUIRE_CLIENT_CERT: "1"' in compose
    # The production service set has no always-on local Agent. The only local
    # Agent is the separately tested, opt-in ``demo-agent`` profile.
    assert "\n  native-agent:\n" not in compose
    assert '"5432:5432"' not in compose
    assert '"50061:50061"' not in compose
    assert '"8080:8080"' not in compose
    assert '"9001:9001"' not in compose
    assert '"8081:8081"' not in compose


def test_control_interview_demo_is_opt_in_bounded_and_uses_a_distinct_mtls_agent() -> None:
    compose = (ROOT / "docker-compose.control.yml").read_text(encoding="utf-8")
    services = yaml.safe_load(compose)["services"]
    hotspot = services["python-hotspot"]
    demo_agent = services["demo-agent"]
    diagnosis_worker = services["diagnosis-worker"]
    env_example = (ROOT / "deploy" / "env" / "control.env.example").read_text(
        encoding="utf-8"
    )

    assert hotspot["profiles"] == ["interview-demo"]
    assert hotspot["environment"]["CPU_HOTSPOT_ACTIVE"] == "0"
    assert "ports" not in hotspot
    assert "pid" not in hotspot
    assert hotspot["read_only"] is True
    assert hotspot["tmpfs"] == ["/tmp:size=192m,mode=1777"]
    assert hotspot["cap_drop"] == ["ALL"]

    assert demo_agent["profiles"] == ["interview-demo"]
    assert demo_agent["pid"] == "host"
    assert demo_agent["network_mode"] == "host"
    assert (
        demo_agent["environment"]["AGENT_ID"]
        == "${INTERVIEW_DEMO_AGENT_ID:-control-interview-demo-agent}"
    )
    assert (
        demo_agent["environment"]["AGENT_GRPC_ADDR"]
        == "127.0.0.1:${CONTROL_GRPC_PORT:-50051}"
    )
    assert demo_agent["environment"]["AGENT_GRPC_TLS_SERVER_NAME"] == "127.0.0.1"
    assert "INTERVIEW_DEMO_AGENT_CERT_DIR" in demo_agent["volumes"][-1]
    assert "INTERVIEW_DEMO_CPU_LIMIT" in str(hotspot["cpus"])
    assert "INTERVIEW_DEMO_MEMORY_LIMIT" in hotspot["mem_limit"]
    assert "INTERVIEW_DEMO_PIDS_LIMIT" in str(hotspot["pids_limit"])
    assert "interviewdemoagentoutbox:" in compose
    assert (
        diagnosis_worker["environment"]["MINIO_AGENT_ENDPOINT"]
        == "${MINIO_AGENT_ENDPOINT:?set Worker-reachable MINIO_AGENT_ENDPOINT without http://}"
    )
    assert diagnosis_worker["environment"]["MINIO_AGENT_SECURE"] == "${MINIO_AGENT_SECURE:-0}"

    assert "MINI_DROP_FAULT_LAB_URL=" in env_example
    assert "INTERVIEW_DEMO_AGENT_ID=control-interview-demo-agent" in env_example
    assert (
        "INTERVIEW_DEMO_AGENT_CERT_DIR=./deploy/certs/control/agents/"
        "control-interview-demo-agent"
    ) in env_example
    assert "MINIO_AGENT_ENDPOINT=127.0.0.1:19000" in env_example


def test_multi_node_worker_is_one_secure_host_pid_agent() -> None:
    compose = (ROOT / "docker-compose.worker.yml").read_text(encoding="utf-8")

    assert "pid: host" in compose
    assert 'AGENT_GRPC_SECURE: "1"' in compose
    assert "AGENT_GRPC_TLS_SERVER_NAME:" in compose
    assert "MINI_DROP_GRPC_TOKEN:" in compose
    assert "AGENT_CERT_DIR:" in compose
    assert '"apparmor:unconfined"' in compose
    assert '"no-new-privileges:true"' in compose
    assert "ports:" not in compose


def test_multi_node_pki_issues_one_identity_per_agent() -> None:
    issuer = (ROOT / "deploy" / "scripts" / "issue-agent-cert.sh").read_text(
        encoding="utf-8"
    )

    assert '-subj "/CN=$AGENT_ID"' in issuer
    assert "refusing to overwrite" in issuer
    assert 'cp "$PKI_DIR/ca.crt" "$OUTPUT_DIR/ca.crt"' in issuer


def test_control_client_key_is_readable_only_by_root_group() -> None:
    compose = (ROOT / "docker-compose.control.yml").read_text(encoding="utf-8")
    initializer = (ROOT / "deploy" / "scripts" / "init-control-pki.sh").read_text(
        encoding="utf-8"
    )

    assert 'group_add: ["0"]' in compose
    assert 'chmod 640 "$PKI_DIR/client.key"' in initializer


def test_native_agent_image_also_builds_the_opt_in_gperftools_bridge() -> None:
    dockerfile = (ROOT / "deploy" / "dockerfiles" / "native-agent.Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "COPY native/gperftools_bridge/ ./native/gperftools_bridge/" in dockerfile
    assert "cmake -S native/gperftools_bridge -B /bridge-build" in dockerfile
    assert "/bridge-build/mini-drop-gperftools-bridge /usr/local/bin/" in dockerfile


def test_native_images_default_to_one_build_job_on_small_cloud_hosts() -> None:
    """A release build must not starve the live 2-core control host."""

    for name in ("native-control.Dockerfile", "native-agent.Dockerfile"):
        dockerfile = (ROOT / "deploy" / "dockerfiles" / name).read_text(
            encoding="utf-8"
        )
        assert "ARG NATIVE_BUILD_JOBS=1" in dockerfile
        assert 'cmake --build /build --parallel "${NATIVE_BUILD_JOBS}"' in dockerfile
