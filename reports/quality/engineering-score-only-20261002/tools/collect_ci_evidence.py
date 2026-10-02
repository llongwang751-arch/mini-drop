"""Collect official reports only; never modify Git, services or prior evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
DEST = STAGE / "ci-evidence"
RUN = 36966142203
HEAD = "de094fff754f2d8cb7139dd99e31c7a30d5fb754"
spec = importlib.util.spec_from_file_location("github_ci", ROOT / "output/quality/ci-validation-20260927/github_ci.py")
github = importlib.util.module_from_spec(spec)
spec.loader.exec_module(github)
client = github.session()
client.trust_env = False
api = "https://api.github.com/repos/" + github.REPO


def persist(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        assert path.read_bytes() == raw, "Prior evidence differs: " + path.name
    else:
        path.write_bytes(raw)
    return {"path": path.relative_to(DEST).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}


def persist_json(name, data):
    return persist(DEST / name, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def json_get(suffix):
    response = client.get(api + suffix, timeout=45)
    response.raise_for_status()
    return response.json()


with ThreadPoolExecutor(max_workers=3) as pool:
    run, jobs, artifacts = list(pool.map(json_get, [f"/actions/runs/{RUN}",
        f"/actions/runs/{RUN}/jobs?per_page=100", f"/actions/runs/{RUN}/artifacts?per_page=100"]))
assert run["head_sha"] == HEAD and run["status"] == "completed" and run["conclusion"] == "success"
assert jobs["total_count"] == len(jobs["jobs"]) == 14
assert all(job["status"] == "completed" and job["conclusion"] == "success" for job in jobs["jobs"])
metadata_files = [persist_json("official-run.json", run), persist_json("official-jobs.json", jobs),
                  persist_json("official-artifacts.json", artifacts)]

REPORTS = {
    "python-quality-": {"python-all/junit.xml", "report.json"},
    "postgres-concurrency-": {"python.junit.xml", "gate.json", "runtime.json", "go-version.txt"},
    "web-browser-": {"result.json"},
    "native-agent-binary-": {"ctest.xml"},
    "hotspot-controls-": {"report.json", "go-io-window/report.json", "hotspot-controls/measurement/report.json",
                          "hotspot-controls/measurement/python/report.json", "hotspot-controls/measurement/go/report.json"},
    "observation-controls-": {"python/report.json", "java/report.json", "cpp/report.json", "perf-source-toolchain/report.json"},
}
selected = [row for row in artifacts["artifacts"] if any(row["name"].startswith(prefix) for prefix in REPORTS)]
assert len(selected) == len(REPORTS) == 6, [row["name"] for row in selected]


def artifact(row):
    assert not row["expired"] and row["workflow_run"]["id"] == RUN
    prefix = next(prefix for prefix in REPORTS if row["name"].startswith(prefix))
    response = client.get(row["archive_download_url"], timeout=90)
    response.raise_for_status()
    raw = response.content
    digest = hashlib.sha256(raw).hexdigest()
    assert row["digest"] == "sha256:" + digest
    assert row["size_in_bytes"] == len(raw)
    files, member_names = [], []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for member in archive.infolist():
            if member.is_dir():
                continue
            name = PurePosixPath(member.filename)
            assert not name.is_absolute() and ".." not in name.parts
            assert not stat.S_ISLNK(member.external_attr >> 16)
            member_names.append(member.filename)
            if member.filename in REPORTS[prefix]:
                path = DEST / "reports" / row["name"] / member.filename
                files.append({"archive_member": member.filename, **persist(path, archive.read(member))})
    assert {file["archive_member"] for file in files} == REPORTS[prefix]
    return {"artifact_id": row["id"], "name": row["name"], "prefix": prefix,
            "download_sha256": digest, "download_bytes": len(raw),
            "official_digest_verified": True, "official_size_verified": True,
            "archive_retained": False, "archive_member_count": len(member_names),
            "retained_report_count": len(files), "omitted_member_count": len(member_names) - len(files),
            "files": files}


with ThreadPoolExecutor(max_workers=4) as pool:
    downloaded = list(pool.map(artifact, selected))
merge_heads = {row["name"].rsplit("-", 2)[1] for row in selected}
assert len(merge_heads) == 1
merge_head = merge_heads.pop()
with ThreadPoolExecutor(max_workers=2) as pool:
    source_commit, merge_commit = list(pool.map(json_get, ["/git/commits/" + HEAD, "/git/commits/" + merge_head]))
assert source_commit["tree"]["sha"] == merge_commit["tree"]["sha"]
assert merge_head == HEAD or HEAD in {row["sha"] for row in merge_commit["parents"]}
local_tree = subprocess.run(["git", "rev-parse", HEAD + "^{tree}"], cwd=ROOT,
                            text=True, capture_output=True, timeout=15, check=True).stdout.strip()
assert local_tree == source_commit["tree"]["sha"]
metadata_files += [persist_json("official-source-commit.json", source_commit),
                   persist_json("official-ci-merge-commit.json", merge_commit)]
proof = {"source_head": HEAD, "source_tree": source_commit["tree"]["sha"],
         "ci_merge_head": merge_head, "ci_merge_tree": merge_commit["tree"]["sha"],
         "ci_parent_heads": [row["sha"] for row in merge_commit["parents"]],
         "local_source_tree": local_tree, "same_git_tree": True}
metadata_files.append(persist_json("source-equivalence.json", proof))


def job_log(job):
    response = client.get(api + f"/actions/jobs/{job['id']}/logs", timeout=90)
    response.raise_for_status()
    file = persist(DEST / "logs" / f"job-{job['id']}.log", response.content)
    plain = re.sub(r"\x1b\[[0-9;]*m", "", response.text)
    lines = [line for line in plain.splitlines() if any(word in line for word in
             ("Tests ", "Test Files ", "passed,", "100% tests passed", '"passed":true', '"passed": true'))]
    return {"job_id": job["id"], "name": job["name"], **file, "summary_lines": lines[-12:]}


with ThreadPoolExecutor(max_workers=4) as pool:
    logs = list(pool.map(job_log, jobs["jobs"]))


def report_file(prefix, member):
    row = next(row for row in downloaded if row["prefix"] == prefix)
    file = next(file for file in row["files"] if file["archive_member"] == member)
    raw = (DEST / file["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == file["sha256"] and len(raw) == file["bytes"]
    return file, raw


def document(prefix, member):
    return json.loads(report_file(prefix, member)[1])


def junit(prefix, member):
    file, raw = report_file(prefix, member)
    tree = ET.fromstring(raw)
    cases = tree.findall(".//testcase")
    assert cases
    counts = {"executed": len(cases),
              "passed": sum(not any(case.find(state) is not None for state in ("skipped", "failure", "error")) for case in cases),
              "skipped": sum(case.find("skipped") is not None for case in cases),
              "failed": sum(case.find("failure") is not None or case.find("error") is not None for case in cases),
              "report": file["path"], "report_sha256": file["sha256"]}
    assert counts["executed"] == counts["passed"] + counts["skipped"] + counts["failed"]
    assert counts["failed"] == 0
    return counts


counts = {"python": junit("python-quality-", "python-all/junit.xml"),
          "postgres_python": junit("postgres-concurrency-", "python.junit.xml"),
          "native_ctest": junit("native-agent-binary-", "ctest.xml")}
assert counts["postgres_python"]["passed"] == 14 and counts["postgres_python"]["skipped"] == 0
assert counts["native_ctest"]["passed"] == 5 and counts["native_ctest"]["skipped"] == 0
web_log = next(row for row in logs if row["name"] == "Web (React)")
test_lines = [line for line in web_log["summary_lines"] if re.search(r"\bTests\s+\d+ passed", line)]
file_lines = [line for line in web_log["summary_lines"] if re.search(r"Test Files\s+\d+ passed", line)]
assert test_lines and file_lines
line = test_lines[-1]
web_passed = int(re.search(r"\bTests\s+(\d+) passed", line)[1])
web_skipped = int(re.search(r"(\d+) skipped", line)[1]) if re.search(r"(\d+) skipped", line) else 0
web_failed = int(re.search(r"(\d+) failed", line)[1]) if re.search(r"(\d+) failed", line) else 0
web_total = int(re.search(r"\((\d+)\)", line)[1])
assert web_passed == web_total == 317 and web_skipped == web_failed == 0
counts["web_vitest"] = {"executed": web_total, "passed": web_passed, "skipped": web_skipped, "failed": web_failed,
                        "test_files": int(re.search(r"Test Files\s+(\d+) passed", file_lines[-1])[1]),
                        "source": web_log["path"], "source_sha256": web_log["sha256"], "terminal_summary": line,
                        "source_kind": "OFFICIAL_JOB_LOG_VITEST_TERMINAL_SUMMARY"}
browser = document("web-browser-", "result.json")
assert browser["passed"] and browser["scope"] == "LOCAL_SYNTHETIC_UI_FIXTURES"
assert browser["browserExceptions"] == [] and browser["legacyAuditRequests"] == 0
assert len(browser["checks"]) == 8 and browser["engineeringGrades"] == {"accepted": 21, "localized": 6, "refuted": 8}
counts["chromium"] = {"passed": len(browser["checks"]), "failed": 0, "skipped": 0,
                      "browser_exceptions": 0, "legacy_score_requests": 0, "scope": browser["scope"],
                      "report": report_file("web-browser-", "result.json")[0]["path"]}
pg_gate = document("postgres-concurrency-", "gate.json")
runtime_reports = {runtime: document("observation-controls-", runtime + "/report.json") for runtime in ("python", "java", "cpp")}
assert all(row["passed"] and row["cleanup_verified"] for row in runtime_reports.values())
smoke = document("observation-controls-", "perf-source-toolchain/report.json")
assert smoke["passed"] and smoke["cleanup_verified"] and smoke["scope"] == "TOOLCHAIN_SMOKE"
assert smoke["live_perf_sampling"] is False and smoke["causal_root_cause_verified"] is False
for row in logs:
    raw = (DEST / row["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row["sha256"] and len(raw) == row["bytes"]
summary = {"schema": "mini-drop.official-ci-main-reports.v1", "status": "VERIFIED",
           "collected_at": datetime.now(timezone.utc).isoformat(), "run_id": RUN, "run_url": run["html_url"],
           "source_head": HEAD, "successful_jobs": 14, "job_count": 14, "source_equivalence": proof,
           "main_suite_counts": counts, "metadata_files": metadata_files, "artifacts": downloaded, "logs": logs,
           "postgres_gate": pg_gate,
           "runtime_observation_reports": {runtime: {"passed": row["passed"], "cleanup_verified": row["cleanup_verified"],
               "report": report_file("observation-controls-", runtime + "/report.json")[0]["path"]} for runtime, row in runtime_reports.items()},
           "perf_source_toolchain": {"passed": True, "cleanup_verified": True, "scope": "TOOLCHAIN_SMOKE",
               "live_perf_sampling": False, "causal_root_cause_verified": False,
               "report": report_file("observation-controls-", "perf-source-toolchain/report.json")[0]["path"]},
           "evidence_retention": "Official ZIP bytes validated against API digest/size in memory; only selected main reports retained. Archives, binary copies, screenshots and auxiliary fixture XML omitted.",
           "counting_boundary": "Only actual main suite JUnit and final Vitest/Chromium summaries counted. Skips never count as passes; parser subsets and auxiliary XML are not added.",
           "scope": "Official CI; browser is synthetic UI regression and toolchain is non-live smoke. No deployment, cloud diagnosis, fault injection or hour test."}
persist_json("summary.json", summary)
lines = ["# 精确 CI 主报告核对", "", f"提交 `{HEAD}`，官方 [CI {RUN}]({run['html_url']}) **14/14 作业成功**。", "",
         "| 主报告 | 通过 | 跳过 | 失败 |", "|---|---:|---:|---:|"]
for name, row in counts.items():
    lines.append(f"| {name} | {row['passed']} | {row['skipped']} | {row['failed']} |")
lines += ["", f"源码及 CI merge `{merge_head}` 的 Git tree 都是 `{local_tree}`，本地只读 Git 核对一致。", "",
          "Web 数量来源为官方 Vitest 作业终态日志；Chromium 使用合成 API，旧成绩请求为 0。", "",
          "六份 ZIP 下载 SHA 与官方 digest/size 一致，仅保留主报告原字节及十四份官方日志；辅助 XML、重复二进制、ZIP 与截图不落盘。跳过及 Python parser 子集不重复计分。", "",
          "三个真实 demo 镜像的观测与清理通过；perf 源码工具链仅为 TOOLCHAIN_SMOKE，不能称现场采样或因果根因。没有运行部署、云端故障或一小时压测。"]
persist(DEST / "summary.md", ("\n".join(lines) + "\n").encode("utf-8"))
print(json.dumps({"status": "VERIFIED", "jobs": "14/14", "counts": counts, "source_equivalence": proof,
                  "report_files": sum(row["retained_report_count"] for row in downloaded),
                  "logs": len(logs), "summary": str((DEST / "summary.json").resolve())}, ensure_ascii=False))
