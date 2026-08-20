from server.app.diagnostic_case_adapter import (
    adapt_cluster_diagnosis,
    adapt_drop_insight,
    adapt_legacy_rca,
    merge_diagnostic_cases,
)


def test_cluster_adapter_preserves_strategy_and_native_link():
    case = adapt_cluster_diagnosis(
        {
            "diagnosis_id": "diag_1",
            "raw_query": "service-a CPU 变高",
            "status": "COLLECTING",
            "normalized_intent": {"analysis_strategy": "DECISION_TREE"},
            "target_scope": {"service_id": "service-a"},
            "child_task_ids": ["task_1"],
            "hypothesis_graph": {"nodes": [{"id": "h1"}]},
            "conclusion_versions": [],
        }
    )

    assert case["case_id"] == "diag_1"
    assert case["source"] == "cluster_diagnosis_v1"
    assert case["strategy"] == "DECISION_TREE"
    assert case["hypothesis_count"] == 1
    assert case["canonical_status"] == "COLLECTING"
    assert case["task_ids"] == ["task_1"]
    assert case["legacy_links"]["detail"] == "/api/v1/diagnoses/diag_1"


def test_cluster_adapter_counts_current_hypotheses_shape():
    case = adapt_cluster_diagnosis(
        {
            "diagnosis_id": "diag_current",
            "hypothesis_graph": {"hypotheses": [{"hypothesis_id": "h1"}, {"hypothesis_id": "h2"}]},
        }
    )

    assert case["hypothesis_count"] == 2


def test_drop_insight_adapter_keeps_evidence_hypothesis_semantics():
    case = adapt_drop_insight(
        {
            "diagnosis_id": "insight_1",
            "query": "订单服务变慢",
            "status": "HYPOTHESIZING",
            "target": {"service": "order-service"},
            "hypotheses": [{"id": "h1"}, {"id": "h2"}],
            "evidence": [{"id": "e1"}],
        }
    )

    assert case["source"] == "drop_insight_v2"
    assert case["strategy"] == "EVIDENCE_HYPOTHESIS"
    assert case["hypothesis_count"] == 2
    assert case["evidence_count"] == 1
    assert case["canonical_status"] == "PLANNING"
    assert case["legacy_links"]["detail"] == "/api/v2/diagnoses/insight_1"


def test_merge_is_read_only_sorted_and_paginated():
    result = merge_diagnostic_cases(
        [
            {
                "diagnosis_id": "diag_old",
                "raw_query": "old",
                "status": "COMPLETED",
                "updated_at": "2026-07-29T10:00:00+08:00",
            }
        ],
        [
            {
                "diagnosis_id": "insight_new",
                "query": "new",
                "status": "UNDERSTANDING",
                "updated_at": "2026-07-30T10:00:00+08:00",
            }
        ],
        legacy_items=[
            {
                "run": {
                    "id": "rca_middle",
                    "task_id": "task_legacy",
                    "summary": "legacy RCA",
                    "status": "SUCCEEDED",
                    "created_at": "2026-07-29T12:00:00+08:00",
                    "finished_at": "2026-07-29T12:01:00+08:00",
                },
                "tool_results": [{"evidence_ref": "e1"}],
                "reports": [{"id": "r1"}],
            }
        ],
        limit=1,
        offset=0,
    )

    assert result["total"] == 3
    assert [item["case_id"] for item in result["items"]] == ["insight_new"]
    assert result["compatibility"] == {
        "v1_preserved": True,
        "v2_preserved": True,
        "legacy_rca_preserved": True,
        "write_mode": "native_api_only",
    }


def test_legacy_rca_adapter_preserves_task_evidence_and_report():
    case = adapt_legacy_rca({
        "run": {
            "id": "rca_1",
            "task_id": "task_1",
            "status": "SUCCEEDED",
            "summary": "CPU hotspot",
        },
        "tool_results": [{"evidence_ref": "e1"}, {"evidence_ref": "e2"}],
        "report": {"id": "report_1"},
    })

    assert case["source"] == "legacy_rca"
    assert case["canonical_status"] == "COMPLETED"
    assert case["task_ids"] == ["task_1"]
    assert case["evidence_count"] == 2
    assert case["report_version_count"] == 1
