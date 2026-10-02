import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import run_distributed_endurance as distributed


def ready():
    return {"run_id": "a" * 32, "machine_id_sha256": "b" * 64,
            "source_hashes": {"fixture": "hash"}, "dataset_sha256": distributed.DATASET_SHA,
            "pid": 123, "port": 8080}


def test_remote_identity_requires_distinct_machines_and_frozen_source():
    distributed.validate_identity(ready(), "c" * 64, {"fixture": "hash"}, "a" * 32)


@pytest.mark.parametrize("key,value", [
    ("run_id", "wrong"), ("machine_id_sha256", "c" * 64),
    ("machine_id_sha256", ""), ("source_hashes", {"fixture": "changed"}),
    ("dataset_sha256", "changed"), ("pid", True), ("pid", 0),
    ("port", 0), ("port", 65536), ("port", True),
])
def test_mismatched_or_invalid_identity_rejected(key, value):
    observation = ready()
    observation[key] = value
    with pytest.raises(ValueError):
        distributed.validate_identity(observation, "c" * 64, {"fixture": "hash"}, "a" * 32)


@pytest.mark.parametrize("host", ["-oProxyCommand=x", "root@host;echo", "a b", "a\nwhoami"])
def test_ssh_option_and_shell_injection_rejected(host):
    with pytest.raises(ValueError):
        distributed.ssh_base(host)


def test_ssh_requires_known_host_and_noninteractive_login():
    options, destination = distributed.ssh_base("root@worker", Path("key with spaces"))
    assert "StrictHostKeyChecking=yes" in options
    assert "BatchMode=yes" in options
    assert options[-1] == "key with spaces"
    assert destination == "root@worker"


def test_contiguous_remote_windows_cover_full_load():
    distributed.validate_windows([
        {"phase": "step-1", "started": 2.0, "ended": 8.1},
        {"phase": "soak", "started": 8.1, "ended": 38.2},
    ], [("step-1", 5, 6), ("soak", 5, 30)])


@pytest.mark.parametrize("windows", [[],
    [{"phase": "soak", "started": 1, "ended": 29}],
    [{"phase": "soak", "started": 1, "ended": float("nan")}],
    [{"phase": "soak", "started": True, "ended": 32}],
    [{"phase": "wrong", "started": 1, "ended": 32}],
])
def test_incomplete_or_invalid_remote_windows_rejected(windows):
    with pytest.raises(ValueError):
        distributed.validate_windows(windows, [("soak", 5, 30)])


def test_remote_phase_gap_rejected():
    with pytest.raises(ValueError):
        distributed.validate_windows([
            {"phase": "step-1", "started": 0, "ended": 6},
            {"phase": "soak", "started": 7, "ended": 37}],
            [("step-1", 5, 6), ("soak", 5, 30)])


@pytest.mark.parametrize("finish", [True, False])
def test_real_fixture_finishes_or_cleans_up_on_stdin_eof(tmp_path, finish):
    output = tmp_path / "remote"
    errors = (tmp_path / "fixture-stderr.log").open("w", encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-u", str(Path(distributed.__file__)),
        "--serve", "--output", str(output), "--run-id", "a" * 32],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
        text=True, encoding="utf-8")
    try:
        channel = distributed.Channel(child)
        try:
            identity = channel.receive(timeout=20)
        except Exception:
            errors.flush()
            pytest.fail((tmp_path / "fixture-stderr.log").read_text(encoding="utf-8"))
        assert identity["pid"] == child.pid
        assert identity["machine_id_sha256"] == distributed.machine_identity()
        # Test protocol locally but never label it as a distinct-host campaign.
        import time
        now = time.perf_counter()
        row = distributed.request(f"http://127.0.0.1:{identity['port']}/query", 0, now, now, 5)
        assert row["success"] and row["quality_passed"], row
        if finish:
            channel.send({"phase": "soak"})
            assert channel.receive()["phase"] == "soak"
            channel.send({"finish": True})
        else:
            child.stdin.close()
        final = channel.receive()
        assert final["pid"] == identity["pid"]
        assert final["resources"]
        assert child.wait(timeout=10) == 0
        assert (output / "completed.json").exists()
        import socket
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", identity["port"])) != 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        child.stdout.close()
        errors.close()


def test_distributed_threshold_cannot_be_relaxed(tmp_path):
    with pytest.raises(ValueError, match="200ms"):
        distributed.run(tmp_path / "unused", distributed.Plan(p95_limit_ms=500),
                        "worker", "/source", "/evidence")
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize('count',[True,0,9])
def test_ssh_transport_pool_rejects_unsafe_counts_before_creating_fixture(tmp_path,count):
    from scripts.run_distributed_endurance import run
    from scripts.run_load_endurance import Plan
    with pytest.raises(ValueError,match='SSH tunnel count'):
        run(tmp_path/'evidence',Plan(),'ubuntu@host','/source','/output',tunnel_count=count)
    assert not (tmp_path/'evidence').exists()
