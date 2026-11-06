"""Regression tests for the teaching implementations, not production claims."""

from copy import deepcopy
from dataclasses import replace
import unittest

from .diagnosis import (
    Authority, CASES, CheckpointLedger, Evidence, EvidenceArchive, Rejected,
    ToolGateway, canonical, fixture, json_pointer, paired_gate, verify_fact,
)


class TeachingDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.binding, self.authority, self.evidence = fixture()

    def reject(self, code, action):
        with self.assertRaises(Rejected) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_rejected_tool_inputs_do_not_execute_or_leak_note(self):
        gate = ToolGateway(self.authority, self.evidence)
        for phase, request, code in [
            ("planning", {"name": "inspect_hotspots", "arguments": {"rank": 1}}, "TOOL_STAGE"),
            ("diagnosis", {"name": "inspect_hotspots", "arguments": {"rank": True}}, "TOOL_SCHEMA"),
            ("diagnosis", {"name": "inspect_hotspots", "arguments": {"rank": 11}}, "TOOL_SCHEMA"),
            ("diagnosis", {"name": "inspect_hotspots", "arguments": {"rank": 1, "shell": "ls"}}, "TOOL_SCHEMA"),
        ]:
            self.reject(code, lambda: gate.invoke(self.binding, phase, request))
        self.assertEqual(gate.executions, 0)
        result = gate.invoke(self.binding, "diagnosis", {"name": "inspect_hotspots", "arguments": {"rank": 1}})
        self.assertNotIn("note", result)
        result["top_functions"][0]["self_percent"] = 0
        self.assertEqual(self.evidence.document["top_functions"][0]["self_percent"], 70)

    def test_mutated_source_is_rejected_even_with_same_metadata(self):
        changed = deepcopy(self.evidence.document)
        changed["top_functions"][0]["self_percent"] = 99
        forged = Evidence(self.evidence.scope, self.evidence.attempt, self.evidence.source_digest, changed)
        gate = ToolGateway(self.authority, forged)
        self.reject("EVIDENCE_SOURCE", lambda: gate.invoke(self.binding, "diagnosis", {"name": "read_profile_summary", "arguments": {}}))
        self.assertEqual(gate.executions, 0)

    def test_fact_matches_exact_decimal_unit_path_and_source(self):
        fact = {"evidence_id": "E1", "source_digest": self.binding.source_digest, "path": "/top_functions/0/self_percent", "unit": "percent", "value": "70.000000000000"}
        self.assertEqual(verify_fact(self.binding, self.authority, self.evidence, fact)["value"], "70")
        for name, value, code in [("value", "70.000000000001", "FACT_VALUE"), ("value", True, "FACT_NUMBER"), ("unit", "samples", "FACT_UNIT"), ("path", "/note", "FACT_PATH"), ("source_digest", "a" * 64, "FACT_SOURCE")]:
            self.reject(code, lambda: verify_fact(self.binding, self.authority, self.evidence, {**fact, name: value}))

    def test_pointer_decodes_escaped_keys_and_rejects_ambiguous_index(self):
        self.assertEqual(json_pointer({"a/b": {"~v": [7]}}, "/a~1b/~0v/0"), 7)
        self.reject("FACT_PATH", lambda: json_pointer([7], "/00"))
        self.reject("FACT_PATH", lambda: json_pointer({"a": 1}, "/a~2"))

    def test_canonical_contract_reorders_objects_without_rounding_floats(self):
        self.assertEqual(canonical({"b": 2, "a": 1}), canonical({"a": 1, "b": 2}))
        self.assertNotEqual(canonical({"a": True}), canonical({"a": 1}))
        self.reject("JSON_TYPE", lambda: canonical({"rank": 1.0}))
        self.reject("JSON_SIZE", lambda: canonical({"text": "x" * 5000}))

    def completed_ledger(self):
        ledger = CheckpointLedger.create(self.authority, self.binding)
        ledger.reserve_model(self.binding)
        request = {"round": 1, "name": "inspect_hotspots", "arguments": {"rank": 1}}
        self.assertEqual(ledger.begin_tool(self.binding, "call-1", request), ("execute", None))
        ledger.complete_tool(self.binding, "call-1", request, {"ok": True, "weight": 70})
        return ledger, request

    def test_receipt_replay_never_reexecutes_and_cannot_change_content(self):
        ledger, request = self.completed_ledger()
        resumed = ledger.resume(self.binding)
        action, result = resumed.begin_tool(self.binding, "call-1", {"arguments": {"rank": 1}, "name": "inspect_hotspots", "round": 1})
        self.assertEqual((action, result), ("replay", {"ok": True, "weight": 70}))
        result["weight"] = 0
        self.assertEqual(ledger.state.receipts["call-1"]["response"]["weight"], 70)
        self.assertEqual(resumed.state.tool_calls, 1)
        self.reject("CALL_ID_REUSED", lambda: resumed.begin_tool(self.binding, "call-1", {**request, "arguments": {"rank": 2}}))
        self.reject("CALL_ID_REUSED", lambda: resumed.complete_tool(self.binding, "call-1", {**request, "round": 2}, {"ok": True}))

    def test_pending_blocks_resume_and_replay_without_creating_second_receipt(self):
        ledger = CheckpointLedger.create(self.authority, self.binding)
        ledger.reserve_model(self.binding)
        request = {"round": 1, "name": "inspect_hotspots", "arguments": {"rank": 1}}
        ledger.begin_tool(self.binding, "pending-1", request)
        self.reject("AMBIGUOUS_PENDING", lambda: ledger.resume(self.binding))
        self.reject("AMBIGUOUS_PENDING", lambda: ledger.begin_tool(self.binding, "pending-1", request))
        self.assertEqual(ledger.state.tool_calls, 1)
        self.assertEqual(list(ledger.state.receipts), ["pending-1"])

    def test_handover_budget_source_and_cursor_are_independent_fences(self):
        ledger, _ = self.completed_ledger()
        ledger.save_visible(self.binding, 1, {"cursor": "model", "observations": ["E1"]})
        newer = replace(self.binding, lease="lease-new")
        self.authority.current = newer
        resumed = ledger.resume(newer)
        self.reject("LEASE_LOST", lambda: ledger.save_visible(self.binding, 2, {"cursor": "finish", "observations": ["E1"]}))
        self.reject("CHECKPOINT_STALE", lambda: resumed.save_visible(newer, 1, {"cursor": "model", "observations": []}))
        self.reject("SPEC_CHANGED", lambda: resumed.resume(newer, "new-tools"))
        resumed.reserve_model(newer)
        self.reject("MODEL_BUDGET", lambda: resumed.reserve_model(newer))
        self.assertEqual(resumed.state.model_calls, 2)
        self.authority.current = replace(newer, source_digest="b" * 64)
        self.reject("SOURCE_CHANGED", lambda: resumed.reserve_model(newer))
        self.reject("CHECKPOINT_BINDING", lambda: resumed.resume(self.authority.current))

    def test_output_contract_and_sham_can_each_veto_good_ratios(self):
        pairs, sham = [(120, 100)] * 6, [(100, 100)] * 6
        expected = ["alice", "bob"]
        self.assertTrue(paired_gate(pairs, sham, expected, expected.copy())["accepted"])
        self.assertFalse(paired_gate(pairs, sham, expected, ["alice"])["accepted"])
        self.assertFalse(paired_gate(pairs, sham, expected, tuple(expected))["accepted"])
        self.assertFalse(paired_gate(pairs, [(130, 100)] * 6, expected, expected.copy())["accepted"])
        self.assertFalse(paired_gate([(100, 100)] * 6, sham, expected, expected.copy())["accepted"])
        self.reject("PAIR_VALUE", lambda: paired_gate([(True, 100)] * 6, sham, expected, expected))

    def test_threshold_is_not_rounded_across_acceptance_boundary(self):
        sham, output = [(100, 100)] * 6, ["x"]
        below = paired_gate([(109999, 100000)] * 6, sham, output, output.copy())
        edge = paired_gate([(110000, 100000)] * 6, sham, output, output.copy())
        self.assertFalse(below["accepted"])
        self.assertTrue(edge["accepted"])
        self.assertEqual(edge["root_cause_status"], "not_tested")

    def test_evidence_failure_is_immutable_and_skips_are_not_passed(self):
        archive = EvidenceArchive(self.authority)
        record = {"run_id": "run-1", "source_digest": self.binding.source_digest, "attempt": 1, "status": "failed", "counts": {"passed": 1, "failed": 1, "skipped": 0}}
        self.assertTrue(archive.append(self.binding, record))
        self.assertFalse(archive.append(self.binding, deepcopy(record)))
        self.reject("RECORD_IMMUTABLE", lambda: archive.append(self.binding, {**record, "status": "passed", "counts": {"passed": 2, "failed": 0, "skipped": 0}}))
        self.reject("RECORD_STATUS", lambda: archive.append(self.binding, {**record, "run_id": "run-2", "status": "passed", "counts": {"passed": 0, "failed": 0, "skipped": 2}}))
        self.assertEqual(len(archive.records), 1)

    def test_old_lease_and_other_source_never_append_evidence(self):
        archive = EvidenceArchive(self.authority)
        record = {"run_id": "run-1", "source_digest": self.binding.source_digest, "attempt": 1, "status": "passed", "counts": {"passed": 1, "failed": 0, "skipped": 0}}
        newer = replace(self.binding, lease="lease-new")
        self.authority.current = newer
        self.reject("LEASE_LOST", lambda: archive.append(self.binding, record))
        self.reject("RECORD_SOURCE", lambda: archive.append(newer, {**record, "source_digest": "c" * 64}))
        self.assertEqual(archive.records, {})

    def test_case_registry_contains_four_working_experiments(self):
        self.assertEqual(set(CASES), {"tool-gate", "checkpoint-replay", "controlled-comparison", "evidence-gate"})
        for name, case in CASES.items():
            with self.subTest(case=name):
                result = case()
                self.assertTrue(result["checks"])
                self.assertTrue(all(item["passed"] for item in result["checks"]))
                self.assertTrue(result["observations"])


if __name__ == "__main__":
    unittest.main()
