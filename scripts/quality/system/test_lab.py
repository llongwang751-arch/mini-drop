"""系统故障控制器的合成边界测试；不连接 Docker、数据库、浏览器或生产。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab import API, Lab, LabError, NoRedirect, atomic_json, inspect_owned, interrupted, validate_system_networks

PROJECT = 'txdrop-dev-qa-' + 'a' * 12


def container(project=PROJECT, service='agent', privileged=False, pid_mode=''):
    return {'Config': {'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': service}},
            'HostConfig': {'Privileged': privileged, 'PidMode': pid_mode}}


class SystemLabSafetyTests(unittest.TestCase):
    def test_failure_states_export_only_owned_fixed_metadata(self):
        lab = Lab.__new__(Lab)
        lab.project = PROJECT
        lab.container = Mock(return_value='owned')
        value = container()
        value['Config']['Env'] = ['SYNTHETIC_SECRET=must-never-export']
        value['State'] = {'Status': 'exited', 'Running': False, 'OOMKilled': True, 'ExitCode': 137,
                          'Error': 'synthetic private body', 'Health': {'Status': 'unhealthy', 'Log': ['private']}}
        def command(_argv):
            return SimpleNamespace(stdout=json.dumps([value]).encode())
        lab.command = command
        rows = lab.failure_states()
        self.assertEqual(len(rows), 8)
        agent = next(r for r in rows if r['service'] == 'agent')
        self.assertEqual(agent, {'service': 'agent', 'status': 'exited', 'running': False,
                                'oom_killed': True, 'exit_code': 137, 'health': 'unhealthy'})
        self.assertNotIn('private', json.dumps(rows))
        self.assertNotIn('SYNTHETIC_SECRET', json.dumps(rows))
        self.assertTrue(all(r.get('error_code') == 'OWNED_STATE_UNAVAILABLE' for r in rows if r['service'] != 'agent'))

    def test_failure_states_never_export_foreign_container_values(self):
        lab = Lab.__new__(Lab)
        lab.project = PROJECT
        lab.container = Mock(return_value='foreign')
        lab.command = Mock(return_value=SimpleNamespace(stdout=json.dumps([container('production')]).encode()))
        self.assertTrue(all(r.get('error_code') == 'OWNED_STATE_UNAVAILABLE' for r in lab.failure_states()))

    def test_host_interrupt_uses_fixed_failure_for_finally_cleanup(self):
        with self.assertRaisesRegex(LabError, '^LAB_INTERRUPTED$'):
            interrupted(15, None)

    def test_container_identity_and_unsafe_runtime_are_rejected(self):
        inspect_owned(container(), PROJECT, 'agent')
        for value in (container('txdrop-prod'), container(service='postgres'), container(privileged=True), container(pid_mode='host')):
            with self.subTest(value=value), self.assertRaises(LabError):
                inspect_owned(value, PROJECT, 'agent')

    def test_api_never_accepts_production_remote_or_redirect_target(self):
        for origin in ('https://82.157.81.79', 'http://120.24.187.205', 'http://localhost:18080', 'http://127.0.0.1:18080@evil'):
            with self.subTest(origin=origin), self.assertRaises(LabError), patch('lab.urllib.request.build_opener') as opener:
                API(origin, Path('/not-read'))
            opener.assert_not_called()
        with self.assertRaises(LabError):
            NoRedirect().redirect_request(None)

    def test_fault_allowlist_prevents_arbitrary_service_or_command(self):
        lab = Lab.__new__(Lab)
        lab.container = Mock()
        for service, action in (('web', 'kill'), ('production-postgres', 'stop'), ('agent', 'rm'), ('postgres', 'exec')):
            with self.subTest(service=service, action=action), self.assertRaises(LabError):
                lab.fault(service, action)
        lab.container.assert_not_called()

    def test_cleanup_preserves_foreign_container_before_any_delete(self):
        lab = Lab.__new__(Lab)
        lab.created, lab.project = True, PROJECT
        calls = []
        def command(argv):
            calls.append(argv)
            return SimpleNamespace(stdout=b'container\n' if argv[1] == 'ps' else json.dumps([container('txdrop-production')]).encode())
        lab.command = command
        with self.assertRaises(LabError):
            lab.cleanup()
        self.assertFalse(any('rm' in args for args in calls))

    def test_cleanup_removes_only_exact_project_labeled_resources(self):
        lab = Lab.__new__(Lab)
        lab.created, lab.project = True, PROJECT
        calls = []
        def command(argv):
            calls.append(argv)
            if argv[1] == 'ps':
                return SimpleNamespace(stdout=b'owned\n')
            if argv[1] == 'inspect':
                return SimpleNamespace(stdout=json.dumps([container()]).encode())
            if argv[2] == 'ls':
                return SimpleNamespace(stdout=('f' * 12 if argv[1] == 'network' else PROJECT + '_fixture').encode())
            if argv[2] == 'inspect':
                return SimpleNamespace(stdout=json.dumps([{'Name': PROJECT + '_fixture', 'Labels': {'com.docker.compose.project': PROJECT}}]).encode())
            return SimpleNamespace(stdout=b'')
        lab.command = command
        result = lab.cleanup()
        self.assertTrue(result['passed'])
        self.assertEqual(result['owned_containers_removed'], 1)
        self.assertFalse(any('down' in args or '-v' in args for args in calls))
        self.assertIn(['docker', 'network', 'rm', 'f' * 12], calls)
        self.assertIn(['docker', 'volume', 'rm', PROJECT + '_fixture'], calls)

    def test_only_web_has_ingress_and_data_networks_stay_internal(self):
        value = {'networks': {'control': {'internal': True}, 'storage': {'internal': True}, 'ingress': {'internal': False}},
                 'services': {'web': {'networks': {'control': {}, 'ingress': {}}}, 'agent': {'networks': {'control': {}, 'storage': {}}}}}
        validate_system_networks(value)
        for mutation in ('backend_ingress', 'control_egress', 'missing_web_ingress', 'unexpected_network'):
            changed = json.loads(json.dumps(value))
            if mutation == 'backend_ingress':
                changed['services']['agent']['networks']['ingress'] = {}
            elif mutation == 'control_egress':
                changed['networks']['control']['internal'] = False
            elif mutation == 'missing_web_ingress':
                del changed['services']['web']['networks']['ingress']
            else:
                changed['networks']['foreign'] = {}
            with self.subTest(mutation=mutation), self.assertRaises(LabError):
                validate_system_networks(changed)

    def test_cleanup_refuses_network_with_foreign_name_despite_matching_owner(self):
        lab = Lab.__new__(Lab)
        lab.created, lab.project = True, PROJECT
        calls = []
        def command(argv):
            calls.append(argv)
            if argv[1] == 'ps' or argv[1:3] == ['volume', 'ls']:
                return SimpleNamespace(stdout=b'')
            if argv[1:3] == ['network', 'ls']:
                return SimpleNamespace(stdout=b'ffffffffffff')
            return SimpleNamespace(stdout=json.dumps([{'Name': 'foreign_network', 'Labels': {'com.docker.compose.project': PROJECT}}]).encode())
        lab.command = command
        with self.assertRaisesRegex(LabError, '^CLEANUP_RESOURCE_OWNER_MISMATCH$'):
            lab.cleanup()
        self.assertFalse(any('rm' in args for args in calls))

    def test_foreign_volume_is_preserved_even_with_same_display_prefix(self):
        lab = Lab.__new__(Lab)
        lab.created, lab.project = True, PROJECT
        calls = []
        def command(argv):
            calls.append(argv)
            if argv[1] == 'ps':
                return SimpleNamespace(stdout=b'')
            if argv[2] == 'ls':
                return SimpleNamespace(stdout=(PROJECT + '_data').encode())
            return SimpleNamespace(stdout=json.dumps([{'Labels': {'com.docker.compose.project': 'foreign'}}]).encode())
        lab.command = command
        with self.assertRaises(LabError):
            lab.cleanup()
        self.assertFalse(any('rm' in args for args in calls))

    def test_atomic_progress_failure_preserves_previous_complete_json(self):
        with tempfile.TemporaryDirectory(prefix='drop-system-atomic-synthetic-') as directory:
            path = Path(directory) / 'summary.json'
            atomic_json(path, {'not_final': True, 'completed_windows': 2})
            with patch('lab.os.replace', side_effect=OSError('synthetic failure')), self.assertRaises(OSError):
                atomic_json(path, {'not_final': True, 'completed_windows': 3})
            self.assertEqual(json.loads(path.read_text())['completed_windows'], 2)
            self.assertFalse(list(Path(directory).glob('.quality-*')))

    def test_browser_container_requires_exact_immutable_image_id(self):
        lab = Lab.__new__(Lab)
        lab.command = Mock()
        args = SimpleNamespace(browser_image='mcr.microsoft.com/playwright:v1.64.1-noble')
        with self.assertRaises(LabError):
            lab.browser(args, Path('/not-read'), Path('/not-read'))
        lab.command.assert_not_called()

    def test_browser_mounts_only_two_owned_login_files_and_explicit_runtime(self):
        with tempfile.TemporaryDirectory(prefix='drop-system-browser-synthetic-') as directory:
            root = Path(directory)
            for relative in ('web/e2e', 'web/node_modules/playwright', 'web/node_modules/playwright-core', 'instance/secrets', 'browser-output'):
                (root / relative).mkdir(parents=True)
            for name in ('admin_username', 'admin_password', 'database_url', 's3_secret_key'):
                (root / 'instance/secrets' / name).write_text('synthetic-not-a-real-secret')
            context = root / 'context.json'
            context.write_text('{}')
            lab = Lab.__new__(Lab)
            lab.path, lab.project = root / 'instance', PROJECT
            image = 'sha256:' + 'b' * 64
            lab.command = Mock(return_value=SimpleNamespace(stdout=(image + '\n').encode(), returncode=7))
            with patch('lab.ROOT', root):
                result = lab.browser(SimpleNamespace(browser_image=image), context, root / 'browser-output')
            argv = lab.command.call_args_list[-1].args[0]
            binds = [argv[i + 1] for i, value in enumerate(argv) if value == '--mount']
            self.assertEqual(len(binds), 7)
            self.assertFalse(any('database_url' in bind or 's3_secret_key' in bind or 'signing' in bind for bind in binds))
            self.assertTrue(all(',readonly' in bind for bind in binds if 'browser-output' not in bind))
            self.assertEqual(argv[argv.index('--memory') + 1], '768m')
            self.assertEqual(argv[argv.index('--network') + 1], 'host')
            self.assertFalse(result['database_storage_or_ca_private_keys_mounted'])
            self.assertEqual(result['exit_code'], 7)
            self.assertTrue(lab.command.call_args_list[-1].kwargs['allowed_failure'])


if __name__ == '__main__':
    unittest.main()
