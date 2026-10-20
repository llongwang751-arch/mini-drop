"""固定的 Saber 组件采样与回归任务，运行于无网络环境，不接受任意命令。

先执行原始测试与冻结契约，再用相同 seed 交替运行基线和候选，降低先后顺序偏差。
CPU 收益只适用于此受控组件基准，不代表整站或生产 P95/P99。
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


PYTHON = "/opt/drop-tools/venv/bin/python"
HARNESS = Path(__file__).resolve().parent


def command(argv, *, source, baseline, python, limit=45):
    """在最小环境中以 argv 运行固定 harness，超时或输出超限返回固定错误码，不泄露子进程文本。"""
    env = {"PATH": os.pathsep.join([str(Path(python).parent), "/usr/bin", "/bin"]),
           "HOME": tempfile.gettempdir(), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(HARNESS),
           "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "DROP_SABER_SOURCE": str(source), "DROP_SABER_BASELINE": str(baseline)}
    if os.name == "nt" and "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            process = subprocess.run(argv, cwd=source, stdout=out, stderr=err, timeout=limit, env=env)
        except subprocess.TimeoutExpired:
            return False, "EXECUTION_TIMEOUT"
        if out.tell() > 65536 or err.tell() > 65536:
            return False, "EXECUTION_OUTPUT_LIMIT"
        return process.returncode == 0, "EXECUTION_FAILED" if process.returncode else ""


def measure(source, output, seed, *, baseline, python, profile=False):
    argv = [python, str(HARNESS / "measure.py"), "--source", str(source), "--output", str(output), "--seed", str(seed)]
    if profile:
        argv.append("--profile")
    ok, code = command(argv, source=source, baseline=baseline, python=python)
    if not ok:
        raise RuntimeError(code)
    return json.loads(output.read_text(encoding="utf-8"))


def main():
    """采样或验证已登记的 FTS5 组件；回归失败立即停止，随后执行六组交替 CPU 对照。

    每组要求精确输出摘要一致，并记录全部测量；门槛未达到时 accepted 为 false，而非把执行成功等同修复有效。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["profile", "test"], required=True)
    parser.add_argument("--workspace", default="/workspace")
    parser.add_argument("--output", default="/output")
    parser.add_argument("--python", default=PYTHON)
    args = parser.parse_args()
    root, out = Path(args.workspace).resolve(), Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    config = json.loads((root / "job.json").read_text(encoding="utf-8"))
    baseline = root / "baseline"
    start = time.monotonic()
    if args.action == "profile":
        result = measure(baseline, out / "baseline-profile.json", config["seed"], baseline=baseline, python=args.python, profile=True)
        result["success"] = True
    else:
        candidate = root / "candidate"
        # 两条原始测试原样执行，仅预加载它们依赖的真实组件；
        # 这不意味着已覆盖完整应用启动或集成测试。
        test_code = ("from saber_component import load_component; import pytest; "
                     "load_component('.', original_namespace=True); "
                     "raise SystemExit(pytest.main(['-q','--noconftest','-p','no:cacheprovider',"
                     + repr(str(candidate / "tests/test_fts5_index.py")) + ","
                     + repr(str(HARNESS / "test_source_contract.py")) + "]))")
        ok, code = command([args.python, "-c", test_code], source=candidate, baseline=baseline, python=args.python, limit=90)
        if not ok:
            raise RuntimeError("SABER_REGRESSION_" + code)
        pairs = []
        for trial in range(6):
            seed = config["seed"] + trial
            sources = ["baseline", "candidate"] if trial % 2 == 0 else ["candidate", "baseline"]
            values = {name: measure(root / name, out / f"{trial}-{name}.json", seed, baseline=baseline, python=args.python) for name in sources}
            if values["baseline"]["output_sha256"] != values["candidate"]["output_sha256"]:
                raise RuntimeError("SABER_OUTPUT_PARITY_FAILED")
            pairs.append({"baseline_ns": values["baseline"]["elapsed_ns"], "candidate_ns": values["candidate"]["elapsed_ns"],
                          "baseline_cpu_ns": values["baseline"]["cpu_ns"], "candidate_cpu_ns": values["candidate"]["cpu_ns"],
                          "cases": values["baseline"]["cases"], "output_sha256": values["baseline"]["output_sha256"],
                          "executed_sql_queries_per_side": values["baseline"]["executed_sql_queries"]})
        if any(item["candidate_cpu_ns"] <= 0 for item in pairs):
            raise RuntimeError("SABER_MEASUREMENT_UNDER_RESOLVED")
        ratios = [item["baseline_cpu_ns"] / item["candidate_cpu_ns"] for item in pairs]
        median = statistics.median(ratios)
        improved = sum(ratio > 1.05 for ratio in ratios)
        accepted = median >= 1.10 and improved >= 5
        result = {"success": True, "functional_regression": "passed", "original_tests": 2, "frozen_contract_tests": 6,
                  "output_parity_cases": sum(item["cases"] for item in pairs), "pairs": pairs,
                  "median_cpu_speedup": median, "improved_pairs": improved, "accepted": accepted,
                  "gate": {"minimum_median_speedup": 1.10, "minimum_pairs_above_1_05": 5, "total_alternating_pairs": 6},
                  "root_cause_status": "validated_in_controlled_saber_benchmark" if accepted else "not_validated",
                  "scope": "Saber FTS5 component on synthetic 1K-8K long queries plus edge cases and real in-memory SQLite searches; excludes index construction and full app, not production P95/P99 or all Saber functions"}
        result["candidate_profile"] = measure(candidate, out / "candidate-profile.json", config["seed"], baseline=baseline, python=args.python, profile=True)
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeError) and str(error).replace("_", "").isupper() else "SANDBOX_FAILED"
        # 遵守代理的固定输出契约；本地覆盖只供运维直接核验，
        # 不接受模型提供的任意路径。
        destination = Path("/output")
        if "--output" in sys.argv:
            destination = Path(sys.argv[sys.argv.index("--output") + 1])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "result.json").write_text(json.dumps({"success": False, "error_code": code}), encoding="utf-8")
        raise SystemExit(1) from None
