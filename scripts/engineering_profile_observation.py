"""Bounded runtime-profile observations; this module never proves causality."""
import math
from server.app.drop_insight.cpu_criteria import compile_cpu_observation_contract


def finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def _python_profile(evidence, rule, records, admitted, domain):
    if domain != 'cpu_hotspot' or rule.get('observation_contract') != 'python-profile-and-os-cpu.v1':
        return False
    envelope = evidence.get('envelope') or {}
    source = envelope.get('source') or {}
    metadata = (envelope.get('observation') or {}).get('metadata') or {}
    metrics = (metadata.get('hypothesis_predicate') or {}).get('metrics') or {}
    if source.get('tool_name') != 'pyspy' or metrics.get('observation_contract') != rule['observation_contract']:
        return False
    count = metadata.get('sample_count')
    functions = metrics.get('concentrated_functions') or []
    if type(count) is not int or count < 50 or not 1 <= len(functions) <= 3:
        return False
    if len({f.get('name') for f in functions}) != len(functions):
        return False
    total = 0
    raw_samples = 0
    for function in functions:
        locations = function.get('locations') or []
        if not locations or len({(r.get('file'), r.get('line')) for r in locations}) != len(locations):
            return False
        share = 0
        for location in locations:
            if not isinstance(location.get('file'), str) or type(location.get('line')) is not int or location['line'] <= 0:
                return False
            rows = [r for r in metadata.get('top_functions', []) if
                all(r.get(k) == v for k, v in {'name':function.get('name'), 'file':location['file'], 'line':location['line']}.items())]
            if len(rows) != 1:
                return False
            row = rows[0]
            if type(row.get('samples')) is not int or not 0 < row['samples'] <= count or not finite(row.get('percent')):
                return False
            # Collector output retains one decimal and may truncate its share.
            if abs(row['samples'] / count * 100 - row['percent']) > .11 or row['percent'] != location.get('percent'):
                return False
            share += row['percent']
            raw_samples += row['samples']
        if not finite(function.get('percent')) or abs(share - function['percent']) > .001:
            return False
        total += share
    if not finite(metrics.get('concentrated_percent')) or abs(total - metrics['concentrated_percent']) > .001 or not 70 <= total <= 100 or not 70 <= raw_samples / count * 100 <= 100:
        return False
    hypothesis = next((h for h in records.get('hypotheses', []) if h.get('hypothesis_id') == evidence.get('hypothesis_id')), {})
    compiled = compile_cpu_observation_contract(hypothesis.get('statement', ''),
        hypothesis.get('expected_observations') or [], hypothesis.get('falsification_criteria') or [])
    if not compiled.get('executable'):
        return False
    for sibling in records.get('evidence', []):
        other = sibling.get('envelope') or {}
        if sibling.get('evidence_id') not in admitted or sibling.get('hypothesis_id') != evidence.get('hypothesis_id'):
            continue
        if (other.get('source') or {}).get('tool_name') != 'sys_metrics':
            continue
        measured = ((other.get('observation') or {}).get('metadata') or {}).get('hypothesis_predicate', {}).get('metrics') or {}
        keys = ('clock_ticks_per_second', 'start_cpu_ticks', 'end_cpu_ticks', 'start_unix_ms', 'end_unix_ms', 'process_cpu_core_usage')
        if measured.get('source') != 'linux_proc_stat' or not all(finite(measured.get(k)) for k in keys):
            continue
        duration = measured['end_unix_ms'] - measured['start_unix_ms']
        ticks = measured['end_cpu_ticks'] - measured['start_cpu_ticks']
        if duration <= 0 or ticks < 0 or measured['clock_ticks_per_second'] <= 0:
            continue
        actual = ticks / measured['clock_ticks_per_second'] / (duration / 1000) * 100
        if abs(actual - measured['process_cpu_core_usage']) < .000001 and actual >= compiled['cpu_threshold']:
            return True
    return False


def _gc_window(metadata):
    if metadata.get('schema_version')!='jvm_gc_metrics.v1':return False
    duration=metadata.get('window_duration_ms');before=metadata.get('before') or {};after=metadata.get('after') or {}
    delta=metadata.get('delta') or {}
    if not finite(duration) or duration<=0:return False
    for key in ('allocated_bytes','gc_count','gc_time_ms'):
        if not all(finite(values.get(key)) and values[key]>=0 for values in (before,after,delta)):
            return False
        if after[key]-before[key]!=delta[key]:return False
    return delta['allocated_bytes']>0 and (delta['gc_count']>0 or delta['gc_time_ms']>0)


def measured_runtime_profile(evidence,rule,records,admitted,domain):
    """Recompute a profile percentage and require the registered collector/event.

    Allocation profiles alone cannot establish GC activity. Their independent
    counter artifact must belong to the same task, be admitted, and match the
    embedded window exactly. Lock profiling describes measured waiting samples,
    without inventing lock-holder identity, source location or causal proof.
    """
    if rule.get('collector') == 'pyspy':
        return _python_profile(evidence, rule, records, admitted, domain)
    envelope=evidence.get('envelope') or {};source=envelope.get('source') or {}
    metadata=(envelope.get('observation') or {}).get('metadata') or {}
    metrics=(metadata.get('hypothesis_predicate') or {}).get('metrics') or {}
    event=rule.get('profile_event')
    registered={('java_async','alloc'):'jvm_gc',('java_async','lock'):'lock_contention'}
    if registered.get((rule.get('collector'),event))!=domain:return False
    if domain=='jvm_gc' and rule.get('require_gc_counters') is not True:return False
    if source.get('tool_name')!=rule.get('collector') or metadata.get('profile_event')!=event or metrics.get('profile_event')!=event:
        return False
    count=metadata.get('sample_count');percent=metrics.get('dominant_percent')
    if type(count) is not int or count<rule.get('minimum_samples',50):return False
    if not finite(percent) or not rule.get('minimum_percent',20)<=percent<=100:return False
    matching=[r for r in metadata.get('top_functions',[]) if r.get('name')==metrics.get('dominant_function')]
    if len(matching)!=1:return False
    name=metrics.get('dominant_function')
    if not isinstance(name,str) or name.startswith(('java/','jdk/','sun/','java.','jdk.','sun.','[')) or name in {'byte[]','char[]'}:
        return False
    row=matching[0];samples=row.get('samples')
    if type(samples) is not int or not 0<samples<=count or not finite(row.get('percent')):return False
    if abs(samples/count*100-row['percent'])>.02 or abs(row['percent']-percent)>.001:return False
    if rule.get('require_gc_counters') is True:
        embedded=metadata.get('jvm_gc_counters') or {}
        if not _gc_window(embedded):return False
        siblings=[e for e in records.get('evidence',[]) if e.get('evidence_id') in admitted
            and e.get('evidence_id')!=evidence.get('evidence_id')
            and e.get('envelope',{}).get('source',{}).get('task_id')==source.get('task_id')
            and e.get('envelope',{}).get('source',{}).get('artifact_id')!=source.get('artifact_id')]
        keys=('schema_version','before','after','delta','window_duration_ms','process_identity')
        if not any(_gc_window(m) and all(m.get(k)==embedded.get(k) for k in keys)
            for m in [e.get('envelope',{}).get('observation',{}).get('metadata',{}) for e in siblings]):return False
    return True
