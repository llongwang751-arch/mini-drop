import unittest
from unittest.mock import patch

from minidrop.collectors import (
    PerfCollector,
    PySpyCollector,
    ResourceCollector,
    _collapsed_stacks,
    _parse_perf_script,
    get_collector,
)


class CollectorTests(unittest.TestCase):
    def test_perf_script_and_collapsed_stack_parsers(self):
        perf = "python 1 0.0: cycles:\n\tabc work+0x1\n\tdef main+0x2\n\n"
        self.assertEqual(_parse_perf_script(perf)[0]["stack"], ["main", "work"])
        collapsed = _collapsed_stacks("module;work 7\ninvalid\n", "python")
        self.assertEqual(collapsed, [{"stack": ["python", "module", "work"], "value": 7}])

    @patch("minidrop.collectors._process_name", return_value="demo")
    @patch("minidrop.collectors._sample_resources", return_value=[{"rss_kb": 10}] * 3)
    def test_resource_collector_returns_visualizable_fallback(self, _samples, _name):
        result = ResourceCollector().collect(1, 1, 10)
        self.assertEqual(result["meta"]["collector"], "resource")
        self.assertEqual(result["stacks"][0]["stack"][-2], "demo")

    @patch("minidrop.collectors.shutil.which", return_value=None)
    @patch("minidrop.collectors._process_name", return_value="demo")
    @patch("minidrop.collectors._sample_resources", return_value=[{}])
    def test_perf_and_pyspy_expose_explicit_degraded_reason(self, _samples, _name, _which):
        perf = PerfCollector().collect(1, 1, 1)
        pyspy = PySpyCollector().collect(1, 1, 1)
        self.assertTrue(perf["meta"]["degraded"])
        self.assertEqual(perf["meta"]["reason"], "perf unavailable")
        self.assertTrue(pyspy["meta"]["degraded"])
        self.assertEqual(pyspy["stacks"][0]["stack"][0], "python")

    def test_collector_registry_rejects_unknown_plugin(self):
        self.assertIsInstance(get_collector("perf"), PerfCollector)
        with self.assertRaises(ValueError):
            get_collector("unknown")

