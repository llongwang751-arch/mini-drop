import json
import time
import urllib.request


BASE = "http://localhost:8080/api"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body else None
    request = urllib.request.Request(BASE + path, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


agents = req("/agents")
if not agents:
    raise SystemExit("No agent online. Run docker compose up first.")
task = req("/tasks", "POST", {"agent_id": agents[0]["id"], "pid": 1, "duration": 3, "rate": 10, "collector": "perf"})
print("Created", task["id"])
for _ in range(30):
    task = req("/tasks/" + task["id"])
    print(task["status"], "-", task["reason"])
    if task["status"] in ("DONE", "FAILED"):
        break
    time.sleep(1)
print("Open http://localhost:8080 and select profile", task["id"])
