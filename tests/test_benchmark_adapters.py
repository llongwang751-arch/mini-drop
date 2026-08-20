from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.diagnosis import benchmark_adapters
from server.app.diagnosis.benchmark_adapters import (
    OTEL_FEATURE_URL,
    adapter_preflight,
    preflight_all,
    replay_local_golden,
    set_otel_feature_flag,
)


PINNED_REVISION = "3684411da9a4dc3e77cddfef929a630d6f5af6c5"


def _write_otel_fixture(root: Path, *, include_ad: bool = True) -> None:
    flag_dir = root / "src" / "flagd"
    flag_dir.mkdir(parents=True)
    flags = {
        "adHighCpu": {
            "defaultVariant": "off",
            "variants": {"off": False, "on": True},
        },
        "emailMemoryLeak": {
            "defaultVariant": "off",
            "variants": {"off": 0, "100x": 100},
        },
        "adManualGc": {
            "defaultVariant": "off",
            "variants": {"off": False, "on": True},
        },
        "imageSlowLoad": {
            "defaultVariant": "off",
            "variants": {"off": 0, "5sec": 5},
        },
        "kafkaQueueProblems": {
            "defaultVariant": "off",
            "variants": {"off": False, "on": True},
        },
        "paymentUnreachable": {
            "defaultVariant": "off",
            "variants": {"off": False, "on": True},
        },
        "paymentFailure": {
            "defaultVariant": "off",
            "variants": {"off": 0, "100%": 100},
        },
        "loadGeneratorTraffic": {
            "defaultVariant": "on",
            "variants": {"on": True},
        },
        "loadGeneratorVUs": {
            "defaultVariant": "5",
            "variants": {"5": 5, "50": 50},
        },
    }
    (flag_dir / "demo.flagd.json").write_text(
        json.dumps({"flags": flags}), encoding="utf-8"
    )
    services = [
        "email",
        "image-provider",
        "frontend",
        "checkout",
        "payment",
        "load-generator",
        "flagd",
        "otel-collector",
    ]
    if include_ad:
        services.append("ad")
    compose = "services:\n" + "".join(f"  {name}:\n    image: example\n" for name in services)
    (root / "compose.yaml").write_text(compose, encoding="utf-8")
    full_services = ["kafka", "accounting", "fraud-detection"]
    compose_full = "services:\n" + "".join(
        f"  {name}:\n    image: example\n" for name in full_services
    )
    (root / "compose.full.yaml").write_text(compose_full, encoding="utf-8")


@pytest.fixture
def ready_otel(monkeypatch, tmp_path: Path) -> Path:
    _write_otel_fixture(tmp_path)
    monkeypatch.setattr(
        benchmark_adapters,
        "_git_revision",
        lambda root: (PINNED_REVISION, None),
    )
    monkeypatch.setattr(
        benchmark_adapters,
        "_docker_readiness",
        lambda: (True, True),
    )
    return tmp_path


def test_all_ten_cases_have_a_known_adapter() -> None:
    report = preflight_all()

    assert report["case_count"] == 10
    assert report["supported_count"] == 10
    assert report["ready_count"] == report["fixture_ready_count"]
    assert report["ready_count"] >= 3


def test_local_golden_exposes_all_three_readiness_levels() -> None:
    check = adapter_preflight("T1-CODE-001")

    assert check["adapter_supported"] is True
    assert check["fixture_ready"] is True
    assert check["diagnosis_ready"] is True
    assert check["ready"] == check["fixture_ready"]


def test_otel_adapter_provides_live_feature_flag_instructions() -> None:
    check = adapter_preflight("T1-CPU-001")

    assert check["adapter_supported"] is True
    assert check["fixture_ready"] is False
    assert check["diagnosis_ready"] is False
    assert check["explicit_otel_root"] is False
    assert check["mode"] == "live_fault_injection"
    assert check["feature_flag"] == "adServiceHighCpu"
    assert check["pinned_flag_values"] == [{"key": "adHighCpu", "variant": "on"}]
    assert check["feature_flag_url"] == OTEL_FEATURE_URL
    assert "explicit otel_root is required" in check["fixture_reasons"]


def test_otel_preflight_accepts_pinned_fixture_and_agent(ready_otel: Path) -> None:
    check = adapter_preflight(
        "T1-CPU-001",
        otel_root=ready_otel,
        agent={
            "id": "worker-1",
            "status": "ONLINE",
            "capabilities": ["sys_metrics", "java_async"],
        },
    )

    assert check["actual_revision"] == PINNED_REVISION
    assert check["revision_ready"] is True
    assert check["flag_keys_ready"] is True
    assert check["flag_variants_ready"] is True
    assert check["services_ready"] is True
    assert check["fixture_ready"] is True
    assert check["diagnosis_ready"] is True
    assert check["ready"] is True


