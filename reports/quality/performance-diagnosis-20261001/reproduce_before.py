from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
stage = Path(__file__).resolve().parent
source = stage / 'before-source'
source.mkdir(exist_ok=False)
paths = ['server/app/drop_insight/hypothesis_predicate.py', 'server/app/metric_analyzers.py']
hashes = {}
for name in paths:
    raw = subprocess.check_output(['git', 'show', 'HEAD:'+name], cwd=ROOT)
    target = source / Path(name).name
    target.write_bytes(raw)
    hashes[name] = hashlib.sha256(raw).hexdigest()
(stage/'before-source.json').write_text(json.dumps({'head': subprocess.check_output(['git','rev-parse','HEAD']).decode().strip(),
    'sha256': hashes},indent=2),encoding='utf-8')

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

class FrozenPlugin:
    def pytest_sessionstart(self, session):
        from server.app.drop_insight import hypothesis_predicate as predicate
        from server.app import metric_analyzers as metric
        old_predicate = module('server.app.drop_insight.before_predicate', source/'hypothesis_predicate.py')
        old_metric = module('server.app.before_metric', source/'metric_analyzers.py')
        predicate._structured_signal_predicate = old_predicate._structured_signal_predicate
        metric._derive_signals = old_metric._derive_signals

selectors = ['tests/test_performance_criteria.py::'+name for name in (
    'test_http_wait_cannot_prove_packet_loss_queue_or_compound_claim',
    'test_writing_bytes_cannot_prove_fsync_or_disk_latency',
    'test_retained_memory_is_not_growth_and_compute_activity_is_not_high_cpu',
    'test_old_slow_calls_do_not_make_recovered_window_abnormally_slow')]
import pytest
result = pytest.main(['-q','--junitxml='+str(stage/'before-negative.xml'), *selectors],plugins=[FrozenPlugin()])
assert result == 1, 'Expected old-source behavioral failures, not a harness error or pass'
print(json.dumps({'old_source_expected_failure': True, 'selectors': selectors}))
