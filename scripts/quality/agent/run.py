"""有界执行冻结JD情境：只导入合成文件、运行只读诊断，不确认采集/修复。

默认仅生成计划。实际执行必须显式--execute，使用文件凭据、严格TLS和固定目标白名单。
私有capture保存实际DiagnosisRun；公开报告应由quality.py生成，仅输出哈希/结构标签/计数。
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import quality


class RunError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RunError("REDIRECT_REJECTED")


def validate_origin(value, allow_loopback=False):
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise RunError("ORIGIN_REJECTED")
    if parsed.hostname == "82.157.81.79" and parsed.scheme == "https" and parsed.port in {None, 443}:
        return "https://82.157.81.79"
    if allow_loopback and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.scheme in {"http", "https"} and parsed.port is not None and 1024 <= parsed.port <= 65535:
        return value.rstrip("/")
    raise RunError("TARGET_NOT_ALLOWLISTED")


class Client:
    def __init__(self, origin, allow_loopback=False):
        self.origin = validate_origin(origin, allow_loopback)
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(self.jar), NoRedirect())
        self.csrf = ""
        self.count = 0

    def request(self, method, path, data=None, key=None):
        # 不接受模型给出的路径、URL或查询；调用者仅传下列固定业务面。
        if method not in {"GET", "POST"} or not re.fullmatch(r"/api/v1/(auth/(login|logout)|tasks(/[1-9][0-9]{0,18}(/cancel|/diagnoses(/[1-9][0-9]{0,18}/cancel)?)?)?)", path):
            raise RunError("PATH_REJECTED")
        self.count += 1
        if self.count > 1500:
            raise RunError("HTTP_BUDGET_EXCEEDED")
        headers = {"Content-Type": "application/json", "Origin": self.origin, "Accept": "application/json"}
        if self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if key:
            headers["Idempotency-Key"] = key
        body = None if data is None else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
        request = urllib.request.Request(self.origin + path, data=body, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=15)
        except urllib.error.HTTPError as exc:
            response = exc
        except (OSError, urllib.error.URLError) as exc:
            raise RunError("HTTP_TRANSPORT_FAILED") from None
        with response:
            raw = response.read(quality.MAX_BYTES + 1)
            if len(raw) > quality.MAX_BYTES:
                raise RunError("HTTP_RESPONSE_TOO_LARGE")
            try:
                parsed = json.loads(raw, object_pairs_hook=quality.unique_object)
            except ValueError:
                raise RunError("HTTP_JSON_INVALID") from None
            if not isinstance(parsed, dict):
                raise RunError("HTTP_ENVELOPE_INVALID")
            return response.status, parsed


def save_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


def error_code(envelope):
    value = envelope.get("error", {}).get("code", "HTTP_REJECTED")
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z_]{1,64}", value) else "HTTP_REJECTED"


def execute(client, plan, credentials, *, analysis_timeout=90, diagnosis_timeout=180, max_seconds=1800):
    if not isinstance(credentials, dict) or set(credentials) != {"username", "password"} or any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in credentials.values()):
        raise RunError("CREDENTIAL_FILE_INVALID")
    inputs, _, frozen = quality.load_dataset()
    payload = {"schema_version": 1, "kind": "actual_platform_http_capture", "origin": client.origin, "plan": plan, "manifest_sha256": quality.evaluation.canonical_sha256(frozen), "rows": [], "limits": ["作者可见合成输入，不是盲测或实际生产事故。", "不确认新采集或修复；失败结果完整保留。", "本文件含实际诊断观测，仅保存到受控私有目录；公开使用quality.py评分输出。"]}
    started = time.monotonic()
    active_task, active_run = None, None
    analysis_pending, analysis_cancel_attempted, diagnosis_cancel_attempted = False, False, False
    diagnosis_request_pending = False
    logged_in = False
    try:
        code, envelope = client.request("POST", "/api/v1/auth/login", credentials)
        if code != 200:
            raise RunError("LOGIN_REJECTED")
        logged_in = True
        data = envelope.get("data")
        csrf = data.get("csrf_token") if isinstance(data, dict) else None
        if not isinstance(csrf, str) or not 1 <= len(csrf) <= 256:
            client.csrf = ""
            raise RunError("CSRF_MISSING")
        client.csrf = csrf
        for trial in plan["trials"]:
            if time.monotonic() - started > max_seconds:
                break  # 未执行项由评分器保留在分母，不能悄悄缩小计划。
            case = inputs[trial["case_id"]]
            row = {**trial, "stage": "import_requested"}
            payload["rows"].append(row)
            key = "jd-quality-" + uuid.uuid4().hex
            status, response = client.request("POST", "/api/v1/tasks", {"name": "JD受控导入 " + case["id"], "kind": "IMPORT_COLLAPSED", "collapsed_text": case["collapsed_text"]}, key)
            if status not in {200, 201}:
                row.update(stage="import_rejected", error_code=error_code(response), http_status=status)
                continue
            task = response.get("data", {})
            if not quality.valid_id(task.get("id")):
                raise RunError("TASK_ID_INVALID")
            active_task = str(task["id"])
            row["task_id"] = active_task
            row["stage"] = "analysis_pending"
            analysis_pending, analysis_cancel_attempted, diagnosis_cancel_attempted = True, False, False
            deadline = min(started + max_seconds, time.monotonic() + analysis_timeout)
            while time.monotonic() < deadline:
                status, response = client.request("GET", f"/api/v1/tasks/{active_task}")
                if status != 200:
                    raise RunError("TASK_POLL_REJECTED")
                task = response.get("data", {})
                if task.get("analysis_status") in {"SUCCESS", "FAILED", "CANCELED"}:
                    break
                time.sleep(1)
            if task.get("analysis_status") != "SUCCESS":
                row.update(stage="analysis_rejected" if task.get("analysis_status") == "FAILED" else "analysis_timeout", error_code=task.get("error_code") or task.get("analysis_error_code") or "UNKNOWN_ANALYSIS_ERROR", analysis_status=task.get("analysis_status"))
                if row["stage"] == "analysis_timeout":
                    analysis_cancel_attempted = True
                    status, _ = client.request("POST", f"/api/v1/tasks/{active_task}/cancel", {})
                    row["timeout_cancel_confirmed"] = status == 200
                    if status != 200:
                        payload["cleanup_error"] = "TASK_CANCEL_UNCONFIRMED"
                        break
                analysis_pending = False
                continue
            analysis_pending = False
            result = task.get("result")
            if not isinstance(result, dict):
                raise RunError("ANALYSIS_RESULT_MISSING")
            # 绑定真实Analyzer投影。oracle与标签不进入请求；保留输入/分析的摘要用于后续核对。
            row["analysis_sha256"] = quality.evaluation.canonical_sha256(quality.evaluation.result_projection(result))
            row["stage"] = "diagnosis_requested"
            diagnosis_request_pending = True
            status, response = client.request("POST", f"/api/v1/tasks/{active_task}/diagnoses", {"goal": case["goal"], "mode": "model", "consent": True, "knowledge_mode": "off"}, "jd-quality-model-" + uuid.uuid4().hex)
            if status != 202:
                diagnosis_request_pending = False
                row.update(stage="diagnosis_rejected", error_code=error_code(response), http_status=status)
                continue
            run = response.get("data", {})
            if not isinstance(run, dict) or not quality.valid_id(run.get("id")):
                raise RunError("RUN_ID_INVALID")
            active_run = str(run["id"])
            diagnosis_request_pending = False
            deadline = min(started + max_seconds, time.monotonic() + diagnosis_timeout)
            while time.monotonic() < deadline:
                status, response = client.request("GET", f"/api/v1/tasks/{active_task}/diagnoses")
                if status != 200:
                    raise RunError("DIAGNOSIS_POLL_REJECTED")
                rows = response.get("data", {}).get("items", [])
                current = [item for item in rows if str(item.get("id")) == active_run]
                if len(current) == 1:
                    run = current[0]
                if run.get("status") in {"success", "failed", "error", "canceled"}:
                    break
                time.sleep(1)
            if run.get("status") not in {"success", "failed", "error", "canceled"}:
                diagnosis_cancel_attempted = True
                status, _ = client.request("POST", f"/api/v1/tasks/{active_task}/diagnoses/{active_run}/cancel", {})
                row.update(stage="diagnosis_timeout", run_id=active_run)
                row["timeout_cancel_confirmed"] = status == 200
                if status != 200:
                    payload["cleanup_error"] = "DIAGNOSIS_CANCEL_UNCONFIRMED"
                    break
            else:
                row.update(stage="diagnosis_terminal", run=run)
            active_run = None
    except (RunError, ValueError, KeyError, TypeError, KeyboardInterrupt) as exc:
        # 只回显本文件自己定义的固定枚举；不序列化HTTP正文、上游异常或凭据。
        code = str(exc) if isinstance(exc, RunError) else "EXECUTION_INTERRUPTED"
        payload["execution_error"] = code if re.fullmatch(r"[A-Z_]{1,64}", code) else "EXECUTION_INTERRUPTED"
        if diagnosis_request_pending:
            payload["cleanup_error"] = "DIAGNOSIS_CREATE_OUTCOME_UNKNOWN_DO_NOT_REPLAY"
    finally:
        if active_task and active_run and not diagnosis_cancel_attempted:
            try:
                status, _ = client.request("POST", f"/api/v1/tasks/{active_task}/diagnoses/{active_run}/cancel", {})
                payload["interrupted_diagnosis_cancel_confirmed"] = status == 200
                if status != 200:
                    payload["cleanup_error"] = "DIAGNOSIS_CANCEL_UNCONFIRMED"
            except (RunError, ValueError, OSError):
                payload["cleanup_error"] = "DIAGNOSIS_CANCEL_UNCONFIRMED"
        if active_task and analysis_pending and not analysis_cancel_attempted:
            try:
                status, _ = client.request("POST", f"/api/v1/tasks/{active_task}/cancel", {})
                payload["interrupted_task_cancel_confirmed"] = status == 200
                if status != 200:
                    payload["cleanup_error"] = "TASK_CANCEL_UNCONFIRMED"
            except (RunError, ValueError, OSError):
                payload["cleanup_error"] = "TASK_CANCEL_UNCONFIRMED"
        if logged_in:
            try:
                status, _ = client.request("POST", "/api/v1/auth/logout", {})
                payload["logout_confirmed"] = status == 200
                if status != 200:
                    payload["logout_error"] = "LOGOUT_UNCONFIRMED"
            except (RunError, ValueError, OSError):
                payload["logout_confirmed"] = False
                payload["logout_error"] = "LOGOUT_UNCONFIRMED"
    payload["http_requests"] = client.count
    payload["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://82.157.81.79")
    parser.add_argument("--allow-loopback", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--credentials", type=Path, help="只含username/password的JSON文件；不能在命令行传密钥")
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        plan = quality.build_plan(args.cases, args.repetitions)
        validate_origin(args.origin, args.allow_loopback)
        if args.output.exists():
            raise RunError("OUTPUT_EXISTS")
        if args.execute:
            if args.credentials is None or args.credentials.stat().st_size > 4096:
                raise RunError("CREDENTIAL_FILE_REQUIRED")
            payload = execute(Client(args.origin, args.allow_loopback), plan, quality.read_json(args.credentials))
            save_new(args.output, payload)
            print(json.dumps({"trials_recorded": len(payload["rows"]), "planned": len(plan["trials"]), "execution_error": payload.get("execution_error")}))
            return 1 if payload.get("execution_error") or payload.get("cleanup_error") else 0
        else:
            save_new(args.output, plan)
            print(json.dumps({"mode": "plan_only", "trials": len(plan["trials"]), "model_calls": 0}))
            return 0
    except (RunError, ValueError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, f"未执行或未完成：{type(exc).__name__}；请检查白名单、凭据文件和输出路径。\n")


if __name__ == "__main__":
    raise SystemExit(main())
