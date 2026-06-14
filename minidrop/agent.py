import argparse
import json
import logging
import os
import platform
import threading
import time
import urllib.error
import urllib.request

from .collectors import get_collector


LOG = logging.getLogger("minidrop.agent")


def request(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        raw = response.read()
        return json.loads(raw) if raw else None


class Agent:
    def __init__(self, server, agent_id, interval=5):
        self.server = server.rstrip("/")
        self.agent_id = agent_id
        self.interval = interval

    def post(self, path, body):
        return request(self.server + path, "POST", body)

    def heartbeat(self):
        return self.post(f"/api/agents/{self.agent_id}/heartbeat", {
            "hostname": platform.node(), "version": "1.0.0", "platform": platform.platform(), "pid": os.getpid()
        })

    def work_once(self):
        task = self.post(f"/api/agents/{self.agent_id}/claim", {})
        if not task:
            return False
        try:
            raw = get_collector(task["collector"]).collect(task["pid"], task["duration"], task["rate"])
            self.post(f"/api/tasks/{task['id']}/upload", {"raw": raw, "reason": "collector finished; uploading"})
        except Exception as exc:
            LOG.exception("task failed")
            self.post(f"/api/tasks/{task['id']}/fail", {"reason": f"{type(exc).__name__}: {exc}"})
        return True

    def once(self):
        self.heartbeat()
        return self.work_once()

    def _heartbeat_loop(self):
        while True:
            try:
                self.heartbeat()
            except (urllib.error.URLError, TimeoutError) as exc:
                LOG.warning(json.dumps({"event": "heartbeat_failed", "error": str(exc)}))
            time.sleep(self.interval)

    def run(self):
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        while True:
            try:
                self.work_once()
            except (urllib.error.URLError, TimeoutError) as exc:
                LOG.warning(json.dumps({"event": "server_unreachable", "error": str(exc)}))
            time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", default=os.getenv("MINIDROP_SERVER", "http://localhost:8080"))
    parser.add_argument("--id", default=os.getenv("MINIDROP_AGENT_ID", "local-agent"))
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    agent = Agent(args.server, args.id, args.interval)
    agent.once() if args.once else agent.run()
