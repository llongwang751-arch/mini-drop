"""CLI for the Mini-Drop unified diagnosis benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from server.app.diagnosis.benchmark_adapters import (
    preflight_all,
    replay_local_golden,
    set_otel_feature_flag,
)
from server.app.diagnosis.benchmark_runner import (
    build_run_plan,
    campaign_progress,
    evaluate_submissions,
    render_html_report,
    scoring_detail_from_api,
    upsert_submission,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mini-Drop diagnosis benchmark")
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="generate the shared 10-case run plan")
    plan.add_argument("--repetitions", type=int, default=None)
    plan.add_argument("--output", type=Path, default=None)

    preflight = commands.add_parser("preflight", help="validate fault adapters")
    preflight.add_argument("--otel-root", type=Path, default=None)
    preflight.add_argument("--output", type=Path, default=None)

    replay = commands.add_parser("replay", help="run a local deterministic case")
    replay.add_argument("case_id")
    replay.add_argument("--output", type=Path, default=None)

    toggle = commands.add_parser(
        "toggle-otel", help="enable or reset one pinned OTel fault fixture"
    )
    toggle.add_argument("case_id")
    toggle.add_argument("--otel-root", type=Path, required=True)
    toggle.add_argument("--reset", action="store_true")
    toggle.add_argument("--output", type=Path, default=None)

    evaluate = commands.add_parser("evaluate", help="score completed diagnosis outputs")
    evaluate.add_argument("submissions", type=Path)
    evaluate.add_argument(
        "--require-complete", action="store_true",
        help="reject reports that do not contain all 90 planned executions",
    )
    evaluate.add_argument("--output", type=Path, default=None)

    submit = commands.add_parser(
        "submit", help="atomically record one finished diagnosis in the campaign"
    )
    submit.add_argument("case_id")
    submit.add_argument("strategy")
    submit.add_argument("repetition", type=int)
    submit.add_argument("diagnosis_json", type=Path)
    submit.add_argument("--submissions", type=Path, required=True)
    submit.add_argument("--overwrite", action="store_true")

    status = commands.add_parser(
        "status", help="show resumable campaign progress and missing executions"
    )
    status.add_argument("submissions", type=Path)
    status.add_argument("--output", type=Path, default=None)

    campaign = commands.add_parser(
        "campaign", help="materialize the 90-run plan and adapter readiness report"
    )
    campaign.add_argument("--otel-root", type=Path, default=None)
    campaign.add_argument("--output-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "plan":
        result = build_run_plan(repetitions=args.repetitions)
    elif args.command == "preflight":
        result = preflight_all(otel_root=args.otel_root)
    elif args.command == "replay":
        result = replay_local_golden(args.case_id)
    elif args.command == "toggle-otel":
        result = set_otel_feature_flag(
            args.case_id,
            otel_root=args.otel_root,
            enabled=not args.reset,
        )
    elif args.command == "evaluate":
        try:
            payload = json.loads(args.submissions.read_text(encoding="utf-8"))
            result = evaluate_submissions(
                payload, require_complete=args.require_complete
            )
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            parser.exit(1, f"benchmark error: {exc}\n")
    elif args.command == "submit":
        diagnosis_payload = json.loads(
            args.diagnosis_json.read_text(encoding="utf-8-sig")
        )
        result = upsert_submission(
            args.submissions,
            {
                "case_id": args.case_id,
                "strategy": args.strategy,
                "repetition": args.repetition,
                "diagnosis_detail": scoring_detail_from_api(diagnosis_payload),
            },
            overwrite=args.overwrite,
        )
    elif args.command == "status":
        payload = json.loads(args.submissions.read_text(encoding="utf-8"))
        result = campaign_progress(payload)
    else:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        run_plan = build_run_plan()
        readiness = preflight_all(otel_root=args.otel_root)
        (args.output_dir / "run-plan.json").write_text(
            json.dumps(run_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.output_dir / "adapter-readiness.json").write_text(
            json.dumps(readiness, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        empty_submissions = args.output_dir / "submissions.json"
        if not empty_submissions.exists():
            empty_submissions.write_text("[]\n", encoding="utf-8")
        result = {
            "output_dir": str(args.output_dir.resolve()),
            "execution_count": run_plan["execution_count"],
            "case_count": run_plan["case_count"],
            "ready_adapter_count": readiness["ready_count"],
            "fixture_ready_adapter_count": readiness["fixture_ready_count"],
            "diagnosis_ready_adapter_count": readiness["diagnosis_ready_count"],
            "supported_adapter_count": readiness["supported_count"],
            "artifacts": ["run-plan.json", "adapter-readiness.json", "submissions.json"],
            "next_command": (
                f"python scripts/diagnosis_benchmark.py evaluate "
                f"{empty_submissions} --require-complete "
                f"--output {args.output_dir / 'evaluation.json'}"
            ),
        }

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    output = getattr(args, "output", None)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        if args.command == "evaluate":
            html_output = output.with_suffix(".html")
            html_output.write_text(render_html_report(result), encoding="utf-8")
        print(output.resolve())
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
