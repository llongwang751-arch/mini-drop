from copy import deepcopy
from uuid import uuid4
import pytest
from pydantic import ValidationError
from server.app.drop_insight.business_acceptance import (
    Workload,RequestOutcome,MeasurementWindow,AcceptancePolicy,compare_business_windows,
)


def window(latency,config='a',**kwargs):
    workload=Workload(dataset_sha256='a'*64,request_set_sha256='b'*64,arrival_rate=6,
        request_count=30,concurrency_limit=8,seed=0,warmup_requests=4,environment='test',
        service='knowledge-api',resources_sha256='c'*64)
    return MeasurementWindow(window_id=uuid4().hex,workload=workload,revision='r1',
        config_sha256=config*64,generator='EXTRACTIVE_LOCAL',elapsed_seconds=5,max_dispatch_lag_ms=0,
        requests=[RequestOutcome(request_id=str(i),latency_ms=latency,success=True,
                                 quality_passed=True,trace_id=uuid4().hex,**kwargs) for i in range(30)])


def test_same_load_improvement_requires_quality_and_non_degraded_results():
    b,f,a=window(10),window(100,'b'),window(10)
    check=lambda:compare_business_windows(b,f,a,AcceptancePolicy(),'cap candidates')
    assert check()['outcome']=='IMPROVEMENT_VERIFIED'
    a.requests[0].quality_passed=False
    assert check()['outcome']=='REJECTED'
    a.requests[0].quality_passed=True;a.requests[0].degraded=True
    assert check()['outcome']=='DEGRADED_AVAILABLE'


@pytest.mark.parametrize('field,value',[('arrival_rate',3),('dataset_sha256','d'*64),('concurrency_limit',4),('service','other')])
def test_reduced_load_and_changed_scope_cannot_pass(field,value):
    b,f,a=window(10),window(100,'b'),window(10)
    setattr(a.workload,field,value)
    r=compare_business_windows(b,f,a,AcceptancePolicy(),'fix')
    assert r['outcome']=='INCOMPARABLE'
    assert 'WORKLOAD_OR_ENVIRONMENT_CHANGED' in r['reasons']


def test_missing_requests_nan_and_reused_evidence_are_rejected():
    data=window(10).model_dump();data['requests'].pop()
    with pytest.raises(ValidationError):MeasurementWindow.model_validate(data)
    data=window(10).model_dump();data['requests'][0]['latency_ms']=float('nan')
    with pytest.raises(ValidationError):MeasurementWindow.model_validate(data)
    b,f,a=window(10),window(100,'b'),window(10)
    a.requests[0].trace_id=b.requests[0].trace_id
    assert compare_business_windows(b,f,a,AcceptancePolicy(),'fix')['outcome']=='INCOMPARABLE'


def test_slow_or_unhealthy_baseline_and_load_generator_lag_do_not_pass():
    b,f,a=window(10),window(100,'b'),window(10)
    b.requests[0].success=False;b.requests[0].quality_passed=False
    r=compare_business_windows(b,f,a,AcceptancePolicy(),'fix')
    assert 'INVALID_NORMAL_BASELINE' in r['reasons']
    b=window(10);a.max_dispatch_lag_ms=500
    assert 'LOAD_GENERATOR_LAG' in compare_business_windows(b,f,a,AcceptancePolicy(),'fix')['reasons']


def test_fixture_returns_real_fts_citations_and_marks_timeout_fallback():
    from demo.rag_service.app import KnowledgeService,Settings,QUESTIONS
    service=KnowledgeService(Settings(dependency_latency_ms=10,dependency_timeout_ms=1,allow_extractive_fallback=True))
    try:
        for question,expected in QUESTIONS:
            result=service.query(question)
            assert expected in result['citations'] and result['answer']
            assert result['degraded'] is True
            assert set(result['stage_ms'])=={'queue','retrieval','rerank','generation'}
    finally:service.close()


def test_test_plan_cannot_omit_scenarios_or_missing_test_files():
    import json
    from scripts.check_business_test_plan import ROOT,validate,affected
    plan=json.loads((ROOT/'contracts/business_test_plan.json').read_text(encoding='utf-8'))
    validate(plan)
    expected={'RAG-01','RAG-02','RAG-03','RAG-ACTUAL-01'}
    assert set(affected(plan,['server/app/drop_insight/service.py']))==expected
    assert set(affected(plan,['demo/rag_service/app.py','new/unknown_runtime.py']))==expected
    missing=deepcopy(plan);missing['external_cases']=[]
    with pytest.raises(ValueError):validate(missing)
    missing=deepcopy(plan);missing['cases'].pop()
    with pytest.raises(ValueError):validate(missing)
    missing=deepcopy(plan);missing['cases'][0]['test']='tests/nonexistent.py'
    with pytest.raises(ValueError):validate(missing)


def test_readonly_report_hash_and_rpc_route(monkeypatch,tmp_path):
    import json
    from server.app.drop_insight.business_acceptance import canonical_hash
    from server.app.drop_insight.business_showcase import get_business_acceptance
    from server.app.diagnostic_ai_rpc import dispatch
    path=tmp_path/'view.json'
    monkeypatch.setenv('MINI_DROP_BUSINESS_ACCEPTANCE_VIEW',str(path))
    assert get_business_acceptance()['status']=='NOT_RUN'
    view={'schema':'mini-drop.business-view.v1','status':'AVAILABLE','cases':[]}
    view['report_sha256']=canonical_hash(view)
    path.write_text(json.dumps(view),encoding='utf-8')
    assert get_business_acceptance()['status']=='AVAILABLE'
    assert dispatch('GET','/showcases/business-acceptance','','','viewer').status==200
    view['cases']=[{'ai_root_cause_verified':True}]
    path.write_text(json.dumps(view),encoding='utf-8')
    assert get_business_acceptance()['status']=='INVALID'


def test_actual_source_projection_recomputes_summary_and_rejects_wrong_target(tmp_path):
    import json
    from scripts.build_business_acceptance_view import build
    from server.app.drop_insight.business_acceptance import canonical_hash
    def save(name, value, hash_key):
        value[hash_key]=canonical_hash({k:v for k,v in value.items() if k!=hash_key})
        path=tmp_path/name;path.write_text(json.dumps(value),encoding='utf-8');return path
    campaign={'status':'COMPLETED','results':[],'finished_at':'now','run_id':'run','revision':'r','scope':'local'}
    source=save('campaign.json',campaign,'report_sha256')
    b,f,a=window(10),window(100,'b'),window(10)
    actual={'schema':'mini-drop.actual-rag-acceptance.v1','status':'COMPLETED','completed_at':'now',
            'policy':AcceptancePolicy().model_dump(),'change':'read text columns','business_description':'slow retrieval',
            'windows':dict(zip(('baseline','fault','after'),[w.model_dump() for w in (b,f,a)])),
            'comparison':compare_business_windows(b,f,a,AcceptancePolicy(),'read text columns')}
    path=save('actual.json',actual,'sha256')
    assert build(source,path)['cases'][0]['source_kind']=='ACTUAL_RAG_ENGINE'
    actual['comparison']['summaries']['fault']['p95_ms']=999
    save('actual.json',actual,'sha256')
    with pytest.raises(ValueError,match='comparison'):build(source,path)
    actual['comparison']=compare_business_windows(b,f,a,AcceptancePolicy(),'read text columns')
    save('actual.json',actual,'sha256')
    link=save('link.json',{'target_matches':False},'sha256')
    with pytest.raises(ValueError,match='target mismatch'):build(source,path,link)
