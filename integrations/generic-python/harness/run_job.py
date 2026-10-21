"""按管理员 manifest 执行通用 Python 采样、缺陷复现、原回归和有负控的六组性能对照。

本入口固定在镜像中。新增测试同时作用于 baseline/candidate，不允许修改原测试或冻结工作负载。
baseline 只有断言失败、candidate 全通过才算缺陷转回归完成；随后还必须通过原回归与输出/类型对照。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time


HARNESS = Path(__file__).resolve().parent


def command(argv, source, python, timeout=30):
    """只执行 argv，不启动 shell；环境不继承云凭据，私有输出仅返回安全错误枚举。"""
    env = {'PATH': str(Path(python).parent), 'HOME': tempfile.gettempdir(), 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': str(source), 'PYTHONHASHSEED': '0', 'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1'}
    if os.name == 'nt' and 'SystemRoot' in os.environ:
        env['SystemRoot'] = os.environ['SystemRoot']
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            result = subprocess.run(argv, cwd=source, env=env, stdout=out, stderr=err, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, 'EXECUTION_TIMEOUT'
        if out.tell() > 65536 or err.tell() > 65536:
            return False, 'EXECUTION_OUTPUT_LIMIT'
        return result.returncode == 0, '' if result.returncode == 0 else 'EXECUTION_FAILED'


def measure(source, manifest_path, action, out, seed, python, manifest):
    substitutes = {'{driver}': str(HARNESS / 'measure.py'), '{source}': str(source), '{manifest}': str(manifest_path),
                   '{output}': str(out), '{seed}': str(seed), 'python': python}
    config = manifest['commands'][action]
    argv = [substitutes.get(arg, arg) for arg in config['argv']]
    ok, code = command(argv, source, python, config['timeout_seconds'])
    if not ok:
        raise RuntimeError('MEASUREMENT_' + code)
    if not out.is_file() or out.stat().st_size > 32768:
        raise RuntimeError('MEASUREMENT_OUTPUT_INVALID')
    return json.loads(out.read_text(encoding='utf-8'))


def regression(source, directory, manifest_path, out, python, seed):
    argv = [python, str(HARNESS / 'measure.py'), '--mode', 'regression', '--source', str(source),
            '--tests', str(directory), '--manifest', str(manifest_path), '--output', str(out), '--seed', str(seed)]
    ok, code = command(argv, source, python)
    if not ok:
        raise RuntimeError('REGRESSION_' + code)
    return json.loads(out.read_text(encoding='utf-8'))


def original_checks(source, manifest, python):
    for name in ('build', 'test'):
        config = manifest['commands'][name]
        ok, code = command([python, *config['argv'][1:]], source, python, config['timeout_seconds'])
        if not ok:
            raise RuntimeError('ORIGINAL_' + name.upper() + '_' + code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=['profile', 'test'], required=True)
    parser.add_argument('--workspace', default='/workspace')
    parser.add_argument('--output', default='/output')
    parser.add_argument('--python', default=sys.executable)
    args = parser.parse_args()
    root, out = Path(args.workspace).resolve(), Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    seed = json.loads((root / 'job.json').read_text(encoding='utf-8'))['seed']
    baseline, candidate = root / 'baseline', root / 'candidate'
    begin = time.monotonic()
    original_checks(baseline, manifest, args.python)
    if args.action == 'profile':
        result = measure(baseline, manifest_path, 'profile', out / 'profile.json', seed, args.python, manifest)
        result.update({'success': True, 'original_regression': 'passed'})
    else:
        before = regression(baseline, root / 'regression-tests', manifest_path, out / 'regression-before.json', args.python, seed)
        after = regression(candidate, root / 'regression-tests', manifest_path, out / 'regression-after.json', args.python, seed)
        defect_reproduced = before['tests'] > 0 and before['failures'] > 0 and before['errors'] == 0 and before['skipped'] == 0 and before['expected_failures'] == 0
        defect_fixed = after['tests'] == before['tests'] and after['success'] and not any(after[key] for key in ('failures', 'errors', 'skipped', 'expected_failures', 'unexpected_successes'))
        validation = {'baseline_failed': defect_reproduced, 'candidate_passed': defect_fixed,
                      'baseline': before, 'candidate': after, 'same_test_files': True,
                      'test_sha256': {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted((root / 'regression-tests').glob('test_*.py'))}}
        if not defect_reproduced or not defect_fixed:
            result = {'success': False, 'accepted': False, 'error_code': 'DEFECT_REGRESSION_NOT_ESTABLISHED', 'regression_test_validation': validation}
        else:
            original_checks(candidate, manifest, args.python)
            pairs, controls = [], []
            for trial in range(6):
                order = ['baseline', 'candidate'] if trial % 2 == 0 else ['candidate', 'baseline']
                measured = {side: measure(root / side, manifest_path, 'benchmark', out / f'{trial}-{side}.json', seed + trial, args.python, manifest) for side in order}
                left, right = measured['baseline'], measured['candidate']
                if left['output_sha256'] != right['output_sha256'] or left['output_type'] != right['output_type'] or not right['mutable_output_isolated']:
                    raise RuntimeError('OUTPUT_TYPE_OR_VALUE_PARITY_FAILED')
                if min(left['cpu_ns'], right['cpu_ns']) < 1000000:
                    raise RuntimeError('MEASUREMENT_UNDER_RESOLVED')
                pairs.append({'baseline_cpu_ns': left['cpu_ns'], 'candidate_cpu_ns': right['cpu_ns'], 'cases': left['cases'], 'output_sha256': left['output_sha256']})
                # sham 对照运行两次完全相同的冻结 baseline；它必须显示稳定性，不能冒充一个优化补丁。
                control = measure(baseline, manifest_path, 'benchmark', out / f'{trial}-negative.json', seed + trial, args.python, manifest)
                controls.append(left['cpu_ns'] / control['cpu_ns'])
            ratios = [pair['baseline_cpu_ns'] / pair['candidate_cpu_ns'] for pair in pairs]
            speedup, negative_median = statistics.median(ratios), statistics.median(controls)
            gate = manifest['performance_gate']
            stable = 1 - gate['negative_control_max_drift'] <= negative_median <= 1 + gate['negative_control_max_drift']
            improved = sum(ratio > 1.05 for ratio in ratios)
            accepted = stable and speedup >= gate['minimum_median_speedup'] and improved >= gate['minimum_improved_pairs']
            result = {'success': True, 'accepted': accepted, 'functional_regression': 'passed', 'regression_test_validation': validation,
                      'original_tests_unchanged': True, 'pairs': pairs, 'output_parity_cases': sum(pair['cases'] for pair in pairs),
                      'median_cpu_speedup': speedup, 'improved_pairs': improved,
                      'negative_control': {'kind': 'identical_frozen_baseline', 'ratios': controls, 'median_ratio': negative_median, 'stable': stable},
                      'gate': gate, 'root_cause_status': 'validated_in_controlled_python_benchmark' if accepted else 'not_validated',
                      'scope': 'administrator-registered Python workload on synthetic input; isolated snapshot only; not production latency or whole-project improvement'}
            result['candidate_profile'] = measure(candidate, manifest_path, 'profile', out / 'candidate-profile.json', seed, args.python, manifest)
    result['elapsed_seconds'] = round(time.monotonic() - begin, 3)
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        destination = Path(sys.argv[sys.argv.index('--output') + 1]) if '--output' in sys.argv else Path('/output')
        destination.mkdir(parents=True, exist_ok=True)
        code = str(error) if isinstance(error, RuntimeError) and str(error).replace('_', '').isupper() else 'SANDBOX_FAILED'
        (destination / 'result.json').write_text(json.dumps({'success': False, 'accepted': False, 'error_code': code}), encoding='utf-8')
        raise SystemExit(1) from None
