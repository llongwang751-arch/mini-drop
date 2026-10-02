"""Frozen real windows must reject workload changes, bad answers and artifacts."""
import json
from pathlib import Path
import shutil

import pytest

from scripts.evaluate_same_load_business_fix import evaluate

FIXTURE=Path(__file__).resolve().parents[1]/'reports/quality/interview-completion-20261001/business-fix-r3'


def test_real_business_fix_preserves_workload_quality_and_probe_integrity():
    result=evaluate(FIXTURE)
    assert result['passed'] is True and result['download_sha_verified']==6
    assert result['causal_root_cause_verified_by_agent'] is False


@pytest.mark.parametrize('fault',['candidates','quality','lifetime','raw_bytes','source_bytes','cleanup'])
def test_business_comparison_rejects_changed_inputs_or_unverified_outputs(tmp_path,fault):
    shutil.copytree(FIXTURE,tmp_path/'case');directory=tmp_path/'case'
    if fault in {'raw_bytes','source_bytes'}:
        path=next((directory/('artifacts' if fault=='raw_bytes' else 'source')).iterdir())
        path.write_bytes(path.read_bytes()+b'changed')
    elif fault=='cleanup':
        (directory/'remote-cleanup.json').write_text('{"original_process_exited":false}',encoding='utf-8')
    else:
        path=directory/'after.json';window=json.loads(path.read_text(encoding='utf-8'))
        if fault=='candidates':window['requests'][0]['candidate_count']=1
        elif fault=='quality':window['requests'][0]['quality_passed']=False
        else:window['identity']['start_ticks']+=1
        path.write_text(json.dumps(window),encoding='utf-8')
    assert evaluate(directory)['passed'] is False
