"""明确合成评分控制与本机真实HTTP测试；不访问生产、不调用模型。

合成DiagnosisRun只验证评分协议，不伪装为实际Agent效果。本机HTTP测试只证明实际路径和
恢复观测，缺少平台采集/模型时不会据此宣称完整因果诊断通过。
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import experiment
spec = importlib.util.spec_from_file_location("causal_runner_unit", HERE / "run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def phase(start, end, latency=1, status=200, *, scenario="normal", sampled=True):
    rows = [{"status_code": status, "duration_ms": latency, "trace_sha256": str(index) * 64} for index in range(1, 4)]
    signals = {role: {"metrics": {"metrics": [{"name": "latency_p95_ms", "unit": "milliseconds", "value": latency}, {"name": "error_rate", "unit": "ratio", "value": float(status >= 500)}, {"name": "throughput_rps", "unit": "requests_per_second", "value": 1.0}]}, "logs": {"logs": []}} for role in ("gateway", "catalog")}
    if scenario in {"slow_query", "timeout"}:
        signals["catalog"]["logs"]["logs"] = [{"event_code": "DB_SLOW_QUERY", "status_code": 200}]
    if scenario == "timeout":
        signals["gateway"]["logs"]["logs"] = [{"event_code": "UPSTREAM_TIMEOUT", "status_code": 504}]
    result = {"stage": "observed", "start": f"2026-10-23T10:{start}", "end": f"2026-10-23T10:{end}", "target_fingerprint": "f" * 64, "responses": rows, "signals": signals}
    if sampled:
        result.update(collection_start="2026-10-23T10:01:23Z" if scenario == "normal" else "2026-10-23T10:00:11Z", collection_parameters_sha256="a" * 64, sampling={"live_collection": True, "kind": "PYTHON_CPU", "source": "py-spy", "total_samples": 120, "controlled_cpu_self_percent": 70 if scenario == "cpu" else 0, "attempt_id": "2", "analysis_sha256": "b" * 64})
    return result


def round_control(case_id="c01", replicate=1):
    scenario = {"c01": "cpu", "c02": "slow_query", "c03": "timeout"}[case_id]
    return {"case_id": case_id, "replicate": replicate, "before": phase("00:00Z", "00:02Z", sampled=False), "fault": phase("00:10Z", "00:20Z", 30 if scenario != "timeout" else 50, 504 if scenario == "timeout" else 200, scenario=scenario), "after": phase("00:21Z", "01:40Z")}


def diagnosis_control(phase_data, run_id, kind):
    model, _, _ = experiment.dataset()
    phase_data["task_id"] = str(int(run_id) + 100)
    phase_data["diagnosis"] = {"id": str(run_id), "task_id": phase_data["task_id"], "attempt_id": "2", "goal": model["goal"], "mode": "model", "knowledge_mode": "off", "report_protocol": 2, "status": "success", "trace": [{"evidence_id": "E1", "tool": "read_profile_summary"}], "report": {"report_version": 2, "facts": [{"id": "F1", "evidence_id": "E1", "unit": "samples", "value": 120}], "hypotheses": [{"kind": kind, "fact_ids": ["F1"], "evidence_ids": ["E1"]}], "agent_runtime": {"framework": "agentscope", "framework_version": "2.0.10", "agent_class": "agentscope.agent.Agent", "model_calls": 1, "tool_calls": 2}}}


def capture_control():
    preregistered = experiment.plan(1, case_ids=["c01"])
    row = round_control()
    diagnosis_control(row["fault"], 11, "cpu_hotspot")
    diagnosis_control(row["after"], 12, "insufficient_evidence")
    return {"plan": preregistered, "environment_sha256": "e" * 64, "rounds": [row], "budget": {}}


class CausalContractTests(unittest.TestCase):
    def test_frozen_model_input_never_contains_injection_or_oracle(self):
        model, oracle, manifest = experiment.dataset()
        self.assertFalse(manifest["ground_truth_sent_to_model"])
        self.assertEqual(len(oracle["cases"]), 3)
        self.assertNotIn("scenario", model)
        self.assertNotIn("direction_kinds", json.dumps(model))
        self.assertEqual(experiment.plan(), experiment.plan())
        self.assertEqual(len(experiment.plan()["trials"]), 6)

    def test_oracle_changes_require_new_manifest_version(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            for name in ("model_input.json", "oracle.json", "manifest.json"):
                (path / name).write_bytes((HERE / name).read_bytes())
            value = json.loads((path / "oracle.json").read_text("utf-8"))
            value["cases"][0]["scenario"] = "normal"
            (path / "oracle.json").write_text(json.dumps(value), "utf-8")
            with patch.object(experiment, "HERE", path), self.assertRaises(ValueError):
                experiment.dataset()

    def test_three_controlled_effects_are_distinct_and_require_real_sampling(self):
        oracle = experiment.dataset()[1]
        for case in oracle["cases"]:
            data = round_control(case["id"])
            value = experiment.controlled_effect(case, data["before"], data["fault"], data["after"])
            self.assertTrue(value["passed"], value)
            self.assertFalse(value["arbitrary_root_cause_accuracy"])
            data["fault"]["sampling"]["live_collection"] = False
            self.assertFalse(experiment.controlled_effect(case, data["before"], data["fault"], data["after"])["passed"])

    def test_empty_windows_do_not_become_normal_zero(self):
        value = round_control()
        value["before"]["responses"] = []
        self.assertIsNone(experiment.observation(value["before"]))
        case = experiment.dataset()[1]["cases"][0]
        self.assertFalse(experiment.controlled_effect(case, value["before"], value["fault"], value["after"])["passed"])

    def test_washout_and_real_parameter_pair_are_required(self):
        case = experiment.dataset()[1]["cases"][0]
        for mutate in ("washout", "parameters", "environment", "order", "cpu"):
            data = round_control()
            if mutate == "washout":
                data["after"]["collection_start"] = "2026-10-23T10:00:30Z"
            elif mutate == "parameters":
                data["after"]["collection_parameters_sha256"] = "d" * 64
            elif mutate == "environment":
                data["after"]["target_fingerprint"] = "e" * 64
            elif mutate == "order":
                data["before"]["end"] = "2026-10-23T10:00:12Z"
            else:
                data["after"]["sampling"]["controlled_cpu_self_percent"] = 60
            self.assertFalse(experiment.controlled_effect(case, data["before"], data["fault"], data["after"])["passed"], mutate)

    def test_status_event_and_metric_must_support_timeout(self):
        case = experiment.dataset()[1]["cases"][2]
        for mutate in ("http", "event", "metric"):
            data = round_control("c03")
            if mutate == "http":
                data["fault"]["responses"][0]["status_code"] = 200
            elif mutate == "event":
                data["fault"]["signals"]["gateway"]["logs"]["logs"] = []
            else:
                data["fault"]["signals"]["gateway"]["metrics"]["metrics"][1]["value"] = 0
            self.assertFalse(experiment.controlled_effect(case, data["before"], data["fault"], data["after"])["passed"], mutate)

    def test_effect_is_independent_from_model_tags_and_failed_models_not_abstention(self):
        capture = capture_control()
        capture["rounds"][0]["fault"]["diagnosis"]["report"]["hypotheses"][0]["kind"] = "normal_load"
        value = experiment.score(capture)
        self.assertEqual(value["metrics"]["controlled_intervention_verified"]["numerator"], 1)
        self.assertEqual(value["metrics"]["fault_supported_direction_top3"]["numerator"], 0)
        self.assertIsNone(value["metrics"]["exact_root_cause_accuracy"])
        self.assertIsNone(value["metrics"]["semantic_abstention_accuracy"])
        capture["rounds"][0]["after"]["diagnosis"]["status"] = "failed"
        self.assertEqual(experiment.score(capture)["metrics"]["normal_after_direction_top3"]["numerator"], 0)

    def test_missing_trials_stay_in_preregistered_denominator(self):
        capture = capture_control()
        capture["plan"] = experiment.plan(2, case_ids=["c01"])
        result = experiment.score(capture)
        self.assertEqual(result["metrics"]["controlled_intervention_verified"]["denominator"], 2)
        self.assertEqual(result["metrics"]["controlled_intervention_verified"]["numerator"], 1)

    def test_reused_run_and_changed_model_goal_rejected(self):
        for mutate in ("reuse", "goal", "source"):
            capture = capture_control()
            if mutate == "reuse":
                capture["rounds"][0]["after"]["diagnosis"]["id"] = "11"
            elif mutate == "goal":
                capture["rounds"][0]["after"]["diagnosis"]["goal"] = "send oracle to model"
            else:
                capture["rounds"][0]["after"]["diagnosis"]["attempt_id"] = "900"
            with self.assertRaises(ValueError):
                experiment.score(capture)

    def test_human_template_is_unfilled_and_agent_review_rejected(self):
        capture = capture_control()
        value = experiment.review_template(capture)
        self.assertEqual(len(value["reviews"]), 2)
        self.assertIsNone(value["reviews"][0]["exact_root_cause_supported"])
        self.assertFalse(value["reviews"][0]["independence_attested"])
        value["reviews"][0].update(exact_root_cause_supported=True, assessment_type="agent_review", independence_attested=True, reviewer="imported-control")
        with self.assertRaises(ValueError):
            experiment.apply_reviews(capture, value)

    def test_human_review_binds_report_and_verified_effect(self):
        for mutate in ("report", "unverified-effect"):
            capture = capture_control()
            submitted = experiment.review_template(capture)
            submitted["reviews"][0].update(exact_root_cause_supported=True, independence_attested=True, reviewer="imported-human-control")
            if mutate == "report":
                submitted["reviews"][0]["report_sha256"] = "0" * 64
            else:
                capture["rounds"][0]["after"]["collection_start"] = "2026-10-23T10:00:30Z"
            with self.assertRaises(ValueError):
                experiment.apply_reviews(capture, submitted)

    def test_human_schema_matches_unreviewed_template_and_bounded_notes(self):
        capture = capture_control()
        submitted = experiment.review_template(capture)
        schema = experiment.load(HERE / "human-review.schema.json")
        self.assertEqual(set(submitted["reviews"][0]), set(schema["$defs"]["review"]["required"]))
        submitted["reviews"][0]["notes"] = "x" * 1001
        with self.assertRaises(ValueError):
            experiment.apply_reviews(capture, submitted)

    def test_actual_human_control_remains_separate_from_automatic_classification(self):
        capture = capture_control()
        submitted = experiment.review_template(capture)
        submitted["reviews"][0].update(exact_root_cause_supported=True, independence_attested=True, reviewer="imported-human-control")
        value = experiment.apply_reviews(capture, submitted)
        self.assertEqual(value["independent_human_metrics"]["reviewed_reports"], 1)
        self.assertEqual(value["independent_human_metrics"]["exact_root_cause_support"]["numerator"], 1)
        self.assertIsNone(value["metrics"]["exact_root_cause_accuracy"])
        self.assertEqual(value["metrics"]["independent_human_reviews"], 0)

    def test_budget_unknown_runs_charge_worst_case_and_stop_before_overrun(self):
        policy = experiment.dataset()[1]["policy"]
        budget = runner.Budget(policy)
        for _ in range(5):
            budget.reserve()
            budget.charge({})
        with self.assertRaises(runner.CausalError):
            budget.reserve()
        self.assertEqual(budget.receipt()["unknown_runs_charged_worst_case"], 5)
        platform = runner.Platform(None, {}, {}, runner.Budget(policy), {}, time.monotonic() + 10)
        platform.request_unknown = True
        self.assertIn("DO_NOT_REPLAY", platform.cleanup()["error"])
        self.assertEqual(platform.cleanup(), {})
        self.assertEqual(platform.budget.unknown_charges, 1)

    def sample_task(self):
        return {"id": "1", "kind": "PYTHON_CPU", "collection_status": "COLLECTED", "analysis_status": "SUCCESS", "params": {"pid": 42, "duration_seconds": 6, "frequency_hz": 49, "scope": "process", "traceparent": "imported-first-trace"}, "attempts": [{"id": "2", "attempt": 1, "started_at": "2026-10-23T10:00:11Z", "finished_at": "2026-10-23T10:00:17Z"}], "result": {"kind": "PYTHON_CPU", "total_samples": 120, "node_count": 2, "top_functions": [{"name": "python_cpu_hotspot (/opt/drop-observability-demo/server.py:139)", "self_percent": 70}], "flamegraph": {"name": "all", "value": 120}, "summary": {"unit": "samples", "source": "py-spy"}}}

    def test_sampling_requires_actual_source_and_semantic_parameters_exclude_only_traceparent(self):
        original = self.sample_task()
        changed = copy.deepcopy(original)
        changed["params"]["traceparent"] = "imported-second-trace"
        self.assertEqual(runner.sampling_projection(original, 42)["parameters_sha256"], runner.sampling_projection(changed, 42)["parameters_sha256"])
        self.assertEqual(runner.sampling_projection(original, 42)["controlled_cpu_self_percent"], 70)
        for field, value in (("source", "import"), ("pid", 999), ("duration_seconds", 7)):
            task = copy.deepcopy(original)
            if field == "source":
                task["result"]["summary"][field] = value
            else:
                task["params"][field] = value
            with self.assertRaises(runner.CausalError):
                runner.sampling_projection(task, 42)

    def test_platform_uses_real_api_contract_without_sending_control_scenario(self):
        model, oracle, _ = experiment.dataset()
        task = self.sample_task()
        sent = []
        class ImportedProtocolControl:
            def request(self, method, path, body=None, key=None):
                sent.append((method, path, body))
                if path == "/api/v1/tasks":
                    return 201, {"data": {"id": "1"}}
                if path == "/api/v1/tasks/1":
                    return 200, {"data": task}
                if method == "POST" and path.endswith("/diagnoses"):
                    return 202, {"data": {"id": "3"}}
                return 200, {"data": {"items": [{"id": "3", "status": "success", "report": {"agent_runtime": {"model_calls": 2, "tool_calls": 3}}}]}}
        budget = runner.Budget(oracle["policy"])
        platform = runner.Platform(ImportedProtocolControl(), {"agent_id": "imported-agent", "telemetry_service_id": "gateway"}, {"catalog": {"pid": 42}}, budget, model, time.monotonic() + 10)
        result = {}
        platform.collect_and_diagnose(result, "1-A", time.monotonic() + 10)
        self.assertEqual(result["diagnosis"]["id"], "3")
        self.assertEqual(budget.models, 2)
        self.assertEqual(budget.tools, 3)
        serialized = json.dumps(sent)
        self.assertNotIn("slow_query", serialized)
        self.assertNotIn("direction_kinds", serialized)
        posted = next(body for method, path, body in sent if method == "POST" and path.endswith("/diagnoses"))
        self.assertEqual(posted["goal"], model["goal"])
        self.assertEqual(posted["knowledge_mode"], "off")

    def test_origins_paths_and_environment_are_allowlisted(self):
        for origin in ("http://example.test:8130", "https://observability-gateway:8130", "http://user:pass@observability-gateway:8130", "http://observability-gateway:8130/path", "http://127.0.0.1:18080"):
            with self.assertRaises(runner.CausalError):
                runner.validate_demo_origin(origin, "gateway")
        client = runner.DemoClient("http://127.0.0.1:18080", "http://127.0.0.1:18081", True)
        for path in ("/health?sql=SELECT", "/demo/request?scenario=normal&sql=SELECT", "/v1/signals?url=http://example.test", "http://example.test/health"):
            with self.assertRaises(runner.CausalError):
                client.request("gateway", path)
        with self.assertRaises(runner.CausalError):
            runner.validate_environment({"schema_version": 1, "images": {"api": "latest"}, "sources": {}})

    def test_public_score_does_not_copy_private_budget_text(self):
        capture = capture_control()
        capture["budget"] = {"error": "private provider body", "accounted_model_calls": "private input"}
        result = experiment.score(capture)
        self.assertEqual(result["budget"], {})
        self.assertNotIn("private provider", json.dumps(result))

    def test_atomic_private_output_refuses_overwrite_and_journal_appends(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.json"
            runner.save(path, {"imported_control": True})
            with self.assertRaises(FileExistsError):
                runner.save(path, {})
            journal = Path(folder) / "progress.jsonl"
            runner.append_checkpoint(journal, {"phase": 1})
            runner.append_checkpoint(journal, {"phase": 2})
            self.assertEqual(len(journal.read_text("utf-8").splitlines()), 2)


class ActualLocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = experiment.ROOT / "integrations/observability-demo/server.py"
        spec = importlib.util.spec_from_file_location("actual_causal_demo_server", source)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)
        cls.folder = tempfile.TemporaryDirectory()
        database = cls.module.initialize_catalog(Path(cls.folder.name) / "catalog.sqlite")
        cls.catalog = cls.module.DemoServer(("127.0.0.1", 0), "catalog", database=database)
        cls.gateway = cls.module.DemoServer(("127.0.0.1", 0), "gateway", downstream=f"http://127.0.0.1:{cls.catalog.server_port}")
        cls.threads = []
        for server in (cls.catalog, cls.gateway):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)
        cls.demo = runner.DemoClient(f"http://127.0.0.1:{cls.gateway.server_port}", f"http://127.0.0.1:{cls.catalog.server_port}", True)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.gateway, cls.catalog):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=2)
        cls.folder.cleanup()

    def measured(self, scenario):
        value = {"start": runner.stamp(), "stage": "observed", "responses": []}
        cpu_started = time.process_time()
        for _ in range(3):
            value["responses"].append(self.demo.work(scenario))
            time.sleep(.85 if scenario == "timeout" else .01)
        value["end"] = runner.stamp()
        value["local_process_cpu_seconds"] = time.process_time() - cpu_started
        value["signals"] = self.demo.signals(value["start"], value["end"])
        return value

    def test_actual_cpu_slow_sql_timeout_and_normal_recovery(self):
        for scenario in ("cpu", "slow_query", "timeout"):
            with self.subTest(scenario=scenario):
                before, fault = self.measured("normal"), self.measured(scenario)
                if scenario == "timeout":
                    time.sleep(2.1)  # 真实下游工作可能晚于网关deadline，不把超时误当取消。
                after = self.measured("normal")
                a, f, z = [experiment.observation(value) for value in (before, fault, after)]
                self.assertEqual(a["error_rate"], 0)
                self.assertEqual(z["error_rate"], 0)
                if scenario == "timeout":
                    self.assertGreaterEqual(f["http_504_rate"], .8)
                    self.assertTrue(any(row["event_code"] == "UPSTREAM_TIMEOUT" for row in experiment.events(fault, "gateway")))
                elif scenario == "slow_query":
                    self.assertGreater(f["p95_ms"], max(a["p95_ms"], z["p95_ms"]) * 5)
                else:
                    # 本机同进程HTTP夹具直接观察CPU工作执行，不把短窗口延迟比写成必然成立的CI断言。
                    self.assertGreater(fault["local_process_cpu_seconds"], max(before["local_process_cpu_seconds"], after["local_process_cpu_seconds"]) + .02)
                self.assertIsNone(fault.get("diagnosis"))
                self.assertNotIn("sampling", fault)


if __name__ == "__main__":
    unittest.main()
