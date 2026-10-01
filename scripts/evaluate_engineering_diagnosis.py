"""Engineering diagnosis acceptance, separate from experimental causal proof."""
from __future__ import annotations

from copy import deepcopy
import math

from scripts.audit_fault_plaza_failures import evaluate_recorded_lineage
from server.app.drop_insight.performance_criteria import evaluate_performance_criterion
from scripts.engineering_profile_observation import measured_runtime_profile


def _resolve(document, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('invalid claim pointer')
    value = document
    for part in pointer[1:].split('/'):
        token = part.replace('~1', '/').replace('~0', '~')
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def evaluate_engineering_case(case, contract):
    """Re-evaluate a recorded case; never mutate its reports or causal grade.

    Independent CONTROL, completed status and a fixed round count are not
    required. Target identity, provenance, measured claims and lab cleanup are.
    A complete refutation passes diagnosis response, never positive localization.
    """
    records = case.get('records') or {}
    lineage_records = deepcopy(records)
    # COUNTER has the same provenance obligations as other admitted evidence.
    # Normalize admission only for the legacy lineage checker; roles stay intact.
    for evidence in lineage_records.get('evidence', []):
        if (evidence.get('classification') or {}).get('decision') == 'ACCEPT_COUNTER':
            evidence['classification']['decision'] = 'ACCEPT_NEUTRAL'
    lineage = evaluate_recorded_lineage(lineage_records, case.get('target'))
    admitted = set(lineage['matched_evidence_ids'])
    evidence_by_id = {e['evidence_id']: e for e in records.get('evidence', [])}
    domain = contract['domain']
    decisions = []
    for report in records.get('reports', []):
        verification = report.get('verification') or {}
        observation = verification.get('observation_verification') or {}
        local = verification.get('bottleneck_localization') or {}
        refs = set(report.get('evidence_refs') or [])
        counters = set(report.get('counter_evidence_refs') or [])
        valid_directions = set()
        profile = False
        for claim in verification.get('claims', []):
            eid = claim.get('evidence_id')
            evidence = evidence_by_id.get(eid) or {}
            envelope = evidence.get('envelope') or {}
            source = envelope.get('source') or {}
            quality = envelope.get('quality') or {}
            direction = claim.get('direction')
            if (claim.get('valid') is not True or eid not in admitted
                    or claim.get('claim_type') != 'HYPOTHESIS_PREDICATE'
                    or claim.get('artifact_sha256') != source.get('artifact_sha256')
                    or evidence.get('hypothesis_id') != report.get('hypothesis_id')
                    or evidence.get('diagnosis_id') != records.get('diagnosis', {}).get('diagnosis_id')
                    or any(quality.get(key) is True for key in ('degraded', 'truncated'))
                    or direction not in {'SUPPORT', 'COUNTER'}
                    or direction != evidence.get('role')
                    or eid not in (refs if direction == 'SUPPORT' else counters)):
                continue
            try:
                actual = _resolve(envelope.get('observation') or {}, claim.get('json_pointer'))
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if actual != claim.get('claimed_value') or actual != direction:
                continue
            predicate = (envelope.get('observation', {}).get('metadata') or {}).get('hypothesis_predicate') or {}
            metrics = predicate.get('metrics') or {}
            claim_profile = (direction == 'SUPPORT'
                and metrics.get('observation_contract') in contract.get('profile_contracts', [])
                and bool(metrics.get('source_locations'))
                and _finite(metrics.get('dominant_percent'))
                and metrics['dominant_percent'] >= contract.get('minimum_profile_percent', 20))
            profile_rule = contract.get('runtime_profile') or {}
            claim_profile = claim_profile or (direction == 'SUPPORT' and bool(profile_rule)
                and measured_runtime_profile(evidence, profile_rule, records, admitted, domain))
            profile = profile or claim_profile
            if predicate.get('signal') == domain or claim_profile:
                valid_directions.add(direction)

        criteria = observation.get('criteria') or []
        def measured(c):
            envelope = evidence_by_id.get(c.get('evidence_id'), {}).get('envelope') or {}
            metadata = envelope.get('observation', {}).get('metadata') or {}
            identity = metadata.get('process_identity') or {}
            actual = evaluate_performance_criterion(c.get('criterion', ''), metadata.get('signals') or {})
            return (identity.get('verified') is True and identity.get('pid') == case.get('target', {}).get('pid')
                and actual is not None and actual == c.get('measurement')
                and actual['matches'] == c.get('matches'))
        complete = (observation.get('schema_version') == 'performance-observation-verification.v1'
            and _finite(observation.get('checked_ratio')) and observation.get('checked_ratio') == 1 and bool(criteria)
            and all(isinstance(c, dict) and c.get('checked') is True
                and type(c.get('matches')) is bool
                and c.get('evidence_id') in admitted
                and c.get('measurement', {}).get('signal') == domain
                and _finite(c.get('measurement', {}).get('value')) and measured(c) for c in criteria)
            and bool(observation.get('evidence_refs'))
            and set(observation.get('evidence_refs', [])).issubset(admitted)
            and len({c.get('evidence_id') for c in criteria}) == 1)
        expected = [c for c in criteria if c.get('kind') == 'expected']
        falsification = [c for c in criteria if c.get('kind') == 'falsification']
        supported = (complete and observation.get('status') == 'VERIFIED'
            and 'SUPPORT' in valid_directions and bool(expected)
            and all(c['matches'] for c in expected) and not any(c['matches'] for c in falsification))
        refuted = (complete and observation.get('status') == 'REFUTED'
            and 'COUNTER' in valid_directions and bool(expected)
            and any(c['matches'] for c in falsification))
        supported = (supported or profile) and 'COUNTER' not in valid_directions
        localized = (supported and local.get('status') == 'LOCALIZED' and local.get('domain') == domain
            and bool(local.get('location')) and bool(local.get('evidence_refs'))
            and set(local['evidence_refs']).issubset(refs & admitted))
        outcome = ('LOCALIZED_ANOMALY' if localized else 'SUPPORTED_OBSERVATION' if supported
            else 'REFUTED' if refuted else 'INSUFFICIENT_EVIDENCE')
        if observation.get('status') == 'CONFLICTING_OBSERVATIONS' or (supported and refuted):
            outcome = 'CONFLICTING_EVIDENCE'
        decisions.append({'report_id': report.get('report_id'), 'outcome': outcome,
            'location': local.get('location') if localized else None,
            'independent_control_required': False})
    accepted = [r for r in decisions if r['outcome'] in {'LOCALIZED_ANOMALY', 'SUPPORTED_OBSERVATION', 'REFUTED'}]
    directions = {'negative' if r['outcome'] == 'REFUTED' else 'positive' for r in accepted}
    selected = max(accepted, key=lambda r: r['outcome'] == 'LOCALIZED_ANOMALY', default=None)
    safe = (case.get('cleanup_verified') is True and case.get('session_drained') is True
        and (case.get('intervention') or {}).get('recovery_observed') is True
        and records.get('diagnosis', {}).get('status') in {'COMPLETED', 'INSUFFICIENT_EVIDENCE'})
    passed = lineage['recorded_chain_consistent'] and safe and selected is not None and len(directions) == 1
    return {'scenario_id': case['scenario_id'], 'diagnosis_id': case.get('diagnosis_id'),
        'diagnosis_accepted': bool(passed),
        'localization_accepted': bool(passed and selected['outcome'] == 'LOCALIZED_ANOMALY'),
        'outcome': selected['outcome'] if passed else 'CONFLICTING_EVIDENCE' if len(directions) > 1 else 'INSUFFICIENT_EVIDENCE',
        'location': selected['location'] if passed else None,
        'causal_root_cause_verified': False, 'same_load_fix_verified': False,
        'recorded_chain_verified': lineage['recorded_chain_consistent'], 'cleanup_and_recovery_verified': safe,
        'reports': decisions, 'profile': 'engineering-diagnosis.v1'}
