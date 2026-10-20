"""验证指标与链路观测的字段约束，避免任务标识和敏感值进入高基数标签。"""

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import importlib.util
import os
import subprocess
import sys
import threading
import unittest
import urllib.request

from drop_analyzer import telemetry


class TelemetryTests(unittest.TestCase):
    def test_actual_metrics_http_contains_bounded_labels(self):
        telemetry.count("test_jobs_total", kind="GO_CPU", status="success")
        server = ThreadingHTTPServer(("127.0.0.1", 0), telemetry.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{server.server_port}/metrics", timeout=2
            ) as response:
                body = response.read().decode()
            self.assertIn(
                'drop_analyzer_test_jobs_total{kind="GO_CPU",status="success"}', body
            )
            self.assertNotIn("task_id", body)
            self.assertNotIn("object_key", body)
        finally:
            server.shutdown()
            server.server_close()

    @unittest.skipUnless(
        importlib.util.find_spec("opentelemetry")
        and importlib.util.find_spec("opentelemetry.sdk"),
        "Install runtime OTEL dependencies",
    )
    def test_actual_otlp_span_contains_task_link(self):
        bodies = []

        class Collector(BaseHTTPRequestHandler):
            def do_POST(self):
                bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            script = """from drop_analyzer import telemetry
from types import SimpleNamespace
from opentelemetry import trace
telemetry.start()
job=SimpleNamespace(task_id=123,attempt_id=7,kind="GO_CPU",pipeline="pprof_cpu_v1",params={"traceparent":"00-11111111111111111111111111111111-2222222222222222-01"})
with telemetry.analysis_span(job): pass
trace.get_tracer_provider().force_flush(timeout_millis=2000)
trace.get_tracer_provider().shutdown()
"""
            result = subprocess.run(
                [sys.executable, "-c", script],
                env={
                    **os.environ,
                    "ANALYZER_METRICS_PORT": "0",
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"http://127.0.0.1:{server.server_port}/v1/traces",
                },
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
                ExportTraceServiceRequest,
            )

            message = ExportTraceServiceRequest.FromString(bodies[0])
            spans = [
                span
                for resource in message.resource_spans
                for scope in resource.scope_spans
                for span in scope.spans
            ]
            self.assertEqual(spans[0].name, "analyze.profile")
            self.assertEqual(spans[0].links[0].trace_id, bytes.fromhex("11" * 16))
            self.assertNotEqual(spans[0].trace_id, spans[0].links[0].trace_id)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
