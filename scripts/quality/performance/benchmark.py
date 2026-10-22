"""真实 HTTP、真实 py-spy 的采集扰动、阶梯负载与有界持续运行测量。

只启动本工具自己的合成目标；不接受远端地址、现有 PID 或任意采样命令。
原始 profile 只留在临时目录并自动删除，公共报告只包含计数与摘要。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
from urllib.request import ProxyHandler, build_opener

from target import EXPECTED, serve


def percentile(values, percentile_value):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile_value / 100) - 1)]


def profile_counts(raw):
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('PROFILE_LIMIT')
    weights = unknown = 0
    for line in raw.decode('utf-8', errors='strict').splitlines():
        if not line.strip():
            continue
        stack, value = line.rsplit(' ', 1)
        if not value.isdecimal() or len(value) > 10:
            raise ValueError('PROFILE_WEIGHT_INVALID')
        count = int(value)
        weights += count
        if not stack.strip():
            unknown += count
    if not 0 < weights <= 10000000:
        raise ValueError('PROFILE_SAMPLES_EMPTY_OR_LIMIT')
    return {'observed_stack_weights': weights, 'unresolved_stack_weights': unknown,
            'lost_samples': None, 'loss_counter_available': False}


def process_usage(pid):
    """stat以最后一个右括号分割，避免进程名含空格影响字段编号。"""
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        status = Path(f'/proc/{pid}/status').read_text().splitlines()
        rss = next(int(line.split()[1]) * 1024 for line in status if line.startswith('VmRSS:'))
        return {'cpu_seconds': (int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK'), 'rss_bytes': rss}
    except (OSError, StopIteration, ValueError, IndexError):
        return None


@contextmanager
def target_process():
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=serve, args=(sender,), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(8):
            raise RuntimeError('OWNED_TARGET_START_TIMEOUT')
        port = receiver.recv()
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            raise RuntimeError('OWNED_TARGET_PORT_INVALID')
        yield process.pid, port
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)
        process.close()


def measure(pid, port, seconds, concurrency, sampler=None, rate=0):
    """每个worker固定请求预算；延迟、错误与资源读数分别保留，不重试失败请求。"""
    if not 1 <= seconds <= 60 or not 1 <= concurrency <= 8 or rate not in (0, 49, 99):
        raise ValueError('MEASUREMENT_BUDGET_INVALID')
    profiler = None
    with tempfile.TemporaryDirectory(prefix='drop-perturbation-') as directory:
        profile = Path(directory) / 'sample.raw'
        error_log = tempfile.TemporaryFile()
        try:
            if rate:
                if not sampler or not Path(sampler).is_file():
                    raise ValueError('REAL_SAMPLER_REQUIRED')
                profiler = subprocess.Popen([sampler, 'record', '--pid', str(pid), '--format', 'raw', '--rate', str(rate),
                                             '--duration', str(seconds + 2), '--output', str(profile), '--nonblocking'],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error_log)
                time.sleep(.35)
                if profiler.poll() is not None:
                    raise RuntimeError('REAL_SAMPLER_START_FAILED')
            before = process_usage(pid)
            if before is None:
                raise RuntimeError('TARGET_USAGE_UNAVAILABLE')
            resources = []
            stop = threading.Event()

            def monitor():
                while not stop.is_set():
                    target = process_usage(pid)
                    collector = process_usage(profiler.pid) if profiler else None
                    resources.append({'elapsed_seconds': round(time.monotonic() - begin, 4),
                                      'target': target, 'collector': collector})
                    stop.wait(.1)

            begin = time.monotonic()
            deadline = begin + seconds
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()

            def worker():
                opener = build_opener(ProxyHandler({}))
                latencies, failures, completions = [], {}, 0
                while time.monotonic() < deadline and completions < 5000:
                    tick = time.perf_counter()
                    try:
                        with opener.open(f'http://127.0.0.1:{port}/work', timeout=2) as response:
                            body = response.read(4097)
                            if response.status != 200 or len(body) > 4096 or json.loads(body).get('checksum') != EXPECTED:
                                raise ValueError('RESULT_ORACLE_MISMATCH')
                        latencies.append((time.perf_counter() - tick) * 1000)
                    except Exception as error:
                        code = 'RESULT_ORACLE_MISMATCH' if isinstance(error, ValueError) else 'HTTP_REQUEST_FAILED'
                        failures[code] = failures.get(code, 0) + 1
                    completions += 1
                return latencies, failures

            try:
                with ThreadPoolExecutor(max_workers=concurrency) as executor:
                    results = list(executor.map(lambda _: worker(), range(concurrency)))
            finally:
                stop.set()
                watcher.join(2)
            elapsed = time.monotonic() - begin
            after = process_usage(pid)
            latencies = [latency for values, _ in results for latency in values]
            failures = {}
            for _, codes in results:
                for code, count in codes.items():
                    failures[code] = failures.get(code, 0) + count
            target_rss = [row['target']['rss_bytes'] for row in resources if row['target']]
            collector_rss = [row['collector']['rss_bytes'] for row in resources if row['collector']]
            collector_cpu = [row['collector']['cpu_seconds'] for row in resources if row['collector']]
            sampled = None
            if profiler:
                try:
                    exit_code = profiler.wait(timeout=5)
                except subprocess.TimeoutExpired as error:
                    raise RuntimeError('REAL_SAMPLER_FINISH_TIMEOUT') from error
                if exit_code != 0 or not profile.is_file():
                    raise RuntimeError('REAL_SAMPLER_FAILED')
                with profile.open('rb') as profile_file:
                    sampled = profile_counts(profile_file.read(2 * 1024 * 1024 + 1))
            return {'sampling_hz': rate, 'concurrency': concurrency, 'requested_seconds': seconds,
                    'elapsed_seconds': elapsed, 'successful_requests': len(latencies), 'failures': failures,
                    'throughput_rps': len(latencies) / elapsed,
                    'latency_ms': {f'p{n}': percentile(latencies, n) for n in (50, 95, 99)},
                    'target_peak_rss_bytes': max(target_rss) if target_rss else None,
                    'target_rss_growth_bytes': target_rss[-1] - target_rss[0] if target_rss else None,
                    'target_cpu_percent': 100 * (after['cpu_seconds'] - before['cpu_seconds']) / elapsed if after else None,
                    'collector_peak_rss_bytes': max(collector_rss) if collector_rss else None,
                    'collector_cpu_seconds_in_measured_window': max(collector_cpu) - min(collector_cpu) if len(collector_cpu) > 1 else None,
                    'resource_observations': len(resources), 'sampling': sampled}
        finally:
            if profiler and profiler.poll() is None:
                profiler.terminate()
                try:
                    profiler.wait(2)
                except subprocess.TimeoutExpired:
                    profiler.kill()
                    profiler.wait(2)
            error_log.close()


def evaluate(trials, stages, soak):
    """这些是预先固定的合成回归门槛，不是生产SLA或最大容量证明。"""
    ratios = {}
    for rate in (49, 99):
        pairs = []
        for repeat in range(3):
            base = next(row for row in trials if row['repeat'] == repeat and row['sampling_hz'] == 0)
            collected = next(row for row in trials if row['repeat'] == repeat and row['sampling_hz'] == rate)
            pairs.append({'p95_ratio': collected['latency_ms']['p95'] / base['latency_ms']['p95'] if base['latency_ms']['p95'] and collected['latency_ms']['p95'] is not None else None,
                          'throughput_ratio': collected['throughput_rps'] / base['throughput_rps'] if base['throughput_rps'] else None})
        complete = all(row['p95_ratio'] is not None and row['throughput_ratio'] is not None for row in pairs)
        ratios[str(rate)] = {'pairs': pairs, 'median_p95_ratio': statistics.median(row['p95_ratio'] for row in pairs) if complete else None,
                            'median_throughput_ratio': statistics.median(row['throughput_ratio'] for row in pairs) if complete else None}
    observations = trials + stages + [soak]
    checks = {'requests_and_oracle': all(not row['failures'] and row['successful_requests'] >= 10 for row in observations),
              'bounded_target_memory': all(row['target_peak_rss_bytes'] is not None and row['target_peak_rss_bytes'] <= 128 * 1024 * 1024 for row in observations),
              'collector_observed': all(row['sampling'] and row['collector_peak_rss_bytes'] for row in trials if row['sampling_hz']),
              'synthetic_perturbation_gate': all(row['median_p95_ratio'] is not None and row['median_throughput_ratio'] is not None and row['median_p95_ratio'] <= 2.0 and row['median_throughput_ratio'] >= .5 for row in ratios.values())}
    return {'passed': all(checks.values()), 'checks': checks, 'paired_sampling_effect': ratios,
            'predeclared_gate': {'maximum_median_p95_ratio': 2.0, 'minimum_median_throughput_ratio': .5,
                                 'maximum_target_rss_bytes': 128 * 1024 * 1024, 'minimum_requests_per_window': 10}}


def run(sampler, window=3, soak_seconds=30):
    if os.name != 'posix' or not Path('/proc/self/stat').exists():
        raise ValueError('LINUX_PROC_REQUIRED')
    if not 2 <= window <= 8 or not 10 <= soak_seconds <= 60:
        raise ValueError('RUN_BUDGET_INVALID')
    executable = shutil.which(sampler) if not Path(sampler).is_absolute() else sampler
    if not executable or not Path(executable).is_file():
        raise ValueError('REAL_SAMPLER_REQUIRED')
    trials, stages = [], []
    with target_process() as (pid, port):
        measure(pid, port, 1, 1)
        # 预固定交错顺序，避免始终把冷机基线与热机采样作比较；三次重复仍不是独立业务。
        for repeat, order in enumerate(((0, 49, 99), (99, 0, 49), (49, 99, 0))):
            for rate in order:
                trial = measure(pid, port, window, 2, executable, rate)
                trial['repeat'] = repeat
                trials.append(trial)
        for concurrency in (1, 2, 4):
            stages.append(measure(pid, port, window, concurrency))
        soak = measure(pid, port, soak_seconds, 2)
    return {'schema_version': 1, 'recorded_at': datetime.now(timezone.utc).isoformat(),
            'provenance': 'repository_owned_synthetic_live_http_and_real_pyspy',
            'sampler_binary_sha256': hashlib.sha256(Path(executable).read_bytes()).hexdigest(),
            'target_source_sha256': hashlib.sha256(Path(__file__).with_name('target.py').read_bytes()).hexdigest(),
            'trials': trials, 'stages': stages, 'bounded_soak': soak, 'gate': evaluate(trials, stages, soak),
            'limits': ['Same-host synthetic closed-loop HTTP workload; not CRM or production capacity.',
                       'Finite windows and low sample counts do not establish tail SLO or long-term leak freedom.',
                       'P95/P99 use successful requests; failures are separately counted without retry.',
                       'py-spy provides no loss counter here; weight deficit is never fabricated as sample loss.',
                       'Collector metrics belong to the standalone profiler, not the complete Drop Agent.',
                       'The separate real Core heartbeat experiment measures the control plane.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sampler', default='/opt/drop-tools/venv/bin/py-spy')
    parser.add_argument('--window-seconds', type=int, default=3)
    parser.add_argument('--soak-seconds', type=int, default=30)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('NEW_OUTPUT_REQUIRED')
    try:
        report = run(args.sampler, args.window_seconds, args.soak_seconds)
    except (ValueError, RuntimeError) as error:
        # 只打印本工具定义的固定枚举，不回显采样stderr或原始profile。
        allowed = {'LINUX_PROC_REQUIRED', 'RUN_BUDGET_INVALID', 'REAL_SAMPLER_REQUIRED', 'MEASUREMENT_BUDGET_INVALID',
                   'OWNED_TARGET_START_TIMEOUT', 'OWNED_TARGET_PORT_INVALID', 'REAL_SAMPLER_START_FAILED',
                   'TARGET_USAGE_UNAVAILABLE', 'REAL_SAMPLER_FINISH_TIMEOUT', 'REAL_SAMPLER_FAILED',
                   'PROFILE_LIMIT', 'PROFILE_WEIGHT_INVALID', 'PROFILE_SAMPLES_EMPTY_OR_LIMIT'}
        parser.exit(2, (str(error) if str(error) in allowed else 'MEASUREMENT_INPUT_OR_RUNTIME_FAILED') + '\n')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['gate']['passed'], 'trial_windows': len(report['trials']),
                      'stage_windows': len(report['stages']), 'soak_seconds': args.soak_seconds}))
    return 0 if report['gate']['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
