"""导入的合成供应商响应；只走 MockTransport，不调用真实模型或读取真实密钥。"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contract import MANDATORY, digest, planner_context
from plan_with_agentscope import PlanFailure, generate_plan, strict_plan

SDK_AVAILABLE = importlib.util.find_spec("agentscope") is not None
PLAN = {
    "schema_version": 1,
    "case_ids": ["name_maximum", "name_too_long", "cursor_tampered"],
}


def completion(content=None, calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": f"imported-call-{index}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for index, (name, arguments) in enumerate(calls)
        ]
    return {
        "id": "imported-synthetic-fixture",
        "object": "chat.completion",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if calls else "stop",
                "message": message,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class StrictPlannerOutputTests(unittest.TestCase):
    def test_pure_json_does_not_accept_markdown_or_duplicate_keys(self):
        for value in (
            "```json\n{}\n```",
            '{"schema_version":1,"case_ids":["viewer_write"],"case_ids":["create_minimum"]}',
        ):
            with self.subTest(value=value), self.assertRaises(PlanFailure):
                strict_plan(value)

    def test_context_does_not_send_assertion_or_implementation(self):
        context = planner_context()
        self.assertTrue(
            all(set(item) == {"id", "category"} for item in context["catalog"])
        )
        self.assertNotIn("expected", json.dumps(context))
        self.assertNotIn("requirement", json.dumps(context))


@unittest.skipUnless(
    SDK_AVAILABLE,
    "可选 SDK 未安装；使用 .local/agentscope-venv 的 Python 执行真实 SDK 离线回归",
)
class NativeAgentScopePlannerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx2

        self.httpx = httpx2
        self.temporary = tempfile.TemporaryDirectory()
        self.key_file = Path(self.temporary.name) / "synthetic-key"
        self.key_file.write_text("imported-fixture-not-a-real-key", encoding="utf-8")
        self.requests = []

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def transport(self, response, status=200):
        def handler(request):
            self.requests.append(json.loads(request.content))
            return self.httpx.Response(status, json=response)

        return self.httpx.MockTransport(handler)

    async def test_native_tool_generates_validated_plan_with_actual_counters(self):
        transport = self.transport(
            completion(calls=[("select_test_cases", json.dumps(PLAN))])
        )
        value, receipt = await generate_plan(self.key_file, transport=transport)
        self.assertEqual(value, PLAN)
        self.assertEqual(receipt["framework_version"], "2.0.10")
        self.assertEqual(
            (
                receipt["model_calls"],
                receipt["provider_requests"],
                receipt["tool_calls"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(receipt["usage"], {"input_tokens": 10, "output_tokens": 5})
        self.assertIsNone(receipt["cost"])
        self.assertEqual(receipt["plan_sha256"], digest(PLAN))
        self.assertEqual(receipt["acceptance_channel"], "native_select_test_cases_tool")
        self.assertTrue(receipt["fixture_provider"])
        payload = self.requests[0]
        self.assertEqual(payload["temperature"], 0)
        self.assertEqual(payload["max_tokens"], 1500)
        self.assertFalse(payload["stream"])
        self.assertEqual(
            [tool["function"]["name"] for tool in payload["tools"]],
            ["select_test_cases"],
        )
        self.assertNotIn("imported-fixture-not-a-real-key", json.dumps(payload))

    async def test_pure_json_also_uses_strict_contract_and_keeps_mandatory(self):
        value, receipt = await generate_plan(
            self.key_file, transport=self.transport(completion(json.dumps(PLAN)))
        )
        self.assertEqual(value, PLAN)
        self.assertEqual(receipt["tool_calls"], 0)
        self.assertEqual(receipt["acceptance_channel"], "validated_pure_json")
        self.assertGreaterEqual(receipt["selected_case_count"], len(MANDATORY))

    async def test_unknown_tool_and_batch_are_rejected_before_any_tool(self):
        batches = [
            [("propose_collection", json.dumps(PLAN))],
            [("select_test_cases", json.dumps(PLAN))] * 2,
        ]
        for calls in batches:
            with (
                self.subTest(calls=len(calls)),
                self.assertRaises(PlanFailure) as caught,
            ):
                await generate_plan(
                    self.key_file, transport=self.transport(completion(calls=calls))
                )
            self.assertEqual(caught.exception.code, "PLAN_TOOL_REJECTED")
            self.assertEqual(caught.exception.receipt["tool_calls"], 0)
            self.assertEqual(caught.exception.receipt["provider_requests"], 1)

    async def test_arbitrary_url_or_oracle_in_tool_is_rejected(self):
        invalid = {**PLAN, "expected": 200, "url": "https://example.invalid"}
        with self.assertRaises(PlanFailure) as caught:
            await generate_plan(
                self.key_file,
                transport=self.transport(
                    completion(calls=[("select_test_cases", json.dumps(invalid))])
                ),
            )
        self.assertEqual(caught.exception.code, "PLAN_OUTPUT_REJECTED")
        self.assertEqual(caught.exception.receipt["tool_calls"], 0)
        self.assertEqual(len(self.requests), 1)

    async def test_provider_failure_does_not_retry(self):
        with self.assertRaises(PlanFailure) as caught:
            await generate_plan(
                self.key_file,
                transport=self.transport(
                    {"error": "synthetic-private-provider-error"}, status=503
                ),
            )
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(caught.exception.receipt["provider_requests"], 1)
        self.assertIsNone(caught.exception.receipt["usage"])
        self.assertNotIn("synthetic-private-provider-error", str(caught.exception))

    async def test_oversized_response_rejected_by_shared_bounded_transport(self):
        with self.assertRaises(PlanFailure) as caught:
            await generate_plan(
                self.key_file, transport=self.transport(completion("x" * 65_536))
            )
        self.assertEqual(caught.exception.receipt["provider_requests"], 1)
        self.assertEqual(caught.exception.receipt["tool_calls"], 0)

    async def test_oversized_key_rejected_before_provider_request(self):
        self.key_file.write_bytes(b"x" * 4097)
        with self.assertRaises(PlanFailure) as caught:
            await generate_plan(
                self.key_file, transport=self.transport(completion(json.dumps(PLAN)))
            )
        self.assertEqual(caught.exception.receipt["provider_requests"], 0)
        self.assertEqual(len(self.requests), 0)


if __name__ == "__main__":
    unittest.main()
