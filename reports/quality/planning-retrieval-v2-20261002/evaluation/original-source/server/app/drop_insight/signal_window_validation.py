"""Check measured window arithmetic before numeric slots earn coverage.

These checks establish bounded observations. They do not establish that an
observed writer, mutex, or peer caused a business request to be slow.
"""
from __future__ import annotations

import math


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _integer(value, *, positive=False):
    return _number(value) and int(value) == value and (value > 0 if positive else value >= 0)


def _identity(metadata):
    identity = metadata.get('process_identity') or {}
    return (metadata.get('schema_version') == 'sys_metrics_analysis.v2'
        and identity.get('verified') is True
        and _integer(identity.get('pid'), positive=True)
        and _integer(identity.get('start_ticks'), positive=True))


def _application_counter(metadata, signal, count_key, duration_key, value_key, delta_key):
    observation = (metadata.get('signals') or {}).get(signal) or {}
    metrics = observation.get('metrics') or {}
    application = metadata.get('application_metrics') or {}
    identity = application.get('identity') or {}
    target = metadata.get('process_identity') or {}
    if (not _identity(metadata) or application.get('schema_version') != 'application_metrics_analysis.v1'
        or identity.get('identity_verified') is not True or identity.get('host_pid') != target['pid']
        or not _integer(application.get('sample_count'), positive=True)
        or application['sample_count'] < 5 or application['sample_count'] != metadata.get('sample_count')
        or not _number(metadata.get('window_duration_seconds')) or metadata['window_duration_seconds'] < 1):
        return False
    before, after, delta = (application.get(k) or {} for k in ('before', 'after', 'delta'))
    if any(not isinstance(row, dict) for row in (before, after, delta)):
        return False
    if not all(_integer(row.get(count_key)) and _number(row.get(duration_key)) for row in (before, after)):
        return False
    count = after[count_key] - before[count_key]
    duration = after[duration_key] - before[duration_key]
    if count < 5 or duration < 0:
        return False
    if (not _number(delta.get(count_key)) or delta[count_key] != count
        or not _number(delta.get(duration_key)) or abs(delta[duration_key] - duration) > .0011
        or not _number(metrics.get(delta_key)) or metrics[delta_key] != count
        or not _number(metrics.get(value_key)) or abs(metrics[value_key] - duration / count) > .00051):
        return False
    if signal == 'io_latency':
        return observation.get('measurement_scope') == 'TARGET_APPLICATION_SYNC_IO'
    if signal == 'lock_contention':
        key = 'lock_contentions'
        if not all(_integer(row.get(key)) for row in (before, after)):
            return False
        contentions = after[key] - before[key]
        return (0 <= contentions <= count and delta.get(key) == contentions
            and metrics.get('lock_contentions_delta') == contentions
            and _number(metrics.get('lock_wait_ms_delta'))
            and abs(metrics['lock_wait_ms_delta'] - duration) <= .0011)
    return False


