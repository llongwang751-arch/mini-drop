"""低基数 Prometheus 指标与可选的短 OpenTelemetry span，不把任务标识变成指标维度。"""

from collections import defaultdict
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

from .config import Config

_lock = threading.Lock()
_values = defaultdict(float)
_tracer = None


def count(name, value=1, *, kind="all", status="all"):
    # 指标标签只使用已登记的 kind/status 常量，不使用任务 ID 或文件路径。
    with _lock:
        _values[(name, kind, status)] += value


def observe(name, value, **labels):
    count(name + "_count", 1, **labels)
    count(name + "_sum", value, **labels)


def gauge(name, value, *, kind="all", status="all"):
    with _lock:
        _values[(name, kind, status)] = float(value)


def render():
    with _lock:
        rows = sorted(_values.items())
    return "".join(
        f'drop_analyzer_{name}{{kind="{kind}",status="{status}"}} {value}\n'
        for (name, kind, status), value in rows
    ).encode()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = render()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def start(config=None):
    global _tracer
    config = config or Config.load([], require_credentials=False)
    port = config.metrics_port
    if port:
        server = ThreadingHTTPServer((config.metrics_host, port), Handler)
        threading.Thread(
            target=server.serve_forever, daemon=True, name="analyzer-metrics"
        ).start()
    endpoint = config.otel_endpoint
    if endpoint:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.exporter.otlp.proto.http import Compression
        import requests
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": "drop-analyzer"})
        )
        session = requests.Session()
        session.trust_env = False
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=endpoint,
                    timeout=5,
                    headers={"User-Agent": "drop-analyzer"},
                    certificate_file=True,
                    compression=Compression.NoCompression,
                    session=session,
                ),
                max_queue_size=256,
                max_export_batch_size=32,
            )
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("drop-analyzer")


@contextmanager
def analysis_span(job):
    if _tracer is None:
        yield
        return
    from opentelemetry.trace import Link
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )
    from opentelemetry import trace

    carrier = {"traceparent": job.params.get("traceparent", "")}
    remote = trace.get_current_span(
        TraceContextTextMapPropagator().extract(carrier)
    ).get_span_context()
    links = [Link(remote)] if remote.is_valid else []
    with _tracer.start_as_current_span(
        "analyze.profile",
        links=links,
        attributes={
            "drop.task.id": str(job.task_id),
            "drop.attempt.id": str(job.attempt_id),
            "drop.kind": job.kind,
            "drop.pipeline": job.pipeline,
        },
    ):
        yield
