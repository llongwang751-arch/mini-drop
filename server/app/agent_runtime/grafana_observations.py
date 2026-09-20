"""Bounded Grafana Prometheus adapter with explicit external provenance.

These service-window observations inform planning only. They are not forged
TaskAttempt/Artifact evidence and cannot directly pass the native claim gate.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from urllib.parse import quote, urlparse

import requests

from server.app.database import new_session
from server.app.models import DropInsightSessionModel


TEMPLATES = {
    "request_rate": 'sum(rate(http_server_request_duration_seconds_count{service_name=%s}[5m]))',
    "latency_p95": 'histogram_quantile(0.95,sum by(le)(rate(http_server_request_duration_seconds_bucket{service_name=%s}[5m])))',
}


def query_service_observations(diagnosis_id: str, metric: str) -> dict:
    if metric not in TEMPLATES:
        raise ValueError("UNKNOWN_METRIC")
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None or diagnosis.deleted_at is not None:
            raise ValueError("UNKNOWN_DIAGNOSIS")
        target = dict(diagnosis.target_json or {})
        window = dict(diagnosis.effective_time_range_json or diagnosis.time_range_json or {})
    finally:
        session.close()
    service = target.get("service")
    environment = target.get("environment")
    # This connector is pinned to one configured environment. A label supplied
    # by the model cannot expand the datasource or resource authority.
    if not service or not environment or environment != os.getenv("MINI_DROP_GRAFANA_ENVIRONMENT"):
        raise ValueError("CONNECTOR_SCOPE_UNAVAILABLE")
    base = os.getenv("MINI_DROP_GRAFANA_URL", "").rstrip("/")
    token = os.getenv("MINI_DROP_GRAFANA_TOKEN", "")
    uid = os.getenv("MINI_DROP_GRAFANA_PROMETHEUS_UID", "")
    parsed = urlparse(base)
    if not token or not uid or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("CONNECTOR_NOT_CONFIGURED")
    if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1"}:
        raise ValueError("CONNECTOR_REQUIRES_TLS")
    start = datetime.fromisoformat(window["start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(window["end"].replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None or not 0 < (end-start).total_seconds() <= 1800:
        raise ValueError("CONNECTOR_WINDOW_INVALID")
    # JSON escaping is appropriate here: PromQL quoted string literal, not shell.
    query = TEMPLATES[metric] % json.dumps(str(service), ensure_ascii=False)
    params = {"query": query, "start": start.timestamp(), "end": end.timestamp(), "step": 15}
    url = base + "/api/datasources/proxy/uid/" + quote(uid, safe="") + "/api/v1/query_range"
    try:
        with requests.get(url, params=params, headers={"Authorization": "Bearer " + token},
                          timeout=(3, 10), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError("CONNECTOR_HTTP_ERROR")
            body = bytearray()
            for block in response.iter_content(8192):
                body.extend(block)
                if len(body) > 32768:
                    raise ValueError("CONNECTOR_RESULT_TOO_LARGE")
            result = json.loads(body)
            if result.get("status") != "success" or result.get("data", {}).get("resultType") != "matrix":
                raise ValueError("CONNECTOR_RESULT_INVALID")
    except Exception:
        raise ValueError("CONNECTOR_QUERY_FAILED") from None
    return {"source_kind": "GRAFANA_PROMETHEUS", "metric": metric,
            "resource_identity": {"service": service, "environment": environment},
            "effective_window": window, "query": query,
            "payload_sha256": hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest(),
            "hash_encoding": "UTF8_SORTED_COMPACT_JSON", "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "unit": "requests/second" if metric == "request_rate" else "seconds",
            "payload": result, "is_evidence": False,
            "limitations": ["服务窗口级指标，不证明目标 PID 或 Span 归属；未进入原生 Evidence 门禁。",
                            "要求数据源使用 http_server_request_duration_seconds 指标及 service_name 标签。"]}
