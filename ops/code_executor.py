"""通过本机 Unix socket 提供类型化、无业务凭据的源码与测试代理。

只能选择管理员登记的不可变项目和镜像。模型补丁在独立无网络容器内执行，
不挂载 Docker socket、采集 Agent 命名空间、服务凭据、业务数据卷或宿主业务源码。
此代理由宿主负责创建沙箱并核验收据，模型本身没有通用命令执行能力。
"""
import argparse
import ast
import hashlib
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import select
import secrets
import shutil
import socket
import socketserver
import stat
import struct
import subprocess
import threading
import time


class Rejected(Exception):
    pass


PRESET_IMPORTS = {
    'crm-brand-palette-v1': {'copy', 'math', 're', 'functools'},
    'saber-fts5-v1': {'__future__', 're', 'sqlite3', 'typing', 'functools'},
}


def frozen_saber_nodes(tree):
    """只允许两个纯查询辅助函数变化；SQL 与租户处理代码保持冻结。"""
    editable = {'fts_tokenize', 'build_fts_query'}
    result = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in editable:
            continue
        if isinstance(node, ast.ImportFrom) and node.module == 'functools':
            continue
        if isinstance(node, ast.Import) and all(a.name == 'functools' for a in node.names):
            continue
        result.append(ast.dump(node, include_attributes=False))
    return result


def require(value, code):
    if not value:
        raise Rejected(code)


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def peer_disconnected(connection):
    """HTTP 调用方断连即撤销本次同步沙箱任务，不能留下无人接收的后台执行。"""
    try:
        readable, _, _ = select.select([connection], [], [], 0)
        return bool(readable) and connection.recv(1, socket.MSG_PEEK) == b''
    except (OSError, ValueError):
        return True


def owned_container(container, key, image):
    """核对容器的不可变 ID、job 标签和登记镜像；后续取消只针对已确认归属的对象。"""
    inspected = subprocess.run(['docker', 'inspect', container], capture_output=True, timeout=10)
    if inspected.returncode != 0:
        return None
    info = json.loads(inspected.stdout)
    require(isinstance(info, list) and len(info) == 1, 'CONTAINER_OWNERSHIP_MISMATCH')
    info = info[0]
    require(info.get('Config', {}).get('Labels', {}).get('drop.code-job') == key
            and info.get('Image') == image and re.fullmatch(r'[0-9a-f]{64}', info.get('Id', '')),
            'CONTAINER_OWNERSHIP_MISMATCH')
    return info['Id']


def run_sandbox(argv, container, key, image, log, canceled, timeout=240):
    """先 create、核对归属，再 start，确保取消操作持有不可变容器 ID。

    不进行全局进程清理，也不只凭容器名删除；Docker CLI 是本请求的子进程，
    取消时只移除已核对 job 标签与镜像的本次容器，避免影响其他作业。
    """
    require(not canceled(), 'EXECUTION_CANCELED')
    old = owned_container(container, key, image)
    if old:
        subprocess.run(['docker', 'rm', '-f', old], check=True, capture_output=True, timeout=15)
    require(not canceled(), 'EXECUTION_CANCELED')
    created = subprocess.run(argv, capture_output=True, timeout=30)
    require(created.returncode == 0, 'SANDBOX_CREATE_FAILED')
    container_id = created.stdout.decode('ascii').strip()
    require(re.fullmatch(r'[0-9a-f]{64}', container_id), 'SANDBOX_CREATE_FAILED')
    process = None
    try:
        require(owned_container(container_id, key, image) == container_id, 'CONTAINER_OWNERSHIP_MISMATCH')
        require(not canceled(), 'EXECUTION_CANCELED')
        process = subprocess.Popen(['docker', 'start', '--attach', container_id], stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            require(not canceled(), 'EXECUTION_CANCELED')
            if time.monotonic() >= deadline:
                return -1
            time.sleep(0.05)
        require(not canceled(), 'EXECUTION_CANCELED')
        return process.returncode
    finally:
        try:
            if owned_container(container_id, key, image) == container_id:
                subprocess.run(['docker', 'rm', '-f', container_id], check=True, capture_output=True, timeout=15)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)


