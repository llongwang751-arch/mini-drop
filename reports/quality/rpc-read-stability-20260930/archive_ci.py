"""Archive successful CI for the exact tested client revision, without credentials."""
import hashlib
import importlib.util
import io
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("ci", ROOT / "output/quality/ci-validation-20260927/github_ci.py")
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
client = ci.session()
client.trust_env = False
dest = ROOT / "reports/quality/rpc-read-stability-20260930"
run_id = 36704929921
expected = "67b4288"


def get(path):
    response = client.get(f"https://api.github.com/repos/{ci.REPO}" + path, timeout=45)
    response.raise_for_status()
    return response.json()


run = get(f"/actions/runs/{run_id}")
assert run["head_sha"].startswith(expected)
if run["status"] != "completed":
    print(json.dumps({"run": run_id, "status": run["status"]}))
    raise SystemExit(2)
assert run["conclusion"] == "success"
jobs = get(f"/actions/runs/{run_id}/jobs")
assert len(jobs["jobs"]) == 13 and all(j["conclusion"] == "success" for j in jobs["jobs"])
artifacts = get(f"/actions/runs/{run_id}/artifacts")
for name, value in [("ci-run.json", run), ("ci-jobs.json", jobs), ("ci-artifacts.json", artifacts)]:
    assert not (dest / name).exists()
    (dest / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
entries = []
for artifact in artifacts["artifacts"]:
    if not artifact["name"].startswith(("python-quality-", "postgres-concurrency-", "business-measurements-", "web-browser-")):
        continue
    response = client.get(artifact["archive_download_url"], timeout=90)
    response.raise_for_status()
    data = response.content
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.testzip() is None
    name = "ci-" + re.sub(r"-[0-9a-f]{40}(?:-\d+)?$", "", artifact["name"]) + ".zip"
    assert not (dest / name).exists()
    (dest / name).write_bytes(data)
    entries.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
(dest / "ci-archive-manifest.json").write_text(json.dumps(entries, indent=2), encoding="utf-8")
print(json.dumps({"run": run_id, "jobs": 13, "archives": len(entries), "head": run["head_sha"]}))
