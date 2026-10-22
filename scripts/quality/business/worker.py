"""只读接入真实 CRM 路由，在独立进程内执行固定业务需求。

使用真实 FastAPI 路由、Pydantic、权限依赖、业务服务和 MemoryRepository。
身份、记录、审计/事件均为合成夹具；不连接 GoTrue、数据库或真实渠道。
这验证 ASGI/HTTP 契约和业务守卫，不宣称 TCP/TLS、PostgreSQL RLS 或全 CRM E2E。
"""

from __future__ import annotations

import asyncio
import builtins
import copy
import hashlib
import importlib.metadata
import io
import json
import logging
import socket
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from contract import CASE_INDEX, MUTANTS, canonical, digest

ORG = "10000000-0000-4000-8000-000000000001"
OTHER_ORG = "10000000-0000-4000-8000-000000000002"
USER = "20000000-0000-4000-8000-000000000001"
CONTACT = "50000000-0000-4000-8000-000000000001"
CONTACT2 = "50000000-0000-4000-8000-000000000002"
FOREIGN = "50000000-0000-4000-8000-000000000003"
ANONYMIZED = "50000000-0000-4000-8000-000000000004"
NOW = "2026-10-23T00:00:00+00:00"
MAX_REQUESTS = 512
MAX_BODY_BYTES = 65_536
MAX_RESPONSE_BYTES = 262_144
MAX_REPORT_BYTES = 1_048_576
CONTACT_PATH = "/api/v1/contacts"
MUTATION_DESCRIPTIONS = {
    "contact_get_ignores_tenant": "synthetic mutation：仅在隔离内存中将联系人按 ID 读取改为忽略组织范围",
    "duplicate_phone_guard_removed": "synthetic mutation：仅在隔离内存中令重复手机号查询为空，绕过创建前检查",
    "viewer_rank_elevated": "synthetic mutation：仅在隔离内存中将 viewer 权限等级提升到 agent",
    "sham_wrapper": "negative control：无语义变化的转发包装，必须保留所有测试通过",
}


def source_manifest(source: Path) -> dict[str, str]:
    """只散列 Python 源文件，绝不导出 CRM 源码、.env、数据或本机绝对路径。"""
    selected = sorted((source / "crm").rglob("*.py"))
    if not selected or len(selected) > 1024:
        raise ValueError("SOURCE_FILE_COUNT_INVALID")
    manifest, total = {}, 0
    for path in selected:
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("SOURCE_SYMLINK_REJECTED")
        size = path.stat().st_size
        total += size
        if size > 4_194_304 or total > 67_108_864:
            raise ValueError("SOURCE_SIZE_EXCEEDED")
        manifest[path.relative_to(source).as_posix()] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    for required in (
        "crm/api/crm.py",
        "crm/auth.py",
        "crm/schemas/domain.py",
        "crm/services/domain.py",
        "crm/repository.py",
    ):
        if required not in manifest:
            raise ValueError("CRM_SOURCE_CONTRACT_MISSING")
    return manifest


@contextmanager
def local_only():
    """ASGI 不需要外连；失败关闭意外网络调用，并拒绝常见 dotenv 文件读取。

    这是防止测试误触外部依赖的护栏，不是运行不可信代码的 OS 沙箱。
    入口启动前已由父进程清空业务/模型凭据环境，并关闭 Python 字节码写入。
    """
    original_open, original_io_open = builtins.open, io.open

    def checked_open(original, file, *args, **kwargs):
        if isinstance(file, (str, bytes, Path)):
            name = Path(file).name
            if name == ".env" or name.startswith(".env."):
                raise PermissionError("DOTENV_READ_BLOCKED")
        return original(file, *args, **kwargs)

    def blocked(*_args, **_kwargs):
        raise PermissionError("NETWORK_ACCESS_BLOCKED")

    with (
        patch.object(socket.socket, "connect", blocked),
        patch.object(socket.socket, "connect_ex", blocked),
        patch.object(socket, "create_connection", blocked),
        patch.object(socket, "getaddrinfo", blocked),
        patch.object(
            builtins,
            "open",
            lambda file, *args, **kwargs: checked_open(
                original_open, file, *args, **kwargs
            ),
        ),
        patch.object(
            io,
            "open",
            lambda file, *args, **kwargs: checked_open(
                original_io_open, file, *args, **kwargs
            ),
        ),
    ):
        yield


