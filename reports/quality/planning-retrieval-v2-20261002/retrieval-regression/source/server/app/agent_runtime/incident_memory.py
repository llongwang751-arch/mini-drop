"""Read-through episodic memory: persisted reports remain the authority.

No model-generated writeback, cross-user retrieval, or new schema. Archived,
expired, partial and different-environment cases cannot become route priors.
Even qualifying reports are historical claims, not current incident evidence.
"""
from datetime import datetime, timedelta, timezone

from server.app.database import new_session
from server.app.models import DropInsightSessionModel, DropInsightReportModel
from .retrieval import _tokenize


def recall_incidents(diagnosis_id: str, query: str, *, limit: int = 3) -> list[dict]:
    session = new_session()
    try:
        current = session.get(DropInsightSessionModel, diagnosis_id)
        if current is None or current.deleted_at is not None:
            return []
        target = current.target_json or {}
        if not target.get("service") or not target.get("environment"):
            return []
        # Narrow by owner, service and environment in SQL before loading text.
        rows = (session.query(DropInsightReportModel, DropInsightSessionModel)
                .join(DropInsightSessionModel, DropInsightReportModel.diagnosis_id == DropInsightSessionModel.id)
                .filter(DropInsightSessionModel.created_by == current.created_by,
                        DropInsightSessionModel.id != current.id,
                        DropInsightSessionModel.deleted_at.is_(None),
                        DropInsightSessionModel.target_json["service"].as_string() == target["service"],
                        DropInsightSessionModel.target_json["environment"].as_string() == target["environment"],
                        DropInsightReportModel.created_at >= datetime.now(timezone.utc) - timedelta(days=30))
                .order_by(DropInsightReportModel.created_at.desc()).limit(100).all())
        terms = set(_tokenize(query))
        ranked = []
        for report, incident in rows:
            verification = report.verification_json or {}
            if (verification.get("status") != "VERIFIED"
                    or verification.get("coverage_ratio") != 1.0
                    or not verification.get("has_independent_counter_or_control")
                    or not report.evidence_refs_json):
                continue
            other = incident.target_json or {}
            if any(target.get(k) != other.get(k) for k in ("runtime", "version") if target.get(k) or other.get(k)):
                continue
            score = len(terms & set(_tokenize(report.conclusion)))
            if score:
                ranked.append((score, {"diagnosis_id": incident.id, "report_id": report.id,
                    "service": target["service"], "environment": target["environment"],
                    "summary": report.conclusion[:1000], "limitations": (report.limitations_json or [])[:5],
                    "recorded_at": report.created_at.isoformat(), "is_evidence": False,
                    "status": "HISTORICAL_REPORT_NOT_REVALIDATED"}))
        ranked.sort(key=lambda r: -r[0])
        seen = set()
        result = []
        for _, item in ranked:
            if item["diagnosis_id"] not in seen:
                seen.add(item["diagnosis_id"])
                result.append(item)
            if len(result) >= min(5, max(1, limit)):
                break
        return result
    finally:
        session.close()