def execution_key(manifest, kind, edits, execution_id):
    """将执行 ID、项目源码版本、镜像与补丁组合成作业键，隔离不同尝试的测量与缓存收据。"""
    require(isinstance(execution_id, str) and (execution_id == '' or re.fullmatch(r'repair:[1-9][0-9]{0,18}', execution_id)), 'EXECUTION_ID_INVALID')
    return hashlib.sha256(json.dumps([3, execution_id, manifest['id'], manifest['source_revision'], manifest['runner_image_id'], kind, edits], sort_keys=True).encode()).hexdigest()


def validate_patch(base, edits, manifest):
    """校验精确上下文补丁并返回修改后的文件内容，不直接写原业务源码。

    只允许登记的 editable_files，先核对原 SHA 再按序替换；AST 限制导入、反射与缓存规模，
    Saber 额外冻结 SQL/租户相关节点。语法通过不等于行为正确，仍需独立回归与性能对照。
    """
    require(isinstance(edits, list) and 1 <= len(edits) <= 6, 'PATCH_EDIT_LIMIT')
    require(len(json.dumps(edits).encode()) <= 24576, 'PATCH_BYTE_LIMIT')
    changed = {}
    ranges = {}
    for edit in edits:
        require(isinstance(edit, dict) and set(edit) == {'path', 'sha256', 'old', 'new'}, 'PATCH_FIELDS_REJECTED')
        name = edit['path']
        require(name in manifest['editable_files'] and name in manifest['files'], 'PATCH_PATH_NOT_ENROLLED')
        target = (base / name)
        require(target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(base.resolve()), 'PATCH_PATH_ESCAPE')
        require(file_hash(target) == edit['sha256'] == manifest['files'][name], 'PATCH_SOURCE_CHANGED')
        old, new = edit['old'], edit['new']
        require(isinstance(old, str) and isinstance(new, str) and 0 < len(old) <= 16384 and len(new) <= 16384, 'PATCH_CONTENT_LIMIT')
        original = target.read_text(encoding='utf-8')
        require(original.count(old) == 1, 'PATCH_CONTEXT_NOT_UNIQUE')
        start = original.index(old)
        end = start + len(old)
        require(all(end <= a or start >= b for a, b in ranges.get(name, [])), 'PATCH_CONTEXT_OVERLAP')
        ranges.setdefault(name, []).append((start, end))
        # 同一文件可有多次精确上下文编辑；每次都绑定原始登记 SHA，
        # 再按声明顺序应用，避免补丁目标漂移。
        text = changed.get(name, original)
        require(text.count(old) == 1 and old != new, 'PATCH_CONTEXT_NOT_UNIQUE')
        candidate = text.replace(old, new, 1)
        require(len(candidate.encode()) <= 131072, 'PATCH_RESULT_LIMIT')
        try:
            tree = ast.parse(candidate)
        except SyntaxError:
            raise Rejected('PATCH_SYNTAX_INVALID') from None
        # 此 preset 只登记纯计算模块；测试、授权、harness 以及网络/OS 导入
        # 不能通过模型工具修改。
        forbidden = {'eval', 'exec', 'compile', '__import__', 'open', 'getattr', 'setattr', 'globals', 'locals', 'breakpoint', 'input'}
        preset = manifest.get('test_preset', 'crm-brand-palette-v1')
        require(preset in PRESET_IMPORTS, 'PROJECT_PRESET_INVALID')
        imports = PRESET_IMPORTS[preset]
        if preset == 'saber-fts5-v1':
            require(name == 'internal/rag/fts5_index.py', 'PATCH_PATH_NOT_ENROLLED')
            require(frozen_saber_nodes(ast.parse(original)) == frozen_saber_nodes(tree), 'PATCH_FROZEN_SQL_CONTRACT')
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                require(all(alias.name in imports for alias in node.names), 'PATCH_IMPORT_REJECTED')
            if isinstance(node, ast.ImportFrom):
                require(node.level == 0 and node.module in imports, 'PATCH_IMPORT_REJECTED')
            if isinstance(node, ast.Name):
                require(node.id not in forbidden and not node.id.startswith('__'), 'PATCH_REFLECTION_REJECTED')
            if isinstance(node, ast.Attribute):
                require(not node.attr.startswith('__'), 'PATCH_REFLECTION_REJECTED')
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute)):
                function = node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                if function in {'lru_cache', 'cache'}:
                    require(function == 'lru_cache', 'PATCH_UNBOUNDED_CACHE')
                    size = next((arg.value for arg in node.keywords if arg.arg == 'maxsize'), None)
                    require(isinstance(size, ast.Constant) and type(size.value) is int and 1 <= size.value <= 4096, 'PATCH_UNBOUNDED_CACHE')
        changed[name] = candidate
    return changed