def fixture_tables() -> dict:
    def contact(row_id, org=ORG, **values):
        return {
            "id": row_id,
            "organization_id": org,
            "name": "合成测试联系人",
            "display_name": "合成测试联系人",
            "is_merged_into": None,
            "is_anonymized": False,
            "tags": [],
            "custom_fields": {},
            "consent": {},
            "created_at": NOW,
            "updated_at": NOW,
            "last_activity_at": NOW,
            **values,
        }

    return {
        "organizations": [
            {"id": ORG, "currency": "CNY", "settings": {}},
            {"id": OTHER_ORG, "currency": "CNY", "settings": {}},
        ],
        "contacts": [
            contact(CONTACT, phone_number="+8613800000000"),
            contact(CONTACT2),
            contact(FOREIGN, OTHER_ORG, name="合成外组织记录"),
            contact(ANONYMIZED, is_anonymized=True),
        ],
    }


def build_app():
    from crm.api.crm import router
    from crm.auth import Identity
    from crm.config import Settings
    from crm.responses import ApiError, fail
    from crm.security import BodyLimitMiddleware, SecurityMiddleware
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    # construct 不读取环境变量；全部外部地址/凭据保持默认空值，也不会启动 CRM lifespan。
    app.state.settings = Settings.model_construct(
        environment="test", repository_backend="memory"
    )
    app.state.cursor_key = b"synthetic-fixed-cursor-key-not-a-production-credential"

    def identity_resolver(request):
        actor = request.headers.get("x-quality-actor", "agent")
        if actor == "anonymous":
            return None
        actors = {
            "agent": (ORG, "agent"),
            "viewer": (ORG, "viewer"),
            "manager": (ORG, "manager"),
            "other": (OTHER_ORG, "agent"),
        }
        if actor not in actors:
            raise ValueError("SYNTHETIC_ACTOR_INVALID")
        org, role = actors[actor]
        return Identity(USER, org, role)

    app.state.identity_resolver = identity_resolver
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(SecurityMiddleware)

    @app.exception_handler(ApiError)
    async def api_error(_request, error):
        return fail(error.code, error.message, error.status, error.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, _error):
        # 不把输入值或 Pydantic 的原始异常详情写入测试产物。
        return fail("validation_failed", "Synthetic request failed validation", 422)

    app.include_router(router)
    return app


@contextmanager
def mutation(name: str):
    """缺陷只改变当前子进程的内存对象，退出后恢复；磁盘源码始终不可编辑。"""
    from crm.auth import ROLE_RANK
    from crm.services.domain import Contacts, Domain

    if name == "baseline":
        yield
    elif name == "contact_get_ignores_tenant":
        original = Domain.get

        async def unsafe_get(self, table, row_id, *, columns="*", filters=None):
            if table == "contacts":
                for row in self.repo.tables.get("contacts", []):
                    if row["id"] == str(row_id):
                        return copy.deepcopy(row)
            return await original(self, table, row_id, columns=columns, filters=filters)

        with patch.object(Domain, "get", unsafe_get):
            yield
    elif name == "duplicate_phone_guard_removed":
        original = Contacts.rows

        async def unsafe_rows(self, table, filters=None, **kwargs):
            if (
                table == "contacts"
                and isinstance(filters, dict)
                and "phone_number" in filters
            ):
                return []
            return await original(self, table, filters, **kwargs)

        with patch.object(Contacts, "rows", unsafe_rows):
            yield
    elif name == "viewer_rank_elevated":
        with patch.dict(ROLE_RANK, {"viewer": ROLE_RANK["agent"]}):
            yield
    elif name == "sham_wrapper":
        original = Domain.get

        async def forwarding(self, *args, **kwargs):
            return await original(self, *args, **kwargs)

        with patch.object(Domain, "get", forwarding):
            yield
    else:
        raise ValueError("MUTATION_UNKNOWN")


