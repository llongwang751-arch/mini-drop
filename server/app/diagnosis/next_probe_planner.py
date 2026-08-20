"""受约束的下一步取证规划器。

模型只能在服务端注册且当前可用的探针中做选择。模型不可生成命令，
不可扩大目标范围，也不可越过预算和审批策略。模型不可用时返回 None，
由编排器继续使用确定性策略。
"""

from __future__ import annotations

import json
from typing import Any

from server.app.ai_provider import chat_completions, get_ai_settings, is_feature_enabled


SYSTEM_PROMPT = """你是性能诊断的下一步取证规划器。你的任务不是直接给根因，
而是从服务端给出的白名单探针和目标中，选择最能支持或推翻当前假设的一次取证。
只能调用 emit_next_probe；禁止输出命令、扩大作用域、绕过审批或虚构探针。
reason 必须是可展示给用户的简短依据，不得输出隐藏思维过程。"""


def propose_next_probe(
    *,
    query: str,
    symptom: str,
    hypotheses: list[dict[str, Any]],
    evidence_summary: list[dict[str, Any]],
    missing_evidence: list[str],
    allowed_probes: list[dict[str, Any]],
    allowed_targets: list[dict[str, Any]],
    attempted_probe_ids: list[str],
    route_priors: dict[str, float] | None = None,
    round_index: int,
) -> dict[str, Any] | None:
    """让模型在严格白名单内选择下一次取证；失败时安静回退。"""

    if not is_feature_enabled("rca") or not allowed_probes or not allowed_targets:
        return None
    probe_ids = [str(item["probe_id"]) for item in allowed_probes]
    target_ids = [str(item["instance_id"]) for item in allowed_targets]
    function = {
        "name": "emit_next_probe",
        "description": "从白名单选择一次最有区分力的下一步取证",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "probe_id": {"type": "string", "enum": probe_ids},
                "target_instance_id": {"type": "string", "enum": target_ids},
                "evidence_purpose": {"type": "string", "enum": ["VERIFY", "SUPPORT", "FALSIFY"]},
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                "expected_observation": {"type": "string", "minLength": 1, "maxLength": 500},
                "falsification_criterion": {"type": "string", "minLength": 1, "maxLength": 500},
            },
            "required": [
                "probe_id", "target_instance_id", "evidence_purpose", "reason",
                "expected_observation", "falsification_criterion",
            ],
        },
    }
    settings = get_ai_settings()
    if settings.provider.lower() == "openai":
        function["strict"] = True
    context = {
        "symptom": symptom,
        "round_index": round_index,
        "hypotheses": hypotheses,
        "evidence_summary": evidence_summary,
        "missing_evidence": missing_evidence,
        "allowed_probes": allowed_probes,
        "allowed_targets": allowed_targets,
        "attempted_probe_ids": attempted_probe_ids,
        "historical_route_success_priors": route_priors or {},
    }
    try:
        response = chat_completions({
            "model": settings.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "<trusted_context>\n" + json.dumps(context, ensure_ascii=False)
                        + "\n</trusted_context>\n<untrusted_problem>\n"
                        + query + "\n</untrusted_problem>"
                    ),
                },
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0.1,
            "max_tokens": 700,
            "tools": [{"type": "function", "function": function}],
            "tool_choice": {"type": "function", "function": {"name": "emit_next_probe"}},
        }, timeout=30)
        if response.status_code != 200:
            return None
        calls = response.json().get("choices", [{}])[0].get("message", {}).get("tool_calls") or []
        if not calls:
            return None
        raw = calls[0].get("function", {}).get("arguments", "{}")
        result = json.loads(raw) if isinstance(raw, str) else raw
        if result.get("probe_id") not in probe_ids:
            return None
        if result.get("target_instance_id") not in target_ids:
            return None
        if result.get("evidence_purpose") not in {"VERIFY", "SUPPORT", "FALSIFY"}:
            return None
        if not str(result.get("reason", "")).strip():
            return None
        return result
    except Exception:
        return None
