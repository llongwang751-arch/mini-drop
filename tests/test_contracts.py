"""Contract deliverables: current Go public API and TaskKind JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.drop_insight.schemas import DiagnosisBudget

CONTRACTS = Path(__file__).resolve().parents[1] / "docs" / "contracts"
PROTO = Path(__file__).resolve().parents[1] / "proto" / "hotmethod.proto"


def _load_openapi() -> dict:
    return json.loads(
        (CONTRACTS / "openapi.v1.json").read_text(encoding="utf-8")
    )


def _load_taskkind_schema() -> dict:
    return json.loads(
        (CONTRACTS / "taskkind.schema.json").read_text(encoding="utf-8")
    )


def test_openapi_spec_exists_and_is_valid():
    spec = _load_openapi()
    assert spec["openapi"].startswith("3.")
    assert "paths" in spec and len(spec["paths"]) >= 30


def test_openapi_routes_match_public_implementations():
    from scripts.check_openapi_routes import _go_routes, _openapi_routes, _python_routes

    assert _go_routes() | _python_routes() == _openapi_routes()


def test_openapi_covers_new_feature_endpoints():
    spec = _load_openapi()
    paths = spec["paths"]
    assert "/api/schedules" in paths
    assert "/api/metrics" in paths
    assert "/api/v2/diagnoses" in paths
    assert "/api/v2/diagnoses/{diagnosis_id}/fix/verify" in paths
    assert "/api/v2/showcases/lats-replays" in paths
    assert "/api/v2/showcases/lats-replays/{scenario_id}/runs" in paths
    assert "/api/composite-tasks" not in paths
    assert "/api/v1/diagnoses" not in paths


def test_openapi_create_diagnosis_exposes_autonomous_and_assisted_modes():
    spec = _load_openapi()
    schema = spec["components"]["schemas"]["CreateDiagnosisRequest"]
    properties = schema["properties"]

    assert "evaluation_oracle" not in properties
    assert schema["required"] == ["query"]
    assert "symptom" not in properties
    assert properties["query"]["minLength"] == 3
    assert properties["mode"]["enum"] == [
        "AUTONOMOUS",
        "ASSISTED",
        "OBSERVE_ONLY",
        "REPRODUCTION",
        "REPLAY",
    ]
    assert properties["auto_scope"]["default"] is False
    assert properties["skill_policy"] == {
        "type": "string",
        "enum": ["AUTO", "DISABLED"],
        "default": "AUTO",
    }
    assert properties["budget"]["$ref"].endswith("/DiagnosisBudget")


def test_openapi_exposes_strict_lats_budget_and_frozen_replay_contract():
    spec = _load_openapi()
    budget = spec["components"]["schemas"]["DiagnosisBudget"]["properties"]
    request = spec["components"]["schemas"]["StartFrozenReplayRequest"]

    assert budget["lats_selection_policy"]["enum"] == ["UCT", "PUCT"]
    assert budget["min_diagnosis_rounds"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 4,
        "default": 1,
    }
    assert budget["max_lats_iterations"]["type"] == ["integer", "null"]
    assert budget["lats_value_lambda"]["maximum"] == 1
    assert request["additionalProperties"] is False
    assert request["required"] == ["client_run_id"]
    assert request["properties"]["client_run_id"]["minLength"] == 8


def test_diagnosis_budget_enforces_a_valid_minimum_round_range():
    assert DiagnosisBudget().min_diagnosis_rounds == 1
    assert DiagnosisBudget(
        min_diagnosis_rounds=4,
        max_diagnosis_rounds=4,
    ).max_diagnosis_rounds == 4

    with pytest.raises(ValidationError, match="min_diagnosis_rounds"):
        DiagnosisBudget(min_diagnosis_rounds=3, max_diagnosis_rounds=2)

    with pytest.raises(ValidationError):
        DiagnosisBudget(min_diagnosis_rounds=5)


def test_taskkind_schema_is_valid_json_schema():
    schema = _load_taskkind_schema()
    assert schema["$schema"].startswith("https://json-schema.org")
    required = {
        "id", "name", "runner", "analysis_pipeline",
        "default_duration_seconds", "max_duration_seconds",
    }
    assert required <= set(schema.get("required", []))
    assert "properties" in schema


def test_openapi_contains_idempotency_aware_create_task():
    spec = _load_openapi()
    post = spec["paths"]["/api/tasks"]["post"]
    assert {"$ref": "#/components/parameters/IdempotencyKey"} in post["parameters"]


def test_task_desc_has_typed_payload_and_resource_budget():
    source = PROTO.read_text(encoding="utf-8")
    assert "ResourceBudget resource_budget = 23;" in source
    assert "oneof payload" in source
    for field in (
        "PerfTask perf",
        "AsyncProfilerTask async_profiler",
        "PprofTask pprof",
        "EbpfTask ebpf",
        "PySpyTask pyspy",
        "MemorySmapsTask memory_smaps",
        "SystemMetricsTask system_metrics",
        "ContinuousPerfTask continuous_perf",
    ):
        assert field in source
