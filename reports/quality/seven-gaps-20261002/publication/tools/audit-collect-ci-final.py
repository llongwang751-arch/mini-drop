"""Download official GitHub CI artifacts without modifying prior evidence."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = STAGE / "ci-artifacts-final"
RUN = 36907241773
HEAD = "35f21b82997349b2f8e8010ae834e27a25fafc8f"
spec = importlib.util.spec_from_file_location("github_ci", ROOT / "output/quality/ci-validation-20260927/github_ci.py")
github = importlib.util.module_from_spec(spec)
spec.loader.exec_module(github)
client = github.session()
client.trust_env = False
api = f"https://api.github.com/repos/{github.REPO}"


def persist(path, raw):
    native = Path("\\\\?\\" + str(path.resolve())) if os.name == "nt" else path
    native.parent.mkdir(parents=True, exist_ok=True)
    if native.exists():
        assert native.read_bytes() == raw, f"Prior evidence differs: {path.name}"
    else:
        native.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def json_get(suffix):
    response = client.get(api + suffix, timeout=40)
    response.raise_for_status()
    return response.json()


with ThreadPoolExecutor(max_workers=3) as pool:
    run, jobs, artifacts = list(pool.map(json_get, [f"/actions/runs/{RUN}", f"/actions/runs/{RUN}/jobs?per_page=100", f"/actions/runs/{RUN}/artifacts?per_page=100"]))
assert run["head_sha"] == HEAD and run["conclusion"] == "success" and run["status"] == "completed"
assert len(jobs["jobs"]) == 14 and all(job["status"] == "completed" and job["conclusion"] == "success" for job in jobs["jobs"])
for name, data in [("run", run), ("jobs", jobs), ("artifacts", artifacts)]:
    persist(DEST / f"official-{name}-{RUN}.json", (json.dumps(data, indent=2) + "\n").encode())
selected = [row for row in artifacts["artifacts"] if row["name"].startswith(("python-quality-", "postgres-concurrency-", "web-browser-", "native-agent-binary-", "hotspot-controls-", "observation-controls-"))]
assert len(selected) == 6, [a["name"] for a in selected]


def artifact(row):
    assert row["expired"] is False and row["workflow_run"]["id"] == RUN
    response = client.get(row["archive_download_url"], timeout=50)
    response.raise_for_status()
    path = DEST / (row["name"] + ".zip")
    digest = persist(path, response.content)
    assert row["digest"] == "sha256:" + digest
    assert row["size_in_bytes"] == len(response.content)
    output = DEST / row["name"]
    files = []
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            target = (output / member.filename).resolve()
            assert target.is_relative_to(output.resolve())
            assert not stat.S_ISLNK(member.external_attr >> 16)
            if member.is_dir():
                continue
            raw = archive.read(member)
            files.append({"path": member.filename, "sha256": persist(target, raw), "size": len(raw)})
    junit = []
    for file in files:
        if not file["path"].endswith(".xml"):
            continue
        tree = ET.parse(output / file["path"])
        cases = tree.findall(".//testcase")
        if not cases:
            continue
        junit.append({"path": file["path"], "tests": len(cases),
                      "passed": sum(not any(case.find(key) is not None for key in ("skipped", "failure", "error")) for case in cases),
                      "skipped": sum(case.find("skipped") is not None for case in cases),
                      "failed": sum(case.find("failure") is not None or case.find("error") is not None for case in cases)})
    return {"id": row["id"], "name": row["name"], "archive_path": path.relative_to(STAGE).as_posix(),
            "archive_sha256": digest, "files": files, "junit": junit}


with ThreadPoolExecutor(max_workers=4) as pool:
    downloaded = list(pool.map(artifact, selected))
ci_heads = {row["name"].rsplit("-", 2)[1] for row in selected}
assert len(ci_heads) == 1
ci_merge = ci_heads.pop()
with ThreadPoolExecutor(max_workers=2) as pool:
    source_commit, ci_commit = list(pool.map(json_get, ["/git/commits/" + HEAD, "/git/commits/" + ci_merge]))
assert source_commit["tree"]["sha"] == ci_commit["tree"]["sha"]
assert HEAD in {row["sha"] for row in ci_commit["parents"]} or ci_merge == HEAD
proof = {"source_head": HEAD, "ci_merge_head": ci_merge,
         "source_tree": source_commit["tree"]["sha"], "ci_tree": ci_commit["tree"]["sha"],
         "same_git_tree": True, "official_run_id": RUN,
         "ci_parent_heads": [row["sha"] for row in ci_commit["parents"]]}
persist(STAGE / "ci-final-source-equivalence.json", (json.dumps(proof, indent=2) + "\n").encode())
for name, doc in [("source", source_commit), ("ci-merge", ci_commit)]:
    persist(DEST / ("official-git-" + name + ".json"), (json.dumps(doc, indent=2) + "\n").encode())



def job_log(job):
    response = client.get(api + f"/actions/jobs/{job['id']}/logs", timeout=50)
    response.raise_for_status()
    path = DEST / "logs" / f"job-{job['id']}.log"
    digest = persist(path, response.content)
    text = re.sub(r"\x1b\[[0-9;]*m", "", response.text)
    lines = [line for line in text.splitlines() if any(word in line for word in ("Tests ", "Test Files ", "tests passed", "passed,", '"passed":', "100% tests passed"))]
    return {"job_id": job["id"], "name": job["name"], "path": path.relative_to(STAGE).as_posix(),
            "sha256": digest, "summary_lines": lines[-12:]}


chosen_jobs = jobs["jobs"]  # Download all fourteen completed official job logs.
with ThreadPoolExecutor(max_workers=4) as pool:
    logs = list(pool.map(job_log, chosen_jobs))
summary = {"schema": "mini-drop.official-ci-evidence.v1", "run_id": RUN,
           "url": run["html_url"], "source_head": HEAD,
           "job_count": len(jobs["jobs"]), "successful_jobs": len(jobs["jobs"]),
           "ci_source_equivalence": json.loads((STAGE / "ci-final-source-equivalence.json").read_text()),
           "artifacts": downloaded, "logs": logs,
           "scope": "Official CI reports; synthetic parser/browser cases and bounded real demo containers; not cloud RCA or an hour test"}
persist(STAGE / "ci-final-evidence-download-inventory.json", (json.dumps(summary, indent=2) + "\n").encode())
print(json.dumps({"run_id": RUN, "jobs": "14/14", "artifact_count": len(downloaded),
                  "junit": [{"artifact": row["name"], "reports": row["junit"]} for row in downloaded],
                  "logs": [{"name": row["name"], "summary_lines": row["summary_lines"]} for row in logs]}, ensure_ascii=False))
