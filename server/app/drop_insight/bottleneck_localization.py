"""Locate an observed path; do not promote it to a causal or fixed outcome."""
from .evidence import classify_evidence


def localize_verified_observation(verification, supporting):
    result = {'schema_version': 'bottleneck-localization.v1', 'status': 'NOT_LOCALIZED',
              'claim_scope': 'BOUNDED_OBSERVATION', 'causal_root_cause_verified': False,
              'same_load_fix_verified': False, 'evidence_refs': []}
    if (verification.get('status') != 'VERIFIED' or verification.get('claim_scope') != 'BOUNDED_OBSERVATION'
        or verification.get('causal_root_cause_verified') is not False or verification.get('counter_claim_count', 0)):
        return result
    observation = verification.get('observation_verification') or {}
    if observation.get('status') == 'VERIFIED' and observation.get('checked_ratio') == 1:
        for row in observation.get('criteria', []):
            measurement = row.get('measurement') or {}
            signal = measurement.get('signal')
            locations = {'network_latency': 'HTTP 调用耗时路径（未区分服务处理与传输）',
                         'downstream_latency': '下游调用耗时路径',
                         'io_latency': '目标进程同步 open/write/sync/close 路径（不是块设备归因）'}
            if row.get('kind') == 'expected' and row.get('matches') is True and signal in locations:
                envelopes = [e for e in supporting if e.evidence_id in observation.get('evidence_refs', [])
                             and classify_evidence(e)['can_support_conclusion']]
                if not envelopes:
                    continue
                if signal == 'io_latency' and not any(
                    e.observation.get('metadata', {}).get('signals', {}).get('io_latency', {}).get('measurement_scope')
                    == 'TARGET_APPLICATION_SYNC_IO' for e in envelopes):
                    continue
                result.update(status='LOCALIZED', domain=signal, location=locations[signal],
                              evidence_refs=observation['evidence_refs'], temporal_relationship='SINGLE_ARTIFACT_WINDOW')
                return result
    contract = verification.get('observation_contract') or {}
    if (contract.get('contract_id') in {'python-profile-and-os-cpu.v1', 'go-profile-and-os-cpu.v1'}
        and verification.get('coverage_ratio') == 1 and verification.get('has_independent_counter_or_control') is True):
        for envelope in supporting:
            if not classify_evidence(envelope)['can_support_conclusion']:
                continue
            metadata = envelope.observation.get('metadata', {})
            profile_schema = ('go_pprof_analysis.' if contract['contract_id'].startswith('go-') else 'pyspy_analysis.')
            if not str(metadata.get('schema_version', '')).startswith(profile_schema):
                continue
            metrics = metadata.get('hypothesis_predicate', {}).get('metrics', {})
            function = metrics.get('dominant_function')
            if function and metrics.get('observation_contract') == contract['contract_id']:
                result.update(status='LOCALIZED', domain='cpu_hot_path', location=str(function),
                              evidence_refs=[envelope.evidence_id], temporal_relationship='SEPARATE_COLLECTION_WINDOWS')
                return result
    return result