class CaseRun:
    def __init__(self, client, repo, counter):
        self.client, self.repo, self.counter = client, repo, counter
        self.checks, self.trace = [], []

    def expect(self, name, condition):
        self.checks.append({"assertion": name, "passed": bool(condition)})

    async def request(self, method, path, *, actor="agent", body=None, raw=None):
        self.counter[0] += 1
        if self.counter[0] > MAX_REQUESTS or len(path) > 4096:
            raise ValueError("REQUEST_BUDGET_EXCEEDED")
        encoded = canonical(body) if raw is None and body is not None else raw or b""
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError("SYNTHETIC_REQUEST_TOO_LARGE")
        response = await asyncio.wait_for(
            self.client.request(
                method,
                path,
                headers={"x-quality-actor": actor, "content-type": "application/json"},
                content=encoded,
            ),
            timeout=3,
        )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ValueError("RESPONSE_LIMIT_EXCEEDED")
        try:
            value = response.json()
        except ValueError:
            value = {}
        if not isinstance(value, dict):
            value = {}
        # 产物只保留请求结构/散列及响应摘要；即使是合成手机号，也不输出请求正文。
        # 最小复现由固定 case_id、夹具/工具版本和场景定义恢复，不依赖原始报文。
        public_path = (
            path.split("&cursor=", 1)[0] + "&cursor=$issued_cursor"
            if "&cursor=" in path
            else path
        )
        self.trace.append(
            {
                "method": method,
                "path": public_path,
                "actor": actor,
                "request_bytes": len(encoded),
                "request_sha256": hashlib.sha256(encoded).hexdigest(),
                "body_fields": sorted(body) if isinstance(body, dict) else None,
                "body_shape": "malformed_json"
                if raw is not None
                else type(body).__name__,
                "status": response.status_code,
                "error_code": (value.get("error") or {}).get("code"),
                "response_sha256": hashlib.sha256(response.content).hexdigest(),
            }
        )
        return response.status_code, value

    def status(self, actual, expected):
        self.expect(f"http_status_{expected}", actual == expected)


CREATE_CASES = {
    "create_minimum": ({"name": "合成创建"}, 201),
    "create_trimmed": ({"name": "  合成创建  ", "tags": [" VIP ", "vip", "NEW"]}, 201),
    "name_maximum": ({"name": "界" * 200}, 201),
    "phone_minimum": ({"phone_number": "+12345678"}, 201),
    "phone_maximum": ({"phone_number": "+123456789012345"}, 201),
    "tags_maximum": (
        {"name": "合成标签", "tags": [f"tag-{index}" for index in range(20)]},
        201,
    ),
    "custom_key_maximum": (
        {"name": "合成字段", "custom_fields": {"x" * 80: "synthetic"}},
        201,
    ),
    "birthdate_leap": ({"name": "合成闰日", "birthdate": "2000-02-29"}, 201),
    "body_tenant_rejected": ({"name": "合成越权", "organization_id": OTHER_ORG}, 422),
    "computed_field_rejected": ({"name": "合成越权", "is_anonymized": False}, 422),
    "name_empty": ({"name": ""}, 422),
    "name_too_long": ({"name": "界" * 201}, 422),
    "name_boolean": ({"name": True}, 422),
    "email_invalid": ({"email": "not-an-email"}, 422),
    "phone_too_short": ({"phone_number": "+1234567"}, 422),
    "phone_too_long": ({"phone_number": "+1234567890123456"}, 422),
    "birthdate_invalid": ({"birthdate": "2025-02-29"}, 422),
    "tags_too_many": ({"tags": [f"tag-{index}" for index in range(21)]}, 422),
    "tag_too_long": ({"tags": ["x" * 41]}, 422),
    "custom_key_too_long": ({"custom_fields": {"x" * 81: "synthetic"}}, 422),
    "custom_fields_null": ({"custom_fields": None}, 422),
    "unknown_field": ({"arbitrary_field": "synthetic"}, 422),
    "array_body": ([], 422),
    "duplicate_phone": ({"phone_number": "+8613800000000"}, 409),
}


