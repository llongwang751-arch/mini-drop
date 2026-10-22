"""新数据/序列DSL及独立oracle回归；合成输入不代表真实PG或真实模型效果。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from case_dsl import (
    deterministic_suite,
    generation_context,
    load_suite,
    suite_schema,
    validate_suite,
)
from independent_oracle import Oracle, normalized_body


class GeneratedDSLTests(unittest.TestCase):
    def test_novel_values_and_sequences_do_not_need_catalog_case_ids(self):
        suite = deterministic_suite()
        suite["suite_id"] = "unseen-customer-story-837"
        suite["cases"][0]["id"] = "entirely-new-case-id"
        suite["cases"][0]["steps"][0]["body"]["name"] = "qa-新数据-837"
        self.assertEqual(len(validate_suite(suite)), 4)

    def test_oracles_urls_sql_and_executable_operations_are_rejected(self):
        for key in ("expected_status", "assertion", "url", "sql", "code"):
            suite = deterministic_suite()
            suite["cases"][0]["steps"][0][key] = "do-not-execute"
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_suite(suite)
        suite = deterministic_suite()
        suite["cases"][0]["steps"][0]["op"] = "execute_sql"
        with self.assertRaises(ValueError):
            validate_suite(suite)

    def test_forward_references_and_model_chosen_actors_cannot_expand_capability(self):
        for field, value in (
            ("target", "not-yet-created"),
            ("target", []),
            ("actor", "superuser"),
        ):
            suite = deterministic_suite()
            step = suite["cases"][0]["steps"][1]
            step[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_suite(suite)

    def test_invalid_business_data_is_allowed_but_unbounded_data_is_not(self):
        suite = deterministic_suite()
        self.assertTrue(validate_suite(suite))
        suite["cases"][0]["steps"][0]["body"]["name"] = "x" * 513
        with self.assertRaises(ValueError):
            validate_suite(suite)

    def test_duplicate_json_fields_do_not_override_oracle_boundary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "suite.json"
            path.write_text(
                '{"schema_version":2,"schema_version":1,"suite_id":"qa","cases":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_suite(path)

    def test_fixed_oracle_predicts_rejections_without_calling_crm(self):
        oracle = Oracle()
        for actor, body, expected in (
            ("viewer", {"name": "qa-viewer"}, 403),
            ("anonymous", {}, 401),
            ("agent", {"name": "界" * 201}, 422),
            ("agent", {"phone_number": "+8613800000000"}, 409),
        ):
            predicted = oracle.predict(
                {"op": "create", "actor": actor, "save_as": "new", "body": body}
            )
            self.assertEqual(predicted["status"], expected)
        self.assertEqual(
            oracle.predict({"op": "read", "actor": "agent", "target": "foreign_seed"})[
                "status"
            ],
            404,
        )

    def test_oracle_state_uses_own_predicted_values_not_response_values(self):
        oracle = Oracle()
        step = {
            "op": "create",
            "actor": "agent",
            "save_as": "new",
            "body": {"name": "qa-original", "custom_fields": {"keep": "a"}},
        }
        predicted = oracle.predict(step)
        actual = {
            "id": "50000000-0000-4000-8000-000000000099",
            "name": "qa-wrong",
            "organization_id": "wrong",
        }
        self.assertFalse(oracle.compare_values(predicted, actual))
        oracle.accept(predicted, actual)
        self.assertEqual(oracle.records[actual["id"]]["name"], "qa-original")
        patch = oracle.predict(
            {
                "op": "patch",
                "actor": "agent",
                "target": "new",
                "body": {"custom_fields": {"add": "b"}},
            }
        )
        self.assertEqual(patch["values"]["custom_fields"], {"keep": "a", "add": "b"})

    def test_bounded_data_validation_has_independent_boundary_oracle(self):
        self.assertIsNotNone(normalized_body({"name": "a" * 200}))
        self.assertIsNone(normalized_body({"name": "a" * 201}))
        self.assertIsNone(normalized_body({"name": True}))
        self.assertEqual(normalized_body({"tags": [" VIP ", "vip"]})["tags"], ["vip"])
        self.assertIsNone(normalized_body({"custom_fields": {"a" * 81: "v"}}))


@unittest.skipUnless(
    importlib.util.find_spec("jsonschema") is not None,
    "需要可选SDK依赖中的标准JSON Schema验证器",
)
class GeneratorSchemaContractTests(unittest.TestCase):
    def test_operation_required_fields_and_extra_fields_match_execution_boundary(self):
        from copy import deepcopy

        from jsonschema import Draft202012Validator

        validator = Draft202012Validator(suite_schema())
        original = deterministic_suite()
        validator.validate(original)
        witnessed = set()
        for case_index, case in enumerate(original["cases"]):
            for step_index, step in enumerate(case["steps"]):
                if step["op"] in witnessed:
                    continue
                witnessed.add(step["op"])
                for field in step:
                    broken = deepcopy(original)
                    del broken["cases"][case_index]["steps"][step_index][field]
                    with self.subTest(op=step["op"], missing=field):
                        self.assertFalse(validator.is_valid(broken))
                        with self.assertRaises(ValueError):
                            validate_suite(broken)
                broken = deepcopy(original)
                broken["cases"][case_index]["steps"][step_index]["expected_status"] = (
                    200
                )
                self.assertFalse(validator.is_valid(broken))
                with self.assertRaises(ValueError):
                    validate_suite(broken)
        self.assertEqual(witnessed, {"create", "patch", "read", "delete", "list"})

    def test_context_explains_reference_semantics_without_duplicating_schema(self):
        context = generation_context()
        self.assertNotIn("schema", context)
        conventions = context["dsl_conventions"]
        self.assertEqual(
            set(conventions["available_seed_targets"]),
            {"own_seed", "foreign_seed", "anonymized_seed"},
        )
        example = {
            "schema_version": 2,
            "suite_id": "synthetic-structure-example",
            "cases": [{"id": "example", "steps": conventions["operation_shapes"]}],
        }
        self.assertEqual(len(validate_suite(example)), 1)


@unittest.skipUnless(
    importlib.util.find_spec("agentscope") is not None,
    "需要可选官方SDK环境；只运行合成MockTransport",
)
class GeneratedNativeSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_generator_emits_new_case_data_and_sequence(self):
        import httpx2
        from plan_with_agentscope import generate_plan
        from test_agentscope_planner import completion

        suite = deterministic_suite()
        captured = []

        def handler(request):
            captured.append(json.loads(request.content))
            return httpx2.Response(
                200,
                json=completion(calls=[("generate_business_cases", json.dumps(suite))]),
            )

        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "synthetic-key"
            key.write_text("not-a-real-provider-credential", encoding="utf-8")
            value, receipt = await generate_plan(
                key,
                transport=httpx2.MockTransport(handler),
                generation_mode="generated_data_sequences",
            )
        self.assertEqual(value, suite)
        self.assertEqual(receipt["generation_mode"], "generated_data_sequences")
        self.assertEqual(
            (
                receipt["model_calls"],
                receipt["provider_requests"],
                receipt["tool_calls"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(captured[0]["max_tokens"], 4000)
        self.assertEqual(
            captured[0]["tools"][0]["function"]["name"], "generate_business_cases"
        )
        self.assertFalse(receipt["source_or_oracle_sent_to_model"])

    async def test_generator_cannot_supply_expected_result_or_execute_sql(self):
        import httpx2
        from plan_with_agentscope import PlanFailure, generate_plan
        from test_agentscope_planner import completion

        for key_name in ("expected_status", "sql"):
            suite = deterministic_suite()
            suite["cases"][0]["steps"][0][key_name] = "unsafe"

            def handler(_request, suite=suite):
                return httpx2.Response(
                    200,
                    json=completion(
                        calls=[("generate_business_cases", json.dumps(suite))]
                    ),
                )

            with tempfile.TemporaryDirectory() as temporary:
                key = Path(temporary) / "synthetic-key"
                key.write_text("not-a-real-provider-credential", encoding="utf-8")
                with self.assertRaises(PlanFailure) as caught:
                    await generate_plan(
                        key,
                        transport=httpx2.MockTransport(handler),
                        generation_mode="generated_data_sequences",
                    )
                self.assertEqual(caught.exception.receipt["tool_calls"], 0)

    async def test_invalid_operation_shape_has_fixed_safe_reason_before_tool(self):
        import httpx2
        from plan_with_agentscope import PlanFailure, generate_plan
        from test_agentscope_planner import completion

        suite = deterministic_suite()
        del suite["cases"][0]["steps"][0]["save_as"]

        def handler(_request):
            return httpx2.Response(
                200,
                json=completion(calls=[("generate_business_cases", json.dumps(suite))]),
            )

        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "synthetic-key"
            key.write_text("not-a-real-provider-credential", encoding="utf-8")
            with self.assertRaises(PlanFailure) as caught:
                await generate_plan(
                    key,
                    transport=httpx2.MockTransport(handler),
                    generation_mode="generated_data_sequences",
                )
        receipt = caught.exception.receipt
        self.assertEqual(receipt["validation_error_code"], "DSL_STEP_FIELDS")
        self.assertEqual(receipt["validation_channel"], "native_tool")
        self.assertEqual(receipt["tool_calls"], 0)

    async def test_non_json_text_has_safe_syntax_reason_and_no_raw_capture(self):
        import httpx2
        from plan_with_agentscope import PlanFailure, generate_plan
        from test_agentscope_planner import completion

        def handler(_request):
            return httpx2.Response(
                200, json=completion("```json\nprivate-synthetic-marker\n```")
            )

        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "synthetic-key"
            key.write_text("not-a-real-provider-credential", encoding="utf-8")
            with self.assertRaises(PlanFailure) as caught:
                await generate_plan(
                    key,
                    transport=httpx2.MockTransport(handler),
                    generation_mode="generated_data_sequences",
                )
        receipt = caught.exception.receipt
        self.assertEqual(receipt["validation_error_code"], "PLAN_JSON_SYNTAX")
        self.assertEqual(receipt["validation_channel"], "pure_json")
        self.assertNotIn("private-synthetic-marker", json.dumps(receipt))


if __name__ == "__main__":
    unittest.main()
