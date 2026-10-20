"""Container probe uses no model and discloses only fixed readiness metadata."""

import asyncio
import os

from .wire import close_writer, read_http, strict_json


async def probe() -> None:
    reader, writer = await asyncio.open_unix_connection(
        os.environ.get(
            "AGENTSCOPE_RUNNER_SOCKET",
            os.environ.get("AGENTSCOPE_SOCKET", "/run/drop-ai-runner/control.sock"),
        ),
        limit=8192,
    )
    try:
        writer.write(
            b"GET /health HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n"
        )
        await writer.drain()
        _, _, body = await read_http(reader, response=True)
        if strict_json(body).get("ready") is not True:
            raise ValueError()
    finally:
        await close_writer(writer)


if __name__ == "__main__":
    try:
        asyncio.run(asyncio.wait_for(probe(), 3))
    except Exception:  # noqa: BLE001 -- container probe discloses only its exit status.
        raise SystemExit(1) from None
