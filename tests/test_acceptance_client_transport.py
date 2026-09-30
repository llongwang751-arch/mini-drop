"""Acceptance transport must expose failures without silently rerunning mutations."""

import http.client
import json
import ssl
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.verify_interview_demo import AcceptanceError, Client


@pytest.fixture
def local_api():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path, self.headers.get("X-API-Key")))
            data = b"artifact" if self.path == "/artifact" else json.dumps(
                {"code": 0, "data": {"status": "COMPLETED"}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_explicit_direct_route_reads_api_and_artifact_without_global_proxy(local_api, monkeypatch):
    base, requests = local_api
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: pytest.fail("global route used"))
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:1"})
    client = Client(base, "test-credential", proxy_mode="direct")
    assert client.request("GET", "/diagnosis") == {"status": "COMPLETED"}
    assert client.request_raw("GET", "/artifact") == b"artifact"
    assert requests == [("GET", "/diagnosis", "test-credential"),
                        ("GET", "/artifact", "test-credential")]


def test_direct_route_uses_current_verified_tls_context(monkeypatch):
    client = Client("https://example.invalid", "never-log-this", proxy_mode="direct")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client._context = context  # Cloud provider installs its private trusted CA after creation.
    seen = []

    class Response:
        headers = {}
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def read(self):
            return b'{"code":0,"data":{}}'

    class Opener:
        def open(self, request, timeout):
            seen.append((request.method, timeout))
            return Response()

    def build(*handlers):
        assert handlers[0].proxies == {}
        assert handlers[1]._context is context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert client.request("GET", "/diagnosis", timeout=7) == {}
    assert seen == [("GET", 7)]


@pytest.mark.parametrize("error", [TimeoutError("read timed out"),
                                  ssl.SSLEOFError("EOF"),
                                  http.client.RemoteDisconnected("connection closed")])
@pytest.mark.parametrize("raw", [False, True])
def test_transport_failure_identifies_request_without_retry_or_credential(monkeypatch, error, raw):
    calls = []
    def fail(request, **kwargs):
        calls.append(request.method)
        raise error
    monkeypatch.setattr(urllib.request, "urlopen", fail)
    client = Client("https://example.invalid", "never-log-this")
    with pytest.raises(AcceptanceError) as failure:
        if raw:
            client.request_raw("GET", "/artifact")
        else:
            client.request("POST", "/diagnoses", {})
    assert ("GET /artifact" if raw else "POST /diagnoses") in str(failure.value)
    assert type(error).__name__ in str(failure.value)
    assert "never-log-this" not in str(failure.value)
    assert len(calls) == 1


def test_unknown_proxy_mode_rejected():
    with pytest.raises(ValueError, match="proxy_mode"):
        Client("https://example.invalid", "secret", proxy_mode="automatic-fallback")
