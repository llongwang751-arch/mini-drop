"""Registration and window counterexamples; these are not live acceptance."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from analyzer.mini_drop_analyzer import hotmethod_analyzer
from server.app.drop_insight.cpu_criteria import cpu_observation_plan, cpu_utilization_hypothesis, compile_cpu_observation_contract
from server.app.drop_insight.hypothesis_predicate import _compute_hypothesis_predicate, _structured_signal_predicate
from server.app.drop_insight.performance_criteria import performance_observation_plan
from server.app.drop_insight.signal_window_validation import validate_signal_window
from server.app.metric_analyzers import _parse_sys_metrics_document


def hypothesis(plan):
    return SimpleNamespace(statement=plan['statement'],
        expected_observations_json=plan.get('expected', plan.get('expected_observations')),
        falsification_criteria_json=plan.get('falsification', plan.get('falsification_criteria')))


def test_actual_source_query_can_select_registered_cpu_observations():
    assert cpu_utilization_hypothesis('先确认 CPU 异常，再定位源码级热点函数')
    assert not cpu_utilization_hypothesis('CPU 未升高但请求出现等待')


@pytest.mark.parametrize('runtime', ['PYTHON','GO','CPP'])
def test_shared_cpu_quota_claim_does_not_turn_into_a_profile_high_cpu_contract(runtime):
    plan=performance_observation_plan('NOISY_NEIGHBOR')
    compiled=compile_cpu_observation_contract(plan['statement'],plan['expected'],plan['falsification'],runtime=runtime)
    assert compiled['status']=='NOT_APPLICABLE'


@pytest.mark.parametrize('statement', [
    '进程存在少数 Python 函数集中占用 CPU，形成明显热点',
    'CPU 时间集中在少数 Python 函数，形成明显热点栈',
])
def test_unregistered_cpu_concentration_is_rejected_without_affecting_quota_domain(statement):
    compiled=compile_cpu_observation_contract(statement,['前几个函数累计占比显著（如 >50%）'],['样本均匀'])
    assert compiled['status']=='UNSUPPORTED'


@pytest.mark.parametrize('frame,expected', [
    ('calculate:/build/demo/main.cpp:131', {'name':'calculate','file':'/build/demo/main.cpp','line':131}),
    ('business::calculate(int):main.cc:42', {'name':'business::calculate(int)','file':'main.cc','line':42}),
    ('plain', {'name':'plain'}), ('unresolved:??:0', {'name':'unresolved:??:0'}),
])
def test_perf_keeps_actual_source_lines_and_cxx_symbol_colons(frame, expected):
    actual=hotmethod_analyzer._perf_source_frame(frame)
    assert {k:actual[k] for k in expected}==expected
    assert ('file' in actual)==('file' in expected)


def test_perf_commands_request_dwarf_source_lines_without_event_period(tmp_path, monkeypatch):
    commands=[]
    monkeypatch.setattr(hotmethod_analyzer.shutil, 'which', lambda _: '/usr/bin/perf')
    monkeypatch.setattr(hotmethod_analyzer.subprocess, 'run', lambda cmd, **kwargs: commands.append(cmd))
    source=tmp_path/'perf.data';source.write_bytes(b'unit-input')
    assert hotmethod_analyzer._perf_script(source,tmp_path/'script.txt')==(True,'')
    assert hotmethod_analyzer._stackcollapse(tmp_path/'script.txt',tmp_path/'folded.txt')==(True,'')
    fields=commands[0][commands[0].index('-F')+1].split(',')
    assert 'srcline' in fields and 'period' not in fields
    assert '--srcline' in commands[1]


@pytest.mark.parametrize('fault', ['missing_source','short_profile','runtime_wrapper','incorrect_share'])
def test_cpp_source_predicate_does_not_invent_a_mapping_or_sampling_mass(fault):
    plan=cpu_observation_plan('CPP')
    metadata={'schema_version':'perf_analysis.v1','sample_count':100,
        'top_functions':[{'name':'applicationWork','file':'/src/main.cpp','line':10,'samples':80,'percent':80}]}
    assert _compute_hypothesis_predicate(hypothesis(plan),metadata)['outcome']=='SUPPORT'
    if fault=='missing_source':metadata['top_functions'][0].pop('file')
    elif fault=='short_profile':metadata['sample_count']=8
    elif fault=='runtime_wrapper':metadata['top_functions'][0]['name']='std::thread::_State_impl'
    else:metadata['top_functions'][0]['samples']=1
    assert _compute_hypothesis_predicate(hypothesis(plan),metadata)['outcome']=='NEUTRAL'


def application_metadata(signal='io_latency', average=30):
    io=signal=='io_latency'
    count_key='io_operations' if io else 'lock_acquisitions'
    duration_key='io_operation_duration_ms_total' if io else 'lock_wait_ms'
    value_key='average_latency_ms' if io else 'average_wait_ms'
    count_metric='operation_count_delta' if io else 'lock_acquisitions_delta'
    before={count_key:20,duration_key:100}
    after={count_key:30,duration_key:100+10*average}
    delta={count_key:10,duration_key:10*average}
    metrics={value_key:average,count_metric:10}
    if not io:
        before['lock_contentions']=10;after['lock_contentions']=18;delta['lock_contentions']=8
        metrics.update(lock_contentions_delta=8,lock_wait_ms_delta=10*average)
    return {'schema_version':'sys_metrics_analysis.v2','sample_count':10,'window_duration_seconds':10,
        'process_identity':{'verified':True,'pid':321,'start_ticks':42},
        'application_metrics':{'schema_version':'application_metrics_analysis.v1','sample_count':10,
            'identity':{'identity_verified':True,'host_pid':321},'before':before,'after':after,'delta':delta},
        'signals':{signal:{'measurement_scope':'TARGET_APPLICATION_SYNC_IO','metrics':metrics}}}


@pytest.mark.parametrize('signal,category', [('io_latency','IO_LATENCY'),('lock_contention','LOCK_CONTENTION')])
def test_new_successful_operation_counters_can_support_or_refute_declared_delay(signal,category):
    plan=performance_observation_plan(category)
    for average,direction in ((30,'SUPPORT'),(.01,'COUNTER')):
        metadata=application_metadata(signal,average)
        assert validate_signal_window(metadata,signal)
        assert _structured_signal_predicate(hypothesis(plan),metadata)['outcome']==direction


@pytest.mark.parametrize('signal', ['io_latency','lock_contention'])
@pytest.mark.parametrize('fault', ['missing_duration','forged_average','wrong_identity','missing_delta',
    'no_new_operations','counter_reset','partial_samples','short_window','bool_value','malformed_window'])
def test_numeric_labels_cannot_replace_measured_operation_windows(signal,fault):
    metadata=application_metadata(signal)
    app=metadata['application_metrics'];metrics=metadata['signals'][signal]['metrics']
    count='io_operations' if signal=='io_latency' else 'lock_acquisitions'
    duration='io_operation_duration_ms_total' if signal=='io_latency' else 'lock_wait_ms'
    value='average_latency_ms' if signal=='io_latency' else 'average_wait_ms'
    if fault=='missing_duration':app['before'].pop(duration)
    elif fault=='forged_average':metrics[value]=500
    elif fault=='wrong_identity':app['identity']['host_pid']=999
    elif fault=='missing_delta':app['delta'].pop(duration)
    elif fault=='no_new_operations':app['after'][count]=app['before'][count]
    elif fault=='counter_reset':app['after'][duration]=0
    elif fault=='partial_samples':app['sample_count']=5
    elif fault=='short_window':metadata['window_duration_seconds']=.1
    elif fault=='bool_value':metrics[value]=True
    else:app['after']=[]
    assert validate_signal_window(metadata,signal) is False
    plan=performance_observation_plan('IO_LATENCY' if signal=='io_latency' else 'LOCK_CONTENTION')
    assert _structured_signal_predicate(hypothesis(plan),metadata) is None


def competition_metadata(throttled=True):
    samples=[]
    for i in range(6):
        row={'schema_version':'mini-drop.cgroup-cpu-observation.v1','source':'linux_proc_and_cgroup_v2',
            'target_pid':321,'target_start_ticks':42,'target_cpu_ticks':100+45*i,
            'boot_id':'unit-boot','cgroup_path':'/unit/quota','cpu_quota_us':65000,'cpu_period_us':100000,
            'nr_periods':10+10*i,'nr_throttled':2+(i if throttled else 0),
            'throttled_usec':1000+(200000*i if throttled else 0),
            'peers':[{'pid':999,'start_ticks':53,'cpu_ticks':100+20*i,'cgroup_path':'/unit/quota'}]}
        samples.append({'offset_sec':i,'captured_at_unix_ms':1000+1000*i,'process_start_ticks':42,
            'process_cpu_ticks':100+45*i,'resource_competition_json':json.dumps(row)})
    return _parse_sys_metrics_document({'schema_version':'sys_metrics.v2','pid':321,
        'clock_ticks_per_second':100,'samples':samples})


@pytest.mark.parametrize('throttled,direction', [(True,'SUPPORT'),(False,'COUNTER')])
def test_actual_shared_quota_window_distinguishes_throttling_from_valid_zero(throttled,direction):
    metadata=competition_metadata(throttled)
    assert validate_signal_window(metadata,'noisy_neighbor') is True
    assert _structured_signal_predicate(hypothesis(performance_observation_plan('NOISY_NEIGHBOR')),metadata)['outcome']==direction


@pytest.mark.parametrize('fault', ['peer_active_only','wrong_cgroup','changed_peer','changed_quota',
    'forged_delta','short_window','counter_reset','no_target_work','no_peer_work','malformed_window'])
def test_neighbor_activity_cannot_be_promoted_to_resource_competition(fault):
    metadata=competition_metadata();window=metadata['resource_competition_window']
    if fault=='peer_active_only':metadata.pop('resource_competition_window')
    elif fault=='wrong_cgroup':window['after']['peers'][0]['cgroup_path']='/independent'
    elif fault=='changed_peer':window['after']['peers'][0]['start_ticks']+=1
    elif fault=='changed_quota':window['after']['cpu_quota_us']+=1
    elif fault=='forged_delta':window['throttled_usec_delta']+=1
    elif fault=='short_window':window['end_unix_ms']=window['start_unix_ms']+100
    elif fault=='counter_reset':window['after']['nr_throttled']=0
    elif fault=='no_target_work':window['after']['target_cpu_ticks']=window['before']['target_cpu_ticks']
    elif fault=='no_peer_work':window['after']['peers'][0]['cpu_ticks']=window['before']['peers'][0]['cpu_ticks']
    else:window['after']=[]
    assert validate_signal_window(metadata,'noisy_neighbor') is False
    assert _structured_signal_predicate(hypothesis(performance_observation_plan('NOISY_NEIGHBOR')),metadata) is None


def test_refutation_requires_a_complete_window_not_just_one_low_counter():
    from server.app.drop_insight.observation_verifier import verify_performance_observation
    from tests.test_performance_observation_verification import measured
    plan=performance_observation_plan('IO_LATENCY')
    e=measured(plan,.01)
    result=verify_performance_observation([('COUNTER',e)],plan['expected'],plan['falsification'])
    assert result['status']=='INSUFFICIENT_OBSERVABILITY'
    assert result['checked_ratio']==0
