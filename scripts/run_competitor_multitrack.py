#!/usr/bin/env python3
"""Run a public competitor adapter against the shared multitrack cases.

The competitor source tree is treated as read-only.  The shared delivery's
ReplayService is injected because the competitor archive references it but
does not publish that module.  Private Oracles are never imported here.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import types
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env", override=True)


def _single(paths: list[Path], label: str) -> Path:
    paths = [path for path in paths if "__MACOSX" not in path.parts]
    if len(paths) != 1:
        raise RuntimeError(f"expected one {label}, found {len(paths)}")
    return paths[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _install_replay_module(replay_path: Path) -> None:
    package = types.ModuleType("benchmark")
    package.__path__ = [str(replay_path.parent)]
    sys.modules["benchmark"] = package
    _load_module("benchmark.replay", replay_path)


def _requests_complete(adapter, messages: list[dict], api_key: str) -> dict:
    """Preserve the competitor payload while avoiding its urllib-only 401."""
    import requests

    payload = {
        "model": adapter.MODEL,
        "messages": messages,
        "tools": adapter.TOOL_SCHEMAS,
        "tool_choice": "auto",
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "max_tokens": 2400,
    }
    response = requests.post(
        adapter.api_url(adapter.BASE_URL),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=(5, adapter.TIMEOUT),
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Mini-Drop DeepSeek call failed: HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
    return response.json()


def _validate_run(run_dir: Path) -> tuple[bool, str]:
    manifest_path = run_dir / "manifest.json"
    answer_path = run_dir / "normalized-answer.json"
    if not manifest_path.exists() or not answer_path.exists():
        return False, "missing manifest or normalized answer"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    answer = json.loads(answer_path.read_text(encoding="utf-8"))
    required = {
        "conclusion",
        "root_location",
        "mechanism",
        "confidence",
        "supporting_evidence",
        "counter_evidence",
        "missing_evidence",
        "next_action",
        "abstain",
    }
    if manifest.get("status") != "completed":
        return False, str(manifest.get("exit_reason") or "adapter did not complete")
    missing = sorted(required.difference(answer))
    if missing:
        return False, f"normalized answer missing fields: {', '.join(missing)}"
    return True, "completed with the shared normalized-answer schema"


def _normalize_output_encoding(run_dir: Path) -> None:
    """Convert the competitor's locale-encoded text artifacts to UTF-8."""
    for path in run_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl", ".txt"}:
            continue
        raw = path.read_bytes()
        try:
            raw.decode("utf-8")
            continue
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
        path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("competitor_root", type=Path)
    parser.add_argument("delivery_root", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case", dest="cases", action="append")
    parser.add_argument("--source-archive", type=Path)
    args = parser.parse_args()

    competitor = args.competitor_root.resolve()
    delivery = args.delivery_root.resolve()
    benchmark = args.work_dir.resolve()
    adapter_path = competitor / "benchmarks" / "multi-agent-native-comparison-20260821" / "benchmark" / "adapters" / "mini-drop" / "native_run_case.py"
    source_benchmark = competitor / "benchmarks" / "multi-agent-native-comparison-20260821" / "benchmark"
    replay_path = _single(list(delivery.rglob("replay_service.py")), "ReplayService")
    from server.app.ai_provider import get_ai_settings

    provider_settings = get_ai_settings()
    api_key = provider_settings.api_key.strip()
    if not api_key:
        raise RuntimeError("resolved Mini-Drop AI provider key is missing")

    shutil.rmtree(benchmark, ignore_errors=True)
    shutil.copytree(source_benchmark / "cases" / "public", benchmark / "cases" / "public")
    shutil.copytree(source_benchmark / "cases" / "replay", benchmark / "cases" / "replay")
    _install_replay_module(replay_path)
    adapter = _load_module("competitor_native_run_case", adapter_path)
    adapter.BENCHMARK = benchmark
    adapter.MODEL = args.model
    adapter.BASE_URL = args.base_url
    adapter.deepseek_complete = lambda messages, api_key: _requests_complete(
        adapter, messages, api_key
    )

    cases = args.cases or [f"case-{number:02d}" for number in range(1, 10)]
    results: list[dict] = []
    validation: list[dict] = []
    for case_id in cases:
        for repeat in range(1, args.repeats + 1):
            result = adapter.run_case(
                case_id,
                repeat,
                repeat - 1,
                benchmark / "runs-native",
                api_key,
            )
            results.append(result)
            run_dir = Path(result["run_dir"])
            _normalize_output_encoding(run_dir)
            valid, reason = _validate_run(run_dir)
            validation.append(
                {
                    "case_id": case_id,
                    "repeat": repeat,
                    "valid": valid,
                    "reason": reason,
                    "run_dir": str(run_dir),
                }
            )
            print(json.dumps({**result, "schema_valid": valid}, ensure_ascii=False), flush=True)

    archive_hash = None
    if args.source_archive and args.source_archive.exists():
        archive_hash = _sha256(args.source_archive.resolve())
    valid_count = sum(item["valid"] for item in validation)
    metadata = {
        "schema": "mini-drop.multitrack-campaign.v1",
        "subject": "jiangyulin1/mini-drop-ai-agent-v2",
        "source_url": "https://github.com/jiangyulin1/mini-drop-ai-agent-v2",
        "source_archive_sha256": archive_hash,
        "adapter_path": str(adapter_path),
        "adapter_gap": "Public archive omitted benchmark.replay; injected the shared delivery ReplayService without private Oracles.",
        "transport_compatibility": "Replaced urllib transport with requests because the same official credential returned HTTP 401 only through urllib; request payload and agent logic were unchanged.",
        "encoding_compatibility": "Converted Windows locale-encoded text artifacts to UTF-8 without changing their content.",
        "model": args.model,
        "base_url": args.base_url,
        "temperature": 0,
        "repeat_count": args.repeats,
        "run_count": len(results),
        "schema_valid_run_count": valid_count,
        "external_adapter_schema_validated": True,
        "score_comparable": valid_count == len(results),
        "validation": validation,
    }
    (benchmark / "campaign-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"RUN_ROOT={benchmark / 'runs-native'}")
    return 0 if valid_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
