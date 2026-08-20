from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "deploy" / "otel-demo"
COMMON = FIXTURE / "compose.common.yaml"
CPU = FIXTURE / "compose.t1-cpu-001.yaml"
MEM = FIXTURE / "compose.t1-mem-001.yaml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_common_fixture_is_project_scoped_and_does_not_mount_host_control_paths() -> None:
    common = _text(COMMON)

    assert "container_name:" not in common
    assert "\n    name:" not in common
    assert "internal: true" in common
    assert "MINI_DROP_OTEL_FLAG_DIR:?" in common
    assert "target: /etc/flagd" in common
    assert "/var/run/docker.sock" not in common
    assert "/hostfs" not in common
    assert "restart:" not in common
    assert "ports:" not in common
    assert common.count("no-new-privileges:true") == 2
    assert common.count("read_only: true") >= 3


def test_cpu_fixture_has_bounded_resources_and_loopback_only_probe_port() -> None:
    cpu = _text(CPU)

    assert "container_name:" not in cpu
    assert 'image: mini-drop/otel-demo-ad:3684411' in cpu
    assert '127.0.0.1::9555' in cpu
    assert "cpus: 0.50" in cpu
    assert "mem_limit: 300m" in cpu
    assert "pids_limit: 256" in cpu
    assert 'com.mini-drop.fault-window-max-seconds: "120"' in cpu
    assert "flagd:" in cpu
    assert "otel-collector:" in cpu
    assert "0.0.0.0:" not in cpu


def test_memory_fixture_has_abort_boundary_and_loopback_only_probe_port() -> None:
    memory = _text(MEM)

    assert "container_name:" not in memory
    assert 'image: mini-drop/otel-demo-email:3684411' in memory
    assert '127.0.0.1::6060' in memory
    assert "cpus: 0.50" in memory
    assert "mem_limit: 100m" in memory
    assert "pids_limit: 128" in memory
    assert 'com.mini-drop.memory-abort-percent: "80"' in memory
    assert "flagd:" in memory
    assert "otel-collector:" in memory
    assert "0.0.0.0:" not in memory


def test_case_flag_templates_expose_only_required_variants() -> None:
    cpu = json.loads(
        (FIXTURE / "flags" / "t1-cpu-001" / "demo.flagd.json").read_text(
            encoding="utf-8"
        )
    )
    memory = json.loads(
        (FIXTURE / "flags" / "t1-mem-001" / "demo.flagd.json").read_text(
            encoding="utf-8"
        )
    )

    assert cpu["flags"] == {
        "adHighCpu": {
            "defaultVariant": "off",
            "description": "Triggers high CPU load in the isolated ad fixture",
            "state": "ENABLED",
            "variants": {"off": False, "on": True},
        }
    }
    assert memory["flags"] == {
        "emailMemoryLeak": {
            "defaultVariant": "off",
            "description": "Retains generated mail in the isolated email fixture",
            "state": "ENABLED",
            "variants": {"off": 0, "100x": 100},
        }
    }
