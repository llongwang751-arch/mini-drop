"""重复试验与独立人工录入边界回归；API对象均为显式控制器夹具，未运行真实模型。"""

from __future__ import annotations

import sys
import unittest
from copy import deepcopy

import diagnostic_experiment as experiment
import evaluate_diagnostics as evaluation
import test_evaluate_diagnostics as fixture_tests


class DiagnosticExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = evaluation.load_cases(evaluation.FACT_CASES)
        fixture_tests.DiagnosticEvaluationTests.setUpClass()

    def pair(self):
        payload = fixture_tests.DiagnosticEvaluationTests().pair()
        case = self.cases["live-normal-load-v2-holdout"]
        for row in payload["runs"]:
            row["case_id"] = case["id"]
            row["replicate_id"] = "1"
            row["fixture_provenance"]["case_sha256"] = evaluation.canonical_sha256(case)
            row["analysis_result"] = deepcopy(case["profile"])
            row["run"]["report_protocol"] = 2
            row["run"]["report"].update(
                report_version=2,
                facts=[
                    {
                        "id": "F1",
                        "evidence_id": "E1",
                        "path": "/total_weight",
                        "value": 1000,
                        "unit": "samples",
                    }
                ],
            )
        return payload

    def reviewed(self, payload):
        reviews = experiment.review_template(payload, self.cases, "experience")
        review = reviews["reviews"][0]
        review.update(
            reviewer="independent-human-test-fixture",
            independence_attested=True,
            category="normal_load",
            abstained=False,
            actionable_defect=False,
        )
        return reviews

    def test_v2_case_set_replays_analyzer_and_preserves_seed_hashes(self):
        self.assertEqual(len(self.cases), 6)
        original = evaluation.load_cases()
        for key, case in self.cases.items():
            if case["split"] == "dev":
                self.assertEqual(
                    evaluation.canonical_sha256(case),
                    evaluation.canonical_sha256(original[key]),
                )
        self.assertEqual(
            {
                case["family"]
                for case in self.cases.values()
                if case["split"] == "holdout"
            },
            {"normal_load", "insufficient_evidence", "mixed_signals"},
        )
        self.assertEqual(
            evaluation.manifest(self.cases)["dataset_id"], "drop-diagnostic-facts-v2"
        )
        sys.path.insert(0, str(evaluation.ROOT / "analyzer"))
        from drop_analyzer.folded import analyze_folded
        from drop_analyzer.result import make_result

        for case in self.cases.values():
            actual = make_result(
                analyze_folded(case["reproduction"]["collapsed_text"].encode()),
                "IMPORT_COLLAPSED",
                {"source": "import"},
            )
            self.assertEqual(
                evaluation.result_projection(actual),
                evaluation.result_projection(case["profile"]),
            )

    def test_reproducible_blocked_plan_has_all_trials_and_no_model_results(self):
        plan = experiment.build_plan(self.cases, 3, 20261022, "experience")
        self.assertEqual(len(plan["trials"]), 24)
        self.assertEqual(
            plan, experiment.build_plan(self.cases, 3, 20261022, "experience")
        )
        self.assertNotEqual(
            plan["trials"],
            experiment.build_plan(self.cases, 3, 1, "experience")["trials"],
        )
        for index in range(0, 24, 2):
            a, b = plan["trials"][index : index + 2]
            self.assertEqual(
                (a["case_id"], a["replicate_id"]), (b["case_id"], b["replicate_id"])
            )
            self.assertEqual(
                {a["knowledge_mode"], b["knowledge_mode"]}, {"off", "experience"}
            )
        self.assertNotIn("metrics", plan)
        self.assertEqual(plan["mode"], "plan_only_no_network")

    def test_invalid_repetitions_or_dev_case_cannot_be_preregistered(self):
        for count, keys in (
            (0, None),
            (31, None),
            (1, ["seed-cpu-folded-dev"]),
            (1, ["unknown"]),
        ):
            with self.assertRaises(evaluation.EvaluationError):
                experiment.build_plan(self.cases, count, 1, "experience", keys)

    def test_actual_repetition_ids_require_independent_runs_and_complete_plan(self):
        payload = self.pair()
        extra = deepcopy(payload["runs"])
        for index, row in enumerate(extra):
            row["replicate_id"] = "2"
            row["run"]["id"] = str(211 + index)
        payload["runs"].extend(extra)
        payload["experiment"] = {
            "case_ids": [payload["runs"][0]["case_id"]],
            "repetitions": 2,
            "report_protocol": 2,
        }
        result = evaluation.evaluate_live(payload, self.cases)
        self.assertEqual(result["pair_count"], 2)
        self.assertEqual(result["case_count"], 1)
        self.assertTrue(result["experiment_plan_declared"])
        self.assertIsNone(
            result["by_knowledge_mode"]["off"]["root_cause_category_hit"]["rate"]
        )
        missing = deepcopy(payload)
        missing["runs"] = missing["runs"][:2]
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate_live(missing, self.cases)
        reused = deepcopy(payload)
        reused["runs"][2]["run"]["id"] = "201"
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate_live(reused, self.cases)
        changed = deepcopy(payload)
        for row in changed["runs"][2:]:
            row["run"]["goal"] = "另一个问题"
        with self.assertRaises(evaluation.EvaluationError):
            evaluation.evaluate_live(changed, self.cases)

    def test_blank_review_template_does_not_fabricate_semantic_scores(self):
        payload = self.pair()
        template = experiment.review_template(payload, self.cases, "experience")
        self.assertNotIn("knowledge_mode", template["reviews"][0])
        self.assertFalse(template["reviews"][0]["independence_attested"])
        copied = experiment.apply_reviews(payload, template, self.cases, "experience")
        self.assertEqual(payload, copied)
        self.assertIsNone(
            evaluation.evaluate_live(copied, self.cases)["by_knowledge_mode"]["off"][
                "root_cause_category_hit"
            ]["rate"]
        )

    def test_independent_human_declaration_is_bound_to_exact_report(self):
        payload = self.pair()
        reviews = self.reviewed(payload)
        reviewed = experiment.apply_reviews(payload, reviews, self.cases, "experience")
        self.assertNotIn("assessment", payload["runs"][0])
        result = evaluation.evaluate_live(reviewed, self.cases)
        self.assertEqual(
            result["by_knowledge_mode"]["off"]["manual_review_coverage"]["rate"], 1
        )
        self.assertEqual(
            result["by_knowledge_mode"]["off"]["root_cause_category_hit"]["rate"], 1
        )
        self.assertIsNone(
            result["by_knowledge_mode"]["experience"]["root_cause_category_hit"]["rate"]
        )
        self.assertEqual(
            result["by_knowledge_mode"]["off"]["normal_load_false_positive_rate"][
                "rate"
            ],
            0,
        )

    def test_agent_review_missing_independence_or_changed_report_are_rejected(self):
        payload = self.pair()
        for field, value in (
            ("assessment_type", "agent_review"),
            ("independence_attested", False),
            ("category", None),
            ("report_sha256", "0" * 64),
        ):
            reviews = self.reviewed(payload)
            reviews["reviews"][0][field] = value
            with self.assertRaises(evaluation.EvaluationError):
                experiment.apply_reviews(payload, reviews, self.cases, "experience")
        with self.assertRaises(evaluation.EvaluationError):
            experiment.apply_reviews(
                payload,
                {"schema_version": 1, "kind": "agent_review", "reviews": []},
                self.cases,
                "experience",
            )
        changed = self.pair()
        reviews = self.reviewed(changed)
        changed["runs"][0]["run"]["report"]["summary"] = "报告已更新"
        with self.assertRaises(evaluation.EvaluationError):
            experiment.apply_reviews(changed, reviews, self.cases, "experience")

    def test_existing_human_review_cannot_be_silently_overwritten(self):
        payload = self.pair()
        reviews = self.reviewed(payload)
        reviewed = experiment.apply_reviews(payload, reviews, self.cases, "experience")
        with self.assertRaises(evaluation.EvaluationError):
            experiment.apply_reviews(reviewed, reviews, self.cases, "experience")


if __name__ == "__main__":
    unittest.main()
