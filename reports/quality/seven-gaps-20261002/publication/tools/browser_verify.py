"""Read-only deployed browser audit. Run only after the new campaign is published.

Expected grades come from the source contract/generator and pinned evidence.
API credentials stay in process environment and CDP memory; neither Python nor
Chrome disables certificate verification. This helper never starts diagnoses.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent
NEW_SCENARIOS = (
    "source-hotspot", "cpp-cpu-hotspot", "cpp-lock-contention",
    "io-write-latency", "java-file-io", "cpp-file-io", "noisy-neighbor",
)
sys.path.insert(0, str(ROOT))


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def public_expectations():
    from scripts.build_engineering_diagnosis import generate

    expected = json.loads(generate(ROOT))
    generated = read_json(ROOT / "web/public/report-assets/engineering-diagnosis/index.json")
    if generated != expected:
        raise RuntimeError("Generated engineering index is stale")
    contract = read_json(ROOT / "contracts/engineering_diagnosis.json")
    campaign = contract.get("current_campaign_id")
    if campaign != "seven-gaps-20261002":
        raise RuntimeError("Publish this seven-gap campaign contract before browser verification")
    specs = {item["scenario_id"]: item for item in contract["scenarios"]}
    rows = {item["scenario_id"]: item for item in expected["cases"]}
    new_cases = []
    for sid in NEW_SCENARIOS:
        spec, result = specs[sid], rows[sid]
        if result.get("campaign_id") != campaign or result.get("fresh_live_run") is not True:
            raise RuntimeError("Scenario does not belong to the newly published live campaign: " + sid)
        if spec.get("requires_verified_downloads") is not True:
            raise RuntimeError("New campaign requires actual verified artifact downloads: " + sid)
        file = ROOT / spec["case_path"]
        raw = file.read_bytes()
        if hashlib.sha256(raw).hexdigest() != spec["case_sha256"]:
            raise RuntimeError("Pinned case SHA mismatch: " + sid)
        case = json.loads(raw)
        tasks = {row["task_id"]: row for row in case["records"]["tasks"]}
        artifact_metadata = {
            (tid, item["id"], item["artifact_type"]): item["sha256"]
            for tid, task in tasks.items() for item in task["artifacts"]
        }
        downloads = []
        for download in case["downloads"]:
            identity = (download["task_id"], download["artifact_id"], download["artifact_type"])
            artifact_file = file.parent / (sid + "-artifacts") / download["file"]
            digest = hashlib.sha256(artifact_file.read_bytes()).hexdigest()
            if digest != download["sha256"] or digest != artifact_metadata[identity]:
                raise RuntimeError("Raw archive/metadata SHA mismatch: " + sid)
            downloads.append({**download, "bytes": artifact_file.stat().st_size})
        evidence = case["records"].get("evidence", [])
        completed_task_ids = [
            item["task_id"] for item in case["records"].get("tool-calls", [])
            if item.get("task_id") and item.get("status") == "COMPLETED"
        ]
        new_cases.append({
            **result, "case_path": spec["case_path"], "downloads": downloads,
            "evidence_ids": [item["evidence_id"] for item in evidence],
            "task_ids": list(tasks), "completed_task_ids": completed_task_ids,
        })
    prior = ROOT / "output/acceptance/interview-completion-20261001"
    health = []
    for folder, name, label in [
        ("go-normal", "normal", "检查结果：正常"),
        ("go-anomaly", "anomaly", "检查结果：异常"),
        ("go-recovery", "recovery", "检查结果：正常"),
        ("go-interrupted", "unknown", "检查结果：无法判断"),
        ("go-resampled", "resampled", "检查结果：正常"),
    ]:
        record = read_json(prior / folder / "records.json")
        health.append({"name": name, "diagnosis_id": record["diagnosis"]["diagnosis_id"], "label": label})
    business = read_json(prior / "business-fix-r3/validated-comparison.json")
    return {
        "schema": "mini-drop.seven-gap-browser-expectations.v1",
        "current": expected, "new_cases": new_cases, "health_cases": health,
        "old_paths": [rows[sid] for sid in ("go-cpu-hotspot", "go-network-latency", "go-file-io")],
        "business_diagnosis_id": business["diagnosis_id"],
        "historical": read_json(ROOT / "web/public/report-assets/performance-audit/index.json"),
        "engineering": read_json(ROOT / "web/public/report-assets/engineering-cases/index.json"),
        "scope": "Read-only browser rendering and fresh authenticated artifact SHA downloads; no fault injection, new diagnosis, causal grade or hour test",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=STAGE / "browser")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("Choose a fresh output directory; existing browser evidence is preserved")
    expected = public_expectations()
    output.mkdir(parents=True)
    expectation_path = output / "expected-public.json"
    expectation_path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.prepare_only:
        print(json.dumps({"prepared": True, "path": str(expectation_path), "current_campaign": expected["current"]["current_campaign_id"]}))
        return 0
    spec = importlib.util.spec_from_file_location("verified_provider", ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    client = provider.authenticated_client()
    client.proxy_mode = "direct"
    if client._context.verify_mode != ssl.CERT_REQUIRED or not client._context.check_hostname:
        raise RuntimeError("Verified CA and hostname validation are required")
    # Check HTTPS with the existing private CA before opening Chrome. Chrome also
    # keeps its normal trusted-CA validation; no ignore-certificate-errors flags.
    served = json.loads(client.request_raw("GET", "/report-assets/engineering-diagnosis/index.json"))
    if served != expected["current"]:
        raise RuntimeError("Published engineering index differs from current generated contract")
    environment = dict(os.environ,
        MINI_DROP_API_KEY=client._key,
        MINI_DROP_BROWSER_BASE_URL=client.base,
        MINI_DROP_ACCEPTANCE_CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe",
        MINI_DROP_BROWSER_OUTPUT=str(output),
        MINI_DROP_BROWSER_EXPECTATIONS=str(expectation_path),
    )
    result = subprocess.run(["node", str(STAGE / "browser_verify.mjs")], env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=900)
    # Child code never logs request headers. Defensive redaction applies before
    # stdout/stderr are persisted if a browser/library unexpectedly prints one.
    log = (result.stdout + result.stderr).replace(client._key, "<REDACTED_API_KEY>")
    (output / "browser.log").write_text(log, encoding="utf-8")
    print(log.strip())
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
