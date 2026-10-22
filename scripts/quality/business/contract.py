"""CRM 接口测试的固定需求与有界计划。

模型只能选择已经审阅的场景 ID，不能提供 URL、Python、SQL、断言或身份。
这里的预期结果独立于 CRM 实现；实现发生变化时，应先审阅需求再改契约。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MAX_PLAN_BYTES = 16_384
MAX_CASES = 64
SCHEMA_VERSION = 1

# (场景 ID、分类、需求说明)。不从被测 Pydantic schema 推导预期值，避免同源自证。
CASES = (
    (
        "create_minimum",
        "positive",
        "接待成员可以创建仅含姓名的联系人，组织来自可信身份",
    ),
    ("create_trimmed", "positive", "姓名去除首尾空格，标签规范化且去重"),
    ("name_maximum", "boundary", "200 个字符姓名有效"),
    ("phone_minimum", "boundary", "加号后 8 位号码有效"),
    ("phone_maximum", "boundary", "加号后 15 位号码有效"),
    ("tags_maximum", "boundary", "20 个不同标签有效"),
    ("custom_key_maximum", "boundary", "80 个字符的自定义字段名有效"),
    ("birthdate_leap", "boundary", "真实闰日有效"),
    ("anonymous_read", "authorization", "未认证读取返回 401"),
    ("anonymous_write", "authorization", "未认证创建返回 401 且不产生业务写入"),
    ("viewer_write", "authorization", "viewer 创建返回 403 且不产生业务写入"),
    ("viewer_patch", "authorization", "viewer 修改返回 403 且不产生业务写入"),
    ("viewer_delete", "authorization", "viewer 删除返回 403 且不产生业务写入"),
    ("cross_tenant_get", "tenant", "不能读取另一组织的联系人，返回 404"),
    ("cross_tenant_patch", "tenant", "不能修改另一组织的联系人，返回 404"),
    ("list_tenant_isolation", "tenant", "列表只包含可信身份所属组织的联系人"),
    ("body_tenant_rejected", "tenant", "请求体不得指定 organization_id，返回 422"),
    (
        "computed_field_rejected",
        "authorization",
        "请求体不得写入 is_anonymized，返回 422",
    ),
    ("name_empty", "validation", "空姓名返回 422"),
    ("name_too_long", "validation", "201 个字符姓名返回 422"),
    ("name_boolean", "validation", "布尔值不得转换为姓名字符串"),
    ("email_invalid", "validation", "无效邮箱返回 422"),
    ("phone_too_short", "validation", "加号后 7 位号码返回 422"),
    ("phone_too_long", "validation", "加号后 16 位号码返回 422"),
    ("birthdate_invalid", "validation", "不存在的日期返回 422"),
    ("tags_too_many", "validation", "21 个标签返回 422"),
    ("tag_too_long", "validation", "41 个字符标签返回 422"),
    ("custom_key_too_long", "validation", "81 个字符的字段名返回 422"),
    ("custom_fields_null", "validation", "自定义字段必须是对象而不能为 null"),
    ("unknown_field", "validation", "未知字段返回 422"),
    ("malformed_json", "protocol", "截断 JSON 返回 422"),
    ("array_body", "protocol", "数组不得替代联系人对象"),
    ("invalid_uuid", "protocol", "无效资源 UUID 返回 422"),
    ("unsupported_method", "protocol", "未定义 PUT 方法返回 405"),
    ("duplicate_phone", "state", "同组织已有手机号不可再次创建，返回 409"),
    ("empty_patch", "state", "空修改返回 422，原记录不变"),
    ("anonymized_patch", "state", "已匿名化联系人不可恢复修改，返回 403"),
    ("create_patch_read", "sequence", "创建、读取、修改、再读取一致，并保留审计及事件"),
    ("pagination_disjoint", "sequence", "固定排序的相邻分页不重复且保持租户范围"),
    ("cursor_tenant_binding", "tenant", "组织 A 的分页游标不能在组织 B 使用"),
    ("cursor_tampered", "protocol", "篡改分页游标签名返回 400"),
    ("limit_minimum", "boundary", "limit=1 有效且返回至多一项"),
    ("limit_maximum", "boundary", "limit=100 有效"),
    ("limit_zero", "validation", "limit=0 返回 422"),
    ("limit_overflow", "validation", "limit=101 返回 422"),
    ("search_maximum", "boundary", "200 个字符搜索词有效"),
    ("search_overflow", "validation", "201 个字符搜索词返回 422"),
)
CASE_INDEX = {
    case_id: (category, description) for case_id, category, description in CASES
}
MANDATORY = (
    "create_minimum",
    "anonymous_read",
    "anonymous_write",
    "viewer_write",
    "viewer_patch",
    "cross_tenant_get",
    "cross_tenant_patch",
    "list_tenant_isolation",
    "body_tenant_rejected",
    "duplicate_phone",
    "create_patch_read",
    "cursor_tenant_binding",
)
MUTANTS = (
    "contact_get_ignores_tenant",
    "duplicate_phone_guard_removed",
    "viewer_rank_elevated",
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("PLAN_DUPLICATE_KEY")
        result[key] = value
    return result


def validate_plan(value: object) -> list[str]:
    """拒绝模型自带的 URL、断言、身份和未知字段；不可移除固定安全回归。"""
    if not isinstance(value, dict) or set(value) != {"schema_version", "case_ids"}:
        raise ValueError("PLAN_FIELDS_INVALID")
    if (
        type(value["schema_version"]) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("PLAN_VERSION_INVALID")
    case_ids = value["case_ids"]
    if not isinstance(case_ids, list) or not 1 <= len(case_ids) <= MAX_CASES:
        raise ValueError("PLAN_CASE_COUNT_INVALID")
    if any(
        not isinstance(case_id, str) or case_id not in CASE_INDEX
        for case_id in case_ids
    ):
        raise ValueError("PLAN_CASE_ID_INVALID")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("PLAN_CASE_ID_DUPLICATE")
    return list(dict.fromkeys((*MANDATORY, *case_ids)))


def load_plan(path: Path | None) -> tuple[list[str], dict]:
    if path is None:
        value = {
            "schema_version": SCHEMA_VERSION,
            "case_ids": [item[0] for item in CASES],
        }
        mode = "deterministic_contract_catalog"
    else:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > MAX_PLAN_BYTES
        ):
            raise ValueError("PLAN_FILE_INVALID")
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys
        )
        mode = "external_bounded_plan"
    return validate_plan(value), {
        "mode": mode,
        "plan_sha256": digest(value),
        "model_called_by_runner": False,
        "mandatory_case_ids": list(MANDATORY),
        "model_cannot_modify_oracle": True,
    }


def planner_context() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "task": "为真实 Python CRM 联系人接口选择边界、异常、权限和状态序列测试；仅返回计划 schema 允许的场景 ID。",
        "mandatory_case_ids": list(MANDATORY),
        # 模型只看到可选择的场景名称/分类，不接收具体预期状态、断言代码或实现源码。
        "catalog": [
            {"id": case_id, "category": category}
            for case_id, category, _description in CASES
        ],
        "constraints": {
            "maximum_case_ids": MAX_CASES,
            "urls_allowed": False,
            "code_allowed": False,
            "oracle_changes_allowed": False,
            "network_target_chosen_by_model": False,
        },
    }


def plan_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "case_ids"],
        "properties": {
            "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
            "case_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CASES,
                "uniqueItems": True,
                "items": {"type": "string", "enum": list(CASE_INDEX)},
            },
        },
    }
