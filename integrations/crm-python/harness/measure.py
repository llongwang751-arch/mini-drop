"""模型可编辑源码之外的固定测量 harness。

只返回 profile 聚合数据；源码路径使用相对路径并与已登记快照核对，不记录原始 pstats/profile 文件。
"""
import argparse
import cProfile
import hashlib
import importlib
import json
from pathlib import Path
import random
import statistics
import sys
import time


def typed(value):
    """仅比较 JSON 会丢失 list/tuple、bool/int 的 Python 类型差异；这里同时编码类型与值。"""
    if type(value) is dict:
        return ['dict', [[typed(key), typed(item)] for key, item in sorted(value.items())]]
    if type(value) in (list, tuple):
        return [type(value).__name__, [typed(item) for item in value]]
    if type(value) in (str, int, float, bool, type(None)):
        return [type(value).__name__, value]
    raise RuntimeError('CRM_OUTPUT_TYPE_UNSUPPORTED')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    source = Path(args.source).resolve()
    sys.path.insert(0, str(source))
    module = importlib.import_module('crm.services.brand_palette')
    rng = random.Random(args.seed)
    seeds = ['#000000', '#ffffff', '#ffff00', '#123456'] + [f'#{rng.randrange(0x1000000):06x}' for _ in range(60)]
    outputs = []
    latencies = []
    profile = cProfile.Profile()
    if args.profile:
        profile.enable()
    start = time.perf_counter_ns()
    cpu_start = time.process_time_ns()
    for seed in seeds:
        tick = time.perf_counter_ns()
        result = module.derive(seed)
        latencies.append(time.perf_counter_ns() - tick)
        outputs.append(result)
    elapsed = time.perf_counter_ns() - start
    cpu_elapsed = time.process_time_ns() - cpu_start
    if args.profile:
        profile.disable()
    serialized = json.dumps(typed(outputs), sort_keys=True, separators=(',', ':')).encode()
    relative = 'crm/services/brand_palette.py'
    result = {'cases': len(seeds), 'output_sha256': hashlib.sha256(serialized).hexdigest(),
              'output_comparison': 'recursive exact Python types and values, including list versus tuple',
              'elapsed_ns': elapsed, 'cpu_ns': cpu_elapsed, 'median_case_ns': statistics.median(latencies),
              'p95_case_ns': sorted(latencies)[int(.95 * (len(latencies) - 1))],
              'source_sha256': hashlib.sha256((source / relative).read_bytes()).hexdigest(),
              'scope': 'actual CRM palette service, synthetic novel brand seeds; not production request latency'}
    if args.profile:
        rows = []
        for stat in profile.getstats():
            code = stat.code
            if not hasattr(code, 'co_filename'):
                continue
            file = Path(code.co_filename).resolve()
            if file != (source / relative).resolve():
                continue
            rows.append({'path': relative, 'line': code.co_firstlineno, 'function': code.co_name,
                         'calls': stat.callcount, 'self_seconds': stat.inlinetime, 'total_seconds': stat.totaltime})
        rows.sort(key=lambda item: item['self_seconds'], reverse=True)
        result['hotspots'] = rows[:20]
        result['profile_unit'] = 'seconds'
        result['profile_source'] = 'actual cProfile execution with exact source-file SHA'
    Path(args.output).write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    main()
