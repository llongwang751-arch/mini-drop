"""Exercise >64 MiB of real writes in an owned 64 MiB tmpfs Docker fixture."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
from uuid import uuid4


def window_average(before, after):
    count = after['io_operations'] - before['io_operations']
    duration = after['io_operation_duration_ms_total'] - before['io_operation_duration_ms_total']
    if count <= 0 or duration < 0 or not math.isfinite(duration):
        raise ValueError('Missing operations or invalid elapsed counter window')
    return duration / count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    name = 'mini-drop-go-io-test-' + uuid4().hex
    result = {'schema': 'mini-drop.go-io-window.v1', 'started_at': datetime.now(timezone.utc).isoformat(),
              'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'image': args.image, 'tmpfs_bytes': 64 * 1024 * 1024, 'minimum_operations': 600,
              'passed': False, 'observations': [], 'cleanup_verified': False,
              'boundary': 'Real synchronous operations in tmpfs; not disk latency or an AI causal RCA result.'}

    def docker(*words):
        return subprocess.check_output(['docker', *words], text=True, timeout=20).strip()

    def request(path, payload=None):
        command = ['exec', name, 'wget', '-q', '-O', '-']
        if payload is not None:
            command += ['--header=Content-Type: application/json', '--post-data=' + json.dumps(payload)]
        return json.loads(docker(*command, 'http://127.0.0.1:6060' + path))

    try:
        result['image_id'] = docker('image', 'inspect', '--format', '{{.Id}}', args.image)
        docker('run', '-d', '--name', name, '--network', 'none', '--read-only',
               '--tmpfs', '/tmp:size=64m,mode=1777', '--memory', '192m', '--cpus', '0.65',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '64', args.image)
        for _ in range(20):
            try:
                request('/health')
                break
            except subprocess.CalledProcessError:
                time.sleep(.2)
        request('/faults/io/start', {'duration_seconds': 75})
        deadline = time.monotonic() + 40
        while True:
            observation = request('/snapshot')
            result['observations'].append(observation)
            if observation['io_failures'] or not observation['io_fault_active']:
                result['filesystem_usage'] = docker('exec', name, 'df', '-k', '/tmp')
                raise RuntimeError('I/O fixture stopped or failed before its declared duration')
            if observation['io_operations'] >= 600:
                before = observation
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Fixture did not exercise the required rotations within its bounded budget')
            time.sleep(1)
        time.sleep(2)
        after = request('/snapshot')
        result['observations'].append(after)
        assert after['io_fault_active'] and after['io_failures'] == 0
        result['window_average_latency_ms'] = window_average(before, after)
        result['passed'] = True
    except Exception as error:
        result['error_type'] = type(error).__name__
        result['error'] = str(error)
    finally:
        try:
            result['stop'] = request('/faults/io/stop', {})
        except Exception:
            pass
        cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=20)
        result['cleanup_verified'] = cleanup.returncode == 0
        result['passed'] = result['passed'] and result['cleanup_verified']
        (args.output / 'report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'passed': result['passed'], 'cleanup': result['cleanup_verified'],
                      'operations': result['observations'][-1]['io_operations'] if result['observations'] else 0}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
