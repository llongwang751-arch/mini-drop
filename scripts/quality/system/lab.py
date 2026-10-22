"""可销毁的真实平台、恢复与有界持续运行验收；不接受公网地址或已有项目。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import http.cookiejar
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('drop_isolated_dev', ROOT / 'scripts/dev.py')
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)


class LabError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):
        raise LabError('ISOLATED_REDIRECT_FORBIDDEN')


def interrupted(_signum, _frame):
    # 宿主取消也经finally执行owner检查与清理，不能把可销毁栈留在共享宿主持续占用资源。
    raise LabError('LAB_INTERRUPTED')


def require(condition, code):
    if not condition:
        raise LabError(code)


def atomic_json(path, value):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', dir=path.parent, prefix='.quality-', delete=False, encoding='utf-8') as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def core_log_projection(raw, service):
    """把本轮Core日志变成固定事件计数/匹配布尔；正文、配置和TLS身份一律不返回。"""
    require(service in ('drop-agent', 'drop-server') and len(raw) <= 256 * 1024, 'CORE_LOG_PROJECTION_BOUND')
    text = raw.decode('utf-8', errors='replace')
    counts = {key: 0 for key in ('configuration_loaded', 'perf_probe_succeeded', 'perf_probe_unavailable',
                                 'registration_succeeded', 'registration_failed', 'server_ready', 'configuration_error',
                                 'rpc_internal_error', 'database_sweep_deferred')}
    grpc_status_counts = {str(code): 0 for code in range(17)}
    registration_error_classes = {name: 0 for name in ('control_internal', 'dns', 'failed_connect', 'other')}
    messages = {'Effective configuration loaded': 'configuration_loaded',
                'perf capability verified by real sampling probe': 'perf_probe_succeeded',
                'Registered over mTLS': 'registration_succeeded',
                'mTLS control plane ready': 'server_ready'}
    codes = {'PERF_UNAVAILABLE': 'perf_probe_unavailable', 'REGISTER_FAILED': 'registration_failed',
             'CONFIGURATION_ERROR': 'configuration_error', 'INTERNAL': 'rpc_internal_error',
             'DB_UNAVAILABLE': 'database_sweep_deferred'}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get('service') != service:
            continue
        for field, mapping in (('message', messages), ('error_code', codes)):
            value = row.get(field)
            if isinstance(value, str) and value in mapping:
                counts[mapping[value]] += 1
        if row.get('error_code') == 'REGISTER_FAILED' and isinstance(row.get('stage'), str):
            match = re.fullmatch(r'register_grpc_([0-9]|1[0-6])', row['stage'])
            if match:
                grpc_status_counts[match.group(1)] += 1
        if row.get('error_code') == 'REGISTER_DETAIL' and isinstance(row.get('stage'), str):
            name = row['stage'].removeprefix('register_detail_')
            if row['stage'].startswith('register_detail_') and name in registration_error_classes:
                registration_error_classes[name] += 1
    lower = text.lower()
    flags = {key: literal in lower for key, literal in {
        'tls_verify_failed': 'certificate verify failed', 'tls_bad_certificate': 'bad certificate',
        'peer_name_mismatch': 'not in peer certificate', 'dns_resolution_failed': 'dns resolution failed',
        'connect_all_addresses_failed': 'failed to connect to all addresses',
        'thread_creation_failed': 'could not create grpc_sync_server worker-thread',
        'shared_library_missing': 'cannot open shared object file',
    }.items()}
    return {'events': counts, 'registration_grpc_status_counts': grpc_status_counts,
            'registration_error_classes': registration_error_classes, 'stderr_flags': flags,
            'scope': 'last 100 owned container log lines; fixed event counts and literal-match booleans only'}


def inspect_owned(value, project, service=None):
    labels = value.get('Config', {}).get('Labels', {})
    require(project.startswith('txdrop-dev-qa-') and labels.get('com.docker.compose.project') == project, 'OWNERSHIP_MISMATCH')
    if service:
        require(labels.get('com.docker.compose.service') == service, 'SERVICE_IDENTITY_MISMATCH')
    require(not value.get('HostConfig', {}).get('Privileged') and value.get('HostConfig', {}).get('PidMode') != 'host', 'UNSAFE_CONTAINER')


def validate_system_networks(rendered):
    """内部网没有宿主端口转发；仅Web接独立入口网，采集/数据面仍在内部网。"""
    networks = rendered.get('networks', {})
    require(set(networks) == {'control', 'storage', 'ingress'}, 'UNEXPECTED_NETWORK')
    require(networks['control'].get('internal') is True and networks['storage'].get('internal') is True
            and networks['ingress'].get('internal', False) is False, 'NETWORK_BOUNDARY_MISMATCH')
    for name, service in rendered.get('services', {}).items():
        attached = set(service.get('networks', {}))
        require('ingress' not in attached or name == 'web', 'NON_WEB_INGRESS_FORBIDDEN')
        if name == 'web':
            require(attached == {'control', 'ingress'}, 'WEB_NETWORK_BOUNDARY_MISMATCH')


class API:
    def __init__(self, origin, secrets):
        require(re.fullmatch(r'http://127\.0\.0\.1:[0-9]{4,5}', origin) is not None, 'NON_LOOPBACK_ORIGIN')
        self.origin, self.csrf = origin, ''
        self.client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        auth = self.call('/auth/login', {'username': (secrets / 'admin_username').read_text().strip(), 'password': (secrets / 'admin_password').read_text().strip()})
        self.csrf = auth['csrf_token']

    def call(self, route, body=None):
        require(route.startswith('/') and '://' not in route and '\\' not in route, 'INVALID_API_ROUTE')
        headers = {'Content-Type': 'application/json', 'Origin': self.origin}
        if body is not None:
            headers.update({'X-CSRF-Token': self.csrf, 'Idempotency-Key': str(uuid.uuid4())})
        request = urllib.request.Request(self.origin + '/api/v1' + route, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with self.client.open(request, timeout=15) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
            require(len(raw) <= 2 * 1024 * 1024, 'API_RESPONSE_BOUND')
            return json.loads(raw)['data']
        except (urllib.error.URLError, OSError, ValueError, KeyError):
            raise LabError('ISOLATED_API_FAILED') from None

    def create(self, duration=2):
        agent = next((a for a in self.call('/agents?page_size=20')['items'] if a['id'] == 'aliyun-local' and 'PROC_CPU' in a.get('capabilities', [])), None)
        require(agent is not None, 'CONTROLLED_AGENT_UNAVAILABLE')
        process = next((p for p in agent.get('resources', {}).get('processes', []) if p.get('pid', 0) > 1 and p.get('uid') == 10001), None)
        require(process is not None, 'CONTROLLED_PROCESS_UNAVAILABLE')
        return str(self.call('/tasks', {'name': 'Synthetic isolated quality ' + uuid.uuid4().hex[:8], 'kind': 'PROC_CPU', 'target_agent_id': agent['id'], 'visibility': 'private', 'parameters': {'pid': process['pid'], 'duration_seconds': duration, 'frequency_hz': 10, 'scope': 'process'}})['id'])

    def task(self, task_id):
        require(re.fullmatch(r'[1-9][0-9]{0,18}', str(task_id)) is not None, 'INVALID_TASK_ID')
        return self.call('/tasks/' + str(task_id))

    def wait(self, task_id, predicate, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.task(task_id)
            if predicate(task):
                return task
            time.sleep(.4)
        raise LabError('TASK_STATE_TIMEOUT')

    def complete(self, task_id):
        task = self.wait(task_id, lambda t: t['collection_status'] in ('FAILED', 'CANCELED') or t['analysis_status'] in ('SUCCESS', 'FAILED'), 90)
        require((task['collection_status'], task['analysis_status']) == ('COLLECTED', 'SUCCESS'), 'REAL_TASK_FAILED')
        return self.verify(task_id, task)

    def verify(self, task_id, task=None):
        task = task or self.task(task_id)
        attempts = task['attempts']
        require(bool(attempts), 'ATTEMPT_MISSING')
        current = max(attempts, key=lambda a: a['attempt'])
        require(current['status'] == 'COLLECTED', 'CURRENT_ATTEMPT_NOT_COLLECTED')
        events = [e for e in task['events'] if str(e.get('attempt_id')) == str(current['id'])]
        counts = Counter(e['type'] for e in events)
        require(counts['COLLECTION_SUCCEEDED'] == 1 and counts['ANALYSIS_SUCCEEDED'] == 1 and counts['TASK_FAILED'] == 0, 'DUPLICATE_OR_MISSING_TERMINAL_EVENT')
        artifacts = [a for a in task['artifacts'] if str(a['attempt_id']) == str(current['id']) and a['status'] == 'ready' and a['kind'] in ('RAW', 'RESULT')]
        require(sum(a['kind'] == 'RAW' for a in artifacts) == 1 and sum(a['kind'] == 'RESULT' for a in artifacts) >= 3, 'ARTIFACT_SET_INCOMPLETE')
        checked = []
        # 独立无Cookie客户端读取精确存储字节，不用透明gzip解压后的浏览器响应做SHA比较。
        downloader = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        for artifact in artifacts:
            signed = self.call(f"/tasks/{task_id}/artifacts/{artifact['id']}/download")
            url = urllib.parse.urljoin(self.origin, signed.get('url') or signed.get('download_url', ''))
            parsed = urllib.parse.urlsplit(url)
            require(parsed.scheme + '://' + parsed.netloc == self.origin and parsed.path.startswith('/objects/') and not parsed.fragment, 'ARTIFACT_ORIGIN_MISMATCH')
            try:
                with downloader.open(urllib.request.Request(url, headers={'Accept-Encoding': 'identity'}), timeout=20) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
            except (urllib.error.URLError, OSError):
                raise LabError('ARTIFACT_DOWNLOAD_FAILED') from None
            require(len(raw) <= 2 * 1024 * 1024 and len(raw) == artifact['size'] and hashlib.sha256(raw).hexdigest() == artifact['sha256'], 'ARTIFACT_BYTES_MISMATCH')
            checked.append({'kind': artifact['kind'], 'sha256': artifact['sha256'], 'size': artifact['size']})
        result = self.call('/tasks/' + str(task_id) + '/result')
        require(result['total_samples'] > 0 and result['summary']['unit'] == 'cpu_ticks' and result['summary']['source'] == 'procfs', 'REAL_CPU_UNIT_MISMATCH')
        return {'task_id': str(task_id), 'attempt': current['attempt'], 'collection_status': task['collection_status'], 'analysis_status': task['analysis_status'], 'total_cpu_ticks': result['total_samples'], 'artifacts': checked, 'terminal_events': dict(counts), 'result_sha256': hashlib.sha256(json.dumps(result, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


class Lab:
    def __init__(self, output, images, bindings):
        self.output = output
        self.private = output / 'private'
        self.private.mkdir(mode=0o700)
        self.instance = 'qa-' + uuid.uuid4().hex[:12]
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.config = dev.settings(self.instance, port, images)
        self.project = self.config['project']
        require(not dev.workspace(self.instance).exists(), 'INSTANCE_ALREADY_EXISTS')
        self.path = dev.prepare(self.config)
        self.bindings = bindings
        self.base = dev.compose(self.config, self.path)
        self.log_number = 0
        self.created = False

    def command(self, argv, timeout=60, allowed_failure=False):
        self.log_number += 1
        try:
            result = subprocess.run(argv, cwd=ROOT, env=dev.environment(), capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise LabError('ISOLATED_COMMAND_TIMEOUT_OR_UNAVAILABLE') from None
        require(len(result.stdout) + len(result.stderr) <= 4 * 1024 * 1024, 'COMMAND_OUTPUT_BOUND')
        (self.private / f'command-{self.log_number:04}.log').write_bytes(result.stdout + result.stderr)
        require(result.returncode == 0 or allowed_failure, 'ISOLATED_COMMAND_FAILED')
        return result

    def prepare(self):
        for kind in ('container', 'volume', 'network'):
            command = ['docker', 'ps', '-aq'] if kind == 'container' else ['docker', kind, 'ls', '-q']
            found = self.command(command + ['--filter', 'label=com.docker.compose.project=' + self.project]).stdout.strip()
            require(not found, 'PROJECT_RESOURCE_COLLISION')
        target = self.path / 'current'
        target.mkdir(mode=0o755)
        for name in ('drop_server', 'drop_agent', 'drop_workload'):
            source = Path(self.bindings['core_binaries']) / name
            require(source.is_file() and not source.is_symlink() and source.stat().st_size <= 64 * 1024 * 1024, 'CURRENT_CORE_BINARY_REQUIRED')
            shutil.copyfile(source, target / name)
            (target / name).chmod(0o755)
        api_source = Path(self.bindings['api_binary'])
        require(api_source.is_file() and not api_source.is_symlink() and api_source.stat().st_size <= 128 * 1024 * 1024, 'CURRENT_API_BINARY_REQUIRED')
        shutil.copyfile(api_source, target / 'drop-api')
        (target / 'drop-api').chmod(0o755)
        dist = Path(self.bindings['web_dist'])
        require((dist / 'index.html').is_file() and not any(p.is_symlink() for p in dist.rglob('*')), 'CURRENT_WEB_DIST_REQUIRED')
        require(sum(p.stat().st_size for p in dist.rglob('*') if p.is_file()) <= 32 * 1024 * 1024, 'WEB_DIST_BOUND')
        shutil.copytree(dist, target / 'web')
        shutil.copytree(ROOT / 'analyzer/drop_analyzer', target / 'drop_analyzer', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copytree(ROOT / 'db/migrations', target / 'migrations')
        shutil.copyfile(ROOT / 'web/nginx.conf', target / 'web-nginx.conf')
        # 独立实例的源码副本不含凭据；容器10001/101可读，但父目录仍不开放秘密文件。
        for file in target.rglob('*'):
            file.chmod(0o755 if file.is_dir() or file.name in ('drop_server', 'drop_agent', 'drop_workload', 'drop-api') else 0o644)
        overlay = json.loads((self.path / 'overlay.json').read_text())
        services = overlay['services']
        limits = {'postgres': ('256m', .4), 'storage-backend': ('256m', .3), 'minio': ('48m', .15), 'drop-server': ('160m', .3), 'api': ('256m', .35), 'analyzer': ('512m', .5), 'agent': ('192m', .4), 'web': ('96m', .2)}
        for service, (memory, cpu) in limits.items():
            services.setdefault(service, {}).update({'mem_limit': memory, 'memswap_limit': memory, 'cpus': cpu, 'pids_limit': 96})
        for service, memory in (('migration', '192m'), ('permission-init', '96m'), ('storage-init', '128m'), ('storage-permission-init', '48m')):
            services.setdefault(service, {}).update({'mem_limit': memory, 'memswap_limit': memory, 'cpus': .25, 'pids_limit': 64})
        # 空存储卷权限初始化仅需要sh/chown，复用显式Core运行镜像，避免额外拉取Ubuntu标签。
        services['storage-permission-init']['image'] = self.config['images']['core']
        for service, definition in services.items():
            definition['restart'] = 'no'
        for service in ('migration', 'api'):
            services[service].setdefault('volumes', []).extend([{'type': 'bind', 'source': str(target / 'drop-api'), 'target': '/usr/local/bin/drop-api', 'read_only': True}, {'type': 'bind', 'source': str(target / 'migrations'), 'target': '/app/db/migrations', 'read_only': True}])
        for service in ('drop-server', 'agent'):
            services[service].setdefault('volumes', []).extend({'type': 'bind', 'source': str(target / name), 'target': '/usr/local/bin/' + name, 'read_only': True} for name in ('drop_server', 'drop_agent', 'drop_workload'))
            services[service].setdefault('environment', {}).update({'TASK_KINDS_FILE': '/etc/drop/task-kinds.json', 'METRICS_LISTEN': 'off'})
            services[service]['volumes'].append({'type': 'bind', 'source': str(self.path / 'task-kinds.json'), 'target': '/etc/drop/task-kinds.json', 'read_only': True})
        services['drop-server']['entrypoint'] = ['/usr/local/bin/drop_server']
        services['agent']['entrypoint'] = ['/usr/local/bin/drop_agent']
        # 本轮GitHub同栈default→native→default实证定位到解析策略差异。
        # 仅隔离QA节点使用系统解析器；不修改生产Compose、证书、APT安全更新或等待阈值。
        services['agent']['environment']['GRPC_DNS_RESOLVER'] = 'native'
        services['analyzer'].update({'entrypoint': ['python', '-m', 'drop_analyzer'], 'working_dir': '/app', 'volumes': [{'type': 'bind', 'source': str(target / 'drop_analyzer'), 'target': '/app/drop_analyzer', 'read_only': True}]})
        services['analyzer']['environment'] = {'ANALYZER_METRICS_PORT': '0'}
        services['web']['volumes'] = [{'type': 'bind', 'source': str(target / 'web'), 'target': '/usr/share/nginx/html', 'read_only': True}, {'type': 'bind', 'source': str(target / 'web-nginx.conf'), 'target': '/etc/nginx/nginx.conf', 'read_only': True}]
        # Docker内部网络不会实际发布宿主端口，即使HostConfig仍列有PortBindings。
        # 单独入口网只连Web，不能为Core/Agent/数据库开放默认外部出口。
        services['web']['networks'] = {'control': {}, 'ingress': {}}
        overlay['networks'] = {'control': {'internal': True}, 'ingress': {'internal': False}}
        atomic_json(self.path / 'overlay.json', overlay)
        rendered = json.loads(self.command(self.base + ['config', '--format', 'json']).stdout)
        dev.validate_rendered(self.config, rendered)
        validate_system_networks(rendered)
        return {str(file.relative_to(target)): digest(file) for file in sorted(target.rglob('*')) if file.is_file()}

    def start(self, bootstrap_image):
        # 独立bootstrap只挂本轮路径；CA签名私钥不进入服务容器。
        self.created = True
        self.command(['docker', 'run', '--rm', '--name', self.project + '-bootstrap', '--label', 'com.docker.compose.project=' + self.project, '--network', 'none', '--user', '0:0', '--cap-drop', 'ALL', '--memory', '128m', '--memory-swap', '128m', '--cpus', '.3', '--pids-limit', '32', '--mount', f'type=bind,src={self.path},dst=/workspace', '--workdir', '/workspace', '--entrypoint', '/bin/bash', bootstrap_image, './bootstrap.sh'], 90)
        self.command(self.base + ['up', '-d', '--no-build', '--wait', '--wait-timeout', '180'], 240)
        for service in ('minio', 'web'):
            self.command(self.base + ['exec', '-T', service, 'nginx', '-s', 'reload'])
        dev.validate_live_web(self.config, self.path)

    def readiness(self):
        # Compose的API healthcheck只有liveness；显式检查真实数据库/S3/Core依赖，不能据healthy推断ready。
        self.container('api')
        self.container('drop-server')
        analyzer = self.container('analyzer')
        program = '''import json,urllib.request,urllib.error
client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
 response=client.open('http://api:8080/health/ready',timeout=6)
except urllib.error.HTTPError as error:
 response=error
except Exception:
 print(json.dumps({'status':0,'dependency':'unavailable'}));raise SystemExit(0)
with response:
 status=response.code
 body=response.read(513)
try:
 value=json.loads(body) if len(body)<=512 else {}
except ValueError:
 value={}
dependency=value.get('dependency') if isinstance(value,dict) else None
dependency=dependency if dependency in ('database','storage','control') else ('none' if status==200 and value=={'status':'ready'} else 'unknown')
print(json.dumps({'status':status,'dependency':dependency}))
'''
        result = json.loads(self.command(['docker', 'exec', analyzer, 'python', '-c', program], 10).stdout)
        require(set(result) == {'status', 'dependency'} and type(result['status']) is int
                and result['status'] in (0, 200, 503) and result['dependency'] in
                ('none', 'database', 'storage', 'control', 'unavailable', 'unknown'), 'INVALID_READINESS_RECEIPT')
        return result

    def collector_dns_resolver(self):
        """从本轮Agent实际运行配置核验固定键；公开收据只返回枚举，不导出环境内容。"""
        container = self.container('agent')
        value = json.loads(self.command(['docker', 'inspect', container]).stdout)[0]
        inspect_owned(value, self.project, 'agent')
        actual = [item for item in value.get('Config', {}).get('Env', []) if item.startswith('GRPC_DNS_RESOLVER=')]
        require(actual == ['GRPC_DNS_RESOLVER=native'], 'COLLECTOR_DNS_RESOLVER_MISMATCH')
        return 'native'

    def container(self, service):
        name = self.command(self.base + ['ps', '-aq', service]).stdout.decode().strip()
        require(re.fullmatch(r'[a-f0-9]{12,64}', name) is not None, 'CONTAINER_ID_MISSING')
        value = json.loads(self.command(['docker', 'inspect', name]).stdout)[0]
        inspect_owned(value, self.project, service)
        return name

    def fault(self, service, action):
        require((service, action) in {('agent', 'kill'), ('agent', 'start'), ('analyzer', 'stop'), ('analyzer', 'start'), ('postgres', 'stop'), ('postgres', 'start')}, 'FAULT_NOT_ALLOWED')
        container = self.container(service)
        arguments = ['docker', action]
        if action == 'stop':
            arguments.extend(['--time', '3'])
        if action == 'kill':
            arguments.extend(['--signal', 'KILL'])
        self.command(arguments + [container], 30)

    def browser(self, args, context_file, artifact_directory):
        script = ROOT / 'web/e2e/quality-live.mjs'
        if not args.browser_image:
            result = self.command([args.node, str(script), str(context_file)], 180, allowed_failure=True)
            return {'mode': 'host Node and installed Chromium', 'business_response_mocking': False, 'exit_code': result.returncode}
        require(re.fullmatch(r'sha256:[a-f0-9]{64}', args.browser_image) is not None, 'IMMUTABLE_BROWSER_IMAGE_REQUIRED')
        actual = self.command(['docker', 'image', 'inspect', args.browser_image, '--format', '{{.Id}}']).stdout.decode().strip()
        require(actual == args.browser_image, 'BROWSER_IMAGE_MISMATCH')
        mounts = [(ROOT / 'web/e2e', True), (ROOT / 'web/node_modules/playwright', True),
                  (ROOT / 'web/node_modules/playwright-core', True), (context_file, True),
                  (self.path / 'secrets/admin_username', True), (self.path / 'secrets/admin_password', True), (artifact_directory, False)]
        arguments = ['docker', 'run', '--rm', '--name', self.project + '-browser',
                     '--label', 'com.docker.compose.project=' + self.project,
                     '--label', 'com.docker.compose.service=quality-browser',
                     '--network', 'host', '--user', '0:0', '--read-only', '--cap-drop', 'ALL',
                     '--security-opt', 'no-new-privileges', '--memory', '768m', '--memory-swap', '768m',
                     '--cpus', '.75', '--pids-limit', '128', '--tmpfs', '/tmp:rw,size=256m,mode=1777',
                     '-e', 'HOME=/tmp', '--entrypoint', 'node']
        for source, readonly in mounts:
            require(source.exists() and not source.is_symlink(), 'BROWSER_BINDING_MISSING_OR_UNSAFE')
            arguments.extend(['--mount', f'type=bind,src={source},dst={source}' + (',readonly' if readonly else '')])
        result = self.command(arguments + [actual, str(script), str(context_file)], 210, allowed_failure=True)
        return {'mode': 'owned immutable Playwright container', 'image_id': actual, 'exit_code': result.returncode, 'memory_limit_bytes': 768 * 1024**2,
                'cpu_quota_cores': .75, 'pids_limit': 128, 'network_mode': 'host',
                'network_boundary': 'browser enforces exact fresh loopback origin; no business response fulfillment',
                'database_storage_or_ca_private_keys_mounted': False}

    def snapshot(self):
        containers = {}
        for service in ('postgres', 'storage-backend', 'minio', 'drop-server', 'api', 'analyzer', 'agent', 'web'):
            container = self.container(service)
            state = json.loads(self.command(['docker', 'inspect', container]).stdout)[0]
            require(state['State']['Running'] and not state['State'].get('OOMKilled'), 'CONTAINER_STOPPED_OR_OOM')
            containers[container[:12]] = service
        lines = self.command(['docker', 'stats', '--no-stream', '--format', '{{json .}}', *containers]).stdout.decode().splitlines()
        observed = []
        for line in lines:
            row = json.loads(line)
            require(row['ID'] in containers, 'RESOURCE_SNAPSHOT_ID_MISMATCH')
            observed.append({'service': containers[row['ID']], 'memory_usage_display': row['MemUsage'], 'cpu_percent_display': row['CPUPerc'], 'pids_display': row['PIDs']})
        require(len(observed) == len(containers), 'RESOURCE_SNAPSHOT_INCOMPLETE')
        return {'monotonic_seconds': time.monotonic(), 'provenance': 'actual docker stats display values, not exact-byte RSS or host meminfo', 'containers': observed}

    def failure_states(self):
        """失败后、清理前保留固定状态字段；不导出inspect中的环境、挂载、命令或日志正文。"""
        states = []
        for service in ('postgres', 'storage-backend', 'minio', 'drop-server', 'api', 'analyzer', 'agent', 'web'):
            try:
                container = self.container(service)
                value = json.loads(self.command(['docker', 'inspect', container]).stdout)[0]
                inspect_owned(value, self.project, service)
                state = value.get('State', {})
                status = state.get('Status')
                health = state.get('Health', {}).get('Status')
                exit_code = state.get('ExitCode')
                states.append({'service': service,
                               'status': status if status in ('created', 'running', 'paused', 'restarting', 'removing', 'exited', 'dead') else 'unknown',
                               'running': state.get('Running') is True,
                               'oom_killed': state.get('OOMKilled') is True,
                               'exit_code': exit_code if type(exit_code) is int and -1 <= exit_code <= 255 else None,
                               'health': health if health in ('starting', 'healthy', 'unhealthy') else 'not_configured'})
            except (LabError, ValueError, KeyError):
                states.append({'service': service, 'error_code': 'OWNED_STATE_UNAVAILABLE'})
        return states

    def failure_core_diagnostics(self):
        diagnostics = {}
        for service in ('drop-server', 'agent'):
            container = self.container(service)
            value = json.loads(self.command(['docker', 'inspect', container]).stdout)[0]
            inspect_owned(value, self.project, service)
            raw = self.command(['docker', 'logs', '--tail', '100', container], allowed_failure=True)
            diagnostics[service] = core_log_projection(raw.stdout + raw.stderr, 'drop-agent' if service == 'agent' else 'drop-server')
            # 只表明代理键是否非空，不能把代理地址或no_proxy内容带入公开报告。
            environment = dict(item.split('=', 1) for item in value.get('Config', {}).get('Env', []) if '=' in item)
            diagnostics[service]['proxy_keys_present'] = {name: bool(environment.get(name)) for name in
                ('grpc_proxy', 'http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY')}
        agent = self.container('agent')
        # 只对已重新验证归属的QA节点做TLS握手，不执行注册、不读取业务内容。
        probe = self.command(['docker', 'exec', agent, 'timeout', '6s', 'openssl', 's_client',
                              '-connect', 'drop-server:50051', '-servername', 'drop-server', '-alpn', 'h2',
                              '-CAfile', '/etc/drop/tls/ca.crt', '-cert', '/etc/drop/tls/agent.crt',
                              '-key', '/etc/drop/tls/agent.key', '-brief', '-no_ign_eof'], 10, allowed_failure=True)
        text = (probe.stdout + probe.stderr).decode('utf-8', errors='replace')
        diagnostics['agent_to_core_tls'] = {'exit_code': probe.returncode,
            'connection_established': 'CONNECTION ESTABLISHED' in text,
            'certificate_verified': 'Verification: OK' in text,
            'scope': 'one six-second bounded openssl handshake between two currently owned QA containers'}
        return diagnostics

    def diagnostic_resolver_comparison(self, initial):
        """仅显式插桩轮、原注册14且零任务时比较解析策略；永不把原失败改成通过。

        每腿重建本轮Agent，并从新CID日志确认注册，避免上一腿数据库中保留的Agent行误报。
        native后再恢复default，区分解析策略与首次启动时序；全部服务、证书、镜像和挂载保持。
        """
        require(initial['agent']['registration_grpc_status_counts']['14'] > 0
                and initial['agent']['events']['registration_succeeded'] == 0
                and initial['drop-server']['events']['rpc_internal_error'] == 0,
                'RESOLVER_DIAGNOSTIC_PRECONDITION')
        require(self.readiness() == {'status': 200, 'dependency': 'none'}, 'RESOLVER_DIAGNOSTIC_CORE_NOT_READY')

        def no_work():
            pg = self.container('postgres')
            row = json.loads(self.command(['docker', 'exec', pg, 'psql', '-U', 'drop', '-d', 'drop', '-XAt', '-c',
                                         "SELECT json_build_object('tasks',(SELECT count(*) FROM tasks),'attempts',(SELECT count(*) FROM task_attempts))"]).stdout)
            require(set(row) == {'tasks', 'attempts'} and all(type(v) is int and v == 0 for v in row.values()), 'RESOLVER_DIAGNOSTIC_NONZERO_WORK')
            return row

        def runtime_signature(expected_resolver=None):
            cid = self.container('agent')
            value = json.loads(self.command(['docker', 'inspect', cid]).stdout)[0]
            inspect_owned(value, self.project, 'agent')
            resolver_values = [v.split('=', 1)[1] for v in value['Config']['Env'] if v.startswith('GRPC_DNS_RESOLVER=')]
            require(resolver_values == ([] if expected_resolver is None else [expected_resolver]), 'RESOLVER_DIAGNOSTIC_RUNTIME_SETTING_MISMATCH')
            return cid, {'image': value['Image'],
                         'environment': sorted(v for v in value['Config']['Env'] if not v.startswith('GRPC_DNS_RESOLVER=')),
                         'mounts': sorted((m['Type'], m['Source'], m['Destination'], m.get('RW')) for m in value['Mounts']),
                         'entrypoint': value['Config']['Entrypoint'], 'command': value['Config']['Cmd'],
                         'user': value['Config']['User'], 'networks': sorted(value['NetworkSettings']['Networks'])}

        no_work()
        old, original_signature = runtime_signature()
        rendered = json.loads(self.command(self.base + ['config', '--format', 'json']).stdout)
        baseline = rendered['services']['agent']
        require('GRPC_DNS_RESOLVER' not in baseline['environment'], 'RESOLVER_DIAGNOSTIC_BASELINE_ALREADY_SET')
        report = {'diagnostic_only': True, 'acceptance_eligible': False, 'original_browser_failure_preserved': True,
                  'initial_default_failed_grpc_14': True, 'legs': [], 'model_calls': 0, 'task_creates': 0}

        for resolver, seconds in (('native', 20), ('default_after_ready', 45)):
            no_work()
            old = self.container('agent')
            self.command(['docker', 'stop', '--time', '3', old], 15)
            overlay = json.loads((self.path / 'overlay.json').read_text())
            environment = overlay['services']['agent']['environment']
            if resolver == 'native':
                environment['GRPC_DNS_RESOLVER'] = 'native'
            else:
                environment.pop('GRPC_DNS_RESOLVER', None)
            atomic_json(self.path / 'overlay.json', overlay)
            rendered = json.loads(self.command(self.base + ['config', '--format', 'json']).stdout)
            dev.validate_rendered(self.config, rendered)
            validate_system_networks(rendered)
            actual = rendered['services']['agent']
            actual['environment'].pop('GRPC_DNS_RESOLVER', None)
            require(actual == baseline, 'RESOLVER_DIAGNOSTIC_UNCONTROLLED_COMPOSE_CHANGE')
            self.command(self.base + ['up', '-d', '--no-deps', '--no-build', '--force-recreate', 'agent'], 45)
            current, signature = runtime_signature('native' if resolver == 'native' else None)
            require(current != old and signature == original_signature, 'RESOLVER_DIAGNOSTIC_UNCONTROLLED_RUNTIME_CHANGE')
            started = time.monotonic()
            projection = None
            while time.monotonic() - started < seconds:
                raw = self.command(['docker', 'logs', '--tail', '100', current], allowed_failure=True)
                projection = core_log_projection(raw.stdout + raw.stderr, 'drop-agent')
                if projection['events']['registration_succeeded'] > 0:
                    break
                time.sleep(1)
            value = json.loads(self.command(['docker', 'inspect', current]).stdout)[0]
            inspect_owned(value, self.project, 'agent')
            state = value.get('State', {})
            row = {'name': resolver, 'registered': projection['events']['registration_succeeded'] > 0,
                   'observed_seconds': time.monotonic() - started, 'new_container_checked': True,
                   'only_resolver_setting_changed': True, 'work_counts': no_work(), 'agent_log_projection': projection,
                   'container_running': state.get('Running') is True, 'container_oom_killed': state.get('OOMKilled') is True,
                   'readiness': self.readiness()}
            report['legs'].append(row)
            atomic_json(self.output / 'resolver-diagnostic.json', report)
            require(row['readiness'] == {'status': 200, 'dependency': 'none'}, 'RESOLVER_DIAGNOSTIC_CORE_CHANGED')
        report['conclusion'] = ('RESOLVER_STRATEGY_DIFFERENCE_REPRODUCED' if report['legs'][0]['registered']
                                and not report['legs'][1]['registered']
                                and report['legs'][1]['agent_log_projection']['registration_grpc_status_counts']['14'] > 0
                                and all(r['container_running'] and not r['container_oom_killed']
                                        and r['readiness'] == {'status': 200, 'dependency': 'none'} for r in report['legs'])
                                else 'NO_CLEAN_RESOLVER_EFFECT')
        atomic_json(self.output / 'resolver-diagnostic.json', report)
        return report

    def cleanup(self):
        if not self.created:
            return {'passed': True, 'scope': 'no compose resources created'}
        ids = self.command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + self.project]).stdout.decode().split()
        for container in ids:
            value = json.loads(self.command(['docker', 'inspect', container]).stdout)[0]
            inspect_owned(value, self.project)
            self.command(['docker', 'rm', '-f', container])
        for kind in ('volume', 'network'):
            names = self.command(['docker', kind, 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + self.project]).stdout.decode().split()
            for name in names:
                value = json.loads(self.command(['docker', kind, 'inspect', name]).stdout)[0]
                # network ls -q返回ID，volume ls -q返回名字；以inspect真实名称与owner双重校验。
                inspected_name = value.get('Name', '')
                require((value.get('Labels') or {}).get('com.docker.compose.project') == self.project
                        and inspected_name.startswith(self.project + '_'), 'CLEANUP_RESOURCE_OWNER_MISMATCH')
                self.command(['docker', kind, 'rm', name])
        return {'passed': True, 'owned_containers_removed': len(ids), 'scope': 'only fresh random Compose project labels; private evidence retained'}


def run(args):
    require(args.output.is_absolute() and not args.output.exists(), 'NEW_ABSOLUTE_OUTPUT_REQUIRED')
    require(args.soak_minutes == 0 or 20 <= args.soak_minutes <= 120, 'SOAK_BUDGET_INVALID')
    require(15 <= args.interval_seconds <= 120, 'SOAK_INTERVAL_INVALID')
    args.output.mkdir(parents=True, mode=0o700)
    images = json.loads(args.images.read_text())
    bindings = json.loads(args.bindings.read_text())
    lab = Lab(args.output, images, bindings)
    summary = {'schema_version': 1, 'passed': False, 'scope': 'disposable real browser -> Go HTTP -> mTLS Core/Agent PROC_CPU -> MinIO -> Analyzer -> browser result; owned recovery and bounded soak', 'production_access': False, 'model_calls': 0, 'phase': 'prepare', 'error_code': None, 'trials': [], 'faults': [], 'soak': {'requested_minutes': args.soak_minutes}, 'cleanup': None}
    try:
        summary['diagnostic_instrumentation'] = bool(args.diagnostic_manifest)
        summary['acceptance_eligible'] = not args.diagnostic_manifest
        if args.diagnostic_manifest:
            path = args.diagnostic_manifest
            require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 16384, 'DIAGNOSTIC_MANIFEST_INVALID')
            manifest = json.loads(path.read_text())
            require(manifest.get('diagnostic_instrumentation') is True and manifest.get('acceptance_eligible') is False
                    and manifest.get('original_source_sha256') == digest(ROOT / 'core/src/agent.cpp'), 'DIAGNOSTIC_SOURCE_BINDING_MISMATCH')
            summary['diagnostic_manifest_sha256'] = digest(path)
        summary['bindings_sha256'] = lab.prepare()
        summary['images'] = {key: lab.command(['docker', 'image', 'inspect', value, '--format', '{{.Id}}']).stdout.decode().strip() for key, value in images.items()}
        summary['phase'] = 'start'
        lab.start(args.bootstrap_image or images['core'])
        summary['collector_dns_resolver'] = lab.collector_dns_resolver()
        summary['phase'] = 'platform_readiness'
        summary['readiness'] = lab.readiness()
        require(summary['readiness'] == {'status': 200, 'dependency': 'none'}, 'PLATFORM_NOT_READY')
        origin = f"http://127.0.0.1:{lab.config['port']}"
        summary['phase'] = 'real_browser'
        artifact_directory = lab.output / 'browser-artifacts'
        artifact_directory.mkdir(mode=0o700)
        context = {'project': lab.project, 'origin': origin, 'secrets': str(lab.path / 'secrets'), 'output': str(artifact_directory / 'browser.json'), 'browser_executable': args.browser_executable}
        context_file = lab.private / 'browser-context.json'
        atomic_json(context_file, context)
        summary['browser_isolation'] = lab.browser(args, context_file, artifact_directory)
        summary['browser'] = json.loads((artifact_directory / 'browser.json').read_text())
        atomic_json(lab.output / 'browser.json', summary['browser'])
        require(summary['browser_isolation']['exit_code'] == 0 and summary['browser']['passed'] is True, 'BROWSER_FAILED')
        api = API(origin, lab.path / 'secrets')
        summary['trials'].append(api.complete(summary['browser']['baseline_task_id']))
        summary['trials'].append(api.complete(summary['browser']['retried_task_id']))
        summary['phase'] = 'agent_strong_kill'
        task_id = api.create(20)
        api.wait(task_id, lambda t: t['collection_status'] == 'RUNNING', 45)
        def accepted_count():
            found = 0
            for event in api.call('/agents/aliyun-local/events')['items']:
                payload = event.get('payload', {})
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if event.get('type') == 'RUNNER_ACCEPTED' and str(payload.get('task_id')) == task_id:
                    found += 1
            return found
        deadline = time.monotonic() + 10
        while accepted_count() != 1:
            require(time.monotonic() < deadline, 'RUNNER_ACCEPTED_EVENT_NOT_PERSISTED')
            time.sleep(.3)
        lab.fault('agent', 'kill')
        lab.fault('agent', 'start')
        failed = api.wait(task_id, lambda t: t['collection_status'] == 'FAILED', 75)
        require(failed['error_code'] == 'AGENT_RESTARTED', 'AGENT_RECOVERY_WRONG_FAILURE')
        require(sum(e['type'] == 'TASK_FAILED' for e in failed['events']) == 1 and len(failed['attempts']) == 1, 'AGENT_RECOVERY_DUPLICATE_TERMINAL')
        require(accepted_count() == 1, 'AGENT_RECOVERY_RESTARTED_SAMPLING')
        summary['faults'].append({'scenario': 'SIGKILL during actual collection, journal recovery', 'task_id': task_id, 'terminal': 'FAILED', 'error_code': 'AGENT_RESTARTED', 'persisted_runner_accepted_events': 1, 'duplicate_sampling_started': False})
        time.sleep(2)
        summary['trials'].append(api.complete(api.create()))
        summary['phase'] = 'analyzer_pending_restart'
        lab.fault('analyzer', 'stop')
        task_id = api.create()
        pending = api.wait(task_id, lambda t: t['collection_status'] == 'COLLECTED', 45)
        require(pending['analysis_status'] == 'PENDING', 'ANALYZER_PENDING_STATE_MISMATCH')
        lab.fault('analyzer', 'start')
        summary['trials'].append(api.complete(task_id))
        summary['faults'].append({'scenario': 'Analyzer stopped before collection; durable pending queue resumes', 'task_id': task_id, 'passed': True, 'excludes': 'not an already leased worker crash test'})
        summary['phase'] = 'postgres_short_outage'
        before = summary['trials'][0]
        lab.fault('postgres', 'stop')
        time.sleep(3)
        lab.fault('postgres', 'start')
        deadline = time.monotonic() + 75
        while True:
            try:
                restored = api.verify(before['task_id'])
                break
            except LabError:
                require(time.monotonic() < deadline, 'POSTGRES_RECOVERY_TIMEOUT')
                time.sleep(1)
        require(before['result_sha256'] == restored['result_sha256'], 'HISTORICAL_RESULT_CHANGED')
        summary['trials'].append(api.complete(api.create()))
        summary['faults'].append({'scenario': 'owned PostgreSQL three-second stop/start, existing session and historical result preserved', 'passed': True})
        summary['phase'] = 'bounded_soak'
        started = time.monotonic()
        deadline = started + args.soak_minutes * 60
        windows = []
        summary['soak']['trials'] = windows
        observations = [lab.snapshot()]
        summary['soak']['resource_observations'] = observations
        while time.monotonic() < deadline:
            tick = time.monotonic()
            active_task = api.create()
            summary['soak']['active_task_id'] = active_task
            windows.append(api.complete(active_task))
            windows[-1]['observed_end_to_end_seconds'] = time.monotonic() - tick
            summary['soak'].update({'completed_windows': len(windows), 'actual_seconds': time.monotonic() - started})
            if len(windows) % 5 == 0:
                observations.append(lab.snapshot())
            atomic_json(lab.output / 'progress.json', {'schema_version': 1, 'completed_windows': len(windows), 'elapsed_seconds': time.monotonic() - started, 'passed_so_far': True, 'not_final': True})
            time.sleep(max(0, min(args.interval_seconds - (time.monotonic() - tick), deadline - time.monotonic())))
        summary['soak'].update({'actual_seconds': time.monotonic() - started, 'interval_seconds': args.interval_seconds, 'completed_windows': len(windows), 'trials': windows, 'scope': 'serial real controlled diagnostics under fixed container caps; not maximum load or days-long leak certification'})
        observations.append(lab.snapshot())
        summary['phase'] = 'complete'
        summary['passed'] = True
    except (LabError, dev.DevError) as error:
        summary['error_code'] = str(error) if isinstance(error, LabError) else 'DEV_SAFETY_REJECTED'
    except Exception:
        summary['error_code'] = 'ISOLATED_LAB_FAILED'
    finally:
        if not summary['passed'] and lab.created:
            try:
                summary['failure_container_states'] = lab.failure_states()
            except Exception:
                summary['failure_state_error'] = 'OWNED_STATE_CAPTURE_FAILED'
            try:
                summary['failure_core_diagnostics'] = lab.failure_core_diagnostics()
            except Exception:
                summary['failure_core_diagnostics_error'] = 'OWNED_CORE_DIAGNOSTICS_UNAVAILABLE'
            if args.diagnostic_manifest and summary.get('failure_core_diagnostics', {}).get('agent', {}).get('registration_grpc_status_counts', {}).get('14', 0) > 0:
                try:
                    summary['resolver_diagnostic'] = lab.diagnostic_resolver_comparison(summary['failure_core_diagnostics'])
                except Exception:
                    summary['resolver_diagnostic_error'] = 'OWNED_RESOLVER_COMPARISON_REJECTED'
        try:
            summary['cleanup'] = lab.cleanup()
        except Exception:
            summary['cleanup'] = {'passed': False, 'error_code': 'OWNED_CLEANUP_FAILED', 'unknown_resources_preserved': True}
            summary['passed'] = False
        atomic_json(lab.output / 'summary.json', summary)
    print(json.dumps({'passed': summary['passed'], 'phase': summary['phase'], 'error_code': summary['error_code'], 'soak_windows': summary['soak'].get('completed_windows', 0), 'cleanup_passed': summary['cleanup']['passed']}))
    return 0 if summary['passed'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--bindings', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bootstrap-image')
    parser.add_argument('--soak-minutes', type=int, default=0)
    parser.add_argument('--interval-seconds', type=int, default=30)
    parser.add_argument('--node', default='node')
    parser.add_argument('--browser-executable', default='')
    parser.add_argument('--browser-image', default='', help='Optional exact local sha256 image ID of official matching Playwright runtime')
    parser.add_argument('--diagnostic-manifest', type=Path, help='Explicit QA-only instrumented-source proof; run is ineligible for final acceptance')
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        return run(args)
    except Exception:
        print(json.dumps({'passed': False, 'error_code': 'LAB_CONFIGURATION_OR_RECEIPT_FAILED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
