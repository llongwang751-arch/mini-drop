"""Conservative request intent: describing information never verifies health.

This policy recognizes a small, explicit informational request class. Anything
ambiguous stays on the existing diagnosis path; user text grants no authority.
The returned metadata is not part of the model's shared planning-output DTO.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import re
from typing import Any

from .planning_output import PlanningOutput, validate_planning_output

PLANNING_REQUEST_POLICY = "mini-drop.planning-request-intent.v1"
PLANNING_CLAIM_SCOPE = "PLANNING_ONLY_NOT_HEALTH_OR_CAUSATION"

_DESCRIPTION = re.compile(
    r"(?:只|仅|仅仅)(?:想|需|需要|要|请)?(?:描述|说明|解释|介绍|阅读|查看|汇总|总结)"
    r"|\b(?:only|just|simply)\s+(?:describe|explain|summari[sz]e|read|review)\b", re.I)
_BOUNDED_INFORMATION = re.compile(
    r"信息|描述|范围|覆盖|已有|已知|已提供|现有|上下文|配置|流程"
    r"|\b(?:information|description|scope|coverage|provided|available|existing|context|configuration|workflow)\b", re.I)
_NO_OBSERVATION_ACTION = re.compile(
    r"(?:不|不要|无需|禁止)(?:做|进行|启动|发起|要求)?(?:新的?|当前|任何|实际)?"
    r"(?:状态检查|健康检查|检查|测量|诊断|取证|采集|观测|探针)"
    r"|(?:不|不要|禁止)(?:提出|生成|创建)[^，,。.;；\n]{0,20}(?:采集动作|探针|采集)"
    r"|\b(?:do\s+not|don't|without|no)\s+(?:new\s+|current\s+|actual\s+|any\s+)?"
    r"(?:check(?:ing|s)?|health\s+check(?:s)?|collect(?:ing|ion)?|observations?|measurements?|probes?)\b", re.I)
_HEALTH_REQUEST = re.compile(
    r"(?:是否|是不是|有无|有没有|能否)[^，,。.;；\n]{0,14}(?:正常|健康|异常|问题|故障)"
    r"|(?:判断|确认|验证|检查|检测|诊断|排查|证明|保证|评估|认定|宣布|标记|声称|给出)[^，,。.;；\n]{0,14}(?:正常|健康|异常|问题|故障)"
    r"|\b(?:is|are|whether|confirm|verify|assess|check|determine|prove|guarantee)\b"
    r"[^,.;\n]{0,40}\b(?:healthy|normal|anomal\w*|fault\w*|problems?)\b", re.I)
_SYMPTOM = re.compile(
    r"异常|故障|问题|症状|报警|告警|报错|错误|宕机|崩溃|卡死|卡顿|变慢|慢请求|超时|持续升高|资源耗尽|泄漏|丢包|失败|不正常|不健康"
    r"|(?:CPU|延迟|内存|磁盘|负载)[^，,。.;；\n]{0,8}(?:过高|很高|升高|飙升|阻塞)"
    r"|\b(?:anomal\w*|fault\w*|errors?|crash\w*|slow(?:down)?|latency\s+spike\w*|"
    r"timeouts?|timed\s+out|leaks?|packet\s+loss|symptoms?|not\s+(?:healthy|normal)|fail(?:ed|ure|ing)?)\b", re.I)
_NEGATION = re.compile(
    r"(?:没有|未见|未观察到|没有观察到|未发现|并无|无|不|不要|无需|禁止)"
    r"(?:任何|明显|当前|已知|新的|提出|生成|创建|声称|确认|判断|要求|继续|再|做|进行|观察到|发现|"
    r"报告|实际|全局|业务|全部|可能|发生|存在|呈现|记录到|检查|诊断|启动|考虑|性能|异常|执行|运行|修改|删除|重启|修复|绕过|允许|采集|工具|调用)*\s*$"
    r"|\b(?:no|not|without|never|don't|do\s+not)\b"
    r"(?:\s+(?:known|reported|observed|current|new|actual|clear|performance|claim|confirm|verify|assess|check|any))*\s*$", re.I)
_UNSAFE_ACTION = re.compile(
    r"执行|运行命令|下发命令|修改|删除|重启|修复|绕过|越权|提权|忽略权限|shell|sudo"
    r"|\b(?:execute|run\s+(?:a\s+)?command|delete|restart|repair|bypass|escalate)\b", re.I)
_ACTION_TOPIC_SUFFIX = re.compile(
    r"^\s*(?:流程|方法|步骤|机制|概念|策略|含义|workflow\b|procedure\b|process\b|method\b|concept\b|policy\b)", re.I)
_ACTION_DIRECTIVE = re.compile(r"然后|立即|立刻|直接|帮我|必须|要求|请|\b(?:then|please|immediately|must)\b", re.I)
_COLLECTION_FAILURE = re.compile(
    r"采集失败|工具失败|权限不足|无法附加|连接失败|Agent\s*离线|采集超时|工具超时"
    r"|\b(?:collector|collection|tool)\s+(?:failed|failure|timeout|timed\s+out)\b|agent\s+offline", re.I)
_CONDITIONAL = re.compile(r"如果|假如|例如|示例|假设|假定|\b(?:if|suppose|example)\b", re.I)
_NUMERIC_PERFORMANCE_CLAIM = re.compile(
    r"(?:CPU|RSS|内存|延迟|吞吐|错误率|丢包率|磁盘|I/O|latency|memory|throughput|error\s+rate|p(?:50|90|95|99))"
    r"[^，,。.;；\n]{0,24}\d+(?:\.\d+)?\s*(?:%|％|ms|us|µs|秒|毫秒|MiB|GiB|MB|GB|rps|ops/s|bytes/s)", re.I)
_DOUBLE_NEGATION = re.compile(
    r"(?:不是|并非|并不是|不能说|不代表|无法保证|无法确认|不敢说|不能保证|不是说|并不)"
    r"[^，,。.;；\n]{0,12}(?:没有|无|未|不)"
    r"|\b(?:not\s+(?:without|no|not)|cannot\s+say\s+(?:there\s+(?:is|are)\s+)?no)\b", re.I)
_CLOSED_HYPOTHESES = frozenset({"REFUTED", "REJECTED", "RESOLVED", "CLOSED", "CANCELLED"})


def _affirmed(text: str, pattern: re.Pattern[str]) -> bool:
    # Negation is local to a clause; "no data, but CPU is high" is still a symptom.
    for clause in re.split(r"[，,。.;；\n]|但是|但|\bbut\b|\bhowever\b", text, flags=re.I):
        for match in pattern.finditer(clause):
            prefix = clause[max(0, match.start() - 32):match.start()]
            if _DOUBLE_NEGATION.search(prefix) or not _NEGATION.search(prefix):
                return True
    return False


def _action_requested(text: str) -> bool:
    """Separate a described workflow topic from an action requested in a clause."""
    for clause in re.split(r"[，,。.;；\n]|但是|但|\bbut\b|\bhowever\b", text, flags=re.I):
        for action in _UNSAFE_ACTION.finditer(clause):
            prefix = clause[max(0, action.start() - 32):action.start()]
            if _NEGATION.search(prefix) and not _DOUBLE_NEGATION.search(prefix):
                continue
            descriptions = [match for match in _DESCRIPTION.finditer(clause[:action.start()])
                            if _affirmed(clause[:action.start()], _DESCRIPTION)]
            is_described_topic = bool(
                descriptions and _ACTION_TOPIC_SUFFIX.match(clause[action.end():])
                and not _ACTION_DIRECTIVE.search(clause[descriptions[-1].end():action.start()]))
            if not is_described_topic:
                return True
    return False


def planning_request_intent(
    query: str, *, user_correction: str = "",
    prior_hypotheses: Iterable[Mapping[str, Any]] = (),
    evidence_summary: Iterable[Mapping[str, Any]] = (),
    investigation_memory: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe public request intent, never observations, authorization or health.

    Recognizing NORMAL requires two explicit request features and no blocker.
    Missing identity/sample data are not blockers for describing provided text.
    They remain blockers when the request asks for a health/incident judgment.
    """
    text = "\n".join(str(value) for value in (query, user_correction) if value)
    matched, blockers = [], []
    if _affirmed(text, _DESCRIPTION) and _BOUNDED_INFORMATION.search(text):
        matched.append("EXPLICIT_BOUNDED_DESCRIPTION")
    if _affirmed(text, _NO_OBSERVATION_ACTION):
        matched.append("EXPLICIT_NO_OBSERVATION_ACTION")
    health_requested = _affirmed(text, _HEALTH_REQUEST)
    if health_requested:
        blockers.append("HEALTH_OR_INCIDENT_JUDGMENT_REQUESTED")
    if _affirmed(text, _SYMPTOM):
        blockers.append("REPORTED_SYMPTOM")
    if _action_requested(text):
        blockers.append("ACTION_OR_AUTHORITY_REQUEST")
    if _affirmed(text, _COLLECTION_FAILURE):
        blockers.append("COLLECTION_FAILURE_NOT_HEALTH_EVIDENCE")
    if _affirmed(text, _CONDITIONAL):
        blockers.append("CONDITIONAL_OR_HYPOTHETICAL_REQUEST")
    if _NUMERIC_PERFORMANCE_CLAIM.search(text):
        blockers.append("USER_NUMERIC_PERFORMANCE_CLAIM")
    if _DOUBLE_NEGATION.search(text):
        blockers.append("DOUBLE_NEGATION_CANNOT_ESTABLISH_ABSENCE")
    if len(text) > 12000:
        blockers.append("REQUEST_TOO_LARGE_FOR_INTENT_POLICY")
    if any(str(row.get("status") or "UNKNOWN").upper() not in _CLOSED_HYPOTHESES
           for row in prior_hypotheses):
        blockers.append("UNRESOLVED_PRIOR_HYPOTHESIS")
    if tuple(evidence_summary):
        blockers.append("EXISTING_INVESTIGATION_OBSERVATIONS")
    memory = investigation_memory or {}
    if memory.get("status") == "UNAVAILABLE":
        blockers.append("INVESTIGATION_MEMORY_UNAVAILABLE")
    if (memory.get("latest_report_id") or memory.get("observations")
            or memory.get("verification") or memory.get("omitted_evidence_count")):
        blockers.append("EXISTING_INVESTIGATION_MEMORY")
    information_only = len(matched) == 2 and not blockers
    return {
        "policy_version": PLANNING_REQUEST_POLICY,
        "intent": ("INFORMATION_ONLY" if information_only else
                   "HEALTH_ASSESSMENT" if health_requested else "DIAGNOSTIC_OR_UNSPECIFIED"),
        "required_disposition": "NORMAL" if information_only else None,
        "request_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "matched_rules": matched, "blocking_reasons": blockers,
        "claim_scope": PLANNING_CLAIM_SCOPE, "is_evidence": False,
        "health_check_performed": False, "causal_root_cause_verified": False,
    }


