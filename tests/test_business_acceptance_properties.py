"""Property-based business acceptance tests: the statistics and the verdict
trichotomy must hold for arbitrary sparse inputs, not only crafted examples."""
import math
from uuid import uuid4

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from server.app.drop_insight.business_acceptance import (
    AcceptancePolicy,MeasurementWindow,RequestOutcome,Workload,compare_business_windows,summarize,
)

POLICY = AcceptancePolicy(minimum_requests=10)
STAGES = st.sampled_from(["queue", "retrieval", "rerank", "generation"])
LATENCIES = st.floats(min_value=0, max_value=10_000)


def workload(count):
    return Workload(dataset_sha256='a'*64,request_set_sha256='b'*64,arrival_rate=6,
        request_count=count,concurrency_limit=8,seed=0,warmup_requests=0,
        environment='property-test',service='knowledge-api',resources_sha256='c'*64)


def clean_window(count,latency,config):
    rows=[RequestOutcome(request_id=f'q-{i}',latency_ms=latency,success=True,
        quality_passed=True,trace_id=uuid4().hex) for i in range(count)]
    return MeasurementWindow(window_id=uuid4().hex,workload=workload(count),revision='r1',
        config_sha256=config,generator='EXTRACTIVE_LOCAL',elapsed_seconds=1.0,
        max_dispatch_lag_ms=0,requests=rows)


@st.composite
def sparse_windows(draw):
    count=draw(st.integers(min_value=10,max_value=30))
    rows=[]
    for i in range(count):
        stages=draw(st.dictionaries(STAGES,LATENCIES,max_size=4))
        rows.append(RequestOutcome(request_id=f'q-{i}',latency_ms=draw(LATENCIES),
            success=True,quality_passed=True,trace_id=uuid4().hex,stage_ms=stages))
    return MeasurementWindow(window_id=uuid4().hex,workload=workload(count),revision='r1',
        config_sha256='a'*64,generator='EXTRACTIVE_LOCAL',elapsed_seconds=1.0,
        max_dispatch_lag_ms=0,requests=rows)


@st.composite
def after_windows(draw):
    count=draw(st.integers(min_value=10,max_value=30))
    rows=[]
    for i in range(count):
        failed=draw(st.booleans())
        rows.append(RequestOutcome(request_id=f'q-{i}',latency_ms=0.0,
            success=not failed,quality_passed=not failed,
            degraded=draw(st.booleans()) and not failed,trace_id=uuid4().hex))
    return MeasurementWindow(window_id=uuid4().hex,workload=workload(count),revision='r2',
        config_sha256='b'*64,generator='EXTRACTIVE_LOCAL',elapsed_seconds=1.0,
        max_dispatch_lag_ms=0,requests=rows)


@settings(deadline=None)
@given(window=sparse_windows())
def test_percentiles_are_observed_latencies_in_nearest_rank_order(window):
    summary=summarize(window)
    values=sorted(row.latency_ms for row in window.requests)
    n=len(values)
    assert summary['p50_ms']==values[max(0,math.ceil(n*.5)-1)]
    assert summary['p95_ms']==values[max(0,math.ceil(n*.95)-1)]
    assert summary['p50_ms']<=summary['p95_ms']
    assert summary['p99_ms'] is None          # under the 1000-sample gate
    assert summary['success_rate']==1.0
    assert summary['degraded_count']==0
    assert summary['completed_rps']==n/window.elapsed_seconds


@settings(deadline=None)
@given(window=sparse_windows())
def test_stage_statistics_cover_exactly_the_observed_samples(window):
    summary=summarize(window)
    observed={}
    for row in window.requests:
        for stage,value in row.stage_ms.items():
            observed.setdefault(stage,[]).append(value)
    assert set(summary['stage_p95_ms'])==set(observed)
    assert set(summary['stage_sample_counts'])==set(observed)
    for stage,samples in observed.items():
        assert summary['stage_sample_counts'][stage]==len(samples)
        expected=sorted(samples)[max(0,math.ceil(len(samples)*.95)-1)]
        assert summary['stage_p95_ms'][stage]==expected


@settings(deadline=None)
@given(after=after_windows())
def test_verdict_trichotomy_follows_success_degraded_and_health_flags(after):
    count=after.workload.request_count
    result=compare_business_windows(clean_window(count,10,'a'*64),
        clean_window(count,100,'c'*64),after,POLICY,'property check')
    assert result['reasons']==[]
    if any(not row.success for row in after.requests):
        assert result['outcome']=='REJECTED'
    elif any(row.degraded for row in after.requests):
        assert result['outcome']=='DEGRADED_AVAILABLE'
    else:
        assert result['outcome']=='IMPROVEMENT_VERIFIED'


@settings(deadline=None)
@given(data=sparse_windows(),victim=st.sampled_from(["fault","after"]))
def test_reused_trace_across_windows_is_always_incomparable(data,victim):
    count=data.workload.request_count
    baseline,fault,after=(clean_window(count,10,'a'*64),clean_window(count,100,'c'*64),
                          clean_window(count,10,'a'*64))
    other=fault if victim=="fault" else after
    other.requests[0].trace_id=baseline.requests[0].trace_id
    result=compare_business_windows(baseline,fault,after,POLICY,'property check')
    assert result['outcome']=='INCOMPARABLE'
    assert 'REQUEST_OBSERVATIONS_REUSED' in result['reasons']


WORKLOAD_MUTATIONS={"arrival_rate":7.5,"concurrency_limit":9,"seed":1,
                    "warmup_requests":1,"environment":"other","service":"other-service"}


@settings(deadline=None)
@given(data=sparse_windows(),field=st.sampled_from(sorted(WORKLOAD_MUTATIONS)))
def test_any_single_workload_drift_is_incomparable(data,field):
    count=data.workload.request_count
    baseline,fault,after=(clean_window(count,10,'a'*64),clean_window(count,100,'c'*64),
                          clean_window(count,10,'a'*64))
    setattr(after.workload,field,WORKLOAD_MUTATIONS[field])
    result=compare_business_windows(baseline,fault,after,POLICY,'property check')
    assert result['outcome']=='INCOMPARABLE'
    assert 'WORKLOAD_OR_ENVIRONMENT_CHANGED' in result['reasons']


def test_p99_is_reported_once_the_window_reaches_1000_samples():
    summary=summarize(clean_window(1000,10,'a'*64))
    assert summary['p99_ms']==10
