"""只用真实本机HTTP验证遥测协议；业务数据全部为合成，既不访问生产也不调用模型。"""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.parse import urlencode

from load import get_json, run_load, signals
from server import DemoServer, Telemetry, initialize_catalog, parse_stamp, stamp, utcnow


class DemoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        database = initialize_catalog(Path(cls.folder.name)/"catalog.sqlite")
        cls.catalog = DemoServer(("127.0.0.1", 0), "catalog", database=database)
        cls.catalog_origin = f"http://127.0.0.1:{cls.catalog.server_port}"
        cls.gateway = DemoServer(("127.0.0.1", 0), "gateway", downstream=cls.catalog_origin)
        cls.gateway_origin = f"http://127.0.0.1:{cls.gateway.server_port}"
        cls.threads = []
        for server in (cls.catalog, cls.gateway):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.threads.append(thread)

    @classmethod
    def tearDownClass(cls):
        for server in (cls.gateway, cls.catalog):
            server.shutdown()
            server.server_close()
        for thread in cls.threads:
            thread.join(timeout=2)
        cls.folder.cleanup()

    def test_01_no_request_does_not_create_normal_zero_metrics(self):
        end = utcnow()-timedelta(days=1)
        body = signals(self.gateway_origin, "gateway", "metrics", stamp(end-timedelta(seconds=5)), stamp(end))
        self.assertEqual(body["metrics"], [])

    def test_02_real_cross_service_trace_and_latency(self):
        normal = run_load(self.gateway_origin, "normal", 4)
        slow = run_load(self.gateway_origin, "slow_query", 4)
        normal_metrics = signals(self.gateway_origin, "gateway", "metrics", normal["start"], normal["end"])
        slow_metrics = signals(self.gateway_origin, "gateway", "metrics", slow["start"], slow["end"])
        self.assertEqual(normal["completed_requests"], 4)
        self.assertTrue(all(row["status_code"] == 200 for row in slow["client_responses"]))
        metric = lambda body, name: next(row for row in body["metrics"] if row["name"] == name)
        self.assertGreater(metric(slow_metrics, "latency_p95_ms")["value"], metric(normal_metrics, "latency_p95_ms")["value"]*5)
        self.assertEqual(metric(slow_metrics, "latency_p95_ms")["unit"], "milliseconds")
        self.assertEqual(metric(slow_metrics, "error_rate")["value"], 0)
        gateway = signals(self.gateway_origin, "gateway", "traces", slow["start"], slow["end"])
        catalog = signals(self.catalog_origin, "catalog", "traces", slow["start"], slow["end"])
        self.assertEqual(len(gateway["traces"]), 4)
        self.assertEqual({row["trace_id"] for row in gateway["traces"]}, {row["trace_id"] for row in catalog["traces"]})
        self.assertTrue(all(row["span_count"] == 3 and row["error_count"] == 0 for row in gateway["traces"]))
        logs = signals(self.catalog_origin, "catalog", "logs", slow["start"], slow["end"])
        self.assertTrue(all(row["event_code"] == "DB_SLOW_QUERY" for row in logs["logs"]))
        for row in logs["logs"]:
            self.assertLessEqual(parse_stamp(slow["start"]), parse_stamp(row["timestamp"]))
            self.assertLessEqual(parse_stamp(row["timestamp"]), parse_stamp(slow["end"]))

    def test_03_errors_are_measured_and_normal_recovers(self):
        failing = run_load(self.gateway_origin, "upstream_error", 3)
        body = signals(self.gateway_origin, "gateway", "metrics", failing["start"], failing["end"])
        self.assertTrue(all(row["status_code"] == 502 for row in failing["client_responses"]))
        self.assertEqual(next(row["value"] for row in body["metrics"] if row["name"] == "error_rate"), 1)
        recovered = run_load(self.gateway_origin, "normal", 3)
        self.assertTrue(all(row["status_code"] == 200 for row in recovered["client_responses"]))

    def test_04_query_scope_and_budget(self):
        now = utcnow()
        base = {"service":"gateway","signal":"metrics","start":stamp(now-timedelta(seconds=1)),"end":stamp(now),"limit":"30"}
        for change in ({"service":"catalog"},{"limit":"31"},{"limit":"0"},{"signal":"raw"},{"end":stamp(now+timedelta(minutes=16))},{"url":"http://example.invalid"}):
            params = dict(base, **change)
            status, _ = get_json(self.gateway_origin+"/v1/signals?"+urlencode(params))
            self.assertEqual(status, 400)
        status,_ = get_json(self.gateway_origin+"/demo/request?scenario=normal&sql=SELECT")
        self.assertEqual(status,400)

    def test_05_timeout_is_real_deadline_and_cpu_is_executed(self):
        cpu=run_load(self.gateway_origin,"cpu",1)
        self.assertEqual(cpu["client_responses"][0]["status_code"],200)
        timed=run_load(self.gateway_origin,"timeout",1)
        self.assertEqual(timed["client_responses"][0]["status_code"],504)
        logs=signals(self.gateway_origin,"gateway","logs",timed["start"],timed["end"])["logs"]
        self.assertEqual(logs[0]["event_code"],"UPSTREAM_TIMEOUT")
        trace_id=timed["client_responses"][0]["trace_id"]
        # 客户端deadline先到不代表下游已经结束；等真实SQL完成记录出现后再检查关联。
        found=False
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            rows=signals(self.catalog_origin,"catalog","logs",timed["start"],stamp(utcnow()))["logs"]
            if any(row["trace_id"]==trace_id and row["event_code"]=="DB_SLOW_QUERY" for row in rows):
                found=True
                break
            time.sleep(.02)
        self.assertTrue(found,"real downstream completion was not observed")


class TelemetryBoundsTests(unittest.TestCase):
    def test_retention_and_output_caps_are_explicit(self):
        telemetry = Telemetry("gateway", capacity=3)
        start=utcnow()-timedelta(seconds=1)
        for _ in range(5):
            telemetry.record(started=utcnow(),duration_ms=1,status_code=200,trace_id="a"*32,event_code="REQUEST_COMPLETE",span_count=1,error_count=0)
        result=telemetry.query("logs",start,utcnow()+timedelta(seconds=1),2)
        self.assertEqual(len(result["logs"]),2)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(telemetry.requests),3)

    def test_startup_change_is_real_start_time_not_a_request(self):
        before=utcnow()
        telemetry=Telemetry("catalog")
        after=utcnow()
        result=telemetry.query("changes",before,after+timedelta(microseconds=1),30)
        self.assertEqual(len(result["changes"]),1)
        self.assertEqual(result["changes"][0]["change_type"],"deployment")
        self.assertEqual(len(telemetry.requests),0)


if __name__ == "__main__":
    unittest.main()
