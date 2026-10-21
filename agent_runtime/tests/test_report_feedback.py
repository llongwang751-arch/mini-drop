"""导入的 Go 验收反馈与模型响应夹具：只验证真实 SDK 的消息传递与预算，不调用模型。

Go 对事实、权限和来源的实际验证由 Go 集成测试负责；此处不会用伪造 fixture 声称其通过。
"""

import json
import tempfile
import unittest
from pathlib import Path

import httpx2 as httpx

from test_runtime import FixtureGateway, Provider, completion, run_body, tool_definition
from drop_agent_runtime.config import Config
from drop_agent_runtime.provider_retry import _circuits
from drop_agent_runtime.runner import RunRequest, run_agent
from drop_agent_runtime.wire import ProtocolError


SAFE_FEEDBACK = json.dumps(
    {
        "reason": "verification_source_tool_mismatch",
        "section": "verification_requests",
        "index": 0,
        "evidence_id": "E12",
        "expected_tool": "propose_verification",
        "actual_tool": "propose_followup_collection",
        "repair": (
            "改为origin=model_proposal并删除source_evidence_id，或在普通轮内调用真正"
            "propose_verification后引用其返回；不得增加权限或预算。"
        ),
    },
    ensure_ascii=False,
    separators=(",", ":"),
)


def imported_report(corrected):
    """结构保持 v2；修正只取消对未获准计划来源的冒认，不把模型建议升级为工具结论。"""
    proposal = {"origin": "model_proposal", "kind": "collect_wait_io"}
    if not corrected:
        proposal = {
            "origin": "tool_plan",
            "kind": "repeat_collection",
            "source_evidence_id": "E12",
        }
    return {
        "report_version": 2,
        "facts": [
            {
                "id": "F1",
                "evidence_id": "E1",
                "path": "/measurement_unit",
                "operator": "eq",
                "value": "samples",
                "unit": "unit",
            }
        ],
        "hypotheses": [
            {
                "kind": "insufficient_evidence",
                "fact_ids": ["F1"],
                "evidence_ids": ["E1"],
            }
        ],
        "verification_requests": [proposal],
    }


class ReportFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _circuits.clear()
        self.directory = tempfile.TemporaryDirectory()
        secret = Path(self.directory.name) / "imported-fixture-key"
        secret.write_text("imported-fixture-key", encoding="utf-8")
        self.config = Config(
            "unused.sock", "unused-gateway.sock", 10001,
            "https://api.deepseek.com", "deepseek-flash", str(secret),
        )

    async def asyncTearDown(self):
        self.directory.cleanup()

    def body(self, maximum=5):
        body = run_body(tools=[
            tool_definition("propose_followup_collection"),
            tool_definition("inspect_hotspots"),
        ])
        body["budget"].update(
            max_model_calls=maximum, max_tool_calls=14 if maximum == 9 else 7,
        )
        return body

    def gateway(self, *, format_only=False, reject_forever=False):
        def callback(payload):
            if payload["type"] == "tool":
                return {
                    "ok": True,
                    "observation": {
                        "evidence_id": "E12",
                        "tool": payload["name"],
                        "imported": True,
                        "source": "explicit synthetic gateway fixture",
                    },
                }
            if payload["type"] == "finish":
                report = json.loads(payload["content"])
                proposal = report["verification_requests"][0]
                if reject_forever or proposal["origin"] != "model_proposal":
                    return {
                        "ok": False, "fatal": False, "accepted": False,
                        "error_code": "AI_INVALID_REPORT",
                        "feedback": SAFE_FEEDBACK, "format_only": format_only,
                    }
                self.assertNotIn("source_evidence_id", proposal)
                return {"ok": True, "accepted": True, "result": report}
            return None

        return FixtureGateway(callback)

    async def execute(self, responses, body, gateway):
        provider = Provider(responses)
        result = await run_agent(
            RunRequest.parse(body, self.config.model), self.config, gateway,
            transport=httpx.MockTransport(provider),
        )
        return result, provider

    async def test_safe_report_feedback_reaches_native_model_and_corrects_proposal(self):
        """回调反馈 string 原样成为下一条 user 消息，旧 5 轮预算内可重交模型待审建议。"""
        gateway = self.gateway()
        result, provider = await self.execute([
            completion(calls=[("imported-followup", "propose_followup_collection", "{}")]),
            completion(json.dumps(imported_report(False))),
            completion(json.dumps(imported_report(True))),
        ], self.body(), gateway)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(provider.requests[2]["messages"][-1]["role"], "user")
        self.assertEqual(provider.requests[2]["messages"][-1]["content"], SAFE_FEEDBACK)
        self.assertEqual(json.loads(SAFE_FEEDBACK)["reason"], "verification_source_tool_mismatch")
        finishes = [c for c in gateway.calls if c["type"] == "finish"]
        self.assertEqual([c["round"] for c in finishes], [2, 3])
        corrected = json.loads(finishes[-1]["content"])
        self.assertEqual(corrected, imported_report(True))
        self.assertNotIn("source_evidence_id", corrected["verification_requests"][0])
        self.assertEqual(result["result"], corrected)
        self.assertEqual((result["runtime"]["model_calls"], result["runtime"]["tool_calls"]), (3, 1))
        self.assertEqual(result["runtime"]["usage"], {"input_tokens": 30, "output_tokens": 15})

    async def test_safe_feedback_on_last_format_round_has_no_tools_or_extra_model(self):
        """已赚取的第 5/9 轮仅纠正 JSON；结构化提示不授予新的工具或普通模型轮。"""
        for maximum in (5, 9):
            with self.subTest(max_model_calls=maximum):
                responses = [completion(calls=[(f"format-observation-{i}", "inspect_hotspots", "{}")]) for i in range(maximum - 2)]
                responses += [
                    completion(json.dumps(imported_report(False))),
                    completion(json.dumps(imported_report(True))),
                ]
                gateway = self.gateway(format_only=True)
                result, provider = await self.execute(responses, self.body(maximum), gateway)
                self.assertEqual(len(provider.requests), maximum)
                self.assertEqual(provider.requests[-1]["messages"][-1]["content"], SAFE_FEEDBACK)
                self.assertNotIn("tools", provider.requests[-1])
                self.assertEqual(provider.requests[-1].get("tool_choice"), "none")
                self.assertEqual(result["runtime"]["model_calls"], maximum)
                self.assertEqual(result["runtime"]["tool_calls"], maximum - 2)
                self.assertEqual(result["result"], imported_report(True))
                self.assertEqual([c["round"] for c in gateway.calls if c["type"] == "model_end"], list(range(1, maximum + 1)))

    async def test_safe_feedback_does_not_authorize_format_tools_or_tenth_request(self):
        """最后格式轮拒绝工具或再次失败时直接终止，既不执行工具也不申请第 6/10 次模型。"""
        for maximum in (5, 9):
            for mode in ("tool", "rejected_report"):
                with self.subTest(max_model_calls=maximum, last_response=mode):
                    responses = [completion(calls=[(f"bounded-observation-{i}", "inspect_hotspots", "{}")]) for i in range(maximum - 2)]
                    responses.append(completion(json.dumps(imported_report(False))))
                    responses.append(
                        completion(calls=[("forbidden-format-tool", "inspect_hotspots", "{}")])
                        if mode == "tool" else completion(json.dumps(imported_report(False)))
                    )
                    provider = Provider(responses)
                    gateway = self.gateway(format_only=True, reject_forever=True)
                    with self.assertRaisesRegex(ProtocolError, "AI_BUDGET_EXCEEDED"):
                        await run_agent(
                            RunRequest.parse(self.body(maximum), self.config.model),
                            self.config, gateway, transport=httpx.MockTransport(provider),
                        )
                    self.assertEqual(len(provider.requests), maximum)
                    self.assertNotIn("tools", provider.requests[-1])
                    self.assertEqual(provider.requests[-1].get("tool_choice"), "none")
                    tools = [c for c in gateway.calls if c["type"] == "tool"]
                    self.assertEqual(len(tools), maximum - 2)
                    self.assertNotIn("forbidden-format-tool", [c["call_id"] for c in tools])
                    self.assertEqual([c["round"] for c in gateway.calls if c["type"] == "model_start"], list(range(1, maximum + 1)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
