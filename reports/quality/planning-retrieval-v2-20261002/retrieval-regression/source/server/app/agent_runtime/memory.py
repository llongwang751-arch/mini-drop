"""Short-term checkpoint and context-window policy."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass


def project_investigation_memory(report: dict | None, evidence: list[dict]) -> dict:
    """Bounded working notebook derived from durable records, never new evidence.

    Keep rejected observations visible so model summaries cannot turn them into
    accepted facts. Raw artifacts and the verifier remain authoritative.
    """
    report = report or {}
    observations = []
    for row in evidence[-8:]:
        envelope = row.get('envelope') or {}
        raw = json.dumps(envelope.get('observation') or {}, ensure_ascii=False, default=str)
        observations.append({
            'evidence_id': row.get('evidence_id'), 'hypothesis_id': row.get('hypothesis_id'),
            'role': row.get('role'), 'classification': row.get('classification') or {},
            'source': envelope.get('source') or {}, 'scope': envelope.get('scope') or {},
            'time_range': envelope.get('time_range') or {},
            'observation_excerpt': raw[:1600], 'observation_truncated': len(raw) > 1600,
            'limitations': (envelope.get('limitations') or [])[:5],
        })
    return {'kind': 'CURRENT_INVESTIGATION_PROJECTION', 'status': 'READY', 'is_evidence': False,
            'latest_report_id': report.get('report_id'),
            'verification': report.get('verification') or {},
            'limitations': (report.get('limitations') or [])[:8],
            'observations': observations,
            'omitted_evidence_count': max(0, len(evidence) - len(observations)),
            'authority': 'Use referenced durable Evidence and verifier decisions; this notebook creates no facts.'}


def load_investigation_memory(diagnosis_id: str) -> dict:
    """Load only this investigation, independently of lossy chat compaction."""
    from server.app.database import new_session
    from server.app.models import DropInsightEvidenceModel, DropInsightReportModel
    from sqlalchemy.exc import SQLAlchemyError
    session = new_session()
    try:
        report = (session.query(DropInsightReportModel)
                  .filter(DropInsightReportModel.diagnosis_id == diagnosis_id)
                  .order_by(DropInsightReportModel.created_at.desc()).first())
        query = session.query(DropInsightEvidenceModel).filter(DropInsightEvidenceModel.diagnosis_id == diagnosis_id)
        count = query.count()
        rows = query.order_by(DropInsightEvidenceModel.created_at.desc(), DropInsightEvidenceModel.id.desc()).limit(8).all()
        result = project_investigation_memory(report.to_dict() if report else None,
                                              [row.to_dict() for row in reversed(rows)])
        result['omitted_evidence_count'] = max(0, count - len(rows))
        return result
    except SQLAlchemyError:
        # History degradation is explicit; it does not authorize a diagnosis or
        # turn missing observations into evidence. Do not expose SQL/payloads.
        return {'kind': 'CURRENT_INVESTIGATION_PROJECTION', 'status': 'UNAVAILABLE',
                'is_evidence': False, 'observations': [], 'reason': 'MEMORY_READ_FAILED'}
    finally:
        session.close()


@dataclass(frozen=True)
class AgentMemoryPolicy:
    checkpoint_backend: str
    max_messages: int
    max_tokens: int
    keep_tokens: int

    @classmethod
    def from_env(cls) -> "AgentMemoryPolicy":
        max_messages = min(
            max(int(os.getenv("MINI_DROP_AGENT_MEMORY_MAX_MESSAGES", "24")), 8),
            80,
        )
        max_tokens = min(
            max(int(os.getenv("MINI_DROP_AGENT_MEMORY_MAX_TOKENS", "12000")), 4000),
            60000,
        )
        keep_tokens = min(
            max(
                int(
                    os.getenv(
                        "MINI_DROP_AGENT_MEMORY_KEEP_TOKENS",
                        str(max_tokens // 2),
                    )
                ),
                2000,
            ),
            max_tokens - 1000,
        )
        return cls(
            checkpoint_backend=os.getenv(
                "MINI_DROP_AGENT_CHECKPOINT_BACKEND", "memory"
            ).strip().lower(),
            max_messages=max_messages,
            max_tokens=max_tokens,
            keep_tokens=keep_tokens,
        )
