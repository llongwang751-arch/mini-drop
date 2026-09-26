"""Repeated business campaigns must record every run and expose spread, not best-of."""
from uuid import uuid4

import pytest

from scripts import run_business_acceptance as runner
from server.app.drop_insight.business_acceptance import (
    Workload,RequestOutcome,MeasurementWindow,
)


def window(latency,config='a',**kwargs):
    workload=Workload(dataset_sha256='a'*64,request_set_sha256='b'*64,arrival_rate=6,
        request_count=30,concurrency_limit=8,seed=0,warmup_requests=4,environment='test',
        service='knowledge-api',resources_sha256='c'*64)
    return MeasurementWindow(window_id=uuid4().hex,workload=workload,revision='r1',
        config_sha256=config*64,generator='EXTRACTIVE_LOCAL',elapsed_seconds=5,max_dispatch_lag_ms=0,
        requests=[RequestOutcome(request_id=str(i),latency_ms=latency,success=True,
                                 quality_passed=True,trace_id=uuid4().hex,**kwargs) for i in range(30)])


def row(sid,run,outcome,p95):
    return {'scenario_id':sid,'run':run,'outcome':outcome,'p95_ms':p95}


def test_aggregate_records_every_run_and_reports_spread():
    summary=runner.aggregate_repeats([
        row('X',1,'IMPROVEMENT_VERIFIED',{'baseline':10,'fault':100,'after':10}),
        row('X',2,'DEGRADED_AVAILABLE',{'baseline':12,'fault':110,'after':10}),
    ])
    scenario=summary['scenarios']['X']
    assert scenario['runs']==2 and scenario['run_indexes']==[1,2]
    assert scenario['outcomes']==['IMPROVEMENT_VERIFIED','DEGRADED_AVAILABLE']
    assert scenario['outcome_stable'] is False
    assert summary['all_outcome_stable'] is False
    assert summary['selection']=='ALL_RUNS_RECORDED; NOT_BEST_OF'
    baseline=scenario['p95_ms']['baseline']
    assert baseline['min']==10 and baseline['max']==12
    assert baseline['mean']==11 and baseline['samples']==2
    assert baseline['stdev']==pytest.approx(1.4142,abs=1e-3)
    assert scenario['p95_ms']['after']['stdev']==0.0


def test_stable_outcomes_aggregate_to_a_single_verdict():
    p95={'baseline':10,'fault':100,'after':10}
    summary=runner.aggregate_repeats([
        row('RAG-01',1,'IMPROVEMENT_VERIFIED',p95),
        row('RAG-01',2,'IMPROVEMENT_VERIFIED',p95),
    ])
    scenario=summary['scenarios']['RAG-01']
    assert scenario['outcome_stable'] is True
    assert summary['all_outcome_stable'] is True


def test_aggregate_refuses_to_fabricate_statistics_from_no_runs():
    with pytest.raises(ValueError):
        runner.aggregate_repeats([])


def test_repeat_bounds_are_rejected_before_any_measurement(tmp_path):
    with pytest.raises(ValueError):
        runner.run(tmp_path/'campaign.json',['RAG-01'],repeat=0)
    with pytest.raises(ValueError):
        runner.run(tmp_path/'campaign.json',['RAG-01'],repeat=11)
    assert not (tmp_path/'campaign.json').exists()


def _campaign(after_factory=None,fault_latency=100):
    """Fake in-order baseline/fault/after measurements; fresh ids per call."""
    state={'call':0}
    def fake_measure(settings,workload,revision,background_import):
        index=state['call'];state['call']+=1
        ingest={'offered_import_batches':0,'completed_import_documents':0}
        if index%3==1:return window(fault_latency,'b'),ingest
        if index%3==2 and after_factory is not None:return after_factory(index),ingest
        return window(10),ingest
    return fake_measure


def test_repeated_campaign_keeps_every_run_and_never_overwrites_case_files(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'measure',_campaign())
    report=runner.run(tmp_path/'campaign.json',['RAG-01'],repeat=2)
    assert report['status']=='COMPLETED' and report['repeat_runs']==2
    assert [entry['run'] for entry in report['results']]==[1,2]
    assert {entry['outcome'] for entry in report['results']}=={'IMPROVEMENT_VERIFIED'}
    cases=tmp_path/'campaign-cases'
    assert (cases/'RAG-01-r01.json').is_file() and (cases/'RAG-01-r02.json').is_file()
    summary=report['repeat_summary']['scenarios']['RAG-01']
    assert summary['runs']==2 and summary['outcome_stable'] is True


def test_unstable_repeat_outcomes_stay_visible_in_summary(tmp_path,monkeypatch):
    after_calls={'n':0}
    def after(index):
        after_calls['n']+=1
        return window(10,degraded=after_calls['n']>1)
    monkeypatch.setattr(runner,'measure',_campaign(after_factory=after))
    report=runner.run(tmp_path/'campaign.json',['RAG-01'],repeat=2)
    outcomes=[entry['outcome'] for entry in report['results']]
    assert outcomes==['IMPROVEMENT_VERIFIED','DEGRADED_AVAILABLE']
    summary=report['repeat_summary']['scenarios']['RAG-01']
    assert summary['outcome_stable'] is False
    assert summary['outcomes']==outcomes


def test_single_run_stays_backward_compatible(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'measure',_campaign())
    report=runner.run(tmp_path/'campaign.json',['RAG-01'],repeat=1)
    assert 'run' not in report['results'][0]
    assert 'repeat_summary' not in report
    assert (tmp_path/'campaign-cases'/'RAG-01.json').is_file()


def test_degradation_scenario_baseline_shares_the_dependency_constant():
    # Repeated real-HTTP runs showed a zero-cost baseline makes the 1.3x
    # recovery gate machine-speed dependent (verdict flipped to REJECTED on a
    # fast machine). The baseline must include the same dependency cost that
    # the post-change timeout keeps, so the ratio travels with the machine.
    case=runner.CASES['RAG-03']
    assert case['baseline'].dependency_latency_ms==case['after'].dependency_timeout_ms>0
