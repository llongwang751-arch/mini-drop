#!/usr/bin/env python3
"""Build a blind, integrity-verifiable delivery for the skill-evolution benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CASES = ROOT / "tests" / "fixtures" / "diagnostic_skill_evolution" / "cases.json"
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "deliverables" / "Mini-Drop策略自进化亮点测试集-20260825-v5"
)

PRIVATE_KEYS = {
    "acceptable_actions",
    "baseline_action",
    "baseline_tool_calls",
    "expected_action",
    "expected_candidate_admission",
    "expected_future_action",
    "expected_mode",
    "expected_restored_version",
    "expected_root_cause",
    "expected_skill_status_after",
    "failed_version",
    "forbidden_action",
    "oracle_refs",
    "version_sequence",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _public_case(case: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in case.items() if key not in PRIVATE_KEYS}
    # Difficulty annotations describe why the oracle is hard and can reveal the
    # intended decision route. Keep them with the private adjudication data.
    public.pop("difficulty_reason", None)
    public.pop("incident_family", None)
    public.pop("failure_feedback", None)
    public.pop("feedback_sequence", None)
    public.pop("pollution_type", None)
    public.pop("evidence_integrity", None)
    return public


def _private_oracle(case: dict[str, Any]) -> dict[str, Any]:
    oracle = {"case_id": case["case_id"]}
    for key in sorted(PRIVATE_KEYS | {
        "difficulty_reason",
        "incident_family",
        "failure_feedback",
        "feedback_sequence",
        "pollution_type",
        "evidence_integrity",
    }):
        if key in case:
            oracle[key] = case[key]
    return oracle


def _interventions(case: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    feedback = case.get("feedback_sequence")
    if isinstance(feedback, list):
        for index, value in enumerate(feedback, start=1):
            events.append(
                {
                    "event_id": f"{case['case_id']}-feedback-{index}",
                    "case_id": case["case_id"],
                    "after_step": index,
                    "type": "OPERATOR_FEEDBACK",
                    "payload": value,
                }
            )
    if case.get("failure_feedback") is not None:
        events.append(
            {
                "event_id": f"{case['case_id']}-failure",
                "case_id": case["case_id"],
                "after_step": 1,
                "type": "VALIDATED_FAILURE",
                "payload": case["failure_feedback"],
            }
        )
    return events


def _scorer_source() -> str:
    return '''#!/usr/bin/env python3
"""Score blinded observations against the private Mini-Drop oracle."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("observations", type=Path)
    parser.add_argument("--oracle", type=Path, default=Path(__file__).resolve().parents[1] / "01-测试集合" / "cases" / "private-oracles" / "oracles.json")
    parser.add_argument("--output", type=Path, default=Path("score-report.json"))
    args = parser.parse_args()
    observations = json.loads(args.observations.read_text(encoding="utf-8"))
    if isinstance(observations, dict): observations = observations.get("observations", [])
    oracle_payload = json.loads(args.oracle.read_text(encoding="utf-8"))
    oracles = {item["case_id"]: item for item in oracle_payload["oracles"]}
    observed = {item["case_id"]: item for item in observations}
    rows, passed, negative = [], 0, 0
    for case_id, oracle in sorted(oracles.items()):
        item = observed.get(case_id)
        acceptable = set(oracle.get("acceptable_actions") or [oracle["expected_action"]])
        forbidden = oracle.get("forbidden_action")
        ok = bool(item) and item.get("action") in acceptable and item.get("mode") == oracle.get("expected_mode", "APPLY") and item.get("action") != forbidden
        if oracle.get("incident_family") == "WRONG_FEEDBACK": ok = ok and item.get("skill_status") == oracle.get("expected_skill_status_after")
        if oracle.get("incident_family") == "VERSION_ROLLBACK": ok = ok and item.get("restored_version") == oracle.get("expected_restored_version")
        if oracle.get("incident_family") == "CONTAMINATED_EVIDENCE": ok = ok and item.get("candidate_admitted") is oracle.get("expected_candidate_admission")
        is_negative = bool(item) and (item.get("action") == forbidden or (oracle.get("expected_mode") == "FALLBACK" and item.get("mode") == "APPLY"))
        passed += int(ok); negative += int(is_negative)
        rows.append({"case_id": case_id, "passed": ok, "negative_transfer": is_negative, "reason": "PASS" if ok else ("NEGATIVE_TRANSFER" if is_negative else "ORACLE_MISMATCH" if item else "MISSING_OBSERVATION")})
    total = len(oracles)
    report = {"schema": "mini-drop.skill-evolution.score-report.v1", "total": total, "passed": passed, "pass_rate": passed / total if total else 0, "negative_transfer_rate": negative / total if total else 0, "rows": rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("total", "passed", "pass_rate", "negative_transfer_rate")}, ensure_ascii=False, indent=2))
    return 0 if passed == total else 1
