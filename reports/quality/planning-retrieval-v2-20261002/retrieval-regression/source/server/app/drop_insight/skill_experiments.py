"""Persisted randomized Skill experiments with conservative rollout gates."""

from __future__ import annotations

import hashlib
import math
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from server.app.database import new_session
from server.app.models import (
    DiagnosticExperimentAssignmentModel,
    DiagnosticExperimentMetricModel,
    DiagnosticExperimentModel,
    DiagnosticExperimentObservationModel,
    DropInsightReportModel,
    DropInsightSessionModel,
    DropInsightToolCallModel,
)

from .schemas import (
    AssignDiagnosticExperimentRequest,
    CreateDiagnosticExperimentRequest,
    RecordDiagnosticExperimentOutcomeRequest,
)
from .service import create_diagnosis


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _assigned_arm(
    *,
    salt: str,
    unit_key: str,
    stratum: str,
    treatment_ratio_bps: int,
) -> str:
    digest = hashlib.sha256(
        f"{salt}\0{stratum}\0{unit_key}".encode("utf-8")
    ).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10_000
    return "AUTO" if bucket < treatment_ratio_bps else "DISABLED"


def create_experiment(
    payload: CreateDiagnosticExperimentRequest,
    *,
    created_by: str,
) -> DiagnosticExperimentModel:
    salt = secrets.token_urlsafe(32)
    timestamp = _now()
    model = DiagnosticExperimentModel(
        id=f"skill_exp_{uuid4().hex}",
        name=payload.name,
        status="RUNNING",
        assignment_salt=salt,
        assignment_salt_sha256=_sha256(salt),
        treatment_ratio_bps=round(payload.treatment_ratio * 10_000),
        minimum_labeled_per_arm=payload.minimum_labeled_per_arm,
        minimum_effect_ppm=round(
            payload.minimum_effect_percentage_points * 10_000
        ),
        alpha=payload.alpha,
        primary_metric="ROOT_CAUSE_ACCURACY",
        guardrails_json=payload.guardrails,
        recommendation_json={},
        created_by=created_by,
        created_at=timestamp,
        updated_at=timestamp,
    )
    session = new_session()
    try:
        session.add(model)
        session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def list_experiments() -> list[DiagnosticExperimentModel]:
    session = new_session()
    try:
        return session.query(DiagnosticExperimentModel).order_by(
            DiagnosticExperimentModel.created_at.desc()
        ).all()
    finally:
        session.close()


def get_experiment(experiment_id: str) -> DiagnosticExperimentModel | None:
    session = new_session()
    try:
        return session.get(DiagnosticExperimentModel, experiment_id)
    finally:
        session.close()


def assign_experiment_diagnosis(
    experiment_id: str,
    payload: AssignDiagnosticExperimentRequest,
    *,
    principal: str,
) -> dict[str, Any] | None:
    session = new_session()
    try:
        experiment = session.get(DiagnosticExperimentModel, experiment_id)
        if experiment is None:
            return None
        if experiment.status != "RUNNING":
            raise ValueError(
                f"experiment does not accept new traffic in status {experiment.status}"
            )
        salt = experiment.assignment_salt
        ratio = experiment.treatment_ratio_bps
    finally:
        session.close()

    arm = _assigned_arm(
        salt=salt,
        unit_key=payload.unit_key,
        stratum=payload.stratum,
        treatment_ratio_bps=ratio,
    )
    diagnosis_request = payload.diagnosis.model_copy(update={"skill_policy": arm})
    diagnosis = create_diagnosis(diagnosis_request, created_by=principal)
    assignment = DiagnosticExperimentAssignmentModel(
        id=f"skill_assign_{uuid4().hex}",
        experiment_id=experiment_id,
        diagnosis_id=diagnosis.id,
        unit_hash=_sha256(payload.unit_key),
        stratum=payload.stratum,
        arm=arm,
        assigned_at=_now(),
    )
    session = new_session()
    try:
        session.add(assignment)
        session.commit()
        session.refresh(assignment)
        return {
            "assignment": assignment.to_dict(),
            "diagnosis": diagnosis.to_dict(),
            "assignment_contract": {
                "method": "SALTED_SHA256_STICKY_BUCKET",
                "raw_unit_key_persisted": False,
                "treatment_ratio": ratio / 10_000,
            },
        }
    except Exception:
        session.rollback()
        # The diagnosis is retained for audit if assignment persistence fails;
        # it cannot be relabelled as experimental traffic later.
        raise
    finally:
        session.close()


