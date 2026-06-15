import unittest

from sqlalchemy import update

from minidrop.db import AgentModel, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = Store("sqlite:///:memory:")

    def tearDown(self):
        self.store.engine.dispose()

    def test_state_machine_requires_valid_transition_and_reason(self):
        task = self.store.create_task("a", 1, 1, 1, "perf")
        with self.assertRaises(ValueError):
            self.store.transition(task["id"], "DONE", "skip")
        with self.assertRaises(ValueError):
            self.store.transition(task["id"], "RUNNING", "")

    def test_offline_and_recovery_are_audited(self):
        self.store.heartbeat("a", "host", "v1", {})
        with self.store.Session.begin() as session:
            session.execute(update(AgentModel).where(AgentModel.id == "a").values(last_seen=0))
        self.store.mark_offline(30)
        self.store.heartbeat("a", "host", "v1", {})
        kinds = [x["kind"] for x in self.store.list_audit()]
        self.assertIn("AGENT_OFFLINE", kinds)
        self.assertGreaterEqual(kinds.count("AGENT_ONLINE"), 2)
