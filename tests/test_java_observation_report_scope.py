"""Replay an immutable cloud failure through the real report persistence path."""
from datetime import datetime
from pathlib import Path

import pytest

from server.app.database import init_db, new_session, reset_engine
from server.app.drop_insight import service
from server.app.drop_insight.schemas import GenerateReportRequest
from server.app.models import (DropInsightSessionModel, DropInsightHypothesisModel,
                               DropInsightEvidenceModel, DropInsightReportModel)

CASE = Path(__file__).resolve().parents[1] / 'reports/ai-diagnosis/fault-plaza-instance-scope-1-after-race-deployed-20260930-cases/java-gc-pressure.json'


@pytest.fixture
def cloud_gc(monkeypatch):
    from scripts.build_fault_plaza_acceptance_index import verified_json
    case = verified_json(CASE)
    records = case['records']
    original = next(r for r in records['reports'] if r['verification']['status'] == 'VERIFIED')
    diagnosis = records['diagnosis']
    hypothesis = next(h for h in records['hypotheses'] if h['hypothesis_id'] == original['hypothesis_id'])
    now = datetime.fromisoformat(case['finished_at'])
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///:memory:')
    reset_engine()
    init_db()
    monkeypatch.setattr(service, 'now_utc', lambda: now)
    monkeypatch.setattr(service, 'map_hot_functions', lambda *_a, **_k: {})

    def no_dispatch(report_id):
        with new_session() as db:
            report = db.get(DropInsightReportModel, report_id)
            db.expunge(report)
            return report

    monkeypatch.setattr(service, '_apply_report_effects', no_dispatch)
    with new_session() as db:
        db.add(DropInsightSessionModel(id=diagnosis['diagnosis_id'], query=diagnosis['query'],
            target_json=diagnosis['target'], time_range_json=diagnosis['time_range'],
            mode=diagnosis['mode'], skill_policy=diagnosis['skill_policy'],
            budget_json=diagnosis['budget'], status='COLLECTING_EVIDENCE', version=diagnosis['version'],
            created_at=now, updated_at=now))
        db.add(DropInsightHypothesisModel(id=hypothesis['hypothesis_id'], diagnosis_id=diagnosis['diagnosis_id'],
            statement=hypothesis['statement'], expected_observations_json=hypothesis['expected_observations'],
            falsification_criteria_json=hypothesis['falsification_criteria'], status='OPEN', source=hypothesis['source'],
            round_index=hypothesis['round_index'], created_at=now, updated_at=now))
        for e in records['evidence']:
            if e['hypothesis_id'] == hypothesis['hypothesis_id']:
                db.add(DropInsightEvidenceModel(id=e['evidence_id'], diagnosis_id=diagnosis['diagnosis_id'],
                    hypothesis_id=hypothesis['hypothesis_id'], role=e['role'], envelope_json=e['envelope'],
                    classification_json=e['classification'], created_at=now))
        db.commit()
    yield diagnosis['diagnosis_id'], hypothesis['hypothesis_id'], original
    reset_engine()


def test_new_gc_report_marks_observation_without_changing_its_verified_measurements(cloud_gc):
    did, hid, original = cloud_gc
    report = service.generate_report(did, GenerateReportRequest(hypothesis_id=hid))
    v = report.verification_json
    assert v['status'] == original['verification']['status'] == 'VERIFIED'
    assert v['coverage_ratio'] == original['verification']['coverage_ratio'] == 1
    assert v['has_independent_counter_or_control'] is True
    assert v['claim_scope'] == 'BOUNDED_OBSERVATION'
    assert v['causal_root_cause_verified'] is False
    assert report.conclusion.startswith('已验证观测：')
    assert 'GC 101 次' in report.conclusion
    assert '418 ms' in report.conclusion
    assert not v['remediation']['root_cause_fixes']
    from scripts.run_fault_plaza_strict_acceptance import evaluate_reports
    assert evaluate_reports('java-gc-pressure', [report.to_dict()])['root_cause_accepted'] is False


def test_replay_of_existing_gc_report_preserves_legacy_scope_and_text(cloud_gc):
    did, hid, original = cloud_gc
    with new_session() as db:
        db.add(DropInsightReportModel(id=original['report_id'], diagnosis_id=did, hypothesis_id=hid,
            conclusion=original['conclusion'], confidence=original['confidence'],
            evidence_refs_json=original['evidence_refs'], counter_evidence_refs_json=original['counter_evidence_refs'],
            verification_json=original['verification'], effects_status='APPLIED',
            created_at=datetime.fromisoformat(original['created_at'])))
        db.commit()
    again = service.generate_report(did, GenerateReportRequest(hypothesis_id=hid))
    assert again.conclusion == original['conclusion']
    assert again.verification_json == original['verification']