async def execute_case(case_id, run):
    """每个场景使用全新夹具。需求断言手工固定，不接受模型提供 expected 值。"""
    before = digest(run.repo.tables["contacts"])
    before_count = len(run.repo.tables["contacts"])
    if case_id in CREATE_CASES:
        payload, expected = CREATE_CASES[case_id]
        status, value = await run.request("POST", CONTACT_PATH, body=payload)
        run.status(status, expected)
        data = value.get("data") or {}
        if expected == 201:
            run.expect(
                "one_contact_inserted",
                len(run.repo.tables["contacts"]) == before_count + 1,
            )
            run.expect(
                "tenant_derived_from_identity", data.get("organization_id") == ORG
            )
            run.expect(
                "created_audit",
                any(
                    row.get("action") == "contact.created"
                    for row in run.repo.tables.get("api_audit_log", [])
                ),
            )
            run.expect(
                "created_event",
                any(
                    row.get("event_type") == "contact.created"
                    for row in run.repo.tables.get("event_log", [])
                ),
            )
            if case_id == "create_trimmed":
                run.expect(
                    "trim_and_tag_normalization",
                    data.get("name") == "合成创建"
                    and data.get("tags") == ["vip", "new"],
                )
            if "name" in payload:
                run.expect(
                    "name_value_preserved", data.get("name") == payload["name"].strip()
                )
        else:
            run.expect(
                "business_state_unchanged",
                digest(run.repo.tables["contacts"]) == before,
            )
            run.expect("no_business_events", not run.repo.tables.get("event_log"))
    elif case_id in {
        "anonymous_read",
        "anonymous_write",
        "viewer_write",
        "viewer_patch",
        "viewer_delete",
    }:
        method = {
            "anonymous_read": "GET",
            "anonymous_write": "POST",
            "viewer_write": "POST",
            "viewer_patch": "PATCH",
            "viewer_delete": "DELETE",
        }[case_id]
        path = CONTACT_PATH + (f"/{CONTACT}" if method in {"PATCH", "DELETE"} else "")
        actor = "anonymous" if case_id.startswith("anonymous") else "viewer"
        status, _ = await run.request(
            method,
            path,
            actor=actor,
            body={"name": "合成不允许写入"} if method in {"POST", "PATCH"} else None,
        )
        run.status(status, 401 if actor == "anonymous" else 403)
        run.expect(
            "business_state_unchanged", digest(run.repo.tables["contacts"]) == before
        )
    elif case_id in {"cross_tenant_get", "cross_tenant_patch"}:
        method = "GET" if case_id.endswith("get") else "PATCH"
        status, _ = await run.request(
            method,
            f"{CONTACT_PATH}/{FOREIGN}",
            body={"name": "合成越权修改"} if method == "PATCH" else None,
        )
        run.status(status, 404)
        run.expect(
            "foreign_record_unchanged", digest(run.repo.tables["contacts"]) == before
        )
    elif case_id == "list_tenant_isolation":
        status, value = await run.request("GET", CONTACT_PATH)
        run.status(status, 200)
        rows = value.get("data") or []
        run.expect("own_records_present", len(rows) == 3)
        run.expect(
            "no_foreign_contact",
            all(
                row.get("organization_id") == ORG and row.get("id") != FOREIGN
                for row in rows
            ),
        )
    elif case_id in {
        "malformed_json",
        "invalid_uuid",
        "unsupported_method",
        "empty_patch",
        "anonymized_patch",
    }:
        method, path, body, raw, expected = {
            "malformed_json": ("POST", CONTACT_PATH, None, b'{"name":', 422),
            "invalid_uuid": ("GET", CONTACT_PATH + "/not-a-uuid", None, None, 422),
            "unsupported_method": ("PUT", CONTACT_PATH, {}, None, 405),
            "empty_patch": ("PATCH", f"{CONTACT_PATH}/{CONTACT}", {}, None, 422),
            "anonymized_patch": (
                "PATCH",
                f"{CONTACT_PATH}/{ANONYMIZED}",
                {"name": "合成恢复尝试"},
                None,
                403,
            ),
        }[case_id]
        status, _ = await run.request(method, path, body=body, raw=raw)
        run.status(status, expected)
        run.expect(
            "business_state_unchanged", digest(run.repo.tables["contacts"]) == before
        )
    elif case_id == "create_patch_read":
        status, created = await run.request(
            "POST",
            CONTACT_PATH,
            body={"name": "合成序列", "custom_fields": {"keep": "original"}},
        )
        run.status(status, 201)
        row_id = (created.get("data") or {}).get("id", CONTACT)
        path = f"{CONTACT_PATH}/{row_id}"
        status, initial = await run.request("GET", path)
        run.status(status, 200)
        run.expect(
            "created_name_visible",
            (initial.get("data") or {}).get("name") == "合成序列",
        )
        status, _ = await run.request(
            "PATCH",
            path,
            body={"name": "合成已修改", "custom_fields": {"added": "value"}},
        )
        run.status(status, 200)
        status, final = await run.request("GET", path)
        run.status(status, 200)
        data = final.get("data") or {}
        run.expect("patched_name_visible", data.get("name") == "合成已修改")
        run.expect(
            "custom_fields_merge_preserves_previous",
            data.get("custom_fields") == {"keep": "original", "added": "value"},
        )
        run.expect(
            "sequence_events",
            [row.get("event_type") for row in run.repo.tables.get("event_log", [])]
            == ["contact.created", "contact.updated"],
        )
        run.expect(
            "sequence_audit",
            [row.get("action") for row in run.repo.tables.get("api_audit_log", [])]
            == ["contact.created", "contact.updated"],
        )
    elif case_id in {"pagination_disjoint", "cursor_tenant_binding", "cursor_tampered"}:
        status, page1 = await run.request(
            "GET", CONTACT_PATH + "?limit=1&order_by=created_at&order_dir=asc"
        )
        run.status(status, 200)
        cursor = (page1.get("meta") or {}).get("cursor")
        run.expect("cursor_issued", isinstance(cursor, str) and bool(cursor))
        if not cursor:
            return
        if case_id == "cursor_tampered":
            cursor = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
        actor = "other" if case_id == "cursor_tenant_binding" else "agent"
        status, page2 = await run.request(
            "GET",
            CONTACT_PATH
            + "?limit=1&order_by=created_at&order_dir=asc&cursor="
            + cursor,
            actor=actor,
        )
        run.status(status, 200 if case_id == "pagination_disjoint" else 400)
        if case_id == "pagination_disjoint":
            first, second = page1.get("data") or [], page2.get("data") or []
            run.expect("pages_exactly_one", len(first) == 1 and len(second) == 1)
            run.expect(
                "pages_do_not_overlap",
                bool(first and second) and first[0].get("id") != second[0].get("id"),
            )
    else:
        query, expected = {
            "limit_minimum": ("limit=1", 200),
            "limit_maximum": ("limit=100", 200),
            "limit_zero": ("limit=0", 422),
            "limit_overflow": ("limit=101", 422),
            "search_maximum": ("search=" + "x" * 200, 200),
            "search_overflow": ("search=" + "x" * 201, 422),
        }[case_id]
        status, value = await run.request("GET", CONTACT_PATH + "?" + query)
        run.status(status, expected)
        if case_id == "limit_minimum":
            run.expect("page_bound", len(value.get("data") or []) <= 1)


