"""Auditable Language Agent Tree Search primitives for Drop Insight.

The original LATS algorithm assumes that an environment can be revisited or
reset between sibling rollouts.  A live production incident does not have that
property: clocks advance, load changes and a profiling probe itself consumes a
small amount of resources.  This module therefore exposes the execution
semantics as data instead of silently claiming strict, reversible MCTS:

* ``REPLAY`` may use ``FULL_LATS`` only with a proven frozen-observation
  provider; the mode string alone is insufficient.
* a controlled reproduction may opt in to ``FULL_LATS`` only when the caller
  proves that it resets the environment between rollouts;
* every other live mode uses ``BUDGETED_LATS`` with progressive widening,
  cached observations and one real-tool step per selected branch.

The functions in this file are deliberately pure.  The domain service remains
the authority for opaque Agent/PID bindings, the tool allowlist, resource
budgets and the Evidence Gate.  In particular, no function here can execute a
shell command or dispatch a collector directly.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4


LATS_ALGORITHM = "LATS-UCT"
LATS_VERSION = "drop-insight-lats-v1"
LATS_EVENT_PREFIX = "lats."


@dataclass(frozen=True)
class LATSConfig:
    """Bounded search configuration derived from the diagnosis budget."""

    top_k: int = 3
    exploration_constant: float = math.sqrt(2.0)
    max_iterations: int = 6
    max_tool_calls: int = 12
    selection_policy: str = "UCT"
    value_lambda: float = 0.5

    @classmethod
    def from_budget(cls, budget: Mapping[str, Any] | None) -> "LATSConfig":
        raw = dict(budget or {})
        requested_iterations = raw.get("max_lats_iterations")
        if requested_iterations is None:
            requested_iterations = raw.get("max_diagnosis_rounds", 6)
        max_iterations = _bounded_int(
            requested_iterations,
            default=6,
            lower=1,
            upper=64,
        )
        selection_policy = str(raw.get("lats_selection_policy") or "UCT").upper()
        if selection_policy not in {"UCT", "PUCT"}:
            selection_policy = "UCT"
        return cls(
            top_k=_bounded_int(raw.get("lats_top_k", 3), default=3, lower=1, upper=8),
            exploration_constant=_bounded_float(
                raw.get("lats_exploration_constant", math.sqrt(2.0)),
                default=math.sqrt(2.0),
                lower=0.0,
                upper=8.0,
            ),
            max_iterations=max_iterations,
            max_tool_calls=_bounded_int(
                raw.get("max_tool_calls", 12), default=12, lower=1, upper=256
            ),
            selection_policy=selection_policy,
            value_lambda=_bounded_float(
                raw.get("lats_value_lambda", 0.5),
                default=0.5,
                lower=0.0,
                upper=1.0,
            ),
        )


class FrozenReplayObservationProvider:
    """Immutable observation lookup used by strict replay LATS.

    The provider owns a defensive JSON copy of one captured environment
    snapshot.  ``reset_to_snapshot`` returns a digest proof before every
    rollout and ``observe`` returns another defensive copy, so a sibling can
    neither mutate nor consume another sibling's environment state.

    Observation keys may be either a prepared ``candidate_key`` or the exact
    candidate statement.  Each observation is expected to contain the same
    Evidence-Gate inputs accepted by :func:`reward_from_outcome`; optional
    ``next_candidates`` can expose a deeper frozen branch after reflection.
    """

    def __init__(
        self,
        *,
        snapshot_id: str,
        observations: Mapping[str, Mapping[str, Any]],
    ) -> None:
        normalized_id = str(snapshot_id or "").strip()
        if not normalized_id:
            raise ValueError("frozen replay snapshot_id is required")
        try:
            owned = json.loads(
                json.dumps(dict(observations), ensure_ascii=False, sort_keys=True)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("frozen replay observations must be JSON serializable") from exc
        if not isinstance(owned, dict):
            raise ValueError("frozen replay observations must be an object")
        canonical = json.dumps(
            {"snapshot_id": normalized_id, "observations": owned},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        self._snapshot_id = normalized_id
        self._snapshot_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self._observations = owned
        self._reset_count = 0

    @property
    def snapshot_id(self) -> str:
        return self._snapshot_id

    @property
    def snapshot_digest(self) -> str:
        return self._snapshot_digest

    @property
    def reset_count(self) -> int:
        return self._reset_count

    def reset_to_snapshot(self) -> dict[str, Any]:
        """Return the immutable snapshot proof used for the next rollout."""

        self._reset_count += 1
        observed_digest = self._current_snapshot_digest()
        return {
            "snapshot_id": self._snapshot_id,
            "snapshot_digest": self._snapshot_digest,
            "observed_snapshot_digest": observed_digest,
            "reset_count": self._reset_count,
            "frozen": observed_digest == self._snapshot_digest,
        }

    def verify_snapshot(self) -> bool:
        """Check that the owned snapshot still matches its creation digest."""

        return self._current_snapshot_digest() == self._snapshot_digest

    def observe(
        self,
        candidate: Mapping[str, Any],
        *,
        iteration: int,
    ) -> dict[str, Any]:
        """Look up one deterministic observation without changing the snapshot."""

        del iteration  # The frozen answer is deliberately iteration-independent.
        keys = (
            str(candidate.get("observation_key") or ""),
            str(candidate.get("node_id") or ""),
            str(candidate.get("candidate_key") or ""),
            str(candidate.get("statement") or ""),
        )
        for key in keys:
            if key and key in self._observations:
                value = self._observations[key]
                if not isinstance(value, Mapping):
                    raise ValueError(f"frozen observation for {key!r} must be an object")
                return deepcopy(dict(value))
        raise KeyError(
            "frozen snapshot has no observation for candidate "
            f"{candidate.get('candidate_key') or candidate.get('statement')}"
        )

    def expand_after_reflection(
        self,
        candidate: Mapping[str, Any],
        *,
        observation: Mapping[str, Any],
        reflection: Mapping[str, str],
    ) -> list[dict[str, Any]]:
        """Return the frozen transition candidates with reflection as input.

        The default provider stores transitions in ``next_candidates`` inside
        the immutable observation.  Keeping this as a method makes the
        reflection-to-expansion dependency explicit and lets a future replay
        fixture validate richer precomputed transitions without granting the
        runner any live tool authority.
        """

        del candidate
        if not reflection.get("decision"):
            return []
        nested = observation.get("next_candidates") or []
        if not isinstance(nested, list):
            raise ValueError("frozen next_candidates must be an array")
        return [deepcopy(dict(item)) for item in nested if isinstance(item, Mapping)]

    def _current_snapshot_digest(self) -> str:
        canonical = json.dumps(
            {
                "snapshot_id": self._snapshot_id,
                "observations": self._observations,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def execution_semantics(
    mode: str | None,
    *,
    controlled_reset: bool = False,
    frozen_observations: bool = False,
) -> dict[str, Any]:
    """Return an honest description of what a rollout means in this session."""

    normalized = str(mode or "AUTONOMOUS").strip().upper()
    if normalized == "REPLAY" and frozen_observations:
        return {
            "execution_mode": "FULL_LATS",
            "environment_semantics": "FROZEN_REPLAY",
            "rollout_semantics": "REPLAY_SIMULATION",
            "strict_environment_reversibility": True,
            "observation_policy": "FROZEN_OBSERVATION_REUSE",
        }
    if normalized == "REPLAY":
        return {
            "execution_mode": "BUDGETED_LATS",
            "environment_semantics": "REPLAY_PROVIDER_REQUIRED",
            "rollout_semantics": "REAL_TOOL_SINGLE_STEP_NO_ROLLBACK",
            "strict_environment_reversibility": False,
            "observation_policy": "PROGRESSIVE_WIDENING_CACHED_OBSERVATIONS",
            "full_lats_blocker": (
                "diagnosis mode alone does not prove frozen observations; "
                "a replay observation provider is required"
            ),
        }
    if normalized == "REPRODUCTION" and controlled_reset:
        return {
            "execution_mode": "FULL_LATS",
            "environment_semantics": "CONTROLLED_REPRODUCTION",
            "rollout_semantics": "RESET_BETWEEN_ROLLOUTS",
            "strict_environment_reversibility": True,
            "observation_policy": "RESET_AND_REOBSERVE",
        }
    if normalized == "REPRODUCTION":
        environment = "CONTROLLED_REPRODUCTION"
        rollout = "CONTROLLED_RESET_REQUIRED"
    else:
        environment = "LIVE_PROGRESSIVE"
        rollout = "REAL_TOOL_SINGLE_STEP_NO_ROLLBACK"
    return {
        "execution_mode": "BUDGETED_LATS",
        "environment_semantics": environment,
        "rollout_semantics": rollout,
        "strict_environment_reversibility": False,
        "observation_policy": "PROGRESSIVE_WIDENING_CACHED_OBSERVATIONS",
    }


_UNKNOWN_CANDIDATE_KEY = "candidate:other-unknown"
_ROUND_PREFIX_RE = re.compile(
    r"^第\s*(?:\d+|[一二三四五六七八九十百]+)\s*轮(?:诊断|探索)?"
    r"\s*[：:、,，.。\-—]*\s*",
    re.IGNORECASE,
)


def _semantic_candidate_text(statement: str) -> str:
    """Normalize display-only round labels without erasing causal meaning."""

    normalized = unicodedata.normalize("NFKC", str(statement or "")).strip()
    normalized = _ROUND_PREFIX_RE.sub("", normalized)
    return " ".join(normalized.split()).casefold()


def _looks_like_unknown_candidate(statement: str) -> bool:
    normalized = _semantic_candidate_text(statement)
    compact = re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)
    return (
        "otherunknown" in compact
        or "其他未知" in compact
        or compact in {"未知", "未知原因", "未知根因", "其他原因", "其他根因"}
    )


def stable_candidate_key(statement: str) -> str:
    """Return a stable semantic key for cross-round candidate de-duplication.

    Round labels are presentation metadata, not part of a causal hypothesis.
    All explicit open-world aliases intentionally share one constant key so a
    model cannot grow repeated ``OTHER/UNKNOWN`` leaves by changing spelling.
    """

    normalized = _semantic_candidate_text(statement)
    if _looks_like_unknown_candidate(normalized):
        return _UNKNOWN_CANDIDATE_KEY
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"candidate:{digest[:20]}"


def prepare_candidates(
    raw_candidates: Sequence[Mapping[str, Any]],
    *,
    top_k: int = 3,
    default_tool: str | None = None,
    value_lambda: float = 0.5,
) -> list[dict[str, Any]]:
    """Deduplicate, score and retain a bounded candidate expansion.

    A caller may supply LM-produced ``prior_probability`` and
    ``estimated_value`` fields.  Missing or invalid scores fall back to a
    deterministic rank heuristic.  ``OTHER/UNKNOWN`` is kept as an explicit
    open-world sentinel in addition to the regular top-k expansion.
    """

    regular: list[dict[str, Any]] = []
    sentinels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rank, item in enumerate(raw_candidates):
        statement = " ".join(str(item.get("statement") or "").split())
        if not statement:
            continue
        is_sentinel = _is_unknown_candidate(item, statement)
        key = _UNKNOWN_CANDIDATE_KEY if is_sentinel else stable_candidate_key(statement)
        if key in seen:
            continue
        seen.add(key)
        explicit_value = _optional_unit_float(
            item.get("lm_value", item.get("estimated_value")), signed=True
        )
        explicit_prior = _optional_unit_float(item.get("prior_probability"), signed=False)
        # Self-consistency is valid only when the server computed candidate
        # frequency from independent samples.  A model may not self-report it.
        explicit_consistency = _optional_unit_float(
            item.get("server_self_consistency"), signed=True
        )
        heuristic_value = deterministic_candidate_value(
            rank=rank, is_unknown=is_sentinel
        )
        bounded_lambda = min(1.0, max(0.0, float(value_lambda)))
        if explicit_value is not None and explicit_consistency is not None:
            self_consistency = explicit_consistency
            initial_value = (
                bounded_lambda * explicit_value
                + (1.0 - bounded_lambda) * self_consistency
            )
            evaluation_source = "LM_PLUS_SELF_CONSISTENCY"
            value_formula = "lambda*LM(s)+(1-lambda)*SC(s)"
        elif explicit_value is not None:
            self_consistency = None
            initial_value = explicit_value
            evaluation_source = "LM_ONLY_SC_UNAVAILABLE"
            value_formula = "LM(s); SC(s)=null (independent samples unavailable)"
        else:
            self_consistency = None
            initial_value = heuristic_value
            evaluation_source = "DETERMINISTIC_FALLBACK"
            value_formula = "deterministic heuristic; LM(s)=SC(s)=null"
        record = {
            "candidate_key": key,
            "statement": statement,
            "expected_observations": _string_list(
                item.get("expected_observations", item.get("expected", []))
            ),
            "falsification_criteria": _string_list(
                item.get("falsification_criteria", item.get("falsification", []))
            ),
            "reason": " ".join(str(item.get("reason") or item.get("rationale") or "").split()),
            "recommended_tool": item.get("recommended_tool") or default_tool,
            "evidence_domain": item.get("evidence_domain"),
            "prior": explicit_prior,
            "initial_value": round(initial_value, 6),
            "lm_value": explicit_value,
            "self_consistency": self_consistency,
            "heuristic_value": heuristic_value,
            "value_lambda": round(bounded_lambda, 6),
            "value_formula": value_formula,
            "value_source": evaluation_source,
            "prior_source": "LM_PRIOR" if explicit_prior is not None else "DETERMINISTIC_FALLBACK",
            "rank": rank,
            "is_open_world_sentinel": is_sentinel,
        }
        (sentinels if is_sentinel else regular).append(record)

    retained = regular[: max(1, int(top_k))]
    if sentinels:
        retained.append(sentinels[0])
    if not retained:
        return []

    fallback_weights = [
        0.08 if item["is_open_world_sentinel"] else 1.0 / (1.0 + item["rank"])
        for item in retained
    ]
    raw_priors = [
        item["prior"] if item["prior"] is not None else fallback_weights[index]
        for index, item in enumerate(retained)
    ]
    total = sum(max(0.0, float(value)) for value in raw_priors)
    if total <= 0:
        raw_priors = [1.0 for _ in retained]
        total = float(len(retained))
    for item, prior in zip(retained, raw_priors, strict=True):
        item["prior"] = round(max(0.0, float(prior)) / total, 6)
    return retained


def deterministic_candidate_value(*, rank: int, is_unknown: bool = False) -> float:
    """Stable value fallback used when an LM value head is unavailable."""

    if is_unknown:
        return -0.05
    return round(max(0.1, 0.68 - 0.11 * max(0, rank)), 6)


def order_progressive_frontier(
    existing_unvisited: Sequence[Mapping[str, Any]],
    newly_expanded: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Give durable unvisited siblings the canonical UCT tie-break priority."""

    ordered = [dict(item) for item in existing_unvisited] + [
        dict(item) for item in newly_expanded
    ]
    for rank, item in enumerate(ordered):
        item["generation_rank"] = item.get("rank")
        item["rank"] = rank
    return ordered


