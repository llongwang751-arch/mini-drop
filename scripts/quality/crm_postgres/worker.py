"""真实CRM → 本地issuer HTTP → PostgREST → 原始PostgreSQL/RLS验收。

管理员连接仅安装迁移、构造/复位合成夹具及只读快照；应用连接用真实NOINHERIT/
NOSUPERUSER/NOBYPASSRLS角色。普通CRM请求不注入Identity，也不修改原仓库。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import logging
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "business"))
from case_dsl import load_suite, requirement_sha256
from contract import digest
from gateway import gateway
from independent_oracle import Oracle
from local_issuer import issue, verify
from run_generated import execute_generated_case
from worker import (
    CONTACT,
    CONTACT_PATH,
    FOREIGN,
    ORG,
    OTHER_ORG,
    USER,
    build_app,
    fixture_tables,
    source_manifest,
)

USERS = {
    "agent": USER,
    "other": "20000000-0000-4000-8000-000000000002",
    "viewer": "20000000-0000-4000-8000-000000000003",
    "manager": "20000000-0000-4000-8000-000000000004",
}
APP_ROLE = "drop_crm_authenticator"
SNAPSHOT_TABLES = ("contacts", "event_log", "job_queue", "crm_lead_activities")


def validate_config(value):
    expected = {
        "schema_version",
        "disposable_only",
        "database_host",
        "database_port",
        "database_name",
        "admin_password",
        "authenticator_password",
        "jwt_secret",
        "rest_origin",
    }
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value["schema_version"] != 1
        or value["disposable_only"] is not True
    ):
        raise ValueError("CRM_PG_CONFIG_FIELDS")
    if (
        value["database_host"] not in {"db", "127.0.0.1", "localhost"}
        or type(value["database_port"]) is not int
        or not 1024 <= value["database_port"] <= 65535
        or value["database_name"] != "crm_quality"
    ):
        raise ValueError("CRM_PG_NOT_DISPOSABLE_TARGET")
    for key in ("admin_password", "authenticator_password", "jwt_secret"):
        if (
            not isinstance(value[key], str)
            or not 32 <= len(value[key]) <= 128
            or not value[key].isascii()
            or any(not char.isalnum() and char not in "-_" for char in value[key])
        ):
            raise ValueError("CRM_PG_CONFIG_SECRET_BOUND")
    parsed = urlsplit(value["rest_origin"])
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"rest", "127.0.0.1", "localhost"}
        or parsed.port not in {None, 3000}
        or parsed.path not in {"", "/"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("CRM_PG_REST_TARGET")
    return value


async def install(admin, source, config):
    hashes = {}
    for relative in ("tests/supabase-prelude.sql", "supabase/baseline.sql"):
        path = source / relative
        with path.open("rb") as incoming:
            raw = incoming.read(8_388_609)
        if len(raw) > 8_388_608:
            raise ValueError("CRM_SQL_SOURCE_BOUND")
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        await admin.execute(raw.decode("utf-8"), timeout=180)
    await admin.execute("SET search_path=public,extensions")
    # DDL不支持PASSWORD绑定参数：仅由数据库format(%I/%L)编码可信固定角色与临时密码。
    statement = await admin.fetchval(
        "SELECT format('CREATE ROLE %I LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L', $1::text, $2::text)",
        APP_ROLE,
        config["authenticator_password"],
    )
    await admin.execute(statement)
    await admin.execute(
        "GRANT anon, authenticated, service_role TO drop_crm_authenticator"
    )
    # 普通authenticated必须保持无superuser/bypass属性，不能因fixture把RLS静默绕过。
    flags = await admin.fetchrow(
        "SELECT rolsuper,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=$1",
        APP_ROLE,
    )
    if not flags or flags["rolsuper"] or flags["rolbypassrls"] or flags["rolinherit"]:
        raise ValueError("CRM_AUTHENTICATOR_PRIVILEGE_INVALID")
    return hashes


async def seed(admin):
    for actor, user in USERS.items():
        await admin.execute(
            "INSERT INTO auth.users(id,email) VALUES($1,$2)",
            UUID(user),
            f"qa-{actor}@example.invalid",
        )
    for org, suffix in ((ORG, "a"), (OTHER_ORG, "b")):
        await admin.execute(
            "INSERT INTO public.organizations(id,slug,legal_name,display_name) VALUES($1,$2,$3,$3)",
            UUID(org),
            "drop-quality-" + suffix,
            "Synthetic quality " + suffix,
        )
    sessions = {}
    for actor, user in USERS.items():
        org = OTHER_ORG if actor == "other" else ORG
        role = "agent" if actor in {"agent", "other"} else actor
        await admin.execute(
            "INSERT INTO public.user_organizations(user_id,organization_id,role,accepted_at) VALUES($1,$2,$3,now())",
            UUID(user),
            UUID(org),
            role,
        )
        session = str(uuid4())
        sessions[user] = {"session_id": session}
        await admin.execute(
            "INSERT INTO auth.sessions(id,user_id,aal,not_after) VALUES($1,$2,'aal1',now()+interval '1 hour')",
            UUID(session),
            UUID(user),
        )
    await reset_contacts(admin)
    return sessions


async def reset_contacts(admin):
    # 仅当前新建、已验证名称的可销毁数据库；不接受任意DSN或生产库。
    await admin.execute("TRUNCATE public.contacts CASCADE")
    for row in fixture_tables()["contacts"]:
        await admin.execute(
            "INSERT INTO public.contacts(id,organization_id,name,display_name,phone_number,is_anonymized,anonymized_at,tags,custom_fields,consent) VALUES($1,$2,$3,$4,$5,$6,CASE WHEN $6 THEN now() ELSE NULL END,$7,$8::jsonb,$9::jsonb)",
            UUID(row["id"]),
            UUID(row["organization_id"]),
            row["name"],
            row["display_name"],
            row.get("phone_number"),
            row["is_anonymized"],
            row["tags"],
            json.dumps(row["custom_fields"]),
            json.dumps(row["consent"]),
        )


async def snapshot(admin, tables=SNAPSHOT_TABLES):
    values = {}
    for table in tables:
        # 表名来自程序常量白名单；所有业务值使用参数绑定。
        raw = await admin.fetchval(
            "SELECT COALESCE(jsonb_agg(x ORDER BY x::text),'[]'::jsonb)::text FROM (SELECT to_jsonb(t) AS x FROM public."
            + table
            + " AS t) rows"
        )
        values[table] = hashlib.sha256(raw.encode()).hexdigest()
    return digest(values)


async def as_user(pool, claims, function):
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("SET LOCAL ROLE authenticated")
        await connection.execute("SET LOCAL row_security=on")
        await connection.fetchval(
            "SELECT set_config('request.jwt.claims',$1,true)", json.dumps(claims)
        )
        return await function(connection)


async def database_checks(admin, app_pool, claims):
    import asyncpg

    results = []

    def record(name, passed, **data):
        results.append({"name": name, "passed": bool(passed), **data})

    async def role_flags(connection):
        return await connection.fetchrow(
            "SELECT current_user AS name,rolsuper,rolbypassrls,current_setting('row_security') AS row_security FROM pg_roles WHERE rolname=current_user"
        )

    flags = await as_user(app_pool, claims["agent"], role_flags)
    record(
        "application_role_is_real_authenticated_non_superuser_non_bypass",
        flags["name"] == "authenticated"
        and not flags["rolsuper"]
        and not flags["rolbypassrls"]
        and flags["row_security"] == "on",
    )

    async def visible(connection):
        return {
            str(row["id"])
            for row in await connection.fetch("SELECT id FROM public.contacts")
        }

    current = await as_user(app_pool, claims["agent"], visible)
    expected = {
        row["id"]
        for row in fixture_tables()["contacts"]
        if row["organization_id"] == ORG
    }
    record("unfiltered_select_enforces_original_rls", current == expected)
    before = await snapshot(admin)

    async def cross_insert(connection):
        await connection.execute(
            "INSERT INTO public.contacts(organization_id,name) VALUES($1,$2)",
            UUID(OTHER_ORG),
            "Synthetic forbidden",
        )

    code = None
    try:
        await as_user(app_pool, claims["agent"], cross_insert)
    except asyncpg.PostgresError as error:
        code = error.sqlstate
    record(
        "rls_cross_tenant_insert_denied_without_business_effect",
        code == "42501" and before == await snapshot(admin),
        sqlstate=code,
    )

    class PlannedRollback(Exception):
        pass

    before = await snapshot(admin)
    try:
        async with app_pool.acquire() as connection, connection.transaction():
            await connection.execute("SET LOCAL ROLE authenticated")
            await connection.execute("SET LOCAL row_security=on")
            await connection.fetchval(
                "SELECT set_config('request.jwt.claims',$1,true)",
                json.dumps(claims["agent"]),
            )
            await connection.execute(
                "INSERT INTO public.contacts(organization_id,name) VALUES($1,$2)",
                UUID(ORG),
                "Synthetic rollback",
            )
            await connection.execute(
                "UPDATE public.contacts SET name=$1 WHERE id=$2",
                "Synthetic rollback update",
                UUID(CONTACT),
            )
            raise PlannedRollback()
    except PlannedRollback:
        pass
    record(
        "real_transaction_rolls_back_insert_update_and_business_side_effects",
        before == await snapshot(admin),
    )

    barrier = asyncio.Barrier(2)
    phone = "+199923000001"

    async def compete(index):
        async def insert(connection):
            await barrier.wait()
            await connection.execute(
                "INSERT INTO public.contacts(organization_id,name,phone_number) VALUES($1,$2,$3)",
                UUID(ORG),
                f"Synthetic concurrent {index}",
                phone,
            )

        try:
            await as_user(app_pool, claims["agent"], insert)
            return "committed"
        except asyncpg.PostgresError as error:
            return error.sqlstate

    outcomes = await asyncio.wait_for(
        asyncio.gather(compete(1), compete(2)), timeout=20
    )
    count = await admin.fetchval(
        "SELECT count(*) FROM public.contacts WHERE organization_id=$1 AND phone_number=$2",
        UUID(ORG),
        phone,
    )
    record(
        "original_unique_index_arbitrates_concurrent_writers",
        sorted(outcomes) == ["23505", "committed"] and count == 1,
        outcomes=outcomes,
        resulting_rows=count,
    )

    async def other_same_phone(connection):
        await connection.execute(
            "INSERT INTO public.contacts(organization_id,name,phone_number) VALUES($1,$2,$3)",
            UUID(OTHER_ORG),
            "Synthetic other tenant",
            phone,
        )

    await as_user(app_pool, claims["other"], other_same_phone)
    count = await admin.fetchval(
        "SELECT count(*) FROM public.contacts WHERE phone_number=$1", phone
    )
    record(
        "positive_control_same_phone_in_different_tenants",
        count == 2,
        resulting_rows=count,
    )

    # 只在此可销毁库短暂移除RLS，证明未过滤查询的断言能够捕获实际越权，随后必定恢复。
    try:
        await admin.execute("ALTER TABLE public.contacts DISABLE ROW LEVEL SECURITY")
        exposed = await as_user(app_pool, claims["agent"], visible)
        killed = FOREIGN in exposed and FOREIGN not in current
    finally:
        await admin.execute("ALTER TABLE public.contacts ENABLE ROW LEVEL SECURITY")
    restored = await admin.fetchval(
        "SELECT relrowsecurity FROM pg_class WHERE oid='public.contacts'::regclass"
    )
    record(
        "synthetic_rls_disabled_mutation_killed_and_restored",
        killed and restored,
        synthetic_mutation=True,
        root_cause_is_production_vulnerability=False,
    )
    await reset_contacts(admin)
    return results


class ActorClient:
    def __init__(self, client, tokens):
        self.client, self.tokens = client, tokens

    async def request(self, method, path, *, headers, content):
        headers = dict(headers)
        actor = headers.pop("x-quality-actor", "agent")
        if actor != "anonymous":
            headers["Authorization"] = "Bearer " + self.tokens[actor]
        return await self.client.request(method, path, headers=headers, content=content)


async def run(config, source, suite, bootstrap_ready=None):
    import asyncpg
    import httpx
    from pydantic import SecretStr

    before_source = source_manifest(source)
    sys.path.insert(0, str(source))
    from crm.config import Settings
    from crm.repository import SupabaseRepository

    logging.disable(logging.CRITICAL)
    admin = await asyncpg.connect(
        host=config["database_host"],
        port=config["database_port"],
        database=config["database_name"],
        user="postgres",
        password=config["admin_password"],
        command_timeout=20,
    )
    app_pool = None
    results = {
        "schema_version": 2,
        "kind": "actual_crm_postgresql_rls_local_issuer_acceptance",
        "passed": False,
        "authentication_scope": "local HS256 issuer over real HTTP plus real CRM auth resolution and PostgREST JWT verification; not GoTrue or external issuer",
        "full_external_authentication_claimed": False,
        "fixture_origin": "all users, sessions, tenants, contacts, JWT keys and DB credentials are generated synthetic fixtures",
    }
    try:
        results["stage"] = "install_original_sql"
        sql_hashes = await install(admin, source, config)
        results["stage"] = "seed_disposable_fixtures"
        users = await seed(admin)
        secret = config["jwt_secret"]
        tokens = {
            actor: issue(
                secret, "authenticated", user=user, session=users[user]["session_id"]
            )
            for actor, user in USERS.items()
        }
        claims = {
            actor: verify(token, secret, users=users)[0]
            for actor, token in tokens.items()
        }
        app_pool = await asyncpg.create_pool(
            host=config["database_host"],
            port=config["database_port"],
            database=config["database_name"],
            user=APP_ROLE,
            password=config["authenticator_password"],
            min_size=1,
            max_size=4,
            command_timeout=20,
        )
        results["stage"] = "application_role_sql_checks"
        checks = await database_checks(admin, app_pool, claims)
        results["stage"] = "wait_original_postgrest"
        if bootstrap_ready is not None:
            # 原SQL/应用角色已实际准备后通知host；避免PostgREST因不存在的登录角色永久退出。
            with bootstrap_ready.open("x", encoding="utf-8") as ready:
                json.dump({"schema_version": 1, "bootstrap_ready": True}, ready)
        await admin.execute("NOTIFY pgrst, 'reload schema'")
        async with httpx.AsyncClient(
            timeout=2, trust_env=False, follow_redirects=False
        ) as waiting:
            for _attempt in range(60):
                try:
                    async with waiting.stream(
                        "GET", config["rest_origin"] + "/"
                    ) as response:
                        if response.status_code == 200:
                            break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            else:
                raise ValueError("DISPOSABLE_POSTGREST_NOT_READY")
        results["stage"] = "real_crm_http_auth_and_generated_cases"
        counters = {
            "gateway_requests": 0,
            "local_issuer_checks": 0,
            "local_issuer_rejections": 0,
        }
        with gateway(config["rest_origin"], secret, users, counters) as origin:
            settings = Settings.model_construct(
                environment="test",
                repository_backend="supabase",
                supabase_url=origin,
                supabase_anon_key=SecretStr(issue(secret, "anon")),
                supabase_service_role_key=SecretStr(issue(secret, "service_role")),
            )
            repo_client = httpx.AsyncClient(
                timeout=10, trust_env=False, follow_redirects=False
            )
            repo = SupabaseRepository(settings, repo_client)
            app = build_app()
            app.state.settings, app.state.repo, app.state.identity_resolver = (
                settings,
                repo,
                None,
            )
            app.state.team_pool, app.state.team_pool_owned = app_pool, False
            if app.state.identity_resolver is not None:
                raise ValueError("SYNTHETIC_IDENTITY_OVERRIDE_NOT_ALLOWED")
            from crm.auth import require_role
            from crm.responses import ok
            from crm.services.domain_mutation import config_transaction
            from fastapi import Depends, Request

            class PlannedServiceRollback(Exception):
                pass

            manager_dependency = Depends(require_role("manager"))

            async def controlled_rollback(request, identity=manager_dependency):
                # 这是明确的测试夹具路由：身份依赖和ConfigTransaction均为原CRM实现。
                # 不把此端点添加到CRM源码或生产服务，只注入预定失败验证真实服务事务回滚。
                try:
                    async with config_transaction(
                        request, identity, resource="quality-fault-injection"
                    ) as transaction:
                        created = await transaction.create_pipeline(
                            {
                                "name": "Synthetic rollback",
                                "description": None,
                                "slug": "qa-rollback-" + uuid4().hex[:8],
                                "position": 1000,
                                "is_default": False,
                            }
                        )
                        await transaction.create_stage(
                            created["id"], "Synthetic stage", "qa-stage", 1000, "user"
                        )
                        raise PlannedServiceRollback()
                except PlannedServiceRollback:
                    return ok({"planned_failure_injected": True})

            controlled_rollback.__annotations__["request"] = Request
            app.post("/quality-fixture/config-rollback")(controlled_rollback)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://crm-quality.invalid",
                trust_env=False,
            ) as client:
                client.cookies.clear()
                adapter = ActorClient(client, tokens)
                transaction_before = await snapshot(
                    admin, ("crm_pipelines", "crm_stages")
                )
                response = await client.post(
                    "/quality-fixture/config-rollback",
                    headers={"Authorization": "Bearer " + tokens["manager"]},
                    json={},
                )
                checks.append(
                    {
                        "name": "original_config_transaction_rolls_back_pipeline_and_stage_after_injected_failure",
                        "passed": response.status_code == 200
                        and transaction_before
                        == await snapshot(admin, ("crm_pipelines", "crm_stages")),
                        "synthetic_failure_injection": True,
                        "fixture_route_not_a_production_endpoint": True,
                    }
                )
                case_results, counter = [], [0]
                for case in suite["cases"]:
                    await reset_contacts(admin)
                    actual = await execute_generated_case(
                        adapter,
                        None,
                        case["steps"],
                        counter,
                        oracle=Oracle(database_constraints=True),
                        state_snapshot=lambda: snapshot(admin),
                    )
                    case_results.append(
                        {"id": case["id"], "case_sha256": digest(case), **actual}
                    )
                await reset_contacts(admin)
                # 普通HTTP认证链的否定对照：错签名、过期和无会话；不得修改业务表。
                bad_tokens = {
                    "missing": None,
                    "wrong_signature": issue(
                        "x" * 48,
                        "authenticated",
                        user=USER,
                        session=users[USER]["session_id"],
                    ),
                    "expired": issue(
                        secret,
                        "authenticated",
                        user=USER,
                        session=users[USER]["session_id"],
                        expires_in=-1,
                    ),
                }
                auth_results = []
                for name, token in bad_tokens.items():
                    state = await snapshot(admin)
                    response = await client.get(
                        CONTACT_PATH,
                        headers={"Authorization": "Bearer " + token} if token else {},
                    )
                    auth_results.append(
                        {
                            "name": name,
                            "status": response.status_code,
                            "passed": response.status_code == 401
                            and state == await snapshot(admin),
                            "business_state_unchanged": state == await snapshot(admin),
                        }
                    )
                state = await snapshot(admin)
                await admin.execute(
                    "UPDATE public.user_organizations SET revoked_at=now() WHERE user_id=$1",
                    UUID(USER),
                )
                response = await client.get(
                    CONTACT_PATH, headers={"Authorization": "Bearer " + tokens["agent"]}
                )
                auth_results.append(
                    {
                        "name": "live_membership_revocation",
                        "status": response.status_code,
                        "passed": response.status_code == 403
                        and state == await snapshot(admin),
                    }
                )
                await admin.execute(
                    "UPDATE public.user_organizations SET revoked_at=NULL WHERE user_id=$1",
                    UUID(USER),
                )
                await repo.close()
        results.update(
            passed=all(item["passed"] for item in checks)
            and all(item["passed"] for item in case_results)
            and all(item["passed"] for item in auth_results),
            database_checks=checks,
            generated_cases=case_results,
            authentication_checks=auth_results,
            adapter_counters=counters,
            actual_generated_asgi_requests=counter[0],
            generated_suite_sha256=digest(suite),
            reviewed_requirements_sha256=requirement_sha256(),
            source_manifest_sha256=digest(before_source),
            source_unchanged=before_source == source_manifest(source),
            original_sql_sources=sql_hashes,
            postgres_version=await admin.fetchval("SHOW server_version"),
            packages={
                name: importlib.metadata.version(name)
                for name in ("fastapi", "pydantic", "httpx", "asyncpg")
            },
            limits=[
                "SQL baseline is original user-supplied CRM; auth/storage prelude remains explicit compatibility fixture, not real Supabase provisioning",
                "Local JWT issuer is not an external GoTrue service, external session recovery or full MFA/SSO acceptance",
                "Security audit entries may be appended on denied authorization; contacts/event/job/activity hashes must remain unchanged",
                "Disabling RLS is an explicitly synthetic disposable mutation restored in finally, not a newly found real vulnerability",
            ],
        )
        results["passed"] = results["passed"] and results["source_unchanged"]
        results["stage"] = "completed"
    except Exception as error:  # noqa: BLE001 -- 不输出SQL/DSN/请求原文或密钥。
        results.update(
            error_type=type(error).__name__, error_code="CRM_PG_ACCEPTANCE_FAILED"
        )
        if isinstance(error, asyncpg.PostgresError):
            results["sqlstate"] = error.sqlstate
    finally:
        if app_pool is not None:
            await app_pool.close()
        await admin.close()
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--crm-source", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-ready", type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.config.stat().st_size > 16_384:
        parser.error("输出必须新建，配置最多16KiB")
    if args.bootstrap_ready is not None and (
        args.bootstrap_ready.exists()
        or args.bootstrap_ready.parent.resolve() != args.output.parent.resolve()
    ):
        parser.error("bootstrap标志必须在本轮新输出目录，且不可覆盖")
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    suite = load_suite(args.suite)
    sys.dont_write_bytecode = True
    value = asyncio.run(
        run(config, args.crm_source.resolve(), suite, args.bootstrap_ready)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n")
    print(
        json.dumps(
            {
                "passed": value["passed"],
                "error_type": value.get("error_type"),
                "sqlstate": value.get("sqlstate"),
            }
        )
    )
    return 0 if value["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
