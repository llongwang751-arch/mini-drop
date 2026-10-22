"""回环HTTP适配器：本地issuer验证 + 固定PostgREST转发，不访问真实Supabase。

仅监听当前容器127.0.0.1，不接受任意上游、不跟随重定向、不输出报文或凭据。
此适配器不替代PostgREST对JWT的再次验证，也不授予任何租户/业务角色。
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from local_issuer import verify

MAX_BODY = 262_144


def validate_rest_origin(value):
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"rest", "127.0.0.1", "localhost"}
        or parsed.port not in {3000, None}
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("LOCAL_REST_ORIGIN_REJECTED")
    return value.rstrip("/")


@contextmanager
def gateway(rest_origin, secret, users, counters):
    upstream = validate_rest_origin(rest_origin)

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    opener = build_opener(ProxyHandler({}), NoRedirect())

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            return

        def reply(self, status, value, *, content_type="application/json"):
            raw = (
                value
                if isinstance(value, bytes)
                else json.dumps(value, separators=(",", ":")).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(raw)

        def handle_request(self):
            counters["gateway_requests"] += 1
            if counters["gateway_requests"] > 2048 or len(self.path) > 8192:
                return self.reply(429, {"code": "FIXTURE_BUDGET"})
            if self.command == "GET" and self.path == "/auth/v1/user":
                counters["local_issuer_checks"] += 1
                header = self.headers.get("Authorization", "")
                try:
                    if not header.startswith("Bearer "):
                        raise ValueError("LOCAL_JWT_MISSING")
                    _claims, user = verify(header[7:], secret, users=users)
                except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                    counters["local_issuer_rejections"] += 1
                    return self.reply(401, {"code": "LOCAL_ISSUER_REJECTED"})
                return self.reply(200, user)
            if not self.path.startswith("/rest/v1/") or self.command not in {
                "GET",
                "POST",
                "PATCH",
                "DELETE",
            }:
                return self.reply(404, {"code": "FIXTURE_PATH_REJECTED"})
            suffix = self.path[len("/rest/v1") :]
            if (
                suffix.startswith("//")
                or "\r" in suffix
                or "\n" in suffix
                or urlsplit(suffix).netloc
            ):
                return self.reply(400, {"code": "FIXTURE_PATH_REJECTED"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.reply(400, {"code": "FIXTURE_BODY_REJECTED"})
            if not 0 <= length <= MAX_BODY or self.headers.get("Transfer-Encoding"):
                return self.reply(413, {"code": "FIXTURE_BODY_REJECTED"})
            raw = self.rfile.read(length)
            headers = {
                name: self.headers[name]
                for name in ("Authorization", "Prefer", "Content-Type", "Accept")
                if self.headers.get(name)
            }
            headers["Accept-Encoding"] = "identity"
            request = Request(
                upstream + suffix,
                data=raw if length else None,
                headers=headers,
                method=self.command,
            )
            try:
                response = opener.open(request, timeout=10)
            except HTTPError as error:
                response = error
            except (URLError, OSError):
                return self.reply(503, {"code": "FIXTURE_REST_UNAVAILABLE"})
            with response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY or response.status in {301, 302, 303, 307, 308}:
                    return self.reply(502, {"code": "FIXTURE_REST_RESPONSE_REJECTED"})
                return self.reply(
                    response.status,
                    body,
                    content_type=response.headers.get(
                        "Content-Type", "application/json"
                    ),
                )

        do_GET = handle_request
        do_POST = handle_request
        do_PATCH = handle_request
        do_DELETE = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
