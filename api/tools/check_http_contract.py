"""核对实际 Gin 路由、OpenAPI 引用、输入边界与公开 Go 字段。

依赖 PyYAML、jsonschema；安装 openapi-spec-validator 后，--spec-validator 可额外运行
OpenAPI 结构验证器。这属于静态契约核验，不能替代真实 HTTP 集成测试。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import warnings

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-validator", action="store_true")
    args = parser.parse_args()
    doc = yaml.safe_load((ROOT / "docs/openapi.yaml").read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]
    routes = set()
    for file in (ROOT / "api/internal/httpapi").glob("*.go"):
        if file.name.endswith("_test.go"):
            continue
        for group, method, path in re.findall(r'\b([rv])\.(GET|POST|PUT|PATCH|DELETE)\("([^\"]+)"', file.read_text(encoding="utf-8")):
            path = re.sub(r":([A-Za-z_]+)", r"{\1}", path)
            routes.add((method.lower(), ("/api/v1" if group == "v" else "") + path))
    documented = {(method, path) for path, item in doc["paths"].items() for method in item if method in METHODS}
    assert routes == documented, {"undocumented": sorted(routes - documented), "nonexistent": sorted(documented - routes)}
    operation_ids = set()
    for method, path in sorted(documented):
        item = doc["paths"][path]
        op = item[method]
        assert op["operationId"] not in operation_ids, op["operationId"]
        operation_ids.add(op["operationId"])
        params = item.get("parameters", []) + op.get("parameters", [])
        names = {(p.get("in"), p.get("name")) for p in params}
        path_parameters = set(re.findall(r"\{([^}]+)\}", path))
        assert path_parameters == {name for location, name in names if location == "path"}, (method, path, names)
        public = path.startswith("/health/") or path == "/metrics" or path in {
            "/api/v1/auth/login", "/api/v1/auth/providers", "/api/v1/auth/oidc/start", "/api/v1/auth/oidc/callback"}
        assert (op.get("security") == []) == public, (method, path, "public security drift")
        if method != "get" and not public:
            assert {"$ref": "#/components/parameters/CSRFToken"} in params, (method, path, "CSRF omitted")
        if (method, path) in {("post", "/api/v1/investigations"), ("post", "/api/v1/code-repairs"), ("post", "/api/v1/tasks"), ("post", "/api/v1/composites"), ("post", "/api/v1/tasks/{id}/retry"), ("post", "/api/v1/tasks/{id}/diagnoses"), ("post", "/api/v1/tasks/{id}/diagnoses/{run_id}/collect")}:
            assert {"$ref": "#/components/parameters/IdempotencyKey"} in params, (method, path, "idempotency omitted")
        assert "default" in op["responses"]

    def walk(value):
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/"), reference
                resolved = doc
                for key in reference[2:].split("/"):
                    resolved = resolved[key.replace("~1", "/").replace("~0", "~")]
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(doc)
    for schema in schemas.values():
        jsonschema.Draft202012Validator.check_schema(schema)

    catalog = json.loads((ROOT / "contracts/task-kinds.json").read_text(encoding="utf-8"))
    assert schemas["TaskKind"]["properties"]["id"]["enum"] == [kind["id"] for kind in catalog]
    assert schemas["CreateTask"]["properties"]["kind"]["enum"] == [kind["id"] for kind in catalog if kind["id"] != "COMPOSITE"]
    for kind in catalog:
        assert schemas[f"NormalizedParameters_{kind['id']}"] == kind["parameter_schema"], kind["id"]

    # 共享 Go DTO 的每个公开 JSON 字段都要有对应描述，包括 Task/CompositeRun
    # 经 allOf 继承的字段；不得为了文档覆盖而暴露凭据字段。
    def properties(schema):
        if "$ref" in schema:
            return properties(schemas[schema["$ref"].rsplit("/", 1)[1]])
        result = set(schema.get("properties", {}))
        for child in schema.get("allOf", []):
            result |= properties(child)
        return result

    public_dtos = {"User": "User", "Target": "TaskTarget", "TaskPermissions": "TaskPermissions", "Budget": "TaskBudget",
                   "ImportInput": "ImportInput", "ImportFile": "ImportFile", "CreateTask": "CreateTask",
                   "Task": "Task", "Attempt": "Attempt", "Agent": "Agent", "Artifact": "Artifact", "Event": "Event",
                   "Schedule": "Schedule", "Audit": "Audit", "Group": "Group", "CompositeRun": "CompositeRun",
                   "CompositeNode": "CompositeNode", "CreateComposite": "CreateComposite", "CompositeNodeInput": "CompositeNodeInput"}
    domain = "\n".join(file.read_text(encoding="utf-8") for file in (ROOT / "api/internal/domain").glob("*.go") if not file.name.endswith("_test.go"))
    public_dtos.update({name: name for name in ("DiagnosisRun", "DiagnosisInput", "DiagnosisStep", "DiagnosisPlan", "DiagnosisHypothesis", "DiagnosisReport", "AgentRuntime", "VerificationRun", "ProfileComparison", "ProfileDifference", "Investigation", "InvestigationInput", "CollectionPlan", "CodeRepair", "CodeRepairInput")})
    domain += "\n" + "\n".join(file.read_text(encoding="utf-8") for file in (ROOT / "api/internal/service").glob("diagnosis*.go") if not file.name.endswith("_test.go"))
    domain += "\n" + "\n".join((ROOT/"api/internal/service"/name).read_text(encoding="utf-8") for name in ("investigation.go","code_repair.go","agent_runtime.go"))
    for go_name, schema_name in public_dtos.items():
        body = re.search(r"type " + go_name + r" struct \{(.*?)\n\}", domain, re.S).group(1)
        go_fields = {name for name in re.findall(r'json:"([^,\"]+)', body) if name != "-"}
        assert go_fields == properties(schemas[schema_name]), (go_name, go_fields - properties(schemas[schema_name]), properties(schemas[schema_name]) - go_fields)

    # 代表性载荷只是 Schema 测试夹具，不是线上运行数据。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        resolver = jsonschema.RefResolver.from_schema(doc)
    def valid(name, value, expected=True):
        validator = jsonschema.Draft202012Validator(schemas[name], resolver=resolver)
        errors = list(validator.iter_errors(value))
        assert (not errors) == expected, (name, [error.message for error in errors])

    valid("CreateTask", {"kind": "IMPORT_COLLAPSED", "import": {"filename": "fixture.folded", "data_base64": "YSAxCg=="}})
    valid("CreateTask", {"kind": "GO_CPU", "import": {"filename": "fixture.pprof", "format": "pprof", "data_base64": "AAAA"}})
    valid("CreateTask", {"kind": "JAVA_CPU", "import": {"filename": "fixture.jfr", "format": "jfr", "data_base64": "AAAA"}})
    valid("CreateTask", {"kind": "RESOURCE_SNAPSHOT", "target_agent_id": "fixture-agent", "parameters": {"target": {"type": "host"}}})
    valid("CreateTask", {"kind": "COMPOSITE"}, False)
    valid("CreateTask", {"kind": "GO_CPU", "url": "https://example.invalid/raw"}, False)
    valid("TaskParameters", {"source": "import"}, False)
    valid("TaskParameters", {"traceparent": "00-a-b-01"}, False)
    valid("TaskParameters", {"argv": ["sh"]}, False)
    valid("DiagnosisInput", {"goal": "Explicit imported contract fixture", "mode": "evidence"})
    valid("DiagnosisInput", {"goal": "", "mode": "model", "consent": True}, False)
    valid("DiagnosisInput", {"goal": "fixture", "mode": "model", "consent": False}, False)
    valid("DiagnosisInput", {"goal": "fixture", "mode": "model", "command": "sh"}, False)
    valid("DiagnosisCollectInput", {"confirm": True})
    valid("DiagnosisCollectInput", {"confirm": False}, False)
    runtime_fixture = {"framework": "agentscope", "framework_version": "2.0.10", "agent_class": "agentscope.agent.Agent",
                       "run_id": "00000000-0000-4000-8000-000000000001", "model_calls": 2, "tool_calls": 2, "elapsed_ms": 12}
    valid("AgentRuntime", runtime_fixture)
    valid("AgentRuntime", {**runtime_fixture, "usage": {"input_tokens": 10, "output_tokens": 5}})
    valid("AgentRuntime", {**runtime_fixture, "capability": "imported-fixture-must-not-be-public"}, False)
    valid("AgentRuntime", {**runtime_fixture, "model_calls": 9}, False)
    valid("AgentRuntime", {**runtime_fixture, "usage": {"input_tokens": -1}}, False)
    valid("CreateShare", {"user_id": "fixture-user", "permission": "read"})
    valid("CreateShare", {"user_id": "fixture-user", "group_id": "fixture-group", "permission": "read"}, False)
    valid("CreateSchedule", {"cron": "30 1 * * *", "timezone": "America/New_York", "template": {"kind": "RESOURCE_SNAPSHOT", "target_agent_id": "fixture-agent"}})
    valid("CreateSchedule", {"cron": "* * * * *", "interval_minutes": 1, "template": {"kind": "PROC_CPU", "target_agent_id": "fixture-agent"}}, False)
    valid("Event", {"sequence": "9007199254740993", "task_id": "1", "type": "CREATED", "source": "api", "payload": {}, "created_at": "2026-10-16T00:00:00Z"})
    valid("Event", {"sequence": 9007199254740993, "task_id": "1", "type": "CREATED", "source": "api", "payload": {}, "created_at": "2026-10-16T00:00:00Z"}, False)
    if args.spec_validator:
        from openapi_spec_validator import validate
        validate(doc)
    print(f"HTTP contract valid: {len(routes)} actual routes, {len(schemas)} schemas, {len(catalog)} exact catalog schemas, public DTO coverage and boundary fixtures")


if __name__ == "__main__":
    main()
