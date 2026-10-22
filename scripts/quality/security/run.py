"""执行真实Go HTTP路由+隔离PG安全矩阵，缺少显式测试数据库时失败而不是把skip算通过。

该入口不扫描生产、不模拟HTTP应用、不读取模型密钥。只允许loopback测试PG，测试自行创建
并清除随机schema；公开收据只有测试名/计数/日志摘要，不包含连接串或请求正文。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]


def validate_database(dsn, opted_in):
    parsed = urlsplit(dsn)
    if opted_in != "1" or parsed.scheme not in {"postgres", "postgresql"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"/postgres", "/drop_test", "/drop_jd_test"} or parsed.fragment:
        raise ValueError("需要显式本机隔离PostgreSQL测试连接及DROP_JD_ISOLATED_POSTGRES=1")


def summarize(stdout, stderr, code):
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("Action") in {"pass", "fail", "skip"} and event.get("Test"):
            events.append(event)
    skips = [e["Test"] for e in events if e["Action"] == "skip"]
    fails = [e["Test"] for e in events if e["Action"] == "fail"]
    passed = [e["Test"] for e in events if e["Action"] == "pass"]
    return {"schema_version": 1, "kind": "actual_go_http_isolated_postgres_security_matrix", "passed": code == 0 and bool(passed) and not skips and not fails, "exit_code": code, "tests_passed_including_subtests": len(passed), "skipped_tests": skips, "failed_tests": fails, "passed_test_names": passed, "log_sha256": hashlib.sha256(stdout + stderr).hexdigest(), "limits": ["真实Server.Router与真实PostgreSQL，不是外部DAST或完整渗透测试。", "拒绝请求逐个核对领域全行、事件、outbox与审计快照；正常防滥用计数不属于业务副作用。", "未测试TLS边缘代理、浏览器SameSite行为或生产漏洞扫描。"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        validate_database(os.environ.get("TEST_DATABASE_URL", ""), os.environ.get("DROP_JD_ISOLATED_POSTGRES"))
        if args.output.exists():
            raise ValueError("输出必须为新文件")
        env = dict(os.environ)
        env.pop("TEST_DATABASE_URL_FILE", None)
        for name in ("LIVE_AI_KEY_FILE", "LIVE_AI_REPORT_FILE"):
            env.pop(name, None)
        completed = subprocess.run(["go", "test", "-json", "-count=1", "-timeout=120s", "./internal/httpapi", "-run", "^TestPostgresJDSecurityMatrix"], cwd=ROOT / "api", env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=180, check=False)
        if os.environ["TEST_DATABASE_URL"].encode() in completed.stdout + completed.stderr:
            raise ValueError("测试日志触发凭据拒绝")
        receipt = summarize(completed.stdout, completed.stderr, completed.returncode)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as target:
            json.dump(receipt, target, ensure_ascii=False, indent=2)
            target.write("\n")
        print(json.dumps({"passed": receipt["passed"], "tests_passed": receipt["tests_passed_including_subtests"], "skipped": len(receipt["skipped_tests"])}))
        return 0 if receipt["passed"] else 1
    except (ValueError, OSError, subprocess.TimeoutExpired):
        parser.exit(2, "安全矩阵未完成；检查显式本机测试连接、工具链、超时与输出路径。\n")


if __name__ == "__main__":
    raise SystemExit(main())
