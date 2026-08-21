"""Hash-bound post-hoc evaluator; never mutates diagnosis state."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.exc import IntegrityError

from server.app.diagnosis.store import DiagnosisStore, utcnow
from server.app.evaluation.artifacts import artifact_hash, canonical_artifact_json
from server.app.evaluation.oracle_repository import EvaluationOracleRepository, OracleUnavailableError
from server.app.evaluation.schemas import EvaluationRequest, FrozenDiagnosisArtifact
from server.app.models import DiagnosisEvaluationModel
from server.app.database import new_session


class ArtifactEvaluationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class DiagnosisArtifactEvaluator:
    def __init__(self, store: DiagnosisStore, oracle_repository: EvaluationOracleRepository):
        self.store = store
        self.oracle_repository = oracle_repository

    @staticmethod
    def _identity(request: EvaluationRequest) -> str:
        logical_identity = json.dumps(
            {
                "artifact_id": request.artifact_id,
                "artifact_hash": request.expected_artifact_hash,
                "evaluator_version": request.evaluator_version,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "evaluation:" + hashlib.sha256(logical_identity).hexdigest()

    def _existing(self, request: EvaluationRequest) -> dict[str, Any] | None:
        session = new_session()
        try:
            row = session.query(DiagnosisEvaluationModel).filter(
                DiagnosisEvaluationModel.artifact_id == request.artifact_id,
                DiagnosisEvaluationModel.artifact_hash == request.expected_artifact_hash,
                DiagnosisEvaluationModel.evaluator_version == request.evaluator_version,
            ).first()
            return row.to_dict() if row is not None else None
        finally:
            session.close()

    def _persist_failure(self, request: EvaluationRequest, code: str) -> dict[str, Any]:
        session = new_session()
        now = utcnow()
        try:
            existing = session.query(DiagnosisEvaluationModel).filter(
                DiagnosisEvaluationModel.artifact_id == request.artifact_id,
                DiagnosisEvaluationModel.artifact_hash == request.expected_artifact_hash,
                DiagnosisEvaluationModel.evaluator_version == request.evaluator_version,
            ).first()
            if existing is not None:
                return existing.to_dict()
            row = DiagnosisEvaluationModel(
                id=self._identity(request),
                artifact_id=request.artifact_id,
                artifact_hash=request.expected_artifact_hash,
                evaluator_version=request.evaluator_version,
                status="FAILED",
                failure_code=code,
                result_json={},
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.query(DiagnosisEvaluationModel).filter(
                    DiagnosisEvaluationModel.artifact_id == request.artifact_id,
                    DiagnosisEvaluationModel.artifact_hash == request.expected_artifact_hash,
                    DiagnosisEvaluationModel.evaluator_version == request.evaluator_version,
                ).first()
                if existing is None:
                    raise
                return existing.to_dict()
            return row.to_dict()
        finally:
            session.close()

    def _fail(self, request: EvaluationRequest, code: str, cause: Exception | None = None) -> None:
        # A missing artifact cannot satisfy the evaluation table's artifact FK.
        # Every failure tied to an existing artifact remains mandatory audit data;
        # persistence errors there must surface instead of being silently lost.
        if code != "ARTIFACT_NOT_FOUND":
            self._persist_failure(request, code)
        error = ArtifactEvaluationError(code)
        if cause is not None:
            raise error from cause
        raise error

    def evaluate(self, request: EvaluationRequest) -> dict[str, Any]:
        existing = self._existing(request)
        if existing is not None:
            if existing["status"] == "COMPLETED":
                return existing
            if existing.get("failure_code") != "ORACLE_UNAVAILABLE":
                raise ArtifactEvaluationError(
                    existing["failure_code"] or "EVALUATION_FAILED"
                )

        try:
            artifact = self.store.get_frozen_diagnosis_artifact(
                request.artifact_id
            )
        except ValueError as exc:
            self._fail(request, "ARTIFACT_HASH_INVALID", exc)
        if artifact is None:
            self._fail(request, "ARTIFACT_NOT_FOUND")
        if artifact["artifact_hash"] != request.expected_artifact_hash:
            self._fail(request, "ARTIFACT_HASH_MISMATCH")
        try:
            validated = FrozenDiagnosisArtifact.model_validate(artifact["payload"])
            canonical = canonical_artifact_json(validated)
        except Exception as exc:
            self._fail(request, "ARTIFACT_MALFORMED", exc)
        if artifact_hash(canonical) != request.expected_artifact_hash:
            self._fail(request, "ARTIFACT_HASH_INVALID")
        if not validated.case_id:
            self._fail(request, "CASE_ID_MISSING")
        try:
            oracle = self.oracle_repository.load(validated.case_id)
        except OracleUnavailableError as exc:
            self._fail(request, "ORACLE_UNAVAILABLE", exc)
        conclusion = validated.conclusion
        assessment = conclusion.get("cluster_assessment") or {}
        root_location = (
            conclusion.get("root_location")
            or assessment.get("root_location")
            or {}
        )
        domain_cause = (
            conclusion.get("domain_cause")
            or assessment.get("domain_cause")
            or {}
        )
        matches = {
            "instance_id": (
                oracle.expected_instance_id is None
                or root_location.get("target_ref") == oracle.expected_instance_id
            ),
            "location_type": (
                oracle.expected_location_type is None
                or root_location.get("type") == oracle.expected_location_type
            ),
            "domain_type": (
                oracle.expected_domain_type is None
                or domain_cause.get("type") == oracle.expected_domain_type
            ),
            "classification": (
                oracle.expected_classification is None
                or assessment.get("classification")
                == oracle.expected_classification
            ),
        }
        result = {"passed": all(matches.values()), "matches": matches}
        session = new_session()
        identity = self._identity(request)
        now = utcnow()
        try:
            existing = session.query(DiagnosisEvaluationModel).filter(
                DiagnosisEvaluationModel.artifact_id == request.artifact_id,
                DiagnosisEvaluationModel.artifact_hash == request.expected_artifact_hash,
                DiagnosisEvaluationModel.evaluator_version == request.evaluator_version,
            ).first()
            if existing is not None:
                if (
                    existing.status == "FAILED"
                    and existing.failure_code == "ORACLE_UNAVAILABLE"
                ):
                    existing.status = "COMPLETED"
                    existing.failure_code = None
                    existing.result_json = result
                    existing.updated_at = now
                    session.commit()
                return existing.to_dict()
            row = DiagnosisEvaluationModel(
                id=identity,
                artifact_id=request.artifact_id,
                artifact_hash=request.expected_artifact_hash,
                evaluator_version=request.evaluator_version,
                status="COMPLETED",
                result_json=result,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                session.commit()
            except IntegrityError:
                # Two workers may evaluate the same immutable identity at once.
                # The unique constraint is the serialization point; recover the
                # winner rather than turning an idempotent retry into a failure.
                session.rollback()
                existing = session.query(DiagnosisEvaluationModel).filter(
                    DiagnosisEvaluationModel.artifact_id == request.artifact_id,
                    DiagnosisEvaluationModel.artifact_hash == request.expected_artifact_hash,
                    DiagnosisEvaluationModel.evaluator_version == request.evaluator_version,
                ).first()
                if existing is None:
                    raise
                return existing.to_dict()
            return row.to_dict()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
