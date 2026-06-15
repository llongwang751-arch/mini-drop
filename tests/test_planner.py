import unittest

from minidrop.planner import plan_collection


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.agents = [{"id": "local-agent", "online": 1}, {"id": "offline", "online": 0}]

    def test_natural_language_selects_tool_and_parameters(self):
        plan = plan_collection("用 py-spy 对 PID 1234 持续采样 8 秒，频率 77Hz", self.agents)
        self.assertEqual(plan["collector"], "pyspy")
        self.assertEqual(plan["pid"], 1234)
        self.assertEqual(plan["duration"], 8)
        self.assertEqual(plan["rate"], 77)
        self.assertTrue(plan["continuous"])

    def test_natural_language_requires_verifiable_target(self):
        with self.assertRaisesRegex(ValueError, "target PID"):
            plan_collection("帮我看看 Python 为什么慢", self.agents)
