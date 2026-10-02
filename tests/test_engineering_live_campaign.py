"""Fault cleanup must survive broken observations and preserve failed evidence."""
import hashlib
from pathlib import Path

import pytest

from scripts import run_engineering_diagnosis_acceptance as runner


def test_download_tamper_is_rejected_before_writing_a_successful_artifact(tmp_path):
    class Client:
        def request_raw(self,*args):return b'changed bytes'
    records={'tasks':[{'task_id':'task-a','artifacts':[{'id':'artifact-a',
        'artifact_type':'sys_metrics','sha256':hashlib.sha256(b'original bytes').hexdigest()}]}]}
    with pytest.raises(ValueError,match='SHA mismatch'):
        runner.download_artifacts(Client(),records,tmp_path)
    assert not list(tmp_path.iterdir())


def test_failed_snapshot_does_not_prevent_fault_withdrawal(monkeypatch,tmp_path):
    calls=[]
    monkeypatch.setattr(runner,'_get_plaza',lambda *args:{'scenarios':[{'scenario_id':'go-memory-growth','active':False}]})
    monkeypatch.setattr(runner,'_start_fault',lambda *args:{'diagnosis_request':{'query':'memory symptom'}})
    monkeypatch.setattr(runner,'_stop_fault',lambda *args:calls.append('stopped'))
    monkeypatch.setattr(runner.time,'sleep',lambda *args:None)
    def measured(*args):
        calls.append('measured')
        if calls.count('measured')==2:raise RuntimeError('fault snapshot broke')
        return {}
    monkeypatch.setattr(runner,'measure',measured)
    monkeypatch.setattr(runner,'evaluate_intervention',lambda *args:{'recovery_observed':False})
    def snapshot(*args):raise RuntimeError('pre-stop snapshot also broke')
    result=runner.run_case(None,{'scenario_id':'go-memory-growth','lab_key':'go','title':'memory','available':True},
        snapshot,tmp_path/'case.json',{'domain':'memory_growth'},agent_id='isolated')
    assert calls==['measured','measured','stopped','measured']
    assert result['cleanup_verified'] is True and result['session_drained'] is False
    assert result['error']=='fault snapshot broke'
    assert result['pre_stop_observation_error']=='pre-stop snapshot also broke'
    assert (tmp_path/'case.json').is_file()


def test_campaign_never_overwrites_existing_records(monkeypatch,tmp_path):
    path=tmp_path/'campaign.json';path.write_text('original evidence',encoding='utf-8')
    with pytest.raises(FileExistsError):
        runner.run_campaign(None,None,path,scenario_ids=['go-memory-growth'],contracts={},provenance={})
    assert path.read_text(encoding='utf-8')=='original evidence'
