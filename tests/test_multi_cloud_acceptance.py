from __future__ import annotations

from scripts.run_multi_cloud_acceptance import run_agent, select_process


class FakeClient:
    def __init__(self):
        self.polls = 0

    def request(self, method, path, payload=None, **kwargs):
        if path.startswith("/api/top-processes"):
            return {"authoritative": True, "fresh": True, "items": [{"pid": 1}, {"pid": 42, "comm": "demo"}]}
        if method == "POST" and path == "/api/tasks":
            assert payload["collector_type"] == "sys_metrics"
            assert kwargs["idempotency_key"].startswith("multi-cloud-")
            return {"task_id": "task-live-1"}
        if path == "/api/tasks/task-live-1":
            self.polls += 1
            return {"status": "DONE", "collection_status": "COLLECTED", "analysis_status": "SUCCESS"}
        if path.endswith("/events"):
            return [{"sequence": 1, "to_status": "DONE"}]
        if path.endswith("/attempts"):
            return [{"attempt_id": "attempt-1", "status": "DONE"}]
        if path.endswith("/artifacts"):
            return [{"id": "artifact-1", "artifact_type": "sys_metrics", "sha256": "abc"}]
        raise AssertionError((method, path))


def test_select_process_avoids_pid_one_when_possible():
    assert select_process([{"pid": 1}, {"pid": 27}])["pid"] == 27


def test_run_agent_requires_real_task_lineage():
    report = run_agent(FakeClient(), "worker-a", timeout_seconds=1, duration_seconds=5)
    assert report["status"] == "DONE"
    assert report["target"] == {"pid": 42, "comm": "demo"}
    assert report["artifacts"][0]["artifact_type"] == "sys_metrics"
