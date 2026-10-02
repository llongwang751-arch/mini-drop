"""Measured service checks, independent of hypothesis support and causal proof."""
from __future__ import annotations

import math
import re


def _number(value, *, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return value if not nonnegative or value >= 0 else None


def assess_health_window(evidence: dict, target: dict) -> dict:
    """Assess a single admitted Analyzer window; missing data never becomes zero."""
    envelope = evidence.get('envelope') or {}
    metadata = (envelope.get('observation') or {}).get('metadata') or {}
    quality = envelope.get('quality') or {}
    source = envelope.get('source') or {}
    scope = envelope.get('scope') or {}
    identity = metadata.get('process_identity') or {}
    binding = target.get('process_binding') or {}
    pid = target.get('pid', binding.get('pid'))
    samples = metadata.get('sample_count', quality.get('sample_count'))
    duration = _number(metadata.get('window_duration_seconds'), nonnegative=True)
    trusted = (
        (evidence.get('classification') or {}).get('decision') in
            {'ACCEPT_SUPPORT', 'ACCEPT_LIMITED', 'ACCEPT_NEUTRAL', 'ACCEPT_COUNTER'}
        and all(quality.get(k) is True for k in
                ('schema_valid', 'analyzer_validated', 'target_match', 'time_overlap'))
        and quality.get('degraded') is False and quality.get('truncated') is not True
        and source.get('tool_name') in {'sys_metrics', 'collect_sys_metrics'}
        and all(source.get(k) for k in ('task_id', 'task_attempt_id', 'artifact_id', 'analysis_job_id'))
        and re.fullmatch(r'[a-f0-9]{64}', str(source.get('artifact_sha256') or '')) is not None
        and bool(target.get('agent_id')) and scope.get('agent_id') == target['agent_id']
        and type(pid) is int and pid > 0 and scope.get('pid') == pid
        and identity.get('verified') is True and identity.get('pid') == pid
        and bool(binding.get('boot_id')) and type(binding.get('process_start_ticks')) is int
        and binding['process_start_ticks'] > 0
        and identity.get('start_ticks') == binding['process_start_ticks']
        and type(samples) is int and samples >= 5 and duration is not None and duration >= 1
    )
    result = {'schema': 'mini-drop.health-check.v1', 'code': 'INSUFFICIENT_OBSERVABILITY',
        'title': '无法判断，请补充采集数据', 'checked': [], 'anomalies': [],
        'evidence_refs': [], 'causal_root_cause_verified': False,
        'unmeasured': ['磁盘操作延迟', '丢包/重传', '内存限额', '全部业务接口和业务正确性']}
    if not trusted:
        result['detail'] = '缺少可信的目标身份、Analyzer 来源或完整采样窗口；本次不能判断正常或异常。'
        return result
    result['evidence_refs'] = [evidence['evidence_id']]
    result['window_duration_seconds'] = duration
    result['sample_count'] = samples
    summary = metadata.get('summary') or {}
    cpu = _number(summary.get('process_cpu_core_usage', summary.get('process_cpu_percent')), nonnegative=True)
    rss = _number(summary.get('vmrss_mb'), nonnegative=True)
    delta = _number(summary.get('vmrss_mb_delta'))
    if cpu is not None:
        result['checked'].append('进程 CPU < 50%（单核口径）')
        if cpu >= 50:
            result['anomalies'].append(f'进程 CPU {cpu:.1f}%')
    if rss is not None and delta is not None:
        result['checked'].append('窗口 RSS 增量 < 8 MiB')
        if delta >= 8:
            result['anomalies'].append(f'窗口 RSS 增量 {delta:.1f} MiB')
    signals = metadata.get('signals') or {}
    for key, label in {
        'network_latency': 'HTTP 调用路径耗时升高', 'downstream_latency': '下游调用耗时升高',
        'io_latency': 'I/O 延迟升高', 'lock_contention': '锁等待', 'queue_backlog': '队列积压',
        'load_saturation': '入口负载超过完成能力', 'jvm_gc': '分配与 GC 活动',
        'http_service_degradation': 'HTTP 耗时或失败率超出观测阈值',
    }.items():
        if isinstance(signals.get(key), dict) and signals[key].get('detected') is True:
            result['anomalies'].append(label)
    app = metadata.get('application_metrics') or {}
    d = app.get('delta') or {}
    count = _number(d.get('http_requests'), nonnegative=True)
    failures = _number(d.get('http_failures'), nonnegative=True)
    elapsed = _number(d.get('http_duration_ms'), nonnegative=True)
    p95 = _number((app.get('max') or {}).get('http_recent_p95_latency_ms'), nonnegative=True)
    if ((app.get('identity') or {}).get('identity_verified') is True and count is not None and count > 0
            and failures is not None and failures <= count and elapsed is not None and p95 is not None):
        result['checked'].append('HTTP 均值 < 500 ms、近期 P95 < 1000 ms、失败率 < 5%')
        if elapsed/count >= 500 or p95 >= 1000 or failures/count >= .05:
            result['anomalies'].append('HTTP 耗时或失败率超出观测阈值')
    if result['anomalies']:
        result.update(code='ANOMALY_OBSERVED', title='已发现性能异常，原因待确认',
                      detail='已观测：'+'；'.join(result['anomalies'])+'。可继续调查具体原因。')
    elif cpu is None or rss is None or delta is None:
        result['detail'] = '缺少有效 CPU 或 RSS 趋势；采集完成不代表检查正常。'
    else:
        result.update(code='NORMAL_OBSERVED', title='本次检查正常（已检查范围）',
                      detail='本窗口内已检查指标未超出观测阈值，无需继续寻找这些指标的异常根因。')
    return result
