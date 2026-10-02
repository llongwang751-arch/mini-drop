"""Read-only real Chrome audit of newly persisted planning cards, with normal TLS."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import ssl
import subprocess

ROOT = Path(__file__).resolve().parents[3]
STAGE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", type=Path, required=True)
    parser.add_argument("--additional-smoke", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    assert not output.exists(), "preserve prior browser evidence; choose a fresh destination"
    smoke = json.loads(args.smoke.read_text(encoding="utf-8"))
    assert smoke["schema"] == "mini-drop.live-planning-output-smoke.v1"
    batches = [(args.smoke, smoke)]
    batches += [(path, json.loads(path.read_text(encoding="utf-8"))) for path in args.additional_smoke]
    assert all(batch["schema"] == smoke["schema"] for _, batch in batches)
    cases = [{**row, "planner_source_head": batch["source_head"], "planner_release": batch["release"]}
             for _, batch in batches for row in batch["cases"] if row.get("passed")]
    assert cases, "no genuine persisted planning result is available to render"
    no_answer_cases = []
    for batch_path, batch in batches:
        for row in batch["cases"]:
            if args.additional_smoke and batch_path != args.smoke and not row.get("passed"):
                continue
            records_path = batch_path.parent / row["name"] / "records.json"
            if not records_path.exists():
                continue
            records = json.loads(records_path.read_text(encoding="utf-8"))
            event_ids = [item["event_id"] for item in records.get("retrievals", [])
                         if item.get("retrieval_trace", {}).get("outcome") == "NO_RELEVANT_KNOWLEDGE"
                         and item.get("retrieval_trace", {}).get("health_scope") == "RETRIEVAL_ONLY"]
            if event_ids:
                no_answer_cases.append({"name": row["name"], "diagnosis_id": row["diagnosis_id"], "retrieval_event_ids": event_ids,
                                        "planner_source_head": batch["source_head"], "planner_release": batch["release"]})
    spec = importlib.util.spec_from_file_location("planning_browser_provider", ROOT / "output/acceptance/deployment-20260930/run_strict.py")
    provider = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(provider)
    client = provider.authenticated_client()
    client.proxy_mode = "direct"
    assert client._context.verify_mode == ssl.CERT_REQUIRED and client._context.check_hostname
    health = client.request("GET", "/api/healthz")
    output.mkdir(parents=True)
    attempts = [{"source_head": batch["source_head"], "release": batch["release"], "status": batch["status"],
                 "total_cases": len(batch["cases"]), "passed_cases": sum(bool(row.get("passed")) for row in batch["cases"]),
                 "planner_invocations": batch["planner_invocations"], "heldout_evaluation": False} for _, batch in batches]
    expected = {"cases": cases, "no_answer_cases": no_answer_cases, "smoke_attempts": attempts,
                "total_live_cases": 3 if args.additional_smoke else len(smoke["cases"]), "source_head": smoke["source_head"],
                "release": smoke["release"], "health": health,
                "scope": "Read-only current Chrome/CDP rendering of genuine persisted planning results with producer source per case; original failed smoke grades remain unchanged; normal certificate verification; no fault/session/tool mutations"}
    expectation_path = output / "expected.json"
    expectation_path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    environment = {**os.environ, "MINI_DROP_API_KEY": client._key, "MINI_DROP_BROWSER_BASE_URL": client.base,
                   "MINI_DROP_BROWSER_OUTPUT": str(output), "MINI_DROP_BROWSER_EXPECTATIONS": str(expectation_path),
                   "MINI_DROP_ACCEPTANCE_CHROME": "C:/Program Files/Google/Chrome/Application/chrome.exe"}
    return subprocess.run(["node", str(STAGE / "browser_planning_smoke.mjs")], cwd=ROOT, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
