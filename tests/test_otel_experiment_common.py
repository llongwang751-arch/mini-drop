from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import otel_experiment_common as common


def test_docker_host_port_parses_ipv4_and_ipv6_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(common, "run_command", lambda *args: "127.0.0.1:49123\n[::1]:49123")

    assert common.docker_host_port("email", 6060) == 49123


def test_docker_host_port_rejects_unparseable_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(common, "run_command", lambda *args: "not-published")

    with pytest.raises(RuntimeError, match="cannot parse docker port"):
        common.docker_host_port("email", 6060)


def test_docker_container_provenance_captures_identity_and_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = [{
        "Id": "container-sha",
        "Name": "/fixture-ad-1",
        "Image": "sha256:image-id",
        "State": {"Pid": 4321},
        "Config": {
            "Image": "mini-drop/otel-demo-ad:3684411",
            "Labels": {
                "com.docker.compose.project": "fixture-project",
                "com.docker.compose.service": "ad",
                "com.docker.compose.config-hash": "service-config-hash",
            },
        },
        "HostConfig": {
            "NanoCpus": 500_000_000,
            "CpuQuota": 0,
            "CpuPeriod": 0,
            "CpusetCpus": "",
            "Memory": 300_000_000,
            "MemorySwap": 600_000_000,
            "PidsLimit": 256,
            "LogConfig": {"Type": "json-file", "Config": {"max-size": "10m"}},
        },
    }]
    image = [{"RepoDigests": ["mini-drop/otel-demo-ad@sha256:repo-digest"]}]

    def fake_command(*args: str) -> str:
        return json.dumps(image if args[1:3] == ("image", "inspect") else container)

    monkeypatch.setattr(common, "run_command", fake_command)

    provenance = common.docker_container_provenance("ad")

    assert provenance["container_id"] == "container-sha"
    assert provenance["container_name"] == "fixture-ad-1"
    assert provenance["host_pid"] == 4321
    assert provenance["image"]["repo_digests"] == [
        "mini-drop/otel-demo-ad@sha256:repo-digest"
    ]
    assert provenance["compose"] == {
        "project": "fixture-project",
        "service": "ad",
        "container_config_hash": "service-config-hash",
    }
    assert provenance["resource_limits"]["nano_cpus"] == 500_000_000
    assert provenance["resource_limits"]["memory_bytes"] == 300_000_000
    assert provenance["resource_limits"]["pids_limit"] == 256
    assert provenance["logging"] == {
        "driver": "json-file",
        "options": {"max-size": "10m"},
    }


@pytest.mark.parametrize("payload", ({"Id": "not-a-list"}, [], ["not-an-object"]))
def test_docker_container_provenance_rejects_invalid_container_inspect(
    monkeypatch: pytest.MonkeyPatch, payload: object,
) -> None:
    monkeypatch.setattr(common, "run_command", lambda *args: json.dumps(payload))

    with pytest.raises(RuntimeError, match="docker inspect returned"):
        common.docker_container_provenance("ad")


def test_docker_container_provenance_rejects_invalid_image_inspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter((
        json.dumps([{"Image": "sha256:image-id"}]),
        json.dumps({"RepoDigests": []}),
    ))
    monkeypatch.setattr(common, "run_command", lambda *args: next(responses))

    with pytest.raises(RuntimeError, match="docker image inspect returned invalid data"):
        common.docker_container_provenance("ad")


def test_compose_config_provenance_hashes_exact_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    common_file = tmp_path / "common.yaml"
    case_file = tmp_path / "case.yaml"
    environment_file = tmp_path / "fixture.env"
    observed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        common,
        "run_command",
        lambda *args: observed.append(args) or "services:\n  ad: {}",
    )

    provenance = common.compose_config_provenance(
        project_name="mini-drop-cpu-r1",
        compose_files=[common_file, case_file],
        environment_file=environment_file,
    )

    assert provenance["project"] == "mini-drop-cpu-r1"
    assert provenance["files"] == [str(common_file.resolve()), str(case_file.resolve())]
    assert provenance["environment_file"] == str(environment_file.resolve())
    assert provenance["sha256"] == (
        "765edc5bb75a1e2635c060e7f684f992f737d51d5bdf37243f8b8a416462cd72"
    )
    assert observed == [(
        "docker", "compose",
        "-f", str(common_file.resolve()),
        "-f", str(case_file.resolve()),
        "--env-file", str(environment_file.resolve()),
        "--project-name", "mini-drop-cpu-r1",
        "config",
    )]


