"""Versioned evaluator-only adapter and safe public scoring projection.

Private Oracle values are parsed only after a diagnosis run has been frozen.  The
public result deliberately exposes boolean gates and keyed commitments rather
than expected or observed private values.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from server.app.evaluation.real_world_admission import validate_formal_admission


ORACLE_SCHEMA_VERSION = "real-world-oracle-v1"
_SUPPORTED_ORACLE_SCHEMA_VERSIONS = frozenset({ORACLE_SCHEMA_VERSION, "1.0"})
SCORE_SCHEMA_VERSION = "real-world-score-v1"


class RealWorldOracleUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class RealWorldOracleV1:
    case_id: str
    root_cause_id: str
    expected_summary: str
    counterfactual: str
    required_locations: tuple[str, ...]
    expected_terminal: Literal["ROOT_CAUSE", "ABSTAIN_OR_PROVISIONAL"]

    def __repr__(self) -> str:
        return "RealWorldOracleV1(<redacted>)"


class PublicRealWorldScore(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["real-world-score-v1"] = SCORE_SCHEMA_VERSION
    scorer_id: str = Field(min_length=1, max_length=64)
    opaque_case_token: str = Field(pattern=r"^case:[0-9a-f]{64}$")
    run_id: str = Field(min_length=1, max_length=128)
    verdict: Literal["PASS", "FAIL", "HARNESS_INVALID"]
    gates: dict[str, bool]
    oracle_commitment: str = Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")
    run_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    public_contract_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


_REQUIRED_CASE_FIELDS = {
    "case_id",
    "root_cause_id",
    "expected_summary",
    "counterfactual",
    "required_locations",
    "expected_terminal",
}
_ORACLE_ENVELOPE_FIELDS = {"schema_version", "cases", "warning"}
_ORACLE_CASE_OPTIONAL_FIELDS = {
    "base_sha",
    "fix_sha",
    "source_url",
    "title",
    "project",
    "language",
    "execution_track",
    "reproducibility",
    "minimum_repetitions",
    "business_scenario",
    "query",
    "observable_symptoms",
    "required_evidence",
    "workload_contract",
    "web_execution",
    "execution_note",
    "expected_terminal_class",
    "root_cause_category",
    "root_cause_type",
    "base_commit",
    "fix_commit",
    "source_revision",
    "fix_revision",
    "oracle_version",
    "upstream_url",
    "pr_url",
    "repository",
    "case_version",
    "replay_status",
    "notes",
    "source",
    "status",
}
_EXPECTED_TERMINALS = {"ROOT_CAUSE", "ABSTAIN_OR_PROVISIONAL"}
_LEGACY_TERMINAL_ALIASES = {
    "ROOT_CAUSE": "ROOT_CAUSE",
    "ABSTAIN_OR_PROVISIONAL": "ABSTAIN_OR_PROVISIONAL",
    "CONFIRMED": "ROOT_CAUSE",
    "CANDIDATE_PENDING_UPSTREAM": "ABSTAIN_OR_PROVISIONAL",
    "PROVISIONAL_UNTIL_REPLAYED": "ABSTAIN_OR_PROVISIONAL",
    "ABSTAIN": "ABSTAIN_OR_PROVISIONAL",
    "PROVISIONAL": "ABSTAIN_OR_PROVISIONAL",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"private Oracle case has invalid {field}")
    return value.strip()


class RealWorldOracleRepositoryV1:
    """Strict repository for the versioned real-world private Oracle envelope."""

    def __init__(self, path: str | Path):
        self._path = Path(path)

    def load(self, case_id: str) -> RealWorldOracleV1:
        oracle = self._load_all().get(case_id)
        if oracle is None:
            raise RealWorldOracleUnavailableError("real-world Oracle case unavailable")
        return oracle

    def case_ids(self) -> frozenset[str]:
        """Return only public case identities, never private Oracle values."""
        return frozenset(self._load_all())

    def _load_all(self) -> dict[str, RealWorldOracleV1]:
        if not self._path.is_file():
            raise RealWorldOracleUnavailableError("real-world Oracle unavailable")
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            return self._parse_envelope(payload)
        except RealWorldOracleUnavailableError:
            raise
        except Exception as exc:
            raise RealWorldOracleUnavailableError("real-world Oracle malformed") from exc

    @staticmethod
    def _parse_envelope(payload: Any) -> dict[str, RealWorldOracleV1]:
        if not isinstance(payload, dict) or not {"schema_version", "cases"}.issubset(payload):
            raise ValueError("private Oracle envelope invalid")
        if set(payload) - _ORACLE_ENVELOPE_FIELDS:
            raise ValueError("private Oracle envelope invalid")
        if payload["schema_version"] not in _SUPPORTED_ORACLE_SCHEMA_VERSIONS:
            raise ValueError("private Oracle schema unsupported")
        if not isinstance(payload["cases"], list):
            raise ValueError("private Oracle cases invalid")

        parsed: dict[str, RealWorldOracleV1] = {}
        # Both supported envelopes use the same explicit answer-bearing and
        # metadata contract; legacy terminal aliases are normalized below.
        allowed_case_fields = _REQUIRED_CASE_FIELDS | _ORACLE_CASE_OPTIONAL_FIELDS
        for value in payload["cases"]:
            if (
                not isinstance(value, dict)
                or not _REQUIRED_CASE_FIELDS.issubset(value)
                or (allowed_case_fields is not None and set(value) - allowed_case_fields)
            ):
                raise ValueError("private Oracle case fields invalid")
            case_id = _required_text(value["case_id"], "case_id")
            if len(case_id) > 128 or case_id in parsed:
                raise ValueError("private Oracle case identity invalid")
            locations = value["required_locations"]
            if (
                not isinstance(locations, list)
                or not locations
                or any(not isinstance(item, str) or not item.strip() for item in locations)
            ):
                raise ValueError("private Oracle case has invalid required_locations")
            expected_terminal = value["expected_terminal"]
            if payload["schema_version"] == "1.0":
                if not isinstance(expected_terminal, str) or not expected_terminal.strip():
                    raise ValueError("private Oracle case has invalid expected_terminal")
                legacy_terminal = expected_terminal.strip().upper()
                expected_terminal = _LEGACY_TERMINAL_ALIASES.get(legacy_terminal)
            if expected_terminal not in _EXPECTED_TERMINALS:
                raise ValueError("private Oracle case has invalid expected_terminal")
            parsed[case_id] = RealWorldOracleV1(
                case_id=case_id,
                root_cause_id=_required_text(value["root_cause_id"], "root_cause_id"),
                expected_summary=_required_text(value["expected_summary"], "expected_summary"),
                counterfactual=_required_text(value["counterfactual"], "counterfactual"),
                required_locations=tuple(item.strip() for item in locations),
                expected_terminal=expected_terminal,
            )
        if not parsed:
            raise ValueError("private Oracle cases empty")
        return parsed


class RealWorldOracleScorerV1:
    """Score admitted full upstream replays without revealing Oracle values."""

    def __init__(self, repository: RealWorldOracleRepositoryV1, commitment_key: bytes):
        if len(commitment_key) < 32:
            raise ValueError("real-world scorer commitment key must be at least 32 bytes")
        self._repository = repository
        self._key = bytes(commitment_key)
        self._scorer_id = "real-world-oracle-v1"

    def score(
        self,
        run: dict[str, Any],
        *,
        minimum_repetitions: int,
        evidence_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> PublicRealWorldScore:
        run_id = run.get("run_id")
        case_id = run.get("case_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("scored run requires run_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError("scored run requires case_id")

        oracle = self._repository.load(case_id)
        if evidence_by_id is None:
            evidence = run.get("evidence")
            evidence_by_id = {
                item["evidence_id"]: item
                for item in evidence
                if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
            } if isinstance(evidence, list) else {}
        admission_valid = True
        try:
            validate_formal_admission(
                run,
                case_id=case_id,
                minimum_repetitions=minimum_repetitions,
                evidence_by_id=evidence_by_id,
            )
        except (TypeError, ValueError):
            admission_valid = False

        should_abstain = oracle.expected_terminal == "ABSTAIN_OR_PROVISIONAL"
        predicted_locations = run.get("predicted_locations")
        location_match = False
        if isinstance(predicted_locations, list):
            expected = {self._location_key(value) for value in oracle.required_locations}
            location_match = any(
                isinstance(value, str) and self._location_key(value) in expected
                for value in predicted_locations
            )
        evidence_refs = {
            ref for ref in run.get("evidence_refs", [])
            if isinstance(ref, str) and ref in evidence_by_id
        }
        counter_evidence_refs = {
            ref for ref in run.get("counter_evidence_refs", [])
            if isinstance(ref, str) and ref in evidence_by_id
        }
        gates = {
            "formal_admission": admission_valid,
            "terminal_expectation": bool(run.get("abstained")) == should_abstain,
            "root_cause": run.get("predicted_root_cause_id") == oracle.root_cause_id,
            "source_location": location_match,
            "evidence_cited": bool(evidence_refs),
            "counter_evidence_cited": bool(counter_evidence_refs),
        }
        if not admission_valid:
            verdict = "HARNESS_INVALID"
        elif all(gates.values()):
            verdict = "PASS"
        else:
            verdict = "FAIL"

        private_projection = {
            "case_id": oracle.case_id,
            "root_cause_id": oracle.root_cause_id,
            "expected_summary": oracle.expected_summary,
            "counterfactual": oracle.counterfactual,
            "required_locations": oracle.required_locations,
            "expected_terminal": oracle.expected_terminal,
        }
        public_contract = {
            "schema_version": SCORE_SCHEMA_VERSION,
            "scorer_id": self._scorer_id,
            "run_id": run_id,
            "verdict": verdict,
            "gates": gates,
        }
        return PublicRealWorldScore(
            scorer_id=self._scorer_id,
            opaque_case_token="case:" + self._hmac(case_id.encode("utf-8")),
            run_id=run_id,
            verdict=verdict,
            gates=gates,
            oracle_commitment="hmac-sha256:" + self._hmac(_canonical_bytes(private_projection)),
            run_digest=_sha256(run),
            public_contract_digest=_sha256(public_contract),
        )

    def _hmac(self, value: bytes) -> str:
        return hmac.new(self._key, value, hashlib.sha256).hexdigest()

    @staticmethod
    def _location_key(value: str) -> str:
        return value.strip().replace("\\", "/").rstrip("/").lower()
