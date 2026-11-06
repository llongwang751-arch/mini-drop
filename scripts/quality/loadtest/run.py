#!/usr/bin/env python3
"""有界的 Drop HTTP 压测入口：固定 profile、硬上限、产出收据。

设计约束（都在代码里强制，不靠口头约定）：
- 只发只读请求，不存在 --writes 开关；
- 虚拟用户数 ≤ 100、单次总时长 ≤ 600 秒；
- 登录会话 ≤ 8 个（服务端登录限流 10 次/15 分钟）；
- 先自检，再压测，最后合成收据。

用法：::

    python scripts/quality/loadtest/run.py --host https://82.157.81.79 \
        --profile public-step --insecure --out .local/loadtest/20261105-public

    python scripts/quality/loadtest/run.py --host http://127.0.0.1:18080 \
        --profile api-step --sessions 4 --out .local/loadtest/local-api
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from typing import Any

MAX_USERS = 100
MAX_SECONDS = 600
MAX_SESSIONS = 8

PROFILES: dict[str, dict[str, Any]] = {
    # 公开层：无账号可跑，测 TLS 入口 / 静态资源 / SPA / 401 路径
    "public-smoke": {"stages": "2:20", "scenario": "public"},
    "public-step": {"stages": "2:20,5:30,10:30,20:30", "scenario": "public"},
    # 容量探针：只打小正文端点（排除 165KB 的构建产物），并缩短思考时间。
    # 目的是找拐点，不是模拟真人节奏；带宽受限于发压机而不受限于服务端。
    "capacity-step": {
        "stages": "25:30,50:30,100:30",
        "scenario": "public",
        "think_ms": "100,300",
        "exclude_tags": ["assets"],
    },
    # 认证层：需要预登录会话，测登录后读路径
    "api-smoke": {"stages": "2:20", "scenario": "api"},
    "api-step": {"stages": "2:20,5:30,10:30,20:30", "scenario": "api"},
    "api-soak": {"stages": "10:180", "scenario": "api"},
}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def validate(args: argparse.Namespace) -> list[tuple[int, int]]:
    if not args.host.startswith(("http://", "https://")):
        raise SystemExit("--host must be an absolute http(s) URL")
    stages_raw = args.stages or PROFILES[args.profile]["stages"]
    stages: list[tuple[int, int]] = []
    for chunk in stages_raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            users, seconds = (int(part) for part in chunk.split(":"))
        except ValueError:
            raise SystemExit(f"bad stage {chunk!r}; expected users:seconds") from None
        if users < 1 or seconds < 5:
            raise SystemExit(f"stage {chunk!r} below minimum (1 user, 5s)")
        stages.append((users, seconds))
    if not stages:
        raise SystemExit("no stages")
    if any(users > MAX_USERS for users, _ in stages):
        raise SystemExit(f"refusing >{MAX_USERS} virtual users")
    if sum(seconds for _, seconds in stages) > MAX_SECONDS:
        raise SystemExit(f"refusing >{MAX_SECONDS}s total run time")
    if args.sessions and not 1 <= args.sessions <= MAX_SESSIONS:
        raise SystemExit(f"refusing {args.sessions} sessions; login rate limit allows at most {MAX_SESSIONS}")
    if PROFILES[args.profile]["scenario"] == "api" and not args.sessions:
        raise SystemExit(f"profile {args.profile} needs --sessions (pre-login session pool)")
    if args.processes > 1 and os.name == "nt":
        # Locust 的 --processes 在 Windows 上不可用；用它只会得到一个没有 CSV 的空目录。
        raise SystemExit(
            "locust --processes is not supported on Windows; "
            "start two concurrent runs (e.g. 2×50 users) and sum their receipts instead"
        )
    return stages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drop 有界 HTTP 压测")
    parser.add_argument("--host", required=True)
    parser.add_argument("--profile", default="public-step", choices=sorted(PROFILES))
    parser.add_argument("--stages", default="", help="覆盖 profile，格式 users:seconds,...")
    parser.add_argument("--sessions", type=int, default=0, help="预登录会话数量（需要凭据）")
    parser.add_argument("--credentials-file", default="",
                        help="两行凭据文件（用户名/口令）；默认用 .local/loadtest/account.txt")
    parser.add_argument("--out", required=True, help="输出目录（新建或空目录）")
    parser.add_argument("--think-ms", default="",
                        help="思考时间覆盖，例如 50,150；不设则用 profile 默认（交互式节奏）")
    parser.add_argument("--processes", type=int, default=1, choices=(1, 2, 3, 4),
                        help="发压进程数；同负载下 >1 可排除发压机单核瓶颈")
    parser.add_argument("--insecure", action="store_true", help="不校验 TLS（自签 IP 证书）")
    parser.add_argument("--dry-run", action="store_true", help="只做参数自检，不压测")
    args = parser.parse_args(argv)

    stages = validate(args)
    total_seconds = sum(seconds for _, seconds in stages)
    print(f"profile={args.profile} stages={stages} total={total_seconds}s sessions={args.sessions}")

    if args.dry_run:
        print("dry-run: configuration accepted; no traffic sent")
        return 0

    if os.path.exists(args.out) and os.listdir(args.out):
        raise SystemExit(f"output dir {args.out} is not empty; use a new directory")
    os.makedirs(args.out, exist_ok=True)

    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(here)))
    prefix = os.path.join(args.out, "locust")
    extra_out = os.path.join(args.out, "status-summary.json")
    sessions_path = os.path.join(args.out, "sessions.json")

    env = os.environ.copy()
    env["DROP_LOADTEST_STAGES"] = ",".join(f"{users}:{seconds}" for users, seconds in stages)
    env["DROP_LOADTEST_EXTRA_OUT"] = extra_out
    env["DROP_LOADTEST_MAX_USERS"] = str(MAX_USERS)
    env["DROP_LOADTEST_MAX_SECONDS"] = str(MAX_SECONDS)
    if args.insecure:
        env["DROP_LOADTEST_INSECURE"] = "1"
    think_ms = args.think_ms or PROFILES[args.profile].get("think_ms")
    if think_ms:
        env["DROP_LOADTEST_THINK_MS"] = think_ms
    exclude_tags = PROFILES[args.profile].get("exclude_tags") or []

    if args.sessions:
        creds = args.credentials_file or os.path.join(repo_root, ".local", "loadtest", "account.txt")
        login = [sys.executable, os.path.join(here, "login_sessions.py"), "--host", args.host,
                 "--count", str(args.sessions), "--out", sessions_path]
        if os.path.exists(creds):
            login += ["--credentials-file", creds]
        if args.insecure:
            login.append("--insecure")
        print(f"exchanging {args.sessions} test session(s); credential values are not printed")
        result = subprocess.run(login, env=env, check=False)
        if result.returncode != 0:
            raise SystemExit("session exchange failed; nothing was loaded")
        env["DROP_LOADTEST_SESSIONS"] = sessions_path

    started = _now()
    locust_cmd = [
        sys.executable, "-m", "locust", "-f", os.path.join(here, "locustfile.py"),
        "--headless", "--host", args.host, "--csv", prefix, "--only-summary",
        "--stop-timeout", "10",
    ]
    if exclude_tags:
        locust_cmd += ["--exclude-tags", ",".join(exclude_tags)]
    if args.processes > 1:
        locust_cmd += ["--processes", str(args.processes)]
    print("running:", " ".join(locust_cmd))
    result = subprocess.run(locust_cmd, env=env, check=False)
    ended = _now()
    if result.returncode != 0:
        print(f"locust exited with {result.returncode}; keeping CSVs under {args.out}", file=sys.stderr)

    meta = {
        "started_at": started,
        "generated_at": ended,
        "target": args.host,
        "profile": args.profile,
        "scenario": PROFILES[args.profile]["scenario"],
        "sessions": args.sessions,
        "virtual_users": max(users for users, _ in stages),
        "duration_seconds": total_seconds,
        "think_time_ms": think_ms or "default (1000–3000 public / 500–2000 api)",
        "excluded_tags": exclude_tags,
        "insecure": bool(args.insecure),
        "generator_location": "developer workstation, public internet",
        "not_measured": [
            "服务端 CPU / 内存 / IO 采样（本机没有该主机的 SSH 凭据）",
            "PostgreSQL 连接数、锁等待与慢查询统计",
            "SSE 长连接并发（需单独的有界客户端，不在本套件内）",
            "真实浏览器 JS 执行后的 Core Web Vitals（另有 Lighthouse 证据）",
            "容量上限与拐点（本套件只做有界阶梯，不找饱和点）",
        ],
    }
    meta_path = os.path.join(args.out, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)

    receipt_path = os.path.join(args.out, "receipt.json")
    summarize = [sys.executable, os.path.join(here, "summarize.py"),
                 "--stats", f"{prefix}_stats.csv",
                 "--failures", f"{prefix}_failures.csv",
                 "--extra", extra_out,
                 "--meta", meta_path, "--out", receipt_path]
    outcome = subprocess.run(summarize, env=env, check=False)
    if sessions_path and os.path.exists(sessions_path):
        os.remove(sessions_path)  # 会话用完即删，不留在证据目录里
    return outcome.returncode


if __name__ == "__main__":
    sys.exit(main())
