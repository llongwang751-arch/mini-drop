"""Imported synthetic provider/tool fixtures; no live model or task data."""

import asyncio
import json
import stat
import sys
import tempfile
import unittest
import uuid
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2 as httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drop_agent_runtime.config import Config
from drop_agent_runtime.runner import RunRequest, SafeRuntimeFailure, run_agent
from drop_agent_runtime.server import Server, validate_socket_parent
from drop_agent_runtime.wire import (
    AcceptedJSON,
    ProtocolError,
    RunDiagnostics,
    accepted_json_field,
    json_bytes,
    read_http,
    strict_json,
)


def tool_definition(name="inspect_hotspots", properties=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Imported fixture tool.",
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "additionalProperties": False,
            },
        },
    }


def run_body(phase="diagnosis", tools=None):
    return {
        "version": 1,
        "run_id": str(uuid.uuid4()),
        "capability": "imported_test_capability_12345678901234567890",
        "phase": phase,
        "model": "deepseek-flash",
        "system_prompt": "Imported synthetic fixture: use the provided business tools then return JSON.",
        "messages": [{"role": "user", "content": "Imported test evidence only."}],
        "tools": tools or [tool_definition()],
        "budget": {
            "max_model_calls": 5 if phase == "diagnosis" else 6,
            "max_tool_calls": 7,
            "max_tools_per_round": 2,
            "timeout_ms": 5000,
        },
    }