async def run_suite(app, case_ids, mutation_name, counter):
    import httpx
    from crm.repository import MemoryRepository

    results = []
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=True)
    with mutation(mutation_name):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://crm-quality.invalid",
            timeout=3,
            trust_env=False,
        ) as client:
            for case_id in case_ids:
                repo = MemoryRepository(fixture_tables())
                app.state.repo = repo
                run = CaseRun(client, repo, counter)
                try:
                    await execute_case(case_id, run)
                except Exception as error:  # noqa: BLE001 -- 保留任何真实应用失败并脱敏，不将异常误计为有效杀死。
                    # 异常消息可能含输入或地址；产物只保留固定错误类别。
                    run.expect("request_completed_without_exception", False)
                    exception_type = type(error).__name__
                else:
                    exception_type = None
                results.append(
                    {
                        "case_id": case_id,
                        "category": CASE_INDEX[case_id][0],
                        "passed": bool(run.checks)
                        and all(item["passed"] for item in run.checks),
                        "checks": run.checks,
                        "exception_type": exception_type,
                        "requests": run.trace,
                    }
                )
    return {
        "name": mutation_name,
        "synthetic_mutation": mutation_name in MUTANTS,
        "negative_control": mutation_name == "sham_wrapper",
        "description": MUTATION_DESCRIPTIONS.get(mutation_name, "未改变真实 CRM 逻辑"),
        "case_count": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "results": results,
    }


