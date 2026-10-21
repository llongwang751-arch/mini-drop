"""验证评分器的输入来源、分区泄漏与计分边界，不把这些控制器样例称为模型实测。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from copy import deepcopy
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "diagnostic_evaluation", Path(__file__).with_name("evaluate_diagnostics.py")
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class DiagnosticEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = evaluation.load_cases()

    def pair(self):
        case = self.cases["live-cpu-folded-holdout"]
        rows = []
        for index, mode in enumerate(("off", "experience")):
            rows.append(
                {
                    "case_id": case["id"],
                    "knowledge_mode": mode,
                    "fixture_provenance": {
                        "source": "imported_synthetic",
                        "case_sha256": evaluation.canonical_sha256(case),
                        "task_id": "101",
                    },
                    "analysis_result": deepcopy(case["profile"]),
                    "elapsed_ms": 1000 + index * 100,
                    "run": {
                        "id": str(201 + index),
                        "task_id": "101",
                        "attempt_id": "301",
                        "analysis_job_id": "401",
                        "analysis_run": 1,
                        "goal": "只解释当前导入观测并注明边界",
                        "mode": "model",
                        "knowledge_mode": mode,
                        "status": "success",
                        "trace": [
                            {
                                "evidence_id": "E1",
                                "tool": "inspect_hotspots",
                                "observation": {},
                            },
                            {
                                "evidence_id": "E2",
                                "tool": "read_diagnostic_knowledge",
                                "observation": {},
                            },
                        ],
                        "report": {
                            "hypotheses": [{"evidence_ids": ["E1"]}],
                            "agent_runtime": {
                                "framework": "agentscope",
                                "framework_version": "2.0.10",
                                "agent_class": "agentscope.agent.Agent",
                                "model_calls": 2,
                                "usage": {"input_tokens": 200, "output_tokens": 50},
                            },
                            **(
                                {
                                    "knowledge": {
                                        "skills": [
                                            {"id": s}
                                            for s in case["ground_truth"]["skill_ids"]
                                        ],
                                        "cases": [],
                                    }
                                }
                                if mode != "off"
                                else {}
                            ),
                        },
                    },
                }
            )
        return {"schema_version": 1, "mode": "live", "runs": rows}

    def test_fixed_dataset_marks_synthetic_and_has_distinct_partitions(self):
        self.assertEqual(len(self.cases), 22)
        manifest = evaluation.manifest(self.cases)
        self.assertEqual(sum(c["live_eligible"] for c in manifest["cases"]), 4)
        for case in self.cases.values():
            self.assertFalse(case["ground_truth"]["causal_root_proven"])

    def test_canonical_sha_is_order_invariant_and_content_bound(self):
        self.assertEqual(
            evaluation.canonical_sha256({"乙": 2, "甲": 1}),
            evaluation.canonical_sha256({"甲": 1, "乙": 2}),
        )
        self.assertNotEqual(
            evaluation.canonical_sha256({"n": 1}), evaluation.canonical_sha256({"n": 2})
        )
        with self.assertRaises(ValueError):
            evaluation.canonical_sha256({"n": float("nan")})

    def test_live_fixture_projection_replays_real_folded_analyzer(self):
        # 与真正的分析器契约交叉验证固定数据，避免手工 envelope 冒充在线结果。
        sys.path.insert(0, str(evaluation.ROOT / "analyzer"))
        from drop_analyzer.folded import analyze_folded
        from drop_analyzer.result import make_result

        for case in self.cases.values():
            if "reproduction" not in case:
                continue
            parsed = analyze_folded(
                case["reproduction"]["collapsed_text"].encode("utf-8")
            )
            actual = make_result(parsed, "IMPORT_COLLAPSED", {"source": "import"})
            self.assertEqual(
                evaluation.result_projection(actual),
                evaluation.result_projection(case["profile"]),
            )

    def test_no_manual_review_means_no_semantic_score_or_invented_price(self):
        result = evaluation.evaluate_live(self.pair(), self.cases)
        for mode in ("off", "experience"):
            score = result["by_knowledge_mode"][mode]
            self.assertIsNone(score["root_cause_category_hit"]["rate"])
            self.assertIsNone(score["normal_load_false_positive_rate"]["rate"])
            self.assertIsNone(score["estimated_cost"])
            self.assertEqual(
                score["model_tokens"], {"input_tokens": 200, "output_tokens": 50}
            )
            self.assertEqual(score["hypotheses_with_current_observation"]["rate"], 1)
        self.assertIsNone(
            result["by_knowledge_mode"]["off"]["skill_routing_exact_match"]["rate"]
        )
        self.assertEqual(
            result["by_knowledge_mode"]["experience"]["skill_routing_exact_match"][
                "rate"
            ],
            1,
        )

    def test_explicit_manual_review_scores_category_without_keyword_guessing(self):
        payload = self.pair()
        payload["runs"][0]["assessment"] = {
            "method": "manual_review",
            "reviewer": "独立测试审阅者",
            "category": "allocation_gc",
            "abstained": False,
            "actionable_defect": False,
        }
        result = evaluation.evaluate_live(payload, self.cases)
        self.assertEqual(
            result["by_knowledge_mode"]["off"]["root_cause_category_hit"]["rate"], 0
        )
        self.assertIsNone(
            result["by_knowledge_mode"]["experience"]["root_cause_category_hit"]["rate"]
        )

    def test_missing_usage_stays_unknown_and_negative_tokens_rejected(self):
        payload = self.pair()
        del payload["runs"][0]["run"]["report"]["agent_runtime"]["usage"]
        result = evaluation.evaluate_live(payload, self.cases)
        self.assertIsNone(
            result["by_knowledge_mode"]["off"]["model_tokens"]["input_tokens"]
        )
        payload["runs"][1]["run"]["report"]["agent_runtime"]["usage"][
            "input_tokens"
        ] = -1
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate_live(payload, self.cases)

    def test_unknown_or_prior_only_refs_do_not_count_as_current_evidence(self):
        payload = self.pair()
        payload["runs"][1]["run"]["report"]["hypotheses"] = [
            {"evidence_ids": ["E2", "E999"]}
        ]
        result = evaluation.evaluate_live(payload, self.cases)
        score = result["by_knowledge_mode"]["experience"]
        self.assertEqual(score["hypotheses_with_current_observation"]["rate"], 0)
        self.assertEqual(score["known_reference_coverage"]["rate"], 0.5)

    def test_missing_pairs_and_duplicates_are_rejected(self):
        for mutate in (
            lambda p: p["runs"].pop(),
            lambda p: p["runs"].append(deepcopy(p["runs"][0])),
        ):
            payload = self.pair()
            mutate(payload)
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.evaluate_live(payload, self.cases)

    def test_top_level_and_row_shapes_are_bounded(self):
        for payload in ([], {"schema_version": 1, "mode": "live", "runs": [None]}):
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.evaluate_live(payload, self.cases)

    def test_provenance_and_real_analyzer_projection_are_required(self):
        for mutate in (
            lambda p: p["runs"][0]["fixture_provenance"].update(
                source="production_history"
            ),
            lambda p: p["runs"][0]["fixture_provenance"].update(case_sha256="0" * 64),
            lambda p: p["runs"][0]["analysis_result"].update(total_samples=7),
        ):
            payload = self.pair()
            mutate(payload)
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.evaluate_live(payload, self.cases)

    def test_online_cannot_claim_minimal_offline_envelope_is_analyzer_result(self):
        payload = self.pair()
        payload["runs"][0]["case_id"] = "cpu-loop-dev"
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate_live(payload, self.cases)

    def test_stale_attempt_or_different_goal_cannot_be_a_pair(self):
        for field, value in (
            ("attempt_id", "999"),
            ("goal", "不同目标"),
            ("id", "201"),
        ):
            payload = self.pair()
            payload["runs"][1]["run"][field] = value
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.evaluate_live(payload, self.cases)

    def test_current_sdk_identity_and_positive_actual_model_calls_are_required(self):
        for field, value in (
            ("agent_class", "ReActAgent"),
            ("framework_version", "1.0.0"),
            ("model_calls", 0),
        ):
            payload = self.pair()
            payload["runs"][1]["run"]["report"]["agent_runtime"][field] = value
            with self.assertRaises(evaluation.EvaluationError):
                evaluation.evaluate_live(payload, self.cases)

    def test_experience_sources_cannot_be_holdout_or_the_current_case(self):
        payload = self.pair()
        holdout = self.cases["live-allocation-folded-holdout"]
        payload["runs"][1]["experience_sources"] = [
            {
                "case_id": holdout["id"],
                "case_sha256": evaluation.canonical_sha256(holdout),
            }
        ]
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate_live(payload, self.cases)

    def test_loaded_experience_requires_explicit_seed_provenance(self):
        payload = self.pair()
        payload["runs"][1]["run"]["report"]["knowledge"]["cases"] = [{"id": "701"}]
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate_live(payload, self.cases)
        source = self.cases["seed-cpu-folded-dev"]
        payload["runs"][1]["experience_sources"] = [
            {
                "case_id": source["id"],
                "case_sha256": evaluation.canonical_sha256(source),
            }
        ]
        result = evaluation.evaluate_live(payload, self.cases)
        self.assertEqual(result["observations"][0]["loaded_experience_count"], 1)

    def test_runtime_metadata_is_not_republished(self):
        payload = self.pair()
        payload["runs"][0]["run"]["report"]["summary"] = "不能公开的示例正文"
        result = evaluation.evaluate_live(payload, self.cases)
        self.assertNotIn("不能公开的示例正文", str(result))
        self.assertNotIn("fixture.calculate_invoice", str(result))


if __name__ == "__main__":
    unittest.main()
