"""系统故障控制器的合成边界测试；不连接 Docker、数据库、浏览器或生产。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lab import API, Lab, LabError, NoRedirect, atomic_json, core_log_projection, inspect_owned, interrupted, validate_system_networks
import make_diagnostic_context as diagnostic_context

PROJECT = 'txdrop-dev-qa-' + 'a' * 12


def container(project=PROJECT, service='agent', privileged=False, pid_mode=''):
    return {'Config': {'Labels': {'com.docker.compose.project': project, 'com.docker.compose.service': service}},
            'HostConfig': {'Privileged': privileged, 'PidMode': pid_mode}}


class SystemLabSafetyTests(unittest.TestCase):
    def test_actual_prepare_renderer_selects_native_only_for_owned_agent(self):
        # fixture放在ASCII仓库私有目录，避免Windows旧代码页把UTF-8 JSON路径误解码。
        fixture_root = Path(__file__).resolve().parents[3] / '.local'
        fixture_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='qa-native-render-synthetic-', dir=fixture_root) as directory:
            root = Path(directory).resolve()
            # 显式合成夹具，只验证真实prepare的复制、生成和渲染校验，不执行这些假二进制。
            fixtures = {'deploy/storage-gateway.conf': '# imported synthetic nginx',
                        'deploy/db-permissions.sql': '-- imported synthetic SQL',
                        'deploy/bootstrap.sh': '# synthetic bootstrap; never executed',
                        'contracts/task-kinds.json': '{"synthetic":true}',
                        'analyzer/drop_analyzer/__init__.py': '# synthetic analyzer',
                        'db/migrations/001_synthetic.sql': '-- imported synthetic migration',
                        'web/nginx.conf': '# synthetic web config', 'inputs/api': 'synthetic API binary',
                        'inputs/web/index.html': '<p>synthetic build</p>'}
            fixtures.update({'inputs/core/' + name: 'synthetic binary ' + name for name in ('drop_server', 'drop_agent', 'drop_workload')})
            for name, body in fixtures.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body)
            output = root / 'output'
            output.mkdir()
            bindings = {'api_binary': str(root / 'inputs/api'), 'web_dist': str(root / 'inputs/web'),
                        'core_binaries': str(root / 'inputs/core')}
            with patch('lab.ROOT', root), patch('lab.dev.ROOT', root):
                lab = Lab(output, {'core': 'synthetic:core'}, bindings)
                rendered_models = []
                def command(argv):
                    if 'config' not in argv:
                        return SimpleNamespace(stdout=b'')
                    # 适配器只补Compose基础网络/name，其余完全读取真实prepare刚写出的覆盖文件。
                    rendered = json.loads((lab.path / 'overlay.json').read_text())
                    rendered['name'] = lab.project
                    rendered['networks']['storage'] = {'internal': True}
                    rendered_models.append(rendered)
                    return SimpleNamespace(stdout=json.dumps(rendered).encode())
                lab.command = command
                hashes = lab.prepare()
            self.assertEqual(len(rendered_models), 1)
            services = rendered_models[0]['services']
            self.assertEqual(services['agent']['environment']['GRPC_DNS_RESOLVER'], 'native')
            self.assertTrue(all('GRPC_DNS_RESOLVER' not in value.get('environment', {}) for name, value in services.items() if name != 'agent'))
            self.assertEqual(set(hashes) & {'drop_agent', 'drop_server', 'drop_workload', 'drop-api'},
                             {'drop_agent', 'drop_server', 'drop_workload', 'drop-api'})

    def test_collector_runtime_requires_owned_exact_native_value(self):
        lab = Lab.__new__(Lab)
        lab.project = PROJECT
        lab.container = Mock(return_value='owned-agent')
        value = container()
        value['Config']['Env'] = ['SYNTHETIC_PRIVATE=never-export', 'GRPC_DNS_RESOLVER=native']
        lab.command = Mock(return_value=SimpleNamespace(stdout=json.dumps([value]).encode()))
        self.assertEqual(lab.collector_dns_resolver(), 'native')
        for environment in ([], ['GRPC_DNS_RESOLVER=ares'], ['GRPC_DNS_RESOLVER=native', 'GRPC_DNS_RESOLVER=native']):
            value['Config']['Env'] = environment
            lab.command.return_value.stdout = json.dumps([value]).encode()
            with self.assertRaisesRegex(LabError, '^COLLECTOR_DNS_RESOLVER_MISMATCH$'):
                lab.collector_dns_resolver()
        value = container('production')
        value['Config']['Env'] = ['GRPC_DNS_RESOLVER=native']
        lab.command.return_value.stdout = json.dumps([value]).encode()
        with self.assertRaisesRegex(LabError, '^OWNERSHIP_MISMATCH$'):
            lab.collector_dns_resolver()

    def test_diagnostic_patch_is_exact_once_and_has_no_error_body(self):
        source = b'prefix\n' + diagnostic_context.BEFORE + b'\nsuffix\n'
        changed = diagnostic_context.patched(source)
        self.assertIn(b'status.error_code()', changed)
        self.assertIn(b'register_grpc_', changed)
        self.assertIn(b'const auto registration_message = status.error_message();', changed)
        self.assertNotIn(b'log("drop-agent", registration_message', changed)
        self.assertIn(b'register_detail_other', changed)
        self.assertNotIn(b'error_details()', changed)
        self.assertEqual(changed.count(diagnostic_context.AFTER), 1)
        for wrong in (b'', source + diagnostic_context.BEFORE, changed):
            with self.assertRaises(ValueError):
                diagnostic_context.patched(wrong)

    def test_diagnostic_context_preserves_original_source_and_binds_image_ids(self):
        with tempfile.TemporaryDirectory(prefix='qa-diagnostic-synthetic-') as directory:
            root = Path(directory)
            (root / 'core/src').mkdir(parents=True)
            source = b'// explicit synthetic C++\n' + diagnostic_context.BEFORE + b'\n'
            (root / 'core/src/agent.cpp').write_bytes(source)
            ids = {'build': 'sha256:' + 'a' * 64, 'runtime': 'sha256:' + 'b' * 64}
            output = (root / 'output').resolve()
            with patch.object(diagnostic_context, 'ROOT', root), patch.object(diagnostic_context, 'image_id', side_effect=lambda ref: ids[ref]):
                row = diagnostic_context.prepare(output, 'build', 'runtime')
            self.assertEqual((root / 'core/src/agent.cpp').read_bytes(), source)
            self.assertFalse(row['acceptance_eligible'])
            self.assertFalse(row['production_or_repository_cpp_modified'])
            self.assertEqual(row['base_image_ids'], ids)
            self.assertEqual(set(row['files']), {'agent.diagnostic.cpp', 'registration-status.patch', 'Dockerfile'})
            self.assertNotEqual(row['original_source_sha256'], row['instrumented_source_sha256'])

    def test_readiness_refuses_foreign_dependency_before_execution(self):
        lab = Lab.__new__(Lab)
        lab.container = Mock(side_effect=LabError('OWNERSHIP_MISMATCH'))
        lab.command = Mock()
        with self.assertRaises(LabError):
            lab.readiness()
        lab.command.assert_not_called()

    def test_readiness_requires_exact_bounded_dependency_receipt(self):
        lab = Lab.__new__(Lab)
        lab.container = Mock(return_value='owned')
        lab.command = Mock(return_value=SimpleNamespace(stdout=b'{"status":503,"dependency":"control"}'))
        self.assertEqual(lab.readiness(), {'status': 503, 'dependency': 'control'})
        for row in ({'status': 200, 'dependency': 'none', 'raw': 'synthetic-private'},
                    {'status': True, 'dependency': 'none'}, {'status': 503, 'dependency': 'unbounded-private'}):
            lab.command.return_value.stdout = json.dumps(row).encode()
            with self.assertRaises(LabError):
                lab.readiness()

    def test_grpc_status_counts_and_server_internal_errors_are_fixed_only(self):
        rows = [{'service': 'drop-agent', 'error_code': 'REGISTER_FAILED', 'stage': 'register_grpc_14'},
                {'service': 'drop-agent', 'error_code': 'REGISTER_FAILED', 'stage': 'register_grpc_999'},
                {'service': 'drop-agent', 'error_code': 'REGISTER_FAILED', 'stage': 'synthetic-private'},
                {'service': 'drop-server', 'error_code': 'INTERNAL'}]
        raw = '\n'.join(json.dumps(r) for r in rows).encode()
        projected = core_log_projection(raw, 'drop-agent')
        self.assertEqual(sum(projected['registration_grpc_status_counts'].values()), 1)
        self.assertEqual(projected['registration_grpc_status_counts']['14'], 1)
        self.assertNotIn('synthetic-private', json.dumps(projected))
        self.assertEqual(core_log_projection(raw, 'drop-server')['events']['rpc_internal_error'], 1)

    def test_registration_detail_allows_only_fixed_class_names(self):
        rows = [{'service': 'drop-agent', 'error_code': 'REGISTER_DETAIL', 'stage': 'register_detail_dns'},
                {'service': 'drop-agent', 'error_code': 'REGISTER_DETAIL', 'stage': 'register_detail_SYNTHETIC_PRIVATE'},
                {'service': 'drop-server', 'error_code': 'REGISTER_DETAIL', 'stage': 'register_detail_control_internal'},
                {'service': 'drop-agent', 'error_code': 'REGISTER_DETAIL', 'stage': 'dns'}]
        result = core_log_projection('\n'.join(json.dumps(r) for r in rows).encode(), 'drop-agent')
        self.assertEqual(result['registration_error_classes'], {'control_internal': 0, 'dns': 1, 'failed_connect': 0, 'other': 0})
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))

    def test_resolver_diagnostic_rejects_non14_before_any_command(self):
        lab = Lab.__new__(Lab)
        lab.command = Mock()
        initial = {'agent': {'registration_grpc_status_counts': {'14': 0}, 'events': {'registration_succeeded': 0}}, 'drop-server': {'events': {'rpc_internal_error': 0}}}
        with self.assertRaisesRegex(LabError, '^RESOLVER_DIAGNOSTIC_PRECONDITION$'):
            lab.diagnostic_resolver_comparison(initial)
        lab.command.assert_not_called()

    def test_resolver_diagnostic_rejects_nonzero_work_before_restart(self):
        lab = Lab.__new__(Lab)
        lab.readiness = Mock(return_value={'status': 200, 'dependency': 'none'})
        lab.container = Mock(return_value='owned-postgres')
        lab.command = Mock(return_value=SimpleNamespace(stdout=b'{"tasks":1,"attempts":0}'))
        initial = {'agent': {'registration_grpc_status_counts': {'14': 26}, 'events': {'registration_succeeded': 0}}, 'drop-server': {'events': {'rpc_internal_error': 0}}}
        with self.assertRaisesRegex(LabError, '^RESOLVER_DIAGNOSTIC_NONZERO_WORK$'):
            lab.diagnostic_resolver_comparison(initial)
        self.assertEqual(lab.command.call_count, 1)
        self.assertNotIn('stop', lab.command.call_args.args[0])

    def test_resolver_diagnostic_three_legs_check_new_cids_not_persisted_agent_row(self):
        with tempfile.TemporaryDirectory(prefix='qa-resolver-synthetic-') as directory:
            lab = Lab.__new__(Lab)
            lab.path, lab.output, lab.project = Path(directory), Path(directory), PROJECT
            lab.config, lab.base = {}, ['compose-synthetic']
            lab.readiness = Mock(return_value={'status': 200, 'dependency': 'none'})
            atomic_json(lab.path / 'overlay.json', {'services': {'agent': {'environment': {'SYNTHETIC_FIXED': 'value'}}}})
            state = {'cid': 'original'}
            calls = []
            lab.container = Mock(side_effect=lambda service: 'pg-owned' if service == 'postgres' else state['cid'])
            def command(argv, *_args, **_kwargs):
                calls.append(argv)
                env = json.loads((lab.path / 'overlay.json').read_text())['services']['agent']['environment']
                if argv[:2] == ['docker', 'inspect']:
                    row = container()
                    row.update({'Image': 'synthetic-fixed-image', 'Mounts': [], 'NetworkSettings': {'Networks': {PROJECT + '_control': {}}},
                                'State': {'Running': True, 'OOMKilled': False}})
                    row['Config'].update({'Env': [k + '=' + v for k, v in env.items()], 'Entrypoint': ['drop_agent'], 'Cmd': None, 'User': '10001:10001'})
                    return SimpleNamespace(stdout=json.dumps([row]).encode())
                if argv[:2] == ['docker', 'exec']:
                    return SimpleNamespace(stdout=b'{"tasks":0,"attempts":0}')
                if 'config' in argv:
                    return SimpleNamespace(stdout=json.dumps({'services': {'agent': {'environment': env, 'image': 'same'}}}).encode())
                if 'up' in argv:
                    state['cid'] = 'native-new' if 'GRPC_DNS_RESOLVER' in env else 'default-new'
                if argv[:2] == ['docker', 'logs']:
                    event = {'service': 'drop-agent', 'message': 'Registered over mTLS'} if state['cid'] == 'native-new' else {'service': 'drop-agent', 'error_code': 'REGISTER_FAILED', 'stage': 'register_grpc_14'}
                    return SimpleNamespace(stdout=json.dumps(event).encode(), stderr=b'')
                return SimpleNamespace(stdout=b'')
            lab.command = command
            initial = {'agent': {'registration_grpc_status_counts': {'14': 26}, 'events': {'registration_succeeded': 0}}, 'drop-server': {'events': {'rpc_internal_error': 0}}}
            clock = iter(range(0, 1000, 5))
            with patch('lab.dev.validate_rendered'), patch('lab.validate_system_networks'), patch('lab.time.sleep'), patch('lab.time.monotonic', side_effect=lambda: next(clock)):
                row = lab.diagnostic_resolver_comparison(initial)
            self.assertEqual(row['conclusion'], 'RESOLVER_STRATEGY_DIFFERENCE_REPRODUCED')
            self.assertEqual([r['registered'] for r in row['legs']], [True, False])
            self.assertFalse(row['acceptance_eligible'])
            self.assertTrue(row['original_browser_failure_preserved'])
            self.assertEqual(sum('stop' in argv for argv in calls), 2)
            self.assertEqual({argv[-1] for argv in calls if argv[:2] == ['docker', 'logs']}, {'native-new', 'default-new'})
            self.assertNotIn('GRPC_DNS_RESOLVER', json.loads((lab.path / 'overlay.json').read_text())['services']['agent']['environment'])
            # 有界负控：第二次对照中Core在第三腿变503，必须拒绝归因，保留已有两腿收据。
            state['cid'] = 'original-again'
            lab.readiness = Mock(side_effect=[{'status': 200, 'dependency': 'none'},
                                              {'status': 200, 'dependency': 'none'},
                                              {'status': 503, 'dependency': 'control'}])
            clock = iter(range(0, 1000, 5))
            with patch('lab.dev.validate_rendered'), patch('lab.validate_system_networks'), patch('lab.time.sleep'), patch('lab.time.monotonic', side_effect=lambda: next(clock)):
                with self.assertRaisesRegex(LabError, '^RESOLVER_DIAGNOSTIC_CORE_CHANGED$'):
                    lab.diagnostic_resolver_comparison(initial)
            rejected = json.loads((lab.output / 'resolver-diagnostic.json').read_text())
            self.assertNotIn('conclusion', rejected)
            self.assertEqual(rejected['legs'][-1]['readiness'], {'status': 503, 'dependency': 'control'})
            self.assertFalse(rejected['acceptance_eligible'])

    def test_core_log_projection_counts_only_fixed_events_without_exporting_body(self):
        rows = [{'service': 'drop-agent', 'message': 'Effective configuration loaded', 'configuration': {'secret': 'SYNTHETIC_PRIVATE'}},
                {'service': 'drop-agent', 'error_code': 'REGISTER_FAILED', 'message': 'SYNTHETIC_PRIVATE'},
                {'service': 'drop-server', 'error_code': 'REGISTER_FAILED'},
                {'service': 'drop-agent', 'error_code': 'SYNTHETIC_PRIVATE', 'message': ['REGISTER_FAILED']}]
        raw = ('\n'.join(json.dumps(r) for r in rows) + '\nTLS certificate verify failed SYNTHETIC_PRIVATE\n').encode()
        result = core_log_projection(raw, 'drop-agent')
        self.assertEqual(result['events']['configuration_loaded'], 1)
        self.assertEqual(result['events']['registration_failed'], 1)
        self.assertTrue(result['stderr_flags']['tls_verify_failed'])
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))
        with self.assertRaises(LabError):
            core_log_projection(b'x' * (256 * 1024 + 1), 'drop-agent')

    def test_core_diagnostics_recheck_owner_before_logs_or_handshake(self):
        lab = Lab.__new__(Lab)
        lab.project = PROJECT
        lab.container = Mock(return_value='foreign')
        lab.command = Mock(return_value=SimpleNamespace(stdout=json.dumps([container('production')]).encode()))
        with self.assertRaises(LabError):
            lab.failure_core_diagnostics()
        self.assertEqual(lab.command.call_count, 1)
        self.assertNotIn('logs', lab.command.call_args.args[0])
        self.assertNotIn('exec', lab.command.call_args.args[0])

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
