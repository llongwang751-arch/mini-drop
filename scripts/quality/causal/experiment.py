"""受控因果实验的冻结计划、独立客观oracle与人工评审接口。

注入路径只在控制器中出现。模型只看采集证据与同一中性问题；本评分不读自由解释猜根因。
干预结论与模型方向匹配分列；精确根因、人审误报和拒答不会由Agent代填。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/evaluation"))
import evaluate_diagnostics as common  # noqa: E402


def load(path):
    if not isinstance(path, Path) or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("INPUT_REJECTED")
    return json.loads(path.read_text("utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))


def dataset():
    model, oracle, manifest = [load(HERE / name) for name in ("model_input.json", "oracle.json", "manifest.json")]
    if common.canonical_sha256(model) != manifest["model_input_sha256"] or common.canonical_sha256(oracle) != manifest["oracle_sha256"]:
        raise ValueError("FROZEN_DATA_CHANGED")
    if set(model) != {"schema_version", "dataset_id", "goal", "collection", "diagnosis"} or len(oracle["cases"]) != 3:
        raise ValueError("DATASET_BOUNDARY_REJECTED")
    return model, oracle, manifest


def plan(repetitions=2, seed=751, case_ids=None):
    _, oracle, manifest = dataset()
    selected = case_ids or [row["id"] for row in oracle["cases"]]
    if type(repetitions) is not int or not 1 <= repetitions <= 2 or type(seed) is not int or not 0 <= seed <= 2**32 - 1 or not selected or len(set(selected)) != len(selected) or any(value not in {"c01", "c02", "c03"} for value in selected):
        raise ValueError("PLAN_BOUNDARY_REJECTED")
    trials = [{"case_id": value, "replicate": rep} for rep in range(1, repetitions + 1) for value in selected]
    random.Random(seed).shuffle(trials)
    value = {"schema_version": 1, "dataset_id": manifest["dataset_id"], "manifest_sha256": common.canonical_sha256(manifest), "repetitions": repetitions, "seed": seed, "case_ids": selected, "trials": trials, "diagnoses_per_round": 2, "author_visible": True, "independent_blind_test": False}
    return {**value, "plan_sha256": common.canonical_sha256(value)}


def utc(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("WINDOW_INVALID")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("WINDOW_INVALID")
    return parsed.astimezone(timezone.utc)


def finite(value):
    return type(value) in {int, float} and math.isfinite(value) and value >= 0


def observation(phase):
    """只接受实际HTTP客户端与服务端固定指标，空窗/未知数绝不填零。"""
    if not isinstance(phase, dict) or phase.get("stage") not in {"observed", "diagnosed", "diagnosis_failed"}:
        return None
    start, end = utc(phase["start"]), utc(phase["end"])
    if not 0 < (end - start).total_seconds() <= 150:
        raise ValueError("OBSERVATION_WINDOW_REJECTED")
    rows = phase.get("responses")
    if not isinstance(rows, list) or not 3 <= len(rows) <= 160:
        return None
    for row in rows:
        if not isinstance(row, dict) or type(row.get("status_code")) is not int or not 100 <= row["status_code"] <= 599 or not finite(row.get("duration_ms")) or not re.fullmatch(r"[a-f0-9]{64}", row.get("trace_sha256", "")):
            raise ValueError("OBSERVATION_RESPONSE_REJECTED")
    metrics = phase.get("signals", {}).get("gateway", {}).get("metrics", {}).get("metrics")
    if not isinstance(metrics, list):
        return None
    fixed = {"latency_p95_ms": "milliseconds", "error_rate": "ratio", "throughput_rps": "requests_per_second"}
    values = {}
    for row in metrics:
        if row.get("name") in fixed:
            if row.get("name") in values or row.get("unit") != fixed[row["name"]] or not finite(row.get("value")):
                raise ValueError("METRIC_SOURCE_REJECTED")
            values[row["name"]] = row["value"]
    if set(values) != set(fixed) or values["error_rate"] > 1 or values["throughput_rps"] <= 0:
        return None
    client_error = sum(row["status_code"] >= 500 for row in rows) / len(rows)
    if abs(client_error - values["error_rate"]) > 1e-9:
        return None
    return {"p95_ms": values["latency_p95_ms"], "error_rate": values["error_rate"], "response_count": len(rows), "http_504_rate": sum(row["status_code"] == 504 for row in rows) / len(rows)}


def events(phase, service):
    rows = phase.get("signals", {}).get(service, {}).get("logs", {}).get("logs", [])
    if not isinstance(rows, list) or len(rows) > 30:
        raise ValueError("LOG_BOUNDARY_REJECTED")
    return rows


def controlled_effect(case, before, fault, after):
    _, oracle, _ = dataset()
    policy = oracle["policy"]
    a, f, z = [observation(value) for value in (before, fault, after)]
    checks = {"all_windows_measured": all(value is not None for value in (a, f, z))}
    if not checks["all_windows_measured"]:
        return {"passed": False, "checks": checks}
    checks["normal_controls_have_no_measured_errors"] = a["error_rate"] == 0 and z["error_rate"] == 0
    checks["same_target_and_environment"] = bool(before.get("target_fingerprint")) and before.get("target_fingerprint") == fault.get("target_fingerprint") == after.get("target_fingerprint")
    checks["phase_order"] = utc(before["end"]) <= utc(fault["start"]) < utc(fault["end"]) <= utc(after["start"])
    checks["fault_recovery_window_separated"] = isinstance(after.get("collection_start"), str) and (utc(after["collection_start"]) - utc(fault["end"])).total_seconds() >= policy["washout_seconds"]
    if case["scenario"] == "timeout":
        checks["actual_gateway_deadline"] = f["http_504_rate"] >= policy["timeout_504_rate_minimum"] and any(row.get("event_code") == "UPSTREAM_TIMEOUT" and row.get("status_code") == 504 for row in events(fault, "gateway"))
        checks["actual_downstream_work"] = any(row.get("event_code") == "DB_SLOW_QUERY" for row in events(fault, "catalog"))
    else:
        normal_p95 = max(a["p95_ms"], z["p95_ms"])
        checks["latency_changes_and_recovers"] = f["p95_ms"] >= normal_p95 * policy["latency_ratio_minimum"] and f["p95_ms"] - normal_p95 >= policy["latency_increase_ms_minimum"] and f["error_rate"] == 0
        if case["scenario"] == "slow_query":
            checks["actual_fixed_sql_path"] = any(row.get("event_code") == "DB_SLOW_QUERY" and row.get("status_code") == 200 for row in events(fault, "catalog")) and not any(row.get("event_code") == "DB_SLOW_QUERY" for row in events(after, "catalog"))
        else:
            cpu_f, cpu_a = fault.get("sampling", {}).get("controlled_cpu_self_percent"), after.get("sampling", {}).get("controlled_cpu_self_percent")
            checks["actual_cpu_sample_changes_and_recovers"] = finite(cpu_f) and finite(cpu_a) and cpu_f >= policy["cpu_fault_self_percent_minimum"] and cpu_a <= policy["cpu_after_self_percent_maximum"]
    checks["real_same_parameter_collection_pair"] = all(phase.get("sampling", {}).get("live_collection") is True and phase["sampling"].get("kind") == "PYTHON_CPU" and phase["sampling"].get("source") == "py-spy" and phase["sampling"].get("total_samples", 0) > 0 for phase in (fault, after)) and isinstance(fault.get("collection_parameters_sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", fault["collection_parameters_sha256"]) is not None and fault.get("collection_parameters_sha256") == after.get("collection_parameters_sha256")
    return {"passed": all(checks.values()), "checks": checks, "measurements": {"normal_before": a, "fault": f, "normal_after": z}, "causal_scope": case["causal_scope"], "arbitrary_root_cause_accuracy": False}


def model_direction(phase, expected):
    run = phase.get("diagnosis")
    if not isinstance(run, dict) or run.get("status") != "success":
        return {"observed": False, "matched": False}
    report = common.parse_embedded(run.get("report") or {}, dict, "report")
    model, _, _ = dataset()
    if any(not isinstance(str(run.get(field)), str) or re.fullmatch(r"[1-9][0-9]{0,18}", str(run.get(field))) is None for field in ("id", "task_id", "attempt_id")):
        raise ValueError("MODEL_RUN_IDENTITY_INVALID")
    runtime = report.get("agent_runtime") or {}
    if run.get("goal") != model["goal"] or run.get("mode") != "model" or run.get("knowledge_mode") != "off" or run.get("report_protocol") != 2 or str(run.get("task_id")) != str(phase.get("task_id")) or str(run.get("attempt_id")) != str(phase.get("sampling", {}).get("attempt_id")) or runtime.get("framework") != "agentscope" or runtime.get("framework_version") != "2.0.10" or runtime.get("agent_class") != "agentscope.agent.Agent" or type(runtime.get("model_calls")) is not int or runtime["model_calls"] <= 0:
        raise ValueError("MODEL_RUNTIME_OR_SOURCE_BINDING_INVALID")
    if report.get("report_version") != 2 or not isinstance(report.get("facts"), list) or not report["facts"]:
        raise ValueError("STRUCTURED_REPORT_REQUIRED")
    references = common.evidence_metrics(common.parse_embedded(run.get("trace") or [], list, "trace"), report)
    if references["known_reference_coverage"]["rate"] != 1 or references["hypotheses_with_current_observation"]["rate"] != 1:
        raise ValueError("CURRENT_OBSERVATION_REFERENCES_REQUIRED")
    hypotheses = report.get("hypotheses", [])
    kinds = [row.get("kind") for row in hypotheses if isinstance(row, dict)]
    recognized = bool(kinds) and all(kind in common.CATEGORIES for kind in kinds)
    return {"observed": True, "matched": recognized and bool(set(kinds[:3]) & set(expected)), "kinds": kinds[:4] if recognized else ["unknown"], "run_id": str(run["id"]), "task_id": str(run["task_id"]), "analysis_sha256": phase.get("sampling", {}).get("analysis_sha256"), "report_sha256": common.canonical_sha256(report), "semantic_root_cause_verified": False, "fact_values_revalidated": False}


def score(capture):
    _, oracle, manifest = dataset()
    selected = capture.get("plan", {})
    if selected != plan(selected.get("repetitions"), selected.get("seed"), selected.get("case_ids")):
        raise ValueError("PREREGISTERED_PLAN_CHANGED")
    if not isinstance(capture.get("environment_sha256"), str) or re.fullmatch(r"[a-f0-9]{64}", capture["environment_sha256"]) is None:
        raise ValueError("ENVIRONMENT_DIGEST_REQUIRED")
    indexed = {}
    for row in capture.get("rounds", []):
        key = (row.get("case_id"), row.get("replicate"))
        if key in indexed or key not in {(r["case_id"], r["replicate"]) for r in selected["trials"]}:
            raise ValueError("TRIAL_DUPLICATE_OR_UNREGISTERED")
        indexed[key] = row
    outcomes, directions, recovery, rows, run_ids = [], [], [], [], set()
    for trial in selected["trials"]:
        row = indexed.get((trial["case_id"], trial["replicate"]), {})
        case = next(value for value in oracle["cases"] if value["id"] == trial["case_id"])
        effect = controlled_effect(case, row.get("before", {}), row.get("fault", {}), row.get("after", {}))
        if row.get("target_stable_after") is False:
            effect["checks"]["target_stable_after"] = False
            effect["passed"] = False
        baseline = model_direction(row.get("fault", {}), case["direction_kinds"])
        candidate = model_direction(row.get("after", {}), {"normal_load", "insufficient_evidence"})
        for value in (baseline, candidate):
            if value.get("run_id"):
                if value["run_id"] in run_ids:
                    raise ValueError("DIAGNOSIS_RUN_REUSED")
                run_ids.add(value["run_id"])
        outcomes.append(effect["passed"])
        directions.append(baseline["matched"])
        recovery.append(candidate["matched"])
        rows.append({**trial, "controlled_effect": effect, "fault_direction": baseline, "after_direction": candidate})
    raw_budget = capture.get("budget", {})
    budget = {key: raw_budget[key] for key in ("accounted_model_calls", "accounted_tool_calls", "unknown_runs_charged_worst_case", "model_limit", "tool_limit") if key in raw_budget and type(raw_budget[key]) is int and 0 <= raw_budget[key] <= 1000}
    return {"schema_version": 1, "kind": "controlled_causal_intervention_scoring", "plan_sha256": selected["plan_sha256"], "manifest_sha256": common.canonical_sha256(manifest), "environment_sha256": capture["environment_sha256"], "metrics": {"controlled_intervention_verified": common.ratio(outcomes), "fault_supported_direction_top3": common.ratio(directions), "normal_after_direction_top3": common.ratio(recovery), "exact_root_cause_accuracy": None, "independent_human_reviews": 0, "semantic_false_positive_rate": None, "semantic_abstention_accuracy": None}, "rounds": rows, "budget": budget, "limitations": oracle["limits"] + ["人工评审与模型方向标签分列，未把模型文字或kind当成精确根因。", "故障前后为相同固定工作负载的闭环客户端，并非固定到达率SLA压测。", "环境摘要是控制器提供的冻结来源绑定，不是远端可信执行证明。"]}


def review_template(capture):
    measured = score(capture)
    reviews = []
    for row in measured["rounds"]:
        for phase in ("fault_direction", "after_direction"):
            value = row[phase]
            if value.get("observed"):
                reviews.append({"case_id": row["case_id"], "replicate": row["replicate"], "phase": phase.removesuffix("_direction"), "run_id": value["run_id"], "task_id": value["task_id"], "analysis_sha256": value["analysis_sha256"], "report_sha256": value["report_sha256"], "assessment_type": "human_manual_review", "reviewer": None, "independence_attested": False, "blind_to_oracle": False, "exact_root_cause_supported": None, "normal_false_positive": None, "appropriate_abstention": None, "notes": None})
    return {"schema_version": 1, "kind": "controlled_causal_human_review", "plan_sha256": measured["plan_sha256"], "reviews": reviews, "review_api_contract": {"identity": ["plan_sha256", "case_id", "replicate", "phase", "run_id", "report_sha256"], "reviewer_required": True, "independence_attested_required": True, "ai_review_allowed": False, "save_state": "unreviewed_until_real_human_submits"}, "instructions": ["报告页可渲染此结构供独立真人填写；没有真人提交前保持null。", "先遮蔽oracle评价报告，再核对受控真值；实际未遮蔽不可声称盲评。", "精确根因须与本轮客观干预结论一起审查，方向命中不自动等于根因正确。"]}


def apply_reviews(capture, submitted):
    measured, template = score(capture), review_template(capture)
    if not isinstance(submitted, dict) or not set(submitted) <= set(template) or submitted.get("schema_version") != 1 or submitted.get("kind") != template["kind"] or submitted.get("plan_sha256") != template["plan_sha256"] or not isinstance(submitted.get("reviews"), list):
        raise ValueError("HUMAN_REVIEW_ENVELOPE_INVALID")
    indexed = {(row["case_id"], row["replicate"], row["phase"], row["run_id"]): row for row in template["reviews"]}
    seen, roots, false_positives, abstentions, actual_reviews = set(), [], [], [], 0
    for row in submitted["reviews"]:
        if not isinstance(row, dict) or not template["reviews"] or not set(row) <= set(template["reviews"][0]) or row.get("notes") is not None and (not isinstance(row["notes"], str) or len(row["notes"]) > 1000):
            raise ValueError("HUMAN_REVIEW_FIELDS_OR_NOTES_INVALID")
        key = tuple(row.get(name) for name in ("case_id", "replicate", "phase", "run_id"))
        if key not in indexed or key in seen:
            raise ValueError("HUMAN_REVIEW_UNKNOWN_OR_REUSED")
        seen.add(key)
        original = indexed[key]
        if any(row.get(name) != original[name] for name in ("task_id", "analysis_sha256", "report_sha256")):
            raise ValueError("HUMAN_REVIEW_SOURCE_CHANGED")
        labels = [row.get(name) for name in ("exact_root_cause_supported", "normal_false_positive", "appropriate_abstention")]
        if all(value is None for value in labels):
            continue
        if row.get("assessment_type") != "human_manual_review" or row.get("independence_attested") is not True or not isinstance(row.get("reviewer"), str) or not 1 <= len(row["reviewer"].strip()) <= 120 or any(value is not None and type(value) is not bool for value in labels):
            raise ValueError("INDEPENDENT_HUMAN_ATTESTATION_REQUIRED")
        effect = next(value for value in measured["rounds"] if value["case_id"] == key[0] and value["replicate"] == key[1])["controlled_effect"]["passed"]
        if labels[0] is True and not effect:
            raise ValueError("ROOT_CAUSE_REVIEW_REQUIRES_VERIFIED_INTERVENTION")
        if labels[0] is not None:
            roots.append(labels[0])
        if labels[1] is not None:
            if key[2] != "after":
                raise ValueError("NORMAL_FALSE_POSITIVE_REQUIRES_AFTER_CONTROL")
            false_positives.append(labels[1])
        if labels[2] is not None:
            abstentions.append(labels[2])
        actual_reviews += 1
    measured["independent_human_metrics"] = {"reviewed_reports": actual_reviews, "available_reports": len(indexed), "exact_root_cause_support": common.ratio(roots) if roots else None, "normal_control_false_positive_rate": common.ratio(false_positives) if false_positives else None, "appropriate_abstention": common.ratio(abstentions) if abstentions else None, "identity_is_attestation_not_remote_verification": True}
    return measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "score", "human-template", "human-score"])
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--reviews", type=Path)
    args = parser.parse_args()
    result = plan(args.repetitions, case_ids=args.cases) if args.command == "plan" else apply_reviews(load(args.input), load(args.reviews)) if args.command == "human-score" else (score if args.command == "score" else review_template)(load(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


if __name__ == "__main__":
    main()
