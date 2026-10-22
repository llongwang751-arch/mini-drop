"""审阅需求的独立状态oracle；不调用CRM schema/业务函数，也不接收模型预期值。

只覆盖明确列出的联系人字段与操作。数据库特有的email/phone约束按后端能力显式标记，
不能把MemoryRepository不具备的数据库约束伪装为真实PG验证。
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date

from worker import (
    ANONYMIZED,
    CONTACT,
    FOREIGN,
    ORG,
    OTHER_ORG,
    fixture_tables,
)

FIELDS = {
    "name",
    "display_name",
    "email",
    "phone_number",
    "birthdate",
    "tags",
    "source",
    "custom_fields",
}
ACTOR_ORG = {"agent": ORG, "viewer": ORG, "manager": ORG, "other": OTHER_ORG}


def normalized_body(body, *, patch=False):
    """纯需求判定，不从被测Pydantic读取字段限制或运行其validator。"""
    if not isinstance(body, dict) or not set(body) <= FIELDS:
        return None
    values = copy.deepcopy(body)
    for name in (
        "name",
        "display_name",
        "source",
        "email",
        "phone_number",
        "birthdate",
    ):
        if name not in values:
            continue
        value = values[name]
        if value is None:
            if name == "source" and not patch:
                return None
            continue
        if not isinstance(value, str):
            return None
        value = value.strip()
        values[name] = value
        if name in {"name", "display_name"} and not 1 <= len(value) <= 200:
            return None
        if name == "source" and not 1 <= len(value) <= 120:
            return None
        if name == "email" and (
            len(value) > 320 or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) is None
        ):
            return None
        if name == "phone_number":
            if re.fullmatch(r"\+[0-9]{8,15}", value) is None:
                return None
            # 审阅需求：旧巴西手机号码的兼容规则；实现独立于CRM canonical_phone。
            digits = value[1:]
            if digits.startswith("55") and len(digits) == 12 and digits[4] in "6789":
                values[name] = "+" + digits[:4] + "9" + digits[4:]
        if name == "birthdate":
            try:
                if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
                    return None
                date.fromisoformat(value)
            except ValueError:
                return None
    if "tags" in values:
        tags = values["tags"]
        if (
            not isinstance(tags, list)
            or len(tags) > 20
            or any(
                not isinstance(tag, str) or not 1 <= len(tag.strip()) <= 40
                for tag in tags
            )
        ):
            return None
        values["tags"] = list(dict.fromkeys(tag.strip().lower() for tag in tags))
    if "custom_fields" in values:
        custom = values["custom_fields"]
        if (
            not isinstance(custom, dict)
            or any(not key or len(key) > 80 for key in custom)
            or len(json.dumps(custom, ensure_ascii=False).encode()) > 32_768
        ):
            return None
    return values


class Oracle:
    def __init__(self, *, database_constraints=False, initial_records=None):
        initial = (
            initial_records
            if initial_records is not None
            else fixture_tables()["contacts"]
        )
        self.records = {row["id"]: copy.deepcopy(row) for row in initial}
        self.slots = {
            "own_seed": CONTACT,
            "foreign_seed": FOREIGN,
            "anonymized_seed": ANONYMIZED,
        }
        self.database_constraints = database_constraints

    def resolve(self, target):
        return self.slots.get(target) or "50000000-0000-4000-8000-999999999999"

    def predict(self, step):
        actor, op = step["actor"], step["op"]
        if actor == "anonymous":
            return {"status": 401, "mutation": None}
        if actor == "viewer" and op in {"create", "patch", "delete"}:
            return {"status": 403, "mutation": None}
        if op in {"create", "patch"}:
            values = normalized_body(step["body"], patch=op == "patch")
            if values is None:
                return {"status": 422, "mutation": None}
        else:
            values = None
        org = ACTOR_ORG[actor]
        if op == "create":
            for row in self.records.values():
                if row.get("organization_id") != org or row.get("is_merged_into"):
                    continue
                if (
                    values.get("phone_number")
                    and row.get("phone_number") == values["phone_number"]
                ):
                    return {"status": 409, "mutation": None}
                if (
                    self.database_constraints
                    and values.get("email")
                    and str(row.get("email") or "").lower() == values["email"].lower()
                ):
                    return {"status": 409, "mutation": None}
            created = {
                "organization_id": org,
                "is_anonymized": False,
                "tags": [],
                "source": "manual",
                "custom_fields": {},
                **values,
            }
            return {
                "status": 201,
                "mutation": "create",
                "values": created,
                "slot": step["save_as"],
            }
        if op == "list":
            query = step["query"]
            if (
                not 1 <= query.get("limit", 50) <= 100
                or len(query.get("search", "")) > 200
            ):
                return {"status": 422, "mutation": None}
            return {
                "status": 200,
                "mutation": None,
                "org": org,
                "limit": query.get("limit", 50),
            }
        row_id = self.resolve(step["target"])
        current = self.records.get(row_id)
        # DELETE明确转到匿名化接口，不执行资源读取；角色门槛为manager。
        if op == "delete":
            return {"status": 422 if actor == "manager" else 403, "mutation": None}
        if current is None or current.get("organization_id") != org:
            return {"status": 404, "mutation": None}
        if op == "read":
            return {
                "status": 200,
                "mutation": None,
                "values": current,
                "row_id": row_id,
            }
        if current.get("is_anonymized"):
            return {"status": 403, "mutation": None}
        if not values:
            return {"status": 422, "mutation": None}
        if self.database_constraints:
            for other_id, other in self.records.items():
                if (
                    other_id == row_id
                    or other.get("organization_id") != org
                    or other.get("is_merged_into")
                ):
                    continue
                if values.get("phone_number") and values["phone_number"] == other.get(
                    "phone_number"
                ):
                    return {"status": 409, "mutation": None}
        updated = copy.deepcopy(current)
        if "custom_fields" in values:
            values["custom_fields"] = {
                **(current.get("custom_fields") or {}),
                **values["custom_fields"],
            }
        updated.update(values)
        return {"status": 200, "mutation": "patch", "values": updated, "row_id": row_id}

    def accept(self, predicted, response):
        """仅将独立预测写入oracle状态；实际响应只提供服务器新生成的opaque ID。"""
        if predicted["mutation"] == "create":
            row_id = response.get("id")
            self.slots[predicted["slot"]] = row_id
            self.records[row_id] = {"id": row_id, **copy.deepcopy(predicted["values"])}
        elif predicted["mutation"] == "patch":
            self.records[predicted["row_id"]] = copy.deepcopy(predicted["values"])

    def compare_values(self, predicted, response):
        if not isinstance(response, dict):
            return False
        values = predicted.get("values", {})
        return all(
            response.get(key) == value
            for key, value in values.items()
            if key in FIELDS | {"organization_id", "is_anonymized"}
        )
