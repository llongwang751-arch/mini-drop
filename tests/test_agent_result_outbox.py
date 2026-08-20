from __future__ import annotations

import json
import grpc

from agent.mini_drop_agent.result_outbox import ResultOutbox
from agent.mini_drop_agent.main import _deliver_outbox_entry


def test_outbox_roundtrip_and_acknowledge(tmp_path):
    outbox = ResultOutbox(str(tmp_path))
    entry = outbox.enqueue("task-1", True, "done", [{"artifact_type": "raw"}])
    pending = outbox.pending()
    assert len(pending) == 1
    assert pending[0].payload["schema_version"] == "mini-drop.notify-result.v1"
    assert pending[0].payload["task_id"] == "task-1"
    outbox.acknowledge(pending[0])
    assert outbox.pending() == []
    assert not entry.path.exists()


def test_same_task_replaces_existing_entry(tmp_path):
    outbox = ResultOutbox(str(tmp_path))
    outbox.enqueue("task-1", False, "first", [])
    outbox.enqueue("task-1", True, "second", [{"artifact_type": "top_json"}])
    pending = outbox.pending()
    assert len(pending) == 1
    assert pending[0].payload["ok"] is True
    assert pending[0].payload["reason"] == "second"


def test_corrupt_entry_is_quarantined(tmp_path):
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    outbox = ResultOutbox(str(tmp_path))
    assert outbox.pending() == []
    assert (tmp_path / "broken.corrupt").exists()


def test_outbox_bounds_pending_entries(tmp_path):
    outbox = ResultOutbox(str(tmp_path), max_entries=2)
    outbox.enqueue("task-1", True, "", [])
    outbox.enqueue("task-2", True, "", [])
    outbox.enqueue("task-3", True, "", [])
    assert len(outbox.pending()) == 2
    overflow = list(tmp_path.glob("*.overflow"))
    assert len(overflow) == 1
    assert json.loads(overflow[0].read_text(encoding="utf-8"))["task_id"] == "task-1"


def test_delivery_acknowledges_only_after_rpc_success(tmp_path, monkeypatch):
    outbox = ResultOutbox(str(tmp_path))
    entry = outbox.enqueue("task-1", True, "done", [])

    class Connection:
        channel = object()

        @staticmethod
        def call_with_retry(callback):
            return callback()

    monkeypatch.setattr("agent.mini_drop_agent.main._notify_result", lambda *args: None)
    monkeypatch.setattr(
        "agent.mini_drop_agent.main.hotmethod_pb2_grpc.HotmethodStub", lambda channel: object()
    )
    assert _deliver_outbox_entry(Connection(), outbox, entry) is True
    assert outbox.pending() == []


def test_failed_delivery_retains_entry_for_replay(tmp_path):
    outbox = ResultOutbox(str(tmp_path))
    entry = outbox.enqueue("task-1", True, "done", [])

    class TemporaryFailure(grpc.RpcError):
        def code(self):
            return grpc.StatusCode.UNAVAILABLE

        def details(self):
            return "server restarting"

    class Connection:
        channel = object()

        @staticmethod
        def call_with_retry(callback):
            raise TemporaryFailure()

    assert _deliver_outbox_entry(Connection(), outbox, entry) is False
    assert len(outbox.pending()) == 1
