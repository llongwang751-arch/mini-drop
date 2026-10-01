"""Separate path-localization evaluation; never rewrites historical RCA grades."""
from __future__ import annotations


DOMAINS={'go-cpu-hotspot':'cpu_hot_path','go-network-latency':'network_latency','go-file-io':'io_latency'}


def evaluate_localization_reports(scenario_id, reports):
    domain=DOMAINS[scenario_id]
    decisions=[]
    for report in reports:
        v=report.get('verification') or {};local=v.get('bottleneck_localization') or {}
        verified=(v.get('status')=='VERIFIED' and v.get('claim_scope')=='BOUNDED_OBSERVATION'
                  and v.get('causal_root_cause_verified') is False and local.get('status')=='LOCALIZED'
                  and local.get('domain')==domain and local.get('causal_root_cause_verified') is False
                  and local.get('same_load_fix_verified') is False and not report.get('counter_evidence_refs')
                  and bool(local.get('location')) and bool(local.get('evidence_refs'))
                  and set(local.get('evidence_refs',[])).issubset(set(report.get('evidence_refs') or [])))
        observation=v.get('observation_verification') or {}
        if domain=='cpu_hot_path':
            verified=verified and ('goCPUHotFunction' in str(local.get('location'))
                and v.get('has_independent_counter_or_control') is True and v.get('coverage_ratio')==1)
        else:
            verified=verified and observation.get('status')=='VERIFIED' and observation.get('checked_ratio')==1
        decisions.append({'report_id':report.get('report_id'),'domain':local.get('domain'),
                          'location':local.get('location'),'localization_accepted':bool(verified)})
    return {'scenario_id':scenario_id,'localization_accepted':any(r['localization_accepted'] for r in decisions),
            'reports':decisions,'boundary':'Observed path localization only; not causal root or same-load fix verification.'}
