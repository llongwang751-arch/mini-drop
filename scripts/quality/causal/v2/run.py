"""受控故障→实际采集/诊断→撤销→同参数复采/再诊断。

仅向既有合成演示发送白名单场景请求；不改生产源码、进程、SQL或采集权限。
控制器持有注入真值，模型只收到中性问题与普通Drop证据。默认仅生成计划。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

experiment_spec = importlib.util.spec_from_file_location("causal_v2_experiment", Path(__file__).with_name("experiment.py"))
experiment = importlib.util.module_from_spec(experiment_spec)
experiment_spec.loader.exec_module(experiment)

sys.path.insert(0, str(experiment.ROOT / "scripts/quality/agent"))
spec = importlib.util.spec_from_file_location("causal_platform_client", experiment.ROOT / "scripts/quality/agent/run.py")
platform_client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(platform_client)


class CausalError(RuntimeError):
    pass


def cleanup_uncertain(value):
    """成功取消与已知终态保留失败，但不阻塞剩余预登记轮次；未知创建/未确认取消立即停。"""
    return bool(value.get("error")) or any(result is not True for key, result in value.items() if key.endswith("_confirmed"))


def phase_uncertain(phase):
    return cleanup_uncertain(phase.get("cleanup", {})) or phase.get("load_stop_confirmed") is not True or phase.get("load_error") in {"DOWNSTREAM_COMPLETION_UNCONFIRMED", "CONTROLLED_LOAD_STOP_UNCONFIRMED"}


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def digest(value):
    return experiment.common.canonical_sha256(value)


def validate_demo_origin(value, role, allow_loopback=False):
    parsed = urllib.parse.urlsplit(value)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or parsed.scheme != "http":
        raise CausalError("DEMO_ORIGIN_REJECTED")
    if parsed.hostname == "observability-" + role and parsed.port == {"gateway": 8130, "catalog": 8131}[role]:
        return value.rstrip("/")
    if allow_loopback and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port is not None and 1024 <= parsed.port <= 65535:
        return value.rstrip("/")
    raise CausalError("DEMO_TARGET_NOT_ALLOWLISTED")


class DemoClient:
    def __init__(self, gateway, catalog, allow_loopback=False):
        self.origins = {role: validate_demo_origin(value, role, allow_loopback) for role, value in (("gateway", gateway), ("catalog", catalog))}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), platform_client.NoRedirect())
        self.calls = 0
        self.lock = threading.Lock()
        self.policy = experiment.dataset()[1]["policy"]

    def request(self, role, path):
        if role not in self.origins or len(path) > 1024:
            raise CausalError("DEMO_PATH_REJECTED")
        parsed = urllib.parse.urlsplit(path)
        values = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=6)
        if parsed.scheme or parsed.netloc or parsed.fragment or any(len(items) != 1 for items in values.values()):
            raise CausalError("DEMO_PATH_REJECTED")
        values = {key: rows[0] for key, rows in values.items()}
        valid = parsed.path == "/health" and not values
        valid |= parsed.path == "/demo/request" and role == "gateway" and set(values) == {"scenario"} and values["scenario"] in {"normal", "cpu", "slow_query", "timeout"}
        if parsed.path == "/v1/signals" and set(values) == {"service", "signal", "start", "end", "limit"}:
            valid |= values["service"] == role and values["signal"] in {"metrics", "logs", "traces"} and values["limit"] == "30" and 0 < (experiment.utc(values["end"]) - experiment.utc(values["start"])).total_seconds() <= 150
        if not valid:
            raise CausalError("DEMO_PATH_REJECTED")
        with self.lock:
            self.calls += 1
            if self.calls > self.policy["demo_http_request_limit"]:
                raise CausalError("DEMO_HTTP_BUDGET_EXCEEDED")
        try:
            response = self.opener.open(self.origins[role] + path, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        except (OSError, urllib.error.URLError):
            raise CausalError("DEMO_TRANSPORT_FAILED") from None
        with response:
            raw = response.read(262145)
            if len(raw) > 262144:
                raise CausalError("DEMO_OUTPUT_LIMIT")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise CausalError("DEMO_ENVELOPE_INVALID")
            return response.status, body

    def health(self):
        rows = {}
        for role in ("gateway", "catalog"):
            status, value = self.request(role, "/health")
            if status != 200 or value.get("role") != role or value.get("provenance") != "synthetic_controlled_live_http_demo" or value.get("runtime_revision") != "observability-demo-v1" or type(value.get("pid")) is not int or value["pid"] <= 1 or type(value.get("uid")) is not int:
                raise CausalError("DEMO_HEALTH_IDENTITY_INVALID")
            rows[role] = {key: value[key] for key in ("role", "pid", "uid", "provenance", "runtime_revision")}
        return rows

    def work(self, scenario):
        if scenario not in {"normal", "cpu", "slow_query", "timeout"}:
            raise CausalError("SCENARIO_REJECTED")
        started = stamp()
        status, body = self.request("gateway", "/demo/request?" + urllib.parse.urlencode({"scenario": scenario}))
        if body.get("provenance") != "synthetic_controlled_live_http_demo" or body.get("service") != "gateway" or body.get("scenario") != scenario or not re.fullmatch(r"[a-f0-9]{32}", body.get("trace_id", "")) or not experiment.finite(body.get("duration_ms")):
            raise CausalError("DEMO_RESPONSE_PROVENANCE_INVALID")
        row = {"status_code": status, "duration_ms": body["duration_ms"], "trace_sha256": hashlib.sha256(body["trace_id"].encode()).hexdigest()}
        if scenario == "timeout" and status == 504:
            # 网关deadline不取消目录SQL。等待同一真实Trace完成，防止下一请求叠成额外并发干预。
            self.await_catalog_completion(row["trace_sha256"], started)
            row["downstream_completion_observed"] = True
        return row

    def await_catalog_completion(self, trace_sha256, started):
        deadline = time.monotonic() + self.policy["downstream_completion_seconds"]
        while time.monotonic() < deadline:
            query = {"service": "catalog", "signal": "logs", "start": started, "end": stamp(), "limit": 30}
            status, body = self.request("catalog", "/v1/signals?" + urllib.parse.urlencode(query))
            if status != 200 or body.get("service_id") != "catalog" or body.get("signal") != "logs":
                raise CausalError("DOWNSTREAM_COMPLETION_SOURCE_INVALID")
            for row in body.get("logs", []):
                value = row.get("trace_id")
                if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) and hashlib.sha256(value.encode()).hexdigest() == trace_sha256:
                    return
            time.sleep(self.policy["downstream_poll_interval_seconds"])
        raise CausalError("DOWNSTREAM_COMPLETION_UNCONFIRMED")

    def signals(self, start, end):
        result = {}
        for role in ("gateway", "catalog"):
            result[role] = {}
            for signal in ("metrics", "logs", "traces"):
                status, body = self.request(role, "/v1/signals?" + urllib.parse.urlencode({"service": role, "signal": signal, "start": start, "end": end, "limit": 30}))
                if status != 200 or body.get("service_id") != role or body.get("signal") != signal:
                    raise CausalError("DEMO_SIGNAL_REJECTED")
                # 客观相关性仅保存合成Trace摘要，不把真实ID公开或提交到模型。
                for row in body.get("logs", []) + body.get("traces", []):
                    if "trace_id" in row:
                        row["trace_sha256"] = hashlib.sha256(row.pop("trace_id").encode()).hexdigest()
                result[role][signal] = body
        return result


class Load:
    def __init__(self, demo, scenario, policy):
        self.demo, self.scenario, self.policy = demo, scenario, policy
        self.rows, self.error = [], None
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.worker, daemon=True)
        self.start = stamp()

    def worker(self):
        deadline = time.monotonic() + self.policy["phase_seconds"]
        try:
            while len(self.rows) < self.policy["load_max_requests_per_phase"] and time.monotonic() < deadline and not self.stopped.is_set():
                self.rows.append(self.demo.work(self.scenario))
                self.stopped.wait(self.policy["load_interval_seconds"])
        except (CausalError, ValueError, TypeError, KeyError) as exc:
            value = str(exc) if isinstance(exc, CausalError) else "CONTROLLED_LOAD_FAILED"
            self.error = value if re.fullmatch(r"[A-Z_]{1,80}", value) else "CONTROLLED_LOAD_FAILED"

    def begin(self):
        self.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        self.thread.join(timeout=7)
        if self.thread.is_alive():
            self.error = "CONTROLLED_LOAD_STOP_UNCONFIRMED"


class Budget:
    def __init__(self, policy):
        self.policy = policy
        self.models = self.tools = self.unknown_charges = 0

    def reserve(self):
        if self.models + self.policy["reserve_model_calls_per_diagnosis"] > self.policy["max_model_calls"] or self.tools + self.policy["reserve_tool_calls_per_diagnosis"] > self.policy["max_tool_calls"]:
            raise CausalError("CUMULATIVE_MODEL_TOOL_BUDGET_EXHAUSTED")

    def charge(self, run):
        report = experiment.common.parse_embedded(run.get("report") or {}, dict, "report")
        runtime = report.get("agent_runtime") or {}
        model, tool = runtime.get("model_calls"), runtime.get("tool_calls")
        if type(model) is int and type(tool) is int and 0 <= model <= 9 and 0 <= tool <= 14:
            self.models += model
            self.tools += tool
        else:
            self.models += self.policy["reserve_model_calls_per_diagnosis"]
            self.tools += self.policy["reserve_tool_calls_per_diagnosis"]
            self.unknown_charges += 1

    def receipt(self):
        return {"accounted_model_calls": self.models, "accounted_tool_calls": self.tools, "unknown_runs_charged_worst_case": self.unknown_charges, "model_limit": self.policy["max_model_calls"], "tool_limit": self.policy["max_tool_calls"], "counts_are_runtime_logical_calls_not_provider_network_attempts": True}


def validate_environment(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "images", "sources"} or value["schema_version"] != 1:
        raise CausalError("ENVIRONMENT_MANIFEST_REQUIRED")
    required_images = {"api", "agent", "helper", "analyzer", "runtime", "observability_demo"}
    if not isinstance(value["images"], dict) or set(value["images"]) != required_images or any(not isinstance(row, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", row) is None for row in value["images"].values()):
        raise CausalError("FROZEN_IMAGE_DIGESTS_REQUIRED")
    if not isinstance(value["sources"], dict) or set(value["sources"]) != {"observability_server", "observability_config", "task_kinds"} or any(not isinstance(row, str) or re.fullmatch(r"[a-f0-9]{64}", row) is None for row in value["sources"].values()):
        raise CausalError("FROZEN_SOURCE_DIGESTS_REQUIRED")
    expected = hashlib.sha256((experiment.ROOT / "integrations/observability-demo/server.py").read_bytes()).hexdigest()
    if value["sources"]["observability_server"] != expected:
        raise CausalError("CONTROLLED_SERVER_SOURCE_CHANGED")
    return value


def sampling_projection(task, expected_pid):
    if task.get("collection_status") != "COLLECTED" or task.get("analysis_status") != "SUCCESS" or task.get("kind") != "PYTHON_CPU":
        raise CausalError("REAL_COLLECTION_NOT_SUCCESSFUL")
    result = task.get("result")
    if not isinstance(result, dict) or result.get("kind") != "PYTHON_CPU" or result.get("summary", {}).get("source") != "py-spy" or not experiment.finite(result.get("total_samples")) or result["total_samples"] < experiment.dataset()[1]["policy"]["minimum_real_samples"]:
        raise CausalError("REAL_PYSPY_RESULT_REQUIRED")
    attempts = task.get("attempts", [])
    if not attempts:
        raise CausalError("COLLECTION_ATTEMPT_REQUIRED")
    attempt = max(attempts, key=lambda row: row.get("attempt", 0))
    if attempt.get("started_at") is None or attempt.get("finished_at") is None:
        raise CausalError("REAL_EXECUTION_CLOCKS_REQUIRED")
    params = task.get("params")
    if not isinstance(params, dict) or params.get("pid") != expected_pid or params.get("duration_seconds") != 20 or params.get("frequency_hz") != 99:
        raise CausalError("COLLECTION_PARAMETERS_CHANGED")
    rows = result.get("top_functions")
    if not isinstance(rows, list):
        raise CausalError("HOTSPOT_TABLE_REQUIRED")
    # 识别本仓库固定控制函数的真实采样符号；不从模型解释或宽泛CPU关键词猜根因。
    cpu = sum(row.get("self_percent", 0) for row in rows if isinstance(row.get("name"), str) and re.fullmatch(r"python_cpu_hotspot(?: \([^\r\n]*server\.py:[0-9]+\))?", row["name"]) and experiment.finite(row.get("self_percent")))
    # traceparent由Go为每次HTTP任务自动注入，属于观测链身份，不是采集策略。
    # 仅排除此固定字段；PID/启动身份/预算/选项等所有执行参数仍参与前后摘要比较。
    comparison_params = {key: value for key, value in params.items() if key != "traceparent"}
    return {"live_collection": True, "kind": "PYTHON_CPU", "source": "py-spy", "total_samples": result["total_samples"], "controlled_cpu_self_percent": cpu, "attempt_id": str(attempt["id"]), "collection_start": attempt["started_at"], "collection_end": attempt["finished_at"], "analysis_sha256": digest(experiment.common.result_projection(result)), "parameters_sha256": digest(comparison_params), "excluded_comparison_fields": ["traceparent"]}


class Platform:
    def __init__(self, client, config, target, budget, model, deadline):
        self.client, self.config, self.target, self.budget, self.model, self.deadline = client, config, target, budget, model, deadline
        self.task = self.run = None
        self.analysis_pending = False
        self.request_unknown = False

    def collect_and_diagnose(self, phase, trial_name, phase_deadline):
        self.budget.reserve()
        params = {**{k: v for k, v in self.model["collection"].items() if k != "kind"}, "pid": self.target["catalog"]["pid"]}
        body = {"name": "受控观测 " + trial_name, "kind": "PYTHON_CPU", "target_agent_id": self.config["agent_id"], "parameters": params}
        status, response = self.client.request("POST", "/api/v1/tasks", body, "causal-collect-" + uuid.uuid4().hex)
        if status not in {200, 201}:
            raise CausalError("COLLECTION_CREATE_REJECTED")
        task = response.get("data", {})
        if not platform_client.quality.valid_id(task.get("id")):
            raise CausalError("COLLECTION_CREATE_OUTCOME_UNKNOWN")
        self.task, self.analysis_pending = str(task["id"]), True
        phase["task_id"] = self.task
        deadline = min(self.deadline, phase_deadline, time.monotonic() + 90)
        notice = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, response = self.client.request("GET", f"/api/v1/tasks/{self.task}")
            if status != 200:
                raise CausalError("COLLECTION_READ_REJECTED")
            task = response.get("data", {})
            if task.get("analysis_status") in {"SUCCESS", "FAILED", "CANCELED"} or task.get("collection_status") in {"FAILED", "CANCELED"}:
                break
            if time.monotonic() >= notice:
                print(json.dumps({"stage": "awaiting_collection_analysis", "task_id": self.task}), flush=True)
                notice = time.monotonic() + 10
            time.sleep(1)
        # 保存真实终态与固定错误码，空样本失败不会伪装成正常结果，也不为此重试模型。
        terminal = task.get("analysis_status") in {"SUCCESS", "FAILED", "CANCELED"} or task.get("collection_status") in {"FAILED", "CANCELED"}
        code = task.get("error_code")
        phase["collection_outcome"] = {"terminal": terminal, "collection_status": task.get("collection_status"), "analysis_status": task.get("analysis_status"), "error_code": code if isinstance(code, str) and re.fullmatch(r"[A-Z_]{1,80}", code) else None}
        if terminal:
            self.analysis_pending = False
        if task.get("analysis_status") != "SUCCESS":
            raise CausalError("COLLECTION_OR_ANALYSIS_NOT_SUCCESSFUL")
        self.analysis_pending = False
        phase["sampling"] = sampling_projection(task, self.target["catalog"]["pid"])
        phase["collection_start"] = phase["sampling"]["collection_start"]
        phase["collection_parameters_sha256"] = phase["sampling"]["parameters_sha256"]
        self.request_unknown = True
        status, response = self.client.request("POST", f"/api/v1/tasks/{self.task}/diagnoses", {"goal": self.model["goal"], **self.model["diagnosis"], "telemetry_service_id": self.config["telemetry_service_id"]}, "causal-diagnosis-" + uuid.uuid4().hex)
        if status != 202:
            self.request_unknown = False
            raise CausalError("DIAGNOSIS_CREATE_REJECTED")
        run = response.get("data", {})
        if not isinstance(run, dict) or not platform_client.quality.valid_id(run.get("id")):
            raise CausalError("DIAGNOSIS_CREATE_OUTCOME_UNKNOWN")
        self.run, self.request_unknown = str(run["id"]), False
        phase["run_id"] = self.run
        deadline = min(self.deadline, phase_deadline, time.monotonic() + 90)
        notice = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, response = self.client.request("GET", f"/api/v1/tasks/{self.task}/diagnoses")
            if status != 200:
                raise CausalError("DIAGNOSIS_READ_REJECTED")
            matches = [row for row in response.get("data", {}).get("items", []) if str(row.get("id")) == self.run]
            if len(matches) == 1:
                run = matches[0]
            if run.get("status") in {"success", "failed", "error", "canceled"}:
                break
            if time.monotonic() >= notice:
                print(json.dumps({"stage": "awaiting_diagnosis", "task_id": self.task, "run_id": self.run}), flush=True)
                notice = time.monotonic() + 10
            time.sleep(1)
        if run.get("status") not in {"success", "failed", "error", "canceled"}:
            self.budget.charge({})
            raise CausalError("DIAGNOSIS_TIMEOUT")
        self.budget.charge(run)
        phase["diagnosis"] = run
        phase["stage"] = "diagnosed" if run["status"] == "success" else "diagnosis_failed"
        self.run = None

    def cleanup(self):
        result = {}
        if self.request_unknown:
            self.budget.charge({})
            result["error"] = "DIAGNOSIS_CREATE_OUTCOME_UNKNOWN_DO_NOT_REPLAY"
            self.request_unknown = False
        for path, key in ((f"/api/v1/tasks/{self.task}/diagnoses/{self.run}/cancel" if self.task and self.run else None, "diagnosis_cancel_confirmed"), (f"/api/v1/tasks/{self.task}/cancel" if self.task and self.analysis_pending else None, "task_cancel_confirmed")):
            if path:
                try:
                    status, _ = self.client.request("POST", path, {})
                    result[key] = status == 200
                except (ValueError, OSError, RuntimeError):
                    result[key] = False
        self.task, self.run, self.analysis_pending = None, None, False
        return result


def independent_before(demo, fingerprint, policy):
    phase = {"stage": "started", "start": stamp(), "target_fingerprint": fingerprint}
    phase["responses"] = []
    for _ in range(3):
        phase["responses"].append(demo.work("normal"))
        time.sleep(policy["load_interval_seconds"])
    phase["end"] = stamp()
    phase["signals"] = demo.signals(phase["start"], phase["end"])
    phase["stage"] = "observed"
    return phase


def measured_phase(demo, scenario, fingerprint, policy, platform, trial_name, record, washout=0):
    phase = record
    phase.update(stage="started", start=stamp(), target_fingerprint=fingerprint)
    worker = Load(demo, scenario, policy).begin()
    try:
        warmup_end = time.monotonic() + max(2, washout)
        notice = time.monotonic() + 10
        while time.monotonic() < warmup_end:
            if worker.error or not worker.thread.is_alive() or time.monotonic() > platform.deadline:
                raise CausalError("CONTROLLED_LOAD_OR_TIME_BUDGET_FAILED")
            if time.monotonic() >= notice:
                print(json.dumps({"stage": "observation_washout", "remaining_seconds": math.ceil(warmup_end - time.monotonic())}), flush=True)
                notice = time.monotonic() + 10
            time.sleep(.2)
        platform.collect_and_diagnose(phase, trial_name, time.monotonic() + max(0, policy["phase_seconds"] - max(2, washout)))
        if worker.error:
            raise CausalError("CONTROLLED_LOAD_FAILED")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, KeyboardInterrupt) as exc:
        phase["stage"] = "diagnosis_failed"
        value = str(exc) if isinstance(exc, CausalError) else "PHASE_FAILED"
        phase["error_code"] = value if re.fullmatch(r"[A-Z_]{1,80}", value) else "PHASE_FAILED"
        phase["cleanup"] = platform.cleanup()
    finally:
        worker.stop()
        phase["end"] = stamp()
        phase["responses"] = worker.rows
        phase["load_stop_confirmed"] = not worker.thread.is_alive()
        if worker.error:
            phase["load_error"] = worker.error
        phase["signals"] = demo.signals(phase["start"], phase["end"])
    return phase


def execute(config, environment, credentials, preregistered, allow_loopback=False, checkpoint=None):
    model, oracle, manifest = experiment.dataset()
    policy = oracle["policy"]
    if set(config) != {"api_origin", "gateway_origin", "catalog_origin", "agent_id", "telemetry_service_id"} or any(not isinstance(config[key], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", config[key]) for key in ("agent_id", "telemetry_service_id")):
        raise CausalError("CONFIG_BOUNDARY_REJECTED")
    validate_environment(environment)
    demo = DemoClient(config["gateway_origin"], config["catalog_origin"], allow_loopback)
    client = platform_client.Client(config["api_origin"], allow_loopback)
    target = demo.health()
    environment_sha = digest(environment)
    fingerprint = digest({"health": target, "environment_sha256": environment_sha})
    budget = Budget(policy)
    capture = {"schema_version": 1, "kind": "actual_controlled_causal_platform_experiment", "plan": preregistered, "environment_sha256": environment_sha, "environment": environment, "health": target, "manifest_sha256": digest(manifest), "started_at": stamp(), "rounds": []}
    platform = Platform(client, config, target, budget, model, time.monotonic() + policy["global_seconds"])
    logged_in = False
    try:
        if not isinstance(credentials, dict) or set(credentials) != {"username", "password"}:
            raise CausalError("CREDENTIAL_FILE_INVALID")
        status, response = client.request("POST", "/api/v1/auth/login", credentials)
        if status != 200:
            raise CausalError("LOGIN_REJECTED")
        logged_in = True
        data = response.get("data")
        value = data.get("csrf_token") if isinstance(data, dict) else None
        if not isinstance(value, str) or not 1 <= len(value) <= 256:
            raise CausalError("CSRF_MISSING")
        client.csrf = value
        for index, trial in enumerate(preregistered["trials"]):
            budget.reserve()
            if demo.health() != target or time.monotonic() >= platform.deadline:
                raise CausalError("TARGET_OR_EXPERIMENT_BUDGET_CHANGED")
            case = next(value for value in oracle["cases"] if value["id"] == trial["case_id"])
            row = {**trial, "before": independent_before(demo, fingerprint, policy), "fault": {}, "after": {}}
            capture["rounds"].append(row)
            if checkpoint:
                checkpoint(capture)
            measured_phase(demo, case["scenario"], fingerprint, policy, platform, f"{index+1}-A", row["fault"])
            if checkpoint:
                checkpoint(capture)
            if phase_uncertain(row["fault"]):
                row["after"] = independent_before(demo, fingerprint, policy)
                raise CausalError("FAULT_PHASE_CLEANUP_UNCONFIRMED_NO_NEW_MODEL_RUN")
            # 场景只随本控制器的请求生效。停故障客户端后持续normal，等待旧故障退出扩展一分钟的查询窗。
            measured_phase(demo, "normal", fingerprint, policy, platform, f"{index+1}-B", row["after"], policy["washout_seconds"])
            row["target_stable_after"] = demo.health() == target
            if checkpoint:
                checkpoint(capture)
            print(json.dumps({"round_finished": index + 1, "planned": len(preregistered["trials"]), "model_calls_accounted": budget.models, "tool_calls_accounted": budget.tools}), flush=True)
            if not row["target_stable_after"] or any(phase_uncertain(phase) for phase in (row["fault"], row["after"])):
                raise CausalError("PHASE_FAILED_REVIEW_RECEIPT_BEFORE_CONTINUING")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, KeyboardInterrupt) as exc:
        value = str(exc) if isinstance(exc, CausalError) else "EXPERIMENT_FAILED"
        capture["execution_error"] = value if re.fullmatch(r"[A-Z_]{1,80}", value) else "EXPERIMENT_FAILED"
    finally:
        capture["cleanup"] = platform.cleanup()
        try:
            capture["normal_final_control"] = independent_before(demo, fingerprint, policy)
        except (ValueError, OSError, RuntimeError):
            capture["normal_final_control_error"] = "FINAL_CONTROL_UNCONFIRMED"
        if logged_in:
            try:
                status, _ = client.request("POST", "/api/v1/auth/logout", {})
                capture["logout_confirmed"] = status == 200
            except (ValueError, OSError, RuntimeError):
                capture["logout_confirmed"] = False
    capture["finished_at"] = stamp()
    capture["budget"] = budget.receipt()
    capture["api_http_requests"] = client.count
    capture["demo_http_requests"] = demo.calls
    capture["recorded_phase_failures"] = sum(phase.get("stage") != "diagnosed" for row in capture["rounds"] for phase in (row["fault"], row["after"]))
    return capture


def save(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2, allow_nan=False)
        target.write("\n")


def append_checkpoint(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        target.write(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        target.flush()
        os.fsync(target.fileno())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-loopback", action="store_true")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        preregistered = experiment.plan(args.repetitions, case_ids=args.cases)
        args.output.mkdir(parents=True, exist_ok=False)
        save(args.output / "plan.json", preregistered)
        if not args.execute:
            print(json.dumps({"mode": "plan_only", "rounds": len(preregistered["trials"]), "planned_diagnoses": 2 * len(preregistered["trials"]), "model_calls": 0}))
            return 0
        capture = execute(experiment.load(args.config), experiment.load(args.environment), experiment.load(args.credentials), preregistered, args.allow_loopback, lambda value: append_checkpoint(args.output / "progress.private.jsonl", value))
        save(args.output / "capture.private.json", capture)
        save(args.output / "score.json", experiment.score(capture))
        save(args.output / "human-review.json", experiment.review_template(capture))
        print(json.dumps({"recorded_rounds": len(capture["rounds"]), "planned_rounds": len(preregistered["trials"]), "execution_error": capture.get("execution_error"), "budget": capture["budget"]}))
        return 1 if capture.get("execution_error") or cleanup_uncertain(capture.get("cleanup", {})) or capture.get("logout_confirmed") is not True or capture["recorded_phase_failures"] else 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError):
        parser.exit(2, "因果实验未完成；检查固定配置、私有凭据/来源文件、网络及全新输出目录。\n")


if __name__ == "__main__":
    raise SystemExit(main())
