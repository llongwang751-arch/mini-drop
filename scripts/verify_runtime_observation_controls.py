"""Measure actual demo images in owned bounded containers; never grade AI RCA."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
import urllib.request
from uuid import uuid4


def counter_window(before, after):
    for observation in (before, after):
        count = observation.get('io_operations')
        elapsed = observation.get('io_operation_duration_ms_total')
        if (type(count) is not int or count < 0 or type(elapsed) not in (int, float)
                or not math.isfinite(elapsed) or elapsed < 0):
            raise ValueError('Missing or invalid synchronous operation counter')
    count = after['io_operations'] - before['io_operations']
    elapsed = after['io_operation_duration_ms_total'] - before['io_operation_duration_ms_total']
    if count <= 0 or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError('Invalid synchronous operation window')
    return {'operations': count, 'duration_ms': elapsed, 'average_latency_ms': elapsed / count}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', choices=['python', 'java', 'cpp'], required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    port = {'python': 8081, 'java': 8082, 'cpp': 8084}[args.runtime]
    name = 'mini-drop-observation-test-' + uuid4().hex
    result = {'schema': 'mini-drop.runtime-observation-controls.v1',
              'started_at': datetime.now(timezone.utc).isoformat(), 'runtime': args.runtime,
              'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'passed': False, 'cleanup_verified': False, 'observations': [],
              'boundary': 'Actual sync IO and waiting counters; tmpfs is not block-device latency; no AI causal proof.'}

    def docker(*words):
        return subprocess.check_output(['docker', *words], text=True, timeout=25).strip()

    def request(path, payload=None):
        raw = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(address + path, data=raw,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=8) as response:
            return json.load(response)

    try:
        result['image_id'] = docker('image', 'inspect', '--format', '{{.Id}}', args.image)
        docker('run', '-d', '--name', name, '-p', f'127.0.0.1::{port}', '--read-only',
               '--tmpfs', '/tmp:size=64m,mode=1777', '--memory', '512m', '--cpus', '0.65',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128', args.image)
        binding = docker('port', name, str(port) + '/tcp')
        address = 'http://' + binding
        for attempt in range(60):
            try:
                request('/snapshot')
                break
            except (OSError, ValueError):
                if attempt == 59:
                    raise
                time.sleep(.25)
        request('/faults/io/start', {'duration_seconds': 90})
        deadline = time.monotonic() + 60
        while True:
            measured = request('/snapshot')
            result['observations'].append(measured)
            if measured.get('io_failures') != 0 or measured.get('io_fault_active') is not True:
                raise RuntimeError('IO stopped or failed before its bounded lifetime')
            if measured['io_operations'] >= 600 and measured['io_bytes_written'] > 64 * 1024 * 1024:
                before = measured
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('Actual IO did not cross tmpfs capacity within the budget')
            time.sleep(.5)
        time.sleep(2)
        after = request('/snapshot')
        result['observations'].append(after)
        assert after['io_fault_active'] and after['io_failures'] == 0
        assert after['io_bytes_written'] > 64 * 1024 * 1024
        result['io_window'] = counter_window(before, after)
        request('/faults/io/stop', {})
        if args.runtime == 'cpp':
            request('/faults/lock/start', {'duration_seconds': 30})
            before = request('/snapshot')
            time.sleep(3)
            after = request('/snapshot')
            result['lock_window'] = {'before': before, 'after': after}
            assert after['lock_contentions'] > before['lock_contentions']
            assert after['lock_wait_ms'] > before['lock_wait_ms']
            request('/faults/lock/stop', {})
        if args.runtime == 'python':
            request('/faults/noisy/start', {'duration_seconds': 30})
            time.sleep(2)
            before = request('/snapshot')
            stat_before = dict(line.split() for line in docker('exec', name, 'cat', '/sys/fs/cgroup/cpu.stat').splitlines())
            time.sleep(3)
            after = request('/snapshot')
            result['peer_window'] = {'before': before, 'after': after}
            assert after['peer_pid'] == before['peer_pid'] > 0
            assert after['peer_cpu_ticks'] > before['peer_cpu_ticks']
            stat = docker('exec', name, 'cat', '/sys/fs/cgroup/cpu.stat')
            stat_after = dict(line.split() for line in stat.splitlines())
            result['cgroup_window'] = {'before': stat_before, 'after': stat_after,
                                      'cpu_max': docker('exec', name, 'cat', '/sys/fs/cgroup/cpu.max')}
            assert int(stat_after['nr_throttled']) > int(stat_before['nr_throttled'])
            assert int(stat_after['throttled_usec']) > int(stat_before['throttled_usec'])
            request('/faults/noisy/stop', {})
        result['passed'] = True
    except Exception as error:
        result.update(error_type=type(error).__name__, error=str(error))
    finally:
        stopped = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True, timeout=25)
        result['cleanup_verified'] = stopped.returncode == 0
        result['passed'] = result['passed'] and result['cleanup_verified']
        (args.output / 'report.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'runtime': args.runtime, 'passed': result['passed'], 'cleanup': result['cleanup_verified']}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
