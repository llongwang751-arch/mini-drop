from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .tools import TOOLS

Decision = Literal["ALLOW", "REQUIRE_APPROVAL", "DENY"]
RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2}
TOOL_BY_NAME = {item["name"]: item for item in TOOLS}


@dataclass(frozen=True)
class PolicyContext:
    allowed_agent_ids: frozenset[str]
    agent_capabilities: frozenset[str]
    max_risk_level: str
    used_tool_calls: int
    max_tool_calls: int


def evaluate_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    context: PolicyContext,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    tool = TOOL_BY_NAME.get(tool_name)
    if tool is None:
        return _result("DENY", [{"name": "TOOL_ALLOW_LIST", "result": "FAIL"}], "工具未注册")

    schema_errors = _validate_schema(arguments, tool["input_schema"])
    checks.append({"name": "SCHEMA", "result": "PASS" if not schema_errors else "FAIL"})
    if schema_errors:
        return _result("DENY", checks, "; ".join(schema_errors))

    agent_id = arguments.get("agent_id")
    scope_ok = not agent_id or agent_id in context.allowed_agent_ids
    checks.append({"name": "TARGET_SCOPE", "result": "PASS" if scope_ok else "FAIL"})
    if not scope_ok:
        return _result("DENY", checks, "Agent 不在本次诊断授权范围")

    required = set(tool.get("required_capabilities", []))
    capability_ok = required.issubset(context.agent_capabilities)
    checks.append({"name": "AGENT_CAPABILITY", "result": "PASS" if capability_ok else "FAIL"})
    if not capability_ok:
        return _result("DENY", checks, f"Agent 缺少能力: {sorted(required - set(context.agent_capabilities))}")

    budget_ok = context.used_tool_calls < context.max_tool_calls
    checks.append({"name": "BUDGET", "result": "PASS" if budget_ok else "FAIL"})
    if not budget_ok:
        return _result("DENY", checks, "工具调用预算已耗尽")

    risk_ok = RISK_ORDER[tool["risk_level"]] <= RISK_ORDER[context.max_risk_level]
    checks.append({"name": "RISK_LIMIT", "result": "PASS" if risk_ok else "FAIL"})
    if not risk_ok:
        return _result("DENY", checks, "工具风险高于会话预算")

    if tool.get("requires_approval", False):
        checks.append({"name": "HUMAN_APPROVAL", "result": "REQUIRE_APPROVAL"})
        return _result("REQUIRE_APPROVAL", checks, f"{tool_name} 需要人工批准")

    checks.append({"name": "HUMAN_APPROVAL", "result": "NOT_REQUIRED"})
    return _result("ALLOW", checks, "策略检查通过")


def _result(decision: Decision, checks: list[dict[str, str]], reason: str) -> dict[str, Any]:
    return {"decision": decision, "checks": checks, "reason": reason}


def _validate_schema(arguments: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    errors = []
    properties = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            errors.append(f"未知参数: {unknown}")
    for name in schema.get("required", []):
        if name not in arguments:
            errors.append(f"缺少参数: {name}")
    for name, value in arguments.items():
        spec = properties.get(name)
        if spec is None:
            continue
        expected = spec.get("type")
        if expected == "string" and not isinstance(value, str):
            errors.append(f"{name} 必须是字符串")
        elif expected == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
            errors.append(f"{name} 必须是整数")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                errors.append(f"{name} 小于最小值")
            if "maximum" in spec and value > spec["maximum"]:
                errors.append(f"{name} 超过最大值")
        if isinstance(value, str) and len(value) < spec.get("minLength", 0):
            errors.append(f"{name} 长度不足")
    return errors

