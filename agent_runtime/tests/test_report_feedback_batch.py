"""导入的多错误反馈夹具：验证真实 AgentScope SDK 消息与既有预算边界，不调用模型。

这里不替代 Go 对错误枚举、字段和事实来源的验证。反馈由合成 gateway 提供，测试只确认
完整字符串会进入下一轮上下文，且分区提示不能改变格式轮权限或 4096 UTF-8 字节上限。
"""

import json
import tempfile
import unittest
from pathlib import Path

import httpx2 as httpx

from test_report_feedback import imported_report
from test_runtime import FixtureGateway, Provider, completion, run_body
from drop_agent_runtime.config import Config
from drop_agent_runtime.provider_retry import _circuits
from drop_agent_runtime.runner import RunRequest, run_agent
from drop_agent_runtime.wire import ProtocolError


# 固定字段和修正建议仿照服务端安全协议；不包含候选值、用户原文或任何原始观测。
# 六条分属三个分区，防止测试只证明第一条错误到达模型而遗漏余下的 JSON 内容。
IMPORTED_BATCH = json.dumps(
    {
        "reason": "report_validation_failed",
        "errors": [
            {
                "reason": "fact_path_not_allowed", "section": "facts", "index": 0,
                "field": "path", "evidence_id": "E3", "actual_tool": "inspect_hotspots",
                "repair": "该工具的path/operator组合不在白名单；按系统协议选择已返回的固定字段，或移除此事实并同步假设引用。",
            },
            {
                "reason": "verification_source_tool_mismatch",
                "section": "verification_requests", "index": 0,
                "field": "source_evidence_id", "evidence_id": "E12",
                "expected_tool": "propose_verification",
                "actual_tool": "propose_followup_collection",
                "repair": "改为origin=model_proposal并删除source_evidence_id，或在普通轮内调用真正propose_verification后引用其返回；不得增加权限或预算。",
            },
            {
                "reason": "hypothesis_allocation_evidence_missing",
                "section": "hypotheses", "index": 0, "field": "kind",
                "repair": "allocation_gc需要本假设引用实际allocation/garbage_collection提示或内存采集kind事实；没有时移除此假设或改为insufficient_evidence。",
            },
            {
                "reason": "fact_unit_mismatch", "section": "facts", "index": 1,
                "field": "unit", "evidence_id": "E6",
                "actual_tool": "query_business_signals", "expected_unit": "milliseconds",
                "repair": "使用expected_unit标明的原观测单位，不能换算或给不同来源套用单位。",
            },
            {
                "reason": "verification_parameter_mismatch",
                "section": "verification_requests", "index": 1,
                "field": "duration_seconds", "evidence_id": "E9",
                "actual_tool": "propose_verification",
                "repair": "工具计划的时长/频率必须与所引用propose_verification实际返回逐项相等；自行建议须改为model_proposal并删除source_evidence_id。",
            },
            {
                "reason": "hypothesis_fact_reference_invalid",
                "section": "hypotheses", "index": 1, "field": "fact_ids",
                "repair": "fact_ids只能包含本报告已核验且互不重复的事实，并在evidence_ids中包含每条事实的真实来源。",
            },
        ],
        "truncated": True,
        "repair": "逐项修正以上安全字段错误；truncated表示仍可能有其余错误，请同时核对报告协议，不增加权限或预算。",
    },
    ensure_ascii=False,
    separators=(",", ":"),
)


