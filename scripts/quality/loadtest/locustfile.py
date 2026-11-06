"""Drop HTTP 负载测试负载定义（Locust）。

只发只读请求。写接口（创建/取消/重试任务、建用户、改排程）全部不发，原因：
创建任务被 ``createSlots`` 串行化（容量 1），写请求还有每用户 60 次/分钟限流，
用压测去撞限流器得到的是 429 比例，不是平台容量。

认证方式刻意不用「每个虚拟用户各登录一次」：登录限流是每 IP 10 次/15 分钟，
20 个虚拟用户会直接把自己锁死。会话由 ``login_sessions.py`` 预先换取，
这里只读会话文件、轮转复用，因此 N 个虚拟用户共享 k 个真实会话（k ≤ 8）。

环境变量（全部可选）：
    DROP_LOADTEST_SESSIONS   会话池 JSON 路径；不设置时只注册 PublicUser
    DROP_LOADTEST_INSECURE   "1" 时不校验 TLS（自签 IP 证书需要）
    DROP_LOADTEST_STAGES     "5:60,10:60,20:60" 阶梯（用户数:持续秒数）
    DROP_LOADTEST_MAX_USERS  阶梯人数硬上限，默认 100
    DROP_LOADTEST_MAX_SECONDS 总时长硬上限，默认 600
    DROP_LOADTEST_EXTRA_OUT  测试结束写状态分布 JSON 的路径
"""

from __future__ import annotations

import json
import os
import random
import re
from collections.abc import Callable
from typing import Any

from locust import HttpUser, LoadTestShape, between, events, tag, task

OK = frozenset({200})
# 契约内可接受的 4xx：403=无该组权限，404=资源不在可见范围，409=结果未就绪/状态不允许，
# 410=制品过期。它们说明授权与状态机在工作，不是平台故障。
SOFT_OK = frozenset({403, 404, 409, 410})
TIMEOUT = 15.0
ASSET_RE = re.compile(r'(?:src|href)="(/assets/[^"]+)"')

_EXTRA_OUT = os.environ.get("DROP_LOADTEST_EXTRA_OUT", "")
_COUNTS: dict[str, dict[str, int]] = {}
_BYTES = {"total": 0}
_ASSETS: list[str] | None = None


def _record(name: str, status: int, size: int) -> None:
    bucket = _COUNTS.setdefault(name, {})
    key = str(status)
    bucket[key] = bucket.get(key, 0) + 1
    _BYTES["total"] += size


@events.test_stop.add_listener
def _write_extra(environment, **_kwargs: Any) -> None:
    """把状态码分布和字节量写到测试进程外的 JSON，供 summarize.py 合成收据。"""
    if not _EXTRA_OUT:
        return
    payload = {
        "status_counts": _COUNTS,
        "bytes_total": _BYTES["total"],
        "assets": _ASSETS or [],
    }
    tmp = f"{_EXTRA_OUT}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
    os.replace(tmp, _EXTRA_OUT)


def _evaluate_status(status: int, body: bytes, body_kind: str) -> str | None:
    """返回 None 表示通过，返回字符串表示这是一次失败（文本会进 Locust 失败表）。"""
    if status in OK:
        if body_kind == "json" and body[:1] not in (b"{", b"["):
            return f"expected JSON body, got {body[:32]!r}"
        return None
    if status in SOFT_OK:
        return None
    return f"unexpected status {status}"


def _request(
    user: HttpUser,
    path: str,
    name: str,
    *,
    body_kind: str = "json",
    headers: dict[str, str] | None = None,
    evaluate: Callable[[int, bytes], str | None] | None = None,
    on_ok: Callable[[bytes], None] | None = None,
) -> None:
    """发一次 GET，统一记录状态码分布、判定成败。

    传输层异常（连接重置、超时）已经由 Locust 记成失败，这里只保证任务不会二次抛出
    打断后续请求，因此有意吞掉异常。
    """
    check = evaluate or (lambda status, body: _evaluate_status(status, body, body_kind))
    try:
        with user.client.get(path, name=name, catch_response=True, timeout=TIMEOUT,
                             headers=headers) as resp:
            status = getattr(resp, "status_code", 0)
            body = getattr(resp, "content", b"") or b""
            _record(name, status, len(body))
            message = check(status, body)
            if message is None:
                resp.success()
                if on_ok is not None:
                    on_ok(body)
            else:
                resp.failure(message)
    except Exception:  # noqa: BLE001 - 见函数说明：传输错误由 Locust 计失败
        return


