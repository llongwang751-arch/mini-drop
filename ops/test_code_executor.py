"""验证源码代理的登记边界、补丁限制和取消归属。

使用合成源码及 Docker 控制夹具，不访问模型或真实业务仓库；需要 Linux 的内核行为单独受平台条件约束。
"""

import hashlib
import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('code_executor', Path(__file__).with_name('code_executor.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourcePatchBoundaryTests(unittest.TestCase):
    def test_new_repairs_do_not_replay_another_repairs_measurements(self):
        manifest = {'id': 'crm-python', 'source_revision': 'a'*64, 'runner_image_id': 'sha256:'+'b'*64}
        profile = module.execution_key(manifest, 'profile', [], 'repair:41')
        self.assertEqual(profile, module.execution_key(manifest, 'profile', [], 'repair:41'))
        self.assertNotEqual(profile, module.execution_key(manifest, 'profile', [], 'repair:42'))
        self.assertNotEqual(profile, module.execution_key(manifest, 'test', [], 'repair:41'))
        for scope in ['../repair', 'repair:0', 'repair:-1', 'repair:41\n', 41]:
            with self.subTest(scope=scope), self.assertRaises(module.Rejected):
                module.execution_key(manifest, 'profile', [], scope)

    def test_exact_source_context_and_bounded_pure_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'palette.py'
            path.write_text('from functools import lru_cache\n\ndef color(value):\n    return value.upper()\n')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = {'files': {'palette.py': digest}, 'editable_files': ['palette.py']}
            edit = {'path': 'palette.py', 'sha256': digest, 'old': 'def color(value):',
                    'new': '@lru_cache(maxsize=128)\ndef color(value):'}
            changed = module.validate_patch(root, [edit], manifest)
            self.assertIn('@lru_cache(maxsize=128)', changed['palette.py'])
            self.assertNotIn('@lru_cache', path.read_text())
            cases = [
                {**edit, 'path': '../palette.py'},
                {**edit, 'path': 'tests/test_palette.py'},
                {**edit, 'sha256': '0' * 64},
                {**edit, 'old': 'missing source context'},
                {**edit, 'new': '@lru_cache(maxsize=None)\ndef color(value):'},
                {**edit, 'new': '@lru_cache()\ndef color(value):'},
                {**edit, 'new': 'import os\ndef color(value):'},
                {**edit, 'new': 'open("/tmp/escape", "w")\ndef color(value):'},
                {**edit, 'new': 'value = ().__class__\ndef color(value):'},
                {**edit, 'new': 'invalid Python syntax!'},
            ]
            for case in cases:
                with self.subTest(case=case['path'] + case['new'][:24]):
                    with self.assertRaises(module.Rejected):
                        module.validate_patch(root, [case], manifest)
            second = {**edit, 'old': 'return value.upper()', 'new': 'return str.upper(value)'}
            combined = module.validate_patch(root, [edit, second], manifest)['palette.py']
            self.assertIn('@lru_cache(maxsize=128)', combined)
            self.assertIn('return str.upper(value)', combined)
            with self.assertRaisesRegex(module.Rejected, 'PATCH_CONTEXT_OVERLAP'):
                module.validate_patch(root, [edit, edit], manifest)
            with self.assertRaisesRegex(module.Rejected, 'PATCH_FIELDS_REJECTED'):
                module.validate_patch(root, [{**edit, 'command': 'anything'}], manifest)

    def test_saber_patch_freezes_sql_and_tenant_contracts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            name = 'internal/rag/fts5_index.py'
            path = root / name
            path.parent.mkdir(parents=True)
            original = 'import re\nimport sqlite3\n\ndef fts_tokenize(text):\n    return text.lower()\n\ndef build_fts_query(text):\n    return text.strip()\n\nclass FTS5IndexManager:\n    def search(self, db, user_id):\n        return db.execute("SELECT id FROM chunks WHERE user_id=?", (user_id,))\n'
            path.write_text(original)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = {'test_preset': 'saber-fts5-v1', 'files': {name: digest}, 'editable_files': [name]}
            edit = {'path': name, 'sha256': digest, 'old': 'return text.lower()', 'new': 'return str.lower(text)'}
            self.assertIn('return str.lower(text)', module.validate_patch(root, [edit], manifest)[name])
            for old, new in [('WHERE user_id=?', 'WHERE 1=1 OR user_id=?'), ('class FTS5IndexManager:', 'class AnotherManager:'), ('import sqlite3', 'import sqlite3\nforeign = 1')]:
                with self.subTest(old=old), self.assertRaisesRegex(module.Rejected, 'PATCH_FROZEN_SQL_CONTRACT'):
                    module.validate_patch(root, [{**edit, 'old': old, 'new': new}], manifest)

    def test_source_modification_and_ambiguous_context_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'palette.py'
            path.write_text('a = 1\nb = 1\n')
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = {'files': {'palette.py': digest}, 'editable_files': ['palette.py']}
            edit = {'path': 'palette.py', 'sha256': digest, 'old': '= 1', 'new': '= 2'}
            with self.assertRaisesRegex(module.Rejected, 'PATCH_CONTEXT_NOT_UNIQUE'):
                module.validate_patch(root, [edit], manifest)
            path.write_text('a = 3\n')
            with self.assertRaisesRegex(module.Rejected, 'PATCH_SOURCE_CHANGED'):
                module.validate_patch(root, [edit], manifest)


class SandboxCancellationTests(unittest.TestCase):
    """导入 Docker 控制响应夹具，使用真实 socket EOF 和本请求拥有的 CLI 子进程验证取消。"""

    key = 'a' * 64
    image = 'sha256:' + 'b' * 64
    container_id = 'c' * 64

    def run_fixture(self, canceled, *, child_code='import time; time.sleep(60)', timeout=2, foreign=False):
        calls, children = [], []
        real_popen = subprocess.Popen

        def docker(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ['docker', 'inspect']:
                if argv[2] == 'fixture-job':
                    if not foreign:
                        return subprocess.CompletedProcess(argv, 1, b'', b'')
                    value = {'Id': self.container_id, 'Image': self.image,
                             'Config': {'Labels': {'drop.code-job': 'another-job'}}}
                else:
                    self.assertEqual(argv[2], self.container_id)
                    value = {'Id': self.container_id, 'Image': self.image,
                             'Config': {'Labels': {'drop.code-job': self.key}}}
                return subprocess.CompletedProcess(argv, 0, json.dumps([value]).encode(), b'')
            if argv[:2] == ['docker', 'create']:
                return subprocess.CompletedProcess(argv, 0, (self.container_id + '\n').encode(), b'')
            self.assertEqual(argv, ['docker', 'rm', '-f', self.container_id])
            return subprocess.CompletedProcess(argv, 0, b'', b'')

        def start(argv, **kwargs):
            self.assertEqual(argv, ['docker', 'start', '--attach', self.container_id])
            child = real_popen([sys.executable, '-c', child_code], **kwargs)
            children.append(child)
            return child

        with tempfile.TemporaryFile() as log, patch.object(module.subprocess, 'run', side_effect=docker), patch.object(module.subprocess, 'Popen', side_effect=start):
            try:
                result = module.run_sandbox(['docker', 'create'], 'fixture-job', self.key, self.image,
                                            log, canceled, timeout=timeout)
                return result, calls, children
            finally:
                for child in children:
                    self.assertIsNotNone(child.poll(), 'owned Docker CLI child leaked')

    def test_real_disconnect_cancels_child_and_removes_only_verified_id(self):
        server, caller = socket.socketpair()
        timer = threading.Timer(.12, caller.close)
        timer.start()
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(module.Rejected, '^EXECUTION_CANCELED$'):
                self.run_fixture(lambda: module.peer_disconnected(server))
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            timer.join()
            server.close()
            caller.close()

    def test_deadline_stops_owned_child(self):
        result, calls, _ = self.run_fixture(lambda: False, timeout=.1)
        self.assertEqual(result, -1)
        self.assertEqual([c for c in calls if c[:2] == ['docker', 'rm']],
                         [['docker', 'rm', '-f', self.container_id]])

    def test_normal_exit_is_preserved(self):
        result, _, _ = self.run_fixture(lambda: False, child_code='raise SystemExit(0)')
        self.assertEqual(result, 0)

    def test_disconnected_before_create_has_no_side_effect(self):
        with patch.object(module.subprocess, 'run') as docker:
            with self.assertRaisesRegex(module.Rejected, '^EXECUTION_CANCELED$'):
                module.run_sandbox([], 'fixture-job', self.key, self.image, None, lambda: True)
            docker.assert_not_called()

    def test_disconnect_after_create_removes_verified_id_without_starting(self):
        disconnected = threading.Event()
        calls = []

        def docker(argv, **kwargs):
            calls.append(argv)
            if argv[:2] == ['docker', 'create']:
                disconnected.set()
                return subprocess.CompletedProcess(argv, 0, (self.container_id + '\n').encode(), b'')
            if argv[:2] == ['docker', 'inspect']:
                if argv[2] == 'fixture-job':
                    return subprocess.CompletedProcess(argv, 1, b'', b'')
                self.assertEqual(argv[2], self.container_id)
                info = {'Id': self.container_id, 'Image': self.image,
                        'Config': {'Labels': {'drop.code-job': self.key}}}
                return subprocess.CompletedProcess(argv, 0, json.dumps([info]).encode(), b'')
            self.assertEqual(argv, ['docker', 'rm', '-f', self.container_id])
            return subprocess.CompletedProcess(argv, 0, b'', b'')

        with patch.object(module.subprocess, 'run', side_effect=docker), patch.object(module.subprocess, 'Popen') as start:
            with self.assertRaisesRegex(module.Rejected, '^EXECUTION_CANCELED$'):
                module.run_sandbox(['docker', 'create'], 'fixture-job', self.key, self.image,
                                   None, disconnected.is_set)
            start.assert_not_called()
        self.assertIn(['docker', 'create'], calls)
        self.assertEqual([call for call in calls if call[:2] == ['docker', 'rm']],
                         [['docker', 'rm', '-f', self.container_id]])

    def test_created_id_with_wrong_image_or_job_is_neither_started_nor_removed(self):
        for field in ('image', 'job'):
            with self.subTest(field=field):
                calls = []

                def docker(argv, **kwargs):
                    calls.append(argv)
                    if argv[:2] == ['docker', 'create']:
                        return subprocess.CompletedProcess(argv, 0, (self.container_id + '\n').encode(), b'')
                    self.assertEqual(argv[:2], ['docker', 'inspect'])
                    if argv[2] == 'fixture-job':
                        return subprocess.CompletedProcess(argv, 1, b'', b'')
                    self.assertEqual(argv[2], self.container_id)
                    info = {'Id': self.container_id,
                            'Image': 'sha256:' + 'd' * 64 if field == 'image' else self.image,
                            'Config': {'Labels': {'drop.code-job': 'another-job' if field == 'job' else self.key}}}
                    return subprocess.CompletedProcess(argv, 0, json.dumps([info]).encode(), b'')

                with patch.object(module.subprocess, 'run', side_effect=docker), patch.object(module.subprocess, 'Popen') as start:
                    with self.assertRaisesRegex(module.Rejected, '^CONTAINER_OWNERSHIP_MISMATCH$'):
                        module.run_sandbox(['docker', 'create'], 'fixture-job', self.key, self.image,
                                           None, lambda: False)
                    start.assert_not_called()
                self.assertIn(['docker', 'create'], calls)
                self.assertEqual([call for call in calls if call[:2] == ['docker', 'rm']], [])

    def test_foreign_name_is_not_removed_or_started(self):
        with self.assertRaisesRegex(module.Rejected, '^CONTAINER_OWNERSHIP_MISMATCH$'):
            self.run_fixture(lambda: False, foreign=True)

    def test_live_socket_is_not_mistaken_for_disconnect(self):
        server, caller = socket.socketpair()
        try:
            self.assertFalse(module.peer_disconnected(server))
            caller.sendall(b'x')
            self.assertFalse(module.peer_disconnected(server))
        finally:
            server.close()
            caller.close()


if __name__ == '__main__':
    unittest.main()