class BatchReportFeedbackTests(unittest.IsolatedAsyncioTestCase):
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

    def body(self, maximum):
        body = run_body()
        body["budget"].update(
            max_model_calls=maximum, max_tool_calls=14 if maximum == 9 else 7,
        )
        return body

    def gateway(self, feedback=IMPORTED_BATCH, *, format_only=False):
        finishes = 0

        def callback(payload):
            nonlocal finishes
            if payload["type"] == "finish":
                finishes += 1
                if finishes == 1:
                    return {
                        "ok": False, "fatal": False, "accepted": False,
                        "error_code": "AI_INVALID_REPORT", "feedback": feedback,
                        "format_only": format_only,
                    }
                return {"ok": True, "accepted": True, "result": imported_report(True)}
            return None

        return FixtureGateway(callback)

    async def execute(self, maximum, responses, gateway):
        provider = Provider(responses)
        result = await run_agent(
            RunRequest.parse(self.body(maximum), self.config.model), self.config,
            gateway, transport=httpx.MockTransport(provider),
        )
        return result, provider

    async def test_all_safe_error_sections_reach_next_native_message_unchanged(self):
        """六条完整 JSON 原样进入下一轮；旧 5 与新 9 轮配置不会因此重置实际计数。"""
        self.assertLessEqual(len(IMPORTED_BATCH.encode("utf-8")), 3500)
        self.assertEqual(len(json.loads(IMPORTED_BATCH)["errors"]), 6)
        for maximum in (5, 9):
            with self.subTest(max_model_calls=maximum):
                gateway = self.gateway()
                result, provider = await self.execute(maximum, [
                    completion(json.dumps(imported_report(False))),
                    completion(json.dumps(imported_report(True))),
                ], gateway)
                message = provider.requests[-1]["messages"][-1]
                self.assertEqual((message["role"], message["content"]), ("user", IMPORTED_BATCH))
                self.assertTrue(json.loads(message["content"])["truncated"])
                self.assertEqual(len(provider.requests), 2)
                self.assertEqual((result["runtime"]["model_calls"], result["runtime"]["tool_calls"]), (2, 0))
                self.assertEqual(result["runtime"]["usage"], {"input_tokens": 20, "output_tokens": 10})
                for kind in ("model_start", "model_end", "finish"):
                    self.assertEqual([c["round"] for c in gateway.calls if c["type"] == kind], [1, 2])

    async def test_batch_does_not_expand_last_format_round_permissions(self):
        """反馈中的建议工具名不能让最后格式轮调用工具，也不能申请第 6/10 次模型。"""
        for maximum in (5, 9):
            for last_kind in ("accepted_report", "forbidden_tool"):
                with self.subTest(max_model_calls=maximum, last_response=last_kind):
                    responses = [
                        completion(calls=[(f"imported-batch-{i}", "inspect_hotspots", "{}")])
                        for i in range(maximum - 2)
                    ]
                    responses.append(completion(json.dumps(imported_report(False))))
                    responses.append(
                        completion(json.dumps(imported_report(True)))
                        if last_kind == "accepted_report" else
                        completion(calls=[("forbidden-final-tool", "inspect_hotspots", "{}")])
                    )
                    provider = Provider(responses)
                    gateway = self.gateway(format_only=True)
                    if last_kind == "forbidden_tool":
                        with self.assertRaisesRegex(ProtocolError, "AI_BUDGET_EXCEEDED"):
                            await run_agent(
                                RunRequest.parse(self.body(maximum), self.config.model),
                                self.config, gateway, transport=httpx.MockTransport(provider),
                            )
                    else:
                        result = await run_agent(
                            RunRequest.parse(self.body(maximum), self.config.model),
                            self.config, gateway, transport=httpx.MockTransport(provider),
                        )
                        self.assertEqual(result["runtime"]["model_calls"], maximum)
                    self.assertEqual(len(provider.requests), maximum)
                    self.assertEqual(provider.requests[-1]["messages"][-1]["content"], IMPORTED_BATCH)
                    self.assertNotIn("tools", provider.requests[-1])
                    self.assertEqual(provider.requests[-1].get("tool_choice"), "none")
                    tools = [c for c in gateway.calls if c["type"] == "tool"]
                    self.assertEqual(len(tools), maximum - 2)
                    self.assertNotIn("forbidden-final-tool", [c["call_id"] for c in tools])

    async def test_feedback_utf8_4096_boundary_is_not_truncated_or_raised(self):
        """多字节 JSON 的 4096 字节原样转交，4097 字节在下一次 provider/工具调用前硬拒绝。"""
        for maximum in (5, 9):
            for size in (4096, 4097):
                with self.subTest(max_model_calls=maximum, feedback_bytes=size):
                    feedback = IMPORTED_BATCH + " " * (size - len(IMPORTED_BATCH.encode("utf-8")))
                    self.assertEqual(len(feedback.encode("utf-8")), size)
                    self.assertLess(len(feedback), size)
                    provider = Provider([
                        completion(json.dumps(imported_report(False))),
                        completion(json.dumps(imported_report(True))),
                    ])
                    gateway = self.gateway(feedback)
                    if size == 4097:
                        with self.assertRaisesRegex(ProtocolError, "AGENT_RUNTIME_PROTOCOL_ERROR"):
                            await run_agent(
                                RunRequest.parse(self.body(maximum), self.config.model),
                                self.config, gateway, transport=httpx.MockTransport(provider),
                            )
                        self.assertEqual(len(provider.requests), 1)
                        self.assertEqual([c["round"] for c in gateway.calls if c["type"] == "model_end"], [1])
                    else:
                        result = await run_agent(
                            RunRequest.parse(self.body(maximum), self.config.model),
                            self.config, gateway, transport=httpx.MockTransport(provider),
                        )
                        self.assertEqual(len(provider.requests), 2)
                        self.assertEqual(provider.requests[-1]["messages"][-1]["content"], feedback)
                        self.assertEqual(result["runtime"]["tool_calls"], 0)
                    self.assertEqual([c for c in gateway.calls if c["type"] == "tool"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