def record_experiment_outcome(
    experiment_id: str,
    diagnosis_id: str,
    payload: RecordDiagnosticExperimentOutcomeRequest,
    *,
    recorded_by: str,
) -> DiagnosticExperimentObservationModel | None:
    session = new_session()
    try:
        assignment = session.query(DiagnosticExperimentAssignmentModel).filter(
            DiagnosticExperimentAssignmentModel.experiment_id == experiment_id,
            DiagnosticExperimentAssignmentModel.diagnosis_id == diagnosis_id,
        ).first()
        if assignment is None:
            return None
        timestamp = _now()
        model = session.query(DiagnosticExperimentObservationModel).filter(
            DiagnosticExperimentObservationModel.assignment_id == assignment.id
        ).first()
        if model is None:
            model = DiagnosticExperimentObservationModel(
                id=f"skill_obs_{uuid4().hex}",
                assignment_id=assignment.id,
                created_at=timestamp,
                updated_at=timestamp,
                root_cause_correct=payload.root_cause_correct,
                outcome_source=payload.outcome_source,
                safety_violation_count=payload.safety_violation_count,
                notes=payload.notes,
                metadata_json=payload.metadata,
                recorded_by=recorded_by,
            )
            session.add(model)
        else:
            model.root_cause_correct = payload.root_cause_correct
            model.outcome_source = payload.outcome_source
            model.safety_violation_count = payload.safety_violation_count
            model.notes = payload.notes
            model.metadata_json = payload.metadata
            model.recorded_by = recorded_by
            model.updated_at = timestamp
        session.commit()
        session.refresh(model)
        return model
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _proportion_summary(success: int, total: int) -> dict[str, Any]:
    return {
        "success": success,
        "total": total,
        "rate": round(success / total, 6) if total else None,
    }


def _two_proportion_test(
    treatment_success: int,
    treatment_total: int,
    control_success: int,
    control_total: int,
) -> dict[str, Any]:
    if not treatment_total or not control_total:
        return {
            "method": "TWO_PROPORTION_Z_TEST",
            "p_value": None,
            "delta_percentage_points": None,
            "confidence_interval_95_percentage_points": [None, None],
        }
    treatment_rate = treatment_success / treatment_total
    control_rate = control_success / control_total
    delta = treatment_rate - control_rate
    pooled = (treatment_success + control_success) / (
        treatment_total + control_total
    )
    pooled_se = math.sqrt(
        pooled * (1 - pooled) * (1 / treatment_total + 1 / control_total)
    )
    p_value = 1.0 if pooled_se == 0 else math.erfc(
        abs(delta / pooled_se) / math.sqrt(2)
    )
    interval_se = math.sqrt(
        treatment_rate * (1 - treatment_rate) / treatment_total
        + control_rate * (1 - control_rate) / control_total
    )
    lower = 100 * (delta - 1.96 * interval_se)
    upper = 100 * (delta + 1.96 * interval_se)
    return {
        "method": "TWO_PROPORTION_Z_TEST",
        "p_value": p_value,
        "delta_percentage_points": round(100 * delta, 4),
        "confidence_interval_95_percentage_points": [
            round(lower, 4), round(upper, 4)
        ],
    }


