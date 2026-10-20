"""One-request HTTP/1.1 over Unix sockets with bounded buffers and peer identity."""

import asyncio
import contextvars
import json
import socket
import struct
from dataclasses import dataclass
from typing import Any

MAX_HEADER_BYTES = 8192
MAX_BODY_BYTES = 262144

DIAGNOSTIC_STAGES = frozenset(
    {
        "setup",
        "model_start",
        "provider_request",
        "provider_response",
        "model_parse",
        "model_end",
        "tool_callback",
        "agent_reply",
        "finish_callback",
        "result_encode",
        "response_write",
    }
)
DIAGNOSTIC_KINDS = frozenset(
    {"protocol", "timeout", "transport", "sdk", "validation", "internal"}
)


@dataclass
class RunDiagnostics:
    stage: str = "setup"
    model_calls: int = 0
    tool_calls: int = 0


current_diagnostics: contextvars.ContextVar[RunDiagnostics | None] = (
    contextvars.ContextVar("drop_run_diagnostics", default=None)
)


def set_stage(stage: str) -> None:
    if stage not in DIAGNOSTIC_STAGES:
        raise ValueError("unknown safe diagnostic stage")
    diagnostics = current_diagnostics.get()
    if diagnostics is not None:
        diagnostics.stage = stage


@dataclass(frozen=True)
class AcceptedJSON:
    """Opaque Go-accepted JSON; preserve every numeric token and its precision."""

    raw: bytes


class ProtocolError(Exception):
    """A fixed public error code; never contains provider responses or credentials."""

    def __init__(self, code: str = "AGENT_RUNTIME_PROTOCOL_ERROR") -> None:
        self.code = code
        super().__init__(code)


def json_bytes(value: Any) -> bytes:
    if isinstance(value, AcceptedJSON):
        return value.raw
    if isinstance(value, dict):
        return (
            b"{"
            + b",".join(
                json_bytes(str(key)) + b":" + json_bytes(item)
                for key, item in value.items()
            )
            + b"}"
        )
    if isinstance(value, (list, tuple)):
        return b"[" + b",".join(json_bytes(item) for item in value) + b"]"
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def accepted_json_field(raw: bytes, field: str = "result") -> AcceptedJSON:
    # Validate the entire document first, including duplicate keys. Then use
    # decoder offsets to copy the original value, never a float conversion.
    parsed = strict_json(raw)
    if not isinstance(parsed, dict) or field not in parsed:
        raise ProtocolError()
    source = raw.decode("utf-8")
    decoder = json.JSONDecoder(parse_int=lambda _: None, parse_float=lambda _: None)
    index = 0

    def whitespace(position: int) -> int:
        while position < len(source) and source[position] in " \t\r\n":
            position += 1
        return position

    index = whitespace(index) + 1
    while True:
        index = whitespace(index)
        key, index = decoder.raw_decode(source, index)
        index = whitespace(index)
        if source[index] != ":":
            raise ProtocolError()
        start = whitespace(index + 1)
        _, end = decoder.raw_decode(source, start)
        if key == field:
            return AcceptedJSON(source[start:end].encode("utf-8"))
        index = whitespace(end)
        if source[index] != ",":
            raise ProtocolError()
        index += 1


def strict_json(raw: bytes | str) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProtocolError()
            result[key] = value
        return result

    def bad_constant(_: str) -> None:
        raise ProtocolError()

    try:
        return json.loads(
            raw, object_pairs_hook=object_pairs, parse_constant=bad_constant
        )
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError() from None


def verify_peer(writer: asyncio.StreamWriter, expected_uid: int | set[int]) -> None:
    conn = writer.get_extra_info("socket")
    if conn is None or not hasattr(socket, "SO_PEERCRED"):
        raise ProtocolError("AGENT_RUNTIME_PEER_REJECTED")
    try:
        _, uid, _ = struct.unpack(
            "3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
    except (OSError, struct.error):
        raise ProtocolError("AGENT_RUNTIME_PEER_REJECTED") from None
    if uid not in (expected_uid if isinstance(expected_uid, set) else {expected_uid}):
        raise ProtocolError("AGENT_RUNTIME_PEER_REJECTED")


async def read_http(
    reader: asyncio.StreamReader,
    *,
    response: bool = False,
    max_body_bytes: int = MAX_BODY_BYTES,
    allowed_first: tuple[str, ...] = ("POST /v1/run HTTP/1.1", "GET /health HTTP/1.1"),
) -> tuple[str, dict, bytes]:
    try:
        header = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        raise ProtocolError() from None
    if len(header) > MAX_HEADER_BYTES:
        raise ProtocolError()
    try:
        lines = header[:-4].decode("ascii").split("\r\n")
    except UnicodeError:
        raise ProtocolError() from None
    first = lines[0]
    headers = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        key = key.lower()
        if not separator or key in headers or not key or any(c.isspace() for c in key):
            raise ProtocolError()
        headers[key] = value.strip()
    length_text = headers.get(
        "content-length", "0" if first == "GET /health HTTP/1.1" else ""
    )
    if (
        not length_text.isascii()
        or not length_text.isdigit()
        or len(length_text) > 7
        or "transfer-encoding" in headers
    ):
        raise ProtocolError()
    size = int(length_text)
    if size > max_body_bytes:
        raise ProtocolError("AGENT_RUNTIME_BODY_TOO_LARGE")
    if response:
        if first != "HTTP/1.1 200 OK":
            raise ProtocolError("AGENT_RUNTIME_GATEWAY_REJECTED")
    elif first not in allowed_first:
        raise ProtocolError()
    try:
        body = await reader.readexactly(size)
    except asyncio.IncompleteReadError:
        raise ProtocolError() from None
    return first, headers, body


async def send_response(writer: asyncio.StreamWriter, status: int, body: Any) -> None:
    raw = json_bytes(body)
    if len(raw) > MAX_BODY_BYTES:
        raise ProtocolError("AGENT_RUNTIME_BODY_TOO_LARGE")
    reason = "OK" if status == 200 else "Rejected"
    writer.write(
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode(
            "ascii"
        )
        + raw
    )
    await writer.drain()


async def close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, RuntimeError):
        pass


class Gateway:
    def __init__(self, path: str, peer_uid: int, run_id: str, capability: str) -> None:
        self.path = path
        self.peer_uid = peer_uid
        self.run_id = run_id
        self.capability = capability
        self.cancelled = False

    async def call(self, payload: dict) -> dict:
        if self.cancelled:
            raise asyncio.CancelledError()
        raw = json_bytes(payload)
        if len(raw) > 65536:
            raise ProtocolError("AGENT_RUNTIME_BODY_TOO_LARGE")
        reader, writer = await asyncio.open_unix_connection(
            self.path, limit=MAX_HEADER_BYTES
        )
        try:
            verify_peer(writer, self.peer_uid)
            writer.write(
                f"POST /v1/callback/{self.run_id} HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer {self.capability}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n".encode(
                    "ascii"
                )
                + raw
            )
            await writer.drain()
            async with asyncio.timeout(60):
                _, _, body = await read_http(
                    reader, response=True, max_body_bytes=65536
                )
            result = strict_json(body)
            if len(body) > 65536 or not isinstance(result, dict):
                raise ProtocolError()
            if "result" in result and (
                result.get("accepted") is True or result.get("terminal") is True
            ):
                result["result"] = accepted_json_field(body)
            return result
        except (OSError, TimeoutError):
            raise ProtocolError("AGENT_RUNTIME_GATEWAY_UNAVAILABLE") from None
        finally:
            await close_writer(writer)
