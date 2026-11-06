#!/usr/bin/env python3
"""为压测预先换取有限数量的真实会话。

为什么不每个虚拟用户各登录一次：``/api/v1/auth/login`` 对来源 IP 限流
10 次/15 分钟。20 个虚拟用户同时登录会把自己锁死在 429 上，测出来的是限流器
而不是平台。这里显式登录 k 次（默认 4，硬上限 8）换 k 个会话，压测侧轮转复用。

凭据只从环境变量或两行文件读取，不落参数、不落日志、不写进会话池；
会话池只保存 Cookie 值与 CSRF token，权限 0600。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import stat
import sys
import urllib.error
import urllib.request

LOGIN_HARD_CAP = 8


def _post_login(host: str, username: str, password: str, insecure: bool) -> tuple[str, str]:
    body = json.dumps({"username": username, "password": password}).encode()
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/v1/auth/login",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    context = None
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=15, context=context) as response:
            payload = json.loads(response.read())
            raw_cookie = response.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"login failed with status {exc.code}; no session written") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"login failed: {exc.reason}; no session written") from None
    data = payload.get("data") or {}
    csrf = data.get("csrf_token")
    if not raw_cookie or not csrf:
        raise SystemExit("login response lacked session cookie or csrf token")
    cookie = raw_cookie.split(";", 1)[0]
    _, _, value = cookie.partition("=")
    if len(value) != 64:
        raise SystemExit("session cookie value has unexpected length")
    return value, csrf


def _read_credentials(path: str) -> tuple[str, str]:
    """从两行文件读凭据：第一行用户名，第二行口令。值不写日志、不回显。"""
    with open(path, encoding="utf-8") as handle:
        lines = [line.rstrip("\r\n") for line in handle]
    values = [line for line in lines if line.strip()]
    if len(values) < 2:
        raise SystemExit(f"{path} must contain username on line 1 and password on line 2")
    return values[0].strip(), values[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="换压测用会话池")
    parser.add_argument("--host", required=True, help="例如 https://82.157.81.79")
    parser.add_argument("--count", type=int, default=4, help=f"会话数量，上限 {LOGIN_HARD_CAP}")
    parser.add_argument("--out", required=True, help="会话池 JSON 输出路径")
    parser.add_argument("--credentials-file", default="", help="两行凭据文件（用户名/口令）")
    parser.add_argument("--insecure", action="store_true", help="不校验 TLS（自签 IP 证书）")
    args = parser.parse_args(argv)

    if not args.host.startswith(("http://", "https://")):
        raise SystemExit("host must be an absolute http(s) URL")
    if not 1 <= args.count <= LOGIN_HARD_CAP:
        raise SystemExit(f"count must be within 1..{LOGIN_HARD_CAP} (login rate limit is 10/15min)")

    if args.credentials_file:
        if not os.path.exists(args.credentials_file):
            raise SystemExit(f"credentials file not found: {args.credentials_file}")
        username, password = _read_credentials(args.credentials_file)
    else:
        username = os.environ.get("DROP_LOADTEST_USERNAME", "")
        password = os.environ.get("DROP_LOADTEST_PASSWORD", "")
    if not username or not password:
        raise SystemExit(
            "provide --credentials-file, or set DROP_LOADTEST_USERNAME and DROP_LOADTEST_PASSWORD"
        )

    sessions = []
    for index in range(args.count):
        cookie, csrf = _post_login(args.host, username, password, args.insecure)
        sessions.append({"label": f"session-{index + 1}", "cookie": cookie, "csrf": csrf})

    tmp = f"{args.out}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"sessions": sessions}, handle, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, args.out)
    print(f"wrote {len(sessions)} session(s) to {args.out} (values not printed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
