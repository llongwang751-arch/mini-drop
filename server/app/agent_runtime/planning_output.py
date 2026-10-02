"""Shared proposal contract; a planner disposition never establishes health or cause."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PLANNING_OUTPUT_SCHEMA = "mini-drop.planning-output.v2"
PLANNING_OUTPUT_REQUIREMENT = (
    "先根据本次描述、可信事实和能力边界选择四态；规则基线、Skill 和知识只是条件先验，不能凭空制造异常。"
    "假设、数值判据、候选扩展和切换证据域仅适用于 INVESTIGATE。非法计划被门禁拒绝不是新的业务异常。"
    "合法非调查结果直接提交并停止知识查询和探针请求；用户声称正常不构成健康证据。"
    "先区分请求意图：明确只描述已有信息/覆盖范围且不检查或采集、不要求健康判断、无当前异常与未解决调查时，"
    "这是 INFORMATION_ONLY，应为 NORMAL；缺少采样不妨碍描述，limitations 必须写明未做当前状态检查。"
    "若是在问是否正常、有没有问题、确认健康或未知症状，即使用户声称正常或禁止采集，也不是纯描述；"
    "缺少必要观测且不能取证时必须 INSUFFICIENT_EVIDENCE，有允许且可执行的异常取证才 INVESTIGATE。"
    "planning_request_intent 是服务器公共意图策略投影，只限定描述范围，不是证据或工具授权。"
    "输出合同 mini-drop.planning-output.v2：disposition 只能为 INVESTIGATE、NORMAL、"
    "INSUFFICIENT_EVIDENCE、REFUSED。有明确待验证异常且存在可执行的取证动作时选择 INVESTIGATE，"
    "tool_name 必须属于 allowed_tools，hypotheses 含 1 至 3 条可证伪假设，每条必须有非空支持与证伪条件。"
    "描述或有界观察未提出待验证异常时选择 NORMAL，并在 limitations 写明描述/规划范围；"
    "这只是规划判断，尚未做当前状态检查，不能声明平台健康或业务全部正常。"
    "目标、时间窗口或必要观测缺失且当前无法提出可执行取证计划时选择 INSUFFICIENT_EVIDENCE，"
    "在 missing_evidence 列明缺口。要求越权、执行命令、修复或超出平台能力时选择 REFUSED，"
    "在 limitations 说明拒绝的权限/能力范围。后三种结果必须 tool_name=null、hypotheses=[]，"
    "不为凑结构虚构异常、条件或探针。正常与缺测不等于拒绝；能安全取得缺失观测时仍可 INVESTIGATE。"
    "所有结果 causal_root_cause_verified 必须 false。知识命中不是本次证据，缺失不能补零。"
)

COLLECTION_FAILURE_MARKERS = (
    "采集失败", "工具失败", "权限不足", "无法附加", "连接失败", "agent 离线",
    "agent offline", "采集超时", "工具超时", "collector timeout", "collection timeout",
    "collection timed out", "tool timeout", "tool timed out",
)


class PlanningHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=3, max_length=1000)
    expected_observations: list[str] = Field(min_length=1, max_length=8)
    falsification_criteria: list[str] = Field(min_length=1, max_length=8)
    rationale: str = Field(min_length=3, max_length=1000)
    prior_probability: float | None = Field(default=None, ge=0.0, le=1.0)
    estimated_value: float | None = Field(default=None, ge=-1.0, le=1.0)

    @field_validator("statement", "rationale", mode="before")
    @classmethod
    def strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("expected_observations", "falsification_criteria")
    @classmethod
    def clean_criteria(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if not cleaned or any(not value for value in cleaned):
            raise ValueError("every criterion must be non-empty")
        return cleaned


class PlanningOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["mini-drop.planning-output.v2"] = PLANNING_OUTPUT_SCHEMA
    disposition: Literal["INVESTIGATE", "NORMAL", "INSUFFICIENT_EVIDENCE", "REFUSED"] = "INVESTIGATE"
    reasoning_summary: str = Field(min_length=3, max_length=1200)
    tool_name: str | None = Field(default=None, min_length=3, max_length=128)
    hypotheses: list[PlanningHypothesis] = Field(default_factory=list, max_length=3)
    missing_evidence: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)
    causal_root_cause_verified: Literal[False] = False

    @field_validator("reasoning_summary", mode="before")
    @classmethod
    def strip_summary(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("missing_evidence", "limitations")
    @classmethod
    def clean_explanations(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 1000 for value in cleaned):
            raise ValueError("explanations must be bounded non-empty text")
        return cleaned

    @field_validator("causal_root_cause_verified", mode="before")
    @classmethod
    def forbid_causal_claim(cls, value: Any) -> bool:
        if value is not False:
            raise ValueError("a planning proposal cannot establish causal verification")
        return False

    @model_validator(mode="after")
    def validate_branch(self) -> "PlanningOutput":
        if self.disposition == "INVESTIGATE":
            if not self.tool_name or not self.hypotheses:
                raise ValueError("INVESTIGATE requires a tool and falsifiable hypotheses")
        elif self.tool_name is not None or self.hypotheses:
            raise ValueError("non-investigation results cannot propose tools or hypotheses")
        if self.disposition == "INSUFFICIENT_EVIDENCE" and not self.missing_evidence:
            raise ValueError("INSUFFICIENT_EVIDENCE must explain missing observations")
        if self.disposition in {"NORMAL", "REFUSED"} and not self.limitations:
            raise ValueError("NORMAL and REFUSED must explain their scope")
        return self


def validate_planning_output(
    payload: dict[str, Any] | PlanningOutput,
    allowed_tools: Iterable[str] | None,
) -> dict[str, Any]:
    """Normalize old probe proposals and v2 outputs without weakening evidence gates."""
    output = PlanningOutput.model_validate(payload)
    if output.disposition == "INVESTIGATE":
        if allowed_tools is not None and output.tool_name not in allowed_tools:
            raise ValueError("tool is outside the server supplied allowlist")
        if any(marker in condition.casefold()
               for hypothesis in output.hypotheses
               for condition in hypothesis.falsification_criteria
               for marker in COLLECTION_FAILURE_MARKERS):
            raise ValueError("采集失败或不可观测不能证伪根因；请用采集成功后的相反观察。")
        # Keep the numeric CPU predicate contract unchanged on both runtimes.
        from server.app.drop_insight.cpu_criteria import cpu_plan_validation_error
        cpu_error = cpu_plan_validation_error(output.hypotheses)
        if cpu_error:
            raise ValueError(cpu_error)
    return output.model_dump(mode="json")


def planning_output_schema(allowed_tools: Iterable[str]) -> dict[str, Any]:
    """Schema advertised to the real legacy tool; semantic branch rules stay server-owned."""
    schema = PlanningOutput.model_json_schema()
    tools = list(allowed_tools)
    schema["properties"]["tool_name"] = {
        "anyOf": ([{"type": "string", "enum": tools}] if tools else []) + [{"type": "null"}],
        "description": "INVESTIGATE uses an allowlisted tool; other dispositions require null.",
    }
    def require_object_fields(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                require_object_fields(value)
        elif isinstance(node, list):
            for value in node:
                require_object_fields(value)
    require_object_fields(schema)
    return schema
