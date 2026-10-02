"""Explicit cross-diagnosis preference memory for the diagnosis Agent.

This store is deliberately narrower than a conversational memory system.  It
keeps only user-confirmed presentation and conservative planning preferences;
queries, inferred root causes, tool permissions and target bindings are never
written here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from server.app.database import new_session
from server.app.models import DropInsightSessionModel, OperatorPreferenceMemoryModel


_ALLOWED_DEPTHS = {"brief", "standard", "detailed"}
_ALLOWED_LANGUAGES = {"zh-CN", "en-US"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _validated_value(memory_key: str, value: Any) -> Any:
    if memory_key == "explanation_depth":
        normalized = str(value or "").strip().lower()
        if normalized not in _ALLOWED_DEPTHS:
            raise ValueError("explanation_depth must be brief, standard or detailed")
        return normalized
    if memory_key == "preferred_low_risk_first":
        if not isinstance(value, bool):
            raise ValueError("preferred_low_risk_first must be boolean")
        return value
    if memory_key == "response_language":
        normalized = str(value or "").strip()
        if normalized not in _ALLOWED_LANGUAGES:
            raise ValueError("response_language must be zh-CN or en-US")
        return normalized
    if memory_key == "timezone":
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 64 or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+-/"
            for character in normalized
        ):
            raise ValueError("timezone must be a bounded IANA-style timezone name")
        return normalized
    raise ValueError("unsupported operator preference key")


def list_operator_preferences(
    principal_id: str,
    *,
    project_scope: str | None = None,
) -> list[OperatorPreferenceMemoryModel]:
    session = new_session()
    try:
        query = session.query(OperatorPreferenceMemoryModel).filter(
            OperatorPreferenceMemoryModel.principal_id == principal_id,
            OperatorPreferenceMemoryModel.status == "ACTIVE",
        )
        if project_scope is not None:
            query = query.filter(
                OperatorPreferenceMemoryModel.project_scope == project_scope
            )
        return query.order_by(
            OperatorPreferenceMemoryModel.project_scope.asc(),
            OperatorPreferenceMemoryModel.memory_key.asc(),
        ).all()
    finally:
        session.close()


def put_operator_preference(
    principal_id: str,
    *,
    project_scope: str,
    memory_key: str,
    value: Any,
) -> OperatorPreferenceMemoryModel:
    principal = principal_id.strip() or "local-anonymous"
    scope = project_scope.strip() or "*"
    normalized = _validated_value(memory_key, value)
    timestamp = _now()
    session = new_session()
    try:
        model = session.query(OperatorPreferenceMemoryModel).filter(
            OperatorPreferenceMemoryModel.principal_id == principal,
            OperatorPreferenceMemoryModel.project_scope == scope,
            OperatorPreferenceMemoryModel.memory_key == memory_key,
        ).first()
        if model is None:
            model = OperatorPreferenceMemoryModel(
                id=f"opmem_{uuid4().hex}",
                principal_id=principal,
                project_scope=scope,
                memory_key=memory_key,
                value_json=normalized,
                source="EXPLICIT_USER",
                status="ACTIVE",
                created_at=timestamp,
                updated_at=timestamp,
            )
            session.add(model)
        else:
            model.value_json = normalized
            model.source = "EXPLICIT_USER"
            model.status = "ACTIVE"
            model.updated_at = timestamp
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            model = session.query(OperatorPreferenceMemoryModel).filter(
                OperatorPreferenceMemoryModel.principal_id == principal,
                OperatorPreferenceMemoryModel.project_scope == scope,
                OperatorPreferenceMemoryModel.memory_key == memory_key,
            ).one()
            model.value_json = normalized
            model.status = "ACTIVE"
            model.updated_at = timestamp
            session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_operator_preference(
    principal_id: str,
    *,
    project_scope: str,
    memory_key: str,
) -> bool:
    session = new_session()
    try:
        model = session.query(OperatorPreferenceMemoryModel).filter(
            OperatorPreferenceMemoryModel.principal_id == principal_id,
            OperatorPreferenceMemoryModel.project_scope == project_scope,
            OperatorPreferenceMemoryModel.memory_key == memory_key,
            OperatorPreferenceMemoryModel.status == "ACTIVE",
        ).first()
        if model is None:
            return False
        model.status = "DELETED"
        model.updated_at = _now()
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_safe_agent_preferences(diagnosis_id: str) -> dict[str, Any]:
    """Load explicit global preferences for one diagnosis owner.

    The returned block is prompt context only.  Policy, budget and tool
    allowlists remain server-owned and are intentionally absent.
    """

    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return {}
        rows = session.query(OperatorPreferenceMemoryModel).filter(
            OperatorPreferenceMemoryModel.principal_id == diagnosis.created_by,
            OperatorPreferenceMemoryModel.project_scope == "*",
            OperatorPreferenceMemoryModel.status == "ACTIVE",
        ).all()
        preferences: dict[str, Any] = {}
        for row in rows:
            try:
                preferences[row.memory_key] = _validated_value(
                    row.memory_key, row.value_json
                )
            except ValueError:
                continue
        return preferences
    except SQLAlchemyError:
        # Preference memory is optional context, never a reason to block the
        # deterministic diagnosis path during startup or schema recovery.
        return {}
    finally:
        session.close()