class Executor:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.slots = threading.BoundedSemaphore(1)

    def project(self, name, revision=None):
        """读取 root 所有且不可被组/其他用户修改的登记 manifest，校验源码修订与固定镜像摘要。"""
        require(isinstance(name, str) and re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name), 'PROJECT_ID_INVALID')
        root = self.root / 'registry' / name
        require(root.is_dir() and not root.is_symlink() and root.resolve().is_relative_to(self.root), 'PROJECT_NOT_ENROLLED')
        path = root / 'manifest.json'
        require(path.stat().st_uid == 0 and not path.stat().st_mode & 0o022 and path.stat().st_size <= 131072, 'PROJECT_MANIFEST_UNSAFE')
        manifest = json.loads(path.read_text())
        require(manifest.get('id') == name and manifest.get('test_preset') in PRESET_IMPORTS, 'PROJECT_PRESET_INVALID')
        digest = hashlib.sha256(json.dumps(manifest['files'], sort_keys=True).encode()).hexdigest()
        require(digest == manifest['source_revision'] and (revision is None or revision == digest), 'PROJECT_REVISION_MISMATCH')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', manifest['runner_image_id']), 'PROJECT_IMAGE_NOT_PINNED')
        return root, manifest

    def invoke(self, request, canceled=lambda: False):
        """分派闭合动作集合并校验字段；执行型动作必须绑定 source_revision，且受单并发槽限制。"""
        require(not canceled(), 'EXECUTION_CANCELED')
        require(isinstance(request, dict) and set(request) <= {'action', 'project_id', 'source_revision', 'execution_id', 'arguments'}, 'REQUEST_FIELDS_REJECTED')
        action = request.get('action')
        args = request.get('arguments') or {}
        require(isinstance(args, dict), 'ARGUMENTS_INVALID')
        if action == 'projects':
            require(not args, 'ARGUMENTS_INVALID')
            return {'projects': [self.describe(path.name) for path in sorted((self.root / 'registry').iterdir()) if path.is_dir() and not path.is_symlink()]}
        revision = request.get('source_revision') or None
        if action != 'describe':
            require(isinstance(revision, str) and re.fullmatch(r'[0-9a-f]{64}', revision), 'PROJECT_REVISION_REQUIRED')
        root, manifest = self.project(request.get('project_id'), revision)
        source = root / 'source'
        if action == 'describe':
            require(not args, 'ARGUMENTS_INVALID')
            return self.describe(manifest['id'])
        if action == 'read_source':
            require(set(args) <= {'path', 'start_line', 'end_line'}, 'SOURCE_ARGUMENTS_INVALID')
            name = args.get('path')
            require(name in manifest['readable_files'], 'SOURCE_PATH_NOT_ENROLLED')
            path = source / name
            require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(source.resolve()) and file_hash(path) == manifest['files'][name], 'SOURCE_REVISION_MISMATCH')
            start, end = args.get('start_line', 1), args.get('end_line', 160)
            require(type(start) is int and type(end) is int and 1 <= start <= end <= start + 159 and end <= 10000, 'SOURCE_LINE_LIMIT')
            lines = path.read_text(encoding='utf-8').splitlines(keepends=True)
            return {'path': name, 'start_line': start, 'end_line': min(end, len(lines)), 'sha256': manifest['files'][name],
                    'source_revision': manifest['source_revision'], 'content': ''.join(lines[start - 1:end]), 'total_lines': len(lines)}
        if action in {'profile_business_flow', 'test_patch'}:
            require(set(args) <= ({'edits'} if action == 'test_patch' else set()), 'EXECUTION_ARGUMENTS_INVALID')
            changed = validate_patch(source, args.get('edits'), manifest) if action == 'test_patch' else {}
            require(self.slots.acquire(blocking=False), 'EXECUTOR_BUSY')
            try:
                return self.execute(source, manifest, changed, args.get('edits', []), request.get('execution_id', ''), canceled)
            finally:
                self.slots.release()
        raise Rejected('ACTION_NOT_ALLOWED')

    def describe(self, name):
        """返回已登记的项目能力；存在工作负载绑定时，再以实际容器镜像、标签和 /proc 身份核对目标。"""
        _, manifest = self.project(name)
        description = {key: manifest[key] for key in ('id', 'label', 'source_revision', 'editable_files', 'readable_files', 'test_preset', 'workload')}
        # 运行容器与源码的绑定由运维登记，不接受模型提供的绑定关系。
        container = manifest.get('workload_container')
        if container:
            require(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', container), 'RUNTIME_BINDING_INVALID')
            inspected = subprocess.run(['docker', 'inspect', container], capture_output=True, timeout=5)
            if inspected.returncode == 0:
                info = json.loads(inspected.stdout)[0]
                labels = info['Config'].get('Labels') or {}
                if (info['State']['Running'] and info['Image'] == manifest.get('workload_image_id', manifest['runner_image_id'])
                        and labels.get('drop.code-project') == name
                        and labels.get('drop.source-revision') == manifest['source_revision']):
                    host_pid = int(info['State']['Pid'])
                    proc = Path('/proc') / str(host_pid)
                    status = dict(line.split(':', 1) for line in (proc / 'status').read_text().splitlines() if ':' in line)
                    start_ticks = int((proc / 'stat').read_text().rsplit(')', 1)[1].split()[19])
                    description['runtime'] = {'agent_id': manifest['agent_id'], 'pid': int(status['NSpid'].split()[-1]),
                                              'uid': int(status['Uid'].split()[0]), 'start_time_ticks': start_ticks,
                                              'source_revision': manifest['source_revision']}
        return description

    def execute(self, source, manifest, changed, edits, execution_id='', canceled=lambda: False):
        # 执行前验证整个已登记快照，不能只验证模型点名的单个文件。
        """在作业私有目录复制基线与候选，并在固定、无网络、无凭据容器中测量。

        先验证完整登记快照，再以执行键和文件锁隔离并发；仅完整收据可命中缓存。
        上次中断产生的 result.json 必须清除，新的成功/失败结果通过临时文件替换发布，避免半成品恢复误判。
        """
        for name, digest in manifest['files'].items():
            require(not canceled(), 'EXECUTION_CANCELED')
            path = source / name
            require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(source.resolve()) and file_hash(path) == digest, 'PROJECT_SOURCE_CHANGED')
        kind = 'test' if changed else 'profile'
        key = execution_key(manifest, kind, edits, execution_id)
        job = self.root / 'jobs' / key
        job.mkdir(mode=0o755, parents=True, exist_ok=True)
        require(not job.is_symlink() and job.resolve().parent == self.root / 'jobs', 'JOB_PATH_ESCAPE')
        # systemd 使用严格 UMask；挂载根目录仍需允许沙箱 UID10001 穿越，
        # 而宿主上层目录继续保持仅 root 可访问的 0700。
        job.chmod(0o755)
        import fcntl
        with (job / 'lock').open('a') as lock:
            require(not canceled(), 'EXECUTION_CANCELED')
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Rejected('EXECUTOR_BUSY') from None
            receipt = job / 'receipt.json'
            if receipt.exists():
                return {**json.loads(receipt.read_text()), 'cache_hit': True}
            for side in ('baseline', 'candidate') if changed else ('baseline',):
                shutil.copytree(source, job / side, dirs_exist_ok=True)
                # JSON 路由回归需要静态挂载目录；此集成不会因此发布 CRM 的公共 UI。
                if manifest['test_preset'] == 'crm-brand-palette-v1':
                    (job / side / 'crm/static').mkdir(exist_ok=True)
            for name, text in changed.items():
                (job / 'candidate' / name).write_text(text, encoding='utf-8')
            output = job / 'output'
            output.mkdir(mode=0o700, exist_ok=True)
            require(not output.is_symlink() and output.resolve().parent == job.resolve(), 'JOB_PATH_ESCAPE')
            os.chown(output, 10001, 10001)
            # 上次中断的尝试没有有效收据；恢复时必须删除半成品结果，
            # 不能把它当成本轮的新测量。
            previous_result = output / 'result.json'
            if previous_result.exists() or previous_result.is_symlink():
                previous_result.unlink()
            (job / 'job.json').write_text(json.dumps({'seed': secrets.randbits(32)}))
            (job / 'job.json').chmod(0o644)
            container = 'drop-code-' + key[:24]
            argv = ['docker', 'create', '--name', container, '--label', 'drop.code-job=' + key,
                    '--pull', 'never', '--network', 'none', '--user', '10001:10001', '--cap-drop', 'ALL',
                    '--security-opt', 'no-new-privileges:true', '--read-only', '--memory', '512m', '--memory-swap', '512m',
                    '--cpus', '1', '--pids-limit', '64', '--ulimit', 'fsize=1048576:1048576',
                    '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777',
                    '--mount', f'type=bind,src={job},dst=/workspace,readonly',
                    '--mount', f'type=bind,src={output},dst=/output',
                    '--entrypoint', '/opt/drop-tools/venv/bin/python', manifest['runner_image_id'], '/harness/run_job.py', '--action', kind]
            with (job / 'runner.private.log').open('wb') as log:
                code = run_sandbox(argv, container, key, manifest['runner_image_id'], log, canceled)
            require(not canceled(), 'EXECUTION_CANCELED')
            result = output / 'result.json'
            require(result.is_file() and not result.is_symlink() and result.stat().st_size <= 65536, 'SANDBOX_RESULT_MISSING')
            data = json.loads(result.read_text())
            require(isinstance(data, dict), 'SANDBOX_RESULT_INVALID')
            if code != 0:
                data = {'success': False, 'error_code': data.get('error_code', 'SANDBOX_FAILED')}
            data.update({'job_key': key, 'source_revision': manifest['source_revision'], 'project_id': manifest['id'],
                         'executor_contract': 3, 'cache_hit': False,
                         'runner_image_id': manifest['runner_image_id'], 'network': 'none', 'uid': 10001, 'credentials_mounted': False})
            if changed:
                data['patch'] = edits
                data['candidate_files'] = {name: file_hash(job / 'candidate' / name) for name in changed}
            temporary = receipt.with_suffix('.next')
            require(not canceled(), 'EXECUTION_CANCELED')
            temporary.write_text(json.dumps(data), encoding='utf-8')
            temporary.replace(receipt)
            return data