def select_puct_candidate(
    candidates: Sequence[Mapping[str, Any]],
    node_metrics: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    parent_visits: int = 0,
    exploration_constant: float = math.sqrt(2.0),
    selection_policy: str = "UCT",
) -> dict[str, Any] | None:
    """Select one non-pruned branch using canonical UCT or explicit PUCT."""

    metrics = node_metrics or {}
    scored: list[dict[str, Any]] = []
    for fallback_rank, candidate in enumerate(candidates):
        node_id = str(candidate.get("node_id") or "")
        if not node_id:
            continue
        current = dict(metrics.get(node_id) or {})
        if current.get("pruned"):
            continue
        visits = max(0, int(current.get("visits") or 0))
        initial_value = _bounded_float(
            candidate.get("initial_value", current.get("initial_value", 0.0)),
            default=0.0,
            lower=-1.0,
            upper=1.0,
        )
        value_sum = _bounded_float(
            current.get("value_sum", 0.0), default=0.0, lower=-1e9, upper=1e9
        )
        policy = str(selection_policy or "UCT").upper()
        if policy not in {"UCT", "PUCT"}:
            policy = "UCT"
        q = value_sum / visits if visits else initial_value
        prior = _bounded_float(
            candidate.get("prior", current.get("prior", 0.0)),
            default=0.0,
            lower=0.0,
            upper=1.0,
        )
        if policy == "UCT":
            # Canonical LATS uses UCT.  An unvisited child has +infinity; use a
            # finite public score and an explicit flag so JSON remains valid.
            if visits == 0:
                exploration = 1_000_000.0
                score_q = 0.0
            else:
                exploration = float(exploration_constant) * math.sqrt(
                    math.log(max(1, int(parent_visits))) / visits
                )
                score_q = q
        else:
            exploration = (
                float(exploration_constant)
                * prior
                * math.sqrt(max(1, int(parent_visits) + 1))
                / (1 + visits)
            )
            score_q = q
        virtual_loss = _bounded_float(
            current.get("virtual_loss", 0.0), default=0.0, lower=0.0, upper=1e6
        )
        score = score_q + exploration - virtual_loss
        scored.append(
            {
                "node_id": node_id,
                "candidate_key": candidate.get("candidate_key"),
                "score": round(score, 6),
                "components": {
                    "q": round(q, 6),
                    "exploration": round(exploration, 6),
                    "prior_bonus": (
                        0.0 if policy == "UCT" else round(exploration, 6)
                    ),
                    "prior": round(prior, 6),
                    "visits": visits,
                    "virtual_loss": round(virtual_loss, 6),
                    "unvisited": visits == 0,
                },
                "rank": int(candidate.get("rank", fallback_rank)),
            }
        )
    if not scored:
        return None
    scored.sort(key=lambda row: (-row["score"], row["rank"], row["node_id"]))
    selected = scored[0]
    return {
        **selected,
        "reason": (
            (
                "UCT 在当前可执行候选中选择平均回报与探索项之和最高的分支；"
                if policy == "UCT"
                else "LATS-PUCT 扩展在当前候选中选择 Q 与先验探索奖励之和最高的分支；"
            )
            + "未访问分支优先，并列时按候选生成顺序稳定选择。"
        ),
        "selection_policy": policy,
        "scores": scored,
    }


