"""Private-network fault sink for the explicitly isolated recovery regression."""
import json
import hashlib
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

MODE = os.environ.get("FIXTURE_MODE", "mixed")
STATE = {"raw_attempts": 0, "log_attempts": 0, "manifest_attempts": 0, "allow": False}
ROOT = Path("/tmp")
RAW_NAME = os.environ.get("FIXTURE_RAW_NAME", "profile.folded")
if RAW_NAME not in ("profile.folded", "profile.perf-script", "java.folded", "heap.hprof", "native.profile"):
    raise RuntimeError("Only fixed RAW names are accepted by the private fixture")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, status, content=b"", etag=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(content)))
        if status == 429:
            self.send_header("Retry-After", "1")
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path != "/stats":
            return self.reply(404)
        self.reply(200, json.dumps(STATE).encode())

    def do_POST(self):
        if self.path != "/allow":
            return self.reply(404)
        STATE["allow"] = True
        self.reply(200)

    def do_PUT(self):
        name = {"/raw": RAW_NAME, "/log": "runner.log", "/manifest": "collection-manifest.json"}.get(self.path)
        if name is None:
            return self.reply(404)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self.reply(400)
        maximum = {"runner.log": 16384, "collection-manifest.json": 65536}.get(name, 67108864)
        if not 0 < size <= maximum:
            return self.reply(400)
        content = self.rfile.read(size)
        if len(content) != size:
            return self.reply(400)
        key = {"runner.log": "log_attempts", "collection-manifest.json": "manifest_attempts"}.get(name, "raw_attempts")
        STATE[key] += 1
        digest = hashlib.sha256(content).hexdigest()
        STATE.setdefault(key.replace("attempts", "sha256s"), []).append(digest)
        if key == "raw_attempts":
            # Keep every attempted RAW fingerprint to prove recovery reused bytes.
            if MODE == "permanent":
                return self.reply(403)
            if MODE == "restart" and not STATE["allow"]:
                return self.reply(503)
            if MODE == "mixed" and STATE[key] <= 2:
                return self.reply(503 if STATE[key] == 1 else 429)
        elif key == "log_attempts" and MODE == "late_log" and STATE[key] <= 3:
            return self.reply(503)
        elif key == "manifest_attempts":
            if MODE == "manifest_retry" and STATE[key] == 1:
                return self.reply(503)
            if MODE == "manifest_restart" and not STATE["allow"]:
                return self.reply(503)
        (ROOT / name).write_bytes(content)
        # Deliberately opaque: the client must preserve this independently of SHA256.
        etag = '"fixture-' + hashlib.md5(content, usedforsecurity=False).hexdigest() + '"'
        STATE.setdefault("etags", {})[name] = etag
        self.reply(200, etag=etag)


HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
