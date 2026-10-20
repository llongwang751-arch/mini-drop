"""只在无网络、无特权且不挂载凭据的容器内执行固定测量与回归任务。"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time


PYTHON = '/opt/drop-tools/venv/bin/python'


def command(argv, *, cwd=None, limit=45):
    # stdout/stderr 仅保存在有界临时文件，不把源码或 profile 原文写入日志。
    with open('/tmp/stdout', 'w+b') as out, open('/tmp/stderr', 'w+b') as err:
        try:
            process = subprocess.run(argv, cwd=cwd, stdout=out, stderr=err, timeout=limit,
                                     env={'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(cwd or '')})
        except subprocess.TimeoutExpired:
            return False, 'EXECUTION_TIMEOUT'
        if out.tell() > 65536 or err.tell() > 65536:
            return False, 'EXECUTION_OUTPUT_LIMIT'
        return process.returncode == 0, 'EXECUTION_FAILED' if process.returncode else ''


def measure(source, output, seed, *, profile=False):
    argv = [PYTHON, '/harness/measure.py', '--source', str(source), '--output', str(output), '--seed', str(seed)]
    if profile:
        argv.append('--profile')
    ok, code = command(argv, cwd=source)
    if not ok:
        raise RuntimeError(code)
    return json.loads(output.read_text())


def main():
    """只执行 profile/test 两种固定任务；test 先回归行为，再交替测量相同 seed 的基线与候选。

    输出一致性包含 Python 类型契约，性能门槛仅适用于受控配色组件，不将合成收益推断为生产请求加速。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('--action', choices=['profile', 'test'], required=True)
    args = parser.parse_args()
    root, out = Path('/workspace'), Path('/output')
    config = json.loads((root / 'job.json').read_text())
    start = time.monotonic()
    if args.action == 'profile':
        result = measure(root / 'baseline', out / 'baseline-profile.json', config['seed'], profile=True)
        result['success'] = True
    else:
        # 原始路由、可访问性及对象不可变性测试保持不变。
        ok, code = command([PYTHON, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests/test_brand_palette.py', '/harness/test_source_contract.py'], cwd=root / 'candidate', limit=90)
        if not ok:
            raise RuntimeError('CRM_REGRESSION_' + code)
        pairs = []
        for trial in range(6):
            seed = config['seed'] + trial
            sources = ['baseline', 'candidate'] if trial % 2 == 0 else ['candidate', 'baseline']
            values = {name: measure(root / name, out / f'{trial}-{name}.json', seed) for name in sources}
            if values['baseline']['output_sha256'] != values['candidate']['output_sha256']:
                raise RuntimeError('CRM_OUTPUT_PARITY_FAILED')
            pairs.append({'baseline_ns': values['baseline']['elapsed_ns'], 'candidate_ns': values['candidate']['elapsed_ns'],
                          'baseline_cpu_ns': values['baseline']['cpu_ns'], 'candidate_cpu_ns': values['candidate']['cpu_ns'],
                          'cases': values['baseline']['cases'], 'output_sha256': values['baseline']['output_sha256']})
        ratios = [item['baseline_cpu_ns'] / item['candidate_cpu_ns'] for item in pairs]
        median = statistics.median(ratios)
        improved = sum(ratio > 1.05 for ratio in ratios)
        # 这是受控测试的实用验收门槛，不是通用 SLA 或正式显著性检验；
        # 所有实际测量值仍完整保留。
        accepted = median >= 1.10 and improved >= 5
        result = {'success': True, 'functional_regression': 'passed', 'output_parity_cases': sum(item['cases'] for item in pairs),
                  'pairs': pairs, 'median_cpu_speedup': median, 'improved_pairs': improved, 'accepted': accepted,
                  'gate': {'minimum_median_speedup': 1.10, 'minimum_pairs_above_1_05': 5, 'total_alternating_pairs': 6},
                  'root_cause_status': 'validated_in_controlled_crm_benchmark' if accepted else 'not_validated',
                  'scope': 'isolated CRM palette service with synthetic novel seeds; not production P95/P99 or all CRM functions'}
        result['candidate_profile'] = measure(root / 'candidate', out / 'candidate-profile.json', config['seed'], profile=True)
    result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (out / 'result.json').write_text(json.dumps(result), encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = str(error) if isinstance(error, RuntimeError) and str(error).replace('_', '').isupper() else 'SANDBOX_FAILED'
        Path('/output/result.json').write_text(json.dumps({'success': False, 'error_code': code}), encoding='utf-8')
        raise SystemExit(1) from None
