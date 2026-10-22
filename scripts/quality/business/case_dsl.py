"""可生成新数据/新操作序列的业务测试 DSL，独立于既有47项场景目录。

DSL只描述合成输入和受限操作，不能携带预期值、断言、URL、SQL或代码。
业务对错由独立oracle判定。此层只验证执行边界，故意允许违反业务约束的数据。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from contract import canonical, digest, reject_duplicate_keys

MAX_BYTES = 65_536
ACTORS = ("agent", "viewer", "manager", "anonymous", "other")
SEEDS = ("own_seed", "foreign_seed", "anonymized_seed")
OPERATIONS = ("create", "read", "patch", "list", "delete")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
BODY_FIELDS = (
    "name",
    "display_name",
    "email",
    "phone_number",
    "birthdate",
    "tags",
    "source",
    "custom_fields",
    "organization_id",
    "is_anonymized",
    "unknown_probe",
)


def bounded_json(value, depth=0):
    if depth > 3:
        raise ValueError("DSL_DATA_DEPTH")
    if value is None or type(value) is bool:
        return
    if type(value) is int and abs(value) <= 1_000_000:
        return
    if type(value) is float and math.isfinite(value) and abs(value) <= 1_000_000:
        return
    if isinstance(value, str) and len(value) <= 512:
        return
    if isinstance(value, list) and len(value) <= 24:
        for item in value:
            bounded_json(item, depth + 1)
        return
    if (
        isinstance(value, dict)
        and len(value) <= 16
        and all(isinstance(key, str) and 1 <= len(key) <= 81 for key in value)
    ):
        for item in value.values():
            bounded_json(item, depth + 1)
        return
    raise ValueError("DSL_DATA_BOUND")


def validate_suite(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "suite_id", "cases"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 2
    ):
        raise ValueError("DSL_SUITE_FIELDS")
    if not isinstance(value["suite_id"], str) or not IDENTIFIER.fullmatch(
        value["suite_id"]
    ):
        raise ValueError("DSL_SUITE_ID")
    cases = value["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 8:
        raise ValueError("DSL_CASE_COUNT")
    seen, count = set(), 0
    for case in cases:
        if (
            not isinstance(case, dict)
            or set(case) != {"id", "steps"}
            or not isinstance(case["id"], str)
            or not IDENTIFIER.fullmatch(case["id"])
            or case["id"] in seen
        ):
            raise ValueError("DSL_CASE_FIELDS")
        seen.add(case["id"])
        if not isinstance(case["steps"], list) or not 1 <= len(case["steps"]) <= 12:
            raise ValueError("DSL_STEP_COUNT")
        slots = set(SEEDS)
        for step in case["steps"]:
            count += 1
            if (
                not isinstance(step, dict)
                or step.get("op") not in OPERATIONS
                or step.get("actor") not in ACTORS
            ):
                raise ValueError("DSL_OPERATION")
            op = step["op"]
            fields = {"op", "actor"} | (
                {"body", "save_as"}
                if op == "create"
                else {"target", "body"}
                if op == "patch"
                else {"target"}
                if op in {"read", "delete"}
                else {"query"}
            )
            if set(step) != fields:
                raise ValueError("DSL_STEP_FIELDS")
            if op == "create":
                slot = step["save_as"]
                if (
                    not isinstance(slot, str)
                    or not IDENTIFIER.fullmatch(slot)
                    or slot in slots
                ):
                    raise ValueError("DSL_SLOT")
                slots.add(slot)
            if op in {"read", "patch", "delete"} and (
                not isinstance(step["target"], str) or step["target"] not in slots
            ):
                raise ValueError("DSL_FORWARD_OR_UNKNOWN_REFERENCE")
            if "body" in step:
                body = step["body"]
                if (
                    not isinstance(body, dict)
                    or not set(body) <= set(BODY_FIELDS)
                    or len(canonical(body)) > 16_384
                ):
                    raise ValueError("DSL_BODY_FIELDS")
                bounded_json(body)
            if op == "list":
                query = step["query"]
                if not isinstance(query, dict) or not set(query) <= {"limit", "search"}:
                    raise ValueError("DSL_QUERY_FIELDS")
                if "limit" in query and (
                    type(query["limit"]) is not int or not -1 <= query["limit"] <= 101
                ):
                    raise ValueError("DSL_LIMIT_BOUND")
                if "search" in query and (
                    not isinstance(query["search"], str) or len(query["search"]) > 201
                ):
                    raise ValueError("DSL_SEARCH_BOUND")
    if count > 96 or len(canonical(value)) > MAX_BYTES:
        raise ValueError("DSL_SUITE_BUDGET")
    return cases


def load_suite(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("DSL_FILE")
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("DSL_FILE_SIZE")
    value = json.loads(
        raw,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=lambda _value: (_ for _ in ()).throw(
            ValueError("DSL_NONFINITE")
        ),
    )
    validate_suite(value)
    return value


def suite_schema():
    # 数据值不把业务边界写死在schema里，否则生成器无法生成反例。
    scalar = {
        "anyOf": [
            {"type": "null"},
            {"type": "boolean"},
            {"type": "integer", "minimum": -1_000_000, "maximum": 1_000_000},
            {"type": "string", "maxLength": 512},
        ]
    }
    body = {
        "type": "object",
        "maxProperties": 16,
        "additionalProperties": False,
        "properties": {
            name: {
                "anyOf": [
                    scalar,
                    {"type": "array", "maxItems": 24, "items": scalar},
                    {
                        "type": "object",
                        "maxProperties": 16,
                        "propertyNames": {"maxLength": 81},
                        "additionalProperties": scalar,
                    },
                ]
            }
            for name in BODY_FIELDS
        },
    }
    fields = {
        "actor": {"type": "string", "enum": list(ACTORS)},
        "save_as": {"type": "string", "pattern": IDENTIFIER.pattern},
        "target": {"type": "string", "pattern": IDENTIFIER.pattern},
        "body": body,
        "query": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "limit": {"type": "integer", "minimum": -1, "maximum": 101},
                "search": {"type": "string", "maxLength": 201},
            },
        },
    }
    # 按操作给出精确字段，避免模型遵守宽松Schema后仍被执行器拒收。
    # 跨步骤别名/总预算继续由validate_suite独立校验，Schema不能代替该边界。
    operation_fields = {
        "create": ("actor", "save_as", "body"),
        "patch": ("actor", "target", "body"),
        "read": ("actor", "target"),
        "delete": ("actor", "target"),
        "list": ("actor", "query"),
    }
    step_schema = {
        "oneOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["op", *names],
                "properties": {
                    "op": {"type": "string", "const": operation},
                    **{name: fields[name] for name in names},
                },
            }
            for operation, names in operation_fields.items()
        ]
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "suite_id", "cases"],
        "properties": {
            "schema_version": {"type": "integer", "const": 2},
            "suite_id": {"type": "string", "pattern": IDENTIFIER.pattern},
            "cases": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "steps"],
                    "properties": {
                        "id": {"type": "string", "pattern": IDENTIFIER.pattern},
                        "steps": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 12,
                            "items": step_schema,
                        },
                    },
                },
            },
        },
    }


REQUIREMENTS = {
    "version": "crm-contacts-reviewed-requirements-v2",
    "scope": "contacts create/read/patch/list/delete; synthetic tenants and actors only",
    "rules": [
        "身份来自可信会话；viewer只读，agent可创建修改，manager仍不能直接删除联系人历史。",
        "组织由可信身份决定，不接受请求体指定；跨组织资源不可访问。",
        "姓名与显示名去首尾空格后为1至200字符；邮箱有基本格式；手机号符合E164的8至15位数字。",
        "标签最多20个，每个1至40字符，规范化为小写并去重；自定义字段名1至80字符，修改合并而非覆盖。",
        "同组织当前联系人手机号唯一；不同组织相同手机号允许；已匿名化记录不得恢复修改。",
        "输入不合法或权限不足不得改联系人/业务事件/任务；拒绝审计是允许且必须标明的安全记录。",
    ],
    "generator_constraints": [
        "生成新的合成数据和多步操作，不引用47场景ID目录。",
        "只生成DSL，不得提供断言、预期状态、URL、SQL或可执行代码。",
        "覆盖角色拒写、foreign_seed跨组织读取、own_seed手机号重复及至少一条新数据创建/修改/读取序列。",
        "own_seed手机号为+8613800000000；foreign_seed在另一组织；anonymized_seed不可恢复。",
        "每个case隔离，save_as只能后续引用；每case最多12步，总计最多96步；优先4个case。",
    ],
}


def generation_context():
    return {
        "schema_version": 2,
        "task": "依据审阅需求和DSL schema生成全新的合成测试数据及操作序列。独立oracle和断言代码不会提供给模型。",
        "requirements": REQUIREMENTS,
        # 完整Schema只放唯一工具声明，避免在消息正文重复大对象；纯JSON路径仍遵循工具Schema。
        "schema_reference": "唯一工具generate_business_cases的输入Schema",
        "dsl_conventions": {
            "top_level": ["schema_version=2", "suite_id", "cases"],
            "case_fields": ["id", "steps"],
            "actor_meanings": {
                "agent": "组织A接待成员",
                "viewer": "组织A只读成员",
                "manager": "组织A管理成员",
                "other": "组织B接待成员",
                "anonymous": "未登录",
            },
            "available_seed_targets": list(SEEDS),
            "references": "target只能是seed名称或同case先前create的save_as；不要写UUID或跨case引用。每个create即使预期拒绝也必须有唯一save_as。",
            "operation_shapes": [
                {
                    "op": "create",
                    "actor": "agent",
                    "save_as": "new_alias",
                    "body": {"name": "qa-example"},
                },
                {
                    "op": "patch",
                    "actor": "agent",
                    "target": "new_alias",
                    "body": {"name": "qa-updated"},
                },
                {"op": "read", "actor": "viewer", "target": "new_alias"},
                {"op": "delete", "actor": "manager", "target": "own_seed"},
                {"op": "list", "actor": "agent", "query": {"limit": 1}},
            ],
            "examples_are_structure_only": True,
        },
        "oracle_sha_not_sent": True,
    }


def deterministic_suite():
    # 首次离线可重复计划；这些是DSL输入，不是模型输出或固定场景目录。
    return {
        "schema_version": 2,
        "suite_id": "synthetic-generated-sequences",
        "cases": [
            {
                "id": "novel-state-sequence",
                "steps": [
                    {
                        "op": "create",
                        "actor": "agent",
                        "save_as": "new_delta",
                        "body": {
                            "name": "qa-Δ-新客户",
                            "custom_fields": {"segment": "trial"},
                        },
                    },
                    {
                        "op": "patch",
                        "actor": "agent",
                        "target": "new_delta",
                        "body": {
                            "name": " qa-更新客户 ",
                            "custom_fields": {"locale": "zh"},
                        },
                    },
                    {"op": "read", "actor": "viewer", "target": "new_delta"},
                    {
                        "op": "patch",
                        "actor": "viewer",
                        "target": "new_delta",
                        "body": {"name": "qa-forbidden"},
                    },
                    {"op": "read", "actor": "other", "target": "new_delta"},
                ],
            },
            {
                "id": "novel-data-counterexamples",
                "steps": [
                    {
                        "op": "create",
                        "actor": "agent",
                        "save_as": "long_name",
                        "body": {"name": "界" * 201},
                    },
                    {
                        "op": "create",
                        "actor": "agent",
                        "save_as": "typed_name",
                        "body": {"name": True},
                    },
                    {
                        "op": "create",
                        "actor": "anonymous",
                        "save_as": "anonymous",
                        "body": {"name": "qa-anonymous"},
                    },
                    {
                        "op": "create",
                        "actor": "agent",
                        "save_as": "forged",
                        "body": {
                            "organization_id": "10000000-0000-4000-8000-000000000002"
                        },
                    },
                ],
            },
            {
                "id": "novel-isolation-and-uniqueness",
                "steps": [
                    {
                        "op": "create",
                        "actor": "agent",
                        "save_as": "duplicate",
                        "body": {"phone_number": "+8613800000000"},
                    },
                    {"op": "read", "actor": "agent", "target": "foreign_seed"},
                    {
                        "op": "create",
                        "actor": "other",
                        "save_as": "other_shared",
                        "body": {"name": "qa-other", "phone_number": "+8613800000000"},
                    },
                    {"op": "read", "actor": "agent", "target": "other_shared"},
                ],
            },
            {
                "id": "novel-immutable-and-query",
                "steps": [
                    {
                        "op": "patch",
                        "actor": "agent",
                        "target": "anonymized_seed",
                        "body": {"name": "qa-restore"},
                    },
                    {"op": "list", "actor": "agent", "query": {"limit": 0}},
                    {"op": "delete", "actor": "manager", "target": "own_seed"},
                    {
                        "op": "create",
                        "actor": "viewer",
                        "save_as": "viewer_new",
                        "body": {"name": "qa-viewer"},
                    },
                ],
            },
        ],
    }


def requirement_sha256():
    return digest(REQUIREMENTS)