def informational_planning_output(
    query: str, *, target: Mapping[str, Any] | None = None,
    response_language: str = "zh-CN", **request_context: Any,
) -> dict[str, Any] | None:
    """Deterministically close explicit description-only requests before a provider.

    Nothing is inferred about the target. A bound identity is only identity;
    absence of samples is an explicit limitation, not a request to measure it.
    """
    intent = planning_request_intent(query, **request_context)
    if intent["intent"] != "INFORMATION_ONLY":
        return None
    identities = [str(key) for key in ("service", "environment", "process", "runtime")
                  if (target or {}).get(key)]
    if response_language == "en-US":
        summary = "This turn only describes provided information and its scope; no current-state check was performed."
        limitations = ["Descriptive planning scope only; target identity is not a health measurement.",
                       "No sampling, incident verification, business-health or causal conclusion is supplied."]
        if identities:
            limitations.append("Provided identity fields: " + ", ".join(identities) + "; values are not revalidated.")
    else:
        summary = "本轮仅描述已提供信息及其覆盖范围；没有进行当前状态检查，不产生健康或根因结论。"
        limitations = ["仅限已有描述和规划范围；目标身份不等于健康测量。",
                       "没有进行采样或异常验证，不确认业务正常或因果根因。"]
        if identities:
            limitations.append("已有身份字段：" + "、".join(identities) + "；字段内容未经本次重新验证。")
    proposal = validate_planning_output({
        "disposition": "NORMAL", "reasoning_summary": summary,
        "tool_name": None, "hypotheses": [], "missing_evidence": [],
        "limitations": limitations, "causal_root_cause_verified": False,
    }, [])
    return {
        **proposal, "planner_kind": "SERVER_REQUEST_INTENT", "planner_version": PLANNING_REQUEST_POLICY,
        "model_invocations": 0, "planning_request_intent": intent,
        "claim_scope": PLANNING_CLAIM_SCOPE, "is_evidence": False,
        "health_check_performed": False, "new_tool_requested": False,
        "agent_framework": "server-request-intent-policy", "checkpoint_backend": "NOT_USED",
    }


