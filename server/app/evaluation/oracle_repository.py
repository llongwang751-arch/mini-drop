"""Evaluator-only private Oracle repository."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from server.app.evaluation.schemas import EvaluationOracle


class OracleUnavailableError(RuntimeError):
    pass


class EvaluationOracleRepository:
    """Loads private expected results from evaluator-owned storage only."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self, case_id: str) -> EvaluationOracle:
        if not self.path.is_file():
            raise OracleUnavailableError("evaluator oracle repository unavailable")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            value = payload.get(case_id) if isinstance(payload, dict) else None
            if value is None:
                raise OracleUnavailableError("evaluation oracle missing")
            if isinstance(value, dict) and "case_id" not in value:
                value = {"case_id": case_id, **value}
            return EvaluationOracle.model_validate(value)
        except OracleUnavailableError:
            raise
        except Exception as exc:
            raise OracleUnavailableError("evaluation oracle repository malformed") from exc