def _shared_cpu(metadata):
    window = metadata.get('resource_competition_window') or {}
    observation = (metadata.get('signals') or {}).get('noisy_neighbor') or {}
    metrics = observation.get('metrics') or {}
    target = metadata.get('process_identity') or {}
    if (not _identity(metadata) or window.get('schema_version') != 'shared-cgroup-cpu-window.v1'
        or window.get('source') != 'linux_proc_and_cgroup_v2'
        or window.get('same_cgroup_verified') is not True or observation.get('same_cgroup_verified') is not True
        or window.get('measurement_scope') != 'TARGET_SHARED_CGROUP_CPU'
        or observation.get('measurement_scope') != 'TARGET_SHARED_CGROUP_CPU'
        or window.get('target_pid') != target['pid'] or window.get('target_start_ticks') != target['start_ticks']
        or not _integer(window.get('sample_count'), positive=True) or window['sample_count'] < 5
        or not _integer(window.get('start_unix_ms'), positive=True)
        or not _integer(window.get('end_unix_ms'), positive=True)
        or window['end_unix_ms'] - window['start_unix_ms'] < 1000):
        return False
    endpoints = [window.get('before') or {}, window.get('after') or {}]
    identities = []
    peer_ticks = []
    for row in endpoints:
        if (row.get('schema_version') != 'mini-drop.cgroup-cpu-observation.v1'
            or row.get('source') != 'linux_proc_and_cgroup_v2'
            or row.get('target_pid') != target['pid'] or row.get('target_start_ticks') != target['start_ticks']
            or not isinstance(row.get('boot_id'), str) or not row['boot_id'].strip()
            or not isinstance(row.get('cgroup_path'), str) or not row['cgroup_path'].strip()
            or not all(_integer(row.get(k), positive=True) for k in ('cpu_quota_us', 'cpu_period_us'))
            or not all(_integer(row.get(k)) for k in ('target_cpu_ticks', 'nr_periods', 'nr_throttled', 'throttled_usec'))
            or row['nr_throttled'] > row['nr_periods']):
            return False
        peers = row.get('peers')
        if not isinstance(peers, list) or not peers:
            return False
        peers_by_identity = {}
        for peer in peers:
            if (not isinstance(peer, dict) or not _integer(peer.get('pid'), positive=True)
                or not _integer(peer.get('start_ticks'), positive=True) or not _integer(peer.get('cpu_ticks'))
                or peer['pid'] == target['pid'] or peer.get('cgroup_path') != row['cgroup_path']):
                return False
            identity = (peer['pid'], peer['start_ticks'])
            if identity in peers_by_identity:
                return False
            peers_by_identity[identity] = peer['cpu_ticks']
        identities.append(set(peers_by_identity))
        peer_ticks.append(peers_by_identity)
    before, after = endpoints
    # /proc/sys/kernel/random/boot_id is a text file ending in a newline.
    # The analyzer trims its summary while retaining both raw endpoint rows.
    # Compare the same text identity, preserving the recorded endpoint bytes.
    stable = ('cgroup_path', 'cpu_quota_us', 'cpu_period_us')
    boot_id = before['boot_id'].strip()
    if (not isinstance(window.get('boot_id'), str)
        or window['boot_id'].strip() != boot_id or after['boot_id'].strip() != boot_id
        or any(before[k] != after[k] or window.get(k) != before[k] for k in stable)
        or identities[0] != identities[1]
        or set((p.get('pid'), p.get('start_ticks')) for p in window.get('peer_identities', [])) != identities[0]):
        return False
    if any(peer_ticks[1][key] < peer_ticks[0][key] for key in identities[0]):
        return False
    values = {out: after[key] - before[key] for out, key in (
        ('target_cpu_ticks_delta', 'target_cpu_ticks'), ('throttled_periods_delta', 'nr_throttled'),
        ('throttled_usec_delta', 'throttled_usec'))}
    values['peer_cpu_ticks_delta'] = sum(peer_ticks[1].values()) - sum(peer_ticks[0].values())
    periods = after['nr_periods'] - before['nr_periods']
    if (periods <= 0 or values['target_cpu_ticks_delta'] <= 0 or values['peer_cpu_ticks_delta'] <= 0
        or values['throttled_periods_delta'] < 0 or values['throttled_periods_delta'] > periods
        or values['throttled_usec_delta'] < 0):
        return False
    values.update(cpu_quota_us=before['cpu_quota_us'], cpu_period_us=before['cpu_period_us'])
    return all(_number(window.get(k)) and _number(metrics.get(k))
        and window[k] == value and metrics[k] == value for k, value in values.items())


def validate_signal_window(metadata, signal):
    """Missing identity, counters, scope, or elapsed time remain unknown."""
    try:
        if signal == 'io_latency':
            return _application_counter(metadata, signal, 'io_operations', 'io_operation_duration_ms_total',
                                        'average_latency_ms', 'operation_count_delta')
        if signal == 'lock_contention':
            return _application_counter(metadata, signal, 'lock_acquisitions', 'lock_wait_ms',
                                        'average_wait_ms', 'lock_acquisitions_delta')
        if signal == 'noisy_neighbor':
            return _shared_cpu(metadata)
        return True
    except (AttributeError, KeyError, TypeError, ValueError, OverflowError):
        return False
