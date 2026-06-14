"""Start a local Mini-Drop server and agent without Docker."""

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def spawn(args, log_name):
    DATA.mkdir(exist_ok=True)
    log = (DATA / log_name).open("a", encoding="utf-8")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, *args],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )


server = spawn(["-m", "minidrop.server", "--db", "data/local-demo.db"], "server.log")
for _ in range(30):
    try:
        urllib.request.urlopen("http://localhost:8080/api/health", timeout=1)
        break
    except OSError:
        time.sleep(0.5)
else:
    raise SystemExit("Server did not become healthy; see data/server.log")

agent = spawn(
    ["-m", "minidrop.agent", "--server", "http://localhost:8080", "--id", "windows-demo-agent"],
    "agent.log",
)
print(f"Mini-Drop running at http://localhost:8080 (server={server.pid}, agent={agent.pid})")
