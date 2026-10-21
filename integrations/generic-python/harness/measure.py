"""通用 Python 项目的可信测量入口；项目提供冻结 workload，驱动负责真实 CPU、cProfile 与类型摘要。

该文件打入管理员固定镜像，模型只能改另一个只读快照中的登记模块。测试和 workload 都不能被补丁覆盖。
本地直接调用只用于仓库自有合成程序的工程验证，不等同于容器隔离已通过。
"""
import argparse
import cProfile
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import random
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout


class BoundedCapture(io.StringIO):
    """连恶意或失控的打印也按字节预算拒绝，避免在内存中无限积累。"""
    def write(self, value):
        count = getattr(self, '_bytes', 0) + len(value.encode('utf-8'))
        if count > 65536:
            raise RuntimeError('MEASUREMENT_STDOUT_LIMIT')
        self._bytes = count
        return super().write(value)


def typed(value, depth=0):
    """按 Python 实际类型编码结果；list/tuple 和 bool/int 不能经 JSON 归一化后混为一谈。"""
    if depth > 16:
        raise RuntimeError('OUTPUT_TYPE_DEPTH')
    kind = type(value)
    if value is None or kind in {str, bool, int}:
        if kind is str and len(value) > 65536:
            raise RuntimeError('OUTPUT_TYPE_LIMIT')
        return [kind.__name__, value]
    if kind is float and math.isfinite(value):
        return ['float', value.hex()]
    if kind in {list, tuple} and len(value) <= 10000:
        return [kind.__name__, [typed(item, depth + 1) for item in value]]
    if kind is dict and len(value) <= 10000:
        rows = [[typed(key, depth + 1), typed(item, depth + 1)] for key, item in value.items()]
        rows.sort(key=lambda row: json.dumps(row[0], sort_keys=True))
        return ['dict', rows]
    raise RuntimeError('OUTPUT_TYPE_UNSUPPORTED')


def mutable_ids(value, depth=0):
    if depth > 16:
        raise RuntimeError('OUTPUT_TYPE_DEPTH')
    ids = {id(value)} if type(value) in {list, dict, set} else set()
    children = value.values() if type(value) is dict else value if type(value) in {list, tuple} else []
    for child in children:
        ids.update(mutable_ids(child, depth + 1))
    return ids


def run_measure(source, manifest, mode, seed):
    entry = manifest['entrypoints'][mode]
    sys.path.insert(0, str(source))
    # importlib 从已登记冻结工作负载加载函数，源模块仍按该快照的 PYTHONPATH 导入。
    spec = importlib.util.spec_from_file_location('drop_registered_workload', source / entry['path'])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, entry['callable'])
    random.seed(seed)
    # 输出不应跨调用共享可变对象；先保留两份真实引用再测，避免 ID 被垃圾回收后重用。
    first, second = function(), function()
    if mutable_ids(first) & mutable_ids(second):
        raise RuntimeError('OUTPUT_MUTABLE_ALIAS')
    expected = typed(first)
    if typed(second) != expected:
        raise RuntimeError('OUTPUT_NONDETERMINISTIC')
    digest = hashlib.sha256()
    profile = cProfile.Profile()
    begin_cpu, begin_wall = time.process_time_ns(), time.perf_counter_ns()
    if mode == 'profile':
        profile.enable()
    for _ in range(entry['calls']):
        actual = typed(function())
        if actual != expected:
            raise RuntimeError('OUTPUT_NONDETERMINISTIC')
        digest.update(json.dumps(actual, ensure_ascii=False, separators=(',', ':')).encode())
    if mode == 'profile':
        profile.disable()
    result = {'cpu_ns': time.process_time_ns() - begin_cpu, 'elapsed_ns': time.perf_counter_ns() - begin_wall,
              'cases': entry['calls'], 'output_sha256': digest.hexdigest(), 'output_type': type(first).__name__,
              'mutable_output_isolated': True, 'measurement': 'python_process_time_ns'}
    if mode == 'profile':
        hotspots = []
        for row in profile.getstats():
            code = row.code
            if isinstance(code, str):
                continue
            filename = Path(code.co_filename).resolve()
            if not filename.is_relative_to(source):
                continue
            name = filename.relative_to(source).as_posix()
            if name not in manifest['files']:
                continue
            hotspots.append({'path': name, 'line': code.co_firstlineno, 'function': code.co_name,
                             'sha256': manifest['files'][name], 'calls': row.callcount,
                             'self_seconds': row.inlinetime, 'total_seconds': row.totaltime})
        hotspots.sort(key=lambda row: (-row['self_seconds'], row['path'], row['line']))
        result['hotspots'] = hotspots[:12]
        result['symbol_mapping'] = 'python_cprofile'
    return result


def run_regression(source, directory):
    """使用真实 unittest Result 计数，不把输出中的 PASS 或子进程自报字符串当验收依据。"""
    sys.path.insert(0, str(source))
    loader = unittest.TestLoader()
    suite = loader.discover(str(directory), pattern='test_*.py')
    result = unittest.TestResult()
    suite.run(result)
    return {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
            'skipped': len(result.skipped), 'unexpected_successes': len(result.unexpectedSuccesses),
            'expected_failures': len(result.expectedFailures), 'success': result.wasSuccessful()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['profile', 'benchmark', 'regression'], required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--tests')
    args = parser.parse_args()
    source = Path(args.source).resolve()
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    # 项目 stdout/stderr 不进入协议或公共日志；RLIMIT_FSIZE 与父进程检查共同限制输出。
    with BoundedCapture() as sink, redirect_stdout(sink), redirect_stderr(sink):
        result = run_regression(source, Path(args.tests).resolve()) if args.mode == 'regression' else run_measure(source, manifest, args.mode, args.seed)
    target = Path(args.output)
    body = json.dumps(result, ensure_ascii=False).encode()
    if len(body) > 32768:
        raise RuntimeError('MEASUREMENT_OUTPUT_LIMIT')
    target.write_bytes(body)


if __name__ == '__main__':
    main()