def reward_from_outcome(
    *,
    verification_status: str | None,
    confidence: float | int | None,
    support_count: int,
    counter_count: int,
    rejected_count: int = 0,
    tool_status: str | None = None,
) -> dict[str, Any]:
    """Map Evidence-Gate output to a bounded, auditable LATS reward."""

    status = str(verification_status or "UNKNOWN").upper()
    normalized_confidence = float(confidence or 0.0)
    if normalized_confidence > 1.0:
        normalized_confidence /= 1000.0
    normalized_confidence = min(1.0, max(0.0, normalized_confidence))
    normalized_tool_status = str(tool_status or "").upper()
    if normalized_tool_status in {"DENIED", "REJECTED"}:
        # A policy or human gate rejected the action before observation.  This
        # is a negative search result, but never counter-evidence about the
        # incident hypothesis itself.
        gate = -0.45
        outcome = "POLICY_BLOCKED"
    elif normalized_tool_status in {"FAILED", "CANCELLED"}:
        gate = -0.8
        outcome = "TOOL_FAILURE"
    elif status == "VERIFIED" and support_count:
        gate = 0.9
        outcome = "VERIFIED"
    elif status == "PARTIAL_WITHOUT_COUNTER" and support_count:
        gate = 0.65
        outcome = "PARTIAL_WITHOUT_COUNTER"
    elif counter_count and not support_count:
        gate = -0.75
        outcome = "FALSIFIED"
    elif not support_count:
        gate = -0.35
        outcome = "INSUFFICIENT_EVIDENCE"
    else:
        gate = 0.15
        outcome = "INCONCLUSIVE"
    confidence_component = (normalized_confidence - 0.5) * 0.2
    quality_penalty = min(0.2, max(0, int(rejected_count)) * 0.04)
    reward = min(1.0, max(-1.0, gate + confidence_component - quality_penalty))
    return {
        "reward": round(reward, 6),
        "outcome": outcome,
        "components": {
            "evidence_gate": round(gate, 6),
            "confidence": round(confidence_component, 6),
            "quality_penalty": round(quality_penalty, 6),
            "support_count": int(support_count),
            "counter_count": int(counter_count),
            "rejected_count": int(rejected_count),
        },
    }


