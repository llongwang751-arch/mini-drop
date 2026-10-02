"""Wall-clock admission shared by planning and persisted probe dispatch."""
from datetime import datetime, timezone

FINALIZATION_RESERVE_SECONDS = 30
PLANNING_STAGE_SECONDS = 60
SCOPE_STAGE_SECONDS = 25


def remaining_seconds(diagnosis, *, now=None):
    if getattr(diagnosis, "mode", None) != "AUTONOMOUS":
        return None
    created = diagnosis.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    limit = max(10, min(1800, int((diagnosis.budget_json or {}).get("max_duration_seconds") or 300)))
    return max(0.0, limit - (timestamp - created).total_seconds())


def probe_deadline_check(diagnosis, tool_name, arguments, *, now=None):
    remaining = remaining_seconds(diagnosis, now=now)
    required = max(0, int(arguments.get("duration_seconds") or 0)) + FINALIZATION_RESERVE_SECONDS
    # Status reads do not start collectors. Interactive sessions retain their
    # existing cumulative resource limits rather than a new expiry policy.
    allowed = tool_name == "get_agent_status" or remaining is None or remaining >= required
    return {"name": "BUDGET_WALL_CLOCK", "result": "PASS" if allowed else "FAIL",
            "remaining_seconds": remaining, "required_seconds": required}


def planning_seconds(diagnosis_id, cap):
    from sqlalchemy.exc import SQLAlchemyError
    from server.app.database import new_session
    from server.app.models import DropInsightSessionModel
    session = new_session()
    try:
        diagnosis = session.get(DropInsightSessionModel, diagnosis_id)
        if diagnosis is None:
            return 0.0
        remaining = remaining_seconds(diagnosis)
        return float(cap) if remaining is None else max(0.0, min(cap, remaining - FINALIZATION_RESERVE_SECONDS - 15))
    except SQLAlchemyError:
        # Database unavailability must not authorize another model round.
        return 0.0
    finally:
        session.close()