def test_resolve_compose_service_container_uses_exact_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    common_file = tmp_path / "common.yaml"
    case_file = tmp_path / "case.yaml"
    environment_file = tmp_path / "fixture.env"
    observed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        common,
        "run_command",
        lambda *args: observed.append(args) or "container-id\n",
    )

    resolved = common.resolve_compose_service_container(
        project_name="mini-drop-cpu-r1",
        compose_files=[common_file, case_file],
        service="ad",
        environment_file=environment_file,
    )

    assert resolved == "container-id"
    assert observed == [(
        "docker", "compose",
        "-f", str(common_file.resolve()),
        "-f", str(case_file.resolve()),
        "--env-file", str(environment_file.resolve()),
        "--project-name", "mini-drop-cpu-r1",
        "ps", "-q", "--status", "running", "ad",
    )]


@pytest.mark.parametrize("output", ("", "first\nsecond\n"))
def test_resolve_compose_service_container_requires_exactly_one_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, output: str,
) -> None:
    monkeypatch.setattr(common, "run_command", lambda *args: output)

    with pytest.raises(RuntimeError, match="expected one running container"):
        common.resolve_compose_service_container(
            project_name="mini-drop-cpu-r1",
            compose_files=[tmp_path / "case.yaml"],
            service="ad",
        )


@pytest.mark.parametrize(
    ("project_name", "compose_files", "service", "message"),
    (
        ("", [Path("case.yaml")], "ad", "project_name must not be empty"),
        ("project", [], "ad", "compose_files must not be empty"),
        ("project", [Path("case.yaml")], "", "service must not be empty"),
    ),
)
def test_resolve_compose_service_container_validates_scope(
    project_name: str,
    compose_files: list[Path],
    service: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        common.resolve_compose_service_container(
            project_name=project_name,
            compose_files=compose_files,
            service=service,
        )


def test_summarize_samples_handles_empty_input() -> None:
    assert common.summarize_samples([]) == {
        "sample_count": 0,
        "cpu_percent_min": None,
        "cpu_percent_max": None,
        "cpu_percent_mean": None,
        "samples": [],
    }


def test_wait_container_healthy_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter((0.0, 0.1, 1.1))
    monkeypatch.setattr(common.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(common.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(common, "run_command", lambda *args: "starting")

    with pytest.raises(TimeoutError, match="email did not become healthy: starting"):
        common.wait_container_healthy("email", timeout=1)


def test_restart_requires_changed_host_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(common, "host_pid", lambda _container: 123)
    monkeypatch.setattr(common, "run_command", lambda *args: "")
    monkeypatch.setattr(common, "wait_container_healthy", lambda *args, **kwargs: "healthy")

    with pytest.raises(RuntimeError, match="did not change host PID 123"):
        common.restart_and_wait_healthy("email")


def test_resolve_otel_revision_falls_back_to_upload_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    marker = tmp_path / common.OTEL_REVISION_MARKER
    marker.write_text(common.PINNED_OTEL_REVISION + "\n", encoding="utf-8")

    def no_git(*args: str) -> str:
        raise FileNotFoundError("git")

    monkeypatch.setattr(common, "run_command", no_git)

    assert common.resolve_otel_revision(tmp_path) == common.PINNED_OTEL_REVISION


def test_resolve_otel_revision_rejects_marker_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    (tmp_path / common.OTEL_REVISION_MARKER).write_text("0" * 40, encoding="utf-8")
    monkeypatch.setattr(
        common,
        "run_command",
        lambda *args: (_ for _ in ()).throw(FileNotFoundError("git")),
    )

    with pytest.raises(RuntimeError, match="OTel revision mismatch"):
        common.resolve_otel_revision(tmp_path)


def test_atomic_write_json_replaces_destination(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    destination.write_text('{"stale": true}\n', encoding="utf-8")

    common.atomic_write_json(destination, {"fresh": True})

    assert json.loads(destination.read_text(encoding="utf-8")) == {"fresh": True}
    assert not destination.with_suffix(".json.tmp").exists()
