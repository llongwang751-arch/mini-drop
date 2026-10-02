import asyncio
import json

from integrations.agi_saber.application_metrics import ApplicationMetricsMiddleware


def test_asgi_metrics_publish_bounded_request_aggregates(tmp_path):
    async def application(scope, receive, send):
        await send({"type": "http.response.start", "status": 503, "headers": []})
        await send({"type": "http.response.body", "body": b"failed"})

    path = tmp_path / "metrics.json"
    middleware = ApplicationMetricsMiddleware(application, str(path))
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"secret payload"}

    async def send(message):
        sent.append(message)

    asyncio.run(middleware({"type": "http", "path": "/private"}, receive, send))
    snapshot = json.loads(path.read_text(encoding="utf-8"))

    assert snapshot["schema_version"] == "mini-drop.application-metrics.v1"
    assert snapshot["pid"] > 0
    assert snapshot["http_requests"] == 1
    assert snapshot["http_failures"] == 1
    assert snapshot["http_inflight_requests"] == 0
    assert snapshot["http_duration_ms"] >= 0
    assert "path" not in snapshot
    assert "secret payload" not in path.read_text(encoding="utf-8")
