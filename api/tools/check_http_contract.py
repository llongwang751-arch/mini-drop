"""核对实际 Gin 路由、OpenAPI 引用、输入边界与公开 Go 字段。

依赖 PyYAML、jsonschema；安装 openapi-spec-validator 后，--spec-validator 可额外运行
OpenAPI 结构验证器。这属于静态契约核验，不能替代真实 HTTP 集成测试。
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import warnings

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def actual_routes():
    """沿Gin分组赋值及helper实参解析别名，不因RouterGroup变量换名而漏掉认证表面。

    此处只接受当前仓库使用的静态路径；发现未知分组应失败，不能静默假定它是公开路由。
    同时记录明确的handler绑定，避免文档路径正确却误指向另一个管理动作。
    """
    sources = {file: file.read_text(encoding="utf-8") for file in (ROOT / "api/internal/httpapi").glob("*.go") if not file.name.endswith("_test.go")}
    combined = "\n".join(sources.values())
    aliases = {name: "" for name in re.findall(r"\b(\w+)\s*:?=\s*gin\.(?:New|Default)\(", combined)}
    helpers = dict(re.findall(r"func\s+\([^)]*\)\s+(\w+)\((\w+)\s+\*gin\.RouterGroup\)", combined))
    for _ in range(16):
        before = dict(aliases)
        for alias, parent, suffix in re.findall(r'\b(\w+)\s*:?=\s*(\w+)\.Group\("([^\"]*)"\)', combined):
            if parent in aliases:
                value = aliases[parent] + suffix
                assert alias not in aliases or aliases[alias] == value, ("ambiguous Gin group alias", alias)
                aliases[alias] = value
        for helper, parameter in helpers.items():
            for caller in re.findall(r"\." + re.escape(helper) + r"\((\w+)\)", combined):
                if caller in aliases:
                    assert parameter not in aliases or aliases[parameter] == aliases[caller], ("ambiguous helper group", helper)
                    aliases[parameter] = aliases[caller]
        if aliases == before:
            break
    routes = {}
    for file, source in sources.items():
        for group, method, path, handler in re.findall(r'\b(\w+)\.(GET|POST|PUT|PATCH|DELETE)\("([^\"]+)"(?:,\s*s\.(\w+))?', source):
            assert group in aliases, (file.name, "unresolved Gin group alias", group)
            path = re.sub(r":([A-Za-z_]+)", r"{\1}", path)
            key = (method.lower(), aliases[group] + path)
            assert key not in routes, ("duplicate Gin route", key)
            routes[key] = handler
    return routes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spec-validator", action="store_true")
    args = parser.parse_args()
    doc = yaml.safe_load((ROOT / "docs/openapi.yaml").read_text(encoding="utf-8"))
    schemas = doc["components"]["schemas"]
    handlers = actual_routes()
    routes = set(handlers)
    documented = {(method, path) for path, item in doc["paths"].items() for method in item if method in METHODS}
    assert routes == documented, {"undocumented": sorted(routes - documented), "nonexistent": sorted(documented - routes)}
    operation_ids = set()
    for method, path in sorted(documented):
        item = doc["paths"][path]
        op = item[method]
        if handlers[(method, path)]:
            assert op["operationId"] == handlers[(method, path)], (method, path, "handler binding drift")
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
    public_dtos.update({name: name for name in ("DiagnosisRun", "DiagnosisInput", "DiagnosisStep", "DiagnosisPlan", "DiagnosisHypothesis", "DiagnosisReport", "DiagnosisVerifiedFact", "AgentRuntime", "VerificationRun", "ProfileComparison", "ProfileDifference", "Investigation", "InvestigationInput", "CollectionPlan", "CodeRepair", "CodeRepairInput", "DiagnosticSkill", "DiagnosticSkillRef", "DiagnosticCasePrior", "DiagnosticKnowledge", "DiagnosticCaseInput", "DiagnosticCase", "FollowupProposal", "InvestigationFollowup", "TelemetryCatalogItem", "DiagnosticPlaybook", "DiagnosticPlaybookInput", "SignalWindow", "TelemetryMetric", "TelemetryLog", "TelemetryTrace", "TelemetryChange", "BusinessSignalObservation")})
    domain += "\n" + "\n".join(file.read_text(encoding="utf-8") for file in (ROOT / "api/internal/service").glob("*.go") if not file.name.endswith("_test.go"))
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
    valid("DiagnosisInput", {"goal": "fixture", "mode": "evidence", "knowledge_mode": "experience"})
    valid("DiagnosisInput", {"goal": "fixture", "mode": "evidence", "knowledge_mode": "self_training"}, False)
    valid("DiagnosticCaseInput", {"outcome": "reviewed_correction", "human_confirmed": True, "correction_code": "insufficient_evidence"})
    valid("DiagnosticCaseInput", {"outcome": "verified_repair", "human_confirmed": True, "code_repair_id": "1"})
    valid("DiagnosticCaseInput", {"outcome": "reviewed_correction", "human_confirmed": True}, False)
    valid("DiagnosticCaseInput", {"outcome": "verified_repair", "human_confirmed": True, "code_repair_id": "1", "accepted": True}, False)
    valid("DiagnosisInput", {"goal": "fixture", "mode": "evidence", "telemetry_service_id": "Checkout.production"})
    valid("DiagnosisInput", {"goal": "fixture", "mode": "evidence", "telemetry_service_id": ""})
    valid("DiagnosisInput", {"goal": "fixture", "mode": "evidence", "telemetry_service_id": "https://example.invalid"}, False)
    playbook_skill = {"title": "导入方法夹具", "task_kinds": ["GO_CPU"], "required_tools": ["inspect_hotspots"],
        "steps": ["检查本轮热点"], "counter_evidence": ["检查是否只是正常负载"], "stop_conditions": ["当前测量不足时停止"], "verification": ["需要当前证据支持"]}
    playbook_input = {"logical_key": "imported_cpu", "version": 1, "skill": playbook_skill, "expires_days": 30}
    valid("DiagnosticPlaybookInput", playbook_input)
    for field, value in (("required_tools", ["Bash"]), ("required_tools", ["inspect_hotspots", "inspect_hotspots"]),
        ("id", "model-authored-id"), ("steps", ["https://example.invalid/instructions"]), ("steps", ["same", "same"]),
        ("steps", ["line\ncontrol"]), ("match_reasons", ["model-self-verified"])):
        changed = copy.deepcopy(playbook_input)
        changed["skill"][field] = value
        valid("DiagnosticPlaybookInput", changed, False)
    valid("DiagnosticPlaybookInput", {**playbook_input, "publish": True}, False)
    valid("DiagnosticPlaybookReview", {"action": "publish", "review_confirmed": True})
    valid("DiagnosticPlaybookReview", {"action": "retire", "review_confirmed": True})
    for invalid in ({"action": "publish"}, {"action": "publish", "review_confirmed": False},
        {"action": "execute", "review_confirmed": True}, {"action": "publish", "review_confirmed": True, "accepted": True}):
        valid("DiagnosticPlaybookReview", invalid, False)
    valid("InvestigationFollowupDecision", {"confirm": True, "reviewed": True})
    valid("InvestigationFollowupDecision", {"confirm": False, "reviewed": True})
    valid("InvestigationFollowupDecision", {"reviewed": True})
    valid("InvestigationFollowupDecision", {"confirm": True}, False)
    valid("InvestigationFollowupDecision", {"confirm": True, "reviewed": False}, False)
    valid("InvestigationFollowupDecision", {"confirm": True, "reviewed": True, "target": {"pid": 1}}, False)
    valid("TelemetryCatalogItem", {"id": "checkout", "label": "合成目录", "signal_types": ["metrics", "traces"]})
    valid("TelemetryCatalogItem", {"id": "checkout", "label": "合成目录", "signal_types": [], "token_file": "secret"}, False)
    fact = {"id": "F1", "evidence_id": "E15", "tool": "query_business_signals", "path": "/metrics/0/value", "operator": "eq", "value": 0.000001, "unit": "milliseconds", "description": "程序生成的合成事实"}
    valid("DiagnosisVerifiedFact", fact)
    valid("DiagnosisVerifiedFact", {**fact, "value": 90071992547409931234567890123456789})
    valid("DiagnosisVerifiedFact", {**fact, "tool": "read_diagnostic_knowledge"}, False)
    valid("DiagnosisVerifiedFact", {**fact, "value": {"untrusted": "object"}}, False)
    valid("DiagnosisStep", {"evidence_id": "E15", "tool": "propose_followup_collection", "observation": {}})
    valid("DiagnosisStep", {"evidence_id": "E16", "tool": "inspect_hotspots", "observation": {}}, False)
    unknown_window = {"available": False, "service_ref": "S1", "signal": "metrics", "reason": "collection_window_unknown", "context_queried": False, "truncated": False, "limitations": ["真实采集时钟缺失"]}
    valid("BusinessSignalObservation", unknown_window)
    valid("BusinessSignalObservation", {**unknown_window, "window": {"start": "0001-01-01T00:00:00Z", "end": "0001-01-01T00:00:00Z"}}, False)
    # 固定导入夹具：原上下文窗未查询，不能把范围元数据解释成本轮聚合范围。
    executed_window = {"start": "2026-10-23T01:00:00Z", "end": "2026-10-23T01:00:20Z"}
    measured_signal = {key: value for key, value in unknown_window.items() if key != "reason"}
    measured_signal.update(available=True, window=executed_window, context_window=executed_window, window_scope="collector_execution")
    valid("BusinessSignalObservation", measured_signal)
    valid("BusinessSignalObservation", {**measured_signal, "window_scope": "attempt_execution"})
    valid("BusinessSignalObservation", {**measured_signal, "window_scope": "historical_aggregate"}, False)
    valid("BusinessSignalObservation", {**measured_signal, "context_queried": True}, False)
    valid("BusinessSignalObservation", {key: value for key, value in measured_signal.items() if key != "context_window"}, False)
    valid("BusinessSignalObservation", {**unknown_window, "context_window": executed_window}, False)
    valid("TelemetryMetric", {"name": "error_rate", "value": 0.01, "unit": "ratio", "observed_at": "2026-10-21T01:00:00Z"})
    valid("TelemetryTrace", {"trace_ref": "trace_imported", "duration_ms": 1, "started_at": "2026-10-21T01:00:00Z"})
    regression_counts = {"tests": 1, "failures": 0, "errors": 0, "skipped": 0, "unexpected_successes": 0, "expected_failures": 0, "success": True}
    valid("RepairRegressionValidation", {"baseline_failed": True, "candidate_passed": True, "same_test_files": True,
        "baseline": {**regression_counts, "failures": 1, "success": False}, "candidate": regression_counts, "test_sha256": {"test_imported.py": "a" * 64}})
    bundle = {"schema_version": 1, "format": "unified-diff", "source_revision": "a" * 64, "content": "--- a/business.py\n+++ b/business.py\n",
        "sha256": "b" * 64, "code_files": ["business.py"], "test_files": ["drop_regression/test_imported.py"], "applied_to": "isolated_snapshot", "production_deployed": False}
    valid("RepairPatchBundle", bundle)
    valid("RepairPatchBundle", {**bundle, "production_deployed": True}, False)
    valid("RepairPatchBundle", {**bundle, "shell": "git apply"}, False)
    valid("RepairNegativeControl", {"kind": "identical_frozen_baseline", "ratios": [1.0] * 6, "median_ratio": 1.0, "stable": True})
    valid("CodeRepairValidation", {"success": True, "accepted": True, "executor_contract": 3, "root_cause_status": "validated_in_controlled_saber_benchmark"})
    valid("CodeRepairValidation", {"success": True, "accepted": True, "executor_contract": 4, "output_contract": "drop-python-project-v1", "patch_bundle": bundle,
        "regression_tests": [{"path": "drop_regression/test_imported.py", "sha256": "a" * 64}], "negative_control": {"kind": "identical_frozen_baseline", "ratios": [1.0] * 6, "median_ratio": 1.0, "stable": True}})
    for path in ("/api/v1/diagnostic-playbooks/{id}/review", "/api/v1/investigations/{id}/followups/{followup_id}/decision"):
        for parameter in doc["paths"][path]["post"]["parameters"]:
            if parameter.get("in") == "path":
                assert parameter["schema"]["pattern"] == r"^[1-9][0-9]*$", (path, parameter)
    catalog_query = next(item for item in doc["paths"]["/api/v1/diagnostic-data-sources"]["get"]["parameters"] if item.get("name") == "agent_id")
    assert catalog_query["required"] is True
    valid("DiagnosisCollectInput", {"confirm": True})
    valid("DiagnosisCollectInput", {"confirm": False}, False)
    runtime_fixture = {"framework": "agentscope", "framework_version": "2.0.10", "agent_class": "agentscope.agent.Agent",
                       "run_id": "00000000-0000-4000-8000-000000000001", "model_calls": 2, "tool_calls": 2, "elapsed_ms": 12}
    valid("AgentRuntime", runtime_fixture)
    valid("AgentRuntime", {**runtime_fixture, "usage": {"input_tokens": 10, "output_tokens": 5}})
    valid("AgentRuntime", {**runtime_fixture, "capability": "imported-fixture-must-not-be-public"}, False)
    valid("AgentRuntime", {**runtime_fixture, "model_calls": 9, "tool_calls": 14})
    valid("AgentRuntime", {**runtime_fixture, "model_calls": 10}, False)
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