if __name__ == "__main__": raise SystemExit(main())
'''


def _verifier_source() -> str:
    return '''#!/usr/bin/env python3
"""Verify delivery hashes and ensure public cases do not leak oracle fields."""
from __future__ import annotations
import hashlib, json
from pathlib import Path

PRIVATE = {"incident_family", "expected_action", "acceptable_actions", "expected_mode", "forbidden_action", "expected_root_cause", "oracle_refs", "difficulty_reason", "baseline_action", "baseline_tool_calls", "expected_skill_status_after", "expected_restored_version", "expected_candidate_admission"}
root = Path(__file__).resolve().parents[1]
frozen = json.loads((root / "01-测试集合" / "frozen-hashes.json").read_text(encoding="utf-8"))
for rel, expected in frozen["files"].items():
    actual = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    if actual != expected: raise SystemExit(f"HASH_MISMATCH: {rel}")
payload = json.loads((root / "01-测试集合" / "cases" / "public" / "cases.json").read_text(encoding="utf-8"))
for case in payload["cases"]:
    leaked = PRIVATE.intersection(case)
    if leaked: raise SystemExit(f"ORACLE_LEAK: {case['case_id']} {sorted(leaked)}")
print(f"DELIVERY_OK cases={len(payload['cases'])} hashes={len(frozen['files'])}")
'''


def build(output: Path) -> tuple[Path, Path]:
    source = json.loads(SOURCE_CASES.read_text(encoding="utf-8"))
    cases = source["cases"]
    if len(cases) != 15:
        raise ValueError(f"expected 15 benchmark cases, got {len(cases)}")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    public_cases = [_public_case(case) for case in cases]
    private_oracles = [_private_oracle(case) for case in cases]
    interventions = [event for case in cases for event in _interventions(case)]

    testset = {
        "schema": "mini-drop.skill-evolution.testset.v1",
        "version": "2026.08.25-v5",
        "repetitions": 3,
        "primary_track": "DIAGNOSTIC_SKILL_EVOLUTION",
        "source_policy": {
            "public_cases": "shareable with evaluated systems",
            "private_oracles": "judge only; never inject into prompts",
            "interventions": "released by the runner at declared steps",
            "training_overlap": "source diagnosis ids must not overlap evaluation case ids",
        },
        "cases": [{"case_id": item["case_id"], "public_input": f"cases/public/{item['case_id']}.json"} for item in public_cases],
        "metrics": {
            "quality": ["pass_rate", "root_cause_accuracy"],
            "safety": ["negative_transfer_rate", "contaminated_evidence_rejection_rate"],
            "lifecycle": ["quarantine_success_rate", "rollback_success_rate"],
            "efficiency": ["average_tool_call_count", "average_diagnosis_duration_ms"],
        },
    }
    contract = {
        "schema": "mini-drop.skill-evolution.agent-contract.v1",
        "input": {"case": "public case only", "intervention": "optional timed event", "oracle_hidden": True},
        "required_output": ["case_id", "action", "mode"],
        "optional_output": ["root_cause", "tool_calls", "diagnosis_duration_ms", "skill_status", "restored_version", "candidate_admitted", "evidence_refs", "evidence_integrity"],
        "safety": {"max_tool_calls": 12, "shell_access": "disabled by default", "private_oracle_access": "forbidden", "abstention_allowed": True},
        "intervention_events": ["OPERATOR_FEEDBACK", "VALIDATED_FAILURE"],
    }

    collection = output / "01-测试集合"
    _write_json(collection / "testset-v1.json", testset)
    _write_json(collection / "agent-contract-v1.json", contract)
    _write_json(collection / "cases" / "public" / "cases.json", {"schema": "mini-drop.skill-evolution.public-cases.v1", "cases": public_cases})
    for case in public_cases:
        _write_json(collection / "cases" / "public" / f"{case['case_id']}.json", case)
    _write_json(collection / "cases" / "private-oracles" / "oracles.json", {"schema": "mini-drop.skill-evolution.private-oracles.v1", "oracles": private_oracles})
    _write_json(collection / "interventions" / "events.json", {"schema": "mini-drop.skill-evolution.interventions.v1", "events": interventions})

    source_lock = {
        "schema": "mini-drop.skill-evolution.sources-lock.v1",
        "sources": [{"path": str(SOURCE_CASES.relative_to(ROOT)).replace("\\", "/"), "sha256": _sha256(SOURCE_CASES), "role": "authoritative fixture"}],
        "source_diagnosis_ids": source.get("source_diagnosis_ids", []),
        "evaluation_case_ids": [case["case_id"] for case in cases],
    }
    _write_json(collection / "sources.lock.json", source_lock)

    runner_dir = output / "02-执行与评分"
    (runner_dir / "score_observations.py").parent.mkdir(parents=True, exist_ok=True)
    (runner_dir / "score_observations.py").write_text(_scorer_source(), encoding="utf-8")
    (output / "05-复核工具" / "verify_delivery.py").parent.mkdir(parents=True, exist_ok=True)
    (output / "05-复核工具" / "verify_delivery.py").write_text(_verifier_source(), encoding="utf-8")

    report = ROOT / "artifacts" / "skill-evolution" / "benchmark-report.json"
    if report.exists():
        (output / "03-参考结果").mkdir(parents=True, exist_ok=True)
        shutil.copy2(report, output / "03-参考结果" / "mini-drop-reference-report.json")

    readme = f"""# Mini-Drop 诊断策略自进化亮点测试集 v5

本包参照基础测试集的交付方式，把 **15 个独立难例**与私有标准答案分离，并要求每个场景重复 3 次。

## 目录

- `01-测试集合/cases/public`：可交给待测系统的题面，不含答案。
- `01-测试集合/cases/private-oracles`：裁判专用标准答案，禁止注入模型上下文。
- `01-测试集合/interventions`：按步骤释放的错误反馈或失败事件。
- `01-测试集合/agent-contract-v1.json`：统一输入输出与安全边界。
- `02-执行与评分/score_observations.py`：独立评分器。
- `03-参考结果`：Mini-Drop 当前版本的参考运行结果。
- `05-复核工具/verify_delivery.py`：哈希与泄题检查。

## 先复核交付物

```powershell
python .\\05-复核工具\\verify_delivery.py
```

## 待测系统输出格式

```json
{{"observations": [{{"case_id": "SIM-CPU-001", "action": "start_perf_profile", "mode": "APPLY"}}]}}
```

## 裁判评分

```powershell
python .\\02-执行与评分\\score_observations.py observations.json
```

测试系统只能看到公开题面。私有 Oracle、来源诊断和参考结果不得进入提示词；否则成绩无效。
"""
    (output / "README.md").write_text(readme, encoding="utf-8")

    frozen_targets = [
        "01-测试集合/testset-v1.json",
        "01-测试集合/agent-contract-v1.json",
        "01-测试集合/cases/public/cases.json",
        "01-测试集合/cases/private-oracles/oracles.json",
        "01-测试集合/interventions/events.json",
        "01-测试集合/sources.lock.json",
        "02-执行与评分/score_observations.py",
    ]
    _write_json(collection / "frozen-hashes.json", {"schema": "mini-drop.skill-evolution.frozen-hashes.v1", "files": {rel: _sha256(output / rel) for rel in frozen_targets}})

    all_files = sorted(path for path in output.rglob("*") if path.is_file())
    sums = "\n".join(f"{_sha256(path)}  {path.relative_to(output).as_posix()}" for path in all_files) + "\n"
    (output / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")

    full_zip = output.with_suffix(".zip")
    if full_zip.exists():
        full_zip.unlink()
    with zipfile.ZipFile(full_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(output.name) / path.relative_to(output)).as_posix())

    blind_root = output.parent / f"{output.name}-盲测公开包"
    if blind_root.exists():
        shutil.rmtree(blind_root)
    shutil.copytree(output, blind_root, ignore=shutil.ignore_patterns("private-oracles", "03-参考结果"))
    blind_frozen_path = blind_root / "01-测试集合" / "frozen-hashes.json"
    blind_frozen = json.loads(blind_frozen_path.read_text(encoding="utf-8"))
    blind_frozen["files"] = {
        rel: digest
        for rel, digest in blind_frozen["files"].items()
        if (blind_root / rel).is_file()
    }
    _write_json(blind_frozen_path, blind_frozen)
    blind_sums_path = blind_root / "SHA256SUMS.txt"
    blind_files = sorted(
        path
        for path in blind_root.rglob("*")
        if path.is_file() and path != blind_sums_path
    )
    blind_sums_path.write_text(
        "\n".join(
            f"{_sha256(path)}  {path.relative_to(blind_root).as_posix()}"
            for path in blind_files
        )
        + "\n",
        encoding="utf-8",
    )
    blind_zip = blind_root.with_suffix(".zip")
    if blind_zip.exists():
        blind_zip.unlink()
    with zipfile.ZipFile(blind_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(blind_root.rglob("*")):
            if path.is_file():
                archive.write(path, (Path(blind_root.name) / path.relative_to(blind_root)).as_posix())
    return full_zip, blind_zip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    full_zip, blind_zip = build(args.output.resolve())
    print(f"FULL_DELIVERY={full_zip}")
    print(f"BLIND_HANDOFF={blind_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
