import argparse
import json
import logging
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .analyzer import analyze
from .db import Store
from .planner import plan_collection


LOG = logging.getLogger("minidrop.server")
WEB = Path(__file__).with_name("web")


class App:
    def __init__(self, db_path="data/minidrop.db"):
        self.store = Store(db_path)

    def routes(self, method, path, body, query=None):
        query = query or {}
        parts = path.strip("/").split("/")
        if method == "GET" and path == "/api/health":
            return 200, {"ok": True}
        if method == "GET" and path == "/api/agents":
            self.store.mark_offline()
            return 200, self.store.list_agents()
        if method == "GET" and path == "/api/tasks":
            return 200, self.store.list_tasks()
        if method == "GET" and path == "/api/audit":
            return 200, self.store.list_audit()
        if method == "POST" and path == "/api/natural-language":
            plan = plan_collection(body.get("text", ""), self.store.list_agents())
            task = self.store.create_task(**plan)
            return 201, {"plan": plan, "task": task}
        if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "continuous"]:
            start = float(query["start"][0]) if query.get("start") else None
            end = float(query["end"][0]) if query.get("end") else None
            return 200, self.store.continuous_window(parts[2], start, end)
        if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "tasks"]:
            task = self.store.get_task(parts[2])
            return (200, task) if task else (404, {"error": "task not found"})
        if method == "POST" and path == "/api/tasks":
            for key in ("agent_id", "pid", "duration", "rate", "collector"):
                if key not in body:
                    return 400, {"error": f"missing {key}"}
            task = self.store.create_task(body["agent_id"], int(body["pid"]), max(1, int(body["duration"])),
                                          max(1, int(body["rate"])), body["collector"], body.get("continuous", False))
            return 201, task
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "agents"] and parts[3] == "heartbeat":
            self.store.heartbeat(parts[2], body.get("hostname", "unknown"), body.get("version", "unknown"), body)
            return 200, {"ok": True}
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "agents"] and parts[3] == "claim":
            return 200, self.store.claim_task(parts[2])
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "upload":
            task_id = parts[2]
            self.store.transition(task_id, "UPLOADING", body.get("reason", "agent uploaded raw profile"))
            self.store.set_payload(task_id, "raw_data", body["raw"])
            self.store.set_payload(task_id, "result", analyze(body["raw"]))
            completed = self.store.transition(task_id, "DONE", "analyzer produced visualizations")
            if completed["continuous"]:
                self.store.create_task(completed["agent_id"], completed["pid"], completed["duration"],
                                       completed["rate"], completed["collector"], True)
            return 200, completed
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "stop-continuous":
            return 200, self.store.stop_continuous(parts[2])
        if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "fail":
            return 200, self.store.transition(parts[2], "FAILED", body.get("reason", "unspecified agent failure"))
        return 404, {"error": "route not found"}


class Handler(BaseHTTPRequestHandler):
    app = None

    def log_message(self, fmt, *args):
        LOG.info(json.dumps({"remote": self.client_address[0], "message": fmt % args}))

    def _json(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self):
        rel = "index.html" if self.path in ("/", "/index") else self.path.lstrip("/")
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return False
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/api/") and method == "GET":
            if self._serve_static():
                return
        try:
            size = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(size)) if size else {}
            status, payload = self.app.routes(method, path, body, parse_qs(parsed.query))
            self._json(status, payload)
        except (ValueError, KeyError, ProcessLookupError) as exc:
            self._json(400, {"error": str(exc)})
        except Exception as exc:
            LOG.exception("request failed")
            self._json(500, {"error": str(exc)})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def serve(host="0.0.0.0", port=8080, db_path="data/minidrop.db"):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    Handler.app = App(db_path)
    server = ThreadingHTTPServer((host, port), Handler)
    watcher = threading.Thread(target=lambda: _offline_loop(Handler.app.store), daemon=True)
    watcher.start()
    LOG.info(json.dumps({"event": "server_started", "host": host, "port": port}))
    server.serve_forever()


def _offline_loop(store):
    while True:
        store.mark_offline()
        time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", default="data/minidrop.db")
    args = parser.parse_args()
    serve(args.host, args.port, args.db)
