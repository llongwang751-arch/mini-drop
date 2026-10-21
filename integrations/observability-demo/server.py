"""仓库自有的受控双服务：只记录实际 HTTP 工作，不生成静态成功样本。

gateway 真正调用 catalog；catalog 执行只读 SQLite 或纯 Python CPU 负载。
进程内遥测有界、重启即清空，仅供 Drop 的隔离演示，不承诺生产监控持久性。
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import uuid

SCENARIOS = {"normal", "slow_query", "cpu", "upstream_error", "timeout"}
TRACE_ID = re.compile(r"^[a-f0-9]{32}$")
SLOW_SQL = "SELECT sum((a.price_cents*b.units)%97) FROM items a CROSS JOIN items b WHERE (a.id+b.id)%7=0"


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_stamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("INVALID_TIMESTAMP")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("TIMESTAMP_TIMEZONE_REQUIRED")
    return result.astimezone(timezone.utc)


class Telemetry:
    """请求完成后才入库；没有请求的窗口返回空数组，不能造零值正常基线。"""

    def __init__(self, service_id, capacity=4096):
        self.service_id = service_id
        self.capacity = capacity
        self.requests = deque(maxlen=capacity)
        self.changes = deque(maxlen=128)
        self.dropped_until = None
        self.lock = threading.Lock()
        self.record_change("deployment", "observability-demo-v1")

    def record_change(self, kind, revision):
        with self.lock:
            self.changes.append({"timestamp": stamp(utcnow()), "change_type": kind, "revision": revision})

    def record(self, *, started, duration_ms, status_code, trace_id, event_code, span_count, error_count):
        row = {"timestamp": stamp(utcnow()), "started_at": stamp(started),
               "duration_ms": round(duration_ms, 6), "status_code": status_code,
               "trace_id": trace_id, "level": "error" if status_code >= 500 else "info",
               "event_code": event_code, "span_count": span_count, "error_count": error_count}
        with self.lock:
            if len(self.requests) == self.capacity:
                self.dropped_until = parse_stamp(self.requests[0]["timestamp"])
            self.requests.append(row)

    def query(self, signal, start, end, limit):
        with self.lock:
            rows = [dict(row) for row in self.requests if start <= parse_stamp(row["timestamp"]) <= end]
            changes = [dict(row) for row in self.changes if start <= parse_stamp(row["timestamp"]) <= end]
            truncated = self.dropped_until is not None and start <= self.dropped_until
        out = {"schema_version": 1, "service_id": self.service_id, "signal": signal, "truncated": truncated}
        if signal == "metrics":
            metrics = []
            if rows:
                # nearest-rank P95 和窗口吞吐量只由本窗口实际完成请求计算，不借用其他场景记录。
                durations = sorted(row["duration_ms"] for row in rows)
                p95 = durations[max(0, math.ceil(len(durations) * .95) - 1)]
                values = [("latency_p95_ms", p95, "milliseconds"),
                          ("error_rate", sum(row["status_code"] >= 500 for row in rows) / len(rows), "ratio"),
                          ("throughput_rps", len(rows) / (end - start).total_seconds(), "requests_per_second")]
                metrics = [{"name": name, "value": value, "unit": unit, "observed_at": rows[-1]["timestamp"]}
                           for name, value, unit in values]
            out["metrics"] = metrics
        elif signal == "logs":
            keys = ("timestamp", "level", "event_code", "duration_ms", "status_code", "trace_id")
            out["logs"] = [{key: row[key] for key in keys} for row in rows[-limit:]][::-1]
            out["truncated"] |= len(rows) > limit
        elif signal == "traces":
            # 整条请求按完成时间选窗，started_at 仍保留真实起点；跨窗口起点不伪装成窗口内发生。
            selected = [row for row in rows if start <= parse_stamp(row["started_at"]) <= end]
            bound = min(limit, 20)
            keys = ("trace_id", "duration_ms", "span_count", "error_count", "started_at")
            out["traces"] = [{key: row[key] for key in keys} for row in selected[-bound:]][::-1]
            out["truncated"] |= len(selected) > bound
        elif signal == "changes":
            bound = min(limit, 20)
            out["changes"] = changes[-bound:][::-1]
            out["truncated"] |= len(changes) > bound
        else:
            raise ValueError("INVALID_SIGNAL")
        return out


def initialize_catalog(path):
    """只生成明确合成的只读业务表；不加载外部数据、SQL 或 SQLite 扩展。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY,price_cents INTEGER NOT NULL,units INTEGER NOT NULL)")
        db.execute("DELETE FROM items")
        db.executemany("INSERT INTO items VALUES(?,?,?)", ((n, 100 + n % 900, 1 + n % 7) for n in range(1200)))
        db.commit()
    return path


def catalog_work(path, scenario):
    """两条固定 SQL 共用同一合成表，慢查询确实执行笛卡尔积；CPU 场景确实执行 Python 算法。"""
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=1)) as db:
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if scenario in {"slow_query", "timeout"}:
            checksum = 0
            for _ in range(4 if scenario == "slow_query" else 12):
                checksum += db.execute(SLOW_SQL).fetchone()[0]
            return checksum
        if scenario == "cpu":
            return python_cpu_hotspot()
        return db.execute("SELECT price_cents*units FROM items WHERE id=?", (17,)).fetchone()[0]


