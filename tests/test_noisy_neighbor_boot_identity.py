"""Linux text boot IDs retain raw newlines without changing the process identity."""

from copy import deepcopy

import pytest

from server.app.drop_insight.hypothesis_predicate import _compute_hypothesis_predicate
from server.app.drop_insight.performance_criteria import performance_observation_plan
from server.app.drop_insight.signal_window_validation import validate_signal_window
from tests.test_seven_gap_diagnosis_windows import competition_metadata, hypothesis


def _native_text_boot_metadata(throttled=True):
    metadata = competition_metadata(throttled)
    window = metadata['resource_competition_window']
    boot = 'a641bc20-0b78-44fb-8965-0e709dfd487a'
    window['boot_id'] = boot
    for endpoint in ('before', 'after'):
        window[endpoint]['boot_id'] = boot + '\n'
    return metadata


@pytest.mark.parametrize('throttled,outcome,indexes', [
    (True, 'SUPPORT', [0, 1, 2, 3]), (False, 'COUNTER', [0]),
])
def test_native_boot_file_newline_preserves_a_complete_numeric_predicate(throttled, outcome, indexes):
    metadata = _native_text_boot_metadata(throttled)
    before = deepcopy(metadata)
    assert validate_signal_window(metadata, 'noisy_neighbor') is True
    predicate = _compute_hypothesis_predicate(hypothesis(performance_observation_plan('NOISY_NEIGHBOR')), metadata)
    assert predicate['outcome'] == outcome
    assert predicate['criterion_indexes'] == indexes
    assert predicate['claim_scope'] == 'BOUNDED_OBSERVATION'
    assert metadata == before


@pytest.mark.parametrize('location', ['before', 'after', 'summary'])
def test_boot_change_still_rejects_the_resource_window(location):
    metadata = _native_text_boot_metadata()
    window = metadata['resource_competition_window']
    row = window if location == 'summary' else window[location]
    row['boot_id'] = 'a641bc20-0b78-44fb-8965-0e709dfd487b\n'
    assert validate_signal_window(metadata, 'noisy_neighbor') is False
    assert _compute_hypothesis_predicate(hypothesis(performance_observation_plan('NOISY_NEIGHBOR')), metadata) is None


@pytest.mark.parametrize('invalid', [None, '', '\n', 123, True])
def test_missing_or_non_text_boot_identity_remains_unknown(invalid):
    metadata = _native_text_boot_metadata()
    metadata['resource_competition_window']['boot_id'] = invalid
    assert validate_signal_window(metadata, 'noisy_neighbor') is False


@pytest.mark.parametrize('fault', ['peer_identity', 'quota', 'fake_delta', 'zero_target', 'zero_peer'])
def test_boot_normalization_does_not_bypass_resource_or_counter_checks(fault):
    metadata = _native_text_boot_metadata()
    window = metadata['resource_competition_window']
    if fault == 'peer_identity':
        window['after']['peers'][0]['start_ticks'] += 1
    elif fault == 'quota':
        window['after']['cpu_quota_us'] += 1
    elif fault == 'fake_delta':
        window['throttled_usec_delta'] += 1
    elif fault == 'zero_target':
        window['after']['target_cpu_ticks'] = window['before']['target_cpu_ticks']
    else:
        window['after']['peers'][0]['cpu_ticks'] = window['before']['peers'][0]['cpu_ticks']
    assert validate_signal_window(metadata, 'noisy_neighbor') is False