def reflection_from_outcome(outcome: Mapping[str, Any]) -> dict[str, str]:
    """Produce a deterministic reflection that cannot invent incident facts."""

    name = str(outcome.get("outcome") or "UNKNOWN")
    if name == "VERIFIED":
        return {
            "decision": "STOP_VERIFIED",
            "summary": "证据门已验证当前假设；保留限制项并停止搜索。",
        }
    if name == "PARTIAL_WITHOUT_COUNTER":
        return {
            "decision": "PROGRESSIVE_WIDEN",
            "summary": "已有支持证据但缺少独立交叉验证；在预算内切换证据域继续取证。",
        }
    if name == "FALSIFIED":
        return {
            "decision": "BACKTRACK",
            "summary": "可信反证推翻当前假设；回传负奖励并转向下一候选分支。",
        }
    if name == "TOOL_FAILURE":
        return {
            "decision": "SWITCH_OR_STOP",
            "summary": "真实工具未产生可用观测；不把失败当成反证，改用允许的替代探针或停止。",
        }
    if name == "POLICY_BLOCKED":
        return {
            "decision": "SWITCH_DIRECTION",
            "summary": "当前动作被策略或人工门禁拒绝；不生成事故反证，改选允许的证据域。",
        }
    return {
        "decision": "PROGRESSIVE_WIDEN",
        "summary": "当前证据区分力不足；缓存本次真实观测并在预算内扩展新的证据域。",
    }


def termination_decision(
    *,
    verification_status: str | None,
    iterations_used: int,
    tool_calls_used: int,
    config: LATSConfig,
    eligible_children: int,
) -> dict[str, Any]:
    status = str(verification_status or "").upper()
    if status == "VERIFIED":
        return {"stopped": True, "reason": "VERIFIED", "detail": "证据门已验证结论。"}
    if iterations_used >= config.max_iterations or tool_calls_used >= config.max_tool_calls:
        return {
            "stopped": True,
            "reason": "BUDGET_EXHAUSTED",
            "detail": "LATS 迭代或真实工具调用预算已经耗尽。",
        }
    if eligible_children <= 0:
        if status == "PARTIAL_WITHOUT_COUNTER":
            return {
                "stopped": True,
                "reason": "VERIFIED_WITH_LIMITATION",
                "detail": "已有支持证据，但没有可安全执行的独立证据域继续交叉验证。",
            }
        return {
            "stopped": True,
            "reason": "NO_ELIGIBLE_CHILD",
            "detail": "没有仍满足绑定、能力、策略与预算门禁的候选分支。",
        }
    if status == "PARTIAL_WITHOUT_COUNTER":
        return {
            "stopped": False,
            "reason": "CROSS_VALIDATION_REQUIRED",
            "detail": "已有支持证据，但仍可在预算内切换独立证据域交叉验证。",
        }
    return {"stopped": False, "reason": "RUNNING", "detail": "仍可在预算内继续搜索。"}


