"""在无网络、无生产卷的新容器运行真实采样扰动测试；只清理本次带所有权标签的容器。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid


def command(argv, timeout=30):
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('ISOLATED_CONTROL_FAILED')
    return result.stdout


def save_receipt(output, summary):
    """先完整写入同目录临时文件再替换，失败轮次也必须留下可审计的固定字段。"""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output,
                                         prefix='.isolation-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(summary, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output / 'isolation.json')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def cleanup_owned(name, owner_key, identity):
    """即使 docker run 超时，仍先重新核对标签；查询失败或归属变化时绝不删除。"""
    try:
        result = subprocess.run(['docker', 'inspect', '--format', '{{index .Config.Labels "' + owner_key + '"}}', name],
                                capture_output=True, timeout=15)
        if result.returncode:
            return {'status': 'absent_or_unavailable', 'error_code': 'ISOLATED_CLEANUP_INSPECTION_FAILED'}
        if result.stdout.decode().strip() != identity:
            return {'status': 'foreign_owner_preserved', 'error_code': 'FIXTURE_OWNERSHIP_MISMATCH'}
        command(['docker', 'rm', '-f', name])
        return {'status': 'owned_container_removed', 'error_code': None}
    except (OSError, RuntimeError, subprocess.TimeoutExpired, UnicodeError):
        return {'status': 'cleanup_control_failed', 'error_code': 'ISOLATED_CLEANUP_FAILED'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='drop-agent:20261020.3')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--window-seconds', type=int, default=3)
    parser.add_argument('--soak-seconds', type=int, default=30)
    args = parser.parse_args()
    if not re.fullmatch(r'drop-agent:[A-Za-z0-9_.-]{1,64}', args.image):
        parser.error('REVIEWED_AGENT_IMAGE_REQUIRED')
    if not args.output.is_absolute() or args.output.exists() or not 2 <= args.window_seconds <= 8 or not 10 <= args.soak_seconds <= 60:
        parser.error('NEW_ABSOLUTE_OUTPUT_AND_BOUNDED_TIME_REQUIRED')
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700)
    identity = uuid.uuid4().hex
    name = 'drop-qa-perturbation-' + identity[:12]
    owner_key = 'drop.jd.quality.run'
    source = Path(__file__).resolve().parent
    # 在任何 Docker 操作之前建立失败收据；镜像尚未成功解析时保持 null，而不是填造摘要。
    summary = {'schema_version': 1, 'completed': False, 'passed': False, 'exit_code': None,
               'image': args.image, 'image_id': None, 'source_sha256': {},
               'error_code': None, 'timed_out': False, 'cleanup': None,
               'production_credentials_or_volumes_mounted': False}
    try:
        summary['source_sha256'] = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(source.glob('*.py'))}
        image_id = command(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}']).decode().strip()
        if not re.fullmatch(r'sha256:[0-9a-f]{64}', image_id):
            raise RuntimeError('IMMUTABLE_IMAGE_REQUIRED')
        summary['image_id'] = image_id
        container_args = ['docker', 'run', '--name', name, '--label', f'{owner_key}={identity}', '--network', 'none',
                          '--read-only', '--cap-drop', 'ALL', '--cap-add', 'SYS_PTRACE', '--security-opt', 'no-new-privileges',
                          '--memory', '384m', '--memory-swap', '384m', '--cpus', '.75', '--pids-limit', '64',
                          '--tmpfs', '/tmp:rw,size=64m', '--mount', f'type=bind,src={source},dst=/qa,readonly',
                          '--mount', f'type=bind,src={output},dst=/output', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                          '--user', '0:0', '--entrypoint', '/opt/drop-tools/venv/bin/python', image_id,
                          '/qa/benchmark.py', '--window-seconds', str(args.window_seconds), '--soak-seconds', str(args.soak_seconds),
                          '--output', '/output/performance.json']
        with (output / 'private-driver.log').open('wb') as log:
            result = subprocess.run(container_args, stdout=log, stderr=log, timeout=300)
        summary['exit_code'] = result.returncode
        inspected = json.loads(command(['docker', 'inspect', name]))[0]
        if inspected['Config']['Labels'].get(owner_key) != identity:
            raise RuntimeError('FIXTURE_OWNERSHIP_MISMATCH')
        summary.update({'network_mode': inspected['HostConfig']['NetworkMode'],
                        'memory_limit_bytes': inspected['HostConfig']['Memory'], 'nanocpus': inspected['HostConfig']['NanoCpus'],
                        'pids_limit': inspected['HostConfig']['PidsLimit'], 'oom_killed': inspected['State']['OOMKilled']})
        if (summary['network_mode'], summary['memory_limit_bytes'], summary['nanocpus'], summary['pids_limit']) != ('none', 384 * 1024 * 1024, 750000000, 64):
            raise RuntimeError('FIXTURE_ISOLATION_MISMATCH')
        if summary['exit_code'] != 0:
            raise RuntimeError('MEASUREMENT_PROCESS_FAILED')
        if summary['oom_killed']:
            raise RuntimeError('MEASUREMENT_OOM')
        summary['passed'] = True
    except subprocess.TimeoutExpired:
        summary.update({'error_code': 'ISOLATED_TIMEOUT', 'timed_out': True})
    except (OSError, UnicodeError):
        summary['error_code'] = 'ISOLATED_DRIVER_IO_FAILED'
    except (ValueError, KeyError, IndexError, TypeError):
        summary['error_code'] = 'ISOLATED_INSPECTION_INVALID'
    except RuntimeError as error:
        allowed = {'ISOLATED_CONTROL_FAILED', 'IMMUTABLE_IMAGE_REQUIRED', 'FIXTURE_OWNERSHIP_MISMATCH',
                   'FIXTURE_ISOLATION_MISMATCH', 'MEASUREMENT_PROCESS_FAILED', 'MEASUREMENT_OOM'}
        summary['error_code'] = str(error) if str(error) in allowed else 'ISOLATED_CONTROL_FAILED'
    finally:
        # 收集已生成的报告摘要；即使超时或 JSON 只写了一半，原失败原因也不能被覆盖。
        report_file = output / 'performance.json'
        try:
            summary['completed'] = report_file.is_file()
            if not summary['completed'] or report_file.is_symlink() or report_file.stat().st_size > 512 * 1024:
                raise ValueError('missing or unsafe report')
            with report_file.open('rb') as stream:
                raw = stream.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                raise ValueError('report grew beyond bound')
            summary['report_sha256'] = hashlib.sha256(raw).hexdigest()
            report = json.loads(raw)
            if report.get('schema_version') != 1 or not isinstance(report.get('gate', {}).get('passed'), bool):
                raise ValueError('invalid report schema')
            if not report['gate']['passed'] and summary['error_code'] is None:
                summary['error_code'] = 'MEASUREMENT_GATE_FAILED'
        except (OSError, ValueError, AttributeError, TypeError):
            if summary['error_code'] is None:
                summary['error_code'] = 'ISOLATED_REPORT_INVALID' if summary['completed'] else 'ISOLATED_REPORT_MISSING'
        summary['cleanup'] = cleanup_owned(name, owner_key, identity)
        if summary['cleanup']['error_code'] and summary['error_code'] is None:
            summary['error_code'] = summary['cleanup']['error_code']
        summary['passed'] = summary['passed'] and summary['error_code'] is None
        try:
            save_receipt(output, summary)
        except OSError:
            # 目录不可写时至少返回固定失败状态，不能把异常正文或私有日志打印到终端。
            summary.update({'passed': False, 'error_code': 'ISOLATED_RECEIPT_WRITE_FAILED'})
    print(json.dumps({'passed': summary['passed'], 'image_id': summary['image_id'], 'report_available': summary['completed'],
                      'error_code': summary['error_code'], 'cleanup_status': summary['cleanup']['status']}))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