def _load_sessions(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    sessions = payload.get("sessions") or []
    if not sessions:
        raise RuntimeError("session pool is empty; re-run login_sessions.py")
    for item in sessions:
        if set(item) != {"label", "cookie", "csrf"}:
            raise RuntimeError("session pool has unexpected fields; refusing to guess")
    return sessions


def _extract_task_id(body: bytes) -> int | None:
    """从任务列表里取一个真实任务 ID，供详情/事件/结果读路径复用。"""
    try:
        items = json.loads(body)["data"].get("items") or []
        return int(items[0]["id"]) if items else None
    except (ValueError, KeyError, AttributeError, TypeError):
        return None


def _discover_assets(user: HttpUser) -> list[str]:
    """像浏览器那样先取入口 HTML，再从中取带哈希的构建产物路径。"""
    try:
        with user.client.get("/", name="GET / (asset discovery)", timeout=TIMEOUT,
                             catch_response=True) as resp:
            status = getattr(resp, "status_code", 0)
            body = getattr(resp, "content", b"") or b""
            _record("GET / (asset discovery)", status, len(body))
            resp.success()
            return ASSET_RE.findall(getattr(resp, "text", "") or "")
    except Exception:  # noqa: BLE001 - 传输错误由 Locust 计失败
        return []


def _think_time(default_min: float, default_max: float):
    """思考时间由环境变量覆盖，用于把"交互模型"和"容量模型"分开。

    ``DROP_LOADTEST_THINK_MS=100,300`` 表示每个虚拟用户两次请求之间等 0.1–0.3 秒。
    这个值越小，同一份脚本越接近开环打满；默认保持 1–3 秒的交互式节奏。
    """
    raw = os.environ.get("DROP_LOADTEST_THINK_MS", "")
    if not raw:
        return between(default_min, default_max)
    head, _, tail = raw.partition(",")
    try:
        low = int(head) / 1000.0
        high = int(tail or head) / 1000.0
    except ValueError:
        raise RuntimeError("DROP_LOADTEST_THINK_MS must look like '100,300'") from None
    if low < 0 or high < low or high > 60:
        raise RuntimeError("think time out of range (0–60000 ms, low ≤ high)")
    return between(low, high)


class PublicUser(HttpUser):
    """未登录可见的那一层：公网 TLS 入口、静态资源、SPA 路由和 401 拒绝路径。

    这一层没有账号也能跑，测的是 nginx 网关（cpus 0.15）、web 容器（cpus 0.3）
    和 Go 认证中间件的固定开销；它**不能**代表登录后的业务查询能力。
    """

    weight = 1
    wait_time = _think_time(1, 3)

    def on_start(self) -> None:
        if os.environ.get("DROP_LOADTEST_INSECURE") == "1":
            self.client.verify = False  # 自签 IP 证书；仅限本次压测进程

    @task(3)
    def page_shell(self) -> None:
        _request(self, "/", "GET /", body_kind="any")

    @task(2)
    def spa_route(self) -> None:
        _request(self, random.choice(["/tasks", "/agents", "/quality"]), "GET /<spa-route>",
                 body_kind="any")

    @task(4)
    @tag("assets")
    def static_asset(self) -> None:
        """单次 165KB 的正文，容量探针里要用 ``--exclude-tags assets`` 排除，
        否则测到的是本机下行带宽而不是服务端处理能力。"""
        global _ASSETS
        if _ASSETS is None:
            _ASSETS = _discover_assets(self)
        if _ASSETS:
            _request(self, random.choice(_ASSETS), "GET /assets/*", body_kind="any")

    @task(2)
    def auth_providers(self) -> None:
        _request(self, "/api/v1/auth/providers", "GET /api/v1/auth/providers")

    @task(2)
    def unauth_rejection(self) -> None:
        """未带会话的安全读请求：测认证中间件的拒绝路径，永远不该返回 2xx。"""

        def expect_401(status: int, _body: bytes) -> str | None:
            if status in (401, 429):  # 429 说明压测自己触达了登录/写限流，显式记录而不是当失败
                return None
            return f"expected 401, got {status}"

        _request(self, "/api/v1/tasks", "GET /api/v1/tasks (401 path)", evaluate=expect_401)


def _make_api_user_class():
    """仅当提供会话池时才定义 ApiUser，避免无账号运行时出现空转用户类。"""
    path = os.environ.get("DROP_LOADTEST_SESSIONS", "")
    if not path:
        return None
    pool = _load_sessions(path)

    class ApiUser(HttpUser):
        """登录后的读路径：概览、任务列表/详情/事件/结果、节点、排程、质量记录。"""

        weight = 4
        wait_time = _think_time(0.5, 2)

        def on_start(self) -> None:
            if os.environ.get("DROP_LOADTEST_INSECURE") == "1":
                self.client.verify = False
            session = random.choice(pool)
            self.csrf = session["csrf"]
            self.client.cookies.set("drop_session", session["cookie"])
            self.task_id: int | None = None

        def _get(self, path: str, name: str) -> None:
            def remember(body: bytes) -> None:
                if self.task_id is None:
                    self.task_id = _extract_task_id(body)

            _request(self, path, name, headers={"X-CSRF-Token": self.csrf}, on_ok=remember)

        @task(3)
        def overview(self) -> None:
            self._get("/api/v1/overview", "GET /api/v1/overview")

        @task(6)
        def task_list(self) -> None:
            page = random.randint(1, 3)
            self._get(f"/api/v1/tasks?page={page}&page_size=20", "GET /api/v1/tasks")

        @task(4)
        def task_detail(self) -> None:
            if self.task_id:
                self._get(f"/api/v1/tasks/{self.task_id}", "GET /api/v1/tasks/:id")

        @task(3)
        def task_events(self) -> None:
            if self.task_id:
                self._get(f"/api/v1/tasks/{self.task_id}/events?page=1&page_size=50",
                          "GET /api/v1/tasks/:id/events")

        @task(2)
        def task_result(self) -> None:
            if self.task_id:
                self._get(f"/api/v1/tasks/{self.task_id}/result", "GET /api/v1/tasks/:id/result")

        @task(2)
        def agents(self) -> None:
            self._get("/api/v1/agents", "GET /api/v1/agents")

        @task(1)
        def kinds(self) -> None:
            self._get("/api/v1/task-kinds", "GET /api/v1/task-kinds")

        @task(1)
        def me(self) -> None:
            self._get("/api/v1/auth/me", "GET /api/v1/auth/me")

        @task(1)
        def quality_runs(self) -> None:
            self._get("/api/v1/quality/runs", "GET /api/v1/quality/runs")

        @task(1)
        def schedules(self) -> None:
            self._get("/api/v1/schedules", "GET /api/v1/schedules")

        @task(1)
        def page_shell(self) -> None:
            _request(self, "/", "GET / (authed)", body_kind="any")

    return ApiUser


if os.environ.get("DROP_LOADTEST_SESSIONS"):
    # 只有拿到会话池才把 ApiUser 暴露给 Locust；否则它连类都不存在，
    # 不会出现"用户被创建出来却无事可做"的空转统计。
    # 注意：同一个类不能同时挂在两个模块级名字上，Locust 会判定用户类重名。
    ApiUser = _make_api_user_class()


def parse_stages(raw: str) -> list[tuple[int, int]]:
    """解析 ``用户数:持续秒数,...``，越界或格式错误立即失败，不做"差不多"的兜底。"""
    stages: list[tuple[int, int]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        head, _, tail = chunk.partition(":")
        try:
            users, seconds = int(head), int(tail)
        except ValueError:
            raise RuntimeError(f"bad stage {chunk!r}; expected users:seconds") from None
        if users < 1 or seconds < 5:
            raise RuntimeError(f"stage {chunk!r} below minimum (1 user, 5s)")
        stages.append((users, seconds))
    if not stages:
        raise RuntimeError("stage list is empty")
    return stages


def plan_stage(elapsed: float, stages: list[tuple[int, int]]) -> tuple[int, int] | None:
    """返回该时刻应达到的 (用户数, 每秒启动数)；阶梯全部走完返回 None 结束测试。"""
    cumulative = 0.0
    for users, seconds in stages:
        cumulative += seconds
        if elapsed < cumulative:
            return users, max(1, users // 5)
    return None


# 只有显式给了阶梯才定义 LoadTestShape：否则 Locust 会把这个类当成唯一 shape，
# 它的 tick 返回 None 会让测试刚开始就结束。
if os.environ.get("DROP_LOADTEST_STAGES"):
    _STAGES = parse_stages(os.environ["DROP_LOADTEST_STAGES"])
    if max(users for users, _ in _STAGES) > int(os.environ.get("DROP_LOADTEST_MAX_USERS", "100")):
        raise RuntimeError("stage users exceed configured hard cap")
    if sum(seconds for _, seconds in _STAGES) > int(os.environ.get("DROP_LOADTEST_MAX_SECONDS", "600")):
        raise RuntimeError("total stage seconds exceed configured hard cap")

    class StagedShape(LoadTestShape):
        """按预先声明的阶梯升压；阶梯之外不再追加负载。"""

        def tick(self):
            step = plan_stage(self.get_run_time(), _STAGES)
            if step is None:
                return None
            return step  # (user_count, spawn_rate)
