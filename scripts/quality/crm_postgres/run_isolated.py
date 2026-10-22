"""仅Linux：使用本机缓存镜像创建一次性PG/PostgREST/CRM验收容器。

只创建带随机名称与本轮标签的测试资源；不连接已有容器/数据库，不拉取镜像，
不挂业务数据卷、Docker socket或主机PID。清理前再次核对标签，保留所有既有镜像和数据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

UTC = timezone.utc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "business"))
from case_dsl import load_suite
from contract import canonical, digest


def docker(*arguments, timeout=30, check=True):
    completed = subprocess.run(
        ["docker", *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if len(completed.stdout) > 2_097_152 or len(completed.stderr) > 2_097_152:
        raise ValueError("DOCKER_METADATA_BOUND")
    if check and completed.returncode:
        raise ValueError("DISPOSABLE_DOCKER_COMMAND_FAILED")
    return completed


def inspect(kind, name):
    result = docker(kind, "inspect", name, check=False)
    if result.returncode:
        return None
    values = json.loads(result.stdout)
    return values[0] if isinstance(values, list) and len(values) == 1 else None


def checked_bundle(path):
    if not path.is_dir() or path.is_symlink():
        raise ValueError("PRIVATE_BUNDLE_REQUIRED")
    manifest = json.loads((path / "manifest.private.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2 or manifest.get("private_only") is not True:
        raise ValueError("PRIVATE_BUNDLE_VERSION")
    for prefix, key in (("crm-source", "snapshot_files"), ("tool", "tool_files")):
        for relative, expected in manifest[key].items():
            candidate = path / prefix / relative
            if (
                candidate.is_symlink()
                or not candidate.resolve().is_relative_to((path / prefix).resolve())
                or hashlib.sha256(candidate.read_bytes()).hexdigest() != expected
            ):
                raise ValueError("PRIVATE_BUNDLE_HASH_MISMATCH")
    suite = load_suite(path / "generated-suite.json")
    if digest(suite) != manifest["generated_suite_sha256"]:
        raise ValueError("PRIVATE_BUNDLE_SUITE_MISMATCH")
    return manifest


def wait_for_bootstrap(marker, runner):
    """只接受本轮worker的小型就绪标志；退出与超时都不提前启动REST。"""
    for _attempt in range(480):
        if marker.exists():
            if (
                marker.is_symlink()
                or not marker.is_file()
                or marker.stat().st_size > 256
            ):
                raise ValueError("DISPOSABLE_BOOTSTRAP_MARKER_BOUND")
            value = json.loads(marker.read_text(encoding="utf-8"))
            if value != {"schema_version": 1, "bootstrap_ready": True}:
                raise ValueError("DISPOSABLE_BOOTSTRAP_MARKER_INVALID")
            return True
        meta = inspect("container", runner)
        if meta is None or meta.get("State", {}).get("Running") is not True:
            return False
        time.sleep(0.5)
    raise ValueError("DISPOSABLE_BOOTSTRAP_TIMEOUT")


def run(bundle, output, *, runner_image, postgres_image, rest_image):
    if sys.platform != "linux" or os.geteuid() != 0:
        raise ValueError(
            "LINUX_OPERATOR_ROOT_REQUIRED_FOR_DISPOSABLE_CONTAINER_OWNERSHIP"
        )
    bundle, output = bundle.resolve(), output.resolve()
    if (
        output.exists()
        or output.is_relative_to(bundle)
        or bundle.is_relative_to(output)
        or any(path.is_symlink() for path in (bundle, output, *output.parents))
    ):
        raise ValueError("NEW_PRIVATE_OUTPUT_REQUIRED")
    manifest = checked_bundle(bundle)
    images = {}
    for kind, name in (
        ("runner", runner_image),
        ("postgres", postgres_image),
        ("postgrest", rest_image),
    ):
        meta = inspect("image", name)
        if not meta:
            raise ValueError("REQUIRED_CACHED_IMAGE_MISSING_" + kind.upper())
        images[kind] = {"requested": name, "image_id": meta["Id"]}
    tag = "drop-crm-quality-" + uuid4().hex[:16]
    network, database, rest, runner = (
        tag + "-net",
        tag + "-db",
        tag + "-rest",
        tag + "-runner",
    )
    output.mkdir(parents=True, mode=0o700)
    os.chown(output, 10001, 10001)
    report = {
        "schema_version": 2,
        "kind": "disposable_crm_postgresql_rest_acceptance",
        "passed": False,
        "started_at_utc": datetime.now(UTC).isoformat(),
        "images": images,
        "bundle_manifest_sha256": hashlib.sha256(
            (bundle / "manifest.private.json").read_bytes()
        ).hexdigest(),
        "source_manifest_sha256": manifest["original_source_manifest_sha256"],
        "generated_suite_sha256": manifest["generated_suite_sha256"],
        "network_scope": "new internal Docker network, no host port publishing",
        "image_downloaded": False,
        "existing_production_resources_targeted": False,
        "cleanup": {},
    }
    with tempfile.TemporaryDirectory(prefix="drop-crm-pg-config-") as temporary:
        temporary = Path(temporary)
        config = {
            "schema_version": 1,
            "disposable_only": True,
            "database_host": "db",
            "database_port": 5432,
            "database_name": "crm_quality",
            "admin_password": secrets.token_urlsafe(40),
            "authenticator_password": secrets.token_urlsafe(40),
            "jwt_secret": secrets.token_urlsafe(48),
            "rest_origin": "http://rest:3000",
        }
        config_path = temporary / "config.json"
        config_path.write_bytes(canonical(config))
        config_path.chmod(0o444)
        pg_env, rest_env = temporary / "postgres.env", temporary / "postgrest.env"
        pg_env.write_text(
            "POSTGRES_DB=crm_quality\nPOSTGRES_PASSWORD="
            + config["admin_password"]
            + "\n",
            encoding="utf-8",
        )
        rest_env.write_text(
            "PGRST_DB_URI=postgresql://drop_crm_authenticator:"
            + config["authenticator_password"]
            + "@db:5432/crm_quality\nPGRST_DB_ANON_ROLE=anon\nPGRST_JWT_SECRET="
            + config["jwt_secret"]
            + "\nPGRST_JWT_AUD=drop-crm-quality\nPGRST_DB_POOL=4\nPGRST_LOG_LEVEL=crit\n",
            encoding="utf-8",
        )
        pg_env.chmod(0o600)
        rest_env.chmod(0o600)
        try:
            docker(
                "network",
                "create",
                "--internal",
                "--label",
                "drop.crm.quality=" + tag,
                network,
            )
            docker(
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                database,
                "--label",
                "drop.crm.quality=" + tag,
                "--network",
                network,
                "--network-alias",
                "db",
                "--memory",
                "768m",
                "--cpus",
                "0.5",
                "--pids-limit",
                "128",
                "--env-file",
                str(pg_env),
                images["postgres"]["image_id"],
            )
            for _attempt in range(60):
                if (
                    docker(
                        "exec",
                        database,
                        "pg_isready",
                        "-U",
                        "postgres",
                        "-d",
                        "crm_quality",
                        check=False,
                    ).returncode
                    == 0
                ):
                    break
                time.sleep(0.5)
            else:
                raise ValueError("DISPOSABLE_POSTGRES_NOT_READY")
            docker(
                "run",
                "-d",
                "--pull",
                "never",
                "--name",
                runner,
                "--label",
                "drop.crm.quality=" + tag,
                "--network",
                network,
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "10001:10001",
                "--memory",
                "512m",
                "--cpus",
                "0.5",
                "--pids-limit",
                "64",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,nodev,size=128m",
                "--mount",
                "type=bind,src=" + str(bundle) + ",dst=/reviewed,readonly",
                "--mount",
                "type=bind,src="
                + str(config_path)
                + ",dst=/run/crm-quality.json,readonly",
                "--mount",
                "type=bind,src=" + str(output) + ",dst=/evidence",
                "--env",
                "PYTHONDONTWRITEBYTECODE=1",
                "--env",
                "PYTHONPATH=",
                "--entrypoint",
                "/opt/drop-tools/venv/bin/python",
                images["runner"]["image_id"],
                "/reviewed/tool/crm_postgres/worker.py",
                "--config",
                "/run/crm-quality.json",
                "--crm-source",
                "/reviewed/crm-source",
                "--suite",
                "/reviewed/generated-suite.json",
                "--output",
                "/evidence/acceptance.json",
                "--bootstrap-ready",
                "/evidence/bootstrap-ready.json",
            )
            report["bootstrap_ready"] = wait_for_bootstrap(
                output / "bootstrap-ready.json", runner
            )
            if report["bootstrap_ready"]:
                docker(
                    "run",
                    "-d",
                    "--pull",
                    "never",
                    "--name",
                    rest,
                    "--label",
                    "drop.crm.quality=" + tag,
                    "--network",
                    network,
                    "--network-alias",
                    "rest",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--memory",
                    "256m",
                    "--cpus",
                    "0.25",
                    "--pids-limit",
                    "64",
                    "--env-file",
                    str(rest_env),
                    images["postgrest"]["image_id"],
                )
            completed = docker("wait", runner, timeout=360, check=False)
            raw_exit = completed.stdout.strip()
            if completed.returncode or not raw_exit.isdigit() or len(raw_exit) > 3:
                raise ValueError("DISPOSABLE_WORKER_EXIT_INVALID")
            report["worker_exit_code"] = int(raw_exit)
            worker_logs = docker(
                "logs",
                runner,
                timeout=30,
                check=False,
            )
            report["worker_output_sha256"] = hashlib.sha256(
                worker_logs.stdout + worker_logs.stderr
            ).hexdigest()
            if (output / "acceptance.json").is_file() and (
                output / "acceptance.json"
            ).stat().st_size <= 1_048_576:
                accepted = json.loads(
                    (output / "acceptance.json").read_text(encoding="utf-8")
                )
                report["passed"] = (
                    report["worker_exit_code"] == 0 and accepted.get("passed") is True
                )
                report["acceptance_sha256"] = hashlib.sha256(
                    (output / "acceptance.json").read_bytes()
                ).hexdigest()
                report["worker_stage"] = accepted.get("stage")
                report["error_type"] = accepted.get("error_type")
                report["sqlstate"] = accepted.get("sqlstate")
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            report["error_type"] = type(error).__name__
            report["error_code"] = (
                str(error)
                if isinstance(error, ValueError)
                else "DISPOSABLE_RUNTIME_INTERRUPTED"
            )
        finally:
            for name in (runner, rest, database):
                meta = inspect("container", name)
                if meta is not None:
                    if (
                        meta.get("Config", {}).get("Labels", {}).get("drop.crm.quality")
                        != tag
                    ):
                        report["cleanup"][name.rsplit("-", 1)[-1]] = (
                            "label_mismatch_preserved"
                        )
                        continue
                    # 只移除本轮新建并再次核对标签的容器及其匿名测试卷，不触碰任何命名业务卷。
                    report["cleanup"][name.rsplit("-", 1)[-1]] = (
                        "removed"
                        if docker(
                            "rm", "--force", "--volumes", name, check=False
                        ).returncode
                        == 0
                        else "unconfirmed"
                    )
            meta = inspect("network", network)
            if (
                meta is not None
                and meta.get("Labels", {}).get("drop.crm.quality") == tag
            ):
                report["cleanup"]["network"] = (
                    "removed"
                    if docker("network", "rm", network, check=False).returncode == 0
                    else "unconfirmed"
                )
    report["finished_at_utc"] = datetime.now(UTC).isoformat()
    report["passed"] = report["passed"] and all(
        value == "removed" for value in report["cleanup"].values()
    )
    with (output / "run-receipt.json").open("x", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--runner-image", default="drop-crm-sandbox:20261020.4")
    parser.add_argument("--postgres-image", default="pgvector/pgvector:pg17")
    parser.add_argument("--rest-image", default="postgrest/postgrest:v13.0.8")
    args = parser.parse_args()
    value = run(
        args.bundle,
        args.output,
        runner_image=args.runner_image,
        postgres_image=args.postgres_image,
        rest_image=args.rest_image,
    )
    print(
        json.dumps(
            {
                "passed": value["passed"],
                "worker_stage": value.get("worker_stage"),
                "error_type": value.get("error_type"),
                "sqlstate": value.get("sqlstate"),
                "cleanup_confirmed": all(
                    item == "removed" for item in value["cleanup"].values()
                ),
            }
        )
    )
    return 0 if value["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
