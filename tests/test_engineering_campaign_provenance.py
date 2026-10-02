"""Current campaigns and required raw downloads must remain independently auditable."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_engineering_diagnosis as builder


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def archive(tmp_path, *, count=1, current=True):
    """Small builder fixtures, explicitly not performance/causal acceptance."""
    root=tmp_path;directory=root/'reports/quality/campaign-fixture'
    cases=[];scenarios=[]
    for i in range(count):
        sid=f'fixture-{i}'
        task=f'task-{i}';payload=f'actual test bytes {i}'.encode('utf-8')
        raw=directory/'cases'/(sid+'-artifacts')/(task+'-raw.raw')
        raw.parent.mkdir(parents=True,exist_ok=True);raw.write_bytes(payload)
        artifact={'id':i+1,'artifact_type':'raw','sha256':digest(payload)}
        case={'scenario_id':sid,'title':'unit fixture','fresh_live_run':True,
            'records':{'tasks':[{'task_id':task,'artifacts':[artifact]}]},
            'downloads':[{'task_id':task,'artifact_id':i+1,'artifact_type':'raw',
                          'sha256':digest(payload),'file':raw.name}],
            'engineering_evaluation':{'diagnosis_accepted':True}}
        path=directory/'cases'/(sid+'.json');write_json(path,case)
        cases.append(case)
        scenarios.append({'scenario_id':sid,'domain':'memory_growth',
            'campaign_id':'seven-gaps-20261002' if i<7 else 'interview-completion-20261001',
            'requires_verified_downloads':True,
            'case_path':path.relative_to(root).as_posix(),'case_sha256':digest(path.read_bytes())})
    plan={'schema':'mini-drop.engineering-diagnosis-contract.v1','profile':'engineering-diagnosis.v1',
        'scenarios':scenarios,'evidence_manifests':[]}
    if current:plan['current_campaign_id']='seven-gaps-20261002'
    repin(root,plan,cases)
    return plan,cases


def repin(root, plan, cases):
    """Re-pin intentionally changed fixture JSON so negative tests exercise linkage."""
    for spec,case in zip(plan['scenarios'],cases):
        path=root/spec['case_path'];write_json(path,case);spec['case_sha256']=digest(path.read_bytes())
    directory=root/'reports/quality/campaign-fixture'
    manifest_path=directory/'manifest.json'
    files=[{'path':p.relative_to(directory).as_posix(),'sha256':digest(p.read_bytes())}
        for p in sorted(directory.rglob('*')) if p.is_file() and p!=manifest_path]
    write_json(manifest_path,{'files':files})
    plan['evidence_manifests']=[{'path':manifest_path.relative_to(root).as_posix(),
                               'sha256':digest(manifest_path.read_bytes())}]
    write_json(root/'contracts/engineering_diagnosis.json',plan)


@pytest.fixture(autouse=True)
def isolate_builder_scoring(monkeypatch):
    # The builder must reject bad downloads even when a permissive upstream
    # grader says yes. Measurement and lineage grading have separate tests.
    monkeypatch.setattr(builder,'evaluate_engineering_case',lambda case,spec:{
        'scenario_id':case['scenario_id'],'diagnosis_id':'unit-only',
        'diagnosis_accepted':True,'localization_accepted':False,'outcome':'SUPPORTED_OBSERVATION',
        'causal_root_cause_verified':False,'same_load_fix_verified':False})


def test_current_seven_and_prior_fourteen_are_not_relabelled_as_twenty_one_new_trials(tmp_path):
    plan,cases=archive(tmp_path,count=21)
    original=deepcopy(cases)
    doc=json.loads(builder.generate(tmp_path))
    assert doc['fresh_live_scenarios']==7 and doc['regraded_prior_scenarios']==14
    assert doc['evaluation_mode']=='MIXED_LIVE_CAMPAIGNS' and doc['fresh_live_run'] is False
    assert all(c['originally_live_record'] is True for c in doc['cases'])
    assert sum(c['fresh_live_run'] for c in doc['cases'])==7
    assert doc['campaigns']==[
        {'campaign_id':'seven-gaps-20261002','evaluated_scenarios':7,'current_campaign':True},
        {'campaign_id':'interview-completion-20261001','evaluated_scenarios':14,'current_campaign':False}]
    assert cases==original


def test_legacy_contract_without_campaign_ids_keeps_the_previous_projection(tmp_path):
    plan,cases=archive(tmp_path,current=False)
    for spec in plan['scenarios']:spec.pop('campaign_id')
    repin(tmp_path,plan,cases)
    doc=json.loads(builder.generate(tmp_path))
    assert doc['fresh_live_scenarios']==1 and doc['fresh_live_run'] is True
    assert 'current_campaign_id' not in doc and 'campaigns' not in doc
    assert 'originally_live_record' not in doc['cases'][0]


@pytest.mark.parametrize('fault',['empty_current','missing_campaign','old_regrading_promoted'])
def test_current_campaign_requires_explicit_case_provenance(tmp_path,fault):
    plan,cases=archive(tmp_path)
    if fault=='empty_current':plan['current_campaign_id']=''
    elif fault=='missing_campaign':plan['scenarios'][0].pop('campaign_id')
    else:cases[0]['fresh_live_run']=False
    repin(tmp_path,plan,cases)
    with pytest.raises(ValueError):builder.generate(tmp_path)


@pytest.mark.parametrize('fault',['missing_downloads','empty_downloads','missing_tasks','empty_artifacts',
    'missing_artifact','wrong_task','wrong_artifact','wrong_kind','wrong_digest','duplicate_download',
    'duplicate_artifact','unarchived_raw','changed_raw_repinned','escaping_filename','records_error','bool_id',
    'duplicate_task','negative_artifact_id','invalid_requirement_flag'])
def test_download_verification_cannot_be_bypassed_by_a_passing_evaluation_override(tmp_path,fault):
    plan,cases=archive(tmp_path);case=cases[0];row=case['downloads'][0]
    raw=tmp_path/Path(plan['scenarios'][0]['case_path']).parent/(case['scenario_id']+'-artifacts')/row['file']
    if fault=='missing_downloads':case.pop('downloads')
    elif fault=='empty_downloads':case['downloads']=[]
    elif fault=='missing_tasks':case['records'].pop('tasks')
    elif fault=='empty_artifacts':case['records']['tasks'][0]['artifacts']=[]
    elif fault=='missing_artifact':
        artifact=deepcopy(case['records']['tasks'][0]['artifacts'][0]);artifact['id']=2
        case['records']['tasks'][0]['artifacts'].append(artifact)
    elif fault=='wrong_task':row['task_id']='another-task'
    elif fault=='wrong_artifact':row['artifact_id']=2
    elif fault=='wrong_kind':row['artifact_type']='sys_metrics'
    elif fault=='wrong_digest':row['sha256']='0'*64
    elif fault=='duplicate_download':case['downloads'].append(deepcopy(row))
    elif fault=='duplicate_artifact':case['records']['tasks'][0]['artifacts'].append(deepcopy(case['records']['tasks'][0]['artifacts'][0]))
    elif fault=='changed_raw_repinned':raw.write_bytes(b'tampered bytes still part of the new manifest')
    elif fault=='escaping_filename':row['file']='../../outside.raw'
    elif fault=='records_error':case['records_error']='download failed'
    elif fault=='bool_id':row['artifact_id']=True
    elif fault=='duplicate_task':case['records']['tasks'].append(deepcopy(case['records']['tasks'][0]))
    elif fault=='negative_artifact_id':
        case['records']['tasks'][0]['artifacts'][0]['id']=-1;row['artifact_id']=-1
    elif fault=='invalid_requirement_flag':plan['scenarios'][0]['requires_verified_downloads']='true'
    repin(tmp_path,plan,cases)
    if fault=='unarchived_raw':
        manifest_path=tmp_path/plan['evidence_manifests'][0]['path']
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['files']=[r for r in manifest['files'] if not r['path'].endswith('.raw')]
        write_json(manifest_path,manifest)
        plan['evidence_manifests'][0]['sha256']=digest(manifest_path.read_bytes())
        write_json(tmp_path/'contracts/engineering_diagnosis.json',plan)
    with pytest.raises((ValueError,FileNotFoundError)):
        builder.generate(tmp_path)


def test_changed_raw_bytes_fail_even_before_task_linkage_if_original_manifest_is_kept(tmp_path):
    plan,cases=archive(tmp_path)
    case=cases[0];row=case['downloads'][0]
    path=tmp_path/Path(plan['scenarios'][0]['case_path']).parent/(case['scenario_id']+'-artifacts')/row['file']
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='archived evidence bytes differ'):
        builder.generate(tmp_path)


def test_prior_cases_can_remain_frozen_without_retroactive_download_requirements(tmp_path):
    plan,cases=archive(tmp_path)
    plan['scenarios'][0].update(campaign_id='previous-campaign',requires_verified_downloads=False)
    cases[0].pop('downloads')
    repin(tmp_path,plan,cases)
    doc=json.loads(builder.generate(tmp_path))
    assert doc['fresh_live_scenarios']==0 and doc['regraded_prior_scenarios']==1
    assert doc['evaluation_mode']=='REGRADING_FROZEN_LIVE_RECORDS'
