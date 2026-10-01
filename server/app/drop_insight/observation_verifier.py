"""Check a declared numeric window without inventing independent controls."""
from __future__ import annotations

from .evidence import classify_evidence, observed_count
from .performance_criteria import evaluate_performance_criterion, parse_performance_criterion


def verify_performance_observation(evidence, expected, falsification):
    slots = [(kind, index, text) for kind, entries in [('expected', expected), ('falsification', falsification)]
             for index, text in enumerate(entries)]
    result = {'schema_version': 'performance-observation-verification.v1', 'status': 'UNSUPPORTED_PLAN',
              'checked_ratio': 0.0, 'criteria': [], 'evidence_refs': [],
              'independent_control_verified': False, 'claim_scope': 'BOUNDED_OBSERVATION',
              'causal_root_cause_verified': False}
    if not expected or not slots or any(parse_performance_criterion(text) is None for _, _, text in slots):
        return result
    windows = []
    for role, envelope in evidence:
        if role not in {'SUPPORT', 'COUNTER', 'CONTROL', 'NEUTRAL'} or not classify_evidence(envelope)['can_support_conclusion']:
            continue
        metadata = envelope.observation.get('metadata') or {}
        if not isinstance(metadata, dict):
            continue
        identity = metadata.get('process_identity') or {}
        if (not isinstance(identity, dict) or metadata.get('schema_version') != 'sys_metrics_analysis.v2'
            or identity.get('verified') is not True or identity.get('pid') != envelope.scope.pid
            or not observed_count(identity.get('start_ticks'))):
            continue
        signals = metadata.get('signals') or {}
        if not isinstance(signals, dict):
            continue
        criteria = []
        for kind, index, text in slots:
            value = evaluate_performance_criterion(text, signals)
            criteria.append({'kind': kind, 'index': index, 'criterion': text,
                             'checked': value is not None, 'matches': value['matches'] if value else None,
                             'measurement': value, 'evidence_id': envelope.evidence_id})
        complete = all(row['checked'] for row in criteria)
        supports = complete and all(row['matches'] for row in criteria if row['kind'] == 'expected')
        refutes = any(row['matches'] is True for row in criteria if row['kind'] == 'falsification')
        windows.append({'criteria': criteria, 'supports': supports and not refutes,
                        'refutes': refutes, 'evidence_id': envelope.evidence_id})
    supporting = [row for row in windows if row['supports']]
    refuting = [row for row in windows if row['refutes']]
    # Evaluate a whole contract within one immutable artifact. Never splice
    # two partly measured windows together to manufacture complete coverage.
    strongest = max(windows, key=lambda row: sum(c['checked'] for c in row['criteria']), default=None)
    if supporting:
        strongest = supporting[0]
    if strongest:
        result['criteria'] = strongest['criteria']
        result['checked_ratio'] = sum(row['checked'] for row in strongest['criteria']) / len(slots)
        result['evidence_refs'] = [strongest['evidence_id']]
    result['status'] = ('CONFLICTING_OBSERVATIONS' if supporting and refuting else
                        'VERIFIED' if supporting else 'REFUTED' if refuting else 'INSUFFICIENT_OBSERVABILITY')
    if supporting and refuting:
        result['conflicting_evidence_refs'] = [row['evidence_id'] for row in refuting]
    return result
