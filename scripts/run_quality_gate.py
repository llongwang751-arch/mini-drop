"""Run versioned risk-based regressions and preserve auditable local/CI results.

No deployment, production traffic, retries or implicit dependency installation.
JUnit test cases, rather than suite summary attributes, determine test outcomes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "contracts/quality_plan.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load_plan(path=PLAN, root=ROOT):
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("schema") != "mini-drop.quality-plan.v1":
        raise ValueError("unsupported quality plan")
    for name, suite in plan["suites"].items():
        if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in name):
            raise ValueError("unsafe suite id")
        cwd = (root / suite["cwd"]).resolve()
        if not cwd.is_relative_to(root.resolve()) or not cwd.is_dir():
            raise ValueError(f"invalid suite directory: {name}")
        if not suite["commands"] or any(not cmd or not all(isinstance(x, str) for x in cmd)
                                        for cmd in suite["commands"]):
            raise ValueError(f"missing command: {name}")
        if not 0 < suite["timeout_seconds"] <= 3600:
            raise ValueError("invalid timeout")
        if not suite["risks"] or set(suite["risks"]) - plan["risks"].keys():
            raise ValueError("unknown risk")
        if suite["report"] not in {"exit-code", "junit", "go-json", "business-campaign"}:
            raise ValueError("unknown report type")
        critical = suite.get("critical_coverage")
        if critical is not None:
            modules_config = critical.get("modules")
            if not isinstance(modules_config, dict) or not modules_config:
                raise ValueError(f"invalid critical coverage modules: {name}")
            for module, floor in modules_config.items():
                if not isinstance(module, str) or not (root / module).is_file():
                    raise ValueError(f"invalid critical coverage module path: {name}")
                if not isinstance(floor, int) or isinstance(floor, bool) or not 0 <= floor <= 100:
                    raise ValueError(f"invalid critical coverage floor: {name}")
        for command in suite["commands"]:
            for part in command:
                if part.startswith("tests/") and not (root / part).is_file():
                    raise ValueError(f"missing test: {part}")
    for suites in plan["profiles"].values():
        if not suites or len(suites) != len(set(suites)) or set(suites) - plan["suites"].keys():
            raise ValueError("invalid profile")
    return plan


def read_junit(path, allowed_skips=()):
    root = ET.parse(path).getroot()
    if root.tag not in {"testsuites", "testsuite"}:
        raise ValueError("not a JUnit report")
    cases = list(root.iter("testcase"))
    result = {"total": len(cases), "passed": 0, "failed": 0, "skipped": 0,
              "unexpected_skips": [], "failures": [], "skips": []}
    for case in cases:
        classname, name = case.get("classname", ""), case.get("name", "")
        identity = f"{classname}::{name}"
        failed = case.find("failure") is not None or case.find("error") is not None
        skipped = case.find("skipped")
        if failed:
            result["failed"] += 1
            result["failures"].append(identity)
        elif skipped is not None:
            result["skipped"] += 1
            reason = skipped.get("message", "") or skipped.text or ""
            result["skips"].append({"test": identity, "reason": reason})
            if not any(classname == rule["class"] and rule["reason"] in reason for rule in allowed_skips):
                result["unexpected_skips"].append(identity)
        else:
            result["passed"] += 1
    # Some runners put infrastructure errors directly below a suite.
    if len(list(root.iter("error"))) + len(list(root.iter("failure"))) > result["failed"]:
        raise ValueError("unattributed JUnit errors")
    if not cases:
        raise ValueError("no test execution")
    return result


def read_go_json(path, allowed_skips=()):
    terminal = {}
    started = set()
    package_failed = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        identity = (event.get("Package", ""), event.get("Test", ""))
        if event.get("Test") and event.get("Action") == "run":
            if identity in started:
                raise ValueError("duplicate Go test start")
            started.add(identity)
        if event.get("Action") == "fail" and not event.get("Test"):
            package_failed = True
        if event.get("Test") and event.get("Action") in {"pass", "fail", "skip"}:
            identity = (event.get("Package", ""), event["Test"])
            if identity in terminal:
                raise ValueError("duplicate Go test result; retries must not erase earlier outcomes")
            terminal[identity] = event["Action"]
    passed = sum(action == "pass" for action in terminal.values())
    if started - terminal.keys():
        raise ValueError("incomplete Go test report: started tests have no result")
    if not terminal:
        raise ValueError("no Go test execution")
    return {"total": len(terminal), "passed": passed,
            "failed": sum(action == "fail" for action in terminal.values()) + int(package_failed),
            "skipped": sum(action == "skip" for action in terminal.values()),
            "unexpected_skips": [name for (_, name), action in terminal.items()
                                 if action == "skip" and name not in allowed_skips],
            "skips": [name for (_, name), action in terminal.items() if action == "skip"],
            "failures": [name for (_, name), action in terminal.items() if action == "fail"]}


def classify(returncodes, counts=None, error=None):
    if error or not returncodes or any(code != 0 for code in returncodes):
        return "FAILED"
    if counts:
        if counts["failed"] or counts["unexpected_skips"] or not counts["passed"]:
            return "FAILED"
        if counts["skipped"]:
            return "PASSED_WITH_SKIPS"
    return "PASSED"


def run_command(command, cwd, log_path, timeout, env):
    """Bound each command and terminate its process tree on timeout/interruption."""
    with log_path.open("wb") as log:
        kwargs = {"start_new_session": True} if os.name != "nt" else {}
        process = subprocess.Popen(command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                   env=env, **kwargs)
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            else:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise


def run_suite(name, spec, output):
    suite_dir = output / name
    suite_dir.mkdir()
    started = time.monotonic()
    result = {"id": name, "title": spec["title"], "risks": spec["risks"],
              "started_at": utc_now(), "returncodes": [], "logs": [], "counts": None}
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["COVERAGE_FILE"] = str(suite_dir / ".coverage")
    try:
        for index, template in enumerate(spec["commands"]):
            command = [part.replace("{python}", sys.executable).replace("{output}", str(suite_dir))
                       for part in template]
            log = suite_dir / f"command-{index + 1}.log"
            result["logs"].append(log.relative_to(output).as_posix())
            remaining = spec["timeout_seconds"] - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("suite budget exhausted")
            code = run_command(command, ROOT / spec["cwd"], log, remaining, env)
            result["returncodes"].append(code)
            if code != 0:
                break
        if spec["report"] == "junit":
            result["counts"] = read_junit(suite_dir / "junit.xml", spec.get("allowed_skips", []))
        elif spec["report"] == "go-json":
            result["counts"] = read_go_json(suite_dir / "command-1.log", spec.get("allowed_skips", []))
        elif spec["report"] == "business-campaign":
            campaign = json.loads((suite_dir / "campaign.json").read_text(encoding="utf-8"))
            # The existing executor validates outcomes against the business plan.
            if campaign.get("status") != "COMPLETED" or not campaign.get("results"):
                raise ValueError("business campaign incomplete")
            result["business_outcomes"] = campaign["results"]
        coverage = suite_dir / "coverage.json"
        if coverage.is_file():
            data = json.loads(coverage.read_text())
            result["coverage"] = {"mode": "OBSERVATION_ONLY", **data["totals"]}
            critical = spec.get("critical_coverage")
            if critical:
                # coverage.py emits OS-specific separators; normalize before matching.
                files = {key.replace("\\", "/"): value for key, value in data.get("files", {}).items()}
                modules = {}
                for module, floor in critical["modules"].items():
                    summary = files.get(module, {}).get("summary")
                    # A module missing from the report was never imported; that is 0, not a pass.
                    percent = summary["percent_covered"] if summary else 0.0
                    modules[module] = {"percent_covered": round(percent, 2), "floor": floor,
                                       "observed": bool(summary)}
                minimum = min(item["percent_covered"] for item in modules.values())
                breaches = sorted(module for module, item in modules.items()
                                  if item["percent_covered"] < item["floor"])
                result["critical_coverage"] = {"mode": "GATED", "minimum": minimum,
                                               "modules": modules, "breaches": breaches}
                if breaches:
                    result["error"] = "CRITICAL_COVERAGE_BELOW_FLOOR: " + ", ".join(breaches)
    except KeyboardInterrupt:
        result["error"] = "INTERRUPTED"
    except (OSError, ValueError, KeyError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    result["status"] = classify(result["returncodes"], result["counts"], result.get("error"))
    result["artifacts"] = {path.relative_to(output).as_posix(): digest(path.read_bytes())
                           for path in suite_dir.iterdir() if path.is_file()}
    return result


def provenance():
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT, stderr=subprocess.DEVNULL)
    info = {"python": platform.python_version(), "platform": platform.platform()}
    try:
        info["git_head"] = git("rev-parse", "HEAD").decode().strip()
        info["tracked_diff_sha256"] = digest(git("diff", "HEAD", "--", "."))
        info["dirty"] = bool(git("status", "--porcelain"))
        # Include new, uncommitted sources without recording credentials or output data.
        paths = git("ls-files", "-z", "--cached", "--others", "--exclude-standard",
                    "server", "analyzer", "tests", "scripts", "contracts", "web/src",
                    "apiserver", "demo", "pyproject.toml", "web/package-lock.json").decode("utf-8").split("\0")
        source_hashes = {p: digest((ROOT / p).read_bytes()) for p in sorted(set(paths))
                         if p and (ROOT / p).is_file() and Path(p).suffix in
                         {".py", ".go", ".js", ".jsx", ".json", ".toml", ".mod", ".sum"}}
        info["source_sha256"] = digest(json.dumps(source_hashes, sort_keys=True).encode())
        info["source_files"] = len(source_hashes)
    except (OSError, subprocess.SubprocessError):
        info["git_status"] = "UNAVAILABLE"
    return info


def summarize(report, plan):
    rows = report["suites"]
    finished = len(rows) == len(report["selected_suites"])
    if report["state"] != "COMPLETED":
        status = "RUNNING"
    elif not finished or any(row["status"] == "FAILED" for row in rows):
        status = "FAILED"
    else:
        status = "PASSED_WITH_SKIPS" if any(row["status"] == "PASSED_WITH_SKIPS" for row in rows) else "PASSED"
    report["status"] = status
    report["risk_results"] = {}
    for risk in plan["risks"]:
        applicable = [row for row in rows if risk in row["risks"]]
        report["risk_results"][risk] = {"description": plan["risks"][risk],
                                        "suites": [row["id"] for row in applicable],
                                        "statuses": [row["status"] for row in applicable] or ["NOT_RUN"]}


def write_reports(output, report):
    serialized = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    pending = output / "report.pending"
    pending.write_text(serialized, encoding="utf-8")
    os.replace(pending, output / "report.json")
    rows = []
    for row in report["suites"]:
        counts = row.get("counts")
        count_text = (f"{counts['passed']} 通过 / {counts['failed']} 失败 / {counts['skipped']} 跳过"
                      if counts else "按检查命令判定；不计作测试用例数")
        links = " ".join(f'<a href="{escape(log, quote=True)}">日志 {i + 1}</a>'
                         for i, log in enumerate(row["logs"]))
        rows.append(f"<tr><td>{escape(row['title'])}</td><td>{row['status']}</td>"
                    f"<td>{escape(count_text)}</td><td>{row['duration_seconds']}s</td><td>{links}</td></tr>")
    risks = "".join(f"<li>{escape(value['description'])}: {escape(', '.join(value['statuses']))}</li>"
                    for value in report["risk_results"].values())
    details = escape(serialized)
    html = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Mini-Drop 质量报告</title>
<style>body{{font:16px/1.6 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#192c3a}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #cbd5df}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}a{{color:#075e9c}}</style>
<h1>Mini-Drop 质量报告</h1><p>{escape(report['profile'])} · <strong>{report['status']}</strong></p>
<p>运行 ID：{report['run_id']}<br>开始：{report['started_at']}</p>
<p>这是仓库回归结果。未执行项、允许跳过项和覆盖率观测不代表生产验收、容量结论或 AI 根因验证。</p>
<table><thead><tr><th>验证项</th><th>状态</th><th>用例</th><th>耗时</th><th>证据</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
<h2>风险映射</h2><ul>{risks}</ul><p><a href="report.json">机器报告 JSON</a></p>
<details><summary>版本、跳过原因、失败项与文件摘要</summary><pre>{details}</pre></details></html>'''
    (output / "report.html").write_text(html, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="smoke")
    parser.add_argument("--output", type=Path, help="New evidence directory; existing paths are never reused")
    parser.add_argument("--list", action="store_true", help="Validate and list profiles without running tests")
    args = parser.parse_args(argv)
    plan = load_plan()
    if args.list:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    if args.profile not in plan["profiles"]:
        parser.error(f"unknown profile; choose {', '.join(plan['profiles'])}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    output = (args.output or ROOT / "output/quality" / run_id).resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema": "mini-drop.quality-report.v1", "run_id": run_id, "state": "RUNNING",
              "profile": args.profile, "started_at": utc_now(), "scope": plan["scope"],
              "plan_sha256": digest(PLAN.read_bytes()), "environment": provenance(),
              "selected_suites": plan["profiles"][args.profile], "suites": []}
    summarize(report, plan)
    write_reports(output, report)
    for name in report["selected_suites"]:
        print(f"[quality] {name}: RUNNING", flush=True)
        result = run_suite(name, plan["suites"][name], output)
        report["suites"].append(result)
        summarize(report, plan)
        write_reports(output, report)
        print(f"[quality] {name}: {result['status']}", flush=True)
        if result.get("error") == "INTERRUPTED":
            break
    report["state"] = "COMPLETED"
    report["finished_at"] = utc_now()
    report["environment_after"] = provenance()
    summarize(report, plan)
    if (report["environment"].get("source_sha256") != report["environment_after"].get("source_sha256")
            or report["plan_sha256"] != digest(PLAN.read_bytes())):
        report["status"] = "FAILED"
        report["error"] = "SOURCE_CHANGED_DURING_RUN; rerun against a stable working tree"
    write_reports(output, report)
    print(f"[quality] {report['status']}: {output / 'report.html'}", flush=True)
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
