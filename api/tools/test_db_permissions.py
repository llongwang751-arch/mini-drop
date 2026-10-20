"""使用隔离的真实 PostgreSQL 容器核验数据库最小权限，不连接现有业务库。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import secrets
import subprocess
import tempfile
import time
import uuid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="postgres:17.11-alpine")
    parser.add_argument("--permissions", required=True)
    parser.add_argument("--migration", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    name = "drop-permissions-test-" + uuid.uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    checks: list[str] = []

    def docker(*argv: str, data: str | None = None, env: dict | None = None,
               allow_error: bool = False) -> subprocess.CompletedProcess:
        result = subprocess.run(["docker", *argv], input=data, text=True,
                                capture_output=True, env=env, timeout=60)
        if password in result.stdout or password in result.stderr:
            raise AssertionError("A database secret appeared in process output")
        if result.returncode and not allow_error:
            # 不输出任意数据库响应或进程参数。
            raise AssertionError("An isolated database test command failed")
        return result

    def admin(sql: str, allow_error: bool = False) -> subprocess.CompletedProcess:
        return docker("exec", "-i", "-u", "postgres", name, "psql", "-X", "-q", "-At",
                      "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
                      data=sql, allow_error=allow_error)

    def app(sql: str, secret: str | None = None,
            allow_error: bool = False) -> subprocess.CompletedProcess:
        env = dict(os.environ, PGPASSWORD=password if secret is None else secret)
        return docker("exec", "-i", "-e", "PGPASSWORD", name, "psql", "-X", "-q", "-At",
                      "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-U", "drop_app",
                      "-d", "postgres", data=sql, env=env, allow_error=allow_error)

    def denied(sql: str, label: str) -> None:
        result = app(sql, allow_error=True)
        if result.returncode == 0 or "permission denied" not in result.stderr:
            raise AssertionError(label + " was not denied by PostgreSQL")
        checks.append(label)

    with tempfile.TemporaryDirectory(prefix="drop-db-permissions-") as folder:
        secret_dir = pathlib.Path(folder)
        secret_dir.chmod(0o700)
        bootstrap = secret_dir / "bootstrap_password"
        bootstrap.write_text(secrets.token_urlsafe(32))
        bootstrap.chmod(0o444)
        app_secret = secret_dir / "app_database_password"
        app_secret.write_text(password + "\n")
        app_secret.chmod(0o444)
        started = False
        try:
            docker("run", "-d", "--name", name, "--network", "none", "--memory", "256m",
                   "--cpus", "0.5", "--tmpfs", "/var/lib/postgresql/data",
                   "--mount", f"type=bind,src={bootstrap},dst=/run/secrets/bootstrap_password,readonly",
                   "--mount", f"type=bind,src={app_secret},dst=/run/secrets/app_database_password,readonly",
                   "-e", "POSTGRES_PASSWORD_FILE=/run/secrets/bootstrap_password",
                   "-e", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256", args.image)
            started = True
            for _ in range(60):
                # entrypoint 的临时初始化服务器仅使用 Unix socket；
                # 必须等最终 TCP 监听启动后再测试，避免将初始化阶段误判为服务已就绪。
                if docker("exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres", allow_error=True).returncode == 0:
                    break
                time.sleep(0.25)
            else:
                raise AssertionError("PostgreSQL did not become ready")
            admin(pathlib.Path(args.migration).read_text())
            admin("CREATE TABLE public.permission_probe(id bigserial PRIMARY KEY, value text NOT NULL);")
            permissions = pathlib.Path(args.permissions).read_text()
            admin(permissions)
            flags = admin("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin "
                          "FROM pg_roles WHERE rolname='drop_app';").stdout.strip()
            if flags != "f|f|f|f|f|t":
                raise AssertionError("Unexpected runtime role attributes")
            checks.append("non-superuser login role with no database/role/replication/RLS elevation")
            if admin("SELECT rolpassword LIKE 'SCRAM-SHA-256$%' FROM pg_authid WHERE rolname='drop_app';").stdout.strip() != "t":
                raise AssertionError("Runtime password is not stored as SCRAM")
            if app("INSERT INTO public.permission_probe(value) VALUES('test') RETURNING id;").stdout.strip() != "1":
                raise AssertionError("INSERT or sequence USAGE failed")
            checks.append("secret file trailing LF is trimmed before SCRAM password assignment")
            if app("UPDATE public.permission_probe SET value='updated' RETURNING value;").stdout.strip() != "updated":
                raise AssertionError("UPDATE failed")
            if app("SELECT value FROM public.permission_probe;").stdout.strip() != "updated":
                raise AssertionError("SELECT failed")
            if app("DELETE FROM public.permission_probe RETURNING id;").stdout.strip() != "1":
                raise AssertionError("DELETE failed")
            app("SELECT last_value FROM public.permission_probe_id_seq;")
            checks.append("table SELECT/INSERT/UPDATE/DELETE and sequence USAGE/SELECT succeed")
            app("INSERT INTO public.audit_logs(action,resource_type,resource_id,request_id,detail) "
                "VALUES('permission.fixture','test','fixture','isolated-permissions','{}'::jsonb);")
            checks.append("audit append succeeds with runtime role")
            denied("UPDATE public.audit_logs SET action='tampered';", "audit UPDATE denied")
            denied("DELETE FROM public.audit_logs;", "audit DELETE denied")
            denied("TRUNCATE public.audit_logs;", "audit TRUNCATE denied")
            denied("CREATE ROLE runtime_forbidden;", "CREATE ROLE denied")
            denied("CREATE TABLE public.runtime_forbidden(id int);", "CREATE TABLE denied")
            denied("CREATE TEMP TABLE runtime_forbidden(id int);", "CREATE TEMP TABLE denied")
            denied("SELECT pg_read_file('/run/secrets/app_database_password');", "pg_read_file denied")
            denied("SELECT setval('public.permission_probe_id_seq',999);", "sequence UPDATE/setval denied")
            denied("TRUNCATE public.permission_probe;", "TRUNCATE denied")
            admin("CREATE TABLE public.future_permission_probe(id bigserial PRIMARY KEY,value text);")
            app("INSERT INTO public.future_permission_probe(value) VALUES('future');")
            checks.append("future migration-owner table/sequence default privileges succeed")
            admin("ALTER ROLE drop_app CREATEROLE CREATEDB BYPASSRLS; GRANT CREATE ON SCHEMA public TO drop_app; "
                  "GRANT pg_read_server_files TO drop_app; GRANT TRUNCATE ON public.permission_probe TO drop_app;")
            admin(permissions)
            admin(permissions)
            if admin("SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin "
                     "FROM pg_roles WHERE rolname='drop_app';").stdout.strip() != "f|f|f|f|f|t":
                raise AssertionError("Repeat initialization did not restore role attributes")
            denied("SELECT pg_read_file('/run/secrets/app_database_password');", "stale privileged membership removed")
            denied("CREATE TABLE public.runtime_forbidden(id int);", "stale schema CREATE removed")
            denied("TRUNCATE public.permission_probe;", "stale table TRUNCATE removed")
            checks.append("repeated initialization is idempotent")
            previous = password
            password = secrets.token_urlsafe(32)
            app_secret.write_text(password + "\r\n")
            app_secret.chmod(0o444)
            admin(permissions)
            app("SELECT 1;")
            if app("SELECT 1;", secret=previous, allow_error=True).returncode == 0:
                raise AssertionError("Previous runtime password remained valid after rotation")
            checks.append("secret file trailing CRLF is trimmed; rotation succeeds and previous password is rejected")
            logs = docker("logs", name)
            if previous in logs.stdout + logs.stderr or password in logs.stdout + logs.stderr:
                raise AssertionError("A password appeared in PostgreSQL container logs")
            checks.append("PostgreSQL container logs contain no tested database passwords")
            report = {"postgres_image": args.image, "isolated_network": "none",
                      "public_ports": [], "passed": True, "checks": checks,
                      "runtime_extra_privileges": ["CONNECT on the application database"],
                      "sequence_update_granted": False}
            report["audit_runtime_append_only"] = True
            pathlib.Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
        finally:
            if started:
                docker("rm", "-f", name, allow_error=True)


if __name__ == "__main__":
    main()