def summarize_experiment(experiment_id: str) -> dict[str, Any] | None:
    session = new_session()
    try:
        experiment = session.get(DiagnosticExperimentModel, experiment_id)
        if experiment is None:
            return None
        assignments = session.query(DiagnosticExperimentAssignmentModel).filter(
            DiagnosticExperimentAssignmentModel.experiment_id == experiment_id
        ).order_by(DiagnosticExperimentAssignmentModel.assigned_at.asc()).all()
        assignment_ids = [item.id for item in assignments]
        diagnosis_ids = [item.diagnosis_id for item in assignments]
        observations = (
            session.query(DiagnosticExperimentObservationModel).filter(
                DiagnosticExperimentObservationModel.assignment_id.in_(assignment_ids)
            ).all()
            if assignment_ids else []
        )
        diagnoses = (
            session.query(DropInsightSessionModel).filter(
                DropInsightSessionModel.id.in_(diagnosis_ids)
            ).all()
            if diagnosis_ids else []
        )
        reports = (
            session.query(DropInsightReportModel).filter(
                DropInsightReportModel.diagnosis_id.in_(diagnosis_ids)
            ).order_by(DropInsightReportModel.created_at.desc()).all()
            if diagnosis_ids else []
        )
        tool_calls = (
            session.query(DropInsightToolCallModel).filter(
                DropInsightToolCallModel.diagnosis_id.in_(diagnosis_ids)
            ).all()
            if diagnosis_ids else []
        )

        observation_by_assignment = {item.assignment_id: item for item in observations}
        diagnosis_by_id = {item.id: item for item in diagnoses}
        latest_report: dict[str, DropInsightReportModel] = {}
        for report in reports:
            latest_report.setdefault(report.diagnosis_id, report)
        tool_count: dict[str, int] = defaultdict(int)
        for tool_call in tool_calls:
            tool_count[tool_call.diagnosis_id] += 1

        arms: dict[str, dict[str, Any]] = {}
        for arm in ("AUTO", "DISABLED"):
            arm_assignments = [item for item in assignments if item.arm == arm]
            labeled = [
                observation_by_assignment[item.id]
                for item in arm_assignments
                if item.id in observation_by_assignment
            ]
            verified = 0
            terminal = 0
            total_tools = 0
            for assignment in arm_assignments:
                diagnosis = diagnosis_by_id.get(assignment.diagnosis_id)
                if diagnosis and diagnosis.status in {
                    "COMPLETED", "INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"
                }:
                    terminal += 1
                report = latest_report.get(assignment.diagnosis_id)
                if report and (report.verification_json or {}).get("status") == "VERIFIED":
                    verified += 1
                total_tools += tool_count.get(assignment.diagnosis_id, 0)
            correct = sum(item.root_cause_correct for item in labeled)
            arms[arm] = {
                "assigned": len(arm_assignments),
                "terminal": terminal,
                "verified_reports": _proportion_summary(
                    verified, len(arm_assignments)
                ),
                "root_cause_accuracy": _proportion_summary(correct, len(labeled)),
                "mean_tool_calls": (
                    round(total_tools / len(arm_assignments), 4)
                    if arm_assignments else None
                ),
                "safety_violation_count": sum(
                    item.safety_violation_count for item in labeled
                ),
            }
        test = _two_proportion_test(
            arms["AUTO"]["root_cause_accuracy"]["success"],
            arms["AUTO"]["root_cause_accuracy"]["total"],
            arms["DISABLED"]["root_cause_accuracy"]["success"],
            arms["DISABLED"]["root_cause_accuracy"]["total"],
        )
        snapshots = session.query(DiagnosticExperimentMetricModel).filter(
            DiagnosticExperimentMetricModel.experiment_id == experiment_id
        ).order_by(DiagnosticExperimentMetricModel.created_at.asc()).all()
        assignment_rows = []
        for assignment in reversed(assignments[-100:]):
            diagnosis = diagnosis_by_id.get(assignment.diagnosis_id)
            observation = observation_by_assignment.get(assignment.id)
            report = latest_report.get(assignment.diagnosis_id)
            assignment_rows.append(
                {
                    **assignment.to_dict(),
                    "diagnosis_status": (
                        diagnosis.status if diagnosis is not None else None
                    ),
                    "report_verification_status": (
                        (report.verification_json or {}).get("status")
                        if report is not None else None
                    ),
                    "labeled": observation is not None,
                    "root_cause_correct": (
                        observation.root_cause_correct
                        if observation is not None else None
                    ),
                    "outcome_source": (
                        observation.outcome_source
                        if observation is not None else None
                    ),
                }
            )
        return {
            "experiment": experiment.to_dict(),
            "arms": arms,
            "significance": test,
            "assignment_count": len(assignments),
            "labeled_count": len(observations),
            "assignments": assignment_rows,
            "metric_history": [item.to_dict() for item in snapshots],
            "truth_boundary": {
                "randomized_assignment": True,
                "assignment_method": "SALTED_SHA256_STICKY_BUCKET",
                "accuracy_requires_human_or_controlled_oracle_label": True,
                "automatic_rollout": False,
                "human_approval_required": True,
                "can_modify_prompt_code_or_permissions": False,
            },
        }
    finally:
        session.close()


