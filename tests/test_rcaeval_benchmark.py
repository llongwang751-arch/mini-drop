from __future__ import annotations

from pathlib import Path

import pytest

from server.app.drop_insight.rcaeval_benchmark import (
    METRIC_FAMILIES,
    SkillGate,
    TelemetrySignature,
    evaluate_paired_replay,
    load_telemetry_signature,
    opaque_case_id,
    predict_with_skill,
    train_route_skills,
)


def _signature(case_id: str, root: str, family: str) -> TelemetrySignature:
    services = {"orders": {}, "payments": {}, "catalog": {}}
    for service in services:
        for item in METRIC_FAMILIES:
            services[service][item] = 1.0
    services[root][family] = 20.0
    features = {item: float(item == family) for item in METRIC_FAMILIES}
    return TelemetrySignature(case_id, "TEST", services, features, 100, 100)


def test_opaque_case_id_does_not_expose_oracle_tokens():
    value = opaque_case_id("re1ob_checkoutservice_cpu_4")
    assert value.startswith("rcaeval-")
    assert "checkoutservice" not in value
    assert "cpu" not in value


def test_route_skill_stores_route_not_root_service():
    examples = [
        (_signature("train-a", "orders", "cpu"), "orders", "cpu"),
        (_signature("train-b", "payments", "cpu"), "payments", "cpu"),
    ]
    skills = train_route_skills(examples)
    assert len(skills) == 1
    serialized = repr(skills[0])
    assert "orders" not in serialized
    assert "payments" not in serialized
    prediction = predict_with_skill(_signature("test", "catalog", "cpu"), skills)
    assert prediction.service_ranking[0] == "catalog"
    assert prediction.inferred_fault == "cpu"


def test_paired_replay_freezes_predictions_and_scores_oracle():
    train = [(_signature("train", "orders", "mem"), "orders", "mem")]
    skills = train_route_skills(train)
    report = evaluate_paired_replay(
        [(_signature("test", "catalog", "mem"), "catalog", "mem")], skills
    )
    assert report["skill_enabled"]["top1"] == 1.0
    assert report["skill_route_selection"]["accuracy_when_activated"] == 1.0


def test_skill_gate_falls_back_to_baseline_when_match_is_ambiguous():
    skills = train_route_skills(
        [
            (_signature("cpu", "orders", "cpu"), "orders", "cpu"),
            (_signature("mem", "orders", "mem"), "orders", "mem"),
        ]
    )
    mixed = _signature("mixed", "catalog", "cpu")
    mixed = TelemetrySignature(
        mixed.case_id,
        mixed.dataset,
        mixed.service_family_scores,
        {item: 1.0 for item in METRIC_FAMILIES},
        mixed.pre_samples,
        mixed.post_samples,
    )
    prediction = predict_with_skill(mixed, skills, gate=SkillGate(min_margin=0.99))
    assert prediction.selected_skill_id is None


def test_missing_metric_families_do_not_add_alphabetical_rank_noise():
    signature = TelemetrySignature(
        "missing-families",
        "TEST",
        {
            "alpha": {"cpu": 1.0},
            "zulu": {"cpu": 20.0},
        },
        {"cpu": 1.0, "mem": 0.0, "load": 0.0, "latency": 0.0, "error": 0.0},
        100,
        100,
    )
    assert predict_with_skill(signature, []).service_ranking[0] == "zulu"


def test_unvalidated_skill_is_rejected_by_allowlist():
    skills = train_route_skills(
        [(_signature("train", "orders", "cpu"), "orders", "cpu")]
    )
    prediction = predict_with_skill(
        _signature("test", "catalog", "cpu"),
        skills,
        gate=SkillGate(allowed_skill_ids=()),
    )
    assert prediction.selected_skill_id is None


def test_load_telemetry_signature_splits_before_and_after(tmp_path: Path):
    duckdb = pytest.importorskip("duckdb")
    parquet = tmp_path / "metrics.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            COPY (
                SELECT i AS time,
                       CASE WHEN i > 100 THEN 90.0 ELSE 10.0 END AS orders_cpu,
                       5.0 AS payments_cpu,
                       20.0 AS orders_mem,
                       20.0 AS payments_mem
                FROM range(0, 201) table_(i)
            ) TO ? (FORMAT PARQUET)
            """,
            [str(parquet)],
        )
    finally:
        connection.close()
    signature = load_telemetry_signature(
        parquet,
        case_id="opaque-test",
        dataset="TEST",
        inject_time=100,
        window_seconds=100,
        guard_seconds=0,
    )
    assert signature.service_family_scores["orders"]["cpu"] > 10
    assert signature.service_family_scores["payments"]["cpu"] == 0
    assert not hasattr(signature, "root_cause_service")
    assert not hasattr(signature, "metrics_path")
