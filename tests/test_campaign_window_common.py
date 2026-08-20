from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from scripts import campaign_window_common as common


def _waiting(step_id: str = "probe-1") -> dict[str, Any]:
    return {
        "data": {
            "status": "WAITING_APPROVAL",
            "probes": [
                {
                    "step_id": step_id,
                    "requires_approval": True,
                    "status": "WAITING_APPROVAL",
                }
            ],
        }
    }


def test_api_json_omits_authorization_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[common.Request] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"data": {}}'

    monkeypatch.delenv("MINI_DROP_API_KEY", raising=False)
    monkeypatch.setattr(
        common,
        "urlopen",
        lambda request, timeout: observed.append(request) or Response(),
    )

    assert common.api_json("http://control", "GET", "/api/healthz") == {"data": {}}
    assert observed[0].get_header("Authorization") is None


def test_api_json_preserves_bearer_authentication_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "protected-test-key"
    observed: list[common.Request] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self) -> bytes:
            return b'{"data": {}}'

    def fake_urlopen(request, timeout):
        observed.append(request)
        if len(observed) == 1:
            raise URLError("temporary outage")
        return Response()

    monkeypatch.setenv("MINI_DROP_API_KEY", secret)
    monkeypatch.setattr(common, "urlopen", fake_urlopen)
    monkeypatch.setattr(common.time, "sleep", lambda _seconds: None)

    common.api_json("http://control", "GET", "/api/healthz", retries=2)

    assert len(observed) == 2
    assert all(
        request.get_header("Authorization") == f"Bearer {secret}"
        for request in observed
    )


@pytest.mark.parametrize("error_kind", ("http", "url"))
def test_api_json_redacts_api_key_from_errors(
    monkeypatch: pytest.MonkeyPatch, error_kind: str,
) -> None:
    secret = "protected-test-key"
    monkeypatch.setenv("MINI_DROP_API_KEY", secret)

    def fake_urlopen(request, timeout):
        if error_kind == "http":
            raise HTTPError(
                request.full_url,
                401,
                "unauthorized",
                {},
                BytesIO(f"rejected {secret}".encode()),
            )
        raise URLError(f"connection rejected for {secret}")

    monkeypatch.setattr(common, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as captured:
        common.api_json("http://control", "GET", "/api/healthz", retries=1)

    assert secret not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)


def test_wait_for_terminal_approves_r2_once(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter((_waiting(), {"data": {"status": "COMPLETED"}}))
    approvals: list[dict[str, Any]] = []

    def api(_base: str, method: str, path: str, payload=None, **kwargs):
        if method == "GET":
            return next(responses)
        approvals.append({"path": path, "payload": payload, "retries": kwargs["retries"]})
        return {"data": {}}

    monkeypatch.setattr(common.time, "sleep", lambda _seconds: None)

    result = common.wait_for_terminal(
        "http://control",
        "diag-1",
        approve_r2=True,
        timeout_seconds=10,
        api=api,
    )

    assert result["data"]["status"] == "COMPLETED"
    assert approvals == [
        {
            "path": "/api/v1/diagnoses/diag-1/approvals",
            "payload": {
                "step_id": "probe-1",
                "decision": "approve",
                "scope": "single_execution",
                "approver_id": "benchmark-operator",
            },
            "retries": 2,
        }
    ]


def test_wait_for_terminal_surfaces_repeated_approval_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def api(_base: str, method: str, _path: str, payload=None, **kwargs):
        if method == "GET":
            return _waiting()
        raise RuntimeError("approval service unavailable")

    monkeypatch.setattr(common.time, "sleep", lambda _seconds: None)

    with pytest.raises(
        RuntimeError,
        match="R2 approval repeatedly failed for probe-1: approval service unavailable",
    ):
        common.wait_for_terminal(
            "http://control",
            "diag-1",
            approve_r2=True,
            timeout_seconds=10,
            api=api,
        )


def test_publish_submissions_replaces_target_only_after_complete_staging(
    tmp_path: Path,
) -> None:
    target = tmp_path / "submissions.json"
    target.write_text('[{"existing": true}]\n', encoding="utf-8")
    calls = 0

    def publisher(path: Path, submission: dict[str, Any], *, overwrite: bool):
        nonlocal calls
        calls += 1
        values = json.loads(path.read_text(encoding="utf-8"))
        values.append(submission)
        path.with_suffix(path.suffix + ".tmp").write_text(
            json.dumps(values), encoding="utf-8"
        )
        path.with_suffix(path.suffix + ".tmp").replace(path)
        return {"execution_id": submission["execution_id"], "path": str(path)}

    records = common.publish_submissions(
        target,
        [{"execution_id": "a"}, {"execution_id": "b"}],
        overwrite=False,
        publisher=publisher,
    )

    assert calls == 2
    assert json.loads(target.read_text(encoding="utf-8")) == [
        {"existing": True},
        {"execution_id": "a"},
        {"execution_id": "b"},
    ]
    assert [item["execution_id"] for item in records] == ["a", "b"]
    assert all(item["path"] == str(target.resolve()) for item in records)
    assert not target.with_suffix(target.suffix + ".window.tmp").exists()
    assert not target.with_suffix(target.suffix + ".window.tmp.tmp").exists()


def test_publish_submissions_preserves_target_and_removes_nested_temp_on_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "submissions.json"
    original = "[]\n"
    target.write_text(original, encoding="utf-8")
    calls = 0

    def publisher(path: Path, submission: dict[str, Any], *, overwrite: bool):
        nonlocal calls
        calls += 1
        nested = path.with_suffix(path.suffix + ".tmp")
        nested.write_text("partial", encoding="utf-8")
        if calls == 2:
            raise RuntimeError("staging interrupted")
        nested.replace(path)
        return {"execution_id": submission["execution_id"]}

    with pytest.raises(RuntimeError, match="staging interrupted"):
        common.publish_submissions(
            target,
            [{"execution_id": "a"}, {"execution_id": "b"}],
            overwrite=False,
            publisher=publisher,
        )

    assert target.read_text(encoding="utf-8") == original
    assert not target.with_suffix(target.suffix + ".window.tmp").exists()
    assert not target.with_suffix(target.suffix + ".window.tmp.tmp").exists()