def run_frozen_replay_lats(
    initial_candidates: Sequence[Mapping[str, Any]],
    observation_provider: FrozenReplayObservationProvider,
    *,
    config: LATSConfig | None = None,
    node_namespace: str | None = None,
) -> dict[str, Any]:
    """Execute strict LATS against one immutable, resettable snapshot.

    This is a real search runner, not a mode-label shortcut.  Before each
    rollout it asks the provider to reset to the same content-addressed
    snapshot and validates the returned proof.  Candidate expansion,
    evaluation, UCT/PUCT selection, replay simulation, observation,
    reflection and reward backpropagation are emitted as the same ``lats.*``
    event shapes consumed by the live exploration-tree projection.

    The runner is intentionally detached from the live tool dispatcher.  It
    cannot execute shell commands or collectors, and using it does not change
    the semantics of ordinary ``REPLAY`` diagnosis sessions.  A Fault Plaza
    scenario can opt in by constructing a frozen provider from captured,
    immutable observations and persisting the returned events.  Callers should
    pass the diagnosis id as ``node_namespace``; otherwise a process-unique run
    namespace is generated so the same snapshot can safely power many sessions.
    """

    if not isinstance(observation_provider, FrozenReplayObservationProvider):
        raise TypeError(
            "strict frozen replay requires FrozenReplayObservationProvider"
        )
    effective = config or LATSConfig()
    namespace = str(node_namespace or "").strip() or f"run-{uuid4().hex}"
    namespace_digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
    policy = str(effective.selection_policy or "UCT").upper()
    if policy not in {"UCT", "PUCT"}:
        policy = "UCT"
    semantics = execution_semantics("REPLAY", frozen_observations=True)
    events: list[dict[str, Any]] = []
    children_by_parent: dict[str | None, list[dict[str, Any]]] = {}
    source_by_node: dict[str, dict[str, Any]] = {}
    reflection_by_node: dict[str, dict[str, str]] = {}
    expanded_parents: set[str | None] = set()
    rollout_count = 0
    stopped = False

    def append_event(event_type: str, actor: str, payload: Mapping[str, Any]) -> None:
        sequence = len(events) + 1
        events.append(
            {
                "event_id": (
                    f"frozen-replay-{namespace_digest}-event-{sequence}"
                ),
                "sequence": sequence,
                "event_type": event_type,
                "actor": actor,
                "payload": deepcopy(dict(payload)),
                "effect_key": (
                    "frozen-replay:"
                    f"{namespace_digest}:{observation_provider.snapshot_digest}:{sequence}"
                ),
            }
        )

    def replay_node_id(parent_node_id: str | None, candidate_key: str) -> str:
        digest = hashlib.sha256(
            (
                f"{namespace_digest}\0{parent_node_id or 'root'}\0{candidate_key}"
            ).encode("utf-8")
        ).hexdigest()
        return f"hypothesis:replay-{namespace_digest}-{digest[:16]}"

    def expand(
        parent_node_id: str | None,
        raw_candidates: Sequence[Mapping[str, Any]],
        *,
        iteration: int,
        phase: str,
    ) -> list[dict[str, Any]]:
        if parent_node_id in expanded_parents:
            return children_by_parent.get(parent_node_id, [])
        prepared = prepare_candidates(
            raw_candidates,
            top_k=effective.top_k,
            value_lambda=effective.value_lambda,
        )
        raw_by_key = {
            stable_candidate_key(str(item.get("statement") or "")): dict(item)
            for item in raw_candidates
            if str(item.get("statement") or "").strip()
        }
        parent_depth = -1
        if parent_node_id:
            parent_depth = int(
                source_by_node.get(parent_node_id, {}).get("depth", 0)
            )
        expanded: list[dict[str, Any]] = []
        for rank, candidate in enumerate(prepared):
            item = dict(candidate)
            item["node_id"] = replay_node_id(
                parent_node_id, str(item["candidate_key"])
            )
            item["parent_node_id"] = parent_node_id
            item["depth"] = parent_depth + 1
            item["rank"] = rank
            item["expansion_origin"] = "FROZEN_REPLAY"
            raw = raw_by_key.get(str(item["candidate_key"])) or {}
            item["observation_key"] = raw.get("observation_key")
            nested = raw.get("children") or []
            item["children"] = [
                dict(child) for child in nested if isinstance(child, Mapping)
            ]
            source_by_node[item["node_id"]] = item
            expanded.append(item)
        children_by_parent[parent_node_id] = expanded
        expanded_parents.add(parent_node_id)
        append_event(
            "lats.candidates_expanded",
            "REPLAY_AGENT",
            {
                "phase": phase,
                "iteration": iteration,
                "parent_node_id": parent_node_id,
                "candidate_count": len(expanded),
                "candidates": expanded,
                "progressive_widening": False,
                "snapshot_id": observation_provider.snapshot_id,
                "snapshot_digest": observation_provider.snapshot_digest,
                "parent_reflection": reflection_by_node.get(parent_node_id),
                "reflection_injected": parent_node_id in reflection_by_node,
            },
        )
        append_event(
            "lats.candidates_evaluated",
            "REPLAY_AGENT",
            {
                "phase": phase,
                "iteration": iteration,
                "parent_node_id": parent_node_id,
                "evaluations": [
                    {
                        "node_id": item["node_id"],
                        "initial_value": item.get("initial_value"),
                        "lm_value": item.get("lm_value"),
                        "self_consistency": item.get("self_consistency"),
                        "heuristic_value": item.get("heuristic_value"),
                        "value_lambda": item.get("value_lambda"),
                        "value_formula": item.get("value_formula"),
                        "value_source": item.get("value_source"),
                    }
                    for item in expanded
                ],
                "self_consistency_source": "SERVER_INDEPENDENT_SAMPLES_OR_NULL",
                "snapshot_id": observation_provider.snapshot_id,
                "parent_reflection": reflection_by_node.get(parent_node_id),
            },
        )
        return expanded

    def select_rollout(iteration: int) -> tuple[dict[str, Any], list[str]] | None:
        parent_node_id: str | None = None
        path: list[str] = []
        while True:
            state = replay_search_events(events)
            metrics = state.get("node_metrics") or {}
            children = children_by_parent.get(parent_node_id) or []
            if not children:
                return None
            if parent_node_id is None:
                parent_visits = sum(
                    int((metrics.get(item["node_id"]) or {}).get("visits") or 0)
                    for item in children
                )
            else:
                parent_visits = int(
                    (metrics.get(parent_node_id) or {}).get("visits") or 0
                )
            selection = select_puct_candidate(
                children,
                metrics,
                parent_visits=parent_visits,
                exploration_constant=effective.exploration_constant,
                selection_policy=policy,
            )
            if selection is None:
                return None
            node_id = str(selection["node_id"])
            path.append(node_id)
            append_event(
                "lats.node_selected",
                "REPLAY_AGENT",
                {
                    **selection,
                    "iteration": iteration,
                    "selection_depth": len(path) - 1,
                    "parent_node_id": parent_node_id,
                    "path_node_ids": list(path),
                    "selection_policy": policy,
                    "snapshot_id": observation_provider.snapshot_id,
                },
            )
            candidate = source_by_node[node_id]
            nested = candidate.get("children") or []
            if node_id not in expanded_parents and nested:
                expanded = expand(
                    node_id,
                    nested,
                    iteration=iteration,
                    phase="REPLAY_TREE_EXPANSION",
                )
                if expanded:
                    parent_node_id = node_id
                    continue
            if children_by_parent.get(node_id):
                parent_node_id = node_id
                continue
            return candidate, path

    append_event(
        "lats.search_started",
        "SYSTEM",
        {
            "algorithm": (
                "LATS-UCT" if policy == "UCT" else "LATS-PUCT-EXTENSION"
            ),
            "algorithm_version": LATS_VERSION,
            "semantics": semantics,
            "config": {
                "top_k": effective.top_k,
                "exploration_constant": effective.exploration_constant,
                "max_iterations": effective.max_iterations,
                # Frozen rollouts are simulations over cached observations,
                # never real collector/tool calls.
                "max_tool_calls": 0,
                "max_simulations": effective.max_tool_calls,
                "selection_policy": policy,
                "value_lambda": effective.value_lambda,
            },
            "snapshot_id": observation_provider.snapshot_id,
            "snapshot_digest": observation_provider.snapshot_digest,
            "node_namespace": namespace,
            "node_namespace_digest": namespace_digest,
            "frozen_provider_verified": True,
            "model_has_shell_access": False,
        },
    )
    expand(
        None,
        initial_candidates,
        iteration=0,
        phase="INITIAL_REPLAY_EXPANSION",
    )

    for iteration in range(1, effective.max_iterations + 1):
        if rollout_count >= effective.max_tool_calls:
            break
        selected = select_rollout(iteration)
        if selected is None:
            append_event(
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": "NO_ELIGIBLE_CHILD",
                    "detail": "冻结快照中没有可继续模拟的候选分支。",
                },
            )
            stopped = True
            break
        candidate, path = selected
        node_id = str(candidate["node_id"])
        proof = observation_provider.reset_to_snapshot()
        if (
            proof.get("frozen") is not True
            or proof.get("snapshot_id") != observation_provider.snapshot_id
            or proof.get("snapshot_digest") != observation_provider.snapshot_digest
        ):
            append_event(
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": "REPLAY_RESET_PROOF_FAILED",
                    "detail": "兄弟 rollout 前未能证明环境已回到同一冻结快照。",
                    "reset_proof": proof,
                },
            )
            stopped = True
            break
        append_event(
            "lats.simulation_started",
            "SYSTEM",
            {
                "node_id": node_id,
                "path_node_ids": path,
                "iteration": iteration,
                "simulation_kind": "FROZEN_REPLAY_ROLLOUT",
                "snapshot_id": observation_provider.snapshot_id,
                "snapshot_digest": observation_provider.snapshot_digest,
                "reset_proof": proof,
                "equivalent_sibling_rollback": True,
            },
        )
        try:
            observation = observation_provider.observe(
                candidate, iteration=iteration
            )
        except (KeyError, ValueError) as exc:
            append_event(
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": "REPLAY_OBSERVATION_MISSING",
                    "detail": str(exc),
                    "node_id": node_id,
                    "snapshot_id": observation_provider.snapshot_id,
                },
            )
            stopped = True
            break
        if not observation_provider.verify_snapshot():
            append_event(
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": "REPLAY_SNAPSHOT_MUTATED",
                    "detail": "冻结观察提供者在 rollout 中改变了快照摘要。",
                    "node_id": node_id,
                },
            )
            stopped = True
            break
        rollout_count += 1
        support_refs = observation.get("evidence_refs") or []
        counter_refs = observation.get("counter_evidence_refs") or []
        support_count = int(observation.get("support_count", len(support_refs)) or 0)
        counter_count = int(observation.get("counter_count", len(counter_refs)) or 0)
        rejected_count = int(observation.get("rejected_count", 0) or 0)
        verification_status = observation.get("verification_status")
        outcome = reward_from_outcome(
            verification_status=verification_status,
            confidence=observation.get("confidence"),
            support_count=support_count,
            counter_count=counter_count,
            rejected_count=rejected_count,
            tool_status=observation.get("tool_status"),
        )
        reflection = reflection_from_outcome(outcome)
        reflection_by_node[node_id] = reflection
        expansion_error: str | None = None
        try:
            next_candidates = observation_provider.expand_after_reflection(
                candidate,
                observation=observation,
                reflection=reflection,
            )
        except ValueError as exc:
            next_candidates = []
            expansion_error = str(exc)
        if node_id not in expanded_parents:
            candidate["children"] = next_candidates
        append_event(
            "lats.observation_recorded",
            "FROZEN_ENVIRONMENT",
            {
                "node_id": node_id,
                "observation_id": (
                    f"frozen:{observation_provider.snapshot_digest}:{iteration}:{node_id}"
                ),
                "observation_source": "FROZEN_REPLAY_PROVIDER",
                "summary": str(
                    observation.get("summary")
                    or (
                        f"冻结观测：支持 {support_count} 条、反证 {counter_count} 条、"
                        f"门禁={verification_status or 'UNKNOWN'}。"
                    )
                ),
                "snapshot_id": observation_provider.snapshot_id,
                "snapshot_digest": observation_provider.snapshot_digest,
                "reset_count": proof["reset_count"],
                "external_observation": False,
                "cached_observation": True,
                "evidence_refs": list(support_refs),
                "counter_evidence_refs": list(counter_refs),
                "verification_status": verification_status,
            },
        )
        append_event(
            "lats.reflection_recorded",
            "REPLAY_AGENT",
            {
                "node_id": node_id,
                "decision": reflection["decision"],
                "summary": reflection["summary"],
                "grounded_in_observation": (
                    f"frozen:{observation_provider.snapshot_digest}:{iteration}:{node_id}"
                ),
                "reflection_is_evidence": False,
                "next_candidate_count": len(candidate.get("children") or []),
                "used_for_next_expansion": bool(candidate.get("children")),
            },
        )
        append_event(
            "lats.backpropagated",
            "SYSTEM",
            {
                "node_id": node_id,
                "path_node_ids": path,
                "iteration": iteration,
                **outcome,
            },
        )
        if outcome["outcome"] == "FALSIFIED":
            append_event(
                "lats.node_pruned",
                "SYSTEM",
                {
                    "node_id": node_id,
                    "reason": "冻结快照中的可信反证推翻当前分支。",
                },
            )
        if expansion_error:
            append_event(
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": "REPLAY_EXPANSION_INVALID",
                    "detail": expansion_error,
                    "node_id": node_id,
                    "snapshot_id": observation_provider.snapshot_id,
                },
            )
            stopped = True
            break
        if outcome["outcome"] in {"VERIFIED", "PARTIAL_WITHOUT_COUNTER"}:
            append_event(
                "lats.search_terminated",
                "SYSTEM",
                {
                    "reason": (
                        "VERIFIED"
                        if outcome["outcome"] == "VERIFIED"
                        else "VERIFIED_WITH_LIMITATION"
                    ),
                    "detail": reflection["summary"],
                    "best_path_node_ids": path,
                    "snapshot_id": observation_provider.snapshot_id,
                },
            )
            stopped = True
            break

    if not stopped:
        append_event(
            "lats.search_terminated",
            "SYSTEM",
            {
                "reason": "BUDGET_EXHAUSTED",
                "detail": "冻结回放的迭代或 simulation 预算已经耗尽。",
                "snapshot_id": observation_provider.snapshot_id,
            },
        )

    projection = build_search_projection(
        mode="REPLAY",
        budget={
            "max_lats_iterations": effective.max_iterations,
            "max_tool_calls": effective.max_tool_calls,
            "lats_top_k": effective.top_k,
            "lats_selection_policy": policy,
            "lats_exploration_constant": effective.exploration_constant,
            "lats_value_lambda": effective.value_lambda,
        },
        status="COMPLETED",
        events=events,
        tool_calls_used=0,
        simulations_used=rollout_count,
    )
    return {
        "node_namespace": namespace,
        "node_namespace_digest": namespace_digest,
        "snapshot": {
            "snapshot_id": observation_provider.snapshot_id,
            "snapshot_digest": observation_provider.snapshot_digest,
            "reset_count": observation_provider.reset_count,
            "frozen": True,
        },
        "rollout_count": rollout_count,
        "events": events,
        "search": projection,
    }


