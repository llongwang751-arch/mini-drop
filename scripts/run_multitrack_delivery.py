#!/usr/bin/env python3
"""Replay the 9-case multitrack delivery against the current RCA pipeline."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)
EVIDENCE_ID_RE = re.compile(r"ev-\d+-[a-z0-9_-]+", re.IGNORECASE)


def _evidence_ids(refs: list[object]) -> list[str]:
    result: list[str] = []
    for ref in refs:
        match = EVIDENCE_ID_RE.search(str(ref))
        if match and match.group(0) not in result:
            result.append(match.group(0))
    return result


def _normalize_model_report(report: dict) -> dict:
    """Adapt the native structured report without reading private Oracles."""
    ranked = report.get("ranked_causes") or []
    summary = str(report.get("summary") or "")
    if not ranked:
        return {
            "schema": "mini-drop.normalized-answer.v1",
            "conclusion": summary or "INSUFFICIENT_EVIDENCE",
            "root_location": "unknown",
            "mechanism": summary,
            "confidence": 0.0,
            "confidence_reason": "No evidence-supported cause was produced.",
            "supporting_evidence": [],
            "counter_evidence": [],
            "missing_evidence": report.get("missing_evidence") or [],
            "next_action": "request aligned evidence",
            "abstain": True,
        }
    top = ranked[0]
    confidence = max(0.0, min(1.0, float(top.get("confidence") or 0)))
    root_location = str(top.get("root_location") or "unknown")
    return {
        "schema": "mini-drop.normalized-answer.v1",
        "conclusion": summary or str(top.get("claim") or ""),
        "root_location": root_location,
        "mechanism": str(top.get("mechanism") or top.get("claim") or ""),
        "confidence": confidence,
        "confidence_reason": "Model confidence after evidence-reference validation.",
        "supporting_evidence": _evidence_ids(top.get("evidence_refs") or []),
        "counter_evidence": _evidence_ids(top.get("counter_evidence_refs") or []),
        "missing_evidence": report.get("missing_evidence") or top.get("uncertainties") or [],
        "next_action": (top.get("verification_steps") or ["request aligned evidence"])[0],
        "abstain": bool(report.get("not_enough_evidence")) or root_location == "unknown",
    }


def _preflight_provider() -> None:
    """Fail before a 27-run campaign when the configured model is unusable."""
    from server.app.ai_provider import chat_completions, get_ai_settings

    settings = get_ai_settings()
    if not settings.rca_enabled or not settings.api_key:
        raise RuntimeError(
            "RCA model is disabled or missing an API key; refusing to score rule fallback "
            "as an AI benchmark result"
        )
    response = chat_completions(
        {
            "model": settings.model,
            "messages": [{"role": "user", "content": "Reply with OK."}],
            "temperature": 0,
            "max_tokens": 4,
        },
        timeout=20,
    )
    if getattr(response, "status_code", None) != 200:
        request_id = getattr(response, "headers", {}).get("x-request-id", "-")
        raise RuntimeError(
            "RCA model preflight failed; refusing to start benchmark "
            f"(status={getattr(response, 'status_code', 'unknown')}, request_id={request_id})"
        )


def _single(paths: list[Path], label: str) -> Path:
    paths = [path for path in paths if "__MACOSX" not in path.parts]
    if len(paths) != 1:
        raise RuntimeError(f"expected one {label}, found {len(paths)}")
    return paths[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("delivery_root", type=Path)
    parser.add_argument(
        "--work-dir", type=Path, default=ROOT / "artifacts" / "multitrack-20260822"
    )
    parser.add_argument(
        "--allow-rule-fallback",
        action="store_true",
        help="Run without a working model for plumbing diagnostics only; results are not comparable.",
    )
    args = parser.parse_args()

    if not args.allow_rule_fallback:
        _preflight_provider()

    delivery = args.delivery_root.resolve()
    runner_path = _single(
        list(delivery.rglob("run_native_llongwang_minidrop.py")), "native runner"
    )
    public_dir = _single(
        [
            path
            for path in delivery.rglob("public")
            if path.is_dir() and "01-测试集合" in path.parts
        ],
        "public cases",
    )
    replay_dir = _single(
        [
            path
            for path in delivery.rglob("replay")
            if path.is_dir() and "01-测试集合" in path.parts
        ],
        "replay cases",
    )

    benchmark = args.work_dir.resolve()
    shutil.rmtree(benchmark, ignore_errors=True)
    shutil.copytree(public_dir, benchmark / "cases" / "public")
    shutil.copytree(replay_dir, benchmark / "cases" / "replay")
    (benchmark / "work").mkdir(parents=True, exist_ok=True)

    spec = importlib.util.spec_from_file_location("multitrack_native_runner", runner_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load delivery runner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BENCHMARK = benchmark
    module.REPO_DIR = ROOT
    module.PYTHON = Path(sys.executable)
    module.DRIVER = ROOT / "scripts" / "multitrack_rca_driver.py"
    module.normalize_answer = _normalize_model_report
    module.SOURCE_SHA = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    module.COMMON_PROMPT = "Mini-Drop current RCA multitrack replay"
    module.main()
    # The delivered runner rebuilds parts of the benchmark directory, so write
    # comparability metadata after it finishes.
    (benchmark / "campaign-metadata.json").write_text(
        json.dumps(
            {
                "schema": "mini-drop.multitrack-campaign.v1",
                "provider_preflight_required": not args.allow_rule_fallback,
                "rule_fallback_allowed": args.allow_rule_fallback,
                "score_comparable": not args.allow_rule_fallback,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"RUN_ROOT={benchmark / 'runs-native'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