def python_cpu_hotspot():
    checksum = 0
    for n in range(450000):
        checksum = ((checksum << 3) ^ (n * 2654435761)) & 0xFFFFFFFF
    return checksum


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class DemoServer(ThreadingHTTPServer):
    """最多16个处理线程；工作请求另受四个并发槽限制，遥测内存也有固定上限。"""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, role, downstream=None, database=None):
        self.role = role
        self.downstream = downstream
        self.database = database
        self.telemetry = Telemetry(role)
        self.work_slots = threading.BoundedSemaphore(4)
        self.thread_slots = threading.BoundedSemaphore(16)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())
        super().__init__(address, DemoHandler)

    def process_request(self, request, client_address):
        if not self.thread_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.thread_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.thread_slots.release()

    def handle_error(self, request, client_address):
        # 断开的演示客户端不应把完整请求或异常栈写入容器日志；工作结果已在finally中记入有界遥测。
        pass


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "DropControlledDemo/1"

    def log_message(self, *_):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def respond(self, status, body):
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass

    def do_GET(self):
        try:
            if len(self.path) > 2048:
                raise ValueError("REQUEST_LIMIT")
            path = urlsplit(self.path)
            query = parse_qs(path.query, keep_blank_values=True, max_num_fields=8)
            if any(len(values) != 1 for values in query.values()):
                raise ValueError("DUPLICATE_PARAMETER")
            values = {key: items[0] for key, items in query.items()}
            if path.path == "/health" and not values:
                self.respond(200, {"healthy": True, "role": self.server.role, "pid": os.getpid(),
                                   "uid": os.getuid() if hasattr(os, "getuid") else None,
                                   "provenance": "synthetic_controlled_live_http_demo", "runtime_revision": "observability-demo-v1"})
                return
            if path.path == "/v1/signals":
                if set(values) != {"service", "signal", "start", "end", "limit"} or values["service"] != self.server.role:
                    raise ValueError("BOUND_SERVICE_REQUIRED")
                start, end = parse_stamp(values["start"]), parse_stamp(values["end"])
                limit = int(values["limit"])
                if not 0 < (end-start).total_seconds() <= 900 or not 1 <= limit <= 30:
                    raise ValueError("WINDOW_OR_LIMIT_REJECTED")
                self.respond(200, self.server.telemetry.query(values["signal"], start, end, limit))
                return
            route = "/demo/request" if self.server.role == "gateway" else "/work"
            if path.path != route:
                self.respond(404, {"error_code": "ROUTE_NOT_FOUND"})
                return
            if set(values) != {"scenario"} or values["scenario"] not in SCENARIOS:
                raise ValueError("SCENARIO_REQUIRED")
            self.work(values["scenario"])
        except (ValueError, OverflowError):
            self.respond(400, {"error_code": "BOUNDED_QUERY_REQUIRED"})

    def work(self, scenario):
        if not self.server.work_slots.acquire(blocking=False):
            self.respond(429, {"error_code": "DEMO_CONCURRENCY_LIMIT"})
            return
        started, tick = utcnow(), time.perf_counter()
        incoming = self.headers.get("X-Drop-Trace", "")
        trace_id = incoming if TRACE_ID.fullmatch(incoming) else uuid.uuid4().hex
        status, event, spans, errors = 200, "REQUEST_COMPLETE", 1, 0
        checksum = None
        try:
            if self.server.role == "catalog":
                if scenario == "upstream_error":
                    status, errors = 503, 1
                else:
                    if scenario in {"slow_query", "timeout"}:
                        event = "DB_SLOW_QUERY"
                    checksum = catalog_work(self.server.database, scenario)
            else:
                spans = 2  # 实际 server span 与实际发出的 HTTP client span。
                request = Request(self.server.downstream + "/work?" + urlencode({"scenario": scenario}), headers={"X-Drop-Trace": trace_id})
                try:
                    # timeout 场景收紧的是实际客户端deadline，不用sleep冒充网络超时。
                    with self.server.opener.open(request, timeout=.05 if scenario == "timeout" else 3) as response:
                        raw = response.read(8193)
                        if len(raw) > 8192:
                            raise ValueError("DOWNSTREAM_OUTPUT_LIMIT")
                        payload = json.loads(raw)
                        if payload.get("service") != "catalog" or payload.get("trace_id") != trace_id:
                            raise ValueError("DOWNSTREAM_IDENTITY_INVALID")
                        spans += int(payload["span_count"])
                        errors += int(payload["error_count"])
                        checksum = payload.get("checksum")
                except HTTPError as error:
                    error.close()
                    status, errors = 502, 2
                except (URLError, socket.timeout, TimeoutError):
                    status, event, errors = 504, "UPSTREAM_TIMEOUT", 2
        except (sqlite3.Error, ValueError, KeyError, TypeError):
            status, errors = 503, 1
        finally:
            duration_ms = (time.perf_counter()-tick)*1000
            self.server.telemetry.record(started=started, duration_ms=duration_ms, status_code=status,
                                         trace_id=trace_id, event_code=event, span_count=spans, error_count=errors)
            self.server.work_slots.release()
        self.respond(status, {"service": self.server.role, "scenario": scenario, "trace_id": trace_id,
                              "span_count": spans, "error_count": errors, "checksum": checksum,
                              "duration_ms": round(duration_ms, 6), "provenance": "synthetic_controlled_live_http_demo"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("gateway", "catalog"), required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--downstream", default="http://observability-catalog:8131")
    parser.add_argument("--database", type=Path, default=Path("/tmp/drop-observability-demo/catalog.sqlite"))
    args = parser.parse_args()
    target = urlsplit(args.downstream)
    if target.scheme != "http" or not target.hostname or target.username or target.password or target.path or target.query or target.fragment:
        parser.error("downstream must be an administrator-provided internal http origin")
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    database = initialize_catalog(args.database) if args.role == "catalog" else None
    server = DemoServer((args.host, args.port), args.role, args.downstream, database)
    print(json.dumps({"event": "controlled_demo_started", "role": args.role, "pid": os.getpid()}), flush=True)
    try:
        server.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
