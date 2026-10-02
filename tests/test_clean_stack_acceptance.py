"""Negative gates for the clean-run verifier; these are not live-stack proof."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat

import pytest
import yaml

from scripts.verify_clean_stack import (
    AcceptanceError, CORE, MEMORY_MB, ROOT, owned_container_ids,
    prepare_compose, public_config, redact, sha, source_migration_heads,
    validate_artifact, validate_key_stat, validate_runtime_config,
)


PROJECT = "mini-drop-clean-0123456789abcdef"


def runtime_config(tmp_path: Path) -> dict:
    """A documented normalized Compose shape, with no real credentials."""
    services = {}
    for name in CORE:
        image = f"{PROJECT}/{'python-worker' if name in {'migrate', 'diagnosis-worker', 'analyzer'} else name}:exact-git"
        spec = {"image": image, "environment": {}, "mem_limit": MEMORY_MB[name] * 1024 ** 2,
                "cpus": "1.0", "pids_limit": 256}
        if name == "postgres":
            spec["image"] = "postgres:16"
        elif name == "minio":
            spec["image"] = "quay.io/minio/minio:RELEASE.2025-04-08T15-41-24Z"
        elif name == "native-agent":
            spec.update({"pid": "host", "cap_drop": ["ALL"], "environment": {"AGENT_GRPC_SECURE": "1"}})
        elif name == "python-hotspot":
            spec.update({"pid": "host", "cap_drop": ["ALL"]})
        elif name in {"migrate", "analyzer"}:
            spec["environment"] = {"MINI_DROP_GRPC_SECURE": "0"}
        elif name == "apiserver":
            spec["environment"] = {"MINI_DROP_API_AUTH_ENABLED": "1", "MINI_DROP_CONTROL_GRPC_TLS": "1"}
        elif name in {"control-plane", "diagnosis-worker"}:
            spec["environment"] = {"MINI_DROP_GRPC_SECURE": "1", "MINI_DROP_GRPC_AUTH_ENABLED": "1",
                                   "MINI_DROP_GRPC_REQUIRE_CLIENT_CERT": "1"}
            if name == "diagnosis-worker":
                spec["environment"].update({"MINI_DROP_AI_ENABLED": "none", "MINI_DROP_AI_API_KEY": ""})
            else:
                spec["environment"].update({"MINI_DROP_GRPC_CLIENT_CERT_FILE": "/certs/client.crt", "MINI_DROP_GRPC_CLIENT_KEY_FILE": "/certs/client.key"})
        services[name] = spec
    services["web"]["ports"] = [{"target": 80, "published": "0", "host_ip": "127.0.0.1"}]
    return {"name": PROJECT, "services": services,
            "volumes": {"pgdata": {"name": PROJECT + "_pgdata"}},
            "networks": {"default": {"name": PROJECT + "_default"}}}


def raw_fixture() -> tuple[dict, bytes]:
    document = {"schema_version": "sys_metrics.v2", "pid": 4242, "namespace_pid": 4242,
                "clock_ticks_per_second": 100,
                "samples": [{"captured_at_unix_ms": 10000 + i * 1000, "process_start_ticks": 123,
                             "process_cpu_ticks": i, "rss_kb": 1024, "threads": 1, "fd_count": 3}
                            for i in range(6)]}
    raw = json.dumps(document).encode()
    artifact = {"id": 7, "integrity_status": "VERIFIED", "size_bytes": len(raw), "sha256": sha(raw),
                "metadata": {"schema_version": "sys_metrics_analysis.v2", "analyzer_type": "collector.sys_metrics",
                             "sample_count": 6, "process_identity": {"pid": 4242, "start_ticks": 123, "verified": True},
                             "summary": {"process_cpu_core_usage": 0.2}}}
    return artifact, raw


def test_valid_documented_config_and_realistic_artifact_shape(tmp_path):
    validate_runtime_config(runtime_config(tmp_path), PROJECT, tmp_path)
    artifact, raw = raw_fixture()
    proof = validate_artifact(artifact, raw, 4242, 123)
    assert proof["sample_count"] == 6 and proof["window_ms"] == 5000


@pytest.mark.parametrize("change", [
    lambda c: c["volumes"]["pgdata"].update(external=True),
    lambda c: c["volumes"]["pgdata"].update(name="mini-drop-control_pgdata"),
    lambda c: c["volumes"]["pgdata"].update(driver_opts={"device": "/var/lib/production"}),
    lambda c: c["networks"]["default"].update(external=True),
    lambda c: c["services"]["analyzer"].update(image="production/analyzer:current"),
    lambda c: c["services"]["web"]["ports"][0].update(host_ip="0.0.0.0"),
    lambda c: c["services"]["web"]["ports"][0].update(published="80"),
    lambda c: c["services"]["native-agent"].update(privileged=True),
    lambda c: c["services"]["native-agent"].update(cap_add=["SYS_ADMIN"]),
    lambda c: c["services"]["native-agent"].update(network_mode="host"),
    lambda c: c["services"]["postgres"].update(mem_limit=0),
    lambda c: c["services"]["web"].update(pids_limit=0),
    lambda c: c["services"]["web"].update(cpus="4"),
    lambda c: c["services"]["analyzer"].update(depends_on={"office": {}}),
    lambda c: c["services"]["analyzer"].update(volumes=[{"type": "bind", "source": "/", "read_only": True}]),
    lambda c: c["services"]["analyzer"].update(volumes=[{"type": "bind", "source": str(ROOT), "read_only": False}]),
    lambda c: c["services"]["apiserver"]["environment"].update(MINI_DROP_API_AUTH_ENABLED="0"),
    lambda c: c["services"]["control-plane"]["environment"].update(MINI_DROP_GRPC_REQUIRE_CLIENT_CERT="0"),
    lambda c: c["services"]["control-plane"]["environment"].pop("MINI_DROP_GRPC_CLIENT_KEY_FILE"),
    lambda c: c["services"]["diagnosis-worker"]["environment"].update(MINI_DROP_AI_API_KEY="provider-secret"),
])
def test_runtime_cannot_attach_production_or_silently_weaken_scope(tmp_path, change):
    config = runtime_config(tmp_path)
    change(config)
    with pytest.raises(AcceptanceError):
        validate_runtime_config(config, PROJECT, tmp_path)


@pytest.mark.parametrize("change", [
    lambda a: a.update(sha256="0" * 64),
    lambda a: a.update(size_bytes=1),
    lambda a: a.update(integrity_status="UNKNOWN"),
    lambda a: a["metadata"].update(schema_version="sys_metrics.v2"),
    lambda a: a["metadata"].update(sample_count=5),
    lambda a: a["metadata"].update(summary={}),
    lambda a: a["metadata"]["process_identity"].update(verified=False),
    lambda a: a["metadata"]["process_identity"].update(pid=9999),
])
def test_raw_presence_is_not_analyzed_verified_evidence(change):
    artifact, raw = raw_fixture()
    change(artifact)
    with pytest.raises(AcceptanceError):
        validate_artifact(artifact, raw, 4242, 123)


@pytest.mark.parametrize("change", [
    lambda d: d.update(pid=9999),
    lambda d: d["samples"][3].update(process_start_ticks=456),
    lambda d: d["samples"][3].pop("process_cpu_ticks"),
    lambda d: d["samples"][3].update(captured_at_unix_ms=11000),
    lambda d: d.update(samples=d["samples"][:1]),
])
def test_even_matching_sha_cannot_hide_pid_reuse_or_missing_observations(change):
    artifact, raw = raw_fixture()
    document = json.loads(raw)
    change(document)
    changed = json.dumps(document).encode()
    artifact.update(sha256=sha(changed), size_bytes=len(changed))
    with pytest.raises(AcceptanceError):
        validate_artifact(artifact, changed, 4242, 123)


def test_canonical_template_remains_unchanged_and_unique_images(tmp_path):
    original = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    snapshot = copy.deepcopy(original)
    config = prepare_compose(original, tmp_path, PROJECT)
    assert original == snapshot
    assert set(config["services"]) == CORE
    assert all(config["services"][name]["image"] == PROJECT + "/python-worker:exact-git"
               for name in ("migrate", "analyzer", "diagnosis-worker"))
    assert config["services"]["native-agent"]["cap_drop"] == ["ALL"]
    assert not any("/sys/kernel" in v for v in config["services"]["native-agent"]["volumes"])
    assert config["services"]["python-hotspot"]["environment"]["CPU_HOTSPOT_ACTIVE"] == "0"


def test_cleanup_checks_every_container_before_returning_any_id():
    valid = {"Id": "a" * 64, "Config": {"Labels": {"com.docker.compose.project": PROJECT,
        "mini-drop.clean-stack.owner": PROJECT, "com.docker.compose.service": "postgres"}}}
    assert owned_container_ids([valid], PROJECT) == ["a" * 64]
    foreign = copy.deepcopy(valid)
    foreign["Config"]["Labels"]["com.docker.compose.project"] = "mini-drop-control"
    with pytest.raises(AcceptanceError, match="foreign"):
        owned_container_ids([valid, foreign], PROJECT)
    with pytest.raises(AcceptanceError):
        owned_container_ids([valid], "mini-drop-control")


def test_public_evidence_never_serializes_keys_credentials_or_signed_urls(tmp_path):
    config = {"environment": {"DATABASE_URL": "postgresql://user:secret@host/db",
        "MINI_DROP_API_KEY": "api-secret", "MINI_DROP_AI_API_KEY": "provider-secret",
        "MINI_DROP_GRPC_TOKEN": "grpc-secret", "MINIO_SECRET_KEY": "object-secret"},
        "path": str(tmp_path / "certs/client.key")}
    text = json.dumps(public_config(config, tmp_path))
    assert all(value not in text for value in config["environment"].values())
    assert str(tmp_path) not in text
    log = redact('token=grpc-secret https://minio/x?X-Amz-Signature=abcdef&X-Amz-Credential=xyz', ["grpc-secret"])
    assert all(value not in log for value in ("grpc-secret", "abcdef", "xyz"))


def test_migration_source_has_real_head_not_empty_success():
    assert source_migration_heads()


@pytest.mark.parametrize("mode,uid,links", [
    (stat.S_IFREG | 0o600, 1001, 1),  # Fresh runner owner is unreadable by cap-free UID 0.
    (stat.S_IFREG | 0o640, 0, 1),
    (stat.S_IFREG | 0o644, 0, 1),
    (stat.S_IFLNK | 0o600, 0, 1),
    (stat.S_IFREG | 0o600, 0, 2),
])
def test_private_key_ownership_and_permissions_cannot_be_silently_weakened(mode, uid, links):
    info = os.stat_result((mode, 1, 1, links, uid, 0, 1, 0, 0, 0))
    with pytest.raises(AcceptanceError):
        validate_key_stat(info, 0)


@pytest.mark.parametrize("uid", [0, 65532])
def test_private_key_exact_runtime_uid_and_0600_are_accepted(uid):
    validate_key_stat(os.stat_result((stat.S_IFREG | 0o600, 1, 1, 1, uid, uid, 1, 0, 0, 0)), uid)


@pytest.mark.parametrize("role", ["migrate", "analyzer"])
@pytest.mark.parametrize("flag", ["1", None])
def test_shared_rpc_tls_flag_cannot_make_non_rpc_worker_need_unmounted_keys(tmp_path, role, flag):
    config = runtime_config(tmp_path)
    if flag is None:
        config["services"][role]["environment"].pop("MINI_DROP_GRPC_SECURE")
    else:
        config["services"][role]["environment"]["MINI_DROP_GRPC_SECURE"] = flag
    with pytest.raises(AcceptanceError, match=f"non-RPC {role}"):
        validate_runtime_config(config, PROJECT, tmp_path)


@pytest.mark.parametrize("role", ["migrate", "analyzer"])
def test_non_rpc_roles_have_no_private_rpc_key_mount(tmp_path, role):
    config = runtime_config(tmp_path)
    config["services"][role]["volumes"] = [{"type": "bind", "source": str(tmp_path / "certs"),
                                             "target": "/certs", "read_only": True}]
    with pytest.raises(AcceptanceError, match="must not require private RPC certificates"):
        validate_runtime_config(config, PROJECT, tmp_path)


def test_disabling_non_rpc_tls_does_not_disable_authenticated_rpc_roles(tmp_path):
    config = runtime_config(tmp_path)
    validate_runtime_config(config, PROJECT, tmp_path)
    assert config["services"]["migrate"]["environment"]["MINI_DROP_GRPC_SECURE"] == "0"
    assert config["services"]["analyzer"]["environment"]["MINI_DROP_GRPC_SECURE"] == "0"
    assert config["services"]["control-plane"]["environment"]["MINI_DROP_GRPC_REQUIRE_CLIENT_CERT"] == "1"
    assert config["services"]["diagnosis-worker"]["environment"]["MINI_DROP_GRPC_SECURE"] == "1"