def validate_request_disposition(
    payload: dict[str, Any] | PlanningOutput, allowed_tools: Iterable[str] | None,
    *, query: str, **request_context: Any,
) -> dict[str, Any]:
    """Keep an unanswered health/incident request from being labelled NORMAL.

    The shared DTO is unchanged. This contextual gate only rejects a planning
    NORMAL when the request needs a health/incident answer and has no current
    observations. INVESTIGATE still passes the original numeric/tool gates;
    INSUFFICIENT_EVIDENCE and REFUSED remain legal stopping outcomes.
    """
    output = validate_planning_output(payload, allowed_tools)
    intent = planning_request_intent(query, **request_context)
    if intent["intent"] == "INFORMATION_ONLY" and output["disposition"] != "NORMAL":
        raise ValueError("本次只是描述已有信息和范围，不要求健康判断或采集；应使用仅规划描述范围的 NORMAL。")
    unresolved = {"REPORTED_SYMPTOM", "COLLECTION_FAILURE_NOT_HEALTH_EVIDENCE",
                  "UNRESOLVED_PRIOR_HYPOTHESIS", "ACTION_OR_AUTHORITY_REQUEST",
                  "DOUBLE_NEGATION_CANNOT_ESTABLISH_ABSENCE"}
    unavailable_description = ("INVESTIGATION_MEMORY_UNAVAILABLE" in intent["blocking_reasons"]
                               and len(intent["matched_rules"]) == 2)
    if output["disposition"] == "NORMAL" and (
            unresolved.intersection(intent["blocking_reasons"])
            or "HEALTH_OR_INCIDENT_JUDGMENT_REQUESTED" in intent["blocking_reasons"] or unavailable_description):
        memory = request_context.get("investigation_memory") or {}
        observations = list(request_context.get("evidence_summary") or []) + list(memory.get("observations") or [])
        has_observations = any(
            isinstance(row, Mapping) and isinstance(row.get("classification"), Mapping)
            and row["classification"].get("decision")
            in {"ACCEPT_SUPPORT", "ACCEPT_COUNTER", "ACCEPT_NEUTRAL"}
            for row in observations)
        # Current symptoms/open hypotheses are not erased by unrelated accepted rows.
        if not has_observations or unresolved.intersection(intent["blocking_reasons"]):
            raise ValueError("本次要求健康或异常判断、存在调查缺口或动作权限边界，不能以纯描述 NORMAL 收束；"
                             "无必要观测且不能采集时用 INSUFFICIENT_EVIDENCE，越权用 REFUSED，"
                             "仅有可执行安全取证动作时用 INVESTIGATE。")
    return output


