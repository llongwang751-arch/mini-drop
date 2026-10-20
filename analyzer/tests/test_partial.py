"""Imported synthetic parser checkpoints; never a successful analysis result."""

import hashlib
import json
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from drop_analyzer.folded import analyze_folded
from drop_analyzer.partial import SOURCES, build_partial, validate_partial
from drop_analyzer.pipeline import SUPPORTED_KINDS


def job():
    return SimpleNamespace(
        task_id=7,
        attempt_id=20,
        id=30,
        attempt=3,
        kind="IMPORT_COLLAPSED",
        params={"source": "import"},
    )


def artifact():
    return {"id": 40, "sha256": hashlib.sha256(b"imported fixture").hexdigest()}


def snapshot():
    return build_partial(
        analyze_folded(b"root;work 100\nroot;malloc 50"), job(), artifact()
    )


class PartialResultTests(unittest.TestCase):
    def test_shared_contract_fixture_and_fractional_profile_weights(self):
        contract = Path(__file__).resolve().parents[2] / "contracts"
        fixture = json.loads(
            (contract / "fixtures/partial-result-v1.json").read_bytes()
        )
        schema = json.loads((contract / "partial-result.schema.json").read_bytes())
        self.assertTrue(validate_partial(fixture))
        self.assertEqual(set(fixture), set(schema["required"]))
        self.assertEqual(
            SOURCES,
            frozenset(schema["properties"]["summary"]["properties"]["source"]["enum"]),
        )
        self.assertEqual(
            SUPPORTED_KINDS, frozenset(schema["properties"]["kind"]["enum"])
        )
        for row in fixture["top_functions"]:
            self.assertEqual(
                row["symbol_sha256"], hashlib.sha256(row["name"].encode()).hexdigest()
            )
        fractional = deepcopy(fixture)
        fractional["summary"]["total_samples"] = 1.5
        for row in fractional["top_functions"]:
            row["self"] = row["total"] = 0.5
        self.assertTrue(validate_partial(fractional))

    def test_checkpoint_contains_only_verified_counts_top_and_current_identity(self):
        parsed = analyze_folded(b"root;work 100\nroot;malloc 50")
        parsed["metadata"]["tool_report"] = "PRIVATE_TOOL_DIAGNOSTIC"
        parsed["metadata"]["collector"] = {"secret": "PRIVATE_COLLECTOR"}
        parsed["sections"] = {"private": {"path": "/internal/private/profile"}}
        actual = build_partial(parsed, job(), artifact())
        self.assertTrue(validate_partial(actual))
        self.assertEqual(actual["completeness"], "partial")
        self.assertEqual(actual["analysis_run"], 3)
        self.assertEqual(actual["input_artifact_id"], "40")
        self.assertEqual(actual["summary"]["total_samples"], 150)
        self.assertEqual(actual["summary"]["source"], "import")
        self.assertEqual(actual["completed_stages"], ["input_verified", "parsed"])
        self.assertEqual(
            actual["missing_stages"],
            ["diagnostic_rules", "result_artifacts", "final_commit"],
        )
        encoded = json.dumps(actual)
        for private in (
            "PRIVATE_",
            "/internal/private",
            "flamegraph",
            "sections",
            "metadata",
            "suggestions",
        ):
            self.assertNotIn(private, encoded)

    def test_top_labels_preserve_utf8_but_bound_bytes_and_strip_controls_and_private_workdir(
        self,
    ):
        parsed = analyze_folded(("root;" + "热点" * 100 + " 1").encode())
        original = "/private/job-30-abc/" + "热点\x00\u202e" * 100
        parsed["top"]["self"][0]["name"] = original
        actual = build_partial(
            parsed, job(), artifact(), private_paths=("/private/job-30-abc",)
        )
        row = actual["top_functions"][0]
        self.assertLessEqual(len(row["name"].encode()), 160)
        self.assertIn("热点", row["name"])
        self.assertNotIn("/private/job-30-abc", row["name"])
        self.assertNotIn("\x00", row["name"])
        self.assertNotIn("\u202e", row["name"])
        self.assertEqual(
            row["symbol_sha256"], hashlib.sha256(original.encode()).hexdigest()
        )

    def test_top_is_limited_to_twenty_without_mutating_parser_result(self):
        parsed = analyze_folded(
            b"\n".join(f"root;work_{n} 1".encode() for n in range(100))
        )
        before = deepcopy(parsed)
        actual = build_partial(parsed, job(), artifact())
        self.assertEqual(len(actual["top_functions"]), 20)
        self.assertLessEqual(
            len(json.dumps(actual, ensure_ascii=False).encode()), 16 * 1024
        )
        self.assertEqual(parsed, before)

    def test_malformed_or_unbounded_parser_values_publish_no_checkpoint(self):
        for change in (
            {"sample_count": True},
            {"node_count": -1},
            {"max_depth": 2**53},
            {"sample_count": float("nan")},
        ):
            parsed = analyze_folded(b"root;work 1")
            parsed["metadata"].update(change)
            with self.subTest(change=change):
                self.assertIsNone(build_partial(parsed, job(), artifact()))
        parsed = analyze_folded(b"root;work 1")
        parsed["top"]["self"][0]["self_ratio"] = float("inf")
        self.assertIsNone(build_partial(parsed, job(), artifact()))
        self.assertIsNone(build_partial({}, job(), artifact()))
        self.assertIsNone(build_partial(parsed, job(), {"sha256": "a" * 64}))

    def test_strict_validation_rejects_unknown_fields_bad_ids_and_nonfinite_numbers(
        self,
    ):
        valid = snapshot()
        invalid = []
        for key, value in (
            ("schema_version", True),
            ("task_id", "01"),
            ("attempt_id", "0"),
            ("analysis_run", 2**31),
            ("input_digest", "A" * 64),
            ("kind", "unknown"),
            ("sections", {}),
            ("metadata", {}),
            ("completed_stages", ["parsed"]),
            ("top_functions", [valid["top_functions"][0]] * 21),
        ):
            invalid.append(dict(valid, **{key: value}))
        for key, value in (
            ("node_count", -1),
            ("node_count", 100001),
            ("unique_stacks", 100001),
            ("max_depth", 129),
            ("total_samples", True),
            ("unit", "invalid unit"),
        ):
            invalid.append(dict(valid, summary=dict(valid["summary"], **{key: value})))
        for key, value in (
            ("name", "x\n"),
            ("name", "界" * 54),
            ("self_percent", float("nan")),
            ("total_percent", 100.1),
            ("total", 2**53),
            ("raw", "private"),
        ):
            value_row = dict(valid["top_functions"][0], **{key: value})
            invalid.append(dict(valid, top_functions=[value_row]))
        for value in invalid:
            with self.subTest(value=value):
                self.assertFalse(validate_partial(value))

    def test_unknown_source_and_unit_use_safe_unknown_enumeration(self):
        parsed = analyze_folded(b"root;work 1")
        parsed["metadata"].update(source="private-url", unit="invalid unit /x")
        regular = job()
        regular.kind, regular.params = "PERF_CPU", {}
        actual = build_partial(parsed, regular, artifact())
        self.assertEqual(
            (actual["summary"]["source"], actual["summary"]["unit"]),
            ("unknown", "unknown"),
        )
        parsed["metadata"]["unit"] = "pprof.custom-unit"
        self.assertEqual(
            build_partial(parsed, regular, artifact())["summary"]["unit"],
            "pprof.custom-unit",
        )


if __name__ == "__main__":
    unittest.main()
