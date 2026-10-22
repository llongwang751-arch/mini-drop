"""合成 stdout 协议负控；不运行采集、S3 或 Analyzer，不冒充真实链路证据。"""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest

from chain_receipt import FIXED_FIELDS, MAX_STDOUT_BYTES, PREFIX, ReceiptError, main, parse_stdout


def fixture(**changes):
    result = {**FIXED_FIELDS, 'schema_version': 1, 'passed': True, 'production_mutations': False,
              'raw_sha256': 'a' * 64, 'raw_size': 47, 'total_cpu_ticks': 99,
              'producer_binary_sha256': {'drop_runner_probe': 'b' * 64, 'drop_workload': 'c' * 64},
              'result_artifacts_verified': 3, 'analysis_success_events': 1}
    result.update(changes)
    return result


def stdout(receipt):
    return PREFIX + json.dumps(receipt).encode() + b'\n'


class ChainReceiptTests(unittest.TestCase):
    def test_valid_shape_and_strict_status_types(self):
        self.assertEqual(parse_stdout(stdout(fixture())), fixture())
        for changes in ({'passed': False}, {'passed': 'true'}, {'passed': 1}, {'schema_version': True},
                        {'schema_version': 1.0}, {'schema_version': 2}, {'production_mutations': 0}):
            with self.subTest(changes=changes), self.assertRaises(ReceiptError):
                parse_stdout(stdout(fixture(**changes)))

    def test_exact_single_marker_rejects_merged_progress_or_other_stdout(self):
        correct = stdout(fixture())
        for raw in (b'', b'progress ... ' + correct, b' ' + correct, correct + correct,
                    correct + b'raw profile\n', b'raw profile\n' + correct, b'x' * (MAX_STDOUT_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(ReceiptError):
                parse_stdout(raw)

    def test_invalid_json_duplicate_keys_and_unknown_raw_fields_fail_closed(self):
        for raw in (PREFIX + b'{bad}\n', PREFIX + b'{} {}\n', PREFIX + b'\xff\n',
                    PREFIX + b'{"passed":false,"passed":true}\n', PREFIX + b'{"x":NaN}\n',
                    stdout(fixture(raw_profile='private;work 1'))):
            with self.subTest(size=len(raw)), self.assertRaises(ReceiptError):
                parse_stdout(raw)

    def test_invalid_counts_digests_and_arbitrary_boundary_text_are_rejected(self):
        for changes in ({'raw_size': True}, {'raw_size': 0}, {'total_cpu_ticks': 2**53},
                        {'result_artifacts_verified': 2}, {'analysis_success_events': 2}, {'raw_sha256': 'private'},
                        {'producer_binary_sha256': {}}, {'scope': 'https://private-url'}, {'excluded_scope': []}):
            with self.subTest(changes=changes), self.assertRaises(ReceiptError):
                parse_stdout(stdout(fixture(**changes)))

    def test_cli_failure_never_echoes_raw_output_or_creates_success_receipt(self):
        secret = 'raw-profile-cookie-presigned-url-must-not-appear'
        with tempfile.TemporaryDirectory(prefix='drop-chain-parser-fixture-') as temporary:
            source, destination = Path(temporary) / 'stdout.private.log', Path(temporary) / 'receipt.json'
            source.write_bytes(b'progress ' + PREFIX + secret.encode())
            public, errors = io.StringIO(), io.StringIO()
            with redirect_stdout(public), redirect_stderr(errors):
                code = main([str(source), str(destination)])
            self.assertEqual(code, 1)
            self.assertFalse(destination.exists())
            self.assertFalse(public.getvalue())
            self.assertNotIn(secret, errors.getvalue())
            self.assertEqual(errors.getvalue().strip(), 'CHAIN_RECEIPT_MISSING_DUPLICATED_OR_POLLUTED')


if __name__ == '__main__':
    unittest.main()