def replay_search_events(events: Iterable[Any]) -> dict[str, Any]:
    """Fold durable ``lats.*`` events into one restart-safe search snapshot."""

    phase = "NOT_STARTED"
    iteration = 0
    selected_node_id: str | None = None
    latest_selection: dict[str, Any] | None = None
    termination: dict[str, Any] | None = None
    semantics: dict[str, Any] = {}
    config: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}
    candidates: dict[str, dict[str, Any]] = {}
    metrics: dict[str, dict[str, Any]] = {}
    best_path: list[str] = []
    latest_path: list[str] = []
    terminated_path: list[str] = []
    started = False

    for event in events:
        event_type, payload = _event_parts(event)
        if not event_type.startswith(LATS_EVENT_PREFIX):
            continue
        if event_type == "lats.search_started":
            started = True
            phase = "EXPANSION"
            semantics = dict(payload.get("semantics") or semantics)
            config = dict(payload.get("config") or config)
            snapshot = {
                "snapshot_id": payload.get("snapshot_id"),
                "snapshot_digest": payload.get("snapshot_digest"),
                "reset_count": 0,
                "frozen": bool(payload.get("frozen_provider_verified")),
            }
        elif event_type == "lats.candidates_expanded":
            phase = "EXPANSION"
            for item in payload.get("candidates") or []:
                node_id = str(item.get("node_id") or "")
                if not node_id:
                    continue
                record = dict(item)
                candidates[node_id] = record
                current = metrics.setdefault(node_id, _empty_metrics(record))
                current.update(
                    {
                        "prior": float(record.get("prior") or current["prior"]),
                        "initial_value": float(
                            record.get("initial_value", current["initial_value"])
                        ),
                        "value_source": record.get("value_source") or current["value_source"],
                        "prior_source": record.get("prior_source") or current["prior_source"],
                        "depth": int(record.get("depth") or current["depth"]),
                    }
                )
        elif event_type == "lats.candidates_evaluated":
            phase = "EVALUATION"
            for item in payload.get("evaluations") or []:
                node_id = str(item.get("node_id") or "")
                if not node_id:
                    continue
                current = metrics.setdefault(node_id, _empty_metrics(item))
                current["initial_value"] = float(item.get("initial_value") or 0.0)
                current["lm_value"] = item.get("lm_value")
                current["self_consistency"] = item.get("self_consistency")
                current["heuristic_value"] = item.get("heuristic_value")
                current["value_lambda"] = item.get("value_lambda")
                current["value_formula"] = item.get("value_formula")
                current["value_source"] = item.get("value_source") or "UNKNOWN"
        elif event_type == "lats.node_selected":
            phase = "SELECTION"
            iteration = max(iteration, int(payload.get("iteration") or iteration + 1))
            selected_node_id = payload.get("node_id")
            latest_selection = dict(payload)
            for metric in metrics.values():
                metric["selected"] = False
            if selected_node_id:
                current = metrics.setdefault(selected_node_id, _empty_metrics({}))
                current["selected"] = True
                current["uct_score"] = float(payload.get("score") or 0.0)
                current["selection_reason"] = payload.get("reason")
        elif event_type == "lats.action_proposed":
            phase = "ACTION_PROPOSED"
        elif event_type == "lats.awaiting_approval":
            phase = "AWAITING_APPROVAL"
        elif event_type == "lats.action_blocked":
            phase = "ACTION_BLOCKED"
        elif event_type == "lats.simulation_started":
            phase = "SIMULATION"
            proof = payload.get("reset_proof") or {}
            snapshot.update(
                {
                    "snapshot_id": payload.get("snapshot_id")
                    or snapshot.get("snapshot_id"),
                    "snapshot_digest": payload.get("snapshot_digest")
                    or snapshot.get("snapshot_digest"),
                    "reset_count": max(
                        int(snapshot.get("reset_count") or 0),
                        int(proof.get("reset_count") or 0),
                    ),
                    "frozen": bool(proof.get("frozen", snapshot.get("frozen", False))),
                }
            )
        elif event_type == "lats.action_dispatched":
            phase = "ACTION"
            node_id = payload.get("node_id")
            if node_id:
                current = metrics.setdefault(node_id, _empty_metrics({}))
                current["tool_call_id"] = payload.get("tool_call_id")
                current["task_id"] = payload.get("task_id")
        elif event_type == "lats.observation_recorded":
            phase = "OBSERVATION"
            node_id = payload.get("node_id")
            if node_id:
                current = metrics.setdefault(node_id, _empty_metrics({}))
                current["last_observation_summary"] = payload.get("summary")
                current["observation_id"] = payload.get("observation_id")
                current["observation_source"] = payload.get("observation_source")
        elif event_type == "lats.reflection_recorded":
            phase = "REFLECTION"
            node_id = payload.get("node_id")
            if node_id:
                current = metrics.setdefault(node_id, _empty_metrics({}))
                current["last_reflection"] = payload.get("summary")
                current["reflection_decision"] = payload.get("decision")
        elif event_type == "lats.backpropagated":
            phase = "BACKPROPAGATION"
            reward = float(payload.get("reward") or 0.0)
            path = [str(item) for item in payload.get("path_node_ids") or [] if item]
            if path:
                latest_path = path
            for node_id in path:
                current = metrics.setdefault(node_id, _empty_metrics({}))
                current["visits"] += 1
                current["value_sum"] = round(current["value_sum"] + reward, 6)
                current["mean_value"] = round(
                    current["value_sum"] / current["visits"], 6
                )
                current["reward"] = reward
        elif event_type == "lats.node_pruned":
            node_id = payload.get("node_id")
            if node_id:
                current = metrics.setdefault(node_id, _empty_metrics({}))
                current["pruned"] = True
                current["prune_reason"] = payload.get("reason")
        elif event_type == "lats.search_terminated":
            phase = "TERMINATED"
            termination = {
                "stopped": True,
                "reason": payload.get("reason") or "STOPPED",
                "detail": payload.get("detail") or "",
            }
            if payload.get("best_path_node_ids"):
                # The terminating branch is useful audit context, but is not
                # necessarily the best branch (for example the last rollout
                # may carry a negative reward).  Q/mean reward below remains
                # authoritative whenever backpropagation statistics exist.
                terminated_path = list(payload["best_path_node_ids"])

    visited = [
        (node_id, metric)
        for node_id, metric in metrics.items()
        if int(metric.get("visits") or 0) > 0
    ]
    if visited:
        best_node_id, _ = max(
            visited,
            key=lambda pair: (
                float(pair[1].get("mean_value") or 0.0),
                # Ancestors and their successful leaf can receive the same
                # reward.  Prefer the deeper terminal node so best_path keeps
                # the complete root-to-leaf explanation instead of truncating
                # at an arbitrary node-id tie.
                int(pair[1].get("depth") or 0),
                int(pair[1].get("visits") or 0),
                pair[0],
            ),
        )
        reverse_path = []
        current: str | None = best_node_id
        seen: set[str] = set()
        while current and current not in seen:
            seen.add(current)
            reverse_path.append(current)
            current = (candidates.get(current) or {}).get("parent_node_id")
        best_path = list(reversed(reverse_path))
    elif terminated_path:
        # Compatibility fallback for persisted sessions that terminated before
        # a backpropagation event was available.
        best_path = terminated_path
    for node_id, metric in metrics.items():
        metric["best_path"] = node_id in best_path
    return {
        "algorithm": LATS_ALGORITHM,
        "algorithm_version": LATS_VERSION,
        "phase": phase,
        "iteration": iteration,
        "selected_node_id": selected_node_id,
        "latest_selection": latest_selection,
        "best_path_node_ids": best_path,
        "latest_path_node_ids": latest_path,
        "terminated_path_node_ids": terminated_path,
        "termination": termination,
        "semantics": semantics,
        "config": config,
        "snapshot": snapshot,
        "candidate_count": len(candidates),
        "started": started,
        "candidates": candidates,
        "node_metrics": metrics,
    }


