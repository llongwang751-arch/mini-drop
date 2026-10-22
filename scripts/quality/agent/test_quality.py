"""均为显式导入的单元控制，不联网、不调用模型，不伪称真实平台验收。"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import quality
spec = importlib.util.spec_from_file_location("jd_agent_run", HERE / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def controlled_run(case_id="jd01", kind="cpu_hotspot", status="success"):
    """模拟实际DTO的合成输入，仅验证评分器，不能作为模型效果证据。"""
    inputs, _, _ = quality.load_dataset()
    return {"id": "23", "task_id": "17", "attempt_id": "18", "analysis_job_id": "19", "analysis_run": 1, "goal": inputs[case_id]["goal"], "mode": "model", "knowledge_mode": "off", "report_protocol": 2, "status": status, "trace": [{"evidence_id": "E1", "tool": "read_profile_summary", "observation": {"total_weight": 1000}}], "report": {"report_version": 2, "facts": [{"id": "F1", "evidence_id": "E1", "path": "/total_weight", "operator": "eq", "value": 1000, "unit": "samples"}], "hypotheses": [{"kind": kind, "fact_ids": ["F1"], "evidence_ids": ["E1"]}], "agent_runtime": {"framework": "agentscope", "framework_version": "2.0.10", "agent_class": "agentscope.agent.Agent", "model_calls": 1}}}


def captured(case_id="jd01", kind="cpu_hotspot", status="success"):
    plan = quality.build_plan([case_id])
    return {"plan": plan, "rows": [{**plan["trials"][0], "task_id": "17", "stage": "diagnosis_terminal", "analysis_sha256": quality.expected_analysis_sha(quality.load_dataset()[0][case_id]), "run": controlled_run(case_id, kind, status)}]}


class QualityTests(unittest.TestCase):
    def test_frozen_manifest_and_distinct_payloads(self):
        inputs, oracle, frozen = quality.load_dataset()
        self.assertEqual(len(inputs), 8)
        self.assertEqual(len({row["collapsed_text"] for row in inputs.values()}), 8)
        self.assertEqual(set(inputs), set(oracle))
        self.assertFalse(frozen["external_blind_test"])
        self.assertFalse(frozen["ground_truth_sent_to_model"])

    def test_oracle_tamper_and_labels_in_model_input_rejected(self):
        for filename in ("oracle.json", "model_inputs.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as folder:
                target = Path(folder)
                for source in quality.DATA.iterdir():
                    (target / source.name).write_bytes(source.read_bytes())
                value = quality.read_json(target / filename)
                value["cases"][0]["accepted_kinds"] = ["normal_load"]
                (target / filename).write_text(json.dumps(value), "utf-8")
                with self.assertRaises(quality.evaluation.EvaluationError):
                    quality.load_dataset(target)

    def test_exact_structured_hint_and_semantic_metrics_remain_null(self):
        result = quality.score(captured())
        self.assertEqual(result["metrics"]["controlled_fixture_hint_top3"], {"numerator": 1, "denominator": 1, "rate": 1.0})
        self.assertIsNone(result["metrics"]["semantic_root_cause_topk"])
        self.assertIsNone(result["metrics"]["normal_load_semantic_false_positive_rate"])
        self.assertIsNone(result["metrics"]["semantic_abstention_accuracy"])

    def test_no_keyword_inference_for_unknown_or_wrong_labels(self):
        for kind in ("unknown", "normal_load"):
            payload = captured(kind=kind)
            payload["rows"][0]["run"]["report"]["summary"] = "cpu_hotspot是正确答案且根因已证实"
            result = quality.score(payload)
            self.assertEqual(result["metrics"]["controlled_fixture_hint_top3"]["numerator"], 0)
        self.assertEqual(quality.score(captured(kind="unknown"))["rows"][0]["controlled_fixture_hint"], "unknown")

    def test_failures_and_missing_rows_stay_in_denominator(self):
        payload = captured(status="failed")
        payload["plan"] = quality.build_plan(["jd01"], 2)
        result = quality.score(payload)
        self.assertEqual(result["metrics"]["controlled_fixture_hint_top3"]["denominator"], 2)
        self.assertEqual(result["metrics"]["controlled_fixture_hint_top3"]["numerator"], 0)
        self.assertEqual(result["rows"][1]["status"], "missing")

    def test_failed_model_is_not_semantic_abstention(self):
        result = quality.score(captured("jd04", "insufficient_evidence", "failed"))
        self.assertEqual(result["metrics"]["controlled_fixture_hint_top3"]["numerator"], 0)
        self.assertIsNone(result["metrics"]["semantic_abstention_accuracy"])

    def test_input_run_goal_and_plan_identity_fenced(self):
        for change in ("goal", "task_id", "mode", "knowledge_mode", "report_protocol"):
            payload = captured()
            payload["rows"][0]["run"][change] = "substituted"
            with self.subTest(change=change), self.assertRaises(quality.evaluation.EvaluationError):
                quality.score(payload)
        payload = captured()
        payload["rows"][0]["input_sha256"] = "0" * 64
        with self.assertRaises(quality.evaluation.EvaluationError):
            quality.score(payload)
        payload = captured()
        payload["rows"][0]["analysis_sha256"] = "0" * 64
        with self.assertRaises(quality.evaluation.EvaluationError):
            quality.score(payload)

    def test_duplicate_runs_or_tasks_rejected(self):
        payload = captured()
        payload["plan"] = quality.build_plan(["jd01"], 2)
        second = copy.deepcopy(payload["rows"][0])
        second["replicate"] = 2
        payload["rows"].append(second)
        with self.assertRaises(quality.evaluation.EvaluationError):
            quality.score(payload)

    def test_invented_evidence_and_forbidden_tool_fail_guard(self):
        for wrong in ("reference", "tool"):
            payload = captured()
            run = payload["rows"][0]["run"]
            if wrong == "reference":
                run["report"]["hypotheses"][0]["evidence_ids"] = ["E999"]
            else:
                run["trace"][0]["tool"] = "execute_shell"
            result = quality.score(payload)
            self.assertEqual(result["metrics"]["successful_report_objective_guard"]["numerator"], 0)

    def test_public_stage_and_status_reject_arbitrary_text_or_types(self):
        for field in ("stage", "status"):
            for value in (None, 17, {}, "arbitrary-private-input-must-not-be-published"):
                payload = captured()
                payload["rows"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(quality.evaluation.EvaluationError):
                    quality.score(payload)

    def test_guard_explicitly_does_not_claim_to_revalidate_fact_scalar(self):
        payload = captured()
        payload["rows"][0]["run"]["report"]["facts"][0]["value"] = 999999
        result = quality.score(payload)
        self.assertFalse(result["rows"][0]["objective_report_guard"]["fact_values_revalidated"])
        self.assertEqual(result["metrics"]["successful_report_objective_guard"]["numerator"], 1)

    def test_invalid_input_rejection_is_separate_and_explicit(self):
        plan = quality.build_plan(["jd08"])
        payload = {"plan": plan, "rows": [{**plan["trials"][0], "stage": "analysis_rejected", "error_code": "INVALID_INPUT", "task_id": "17"}]}
        result = quality.score(payload)
        self.assertEqual(result["metrics"]["invalid_input_rejection"]["numerator"], 1)
        self.assertEqual(result["metrics"]["controlled_fixture_hint_top3"]["denominator"], 0)
        payload["rows"][0]["error_code"] = "AI_BUDGET_EXCEEDED"
        self.assertEqual(quality.score(payload)["metrics"]["invalid_input_rejection"]["numerator"], 0)

    def test_human_template_never_prefills_human_labels(self):
        template = quality.human_template(captured())
        row = template["reviews"][0]
        for field in ("reviewer", "root_cause_topk_correct", "normal_load_false_positive", "appropriate_abstention"):
            self.assertIsNone(row[field])
        self.assertFalse(row["independence_attested"])
        self.assertFalse(row["blind_to_oracle"])

    def test_human_intake_rejects_agent_self_review_and_changed_report(self):
        payload = captured("jd04", "insufficient_evidence")
        template = quality.human_template(payload)
        row = template["reviews"][0]
        row.update(appropriate_abstention=True, reviewer="imported-human-control", independence_attested=True)
        row["assessment_type"] = "agent_review"
        with self.assertRaises(quality.evaluation.EvaluationError):
            quality.score_human(payload, template)
        row["assessment_type"] = "human_manual_review"
        row["report_sha256"] = "0" * 64
        with self.assertRaises(quality.evaluation.EvaluationError):
            quality.score_human(payload, template)

    def test_human_control_is_separate_and_requires_causal_source_for_root_cause(self):
        payload = captured("jd04", "insufficient_evidence")
        template = quality.human_template(payload)
        row = template["reviews"][0]
        row.update(appropriate_abstention=True, reviewer="imported-human-control", independence_attested=True)
        result = quality.score_human(payload, template)
        self.assertEqual(result["human_metrics"]["semantic_abstention_accuracy"]["numerator"], 1)
        self.assertEqual(result["metrics"]["independent_human_review_coverage"]["numerator"], 1)
        self.assertIsNone(result["metrics"]["semantic_abstention_accuracy"])
        row["root_cause_topk_correct"] = True
        with self.assertRaises(quality.evaluation.EvaluationError):
            quality.score_human(payload, template)

    def test_duplicate_json_keys_are_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "duplicate.json"
            path.write_text('{"case_id":"jd01","case_id":"jd07"}', "utf-8")
            with self.assertRaises(quality.evaluation.EvaluationError):
                quality.read_json(path)

    def test_bounded_plan_and_target_no_redirect_or_credentials_in_url(self):
        for repetitions in (0, 4, True):
            with self.assertRaises(quality.evaluation.EvaluationError):
                quality.build_plan(["jd01"], repetitions)
        for origin in ("https://120.24.187.205", "http://82.157.81.79", "https://82.157.81.79.evil.test", "https://user:password@82.157.81.79", "https://82.157.81.79/path", "https://127.0.0.1:18080"):
            with self.assertRaises(runner.RunError):
                runner.validate_origin(origin)
        self.assertEqual(runner.validate_origin("http://127.0.0.1:18080", True), "http://127.0.0.1:18080")
        with self.assertRaises(runner.RunError):
            runner.NoRedirect().redirect_request(None, None, None, None, None, None)

    def test_output_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.json"
            runner.save_new(path, {"synthetic": True})
            with self.assertRaises(FileExistsError):
                runner.save_new(path, {})

    def test_actual_runner_never_sends_oracle_or_confirms_mutation(self):
        case = quality.load_dataset()[0]["jd01"]
        class ClientControl:
            origin = "http://127.0.0.1:18080"
            csrf = ""
            count = 0
            sent = []
            def request(self, method, path, body=None, key=None):
                self.count += 1
                self.sent.append((method, path, body))
                if path.endswith("/login"):
                    return 200, {"data": {"csrf_token": "imported-control-csrf"}}
                if path.endswith("/logout"):
                    return 200, {"data": {}}
                if path == "/api/v1/tasks":
                    return 201, {"data": {"id": "17"}}
                if path.endswith("/diagnoses") and method == "POST":
                    return 202, {"data": {"id": "23", "status": "pending"}}
                if path.endswith("/diagnoses"):
                    return 200, {"data": {"items": [controlled_run()]}}
                result = {"kind": "IMPORT_COLLAPSED", "total_samples": 1000, "node_count": 4, "top_functions": [], "flamegraph": {"name": "all", "value": 1000}, "summary": {"source": "import", "unit": "samples"}}
                return 200, {"data": {"analysis_status": "SUCCESS", "result": result}}
        client = ClientControl()
        payload = runner.execute(client, quality.build_plan(["jd01"]), {"username": "imported-control", "password": "not-a-real-credential"})
        self.assertEqual(payload["rows"][0]["run"]["id"], "23")
        posted = [body for method, path, body in client.sent if method == "POST" and path.endswith("/diagnoses")]
        self.assertEqual(posted, [{"goal": case["goal"], "mode": "model", "consent": True, "knowledge_mode": "off"}])
        serialized = json.dumps(client.sent)
        self.assertNotIn("accepted_kinds", serialized)
        self.assertNotIn("oracle", serialized)
        self.assertFalse(any("/collect" in path or "/confirm" in path or "/decision" in path for _, path, _ in client.sent))

    def test_analysis_timeout_cancels_actual_task_once_and_records_confirmation(self):
        class TimeoutControl:
            origin = "http://127.0.0.1:18080"
            csrf = ""
            count = 0
            sent = []
            def request(self, method, path, body=None, key=None):
                self.count += 1
                self.sent.append((method, path))
                if path.endswith("/login"):
                    return 200, {"data": {"csrf_token": "control-csrf"}}
                if path == "/api/v1/tasks":
                    return 201, {"data": {"id": "17"}}
                return 200, {"data": {}}
        control = TimeoutControl()
        payload = runner.execute(control, quality.build_plan(["jd01"]), {"username": "imported", "password": "control"}, analysis_timeout=0)
        self.assertEqual(payload["rows"][0]["stage"], "analysis_timeout")
        self.assertTrue(payload["rows"][0]["timeout_cancel_confirmed"])
        self.assertEqual(control.sent.count(("POST", "/api/v1/tasks/17/cancel")), 1)
        self.assertNotIn("cleanup_error", payload)

    def test_interrupted_analysis_attempts_cleanup_but_never_claims_failed_cancel_succeeded(self):
        class InterruptedControl:
            origin = "http://127.0.0.1:18080"
            csrf = ""
            count = 0
            def request(self, method, path, body=None, key=None):
                self.count += 1
                if path.endswith("/login"):
                    return 200, {"data": {"csrf_token": "control-csrf"}}
                if path == "/api/v1/tasks":
                    return 201, {"data": {"id": "17"}}
                if path.endswith("/cancel"):
                    return 503, {"error": {"code": "UNAVAILABLE"}}
                if path.endswith("/logout"):
                    return 200, {"data": {}}
                raise runner.RunError("HTTP_TRANSPORT_FAILED")
        payload = runner.execute(InterruptedControl(), quality.build_plan(["jd01"]), {"username": "imported", "password": "control"})
        self.assertEqual(payload["execution_error"], "HTTP_TRANSPORT_FAILED")
        self.assertEqual(payload["cleanup_error"], "TASK_CANCEL_UNCONFIRMED")
        self.assertFalse(payload["interrupted_task_cancel_confirmed"])

    def test_successful_login_with_invalid_csrf_is_captured_and_logout_attempted(self):
        for supplied in (None, "", "x" * 257, [], {}):
            class MissingCSRFControl:
                origin = "http://127.0.0.1:18080"
                csrf = ""
                count = 0
                sent = []
                def request(self, method, path, body=None, key=None):
                    self.count += 1
                    self.sent.append((method, path))
                    if path.endswith("/login"):
                        return 200, {"data": {"csrf_token": supplied}}
                    if path.endswith("/logout"):
                        return 403, {"error": {"code": "CSRF_INVALID"}}
                    raise AssertionError("invalid CSRF must stop before importing data")
            control = MissingCSRFControl()
            payload = runner.execute(control, quality.build_plan(["jd01"]), {"username": "imported", "password": "control"})
            self.assertEqual(payload["execution_error"], "CSRF_MISSING")
            self.assertFalse(payload["logout_confirmed"])
            self.assertEqual(payload["logout_error"], "LOGOUT_UNCONFIRMED")
            self.assertEqual(payload["rows"], [])
            self.assertEqual(control.sent, [("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/logout")])

    def test_accepted_diagnosis_without_valid_id_is_uncertain_and_never_replayed(self):
        class UnknownCreationControl:
            origin = "http://127.0.0.1:18080"
            csrf = ""
            count = 0
            sent = []
            def request(self, method, path, body=None, key=None):
                self.count += 1
                self.sent.append((method, path))
                if path.endswith("/login"):
                    return 200, {"data": {"csrf_token": "control-csrf"}}
                if path.endswith("/logout"):
                    return 200, {"data": {}}
                if path == "/api/v1/tasks":
                    return 201, {"data": {"id": "17"}}
                if path.endswith("/diagnoses"):
                    return 202, {"data": {"id": "not-a-valid-id"}}
                result = {"kind": "IMPORT_COLLAPSED", "total_samples": 1000, "node_count": 4, "top_functions": [], "flamegraph": {"name": "all", "value": 1000}, "summary": {"source": "import", "unit": "samples"}}
                return 200, {"data": {"analysis_status": "SUCCESS", "result": result}}
        control = UnknownCreationControl()
        payload = runner.execute(control, quality.build_plan(["jd01"]), {"username": "imported", "password": "control"})
        self.assertEqual(payload["execution_error"], "RUN_ID_INVALID")
        self.assertEqual(payload["cleanup_error"], "DIAGNOSIS_CREATE_OUTCOME_UNKNOWN_DO_NOT_REPLAY")
        self.assertTrue(payload["logout_confirmed"])
        self.assertEqual(control.sent.count(("POST", "/api/v1/tasks/17/diagnoses")), 1)
        self.assertFalse(any("/cancel" in path for _, path in control.sent))


if __name__ == "__main__":
    unittest.main()
