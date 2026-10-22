"""可选真实AgentScope生成新数据/操作序列；不执行模型给出的代码或断言。

复用现有SDK预算、固定DeepSeek端点、有界传输、密钥读取与安全回执。
没有显式受保护密钥文件不会调用模型；本程序不访问CRM或数据库。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
from unittest.mock import patch

from case_dsl import load_suite, requirement_sha256
from plan_with_agentscope import MODELS, PlanFailure, generate_plan
from run import clean_environment, emit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", required=True, type=Path)
    parser.add_argument("--model", choices=MODELS, default="deepseek-flash")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output = args.output.absolute()
    if output.exists() or any(path.is_symlink() for path in (output, *output.parents)):
        parser.error("必须使用新的非符号链接输出目录")
    output.mkdir(parents=True)
    logging.disable(logging.CRITICAL)
    try:
        with (
            patch.dict(os.environ, clean_environment(), clear=True),
            open(os.devnull, "w", encoding="utf-8") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            value, receipt = asyncio.run(
                generate_plan(
                    args.key_file,
                    args.model,
                    generation_mode="generated_data_sequences",
                )
            )
        emit(output / "case-suite.json", value)
        checked = load_suite(output / "case-suite.json")
        receipt.update(
            schema_version=2,
            generated_case_count=len(checked["cases"]),
            generated_step_count=sum(len(case["steps"]) for case in checked["cases"]),
            suite_file_sha256=hashlib.sha256(
                (output / "case-suite.json").read_bytes()
            ).hexdigest(),
            reviewed_requirements_sha256=requirement_sha256(),
            fixed_catalog_selection=False,
            executable_code_or_oracle_generated=False,
        )
        receipt.pop("selected_case_count", None)
    except PlanFailure as error:
        receipt = error.receipt or {
            "schema_version": 2,
            "success": False,
            "error_code": error.code,
        }
    except Exception:  # noqa: BLE001 -- 只输出固定码，不泄漏密钥或供应商原始响应。
        receipt = {
            "schema_version": 2,
            "success": False,
            "error_code": "CASE_GENERATION_SETUP_FAILED",
        }
    emit(output / "model-receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