async def main_async(config):
    source = Path(config["source"]).resolve()
    before = source_manifest(source)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    logging.disable(logging.CRITICAL)
    counter = [0]
    with local_only():
        app = build_app()
        suites = []
        for name in ("baseline", "sham_wrapper", *MUTANTS):
            suites.append(await run_suite(app, config["case_ids"], name, counter))
        loaded = sorted(
            {
                Path(module.__file__).resolve().relative_to(source).as_posix()
                for name, module in sys.modules.copy().items()
                if (name == "crm" or name.startswith("crm."))
                and getattr(module, "__file__", None)
                and Path(module.__file__).resolve().is_relative_to(source)
            }
        )
    after = source_manifest(source)
    baseline, sham, *mutants = suites
    baseline_ok = baseline["failed"] == 0
    sham_ok = sham["failed"] == 0
    mutations_killed = all(suite["failed"] > 0 for suite in mutants)
    kills = []
    for suite in mutants:
        failures = [item for item in suite["results"] if not item["passed"]]
        baseline_ids = {
            item["case_id"] for item in baseline["results"] if item["passed"]
        }
        valid = [
            item
            for item in failures
            if item["case_id"] in baseline_ids and not item["exception_type"]
        ]
        first = min(valid, key=lambda item: len(item["requests"])) if valid else None
        kills.append(
            {
                "mutation": suite["name"],
                "killed_by_fixed_oracle": bool(valid),
                "minimal_reproduction": first,
                "minimality": "fewest requests among executed fixed scenarios; not a global minimization proof",
            }
        )
    packages = {}
    for package in ("fastapi", "httpx", "pydantic", "starlette"):
        packages[package] = importlib.metadata.version(package)
    return {
        "schema_version": 1,
        "project": "user-provided-python-crm-contacts",
        "fixture_origin": "explicitly synthetic identity, tenant, contacts and requests; actual CRM code imported read-only",
        "execution": "real FastAPI ASGI routes + actual schemas/auth/services + MemoryRepository; no TCP/TLS or PostgreSQL",
        "source": {
            "revision_sha256": digest(before),
            "file_count": len(before),
            "files": before,
            "loaded_source_files": loaded,
            "unchanged_after_run": before == after,
            "business_source_redistributed": False,
        },
        "environment": {
            "python": sys.version.split()[0],
            "packages": packages,
            "external_network_blocked": True,
            "dotenv_reads_blocked": True,
            "bytecode_writes_disabled": True,
            "synthetic_identity_resolver": True,
            "production_auth_provider_tested": False,
            "production_database_tested": False,
            "os_sandbox_claimed": False,
        },
        "budgets": {
            "maximum_requests": MAX_REQUESTS,
            "actual_requests": counter[0],
            "request_timeout_seconds": 3,
            "maximum_request_bytes": MAX_BODY_BYTES,
            "maximum_response_bytes": MAX_RESPONSE_BYTES,
        },
        "summary": {
            "passed": baseline_ok
            and sham_ok
            and mutations_killed
            and all(item["killed_by_fixed_oracle"] for item in kills)
            and before == after,
            "baseline_passed": baseline["passed"],
            "baseline_total": baseline["case_count"],
            "negative_control_passed": sham_ok,
            "synthetic_mutants_total": len(mutants),
            "synthetic_mutants_killed": sum(
                item["killed_by_fixed_oracle"] for item in kills
            ),
        },
        "suites": suites,
        "mutation_kills": kills,
        "limitations": [
            "只覆盖联系人资源的审阅场景，不代表全部 CRM API",
            "身份提供器为合成夹具，未验证 GoTrue 登录",
            "使用真实 MemoryRepository，不代表 PostgreSQL/RLS、真实网络或数据库并发",
            "人为植入错误明确标记 synthetic mutation，不是实际 CRM 漏洞发现",
            "外部结构化计划本身不证明调用过模型，需独立模型回执",
        ],
    }


def main():
    config_path, result_path = map(Path, sys.argv[1:3])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    try:
        # 先创建事件循环，再禁止 socket.connect；Windows 循环自身会创建内部唤醒管道。
        report = asyncio.run(main_async(config))
    except Exception as error:  # noqa: BLE001 -- 最外层输出有界失败回执，禁止泄漏原异常中的输入/凭据。
        report = {
            "schema_version": 1,
            "summary": {"passed": False},
            "worker_error": type(error).__name__,
        }
        if (
            isinstance(error, ModuleNotFoundError)
            and isinstance(error.name, str)
            and error.name.replace("_", "").replace(".", "").isalnum()
        ):
            report["missing_dependency"] = error.name
    raw = canonical(report)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("REPORT_LIMIT_EXCEEDED")
    result_path.write_bytes(raw)
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
