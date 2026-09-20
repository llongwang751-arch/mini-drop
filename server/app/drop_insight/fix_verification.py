"""修复复测闭环：前后对比、修复验证会话与只读投影。

从 service.py 拆出的叶子模块：只依赖 SQLAlchemy、领域模型与 datetime 工具。
service 命名空间继续 re-export，作为调用方与测试的唯一补丁点。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from server.app.database import new_session
from server.app.models import ArtifactModel, FixVerificationModel
from server.app.state_machine import now_utc


def _parse_datetime(value):
    if value is None or not isinstance(value, str):
        return value
    try:
        from datetime import datetime

        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_ranges_overlap(start, end, requested: dict) -> bool:
    requested_start = requested.get("start")
    requested_end = requested.get("end")
    if not requested_start or not requested_end:
        return True
    try:
        from datetime import datetime, timezone

        if isinstance(requested_start, str):
            requested_start = datetime.fromisoformat(requested_start.replace("Z", "+00:00"))
        if isinstance(requested_end, str):
            requested_end = datetime.fromisoformat(requested_end.replace("Z", "+00:00"))
        start = _as_utc_with_timezone(start, timezone)
        end = _as_utc_with_timezone(end, timezone)
        requested_start = _as_utc_with_timezone(requested_start, timezone)
        requested_end = _as_utc_with_timezone(requested_end, timezone)
        return start < requested_end and end > requested_start
    except (TypeError, ValueError):
        return False


def _as_utc_with_timezone(value, timezone):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ── 修复前后 VERIFIED 验证闭环（guide #4.6）──────────────────

FIX_VERIFY_RELATIVE_THRESHOLD = 0.3


def compare_before_after(
    before_top: list[dict] | None,
    after_top: list[dict] | None,
    *,
    threshold: float = FIX_VERIFY_RELATIVE_THRESHOLD,
) -> dict:
    """Compare the dominant hotspot between a before and after profile task.

    A fix is VERIFIED when the before-task hotspot function has disappeared
    from the after top list or its percent dropped by at least ``threshold``
    (relative). Pure and deterministic so it can be unit-tested.
    """
    def _hotspots(rows):
        return sorted(
            [row for row in (rows or []) if isinstance(row, dict)],
            key=lambda row: float(row.get("percent") or 0),
            reverse=True,
        )

    before = _hotspots(before_top)
    after = _hotspots(after_top)
    if not before:
        return {
            "outcome": "REJECTED",
            "reason": "修复前任务没有有效 TopN 热点数据，无法建立对比基线",
        }
    if not after:
        return {
            "outcome": "REJECTED",
            "reason": "修复后任务没有有效 TopN 热点数据，不能把数据缺失当作热点消失",
        }
    hotspot = before[0]
    name = str(hotspot.get("name") or "")
    before_pct = float(hotspot.get("percent") or 0)
    after_names = {row.get("name") for row in after if row.get("name")}
    after_same = next((row for row in after if row.get("name") == name), None)
    after_pct = float(after_same.get("percent") or 0) if after_same else 0.0

    if name and name not in after_names:
        outcome, reason = "VERIFIED", f"修复后热点 {name} 已从 TopN 消失"
    elif after_pct <= before_pct * (1 - threshold):
        outcome, reason = (
            "VERIFIED",
            f"热点 {name} 占比由 {before_pct:.1f}% 降至 {after_pct:.1f}%",
        )
    else:
        outcome, reason = (
            "REJECTED",
            f"热点 {name} 占比未显著下降（{before_pct:.1f}% -> {after_pct:.1f}%）",
        )
    return {
        "outcome": outcome,
        "reason": reason,
        "before_hotspot": hotspot,
        "after_hotspot": after_same,
        "before_percent": before_pct,
        "after_percent": after_pct,
    }


def _task_top_functions(task_id: str) -> list[dict]:
    session = new_session()
    try:
        artifacts = (
            session.query(ArtifactModel)
            .filter(
                ArtifactModel.task_id == task_id,
                ArtifactModel.artifact_type == "top_json",
            )
            .all()
        )
        for artifact in artifacts:
            top = (artifact.meta_json or {}).get("top_functions")
            if isinstance(top, list):
                return top
        return []
    finally:
        session.close()


def verify_diagnosis_fix(
    diagnosis_id: str,
    *,
    before_task_id: str,
    after_task_id: str,
    fix_summary: str | None = None,
    created_by: str | None = None,
) -> dict | None:
    """Apply-fix -> same-load re-test -> before/after comparison."""
    before_top = _task_top_functions(before_task_id)
    after_top = _task_top_functions(after_task_id)
    comparison = compare_before_after(before_top, after_top)
    session = new_session()
    try:
        model = FixVerificationModel(
            id=f"fix_{uuid4().hex}",
            diagnosis_id=diagnosis_id,
            fix_summary=fix_summary,
            before_task_id=before_task_id,
            after_task_id=after_task_id,
            outcome=comparison["outcome"],
            before_hotspot_json=comparison.get("before_hotspot"),
            after_hotspot_json=comparison.get("after_hotspot"),
            comparison_json=comparison,
            created_by=created_by,
            created_at=now_utc(),
        )
        session.add(model)
        session.commit()
        session.refresh(model)
        return _fix_view(model)
    finally:
        session.close()


def list_fix_verifications(
    diagnosis_id: str, *, limit: int = 50
) -> list[dict]:
    session = new_session()
    try:
        rows = (
            session.query(FixVerificationModel)
            .filter(FixVerificationModel.diagnosis_id == diagnosis_id)
            .order_by(FixVerificationModel.created_at.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        return [_fix_view(row) for row in rows]
    finally:
        session.close()


def _fix_view(model) -> dict:
    return {
        "id": model.id,
        "diagnosis_id": model.diagnosis_id,
        "fix_summary": model.fix_summary,
        "before_task_id": model.before_task_id,
        "after_task_id": model.after_task_id,
        "outcome": model.outcome,
        "comparison": model.comparison_json or {},
        "created_at": model.created_at,
    }
