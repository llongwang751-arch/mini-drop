"""明确合成的统计、资源读数及profile负控；不把单测当成真实采集性能结果。"""
import unittest
from benchmark import evaluate, percentile, profile_counts, target_process
from target import EXPECTED
import json
from urllib.request import ProxyHandler, build_opener


class MeasurementTests(unittest.TestCase):
    def test_percentiles_have_nearest_rank_and_empty_is_unknown(self):
        self.assertIsNone(percentile([], 99))
        self.assertEqual(percentile(list(range(1, 101)), 95), 95)
        self.assertEqual(percentile([1, 2, 100], 99), 100)

    def test_raw_weight_preservation_including_unresolved_stack(self):
        result = profile_counts(b'root;work 12\n 1\n')
        self.assertEqual(result['observed_stack_weights'], 13)
        self.assertEqual(result['unresolved_stack_weights'], 1)
        self.assertIsNone(result['lost_samples'])

    def test_raw_empty_negative_and_unbounded_rejected(self):
        for body in (b'', b'root -1\n', b'root 1.2\n', b'root 999999999999\n', b'x' * (2 * 1024 * 1024 + 1)):
            with self.subTest(size=len(body)), self.assertRaises(ValueError):
                profile_counts(body)

    def test_real_owned_http_target_has_fixed_checksum(self):
        with target_process() as (pid, port):
            self.assertGreater(pid, 0)
            with build_opener(ProxyHandler({})).open(f'http://127.0.0.1:{port}/work', timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response)['checksum'], EXPECTED)

    def test_gate_rejects_missing_results_without_dividing_by_zero(self):
        rows = [{'repeat': repeat, 'sampling_hz': rate, 'latency_ms': {'p95': None}, 'throughput_rps': 0,
                 'failures': {'HTTP_REQUEST_FAILED': 1}, 'successful_requests': 0,
                 'target_peak_rss_bytes': None, 'sampling': None, 'collector_peak_rss_bytes': None}
                for repeat in range(3) for rate in (0, 49, 99)]
        result = evaluate(rows, [], rows[0])
        self.assertFalse(result['passed'])
        self.assertIsNone(result['paired_sampling_effect']['49']['median_p95_ratio'])

    def test_gate_detects_actual_policy_regression_and_preserves_ratios(self):
        rows = [{'repeat': repeat, 'sampling_hz': rate, 'latency_ms': {'p95': 5 if rate == 0 else 20},
                 'throughput_rps': 100 if rate == 0 else 20, 'failures': {}, 'successful_requests': 20,
                 'target_peak_rss_bytes': 1024, 'sampling': {'observed_stack_weights': 10}, 'collector_peak_rss_bytes': 2048}
                for repeat in range(3) for rate in (0, 49, 99)]
        result = evaluate(rows, [], rows[0])
        self.assertFalse(result['passed'])
        self.assertTrue(result['checks']['requests_and_oracle'])
        self.assertEqual(result['paired_sampling_effect']['49']['median_p95_ratio'], 4)


if __name__ == '__main__':
    unittest.main()