def evaluate_experiment(
    experiment_id: str,
    *,
    evaluated_by: str,
) -> dict[str, Any] | None:
    summary = summarize_experiment(experiment_id)
    if summary is None:
        return None
    experiment_data = summary["experiment"]
    auto = summary["arms"]["AUTO"]
    control = summary["arms"]["DISABLED"]
    significance = summary["significance"]
    minimum = int(experiment_data["minimum_labeled_per_arm"])
    minimum_effect = float(experiment_data["minimum_effect_percentage_points"])
    guardrails = dict(experiment_data.get("guardrails") or {})
    max_safety = int(guardrails.get("maximum_safety_violations", 0))
    max_verified_regression = float(
        guardrails.get(
            "maximum_verified_rate_regression_percentage_points", 5.0
        )
    )
    auto_verified = auto["verified_reports"]["rate"]
    control_verified = control["verified_reports"]["rate"]
    verified_regression_pp = (
        0.0
        if auto_verified is None or control_verified is None
        else 100 * (control_verified - auto_verified)
    )
    blockers: list[str] = []
    if auto["root_cause_accuracy"]["total"] < minimum:
        blockers.append("AUTO_LABELED_SAMPLE_BELOW_MINIMUM")
    if control["root_cause_accuracy"]["total"] < minimum:
        blockers.append("DISABLED_LABELED_SAMPLE_BELOW_MINIMUM")
    if significance["p_value"] is None or significance["p_value"] > float(
        experiment_data["alpha"]
    ):
        blockers.append("SIGNIFICANCE_NOT_REACHED")
    if (
        significance["delta_percentage_points"] is None
        or significance["delta_percentage_points"] < minimum_effect
    ):
        blockers.append("MINIMUM_EFFECT_NOT_REACHED")
    safety_violations = (
        auto["safety_violation_count"] + control["safety_violation_count"]
    )
    if safety_violations > max_safety:
        blockers.append("SAFETY_GUARDRAIL_FAILED")
    if verified_regression_pp > max_verified_regression:
        blockers.append("VERIFIED_RATE_GUARDRAIL_FAILED")
    eligible = not blockers
    recommendation = {
        "decision": "RECOMMEND_HUMAN_ROLLOUT" if eligible else "NOT_READY",
        "eligible": eligible,
        "blockers": blockers,
        "evaluated_by": evaluated_by,
        "evaluated_at": _now().isoformat(),
        "primary_metric": experiment_data["primary_metric"],
        "significance": significance,
        "verified_rate_regression_percentage_points": round(
            verified_regression_pp, 4
        ),
        "safety_violation_count": safety_violations,
    }
    snapshot_payload = {
        "arms": summary["arms"],
        "significance": significance,
        "recommendation": recommendation,
    }
    session = new_session()
    try:
        experiment = session.get(DiagnosticExperimentModel, experiment_id)
        if experiment is None:
            return None
        timestamp = _now()
        snapshot = DiagnosticExperimentMetricModel(
            id=f"skill_metric_{uuid4().hex}",
            experiment_id=experiment_id,
            metrics_json=snapshot_payload,
            created_by=evaluated_by,
            created_at=timestamp,
        )
        session.add(snapshot)
        experiment.recommendation_json = recommendation
        experiment.updated_at = timestamp
        if eligible and experiment.status in {"RUNNING", "PAUSED"}:
            experiment.status = "ROLLOUT_RECOMMENDED"
        session.commit()
        session.refresh(snapshot)
        session.refresh(experiment)
        return {
            **summary,
            "experiment": experiment.to_dict(),
            "recommendation": recommendation,
            "snapshot": snapshot.to_dict(),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def approve_experiment_rollout(
    experiment_id: str,
    *,
    approved_by: str,
    reason: str,
) -> DiagnosticExperimentModel | None:
    session = new_session()
    try:
        experiment = session.get(DiagnosticExperimentModel, experiment_id)
        if experiment is None:
            return None
        recommendation = dict(experiment.recommendation_json or {})
        if experiment.status != "ROLLOUT_RECOMMENDED" or not recommendation.get(
            "eligible"
        ):
            raise ValueError("experiment has no eligible rollout recommendation")
        timestamp = _now()
        experiment.status = "APPROVED"
        experiment.approved_by = approved_by
        experiment.approved_at = timestamp
        experiment.updated_at = timestamp
        recommendation["human_approval"] = {
            "approved_by": approved_by,
            "approved_at": timestamp.isoformat(),
            "reason": reason,
        }
        experiment.recommendation_json = recommendation
        session.commit()
        session.refresh(experiment)
        return experiment
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