def build_search_projection(
    *,
    mode: str | None,
    budget: Mapping[str, Any] | None,
    status: str | None,
    events: Iterable[Any],
    tool_calls_used: int,
    simulations_used: int | None = None,
) -> dict[str, Any] | None:
    """Build the public tree/API search payload with conservative defaults."""

    event_rows = list(events)
    if simulations_used is None:
        simulations_used = sum(
            1
            for event in event_rows
            if _event_parts(event)[0] == "lats.simulation_started"
        )
    folded = replay_search_events(event_rows)
    # Historical diagnoses predate this search contract.  Omitting ``search``
    # is more honest than presenting zero-filled PUCT metrics that never ran.
    if not folded.get("started"):
        return None
    config = LATSConfig.from_budget(budget)
    semantics = {**execution_semantics(mode), **(folded.pop("semantics") or {})}
    persisted_config = folded.pop("config") or {}
    effective_policy = str(
        persisted_config.get("selection_policy") or config.selection_policy
    ).upper()
    if effective_policy not in {"UCT", "PUCT"}:
        effective_policy = "UCT"
    search_config = {
        "top_k": _bounded_int(
            persisted_config.get("top_k", config.top_k),
            default=config.top_k,
            lower=1,
            upper=8,
        ),
        "exploration_constant": _bounded_float(
            persisted_config.get("exploration_constant"),
            default=config.exploration_constant,
            lower=0.0,
            upper=8.0,
        ),
        "max_iterations": _bounded_int(
            persisted_config.get("max_iterations"),
            default=config.max_iterations,
            lower=1,
            upper=64,
        ),
        "max_tool_calls": _bounded_int(
            persisted_config.get("max_tool_calls"),
            default=config.max_tool_calls,
            lower=0,
            upper=256,
        ),
        "max_simulations": _bounded_int(
            persisted_config.get(
                "max_simulations", (budget or {}).get("max_simulations")
            ),
            default=max(config.max_iterations, int(simulations_used or 0), 1),
            lower=1,
            upper=256,
        ),
        "selection_policy": effective_policy,
        "value_lambda": _bounded_float(
            persisted_config.get("value_lambda"),
            default=config.value_lambda,
            lower=0.0,
            upper=1.0,
        ),
        "self_consistency_source": persisted_config.get(
            "self_consistency_source",
            "SERVER_INDEPENDENT_SAMPLES_OR_NULL",
        ),
    }
    folded["algorithm"] = (
        "LATS-UCT" if effective_policy == "UCT" else "LATS-PUCT-EXTENSION"
    )
    iterations_used = int(folded.get("iteration") or 0)
    termination = folded.get("termination")
    if termination is None:
        if iterations_used >= config.max_iterations or tool_calls_used >= config.max_tool_calls:
            termination = termination_decision(
                verification_status=None,
                iterations_used=iterations_used,
                tool_calls_used=tool_calls_used,
                config=config,
                eligible_children=max(0, folded.get("candidate_count", 0)),
            )
        elif str(status or "").upper() == "COMPLETED":
            termination = {
                "stopped": True,
                "reason": "SESSION_COMPLETED",
                "detail": "诊断会话已完成；以 Evidence Gate 报告为最终权威。",
            }
        elif folded.get("selected_node_id"):
            termination = {
                "stopped": False,
                "reason": "AWAITING_OBSERVATION",
                "detail": "已选择分支，等待真实工具观测和 Evidence Gate。",
            }
        else:
            termination = {
                "stopped": False,
                "reason": "RUNNING",
                "detail": "搜索尚未终止。",
            }
    folded["termination"] = termination
    folded["search_config"] = search_config
    folded["semantics"] = dict(semantics)
    folded.update(semantics)
    effective_max_tool_calls = int(
        persisted_config.get("max_tool_calls", config.max_tool_calls)
    )
    effective_max_simulations = int(
        persisted_config.get(
            "max_simulations",
            (budget or {}).get("max_simulations", max(config.max_iterations, 1)),
        )
    )
    folded["budget"] = {
        "max_iterations": int(persisted_config.get("max_iterations", config.max_iterations)),
        "max_tool_calls": effective_max_tool_calls,
        "max_simulations": effective_max_simulations,
        "max_diagnosis_rounds": int(
            (budget or {}).get("max_diagnosis_rounds", config.max_iterations)
        ),
        "used_iterations": iterations_used,
        "used_tool_calls": int(tool_calls_used),
        "used_simulations": int(simulations_used or 0),
        "remaining_iterations": max(0, config.max_iterations - iterations_used),
        "remaining_tool_calls": max(0, effective_max_tool_calls - int(tool_calls_used)),
        "remaining_simulations": max(
            0,
            effective_max_simulations - int(simulations_used or 0),
        ),
    }
    return folded


