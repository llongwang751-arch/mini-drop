"""实际执行仓库自有合成项目，输出可复查的通用接入证据，不请求模型或修改原仓库。

默认是本机可信夹具子进程验证，明确不宣称容器隔离；--docker 则经真正 broker 创建无网络容器。
Docker 验收需要 Linux/root、已审阅固定镜像 ID，不自动拉取镜像或操作任何生产数据卷。
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


broker = load('drop_generic_broker', ROOT / 'ops/code_executor.py')
registry = load('drop_generic_registry', ROOT / 'scripts/code_projects.py')
FIX = "        for _ in range(120):\n            key = str(row['sku']).lower()\n"
NEW = "        key = str(row['sku']).strip().lower()\n"
TEST = """import unittest
from business import price_cart


class WhitespaceSKURegression(unittest.TestCase):
    def test_whitespace_sku_normalizes_to_existing_key(self):
        actual = price_cart([{'sku': ' Book ', 'qty': 1, 'price_cents': 125}])
        self.assertEqual(actual, {'book': 125})
"""


def verify(destination, image_id, docker=False):
    output = Path(destination).resolve()
    broker.require(not output.exists(), 'VERIFICATION_DESTINATION_EXISTS')
    output.mkdir(parents=True)
    project_root = output / 'registry' / 'synthetic-cart-python'
    enrollment = registry.prepare(BASE / 'fixture', BASE / 'fixture/project.json', project_root, image_id)
    manifest = json.loads((project_root / 'manifest.json').read_text(encoding='utf-8'))
    source = project_root / 'source'
    edits = [{'path': 'business.py', 'sha256': manifest['files']['business.py'], 'old': FIX, 'new': NEW}]
    tests = [{'path': 'drop_regression/test_sku.py', 'content': TEST}]
    changed = broker.validate_patch(source, edits, manifest)
    additions = broker.validate_regression_tests(tests, manifest)
    begin = time.monotonic()
    if docker:
        broker.require(sys.platform == 'linux', 'LINUX_DOCKER_REQUIRED')
        executor = broker.Executor(output)
        common = {'project_id': manifest['id'], 'source_revision': manifest['source_revision'], 'execution_id': 'repair:900001'}
        profile = executor.invoke({**common, 'action': 'profile_business_flow', 'arguments': {}})
        result = executor.invoke({**common, 'action': 'test_patch', 'arguments': {'edits': edits, 'regression_tests': tests}})
        broker.require(profile.get('network') == 'none' and result.get('network') == 'none' and result.get('credentials_mounted') is False, 'ISOLATION_RECEIPT_INVALID')
    else:
        job = output / 'local-trusted-job'
        job.mkdir()
        shutil.copytree(source, job / 'baseline')
        shutil.copytree(source, job / 'candidate')
        for name, content in changed.items():
            (job / 'candidate' / name).write_text(content, encoding='utf-8')
        (job / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (job / 'job.json').write_text(json.dumps({'seed': 1984}), encoding='utf-8')
        regression = job / 'regression-tests'
        regression.mkdir()
        for name, content in additions.items():
            (regression / name.rsplit('/', 1)[-1]).write_text(content, encoding='utf-8')
        values = {}
        for action in ('profile', 'test'):
            target = output / ('local-' + action)
            call = subprocess.run([sys.executable, str(BASE / 'harness/run_job.py'), '--action', action, '--workspace', str(job), '--output', str(target)], capture_output=True, timeout=180)
            broker.require(call.returncode == 0, 'LOCAL_HARNESS_EXECUTION_FAILED')
            values[action] = json.loads((target / 'result.json').read_text(encoding='utf-8'))
        profile, result = values['profile'], values['test']
        result['patch_bundle'] = broker.patch_bundle(source, changed, additions, manifest)
    broker.require(profile.get('success') and profile.get('hotspots'), 'PROFILE_NOT_ESTABLISHED')
    broker.require(result.get('success') and result.get('accepted') and result.get('regression_test_validation', {}).get('baseline_failed')
                   and result.get('regression_test_validation', {}).get('candidate_passed') and result.get('negative_control', {}).get('stable'), 'ACTUAL_REGRESSION_OR_PERFORMANCE_GATE_FAILED')
    for name, digest in manifest['files'].items():
        broker.require(hashlib.sha256((source / name).read_bytes()).hexdigest() == digest, 'REGISTERED_SOURCE_MODIFIED')
    receipt = {'schema_version': 1, 'provenance': 'repository_owned_imported_synthetic_python_fixture',
               'model_requests': 0, 'production_requests': 0, 'original_repository_modified': False,
               'isolation': 'docker_network_none_uid10001_readonly_no_credentials' if docker else 'local_trusted_fixture_subprocess_only_not_container_acceptance',
               'enrollment': enrollment, 'profile': profile, 'validation': result, 'elapsed_seconds': round(time.monotonic() - begin, 3)}
    (output / 'verification.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'status': 'passed', 'isolation': receipt['isolation'], 'baseline_failed': True, 'candidate_passed': True,
            'negative_control_stable': True, 'source_revision': manifest['source_revision'], 'median_cpu_speedup': result['median_cpu_speedup']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--docker', action='store_true')
    args = parser.parse_args()
    print(json.dumps(verify(args.output, args.image_id, args.docker), ensure_ascii=False))