def serve(root, path):
    """在本机 Unix socket 暴露固定 RPC，并用 SO_PEERCRED、请求体大小和响应大小限制保护入口。"""
    executor = Executor(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                require(self.path == '/rpc', 'ENDPOINT_NOT_ALLOWED')
                _, uid, _ = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                require(uid in {0, 10001}, 'PEER_NOT_AUTHORIZED')
                length = int(self.headers.get('Content-Length', '0'))
                require(0 < length <= 49152, 'REQUEST_BYTE_LIMIT')
                data = executor.invoke(json.loads(self.rfile.read(length)), lambda: peer_disconnected(self.connection))
                body = json.dumps({'data': data}).encode()
                require(len(body) <= 131072, 'RESPONSE_BYTE_LIMIT')
                self.send_response(200)
            except Exception as error:
                body = json.dumps({'error_code': str(error) if isinstance(error, Rejected) else 'EXECUTOR_FAILED'}).encode()
                self.send_response(409)
            try:
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True
        request_queue_size = 8

    path = Path(path)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        require(stat.S_ISSOCK(path.lstat().st_mode), 'SOCKET_PATH_UNSAFE')
        path.unlink()
    with Server(str(path), Handler) as server:
        os.chown(path, 0, 10001)
        path.chmod(0o660)
        server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    parser.add_argument('--socket', required=True)
    options = parser.parse_args()
    serve(options.root, options.socket)
