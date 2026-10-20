"""Linux 私有 Unix socket 控制入口，不提供数据库、文件、shell 或 MCP 工具。

先核对内核给出的对端 UID，再解析有界请求；调用方断连会取消本次 SDK 任务。
就绪信息与固定错误码可返回 Go，供应商异常及配置内容不能进入响应或日志。
"""

import asyncio
import os
import signal
import stat
from importlib.metadata import version
from pathlib import Path

from . import AGENTSCOPE_VERSION
from .config import Config
from .runner import RunRequest, SafeRuntimeFailure, run_agent
from .wire import (
    MAX_HEADER_BYTES,
    Gateway,
    ProtocolError,
    close_writer,
    read_http,
    send_response,
    strict_json,
    verify_peer,
)


def health() -> dict:
    installed = version("agentscope")
    return {
        "ready": installed == AGENTSCOPE_VERSION,
        "framework": "agentscope",
        "framework_version": installed,
    }


def validate_socket_parent(path: Path, *, owner_uid: int, group_gid: int) -> None:
    """核对 socket 父目录的属主与权限，防止非可信进程替换本机控制入口。"""
    if not path.is_absolute():
        raise ValueError("invalid runner socket directory")
    try:
        parent = path.parent.lstat()
    except OSError:
        raise ValueError("invalid runner socket directory") from None
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != owner_uid
        or parent.st_gid != group_gid
        or stat.S_IMODE(parent.st_mode) != 0o2750
    ):
        raise ValueError("unsafe runner socket directory")


class Server:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.tasks: set[asyncio.Task] = set()
        self.run_ids: set[str] = set()
        self.active = 0

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """处理单连接：先校验 UID 与请求，再争取并发名额并驱动一次运行。

        同时监听调用方 EOF；断连、服务停止或超时都应取消对应 SDK 任务，结束后释放运行登记与资源。
        """
        current = asyncio.current_task()
        self.tasks.add(current)
        job = None
        disconnected = None
        admitted = False
        run_id = None
        gateway = None
        try:
            verify_peer(writer, {self.config.peer_uid, os.getuid()})
            async with asyncio.timeout(10):
                first, headers, body = await read_http(reader)
            if first == "GET /health HTTP/1.1":
                if body:
                    raise ProtocolError()
                await send_response(writer, 200, health())
                return
            if first != "POST /v1/run HTTP/1.1":
                raise ProtocolError()
            verify_peer(writer, self.config.peer_uid)
            if not headers.get("content-type", "").startswith("application/json"):
                raise ProtocolError()
            request = RunRequest.parse(strict_json(body), self.config.model)
            if self.active >= self.config.max_concurrent:
                raise ProtocolError("AGENT_RUNTIME_BUSY")
            if request.run_id in self.run_ids:
                raise ProtocolError("AGENT_RUNTIME_DUPLICATE_RUN")
            run_id = request.run_id
            self.run_ids.add(run_id)
            self.active += 1
            admitted = True
            gateway = Gateway(
                self.config.callback_socket_path,
                self.config.peer_uid,
                run_id,
                request.capability,
            )
            job = asyncio.create_task(run_agent(request, self.config, gateway))
            # 写端半关闭也视作取消；正常 Go HTTP 客户端会保持连接，直到完整读完响应。
            disconnected = asyncio.create_task(reader.read(1))
            done, _ = await asyncio.wait(
                {job, disconnected}, return_when=asyncio.FIRST_COMPLETED
            )
            if disconnected in done:
                gateway.cancelled = True
                job.cancel()
                await asyncio.gather(job, return_exceptions=True)
                return
            response = await job
            await send_response(writer, 200, response)
        except asyncio.CancelledError:
            raise
        except Exception as failure:  # noqa: BLE001 -- SDK 异常必须脱敏。
            code = (
                failure.code
                if isinstance(failure, ProtocolError)
                else "AGENT_RUNTIME_FAILED"
            )
            try:
                body = {"error_code": code}
                if isinstance(failure, SafeRuntimeFailure):
                    body["diagnostics"] = failure.diagnostics
                await send_response(writer, 400, body)
            except (OSError, ProtocolError):
                pass
        finally:
            if gateway is not None:
                gateway.cancelled = True
            pending = [
                task
                for task in (job, disconnected)
                if task is not None and not task.done()
            ]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if admitted:
                self.active -= 1
                self.run_ids.discard(run_id)
            self.tasks.discard(current)
            await close_writer(writer)

    async def stop(self) -> None:
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def serve(config: Config) -> None:
    """绑定私有控制 socket 并管理服务关闭；不暴露公网 HTTP 端口或通用命令工具。"""
    if not health()["ready"] or not 1 <= config.max_concurrent <= 16:
        raise ValueError("invalid runner configuration")
    config.read_api_key()
    path = Path(config.socket_path)
    validate_socket_parent(path, owner_uid=os.getuid(), group_gid=config.socket_group)
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("unsafe existing runner socket")
        path.unlink()
    handler = Server(config)
    server = await asyncio.start_unix_server(
        handler.handle, path=path, limit=MAX_HEADER_BYTES
    )
    path.chmod(0o660)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        async with server:
            await stop.wait()
    finally:
        server.close()
        await server.wait_closed()
        await handler.stop()
        path.unlink(missing_ok=True)


def main() -> None:
    try:
        asyncio.run(serve(Config.from_env()))
    except (Exception, KeyboardInterrupt):  # noqa: BLE001 -- 不输出 SDK 或配置细节。
        # SDK 异常和配置可能携带供应商 URL、请求内容或凭据，不能格式化输出；
        # Go 通过固定就绪信息判断失败。
        print("AGENT_RUNTIME_STOPPED", flush=True)
        raise SystemExit(1) from None