def audited_planner_metadata(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Project server-only provenance, refusing an invalid or forged metadata shape.

    Model outputs are parsed by PlanningOutput(extra='forbid') before callers can
    attach metadata. This function additionally bounds the persisted server tag.
    """
    if proposal.get("planner_kind") != "SERVER_REQUEST_INTENT":
        return {}
    intent = proposal.get("planning_request_intent")
    expected_keys = {"policy_version", "intent", "required_disposition", "request_digest",
                     "matched_rules", "blocking_reasons", "claim_scope", "is_evidence",
                     "health_check_performed", "causal_root_cause_verified"}
    if (not isinstance(intent, dict) or set(intent) != expected_keys
            or intent.get("policy_version") != PLANNING_REQUEST_POLICY
            or intent.get("intent") != "INFORMATION_ONLY"
            or intent.get("required_disposition") != "NORMAL"
            or intent.get("matched_rules") != ["EXPLICIT_BOUNDED_DESCRIPTION", "EXPLICIT_NO_OBSERVATION_ACTION"]
            or intent.get("blocking_reasons") != []
            or not isinstance(intent.get("request_digest"), str)
            or re.fullmatch(r"[0-9a-f]{64}", intent["request_digest"]) is None
            or proposal.get("planner_version") != PLANNING_REQUEST_POLICY
            or type(proposal.get("model_invocations")) is not int
            or proposal.get("model_invocations") != 0
            or any(source.get("claim_scope") != PLANNING_CLAIM_SCOPE for source in (proposal, intent))
            or any(source.get(key) is not False for source in (proposal, intent)
                   for key in ("is_evidence", "health_check_performed", "causal_root_cause_verified"))
            or proposal.get("new_tool_requested") is not False):
        raise ValueError("invalid server request-intent provenance")
    output = validate_planning_output(
        {key: value for key, value in proposal.items() if key in PlanningOutput.model_fields}, [])
    if output["disposition"] != "NORMAL":
        raise ValueError("server information-only policy cannot assert a different disposition")
    return {"planner_kind": "SERVER_REQUEST_INTENT", "planner_version": PLANNING_REQUEST_POLICY,
            "model_invocations": 0, "planning_request_intent": dict(intent)}
