import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from minidrop.server import create_app


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app("sqlite:///:memory:", start_watcher=False)
        self.client = TestClient(self.app)
        self.client.post("/api/agents/demo/heartbeat", json={"hostname": "test", "version": "1"})

    def tearDown(self):
        self.client.close()
        self.app.state.store.engine.dispose()

    def create_and_claim(self, collector="perf"):
        response = self.client.post("/api/tasks", json={
            "agent_id": "demo", "pid": 1, "duration": 1, "rate": 1, "collector": collector
        })
        self.assertEqual(response.status_code, 201)
        task = response.json()
        claimed = self.client.post("/api/agents/demo/claim", json={}).json()
        return task, claimed

    def test_normal_path_reaches_done_with_analysis(self):
        task, _ = self.create_and_claim()
        response = self.client.post(f"/api/tasks/{task['id']}/upload", json={
            "reason": "profile ready",
            "raw": {"stacks": [{"stack": ["main", "work"], "value": 4}], "samples": []},
        })
        self.assertEqual(response.status_code, 200)
        done = response.json()
        self.assertEqual(done["status"], "DONE")
        self.assertIsNotNone(done["result"]["flamegraph"])
        self.assertEqual(len(done["transitions"]), 4)

    def test_process_collection_failure_reaches_failed(self):
        task, _ = self.create_and_claim()
        failed = self.client.post(f"/api/tasks/{task['id']}/fail", json={
            "reason": "ProcessLookupError: pid 999999 does not exist"
        }).json()
        self.assertEqual(failed["status"], "FAILED")
        self.assertIn("ProcessLookupError", failed["reason"])

    def test_unsupported_collector_failure_is_persisted(self):
        task, _ = self.create_and_claim("unknown")
        failed = self.client.post(f"/api/tasks/{task['id']}/fail", json={
            "reason": "ValueError: unsupported collector: unknown"
        }).json()
        fetched = self.client.get(f"/api/tasks/{task['id']}").json()
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(fetched["transitions"][-1]["reason"], "ValueError: unsupported collector: unknown")

    def test_natural_language_request_creates_a_verifiable_plan(self):
        response = self.client.post("/api/natural-language", json={
            "text": "对 PID 1 做 2 秒 eBPF IO 采集，频率 19Hz"
        })
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["plan"]["collector"], "ebpf")
        self.assertEqual(data["task"]["status"], "PENDING")

    def test_continuous_task_creates_next_slice_and_window(self):
        task = self.client.post("/api/tasks", json={
            "agent_id": "demo", "pid": 1, "duration": 1, "rate": 1, "collector": "perf", "continuous": True
        }).json()
        self.client.post("/api/agents/demo/claim", json={})
        self.client.post(f"/api/tasks/{task['id']}/upload", json={
            "raw": {"stacks": [{"stack": ["main"], "value": 1}]}
        })
        window = self.client.get("/api/continuous/demo").json()
        tasks = self.client.get("/api/tasks").json()
        self.assertEqual(len(window), 1)
        self.assertTrue(any(x["status"] == "PENDING" and x["continuous"] for x in tasks))
        stopped = self.client.post(f"/api/tasks/{task['id']}/stop-continuous", json={}).json()
        self.assertGreaterEqual(stopped["stopped"], 2)
        self.assertFalse(any(x["continuous"] for x in self.client.get("/api/tasks").json()))

    def test_payloads_are_read_back_from_object_storage(self):
        task, _ = self.create_and_claim()
        self.client.post(f"/api/tasks/{task['id']}/upload", json={
            "reason": "profile ready",
            "raw": {"stacks": [{"stack": ["main", "storage"], "value": 3}], "samples": []},
        })
        fetched = self.client.get(f"/api/tasks/{task['id']}").json()
        self.assertEqual(fetched["raw_data"]["stacks"][0]["stack"], ["main", "storage"])
        self.assertEqual(fetched["result"]["top"][0]["name"], "main")

    def test_schedule_creates_pending_task(self):
        response = self.client.post("/api/schedules", json={
            "agent_id": "demo", "pid": 1, "duration": 1, "rate": 1, "collector": "perf",
            "continuous": False, "interval_seconds": 30
        })
        self.assertEqual(response.status_code, 201)
        created = self.client.post("/api/schedules/run-due", json={}).json()
        self.assertEqual(len(created["created"]), 1)
        self.assertEqual(created["created"][0]["status"], "PENDING")

    def test_optional_api_key_auth(self):
        with patch.dict("os.environ", {"MINIDROP_API_KEY": "secret"}):
            app = create_app("sqlite:///:memory:", start_watcher=False)
            client = TestClient(app)
            try:
                self.assertEqual(client.get("/api/health").status_code, 200)
                self.assertEqual(client.get("/api/tasks").status_code, 401)
                self.assertEqual(client.get("/api/tasks", headers={"X-MiniDrop-Token": "secret"}).status_code, 200)
            finally:
                client.close()
                app.state.store.engine.dispose()
