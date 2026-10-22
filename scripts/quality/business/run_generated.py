"""在真实CRM ASGI/MemoryRepository执行新生成的数据/序列，并由独立需求oracle判定。

此入口不使用47场景ID，不调用模型。生成器输出和断言来源分开；单元、真实模型和数据库
证据必须分别记录。报告不保存任何合成/真实请求正文，只保留散列、操作和固定断言结果。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

from case_dsl import deterministic_suite, load_suite, requirement_sha256, validate_suite
from contract import canonical, digest
from independent_oracle import Oracle
from run import clean_environment, emit
from worker import (
    CONTACT_PATH,
    MUTANTS,
    CaseRun,
    build_app,
    fixture_tables,
    local_only,
    mutation,
    source_manifest,
)


async def execute_generated_case(
    client, repo, steps, counter, *, oracle=None, state_snapshot=None
):
    oracle = oracle or Oracle()
    run = CaseRun(client, repo, counter)
    for index, step in enumerate(steps):
        predicted = oracle.predict(step)
        before = (
            await state_snapshot()
            if state_snapshot
            else digest(repo.tables["contacts"])
        )
        op = step["op"]
        method = {
            "create": "POST",
            "read": "GET",
            "patch": "PATCH",
            "list": "GET",
            "delete": "DELETE",
        }[op]
        path = CONTACT_PATH
        if op in {"read", "patch", "delete"}:
            path += "/" + oracle.resolve(step["target"])
        if op == "list" and step["query"]:
            path += "?" + urlencode(step["query"])
        status, value = await run.request(
            method, path, actor=step["actor"], body=step.get("body")
        )
        run.expect(
            f"step_{index}_fixed_requirement_status_{predicted['status']}",
            status == predicted["status"],
        )
        data = value.get("data")
        if predicted["status"] >= 400:
            after = (
                await state_snapshot()
                if state_snapshot
                else digest(repo.tables["contacts"])
            )
            run.expect(
                f"step_{index}_rejected_business_state_unchanged", before == after
            )
        elif status == predicted["status"]:
            if op == "list":
                run.expect(
                    f"step_{index}_list_tenant_and_limit",
                    isinstance(data, list)
                    and len(data) <= predicted["limit"]
                    and all(
                        row.get("organization_id") == predicted["org"] for row in data
                    ),
                )
            else:
                if op == "create":
                    try:
                        valid_id = str(UUID(data.get("id"))) == data.get("id")
                    except (ValueError, TypeError, AttributeError):
                        valid_id = False
                    run.expect(f"step_{index}_server_generated_uuid", valid_id)
                run.expect(
                    f"step_{index}_fixed_requirement_values",
                    oracle.compare_values(predicted, data),
                )
                if isinstance(data, dict) and (op != "create" or valid_id):
                    oracle.accept(predicted, data)
        # 先记录失败再停止此case，避免错误响应污染独立状态或产生无意义连锁报错。
        if not all(item["passed"] for item in run.checks):
            break
    return {
        "passed": bool(run.checks) and all(item["passed"] for item in run.checks),
        "checks": run.checks,
        "requests": run.trace,
        "input_step_count": len(steps),
        "executed_step_count": len(run.trace),
    }


async def worker(config):
    source = Path(config["source"])
    before = source_manifest(source)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    logging.disable(logging.CRITICAL)
    counter = [0]
    suites = []
    with local_only():
        import httpx
        from crm.repository import MemoryRepository

        app = build_app()
        for name in ("baseline", "sham_wrapper", *MUTANTS):
            results = []
            with mutation(name):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://crm-quality.invalid",
                    trust_env=False,
                ) as client:
                    for case in config["suite"]["cases"]:
                        repo = MemoryRepository(fixture_tables())
                        app.state.repo = repo
                        try:
                            result = await execute_generated_case(
                                client, repo, case["steps"], counter
                            )
                        except Exception as error:  # noqa: BLE001 -- 真实应用异常保留失败，不输出原文。
                            result = {
                                "passed": False,
                                "exception_type": type(error).__name__,
                                "checks": [],
                                "requests": [],
                            }
                        results.append(
                            {"id": case["id"], "case_sha256": digest(case), **result}
                        )
            suites.append(
                {
                    "name": name,
                    "passed": sum(row["passed"] for row in results),
                    "total": len(results),
                    "results": results,
                }
            )
    after = source_manifest(source)
    baseline, sham, *mutants = suites
    baseline_passed_ids = {row["id"] for row in baseline["results"] if row["passed"]}
    kills = [
        {
            "mutation": row["name"],
            "synthetic_mutation": True,
            "killed_by_cases": [
                case["id"]
                for case in row["results"]
                if case["id"] in baseline_passed_ids
                and not case["passed"]
                and not case.get("exception_type")
            ],
        }
        for row in mutants
    ]
    return {
        "schema_version": 2,
        "kind": "actual_crm_generated_data_sequence_acceptance",
        "fixture_origin": "explicit synthetic data and reviewed requirements; real CRM code, ASGI and MemoryRepository",
        "summary": {
            "passed": baseline["passed"] == baseline["total"] == sham["passed"]
            and all(row["killed_by_cases"] for row in kills)
            and before == after,
            "baseline_passed": baseline["passed"],
            "baseline_total": baseline["total"],
            "sham_passed": sham["passed"],
            "synthetic_mutants_killed": sum(
                bool(row["killed_by_cases"]) for row in kills
            ),
            "actual_requests": counter[0],
        },
        "suite_sha256": digest(config["suite"]),
        "reviewed_requirements_sha256": requirement_sha256(),
        "source_manifest_sha256": digest(before),
        "source_unchanged": before == after,
        "suites": suites,
        "mutation_kills": kills,
        "generation": {
            "new_data_and_operation_sequences": True,
            "fixed_catalog_ids_used": False,
            "oracle_accepts_model_assertions": False,
            "model_called_by_executor": False,
        },
        "limits": [
            "This run uses MemoryRepository; PostgreSQL/RLS/authentication evidence is separate",
            "Inputs can contain unseen data and sequences but remain bounded to reviewed contact operations",
            "In-memory synthetic mutations are not discoveries of production vulnerabilities",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crm-source", type=Path)
    parser.add_argument("--suite", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker-config", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-result", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--emit-deterministic-suite", type=Path)
    args = parser.parse_args(argv)
    if args.emit_deterministic_suite:
        value = deterministic_suite()
        validate_suite(value)
        emit(args.emit_deterministic_suite, value)
        return 0
    if args.worker_config:
        try:
            config = json.loads(args.worker_config.read_text(encoding="utf-8"))
            value = asyncio.run(worker(config))
        except Exception as error:  # noqa: BLE001 -- 工人进程只输出固定类别，不泄漏原始异常。
            value = {
                "schema_version": 2,
                "summary": {"passed": False},
                "worker_error": type(error).__name__,
            }
        args.worker_result.write_bytes(canonical(value))
        return 0 if value["summary"]["passed"] else 1
    if not args.crm_source or not args.suite or not args.output:
        parser.error("需要显式CRM源码、DSL suite与新的输出目录")
    source, output = args.crm_source.resolve(), args.output.resolve()
    if (
        not source.is_dir()
        or output.exists()
        or output.is_relative_to(source)
        or source.is_relative_to(output)
        or any(
            path.is_symlink()
            for path in (args.crm_source, args.output, *args.output.parents)
        )
    ):
        parser.error("源码/输出路径不合法或会覆盖既有证据")
    suite = load_suite(args.suite)
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="drop-crm-generated-") as temporary:
        temporary = Path(temporary)
        config, result = temporary / "config.json", temporary / "result.json"
        config.write_bytes(canonical({"source": str(source), "suite": suite}))
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    __file__,
                    "--worker-config",
                    str(config),
                    "--worker-result",
                    str(result),
                ],
                cwd=temporary,
                env=clean_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
            value = (
                json.loads(result.read_text(encoding="utf-8"))
                if result.is_file() and result.stat().st_size <= 1_048_576
                else {
                    "schema_version": 2,
                    "summary": {"passed": False},
                    "worker_error": "WorkerResultMissingOrOversized",
                    "exit_code": completed.returncode,
                }
            )
        except subprocess.TimeoutExpired:
            value = {
                "schema_version": 2,
                "summary": {"passed": False},
                "worker_error": "WorkerDeadlineExceeded",
            }
    value["suite_file_sha256"] = hashlib.sha256(args.suite.read_bytes()).hexdigest()
    value["tool_source_files"] = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }
    emit(output / "report.json", value)
    print(json.dumps(value["summary"]))
    return 0 if value["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
