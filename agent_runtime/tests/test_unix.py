"""Linux kernel Unix-socket tests with imported synthetic model/tool fixtures."""

import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx2 as httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from drop_agent_runtime.config import Config
from drop_agent_runtime.runner import run_agent
from drop_agent_runtime.server import Server, validate_socket_parent
from drop_agent_runtime.wire import (
    AcceptedJSON,
    Gateway,
    ProtocolError,
    close_writer,
    json_bytes,
    read_http,
    send_response,
    strict_json,
)
from test_runtime import Provider, completion, run_body


@unittest.skipUnless(
    sys.platform == "linux", "Linux SO_PEERCRED regression requires Linux"
)
class UnixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="drop-as-test-")
        root = Path(self.directory.name)
        secret = root / "fixture-secret"
        secret.write_text("imported-fixture-key", encoding="utf-8")
        self.config = Config(
            str(root / "runner.sock"),
            str(root / "gateway.sock"),
            os.getuid(),
            "https://api.deepseek.com",
            "deepseek-flash",
            str(secret),
        )
        self.servers = []
        self.body = run_body()
        self.callbacks = []
        self.callback_jobs = set()

    async def asyncTearDown(self):
        for server in self.servers:
            server.close()
            await server.wait_closed()
        for task in list(self.callback_jobs):
            task.cancel()
        if self.callback_jobs:
            await asyncio.gather(*self.callback_jobs, return_exceptions=True)
        self.directory.cleanup()

    async def start_gateway(self, handler=None):
        async def callback(reader, writer):
            current = asyncio.current_task()
            self.callback_jobs.add(current)
            try:
                _, headers, raw = await read_http(
                    reader,
                    allowed_first=(
                        f"POST /v1/callback/{self.body['run_id']} HTTP/1.1",
                    ),
                )
                self.assertEqual(
                    headers["authorization"], "Bearer " + self.body["capability"]
                )
                payload = strict_json(raw)
                self.callbacks.append(payload)
                response = await handler(reader, payload) if handler else None
                if response is None:
                    if payload["type"] == "tool":
                        response = {
                            "ok": True,
                            "observation": {
                                "evidence_id": "E2",
                                "source": "imported synthetic fixture",
                            },
                        }
                    elif payload["type"] == "finish":
                        response = {
                            "ok": True,
                            "accepted": True,
                            "result": {"summary": "accepted synthetic fixture"},
                        }
                    else:
                        response = {"ok": True}
                if not reader.at_eof():
                    await send_response(writer, 200, response)
            except (OSError, ProtocolError):
                # Peer rejection and cancellation deliberately close a
                # connection before its imported fixture handler can reply.
                pass
            finally:
                self.callback_jobs.discard(current)
                await close_writer(writer)

        server = await asyncio.start_unix_server(
            callback, self.config.callback_socket_path, limit=8192
        )
        self.servers.append(server)

    async def start_runner(self, provider):
        handler = Server(self.config)

        async def actual_sdk(request, config, gateway):
            return await run_agent(
                request, config, gateway, transport=httpx.MockTransport(provider)
            )

        original = patch("drop_agent_runtime.server.run_agent", actual_sdk)
        original.start()
        self.addCleanup(original.stop)
        server = await asyncio.start_unix_server(
            handler.handle, self.config.socket_path, limit=8192
        )
        self.servers.append(server)
        return handler

    async def request(self, first="POST /v1/run HTTP/1.1"):
        reader, writer = await asyncio.open_unix_connection(
            self.config.socket_path, limit=8192
        )
        raw = json_bytes(self.body)
        writer.write(
            f"{first}\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n".encode()
            + raw
        )
        await writer.drain()
        return reader, writer

    async def test_actual_uds_rejects_wrong_method_path_with_valid_run_body(self):
        provider = Provider([])
        handler = await self.start_runner(provider)
        for first in (
            "GET /v1/run HTTP/1.1",
            "POST /other HTTP/1.1",
            "POST /v1/run?extra=1 HTTP/1.1",
        ):
            reader, writer = await self.request(first)
            try:
                raw = await asyncio.wait_for(reader.read(), 2)
            finally:
                await close_writer(writer)
            self.assertIn(b"AGENT_RUNTIME_PROTOCOL_ERROR", raw)
        self.assertEqual(provider.requests, [])
        self.assertEqual(handler.active, 0)
        await handler.stop()

    async def test_real_socket_parent_mode_and_symlink_rejected_without_root(self):
        path = Path(self.config.socket_path)
        path.parent.chmod(0o2750)
        validate_socket_parent(path, owner_uid=os.getuid(), group_gid=os.getgid())
        path.parent.chmod(0o2770)
        with self.assertRaises(ValueError):
            validate_socket_parent(path, owner_uid=os.getuid(), group_gid=os.getgid())
        path.parent.chmod(0o2750)
        link = path.parent / "link"
        link.symlink_to(path.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            validate_socket_parent(
                link / "runner.sock", owner_uid=os.getuid(), group_gid=os.getgid()
            )

    async def test_actual_uds_peercred_gateway_and_sdk_round_trip(self):
        await self.start_gateway()
        provider = Provider(
            [
                completion(calls=[("imported-unix-call", "inspect_hotspots", "{}")]),
                completion('{"summary":"candidate"}'),
            ]
        )
        handler = await self.start_runner(provider)
        reader, writer = await self.request()
        try:
            _, _, raw = await asyncio.wait_for(read_http(reader, response=True), 5)
            result = strict_json(raw)
        finally:
            await close_writer(writer)
        self.assertEqual(result["result"], {"summary": "accepted synthetic fixture"})
        self.assertEqual(result["runtime"]["model_calls"], 2)
        self.assertEqual(result["runtime"]["tool_calls"], 1)
        self.assertEqual(len(self.callbacks), 6)
        await handler.stop()
        self.assertEqual(handler.active, 0)

    async def test_actual_uds_accepted_report_above_16k_round_trip(self):
        accepted = {
            "summary": "Imported accepted fixture",
            "validation": {"padding": "x" * 24000},
        }

        async def large_report(reader, payload):
            if payload["type"] == "finish":
                return {"ok": True, "accepted": True, "result": accepted}
            return None

        await self.start_gateway(large_report)
        provider = Provider([completion('{"summary":"candidate"}')])
        handler = await self.start_runner(provider)
        reader, writer = await self.request()
        try:
            _, _, raw = await asyncio.wait_for(read_http(reader, response=True), 5)
        finally:
            await close_writer(writer)
        self.assertGreater(len(raw), 16384)
        self.assertEqual(strict_json(raw)["result"], accepted)
        await handler.stop()

    async def test_actual_uds_preserves_large_accepted_numeric_tokens_exactly(self):
        accepted = (
            b'{"fixed":0.000001,"exponent":1e-6,"negative_zero":-0,"precise":0.12345678901234567890123456789,"padding":"'
            + b"x" * 24576
            + b'"}'
        )

        async def exact_report(reader, payload):
            if payload["type"] == "finish":
                return {"ok": True, "accepted": True, "result": AcceptedJSON(accepted)}
            return None

        await self.start_gateway(exact_report)
        provider = Provider([completion('{"summary":"candidate"}')])
        handler = await self.start_runner(provider)
        reader, writer = await self.request()
        try:
            _, _, raw = await asyncio.wait_for(read_http(reader, response=True), 5)
        finally:
            await close_writer(writer)
        self.assertGreater(len(raw), 24576)
        self.assertIn(b'"result":' + accepted, raw)
        await handler.stop()

    async def test_gateway_peer_uid_mismatch_fails_closed(self):
        await self.start_gateway()
        gateway = Gateway(
            self.config.callback_socket_path,
            os.getuid() + 1000,
            self.body["run_id"],
            self.body["capability"],
        )
        with self.assertRaisesRegex(ProtocolError, "AGENT_RUNTIME_PEER_REJECTED"):
            await gateway.call(
                {"type": "model_start", "round": 1, "call_id": "fixture"}
            )

    async def test_runner_peer_uid_mismatch_rejects_run(self):
        self.config = replace(self.config, peer_uid=os.getuid() + 1000)
        provider = Provider([])
        handler = await self.start_runner(provider)
        reader, writer = await self.request()
        try:
            raw = await asyncio.wait_for(reader.read(), 2)
        finally:
            await close_writer(writer)
        self.assertIn(b"AGENT_RUNTIME_PEER_REJECTED", raw)
        self.assertEqual(provider.requests, [])
        await handler.stop()

    async def test_disconnect_cancels_actual_sdk_and_callback_socket(self):
        callback_started, callback_closed = asyncio.Event(), asyncio.Event()

        async def stalled(reader, payload):
            if payload["type"] == "tool":
                callback_started.set()
                self.assertEqual(await reader.read(), b"")
                callback_closed.set()
                return {"ok": False, "fatal": True, "error_code": "CANCELLED"}
            return None

        await self.start_gateway(stalled)
        provider = Provider(
            [completion(calls=[("imported-cancel-call", "inspect_hotspots", "{}")])]
        )
        handler = await self.start_runner(provider)
        _, writer = await self.request()
        await asyncio.wait_for(callback_started.wait(), 5)
        await close_writer(writer)
        await asyncio.wait_for(callback_closed.wait(), 2)
        await handler.stop()
        self.assertEqual(handler.active, 0)
        self.assertEqual(handler.run_ids, set())
        self.assertEqual(len(provider.requests), 1)
        self.assertFalse(any(payload["type"] == "finish" for payload in self.callbacks))

    async def test_own_uid_health_without_content_length(self):
        await self.start_runner(Provider([]))
        reader, writer = await asyncio.open_unix_connection(
            self.config.socket_path, limit=8192
        )
        writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        try:
            _, _, raw = await asyncio.wait_for(read_http(reader, response=True), 2)
            self.assertTrue(strict_json(raw)["ready"])
        finally:
            await close_writer(writer)