def hypothesis_path(
    hypothesis_id: str,
    parent_by_hypothesis: Mapping[str, str | None],
) -> list[str]:
    """Return a cycle-safe root-to-leaf list of public hypothesis node IDs."""

    current: str | None = hypothesis_id
    reverse_path: list[str] = []
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        reverse_path.append(f"hypothesis:{current}")
        current = parent_by_hypothesis.get(current)
    reverse_path.reverse()
    return reverse_path


def _empty_metrics(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "visits": 0,
        "value_sum": 0.0,
        "mean_value": 0.0,
        "prior": float(candidate.get("prior") or 0.0),
        "initial_value": float(candidate.get("initial_value") or 0.0),
        "value_source": candidate.get("value_source") or "UNKNOWN",
        "prior_source": candidate.get("prior_source") or "UNKNOWN",
        "uct_score": 0.0,
        "reward": 0.0,
        "depth": int(candidate.get("depth") or 0),
        "selected": False,
        "best_path": False,
        "pruned": False,
    }


def _event_parts(event: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(event, Mapping):
        event_type = str(event.get("event_type") or "")
        payload = event.get("payload")
        if payload is None:
            payload = event.get("payload_json")
    else:
        event_type = str(getattr(event, "event_type", "") or "")
        payload = getattr(event, "payload_json", None)
    return event_type, dict(payload or {})


def _is_unknown_candidate(item: Mapping[str, Any], statement: str) -> bool:
    if bool(item.get("is_open_world_sentinel")):
        return True
    return _looks_like_unknown_candidate(statement)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [" ".join(str(item).split()) for item in value if str(item).strip()]


def _optional_unit_float(value: Any, *, signed: bool) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    lower = -1.0 if signed else 0.0
    if not math.isfinite(parsed) or parsed < lower or parsed > 1.0:
        return None
    return parsed


def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(upper, max(lower, parsed))


def _bounded_float(
    value: Any,
    *,
    default: float,
    lower: float,
    upper: float,
) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    if not math.isfinite(parsed):
        parsed = default
    return min(upper, max(lower, parsed))
