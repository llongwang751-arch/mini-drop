import tempfile
import unittest
from pathlib import Path

from minidrop.server import App


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = App(str(Path(self.tmp.name) / "e2e.db"))
        self.app.routes("POST", "/api/agents/demo/heartbeat", {"hostname": "test", "version": "1"})

    def tearDown(self):
        self.tmp.cleanup()

    def create_and_claim(self, collector="perf"):
        status, task = self.app.routes("POST", "/api/tasks", {
            "agent_id": "demo", "pid": 1, "duration": 1, "rate": 1, "collector": collector
        })
        self.assertEqual(status, 201)
        _, claimed = self.app.routes("POST", "/api/agents/demo/claim", {})
        return task, claimed

    def test_normal_path_reaches_done_with_analysis(self):
        task, _ = self.create_and_claim()
        status, done = self.app.routes("POST", f"/api/tasks/{task['id']}/upload", {
            "reason": "profile ready",
            "raw": {"stacks": [{"stack": ["main", "work"], "value": 4}], "samples": []},
        })
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "DONE")
        self.assertIsNotNone(done["result"]["flamegraph"])
        self.assertEqual(len(done["transitions"]), 4)

    def test_process_collection_failure_reaches_failed(self):
        task, _ = self.create_and_claim()
        _, failed = self.app.routes("POST", f"/api/tasks/{task['id']}/fail", {
            "reason": "ProcessLookupError: pid 999999 does not exist"
        })
        self.assertEqual(failed["status"], "FAILED")
        self.assertIn("ProcessLookupError", failed["reason"])

    def test_unsupported_collector_failure_is_persisted(self):
        task, _ = self.create_and_claim("unknown")
        _, failed = self.app.routes("POST", f"/api/tasks/{task['id']}/fail", {
            "reason": "ValueError: unsupported collector: unknown"
        })
        fetched = self.app.routes("GET", f"/api/tasks/{task['id']}", {})[1]
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(fetched["transitions"][-1]["reason"], "ValueError: unsupported collector: unknown")

    def test_continuous_task_creates_next_slice_and_window(self):
        _, task = self.app.routes("POST", "/api/tasks", {
            "agent_id": "demo", "pid": 1, "duration": 1, "rate": 1, "collector": "perf", "continuous": True
        })
        self.app.routes("POST", "/api/agents/demo/claim", {})
        self.app.routes("POST", f"/api/tasks/{task['id']}/upload", {
            "raw": {"stacks": [{"stack": ["main"], "value": 1}]}
        })
        window = self.app.routes("GET", "/api/continuous/demo", {})[1]
        tasks = self.app.routes("GET", "/api/tasks", {})[1]
        self.assertEqual(len(window), 1)
        self.assertTrue(any(x["status"] == "PENDING" and x["continuous"] for x in tasks))
