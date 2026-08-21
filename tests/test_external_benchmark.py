from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import json

import pytest

from server.app.diagnosis.external_benchmark import (
    ExternalBenchmarkUnavailable,
    external_benchmark_case,
    external_benchmark_summary,
)


def _write_json(archive: ZipFile, name: str, payload) -> None:
    archive.writestr(name, json.dumps(payload, ensure_ascii=False), compress_type=ZIP_DEFLATED)


def _fixture_archive(path: Path) -> Path:
    case_id = "OB-SINGLE-CPU-001"
    diagnosis_id = "diag-test-r1"
    with ZipFile(path, "w") as archive:
        _write_json(archive, "dataset/ai_ops_v2/manifest.json", {
            "schema_version": "2.0",
            "dataset": "mini-drop-ai-ops-comparison",
            "version": "2.0.0-test",
            "policy": {"oracle_never_sent_to_system_under_test": True},
            "tracks": {"live_single_fault": 1},
            "metrics": ["exact_root_accuracy"],
            "cases": [{"case_id": case_id, "track": "live_single_fault"}],
        })
        _write_json(archive, "dataset/ai_ops_v2/public/cases.json", {
            "cases": [{
                "case_id": case_id,
                "query": "service CPU is high",
                "service_hint": "service-a",
                "environment": "production",
            }],
        })
        _write_json(archive, "dataset/ai_ops_v2/private/oracles.json", {
            "cases": [{
                "case_id": case_id,
                "expected": {
                    "location_type": "self",
                    "domain_type": "cpu",
                    "classification": "self_code_or_process_pressure",
                },
                "evidence": {"required_collectors": ["sys_metrics", "perf_cpu"]},
            }],
        })
        result = {
            "case_id": case_id,
            "diagnosis_id": diagnosis_id,
            "repetition": 1,
            "score": 94.7,
            "exact_root_match": True,
            "actual": {"location_type": "self", "domain_type": "cpu"},
            "dimensions": {
                "root_cause": {"score": 40, "maximum": 40, "checks": []},
                "evidence": {
                    "score": 25,
                    "maximum": 25,
                    "citation_valid": True,
                    "required_collectors": ["sys_metrics", "perf_cpu"],
                    "observed_collectors": ["sys_metrics", "perf_cpu"],
                },
                "trace": {"runtime_step_count": 2, "present_stages": ["intent", "scope"]},
                "safety": {"unsafe_actions": []},
                "recovery": {"applicable": False, "passed": False},
            },
        }
        _write_json(archive, "results/evaluation.json", {
            "aggregate": {
                "case_count": 1,
                "run_count": 1,
                "mean_score": 94.7,
                "exact_root_accuracy": 1.0,
                "results": [result],
            },
        })
        _write_json(archive, "records/summary.json", {
            "planned_runs": 1,
            "completed_runs": 1,
            "failed_runs": 0,
        })
        _write_json(archive, f"audits/{case_id}__r01.json", {
            "diagnosis_id": diagnosis_id,
            "run": {"status": "COMPLETED", "model_version": "test-model"},
            "conclusion": {"summary": "CPU hotspot", "confidence_level": "high"},
            "evidence_manifest": [{"evidence_id": "ev-1"}],
            "probes": [{"collector": "sys_metrics"}, {"collector": "perf_cpu"}],
            "trace": [{
                "sequence": 1,
                "stage": "intent",
                "component": "intent_parser",
                "decision": "accepted",
                "summary": "parsed query",
                "evidence_refs": [],
            }],
        })
    return path


def test_external_catalog_hides_oracle_but_exposes_metrics(tmp_path):
    archive = _fixture_archive(tmp_path / "ai_ops_v2.zip")
    summary = external_benchmark_summary(archive)

    assert summary["dataset"] == "mini-drop-ai-ops-comparison"
    assert summary["evaluation"]["exact_root_accuracy"] == 1.0
    assert summary["cases"][0]["run_count"] == 1
    assert "oracle" not in summary["cases"][0]
    assert all(not key.startswith("_") for key in summary)


def test_external_case_keeps_public_trace_without_oracle(tmp_path):
    archive = _fixture_archive(tmp_path / "ai_ops_v2.zip")
    detail = external_benchmark_case("OB-SINGLE-CPU-001", path=archive)

    assert "oracle_revealed" not in detail
    assert "oracle" not in detail
    assert detail["runs"][0]["exact_root_match"] is True
    assert detail["runs"][0]["trace"][0]["stage"] == "intent"
    assert detail["runs"][0]["evidence_count"] == 1


def test_external_archive_rejects_missing_contract(tmp_path):
    archive = tmp_path / "broken.zip"
    with ZipFile(archive, "w") as handle:
        _write_json(handle, "manifest.json", {"dataset": "broken"})

    with pytest.raises(ExternalBenchmarkUnavailable):
        external_benchmark_summary(archive)

