"""合成 Docker 子进程负控；不启动 Docker、不执行采样，也不把这些结果当性能测量。"""

from contextlib import nullcontext, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_isolated


OWNER = 'a' * 32
IMAGE_ID = 'sha256:' + 'b' * 64
PRIVATE = 'secret-profile-or-control-stderr-must-not-appear'


class IsolationFailureReceiptTests(unittest.TestCase):
    def run_fixture(self, mode='success'):
        calls = []
        with tempfile.TemporaryDirectory(prefix='drop-quality-driver-fixture-') as temporary:
            output = Path(temporary) / 'new-output'

            def invoke(argv, **kwargs):
                calls.append(argv)
                if argv[:3] == ['docker', 'image', 'inspect']:
                    if mode == 'image_control':
                        return SimpleNamespace(returncode=1, stdout=b'', stderr=PRIVATE.encode())
                    return SimpleNamespace(returncode=0, stdout=(IMAGE_ID + '\n').encode(), stderr=b'')
                if argv[:2] == ['docker', 'run']:
                    if mode == 'timeout':
                        raise subprocess.TimeoutExpired([PRIVATE], 300)
                    if mode == 'driver_io':
                        raise OSError(PRIVATE)
                    if mode != 'missing_report':
                        gate = False if mode == 'gate_false' else 'true' if mode == 'gate_not_bool' else True
                        report = {'schema_version': 1, 'provenance': 'explicit synthetic driver fixture', 'gate': {'passed': gate}}
                        (output / 'performance.json').write_text(json.dumps(report), encoding='utf-8')
                    return SimpleNamespace(returncode=9 if mode == 'child_nonzero' else 0)
                if argv[:2] == ['docker', 'inspect']:
                    if '--format' in argv:
                        if mode == 'cleanup_timeout':
                            raise subprocess.TimeoutExpired([PRIVATE], 15)
                        if mode == 'image_control':
                            return SimpleNamespace(returncode=1, stdout=b'', stderr=PRIVATE.encode())
                        owner = 'foreign' if mode in ('foreign_owner', 'foreign_cleanup') else OWNER
                        return SimpleNamespace(returncode=0, stdout=owner.encode(), stderr=b'')
                    if mode == 'inspect_control':
                        return SimpleNamespace(returncode=1, stdout=b'', stderr=PRIVATE.encode())
                    if mode == 'inspect_timeout':
                        raise subprocess.TimeoutExpired([PRIVATE], 30)
                    if mode == 'inspect_invalid':
                        return SimpleNamespace(returncode=0, stdout=PRIVATE.encode(), stderr=b'')
                    row = {'Config': {'Labels': {'drop.jd.quality.run': 'foreign' if mode == 'foreign_owner' else OWNER}},
                           'HostConfig': {'NetworkMode': 'host' if mode == 'isolation_mismatch' else 'none', 'Memory': 384 * 1024 * 1024,
                                          'NanoCpus': 750000000, 'PidsLimit': 64}, 'State': {'OOMKilled': mode == 'oom'}}
                    return SimpleNamespace(returncode=0, stdout=json.dumps([row]).encode(), stderr=b'')
                if argv[:3] == ['docker', 'rm', '-f']:
                    return SimpleNamespace(returncode=1 if mode == 'cleanup_control' else 0, stdout=b'', stderr=PRIVATE.encode())
                raise AssertionError('unexpected fixture command')

            captured = io.StringIO()
            with patch.object(sys, 'argv', ['run_isolated.py', '--output', str(output)]), \
                 patch.object(run_isolated.subprocess, 'run', side_effect=invoke), \
                 patch.object(run_isolated.uuid, 'uuid4', return_value=SimpleNamespace(hex=OWNER)), \
                 (patch.object(run_isolated.os, 'replace', side_effect=OSError(PRIVATE)) if mode == 'receipt_write' else nullcontext()), \
                 redirect_stdout(captured):
                code = run_isolated.main()
            if mode == 'receipt_write':
                self.assertFalse((output / 'isolation.json').exists())
                self.assertFalse(list(output.glob('.isolation-*.tmp')))
                self.assertNotIn(PRIVATE, captured.getvalue())
                return code, json.loads(captured.getvalue()), calls
            receipt = json.loads((output / 'isolation.json').read_text(encoding='utf-8'))
            self.assertNotIn(PRIVATE, captured.getvalue())
            self.assertNotIn(PRIVATE, json.dumps(receipt))
            self.assertFalse(list(output.glob('.isolation-*.tmp')))
            source = Path(run_isolated.__file__)
            self.assertEqual(receipt['source_sha256'][source.name], hashlib.sha256(source.read_bytes()).hexdigest())
            return code, receipt, calls

    def test_success_records_atomic_receipt_and_exact_owned_cleanup(self):
        code, receipt, calls = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertTrue(receipt['passed'])
        self.assertEqual(receipt['image_id'], IMAGE_ID)
        self.assertEqual(len(receipt['report_sha256']), 64)
        self.assertEqual(receipt['cleanup'], {'status': 'owned_container_removed', 'error_code': None})
        self.assertEqual(sum(call[:3] == ['docker', 'rm', '-f'] for call in calls), 1)

    def test_timeout_keeps_failure_receipt_and_removes_only_owned_container(self):
        code, receipt, calls = self.run_fixture('timeout')
        self.assertEqual(code, 1)
        self.assertFalse(receipt['passed'])
        self.assertTrue(receipt['timed_out'])
        self.assertEqual(receipt['error_code'], 'ISOLATED_TIMEOUT')
        self.assertEqual(receipt['image_id'], IMAGE_ID)
        self.assertEqual(receipt['cleanup']['status'], 'owned_container_removed')
        self.assertEqual(sum(call[:3] == ['docker', 'rm', '-f'] for call in calls), 1)

    def test_foreign_owner_is_never_deleted_even_after_apparent_success(self):
        for mode in ('foreign_owner', 'foreign_cleanup'):
            with self.subTest(mode=mode):
                code, receipt, calls = self.run_fixture(mode)
                self.assertEqual(code, 1)
                self.assertFalse(receipt['passed'])
                self.assertEqual(receipt['error_code'], 'FIXTURE_OWNERSHIP_MISMATCH')
                self.assertEqual(receipt['cleanup']['status'], 'foreign_owner_preserved')
                self.assertFalse(any(call[:3] == ['docker', 'rm', '-f'] for call in calls))

    def test_control_and_inspection_failures_are_fixed_and_persisted(self):
        for mode, expected in (('image_control', 'ISOLATED_CONTROL_FAILED'), ('inspect_control', 'ISOLATED_CONTROL_FAILED'),
                               ('inspect_invalid', 'ISOLATED_INSPECTION_INVALID'), ('driver_io', 'ISOLATED_DRIVER_IO_FAILED')):
            with self.subTest(mode=mode):
                code, receipt, _ = self.run_fixture(mode)
                self.assertEqual(code, 1)
                self.assertFalse(receipt['passed'])
                self.assertEqual(receipt['error_code'], expected)
                if mode == 'image_control':
                    self.assertIsNone(receipt['image_id'])

    def test_inspection_timeout_preserves_existing_report_without_claiming_success(self):
        code, receipt, _ = self.run_fixture('inspect_timeout')
        self.assertEqual(code, 1)
        self.assertTrue(receipt['timed_out'])
        self.assertEqual(receipt['error_code'], 'ISOLATED_TIMEOUT')
        self.assertTrue(receipt['completed'])
        self.assertIn('report_sha256', receipt)
        self.assertEqual(receipt['cleanup']['status'], 'owned_container_removed')

    def test_failed_cleanup_cannot_leave_a_success_receipt(self):
        for mode in ('cleanup_timeout', 'cleanup_control'):
            with self.subTest(mode=mode):
                code, receipt, _ = self.run_fixture(mode)
                self.assertEqual(code, 1)
                self.assertFalse(receipt['passed'])
                self.assertEqual(receipt['error_code'], 'ISOLATED_CLEANUP_FAILED')

    def test_success_report_cannot_override_process_isolation_or_oom_failure(self):
        for mode, expected in (('child_nonzero', 'MEASUREMENT_PROCESS_FAILED'), ('isolation_mismatch', 'FIXTURE_ISOLATION_MISMATCH'), ('oom', 'MEASUREMENT_OOM')):
            with self.subTest(mode=mode):
                code, receipt, _ = self.run_fixture(mode)
                self.assertEqual(code, 1)
                self.assertFalse(receipt['passed'])
                self.assertEqual(receipt['error_code'], expected)
                self.assertTrue(receipt['completed'])
                self.assertIn('report_sha256', receipt)

    def test_missing_false_or_truthy_string_report_never_passes(self):
        for mode, expected in (('missing_report', 'ISOLATED_REPORT_MISSING'), ('gate_false', 'MEASUREMENT_GATE_FAILED'), ('gate_not_bool', 'ISOLATED_REPORT_INVALID')):
            with self.subTest(mode=mode):
                code, receipt, _ = self.run_fixture(mode)
                self.assertEqual(code, 1)
                self.assertFalse(receipt['passed'])
                self.assertEqual(receipt['error_code'], expected)

    def test_atomic_writer_failure_removes_temporary_file(self):
        with tempfile.TemporaryDirectory(prefix='drop-quality-atomic-fixture-') as temporary:
            output = Path(temporary)
            with patch.object(run_isolated.os, 'replace', side_effect=OSError(PRIVATE)), self.assertRaises(OSError):
                run_isolated.save_receipt(output, {'passed': False, 'error_code': 'fixture'})
            self.assertFalse(list(output.iterdir()))

    def test_unwritable_receipt_returns_fixed_terminal_failure_after_cleanup(self):
        code, terminal, calls = self.run_fixture('receipt_write')
        self.assertEqual(code, 1)
        self.assertFalse(terminal['passed'])
        self.assertEqual(terminal['error_code'], 'ISOLATED_RECEIPT_WRITE_FAILED')
        self.assertEqual(sum(call[:3] == ['docker', 'rm', '-f'] for call in calls), 1)


if __name__ == '__main__':
    unittest.main()
