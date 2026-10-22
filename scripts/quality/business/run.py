"""生成/校验有界业务用例计划，并在临时进程中运行真实 CRM 联系人契约。

所有输出均保留实际失败；不能用再次挑选成功用例覆盖原始报告。
默认不调用模型，外部 AI 计划也只能选择固定场景，固定安全回归始终执行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from contract import (
    CASE_INDEX,
    canonical,
    digest,
    load_plan,
    plan_schema,
    planner_context,
)


def emit(path: Path, value):
    if path.exists() or any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError("OUTPUT_EXISTS_OR_SYMLINK")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def clean_environment() -> dict[str, str]:
    # 使用白名单而不是复制 os.environ，避免把业务、模型、代理、云端密钥带进被测应用。
    allowed = {
        "SystemRoot",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "PATH",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUTF8"] = "1"
    return environment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crm-source", type=Path, help="只读的用户 CRM 源码目录，必须显式指定"
    )
    parser.add_argument("--output", type=Path, help="新的结果目录，不能位于 CRM 源码内")
    parser.add_argument(
        "--plan", type=Path, help="可选、最多 16 KiB 的结构化用例 ID 计划"
    )
    parser.add_argument(
        "--timeout", type=int, default=120, help="子进程总时限，10..180 秒"
    )
    parser.add_argument(
        "--emit-planner-context",
        type=Path,
        help="导出不含源码/凭据的模型用例选择上下文",
    )
    parser.add_argument(
        "--emit-plan-schema", type=Path, help="导出模型输出 JSON Schema"
    )
    args = parser.parse_args(argv)
    if args.emit_planner_context or args.emit_plan_schema:
        if args.emit_planner_context:
            emit(args.emit_planner_context, planner_context())
        if args.emit_plan_schema:
            emit(args.emit_plan_schema, plan_schema())
        return 0
    if args.crm_source is None or args.output is None or not 10 <= args.timeout <= 180:
        parser.error("执行需要 --crm-source、--output，且 --timeout 为 10..180")
    source = args.crm_source.absolute()
    output = args.output.absolute()
    if not source.is_dir() or any(
        item.is_symlink() for item in (source, *source.parents, output, *output.parents)
    ):
        parser.error("源码/输出不得包含符号链接")
    source, output = source.resolve(), output.resolve()
    if (
        output.is_relative_to(source)
        or source.is_relative_to(output)
        or output.exists()
    ):
        parser.error("输出必须是新的目录，且与 CRM 源码目录互不包含")
    case_ids, provenance = load_plan(args.plan)
    tool_files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(__file__).parent.glob("*.py"))
    }
    started = datetime.now(UTC).isoformat()
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="drop-crm-quality-") as temp:
        temporary = Path(temp)
        config_path, result_path = temporary / "input.json", temporary / "result.json"
        config_path.write_bytes(
            canonical({"source": str(source), "case_ids": case_ids})
        )
        try:
            # 只接收有界结构化结果，不落盘原应用 stdout/stderr，避免凭据或无界日志进入产物。
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).with_name("worker.py")),
                    str(config_path),
                    str(result_path),
                ],
                cwd=temporary,
                env=clean_environment(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=args.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            report = {
                "schema_version": 1,
                "summary": {"passed": False},
                "worker_error": "WorkerDeadlineExceeded",
            }
        else:
            if not result_path.is_file() or result_path.stat().st_size > 1_048_576:
                report = {
                    "schema_version": 1,
                    "summary": {"passed": False},
                    "worker_error": "WorkerResultMissingOrOversized",
                    "exit_code": completed.returncode,
                }
            else:
                report = json.loads(result_path.read_text(encoding="utf-8"))
    report.update(
        {
            "started_at_utc": started,
            "finished_at_utc": datetime.now(UTC).isoformat(),
            "plan": provenance,
            "selected_case_ids": case_ids,
            "tool_source_files": tool_files,
            "oracle_revision_sha256": digest(tool_files),
        }
    )
    emit(output / "report.json", report)
    regressions = {
        "schema_version": 1,
        "fixture_origin": "synthetic; no production records",
        "plan": provenance,
        "source_revision_sha256": report.get("source", {}).get("revision_sha256"),
        "oracle_revision_sha256": report["oracle_revision_sha256"],
        "replay": {
            "entrypoint": "scripts/quality/business/run.py",
            "source_argument_required": True,
            "plan": {"schema_version": 1, "case_ids": case_ids},
        },
        "regressions": report.get("mutation_kills", []),
    }
    emit(output / "regressions.json", regressions)
    summary = {
        "passed": report["summary"].get("passed", False),
        **report.get("summary", {}),
        "report_sha256": hashlib.sha256(
            (output / "report.json").read_bytes()
        ).hexdigest(),
        "source_revision_sha256": report.get("source", {}).get("revision_sha256"),
        "model_called_by_runner": False,
        "case_categories": sorted({CASE_INDEX[item][0] for item in case_ids}),
    }
    emit(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