def completion(content=None, calls=None, **extra):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
            for call_id, name, arguments in calls
        ]
    message.update(extra)
    return {
        "id": "imported-test-response",
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


class FixtureGateway:
    def __init__(self, handler=None):
        self.cancelled = False
        self.calls = []
        self.handler = handler

    async def call(self, payload):
        self.calls.append(deepcopy(payload))
        if self.handler is not None:
            value = self.handler(payload)
            if asyncio.iscoroutine(value):
                value = await value
            if value is not None:
                return value
        if payload["type"] == "tool":
            return {
                "ok": True,
                "observation": {
                    "evidence_id": "E2",
                    "source": "imported synthetic fixture",
                },
            }
        if payload["type"] == "finish":
            return {
                "ok": True,
                "accepted": True,
                "result": {"summary": "Go accepted fixture"},
            }
        return {"ok": True}


class Provider:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def __call__(self, request):
        self.requests.append(json.loads(request.content))
        if not self.responses:
            raise AssertionError("unexpected extra provider call")
        return httpx.Response(200, json=self.responses.pop(0))


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        secret = Path(self.directory.name) / "test-only-key"
        secret.write_text("imported-fixture-key", encoding="utf-8")
        self.config = Config(
            "unused.sock",
            "unused-gateway.sock",
            10001,
            "https://api.deepseek.com",
            "deepseek-flash",
            str(secret),
        )

    async def asyncTearDown(self):
        self.directory.cleanup()

    async def execute(self, responses, body=None, gateway=None):
        provider = Provider(responses)
        body = body or run_body()
        gateway = gateway or FixtureGateway()
        result = await run_agent(
            RunRequest.parse(body, self.config.model),
            self.config,
            gateway,
            transport=httpx.MockTransport(provider),
        )
        return result, provider, gateway

    async def test_real_sdk_react_tool_history_and_runtime_proof(self):
        result, provider, gateway = await self.execute(
            [
                completion(calls=[("fixture-1", "inspect_hotspots", "{}")]),
                completion(
                    '{"summary":"candidate"}',
                    reasoning_content="fixture-hidden-thought-do-not-retain",
                ),
            ]
        )
        self.assertEqual(result["result"], {"summary": "Go accepted fixture"})
        runtime = result["runtime"]
        self.assertEqual(runtime["framework"], "agentscope")
        self.assertEqual(runtime["framework_version"], "2.0.10")
        self.assertEqual(runtime["agent_class"], "agentscope.agent.Agent")
        self.assertEqual((runtime["model_calls"], runtime["tool_calls"]), (2, 1))
        self.assertEqual(runtime["usage"], {"input_tokens": 20, "output_tokens": 10})
        self.assertEqual(
            [call["type"] for call in gateway.calls],
            ["model_start", "model_end", "tool", "model_start", "model_end", "finish"],
        )
        self.assertEqual(provider.requests[0]["thinking"], {"type": "disabled"})
        self.assertEqual(provider.requests[0]["stream"], False)
        self.assertEqual(
            [message["role"] for message in provider.requests[1]["messages"]],
            ["system", "user", "assistant", "tool"],
        )
        self.assertEqual(gateway.calls[2]["call_id"], "fixture-1")
        self.assertEqual(gateway.calls[2]["arguments"], {})
        serialized = json.dumps(
            {
                "result": result,
                "callbacks": gateway.calls,
                "requests": provider.requests,
            }
        )
        self.assertNotIn("fixture-hidden-thought", serialized)
        self.assertNotIn("imported-fixture-key", serialized)
        self.assertNotIn(run_body()["capability"], serialized)

    async def test_go_accepted_result_above_16k_is_returned_intact(self):
        accepted = {
            "summary": "Imported accepted fixture",
            "validation": {"padding": "x" * 24000},
        }
        gateway = FixtureGateway(
            lambda payload: (
                {"ok": True, "accepted": True, "result": accepted}
                if payload["type"] == "finish"
                else None
            )
        )
        result, _, _ = await self.execute(
            [completion('{"summary":"candidate"}')], gateway=gateway
        )
        self.assertEqual(result["result"], accepted)
        self.assertGreater(len(json.dumps(result).encode()), 16384)

    async def test_real_sdk_accepted_numeric_tokens_are_returned_without_float_conversion(
        self,
    ):
        accepted = (
            b'{"fixed":0.000001,"exponent":1e-6,"negative_zero":-0,"precise":0.12345678901234567890123456789,"padding":"'
            + b"x" * 24576
            + b'"}'
        )
        gateway = FixtureGateway(
            lambda payload: (
                {"ok": True, "accepted": True, "result": AcceptedJSON(accepted)}
                if payload["type"] == "finish"
                else None
            )
        )
        result, _, _ = await self.execute(
            [completion('{"summary":"candidate"}')], gateway=gateway
        )
        self.assertIn(b'"result":' + accepted, json_bytes(result))
        self.assertNotEqual(json_bytes(strict_json(accepted)), accepted)

    async def test_upstream_transport_failure_has_only_fixed_safe_diagnostics(self):
        async def provider(request):
            raise httpx.ConnectError("fixture-secret-value-do-not-disclose")

        with self.assertRaises(SafeRuntimeFailure) as caught:
            await run_agent(
                RunRequest.parse(run_body(), self.config.model),
                self.config,
                FixtureGateway(),
                transport=httpx.MockTransport(provider),
            )
        self.assertEqual(caught.exception.code, "AGENT_RUNTIME_FAILED")
        self.assertEqual(
            caught.exception.diagnostics,
            {
                "stage": "provider_request",
                "exception_kind": "transport",
                "model_calls": 1,
                "tool_calls": 0,
            },
        )
        self.assertNotIn("fixture-secret", json.dumps(caught.exception.diagnostics))

    async def test_planning_terminal_tool_stops_without_second_model(self):
        body = run_body("planning", [tool_definition("propose_collection")])
        gateway = FixtureGateway(
            lambda payload: (
                {"ok": True, "terminal": True, "result": {"kind": "IMPORTED_FIXTURE"}}
                if payload["type"] == "tool"
                else None
            )
        )
        result, provider, gateway = await self.execute(
            [completion(calls=[("fixture-plan", "propose_collection", "{}")])],
            body,
            gateway,
        )
        self.assertEqual(result["result"], {"kind": "IMPORTED_FIXTURE"})
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(result["runtime"]["tool_calls"], 1)
        self.assertNotIn("finish", [payload["type"] for payload in gateway.calls])

    async def test_fourth_finish_repair_is_fifth_call_without_tools(self):
        finishes = 0

        def callback(payload):
            nonlocal finishes
            if payload["type"] == "finish":
                finishes += 1
                if finishes == 1:
                    return {
                        "ok": False,
                        "fatal": False,
                        "accepted": False,
                        "format_only": True,
                        "feedback": "Fix JSON only.",
                    }
            return None

        gateway = FixtureGateway(callback)
        responses = [
            completion(calls=[(f"fixture-{i}", "inspect_hotspots", "{}")])
            for i in range(3)
        ] + [completion("invalid"), completion('{"summary":"fixed"}')]
        result, provider, _ = await self.execute(responses, gateway=gateway)
        self.assertEqual(result["runtime"]["model_calls"], 5)
        self.assertNotIn("tools", provider.requests[-1])
        self.assertEqual(provider.requests[-1].get("tool_choice"), "none")
        self.assertEqual(
            provider.requests[-1]["messages"][-1]["content"], "Fix JSON only."
        )

    async def test_regular_fifth_diagnosis_round_is_rejected(self):
        responses = [
            completion(calls=[(f"fixture-{i}", "inspect_hotspots", "{}")])
            for i in range(4)
        ]
        with self.assertRaisesRegex(ProtocolError, "AI_BUDGET_EXCEEDED"):
            await self.execute(responses)

    async def test_duplicate_call_id_is_never_executed_twice(self):
        gateway = FixtureGateway()
        with self.assertRaisesRegex(ProtocolError, "AI_TOOL_REJECTED"):
            await self.execute(
                [
                    completion(calls=[("fixture-repeat", "inspect_hotspots", "{}")]),
                    completion(calls=[("fixture-repeat", "inspect_hotspots", "{}")]),
                ],
                gateway=gateway,
            )
        self.assertEqual(
            len([call for call in gateway.calls if call["type"] == "tool"]), 1
        )

    async def test_duplicate_call_id_same_batch_is_rejected_before_execution(self):
        gateway = FixtureGateway()
        with self.assertRaisesRegex(ProtocolError, "AI_TOOL_REJECTED"):
            await self.execute(
                [
                    completion(
                        calls=[
                            ("fixture-repeat", "inspect_hotspots", "{}"),
                            ("fixture-repeat", "inspect_hotspots", "{}"),
                        ]
                    )
                ],
                gateway=gateway,
            )
        self.assertFalse(any(call["type"] == "tool" for call in gateway.calls))

    async def test_tool_parameter_injection_is_rejected(self):
        for arguments in (
            '{"shell":"id"}',
            '{"url":"https://example.test"}',
            '{"sql":"select 1"}',
            '{"task_id":1}',
            '{"a":1,"a":2}',
            "null",
            "{} trailing",
        ):
            with self.subTest(arguments=arguments):
                gateway = FixtureGateway()
                with self.assertRaises(ProtocolError):
                    await self.execute(
                        [
                            completion(
                                calls=[
                                    ("fixture-injected", "inspect_hotspots", arguments)
                                ]
                            )
                        ],
                        gateway=gateway,
                    )
                self.assertFalse(any(call["type"] == "tool" for call in gateway.calls))

    async def test_tool_outside_phase_allowlist_is_rejected(self):
        with self.assertRaisesRegex(ProtocolError, "AI_TOOL_REJECTED"):
            await self.execute(
                [completion(calls=[("fixture-shell", "Bash", '{"command":"id"}')])]
            )

    async def test_model_start_go_rejection_prevents_provider_call(self):
        gateway = FixtureGateway(
            lambda payload: {
                "ok": False,
                "fatal": True,
                "error_code": "AI_BUDGET_EXCEEDED",
            }
        )
        provider = Provider([])
        with self.assertRaisesRegex(ProtocolError, "AI_BUDGET_EXCEEDED"):
            await run_agent(
                RunRequest.parse(run_body(), self.config.model),
                self.config,
                gateway,
                transport=httpx.MockTransport(provider),
            )
        self.assertEqual(provider.requests, [])

    async def test_fatal_callback_rejection_stops_next_tool_and_model(self):
        gateway = FixtureGateway(
            lambda payload: (
                {"ok": False, "fatal": True, "error_code": "STALE_ATTEMPT"}
                if payload["type"] == "tool"
                else None
            )
        )
        with self.assertRaisesRegex(ProtocolError, "STALE_ATTEMPT"):
            await self.execute(
                [
                    completion(
                        calls=[
                            ("fixture-1", "inspect_hotspots", "{}"),
                            ("fixture-2", "inspect_hotspots", "{}"),
                        ]
                    )
                ],
                gateway=gateway,
            )
        self.assertEqual(
            len([call for call in gateway.calls if call["type"] == "tool"]), 1
        )

    async def test_deadline_cancels_pending_callback_and_sdk(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def callback(payload):
            if payload["type"] == "tool":
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

        gateway = FixtureGateway(callback)
        body = run_body()
        body["budget"]["timeout_ms"] = 300
        with self.assertRaises(SafeRuntimeFailure) as caught:
            await self.execute(
                [completion(calls=[("fixture-1", "inspect_hotspots", "{}")])],
                body,
                gateway,
            )
        self.assertTrue(started.is_set())
        self.assertTrue(cancelled.is_set())
        self.assertTrue(gateway.cancelled)
        self.assertEqual(caught.exception.diagnostics["exception_kind"], "timeout")
        self.assertEqual(caught.exception.diagnostics["stage"], "tool_callback")

    async def test_task_cancellation_interrupts_model_and_releases_transport(self):
        started, closed = asyncio.Event(), asyncio.Event()

        async def provider(request):
            started.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()

        gateway = FixtureGateway()
        task = asyncio.create_task(
            run_agent(
                RunRequest.parse(run_body(), self.config.model),
                self.config,
                gateway,
                transport=httpx.MockTransport(provider),
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        self.assertTrue(closed.is_set())
        self.assertTrue(gateway.cancelled)
        self.assertFalse(any(call["type"] == "finish" for call in gateway.calls))

    async def test_provider_malformed_and_oversize_responses_are_bounded(self):
        for response in (
            {"choices": []},
            {
                "choices": [
                    {"message": {"role": "assistant"}, "finish_reason": "length"}
                ]
            },
            completion("x" * 70000),
        ):
            with (
                self.subTest(response_size=len(json.dumps(response))),
                self.assertRaises(ProtocolError),
            ):
                await self.execute([response])


class ValidationTests(unittest.TestCase):
    def test_raw_accepted_result_preserves_numeric_lexemes_and_rejects_duplicate_fields(
        self,
    ):
        accepted = b'{"fixed":0.000001,"exponent":1e-6,"negative_zero":-0,"precise":0.12345678901234567890123456789,"nested":{"result":"quoted \\" result"}}'
        callback = b'{"ok":true,"accepted":true,"result":' + accepted + b"}"
        extracted = accepted_json_field(callback)
        self.assertEqual(extracted.raw, accepted)
        self.assertEqual(
            json_bytes({"result": extracted}), b'{"result":' + accepted + b"}"
        )
        with self.assertRaises(ProtocolError):
            accepted_json_field(b'{"result":1,"result":2}')

    def test_generic_exception_classification_never_contains_exception_text(self):
        failure = SafeRuntimeFailure(
            ValueError("fixture-sensitive-key-capability-source"),
            RunDiagnostics(stage="result_encode", model_calls=2, tool_calls=3),
        )
        self.assertEqual(failure.code, "AGENT_RUNTIME_FAILED")
        self.assertEqual(failure.diagnostics["exception_kind"], "internal")
        self.assertNotIn("fixture-sensitive", str(failure))
        self.assertNotIn("fixture-sensitive", json.dumps(failure.diagnostics))

    def test_socket_parent_mode_owner_group_and_symlink_fail_closed(self):
        path = Path(__file__).resolve().parent / "fixture.sock"
        safe = {"st_mode": stat.S_IFDIR | 0o2750, "st_uid": 10002, "st_gid": 10003}
        with patch(
            "drop_agent_runtime.server.Path.lstat", return_value=SimpleNamespace(**safe)
        ):
            validate_socket_parent(path, owner_uid=10002, group_gid=10003)
        for change in (
            {"st_mode": stat.S_IFDIR | 0o2770},
            {"st_mode": stat.S_IFDIR | 0o2751},
            {"st_mode": stat.S_IFLNK | 0o2750},
            {"st_uid": 10001},
            {"st_gid": 10002},
        ):
            with (
                self.subTest(change=change),
                patch(
                    "drop_agent_runtime.server.Path.lstat",
                    return_value=SimpleNamespace(**{**safe, **change}),
                ),
                self.assertRaises(ValueError),
            ):
                validate_socket_parent(path, owner_uid=10002, group_gid=10003)

    def test_request_rejects_model_mismatch_unknown_fields_and_builtin_tools(self):
        for mutate in (
            lambda body: body.update(model="model-supplied-override"),
            lambda body: body.update(endpoint="https://example.test"),
            lambda body: body.update(tools=[tool_definition("Bash")]),
            lambda body: body["messages"][0].update(role="system"),
            lambda body: body["budget"].update(timeout_ms=9999999),
            lambda body: body["tools"][0]["function"]["parameters"].update(
                {"$ref": "https://example.test/schema"}
            ),
        ):
            body = run_body()
            mutate(body)
            with self.assertRaises(ProtocolError):
                RunRequest.parse(body, "deepseek-flash")

    def test_strict_json_rejects_duplicate_and_nonfinite(self):
        for data in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "{} trailing"):
            with self.assertRaises(ProtocolError):
                strict_json(data)


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_independently_rejects_wrong_method_and_path(self):
        config = Config(
            "unused.sock",
            "unused-gateway.sock",
            10001,
            "https://api.deepseek.com",
            "deepseek-flash",
            "unused-secret",
        )
        for first in (
            "GET /v1/run HTTP/1.1",
            "POST /other HTTP/1.1",
            "POST /v1/run?extra=1 HTTP/1.1",
        ):
            writer = MagicMock()
            writer.drain = AsyncMock()
            writer.wait_closed = AsyncMock()
            with (
                self.subTest(first=first),
                patch("drop_agent_runtime.server.verify_peer"),
                patch(
                    "drop_agent_runtime.server.os.getuid",
                    return_value=10002,
                    create=True,
                ),
                patch(
                    "drop_agent_runtime.server.read_http",
                    new=AsyncMock(
                        return_value=(
                            first,
                            {"content-type": "application/json"},
                            json.dumps(run_body()).encode(),
                        )
                    ),
                ),
                patch("drop_agent_runtime.server.run_agent", new=AsyncMock()) as agent,
            ):
                await Server(config).handle(asyncio.StreamReader(), writer)
                agent.assert_not_awaited()
                raw = b"".join(call.args[0] for call in writer.write.call_args_list)
                self.assertIn(b"AGENT_RUNTIME_PROTOCOL_ERROR", raw)

    async def test_http_parser_rejects_wrong_method_and_path(self):
        for first in (
            "GET /v1/run HTTP/1.1",
            "POST /other HTTP/1.1",
            "POST /v1/run?extra=1 HTTP/1.1",
        ):
            reader = asyncio.StreamReader()
            body = json.dumps(run_body()).encode()
            reader.feed_data(
                f"{first}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body
            )
            reader.feed_eof()
            with self.subTest(first=first), self.assertRaises(ProtocolError):
                await read_http(reader)

    async def test_http_bounds_duplicate_headers_and_chunking_rejected(self):
        for raw in (
            b"POST /v1/run HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n",
            b"POST /v1/run HTTP/1.1\r\nContent-Length: 0\r\nTransfer-Encoding: chunked\r\n\r\n",
            b"POST /v1/run HTTP/1.1\r\nContent-Length: 262145\r\n\r\n",
        ):
            reader = asyncio.StreamReader()
            reader.feed_data(raw)
            reader.feed_eof()
            with self.assertRaises(ProtocolError):
                await read_http(reader)


if __name__ == "__main__":
    unittest.main()