def test_otel_preflight_rejects_wrong_commit(monkeypatch, ready_otel: Path) -> None:
    monkeypatch.setattr(
        benchmark_adapters,
        "_git_revision",
        lambda root: ("0" * 40, None),
    )

    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["actual_revision"] == "0" * 40
    assert check["revision_ready"] is False
    assert check["fixture_ready"] is False


def test_git_worktree_does_not_require_git_directory(monkeypatch, tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        return SimpleNamespace(returncode=0, stdout=PINNED_REVISION + "\n", stderr="")

    monkeypatch.setattr(benchmark_adapters.subprocess, "run", fake_run)

    revision, error = benchmark_adapters._git_revision(tmp_path)

    assert revision == PINNED_REVISION
    assert error is None
    assert observed["command"] == ["git", "-C", str(tmp_path), "rev-parse", "HEAD"]


def test_otel_preflight_rejects_missing_flag_key(ready_otel: Path) -> None:
    path = ready_otel / "src" / "flagd" / "demo.flagd.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["flags"]["adHighCpu"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["missing_flag_keys"] == ["adHighCpu"]
    assert check["flag_keys_ready"] is False
    assert check["fixture_ready"] is False


def test_otel_preflight_rejects_missing_flag_variant(ready_otel: Path) -> None:
    path = ready_otel / "src" / "flagd" / "demo.flagd.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["flags"]["adHighCpu"]["variants"]["on"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["missing_enabled_variants"] == [{"key": "adHighCpu", "variant": "on"}]
    assert check["flag_variants_ready"] is False
    assert check["fixture_ready"] is False


def test_otel_preflight_rejects_missing_baseline_variant(ready_otel: Path) -> None:
    path = ready_otel / "src" / "flagd" / "demo.flagd.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    del payload["flags"]["adHighCpu"]["variants"]["off"]
    path.write_text(json.dumps(payload), encoding="utf-8")

    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["missing_baseline_variants"] == [
        {"key": "adHighCpu", "variant": "off"}
    ]
    assert check["flag_variants_ready"] is False
    assert check["fixture_ready"] is False


def test_otel_preflight_rejects_malformed_flag_fixture(ready_otel: Path) -> None:
    path = ready_otel / "src" / "flagd" / "demo.flagd.json"
    path.write_text("{not-json", encoding="utf-8")

    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["flag_keys_ready"] is False
    assert check["fixture_ready"] is False
    assert any(
        reason.startswith("cannot read pinned flag configuration:")
        for reason in check["fixture_reasons"]
    )


def test_all_pinned_flag_mappings_validate(ready_otel: Path) -> None:
    case_ids = [
        "T1-CPU-001",
        "T1-GC-001",
        "T1-MEM-001",
        "T1-DOWNSTREAM-001",
        "T1-NET-001",
        "T1-QUEUE-001",
        "T1-LOAD-001",
    ]

    checks = [adapter_preflight(case_id, otel_root=ready_otel) for case_id in case_ids]

    assert all(check["flag_keys_ready"] for check in checks)
    assert all(check["flag_variants_ready"] for check in checks)
    assert all(not check["missing_enabled_variants"] for check in checks)
    assert all(not check["missing_baseline_variants"] for check in checks)


def test_compose_services_are_merged_across_layers(ready_otel: Path) -> None:
    check = adapter_preflight("T1-QUEUE-001", otel_root=ready_otel)

    assert check["services_ready"] is True
    assert {"kafka", "accounting", "fraud-detection"} <= set(
        check["available_services"]
    )
    assert check["fixture_ready"] is True
    assert check["runner_supported"] is False
    assert check["diagnosis_ready"] is False
    assert check["ready"] is True


def test_missing_required_service_blocks_fixture(monkeypatch, tmp_path: Path) -> None:
    _write_otel_fixture(tmp_path, include_ad=False)
    monkeypatch.setattr(
        benchmark_adapters,
        "_git_revision",
        lambda root: (PINNED_REVISION, None),
    )
    monkeypatch.setattr(
        benchmark_adapters,
        "_docker_readiness",
        lambda: (True, True),
    )

    check = adapter_preflight("T1-CPU-001", otel_root=tmp_path)

    assert check["services_ready"] is False
    assert check["missing_services"] == ["ad"]
    assert check["fixture_ready"] is False


def test_docker_unavailable_blocks_fixture(monkeypatch, ready_otel: Path) -> None:
    monkeypatch.setattr(
        benchmark_adapters,
        "_docker_readiness",
        lambda: (False, False),
    )

    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["docker_available"] is False
    assert check["compose_available"] is False
    assert check["fixture_ready"] is False
    assert "docker executable is unavailable" in check["fixture_reasons"]


def test_compose_unavailable_blocks_fixture(monkeypatch, ready_otel: Path) -> None:
    monkeypatch.setattr(
        benchmark_adapters,
        "_docker_readiness",
        lambda: (True, False),
    )

    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["docker_available"] is True
    assert check["compose_available"] is False
    assert check["fixture_ready"] is False
    assert "docker compose is unavailable" in check["fixture_reasons"]


def test_missing_agent_blocks_diagnosis(ready_otel: Path) -> None:
    check = adapter_preflight("T1-CPU-001", otel_root=ready_otel)

    assert check["fixture_ready"] is True
    assert check["capabilities_ready"] is False
    assert check["missing_capabilities"] == ["java_async", "sys_metrics"]
    assert check["diagnosis_ready"] is False
    assert "explicit target Agent readiness is required" in check["diagnosis_reasons"]


def test_offline_agent_blocks_diagnosis(ready_otel: Path) -> None:
    check = adapter_preflight(
        "T1-CPU-001",
        otel_root=ready_otel,
        agent={
            "id": "worker-1",
            "status": "OFFLINE",
            "capabilities": ["sys_metrics", "java_async"],
        },
    )

    assert check["fixture_ready"] is True
    assert check["missing_capabilities"] == []
    assert check["capabilities_ready"] is False
    assert check["diagnosis_ready"] is False
    assert "target Agent is not ONLINE" in check["diagnosis_reasons"]


def test_partial_agent_capabilities_block_diagnosis(ready_otel: Path) -> None:
    check = adapter_preflight(
        "T1-CPU-001",
        otel_root=ready_otel,
        agent={"id": "worker-1", "status": "ONLINE", "capabilities": ["sys_metrics"]},
    )

    assert check["fixture_ready"] is True
    assert check["missing_capabilities"] == ["java_async"]
    assert check["capabilities_ready"] is False
    assert check["diagnosis_ready"] is False


def test_mem_agent_with_required_capabilities_is_diagnosis_ready(
    ready_otel: Path,
) -> None:
    check = adapter_preflight(
        "T1-MEM-001",
        otel_root=ready_otel,
        agent={
            "id": "worker-2",
            "status": "ONLINE",
            "capabilities": ["memory_smaps", "sys_metrics"],
        },
    )

    assert check["required_capabilities"] == ["sys_metrics", "memory_smaps"]
    assert check["fixture_ready"] is True
    assert check["capabilities_ready"] is True
    assert check["diagnosis_ready"] is True


def test_local_golden_adapter_replays_existing_scenario() -> None:
    result = replay_local_golden("T1-CODE-001")

    assert result["scenario_id"] == "self_code_hotspot"
    assert result["passed"] is True


def test_live_case_cannot_be_misrepresented_as_local_replay() -> None:
    with pytest.raises(ValueError, match="not a local Golden"):
        replay_local_golden("T1-CPU-001")


def test_otel_flag_toggle_is_version_checked_and_reversible(tmp_path: Path) -> None:
    fixture = tmp_path / "src" / "flagd"
    fixture.mkdir(parents=True)
    config = {
        "flags": {
            "adHighCpu": {
                "defaultVariant": "off",
                "variants": {"off": False, "on": True},
            }
        }
    }
    path = fixture / "demo.flagd.json"
    path.write_text(json.dumps(config), encoding="utf-8")

    enabled = set_otel_feature_flag("T1-CPU-001", otel_root=tmp_path, enabled=True)
    assert enabled["changes"][0]["after"] == "on"
    assert json.loads(path.read_text())["flags"]["adHighCpu"]["defaultVariant"] == "on"

    set_otel_feature_flag("T1-CPU-001", otel_root=tmp_path, enabled=False)
    assert json.loads(path.read_text())["flags"]["adHighCpu"]["defaultVariant"] == "off"


def test_otel_flag_toggle_uses_explicit_isolated_config_path(tmp_path: Path) -> None:
    _write_otel_fixture(tmp_path)
    shared_path = tmp_path / "src" / "flagd" / "demo.flagd.json"
    shared_before = shared_path.read_bytes()
    isolated_path = tmp_path / "run-flags" / "demo.flagd.json"
    isolated_path.parent.mkdir()
    isolated_path.write_bytes(shared_before)

    result = set_otel_feature_flag(
        "T1-CPU-001",
        otel_root=tmp_path,
        enabled=True,
        flag_config_path=isolated_path,
    )

    assert result["config_path"] == str(isolated_path)
    assert json.loads(isolated_path.read_text(encoding="utf-8"))["flags"]["adHighCpu"]["defaultVariant"] == "on"
    assert shared_path.read_bytes() == shared_before
