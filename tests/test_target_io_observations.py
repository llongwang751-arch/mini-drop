import json

import pytest

from server.app.metric_analyzers import _application_counter_average, _derive_signals, _parse_sys_metrics_document
from server.app.drop_insight.performance_criteria import evaluate_performance_criterion


def test_target_io_elapsed_time_is_not_inferred_from_writing_bytes():
    app = {'before': {'io_operations': 100, 'io_operation_duration_ms_total': 10000},
           'after': {'io_operations': 110, 'io_operation_duration_ms_total': 10300},
           'delta': {'io_operations': 10, 'io_bytes_written': 100000}}
    signal = _derive_signals({}, app)['io_latency']
    assert signal['metrics']['average_latency_ms'] == 30
    assert signal['measurement_scope'] == 'TARGET_APPLICATION_SYNC_IO'
    app['after']['io_operation_duration_ms_total'] = 10010
    assert _derive_signals({}, app)['io_latency']['detected'] is False
    del app['before']['io_operation_duration_ms_total']
    assert 'io_latency' not in _derive_signals({}, app)


@pytest.mark.parametrize('count,duration', [(100, 10300), (99, 10300), (110, 9999), (110.5, 10300), (True, 10300), (110, float('nan'))])
def test_reset_missing_or_invalid_io_window_stays_unknown(count, duration):
    app = {'before': {'io_operations': 100, 'io_operation_duration_ms_total': 10000},
           'after': {'io_operations': count, 'io_operation_duration_ms_total': duration}}
    assert _application_counter_average(app, 'io_operations', 'io_operation_duration_ms_total') is None


def test_real_analyzer_binds_io_measurements_to_process_identity_and_rejects_oracle_fields():
    def sample(i):
        return {'offset_sec': i, 'captured_at_unix_ms': 1000 + i*1000, 'process_start_ticks': 42,
                'process_cpu_ticks': 100, 'application_metrics_json': json.dumps({
                    'schema_version': 'mini-drop.application-metrics.v1', 'host_pid': 123,
                    'io_operations': 10*i, 'io_operation_duration_ms_total': 300*i,
                    'io_fault_active': True, 'scenario_id': 'secret-oracle'})}
    document = {'schema_version': 'sys_metrics.v2', 'pid': 123, 'clock_ticks_per_second': 100,
                'samples': [sample(i) for i in range(5)]}
    metadata = _parse_sys_metrics_document(document)
    assert metadata['signals']['io_latency']['metrics']['average_latency_ms'] == 30
    assert 'secret-oracle' not in json.dumps(metadata)
    for row in document['samples']:
        payload = json.loads(row['application_metrics_json']);payload['host_pid'] = 999
        row['application_metrics_json'] = json.dumps(payload)
    with pytest.raises(ValueError, match='target identity mismatch'):
        _parse_sys_metrics_document(document)


def test_measured_memory_decrease_can_refute_growth_without_fabricating_zero():
    signals = _derive_signals({'vmrss_mb_delta': -2}, None)
    result = evaluate_performance_criterion('memory_growth.rss_delta_mb < 8', signals)
    assert result['value'] == -2 and result['matches'] is True
