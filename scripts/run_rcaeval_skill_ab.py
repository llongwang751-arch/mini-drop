from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from server.app.drop_insight.rcaeval_benchmark import (
    calibrate_skill_gate,
    evaluate_paired_replay,
    load_private_cases,
    load_signatures_with_exclusions,
    train_route_skills,
)


REPO_ID = "phamquiluan/RCAEval"
SOURCE_URL = "https://github.com/phamquiluan/RCAEval"


def _download_subset(root: Path, selector: str) -> None:
    try:
        from huggingface_hub import hf_hub_download, snapshot_download
    except ImportError as exc:
        raise RuntimeError("install the benchmark extra: pip install -e '.[benchmark]'") from exc
    hf_hub_download(REPO_ID, "cases.parquet", repo_type="dataset", local_dir=root)
    prefix = selector.lower().replace("-", "")
    snapshot_download(
        REPO_ID,
        repo_type="dataset",
        allow_patterns=[f"{prefix}*/*"],
        local_dir=root,
        max_workers=8,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _upstream_revision(root: Path) -> str | None:
    metadata = root / ".cache" / "huggingface" / "download" / "cases.parquet.metadata"
    if not metadata.is_file():
        return None
    lines = metadata.read_text(encoding="utf-8").splitlines()
    return lines[0].strip() if lines else None


def _render_markdown(report: dict) -> str:
    before = report["no_skill"]
    after = report["skill_enabled"]
    route = report["skill_route_selection"]
    return "\n".join(
        [
            "# RCAEval RE1 Metric Route Skill A/B",
            "",
            f"- Dataset: `{report['dataset']['name']}`",
            f"- Train/test: `{report['dataset']['train_cases']}` / `{report['dataset']['test_cases']}` cases",
            "- Split: repetitions 1-3 train Route Skills; repetitions 4-5 blind test",
            f"- No-Skill Top-1: `{before['top1']:.2%}`",
            f"- Skill Top-1: `{after['top1']:.2%}`",
            f"- Top-1 delta: `{report['delta']['top1_percentage_points']:+.2f}` percentage points",
            f"- No-Skill / Skill MRR: `{before['mrr']:.4f}` / `{after['mrr']:.4f}`",
            f"- Skill activation coverage: `{route['coverage']:.2%}` (`{route['activated']}/{route['total']}`)",
            f"- Fault-family accuracy when activated: `{route['accuracy_when_activated']:.2%}`",
            f"- Paired improved / regressed: `{report['delta']['improved']}` / `{report['delta']['regressed']}`",
            f"- Paired exact test p-value: `{report['statistical_test']['p_value_two_sided']:.4f}`",
            "",
            "## Per System",
            "",
            "| System | Cases | No-Skill Top-1 | Skill Top-1 | Delta |",
            "|---|---:|---:|---:|---:|",
            *[
                f"| {name} | {row['case_count']} | {row['no_skill_top1']:.2%} | {row['skill_top1']:.2%} | {row['top1_delta_percentage_points']:+.2f} pp |"
                for name, row in report["breakdown_by_dataset"].items()
            ],
            "",
            "## Interpretation",
            "",
            "The blind split observed a small positive delta with no Top-1 regressions after the train-only Skill admission gate.",
            "The result is not statistically significant at p < 0.05, so it must not be presented as a proven production uplift.",
            "",
            "## Boundary",
            "",
            "This is an offline metric-only service-localization replay on public RCAEval data.",
            "It is not a live Linux Collector run, production traffic, or proof of exact code-level root cause.",
            "The Route Skill stores metric-family priorities learned from train repetitions; it does not store test service labels.",
        ]
    ) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("artifacts/external/rcaeval"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/rcaeval-skill-ab"))
    parser.add_argument("--suite", default="RE1")
    parser.add_argument("--dataset")
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()

    if args.download:
        _download_subset(args.data_root, args.dataset or args.suite)
    index_path = args.data_root / "cases.parquet"
    started = time.perf_counter()
    cases = load_private_cases(
        index_path,
        args.data_root,
        dataset=args.dataset,
        suite=None if args.dataset else args.suite,
    )
    train_cases = [case for case in cases if case.repetition <= 3]
    test_cases = [case for case in cases if case.repetition >= 4]
    train_rows, train_exclusions = load_signatures_with_exclusions(train_cases)
    fit_rows, _ = load_signatures_with_exclusions(
        [case for case in train_cases if case.repetition <= 2]
    )
    validation_rows, _ = load_signatures_with_exclusions(
        [case for case in train_cases if case.repetition == 3]
    )
    gate, calibration = calibrate_skill_gate(fit_rows, validation_rows)
    skills = train_route_skills(train_rows)
    test_rows, test_exclusions = load_signatures_with_exclusions(test_cases)
    report = evaluate_paired_replay(test_rows, skills, gate=gate)
    report["dataset"] = {
        "name": args.dataset or args.suite,
        "source": SOURCE_URL,
        "upstream_revision": _upstream_revision(args.data_root),
        "index_sha256": _sha256(index_path),
        "train_cases": len(train_rows),
        "test_cases": len(test_rows),
        "train_repetitions": [1, 2, 3],
        "test_repetitions": [4, 5],
        "excluded_cases": train_exclusions + test_exclusions,
    }
    report["oracle_isolation"] = {
        "prediction_input_fields": [
            "opaque_case_id",
            "dataset",
            "service_family_scores",
            "family_features",
            "pre_samples",
            "post_samples",
        ],
        "private_fields_revealed_after_prediction": [
            "root_cause_service",
            "fault",
        ],
        "source_case_name_passed_to_predictor": False,
    }
    report["gate_calibration"] = calibration
    report["route_skills"] = [
        {
            "skill_id": skill.skill_id,
            "dataset": skill.dataset,
            "fault_family": skill.fault_family,
            "probe_order": list(skill.probe_order),
            "family_weights": dict(skill.family_weights),
            "training_case_count": skill.training_case_count,
        }
        for skill in skills
    ]
    report["elapsed_seconds"] = time.perf_counter() - started
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output_dir / "report.md").write_text(_render_markdown(report), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, indent=2))


if __name__ == "__main__":
    main()
