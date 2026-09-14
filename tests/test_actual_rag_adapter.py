"""Transport/privacy contracts do not require another user's checkout in CI."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from integrations.agi_saber.service import ActualRagService, STAGES, serve, trace_identity


def isolated_service():
    service = ActualRagService.__new__(ActualRagService)
    service.records = deque(maxlen=512)
    service.total = 0
    service.lock = threading.Lock()
    service.instance_id = "fixture-instance"
    service.source_sha256 = "a"*64
    service.rows = 0
    def answer(question):
        STAGES.get()["local_search"] = 1.25
        return "private answer", [{"pg_id": 7}], {"retrieval": {"query_paths": [{"mode": "local"}]}}
    service.engine = SimpleNamespace(query_with_history_trace=answer)
    return service


def test_trace_context_rejects_zero_or_malformed_ids_and_isolates_requests():
    trace, span = "a"*32, "b"*16
    assert trace_identity(f"00-{trace}-{span}-01") == (trace, span, "01")
    for value in [f"00-{'0'*32}-{span}-01", f"00-{trace}-{'0'*16}-01", "bad", "01-"+trace+"-"+span+"-01"]:
        assert trace_identity(value)[1] is None
    service = isolated_service()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: service.query("private question", f"00-{i+1:032x}-{span}-01"), range(24)))
    assert len({x["observation"]["trace_id"] for x in results}) == 24
    assert all(x["observation"]["stage_ms"] == {"local_search": 1.25} for x in results)
    assert STAGES.get() is None
    encoded = json.dumps(service.observations())
    assert "private question" not in encoded and "private answer" not in encoded


def test_failure_observations_and_bounded_retention_are_truthful():
    service = isolated_service()
    for _ in range(515):
        service.query("question")
    def failed(question):
        raise RuntimeError("secret provider message")
    service.engine.query_with_history_trace = failed
    with pytest.raises(RuntimeError):
        service.query("private question")
    data = service.observations()
    assert data["total_requests"] == 516 and data["retained_requests"] == 512
    assert data["records"][-1]["success"] is False
    assert "secret" not in json.dumps(data)


def test_http_only_exposes_bounded_query_and_redacted_observations():
    service = isolated_service()
    http = serve(service, 0)
    assert http.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{http.server_port}"
    try:
        req = Request(base+"/query", data=b'{"question":"private question"}', method="POST")
        with urlopen(req) as response:
            assert json.load(response)["answer"] == "private answer"
        for body, status in [(b'{"question":"q","url":"http://host"}', 400), (b'x'*4097, 413), (b'null', 400)]:
            with pytest.raises(HTTPError) as exc:
                urlopen(Request(base+"/query", data=body, method="POST"))
            assert exc.value.code == status
        with urlopen(base+"/observations") as response:
            data = response.read().decode()
            assert "private question" not in data and "private answer" not in data
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)
