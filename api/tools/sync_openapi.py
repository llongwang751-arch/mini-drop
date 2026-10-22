"""根据已审阅 handler 与共享 TaskKind catalog 生成 HTTP 参考文档。

仅需 Python 与 PyYAML，可从任意目录运行，不访问在线服务；
配套 check_http_contract.py 会逐项比较文档操作与真实 Gin 路由。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CATALOG = json.loads((ROOT / "contracts/task-kinds.json").read_text(encoding="utf-8"))
PARTIAL_RESULT = json.loads((ROOT / "contracts/partial-result.schema.json").read_text(encoding="utf-8"))
KINDS = [k["id"] for k in CATALOG]
IMPORT_KINDS = [k for k in KINDS if k != "COMPOSITE"]
AGENT_KINDS = [k for k in IMPORT_KINDS if k != "IMPORT_COLLAPSED"]


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def obj(props=None, required=(), *, closed=False, **extra):
    value = {"type": "object", "properties": props or {}}
    if required:
        value["required"] = list(required)
    if closed:
        value["additionalProperties"] = False
    return value | extra


def arr(item, **extra):
    return {"type": "array", "items": item} | extra


def string(**extra):
    return {"type": "string"} | extra


def integer(**extra):
    return {"type": "integer"} | extra


BOOL = {"type": "boolean"}
NUMBER = {"type": "number"}
DATE = string(format="date-time")
NULL_DATE = {"type": ["string", "null"], "format": "date-time"}
ID = string(pattern=r"^[1-9][0-9]*$", description="Database bigint encoded as a decimal string.")
NULL_STRING = {"type": ["string", "null"]}
MAP = {"type": "object", "additionalProperties": True}
ROLE = string(enum=["viewer", "operator", "group_admin", "platform_admin", "admin"])
GROUP_ROLE = string(enum=["viewer", "operator", "group_admin"])
PERMISSION = string(enum=["read", "operate"])
VISIBILITY = string(enum=["private", "group"], default="private")
GROUP_FIELDS = {"group_id": NULL_STRING, "visibility": VISIBILITY}
S = {}

S["Error"] = obj({"code": string(), "message": string(), "retryable": BOOL, "details": MAP},
                 ("code", "message", "retryable", "details"))
S["ErrorEnvelope"] = obj({"request_id": string(), "data": {"type": "null"}, "error": ref("Error")},
                         ("request_id", "data", "error"))
S["User"] = obj({"id": string(), "username": string(), "display_name": string(), "role": ROLE,
                 "created_at": DATE}, ("id", "username", "display_name", "role", "created_at"))
S["Auth"] = obj({"user": ref("User"), "csrf_token": string()}, ("user", "csrf_token"))
S["AuthProviders"] = obj({"local": BOOL,
    "oidc": obj({"enabled": BOOL, "name": string()}, ("enabled", "name")),
    "ai": obj({"enabled": BOOL, "name": string()}, ("enabled", "name"))}, ("local", "oidc", "ai"))
S["Login"] = obj({"username": string(maxLength=64), "password": string(format="password", maxLength=72)},
                  ("username", "password"), closed=True)
S["CreateUser"] = obj({"username": string(pattern="^[a-zA-Z0-9_.-]{3,64}$", maxLength=64), "password": string(format="password", minLength=12, maxLength=72),
    "display_name": string(maxLength=120), "role": ROLE}, ("username", "password", "role"), closed=True)
S["UpdateUser"] = obj({"role": ROLE, "display_name": string(minLength=1, maxLength=120),
    "password": string(format="password", minLength=12, maxLength=72)}, closed=True)
S["TaskTarget"] = obj({"type": string(enum=["process", "host", "container"]),
    "pid": integer(minimum=2, maximum=2147483647), "start_time_ticks": integer(minimum=0),
    "container_id": string(), "runtime": string(enum=["docker", "containerd"]),
    "container_pid": integer(minimum=0, maximum=2147483647), "expected_uid": integer(minimum=0, maximum=4294967295),
    "check_uid": BOOL}, ("type",), closed=True,
    description="Target restrictions and UID/container allowlists are enforced by API and rechecked against actual OS identity by Agent.")
S["TaskBudget"] = obj({"max_file_bytes": integer(minimum=0), "max_address_space_bytes": integer(minimum=0),
    "max_cpu_seconds": integer(minimum=0), "max_processes": integer(minimum=0), "max_concurrency": integer(minimum=0)}, closed=True,
    description="Zero is omitted by normalization. Nonzero limits depend on the selected TaskKind parameter_schema; clients cannot override configured hard caps.")
S["TaskParameters"] = obj({"pid": integer(minimum=0, maximum=2147483647),
    "duration_seconds": integer(minimum=0), "frequency_hz": integer(minimum=0),
    "scope": string(enum=["process", "host", "container"]), "target": ref("TaskTarget"),
    "budget": ref("TaskBudget"), "options": MAP}, closed=True,
    description="Wire input before normalization. Zero/omitted duration and frequency use catalog defaults. options and normalized parameters must satisfy that kind's parameter_schema; source and traceparent are server-only and rejected in input.")
S["StoredTaskParameters"] = copy.deepcopy(S["TaskParameters"])
S["StoredTaskParameters"]["properties"].update({"source": string(enum=["import"]), "traceparent": string(readOnly=True)})
S["StoredTaskParameters"]["description"] = "Persisted normalized parameters. Imported input has source=import and no claimed live PID/duration/frequency. traceparent is injected by the API."
S["ImportFile"] = obj({"filename": string(minLength=1, maxLength=120), "content_type": string(),
    "data_base64": string(contentEncoding="base64", minLength=4, maxLength=44739244), "format": string()},
    ("filename", "data_base64"), closed=True,
    description="Strict base64, decoded 1 byte..32 MiB. Basename only; no paths/control characters. Filename/format must match the selected kind's auxiliary_artifacts allowlist.")
S["ImportInput"] = copy.deepcopy(S["ImportFile"])
S["ImportInput"]["properties"]["auxiliary"] = arr(ref("ImportFile"))
S["ImportInput"]["description"] = "Main file plus all auxiliary files must total at most 32 MiB; HTTP body at most 64 MiB. format defaults to the TaskKind input_format, and must match input_formats. Client content_type is accepted but storage content type is derived by the server. BOLT requires target.elf."
S["CreateTask"] = obj({"name": string(maxLength=120), "kind": string(enum=IMPORT_KINDS), "priority": integer(minimum=-10,maximum=10,default=0),
    "target_agent_id": string(), "parameters": ref("TaskParameters"), "collapsed_text": string(),
    "import": ref("ImportInput"), **GROUP_FIELDS}, ("kind",), closed=True,
    description="Use either a live Agent target, generic file import, or IMPORT_COLLAPSED collapsed_text (1 byte..2 MiB). Import is checked before the legacy collapsed_text branch, so IMPORT_COLLAPSED accepts generic import too. Generic imports cannot include a nonempty target_agent_id or collapsed_text. Group visibility requires group_id. Name defaults to kind. All lengths enforced by Go are byte limits.",
    anyOf=[{"required": ["import"]},
           {"properties": {"kind": {"const": "IMPORT_COLLAPSED"}, "collapsed_text": string(minLength=1)}, "required": ["collapsed_text"]},
           {"properties": {"kind": string(enum=AGENT_KINDS), "target_agent_id": string(minLength=1)}, "required": ["target_agent_id"]}],
    allOf=[{"if": {"required": ["import"]}, "then": {"properties": {"target_agent_id": string(maxLength=0), "collapsed_text": string(maxLength=0)}}},
           {"if": {"properties": {"visibility": {"const": "group"}}, "required": ["visibility"]}, "then": {"required": ["group_id"], "properties": {"group_id": string()}}}])
S["TaskPermissions"] = obj({key: BOOL for key in ("read", "operate", "share", "delete", "reanalyze")},
    ("read", "operate", "share", "delete", "reanalyze"), description="Computed by the same authorization service used by mutations. These flags describe authorization; lifecycle restrictions still apply.")
S["Task"] = obj({"id": ID, "name": string(), "kind": string(enum=IMPORT_KINDS), "priority": integer(minimum=-10,maximum=10), "target_agent_id": NULL_STRING,
    "params": ref("StoredTaskParameters"), "collection_status": string(enum=["CREATED", "QUEUED", "DELIVERED", "RUNNING", "UPLOADING", "COLLECTED", "FAILED", "CANCELED"]),
    "analysis_status": string(enum=["PENDING", "RUNNING", "RETRY", "SUCCESS", "FAILED", "CANCELED"]),
    "creator_id": string(), **GROUP_FIELDS, "permissions": ref("TaskPermissions"), "request_id": string(),
    "parent_task_id": ID, "collection_deadline": DATE, "error_code": string(), "error_message": string(),
    "version": integer(), "created_at": DATE, "updated_at": DATE, "deleted_at": DATE},
    ("id", "name", "kind", "params", "collection_status", "analysis_status", "creator_id", "group_id", "visibility", "created_at", "updated_at"))
S["Attempt"] = obj({"id": ID, "task_id": ID, "attempt": integer(minimum=1), "agent_id": NULL_STRING,
    "status": string(), "runner_version": string(), "runner_metadata": MAP, "started_at": NULL_DATE,
    "finished_at": NULL_DATE, "exit_code": {"type": ["integer", "null"]}, "error_code": NULL_STRING,
    "error_message": NULL_STRING, "created_at": DATE}, ("id", "task_id", "attempt", "status", "runner_metadata"))
S["Artifact"] = obj({"id": ID, "task_id": ID, "attempt_id": ID,
    "kind": string(enum=["RAW", "RESULT", "MANIFEST", "INTERMEDIATE", "LOG", "BINARY"]), "name": string(),
    "content_type": string(), "size": integer(minimum=0), "sha256": string(pattern="^[a-f0-9]{64}$"),
    "status": string(), "format": string(), "metadata": MAP, "expires_at": NULL_DATE,
    "retention_class": string(), "download_allowed": BOOL, "created_at": DATE},
    ("id", "task_id", "attempt_id", "kind", "name", "content_type", "size", "sha256", "status", "format", "metadata", "expires_at", "retention_class", "download_allowed"))
S["Event"] = obj({"sequence": ID, "task_id": ID, "attempt_id": ID, "type": string(), "source": string(),
    "payload": MAP, "created_at": DATE}, ("sequence", "task_id", "type", "source", "payload", "created_at"),
    description="sequence is a decimal string in JSON and the SSE id. AI events use types SUGGESTION_DELTA/CREATED/FAILED; transport event name remains task_status.")
S["Suggestion"] = obj({"id": ID, "task_id": ID, "attempt_id": {"type": ["string", "null"]},
    "engine": string(enum=["rules", "ai"]), "version": string(), "input_digest": string(),
    "severity": string(), "title": string(), "content": string(description="Plain text; never render as unsafe HTML."),
    "evidence": MAP, "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "feedback": NULL_STRING, "created_at": DATE, "superseded_at": NULL_DATE},
    ("engine", "version", "severity", "title", "content", "evidence", "confidence"),
    description="Current suggestions exclude superseded/old collection attempts. AI confidence is uncalibrated (0 with explicit limitations), not a measured probability.")
S["FlameNode"] = obj({"name": string(), "value": integer(minimum=0, maximum=9007199254740991),
    "self": integer(minimum=0), "children": arr(ref("FlameNode"))}, ("name", "value", "self", "children"),
    description="Inclusive value=self+sum(children.value). At most 50k nodes and depth 128.")
S["TopFunction"] = obj({"name": string(), "self": integer(), "total": integer(), "self_percent": NUMBER,
    "total_percent": NUMBER}, ("name", "self", "total", "self_percent", "total_percent"))
S["AnalysisSummary"] = obj({"duration_seconds": {"type": ["number", "null"]},
    "frequency_hz": {"type": ["number", "null"]}, "unit": string(), "source": string(), "profile_source": string(),
    "input_format": string(), "observation_count": {"type": ["integer", "null"]}, "profile_type": NULL_STRING,
    "lost_samples": {"type": ["integer", "null"]}, "limitations": arr(string())},
    ("duration_seconds", "frequency_hz", "unit", "source", "limitations"))
S["AnalysisResult"] = obj({"schema_version": integer(const=1), "kind": string(enum=IMPORT_KINDS),
    "total_samples": integer(), "unique_stacks": integer(), "node_count": integer(), "max_depth": integer(),
    "flamegraph": ref("FlameNode"), "top_functions": arr(ref("TopFunction")), "summary": ref("AnalysisSummary"),
    "suggestions": arr(ref("Suggestion")), "sections": MAP},
    ("schema_version", "kind", "total_samples", "unique_stacks", "node_count", "max_depth", "flamegraph", "top_functions", "summary", "suggestions"),
    description="Actual current-attempt Analyzer result. total_samples is a kind-specific weight, not universally samples. sections varies by pipeline; see analyzer/section-examples.json (schema examples, not measurements). Import source is always import, with live duration/frequency null.")
S["PartialAnalysisIdentity"] = obj({name: copy.deepcopy(PARTIAL_RESULT["properties"][name]) for name in
    ("analysis_job_id", "analysis_run", "input_artifact_id", "input_digest")},
    ("analysis_job_id", "analysis_run", "input_artifact_id", "input_digest"), closed=True,
    description="Current job/run and latest ready RAW identity for the latest collection attempt. Absence disables partial-result presentation; never infer a run from a historical event batch.")
S["PartialAnalysisResult"] = {key: copy.deepcopy(value) for key, value in PARTIAL_RESULT.items() if key not in ("$schema", "$id")}
S["TaskDetail"] = {"allOf": [ref("Task"), obj({"attempts": arr(ref("Attempt")), "artifacts": arr(ref("Artifact")),
    "events": arr(ref("Event")), "suggestions": arr(ref("Suggestion")), "result": ref("AnalysisResult"),
    "analysis_identity": ref("PartialAnalysisIdentity")},
    ("attempts", "artifacts", "events", "suggestions"))]}
S["TargetPolicy"] = obj({"host_processes": BOOL, "containers": BOOL, "host_resources": BOOL,
    "allowed_uids": arr(integer(minimum=0)), "allowed_container_ids": arr(string())},
    description="Canonical Agent resource policy keys, shared fixture contracts/fixtures/target-policy.json. An omitted process in a bounded snapshot is not an authorization decision. Off-snapshot PID requires pinned allowed UID plus actual Agent verification.")
S["AgentResources"] = obj({"target_policy": ref("TargetPolicy"), "processes": arr(obj({"pid": integer(),
    "name": string(), "uid": integer(), "start_time_ticks": integer(), "cpu_percent": NUMBER}, ("pid", "name"))),
    "memory_total_bytes": integer(), "memory_used_bytes": integer()}, description="Versioned bounded Agent snapshot; fields depend on OS/protocol/capabilities. No assumption that a truncated snapshot is a complete host inventory.")
S["Agent"] = obj({"id": string(), "hostname": string(), "ip": string(), "version": string(), "platform": string(),
    "status": string(enum=["online", "degraded", "offline", "disabled"]), "capabilities": arr(string()),
    "capability_details": MAP, "labels": MAP, "resources": ref("AgentResources"), "cpu_percent": NUMBER,
    "memory_percent": NUMBER, "load_average": NUMBER, "last_seen_at": DATE, "created_at": DATE},
    ("id", "hostname", "status", "capabilities", "capability_details", "labels", "resources", "last_seen_at"),
    description="labels.registration is an object with protocol_version/build_commit/os/arch/kernel/tool_versions/certificate_expires_unix_ms. Do not render all label values as scalar strings.")
S["AgentMetric"] = obj({"created_at": DATE, "cpu_percent": NUMBER, "memory_percent": NUMBER, "load_average": NUMBER},
    ("created_at", "cpu_percent", "memory_percent", "load_average"))
S["AgentEvent"] = obj({"id": ID, "agent_id": string(), "event_id": string(), "type": string(), "payload": MAP,
    "occurred_at": DATE, "created_at": DATE}, ("id", "agent_id", "event_id", "type", "payload", "occurred_at"))
S["TaskKind"] = obj({"id": string(enum=KINDS), "display_name": string(), "description": string(), "runner": string(),
    "analysis_pipeline": string(), "input_format": string(), "input_formats": arr(string()), "raw_name": string(),
    "raw_content_type": string(), "default_duration_seconds": integer(), "max_duration_seconds": integer(),
    "min_frequency_hz": integer(), "max_frequency_hz": integer(), "default_frequency_hz": integer(),
    "requires_pid": BOOL, "supported_os": arr(string()), "supported_arch": arr(string()), "supported_targets": arr(string()),
    "parameter_schema": MAP, "parameters": MAP, "max_file_bytes": integer(), "auxiliary_artifacts": arr(MAP),
    "tool_requirements": arr(MAP), "available": BOOL}, ("id", "display_name", "runner", "analysis_pipeline", "parameter_schema"),
    description="Passes through contracts/task-kinds.json (18 entries including COMPOSITE). agent_id annotates availability and does not hide offline-import kinds; COMPOSITE uses /composites rather than /tasks.")
S["Overview"] = obj({**{k: integer() for k in ("agents_online", "agents_total", "tasks_total", "tasks_running", "tasks_success", "storage_bytes")},
    "success_rate": {"type": "number", "minimum": 0, "maximum": 100},
    "trend": arr(obj({"time": DATE, "total": integer(), "success": integer()}, ("time", "total", "success"))),
    "recent_tasks": arr(ref("Task")), "collection_counts": {"type": "object", "additionalProperties": integer()},
    "analysis_counts": {"type": "object", "additionalProperties": integer()}},
    ("agents_online", "agents_total", "tasks_total", "tasks_running", "tasks_success", "success_rate", "trend", "recent_tasks", "collection_counts", "analysis_counts", "storage_bytes"),
    description="Scoped by the same resource authorization as list endpoints. success_rate is 0..100 percent. tasks_running includes COLLECTED plus analysis PENDING/RUNNING/RETRY; canceled/failed collection is excluded.")
S["Group"] = obj({"id": string(), "name": string(), "description": string(), "created_at": DATE},
    ("id", "name", "description", "created_at"))
S["GroupMember"] = obj({"user_id": string(), "username": string(), "display_name": string(), "role": GROUP_ROLE},
    ("user_id", "username", "display_name", "role"))
S["AgentGrant"] = obj({"agent_id": string(), "permission": PERMISSION}, ("agent_id", "permission"))
S["GroupDetail"] = {"allOf": [ref("Group"), obj({"members": arr(ref("GroupMember")), "agents": arr(ref("AgentGrant"))}, ("members", "agents"))]}
S["TaskShare"] = obj({"id": ID, "task_id": ID, "user_id": NULL_STRING, "group_id": NULL_STRING,
    "permission": PERMISSION, "created_by": string(), "created_at": DATE}, ("id", "task_id", "permission"))
S["CreateShare"] = obj({"user_id": string(), "group_id": string(), "permission": PERMISSION}, ("permission",), closed=True,
    oneOf=[{"required": ["user_id"], "not": {"required": ["group_id"]}}, {"required": ["group_id"], "not": {"required": ["user_id"]}}])
S["CompositeNodeInput"] = obj({"key": string(pattern="^[a-zA-Z][a-zA-Z0-9_-]{0,47}$"), "required": BOOL,
    "depends_on": arr(string()), "task": ref("CreateTask")}, ("key", "task"), closed=True,
    description="Live collection only; no nested COMPOSITE, import, or collapsed_text. required defaults false. Missing/duplicate keys and cycles are rejected.")
S["CreateComposite"] = obj({"name": string(minLength=1, maxLength=120), **GROUP_FIELDS,
    "deadline_seconds": integer(minimum=1, maximum=3600), "allow_partial_success": BOOL,
    "nodes": arr(ref("CompositeNodeInput"), minItems=1, maxItems=16)}, ("name", "deadline_seconds", "nodes"), closed=True)
S["CompositeSummary"] = obj({k: integer(minimum=0) for k in ("total", "waiting", "running", "succeeded", "failed", "skipped")})
S["CompositeRun"] = obj({"id": ID, "name": string(), "creator_id": string(), **GROUP_FIELDS,
    "status": string(enum=["PENDING", "RUNNING", "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "CANCELED", "TIMED_OUT"]),
    "allow_partial_success": BOOL, "deadline_at": DATE, "summary": ref("CompositeSummary"), "created_at": DATE, "updated_at": DATE},
    ("id", "name", "creator_id", "status", "deadline_at", "summary", "allow_partial_success", "created_at", "updated_at"))
S["CompositeNode"] = obj({"id": ID, "run_id": ID, "key": string(), "required": BOOL, "depends_on": arr(string()),
    "task_id": ID, "status": string(enum=["WAITING", "RUNNING", "SUCCESS", "FAILED", "SKIPPED"]),
    "error_code": string(), "task": ref("Task"), "result": MAP, "created_at": DATE, "updated_at": DATE},
    ("id", "run_id", "key", "required", "depends_on", "status", "created_at", "updated_at"))
S["CompositeReport"] = obj({"results": arr(obj({"node_key": string(), "task_id": ID, "summary": MAP}, ("node_key", "task_id", "summary"))),
    "suggestions": arr(obj({"node_key": string(), "task_id": ID, "suggestion": ref("Suggestion")}, ("node_key", "task_id", "suggestion"))),
    "limitations": arr(string())}, ("results", "suggestions", "limitations"))
S["CompositeDetail"] = {"allOf": [ref("CompositeRun"), obj({"nodes": arr(ref("CompositeNode")), "report": ref("CompositeReport")}, ("nodes", "report"))]}
S["CreateSchedule"] = obj({"name": string(maxLength=120), "interval_minutes": integer(minimum=1, maximum=10080),
    "cron": string(description="Standard five-field minute/hour/day-of-month/month/day-of-week; no @ shortcuts or embedded TZ."),
    "timezone": string(default="Asia/Shanghai", description="Valid IANA name, including DST rules."), **GROUP_FIELDS,
    "template": ref("CreateTask")}, ("template",), closed=True,
    description="Choose cron or interval_minutes. Template is immutable, live collection only. Missed slots advance to the next future slot; no replay flood. Each record stores the template snapshot and unique scheduled_at.",
    oneOf=[{"required": ["cron"], "properties": {"cron": string(minLength=1), "interval_minutes": integer(const=0)}},
           {"required": ["interval_minutes"], "properties": {"cron": string(maxLength=0), "interval_minutes": integer(minimum=1, maximum=10080)}}])
# 提供 Cron 表达式时，Go 可选 interval 字段按零值解码。
S["CreateSchedule"]["properties"]["interval_minutes"] = integer(minimum=0, maximum=10080)
S["Schedule"] = obj({"id": ID, "creator_id": string(), "name": string(), "interval_minutes": integer(minimum=1),
    "cron": string(), "timezone": string(), **GROUP_FIELDS, "template": ref("CreateTask"), "enabled": BOOL,
    "next_run_at": DATE, "last_run_at": DATE, "created_at": DATE, "updated_at": DATE},
    ("id", "creator_id", "name", "interval_minutes", "cron", "timezone", "template", "enabled", "next_run_at"),
    description="cron nonempty selects Cron mode; legacy non-null interval_minutes is 1 for Cron rows and must not be interpreted as their actual cadence.")
S["ScheduleRecord"] = obj({"id": ID, "schedule_id": ID, "task_id": NULL_STRING, "scheduled_at": DATE,
    "status": string(), "error_message": NULL_STRING, "template_snapshot": ref("CreateTask"), "created_at": DATE},
    ("id", "schedule_id", "scheduled_at", "status", "template_snapshot"))
S["Rule"] = obj({"key": string(), "enabled": BOOL, "severity": string(enum=["info", "warning", "critical"]),
    "min_samples": integer(minimum=0), "threshold": {"type": "number", "minimum": 0, "maximum": 100},
    "config": MAP, "version": integer(minimum=1), "updated_by": NULL_STRING, "updated_at": DATE},
    ("key", "enabled", "severity", "min_samples", "threshold", "config", "version", "updated_at"))
S["UpdateRule"] = obj({key: copy.deepcopy(S["Rule"]["properties"][key]) for key in ("enabled", "severity", "min_samples", "threshold", "config")},
    closed=True, description="min_samples <=1e12; encoded config <=8192 bytes. Every update increments version and writes audit in one transaction.")
S["AIRequest"] = obj({"id": ID, "task_id": ID, "attempt_id": ID, "requested_by": string(), "input_digest": string(),
    "status": string(enum=["pending", "running", "success", "failed"]), "error_code": string(), "created_at": DATE, "updated_at": DATE},
    ("id", "task_id", "attempt_id", "requested_by", "input_digest", "status", "created_at", "updated_at"))
S["FeedbackInput"] = obj({"rating": string(enum=["useful", "not_useful"]), "comment": string()}, ("rating",), closed=True,
    description="Comment UTF-8 byte length <=1000. Upserts the current user's feedback for this suggestion.")
S["DiagnosticSkillRef"] = obj({"id": string(), "version": string()}, ("id", "version"), closed=True)
S["DiagnosticSkill"] = obj({"id": string(), "version": string(), "title": string(),
    "task_kinds": arr(string(enum=KINDS)), "required_tools": arr(string()), "steps": arr(string()),
    "counter_evidence": arr(string()), "stop_conditions": arr(string()), "verification": arr(string()),
    "match_reasons": arr(string())}, ("id", "version", "title", "task_kinds", "required_tools", "steps", "counter_evidence", "stop_conditions", "verification"), closed=True)
S["DiagnosticCasePrior"] = obj({"id": ID, "source_diagnosis_id": ID, "outcome": string(enum=["verified_repair", "reviewed_correction"]),
    "assurance": string(), "conclusion": string(), "skill_refs": arr(ref("DiagnosticSkillRef")),
    "match_reasons": arr(string()), "prior_only": {"type": "boolean", "const": True}},
    ("id", "source_diagnosis_id", "outcome", "assurance", "conclusion", "skill_refs", "match_reasons", "prior_only"), closed=True)
S["DiagnosticKnowledge"] = obj({"skills": arr(ref("DiagnosticSkill")), "cases": arr(ref("DiagnosticCasePrior"), maxItems=3),
    "prior_only": {"type": "boolean", "const": True}, "history_enabled": BOOL}, ("skills", "cases", "prior_only", "history_enabled"), closed=True,
    description="程序绑定的诊断方法与历史先验。每项假设必须同时引用本轮测量证据；历史经验不证明当前根因。")
S["DiagnosticCaseInput"] = obj({"outcome": string(enum=["verified_repair", "reviewed_correction"]), "code_repair_id": ID,
    "human_confirmed": {"type": "boolean", "const": True}, "correction_code": string(enum=["insufficient_evidence", "wrong_hotspot", "causality_not_established", "incomparable_measurements", "regression_detected"])},
    ("outcome", "human_confirmed"), closed=True,
    description="人工纠正使用固定枚举。verified_repair必须绑定同一调查的真实成功修复与当前登记版本；不接受客户端的accepted标志、自由文本或补丁。")
S["DiagnosticCaseInput"]["allOf"] = [
    {"if": {"properties": {"outcome": {"const": "reviewed_correction"}}, "required": ["outcome"]},
     "then": {"required": ["correction_code"], "not": {"required": ["code_repair_id"]}}},
    {"if": {"properties": {"outcome": {"const": "verified_repair"}}, "required": ["outcome"]},
     "then": {"required": ["code_repair_id"], "not": {"required": ["correction_code"]}}},
]
S["DiagnosticCase"] = obj({"id": ID, "task_id": ID, "diagnosis_id": ID, "attempt_id": ID, "analysis_job_id": ID,
    "analysis_run": integer(minimum=0), "code_repair_id": ID, "task_kind": string(enum=KINDS), "project_id": string(), "source_revision": string(),
    "outcome": string(enum=["verified_repair", "reviewed_correction"]), "correction_code": string(),
    "status": string(enum=["active", "revoked"]), "created_at": DATE, "expires_at": DATE, "revoked_at": DATE},
    ("id", "task_id", "diagnosis_id", "attempt_id", "analysis_job_id", "analysis_run", "task_kind", "outcome", "status", "created_at", "expires_at"), closed=True)
S["DiagnosisInput"] = obj({"goal": string(minLength=1, maxLength=600), "mode": string(enum=["evidence", "model"]), "consent": BOOL,
    "knowledge_mode": string(enum=["off", "skills", "experience"], default="experience"),
    "telemetry_service_id": string(pattern=r"^(?:[a-zA-Z][a-zA-Z0-9_.-]{0,79})?$")},
    ("goal", "mode"), closed=True, description="Trimmed goal 1..600 Unicode characters, no control characters except LF/TAB. Model mode requires consent=true; evidence mode never calls a provider.")
S["DiagnosisInput"]["allOf"] = [{"if": {"properties": {"mode": {"const": "model"}}, "required": ["mode"]},
    "then": {"properties": {"consent": {"const": True}}, "required": ["consent"]}}]
S["DiagnosisPlan"] = obj({"kind": string(enum=AGENT_KINDS), "duration_seconds": integer(minimum=1, maximum=60),
    "frequency_hz": integer(minimum=0, maximum=999), "reason": string()}, ("kind", "duration_seconds", "frequency_hz", "reason"), closed=True)
EVIDENCE_ID = string(pattern=r"^E(?:[1-9]|1[0-5])$")
FACT_ID = string(pattern=r"^F[1-9][0-9]{0,2}$")
DIAGNOSIS_TOOLS = ["read_profile_summary", "inspect_hotspots", "check_collection_quality", "inspect_call_paths", "propose_verification", "compare_profiles", "read_diagnostic_knowledge", "query_business_signals", "inspect_service_topology", "propose_followup_collection"]
HYPOTHESIS_KINDS = ["cpu_hotspot", "allocation_gc", "lock_io", "normal_load", "insufficient_evidence", "mixed_signals", "business_latency", "downstream_dependency", "deployment_regression"]
S["DiagnosisVerifiedFact"] = obj({"id": FACT_ID, "evidence_id": EVIDENCE_ID,
    "tool": string(enum=[tool for tool in DIAGNOSIS_TOOLS if tool not in {"read_diagnostic_knowledge", "propose_followup_collection"}]),
    "path": string(pattern=r"^/", maxLength=160), "operator": string(enum=["eq", "count", "count_positive_self"]),
    "value": {"anyOf": [string(), NUMBER, BOOL]}, "unit": string(), "description": string()},
    ("id", "evidence_id", "tool", "path", "operator", "value", "unit", "description"), closed=True, readOnly=True,
    description="程序逐字段复算的当前观测事实；path、操作符和单位还须通过工具专属白名单。只证明该字段观测，不证明根因。数值保留Go已验收的JSON词法与精度。")
S["DiagnosisHypothesis"] = obj({"claim": string(), "evidence_ids": arr(EVIDENCE_ID, minItems=1, maxItems=8),
    "verification": string(), "kind": string(enum=HYPOTHESIS_KINDS), "fact_ids": arr(FACT_ID, minItems=1, maxItems=8),
    "unverified_interpretation": string(maxLength=600)}, ("claim", "evidence_ids", "verification"), closed=True,
    description="v2的claim/verification由程序生成；可选自由解释明确未核验，不能代替facts。旧报告没有kind/fact_ids。")
S["AgentRuntime"] = obj({"framework": string(enum=["agentscope"]),
    "framework_version": string(enum=["2.0.10"]), "agent_class": string(enum=["agentscope.agent.Agent"]),
    "run_id": string(format="uuid"), "model_calls": integer(minimum=0, maximum=9),
    "tool_calls": integer(minimum=0, maximum=14), "elapsed_ms": integer(minimum=0, maximum=425000),
    "usage": {"type": "object", "maxProperties": 16, "additionalProperties": integer(minimum=0, maximum=1000000000)}},
    ("framework", "framework_version", "agent_class", "run_id", "model_calls", "tool_calls", "elapsed_ms"),
    closed=True, readOnly=True,
    description="程序核对的AgentScope运行证明，不由模型编写。启用检查点时model/tool/usage为持久阶段累计值，elapsed_ms只表示本次Python会话墙钟，不能当作累计调查耗时。阶段预算分别更低，诊断新协议最多9模型/14工具。历史及legacy运行可不含本字段。")
S["DiagnosisReport"] = obj({"summary": string(), "hypotheses": arr(ref("DiagnosisHypothesis"), minItems=1, maxItems=4),
    "limitations": arr(string()), "plan": ref("DiagnosisPlan"), "engine": string(enum=["model", "evidence"]), "model": string(),
    "agent_runtime": ref("AgentRuntime"), "knowledge": ref("DiagnosticKnowledge"), "report_version": integer(enum=[1, 2]),
    "facts": arr(ref("DiagnosisVerifiedFact"), minItems=1, maxItems=24), "followup_plans": arr(ref("FollowupProposal"), maxItems=2)},
    ("summary", "hypotheses", "limitations", "engine"), closed=True)
S["DiagnosisReport"]["allOf"] = [{"if": {"properties": {"report_version": {"const": 2}}, "required": ["report_version"]},
    "then": {"required": ["facts"], "properties": {"hypotheses": arr({"allOf": [ref("DiagnosisHypothesis"), {"required": ["kind", "fact_ids"]}]}, minItems=1, maxItems=4)}}}]
S["DiagnosisStep"] = obj({"evidence_id": EVIDENCE_ID, "tool": string(enum=DIAGNOSIS_TOOLS),
    "observation": MAP}, ("evidence_id", "tool", "observation"), closed=True)
# 窗口来自真实采集attempt执行时钟；未知窗口省略字段，禁止用分析完成时刻或零时间伪造范围。
S["SignalWindow"] = obj({"start": DATE, "end": DATE}, ("start", "end"), closed=True,
    description="采集attempt真实StartedAt/FinishedAt各扩展一分钟，合计不超过15分钟。缺失、反序或时钟异常返回collection_window_unknown；不以任务创建/分析时钟替代。")
S["TelemetryMetric"] = obj({"name": string(enum=["latency_p95_ms", "error_rate", "throughput_rps"]),
    "value": {"type": "number", "minimum": 0, "maximum": 1e15}, "unit": string(enum=["milliseconds", "ratio", "requests_per_second"]),
    "observed_at": DATE}, ("name", "value", "unit"), closed=True,
    description="固定登记指标；名称与单位由程序映射核对。observed_at仅在源返回真实观测时间时出现，适配器协议要求其位于真实采集窗口。")
S["TelemetryLog"] = obj({"timestamp": DATE, "level": string(), "event_code": string(),
    "duration_ms": {"type": "number", "minimum": 0, "maximum": 1e15}, "status_code": integer(), "trace_ref": string()},
    ("timestamp", "level", "event_code"), closed=True)
S["TelemetryTrace"] = obj({"trace_ref": string(), "duration_ms": {"type": "number", "minimum": 0, "maximum": 1e15},
    "span_count": integer(minimum=0, maximum=100000), "error_count": integer(minimum=0, maximum=100000), "started_at": DATE},
    ("trace_ref", "duration_ms"), closed=True, description="真实Trace开始时间可选；只保留脱敏引用、时长与计数，不输出原Trace ID或属性。")
S["TelemetryChange"] = obj({"timestamp": DATE, "change_type": string(enum=["deployment", "configuration", "feature_flag"]), "revision_ref": string()},
    ("timestamp", "change_type", "revision_ref"), closed=True)
S["BusinessSignalObservation"] = obj({"available": BOOL, "reason": string(), "service_ref": string(pattern=r"^S[1-8]$"),
    "signal": string(enum=["metrics", "logs", "traces", "changes"]), "window": ref("SignalWindow"),
    "window_scope": string(enum=["collector_execution", "attempt_execution"]),
    "context_window": ref("SignalWindow"), "context_queried": {"type": "boolean", "const": False},
    "source_kind": string(enum=["prometheus", "loki", "tempo", "drop-adapter"]), "query_digest": string(),
    "metrics": arr(ref("TelemetryMetric"), maxItems=3), "logs": arr(ref("TelemetryLog"), maxItems=30),
    "traces": arr(ref("TelemetryTrace"), maxItems=20), "changes": arr(ref("TelemetryChange"), maxItems=20),
    "truncated": BOOL, "limitations": arr(string())}, ("available", "service_ref", "signal", "context_queried", "truncated", "limitations"), closed=True,
    description="只读业务观测。window优先使用collector执行时钟，旧任务明确回退attempt执行区间；context_window只作上下文说明，context_queried固定false，未混入本轮聚合。没有可核实时钟则省略窗口，并返回available=false/reason=collection_window_unknown；缺数据不证明正常。")
S["BusinessSignalObservation"]["allOf"] = [{"if": {"properties": {"reason": {"const": "collection_window_unknown"}}, "required": ["reason"]},
    "then": {"not": {"anyOf": [{"required": [key]} for key in ("window", "window_scope", "context_window")]}, "properties": {"available": {"const": False}}}},
    {"if": {"required": ["window_scope"]}, "then": {"required": ["window", "context_window"]}}]
S["DiagnosisStep"]["allOf"] = [{"if": {"properties": {"tool": {"const": "query_business_signals"}}, "required": ["tool"]},
    "then": {"properties": {"observation": ref("BusinessSignalObservation")}}}]
S["DiagnosisRun"] = obj({"id": ID, "task_id": ID, "attempt_id": ID, "analysis_job_id": ID, "analysis_run": integer(minimum=0),
    "requested_by": string(), "goal": string(), "mode": string(enum=["evidence", "model"]), "knowledge_mode": string(enum=["off", "skills", "experience"]), "input_digest": string(),
    "report_protocol": integer(enum=[1, 2]), "telemetry_service_id": string(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$"),
    "status": string(enum=["pending", "running", "success", "failed"]), "trace": arr(ref("DiagnosisStep"), maxItems=15),
    "report": {"anyOf": [ref("DiagnosisReport"), {"type": "null"}]}, "error_code": string(), "created_at": DATE, "updated_at": DATE},
    ("id", "task_id", "attempt_id", "analysis_job_id", "analysis_run", "requested_by", "goal", "mode", "knowledge_mode", "report_protocol", "input_digest", "status", "trace", "report", "created_at", "updated_at"))
S["DiagnosisRun"]["allOf"] = [{"if": {"properties": {"report_protocol": {"const": 1}}, "required": ["report_protocol"]},
    "then": {"properties": {"trace": arr(ref("DiagnosisStep"), maxItems=8)}}}]
S["DiagnosisCollectInput"] = obj({"confirm": {"type": "boolean", "const": True}}, ("confirm",), closed=True)
S["ProfileDifference"] = obj({"symbol": string(), "baseline_self_percent": NUMBER, "candidate_self_percent": NUMBER, "delta_percentage_points": NUMBER})
S["ProfileComparison"] = obj({"comparable": BOOL, "kind": string(), "unit": string(), "baseline_weight": integer(), "candidate_weight": integer(),
    "differences": arr(ref("ProfileDifference")), "outcome": string(enum=["comparison_ready", "insufficient_evidence"]), "root_cause_status": string(enum=["not_tested"]), "limitations": arr(string())})
S["VerificationRun"] = obj({"id": ID,"diagnosis_id": ID,"baseline_task_id": ID,"candidate_task_id": ID,"requested_by": string(),
    "candidate_diagnosis_id": ID,"status": string(enum=["waiting","diagnosing","comparing","success","failed","canceled"]),
    "report": {"type":["object","null"]},"error_code": string(),"created_at": DATE,"updated_at": DATE})
S["CollectionPlan"] = obj({"kind":string(enum=AGENT_KINDS),"duration_seconds":integer(minimum=1,maximum=60),"frequency_hz":integer(minimum=0,maximum=999),"options":MAP,"rationale":string(maxLength=600)}, ("kind", "duration_seconds", "frequency_hz", "rationale"), closed=True,
    description="登记采集器的草案。options须再通过该TaskKind共享parameter_schema及实际目标能力校验；计划本身不授予采集权限。")
S["FollowupProposal"] = obj({"plan": ref("CollectionPlan"), "evidence_ids": arr(EVIDENCE_ID, minItems=1, maxItems=4),
    "requires_confirmation": {"type": "boolean", "const": True}}, ("plan", "evidence_ids", "requires_confirmation"), closed=True,
    description="同一调查、同一授权目标的待审补证计划；本轮真实观测支持取证方向，不能从历史先验直接启动采集。")
S["InvestigationFollowup"] = obj({"id": ID, "investigation_id": ID, "parent_task_id": ID, "source_diagnosis_id": ID,
    "plan": ref("FollowupProposal"), "plan_digest": string(pattern=r"^[0-9a-f]{64}$"),
    "status": string(enum=["proposed", "rejected", "queued", "collecting", "diagnosing", "success", "failed", "canceled"]),
    "task_id": ID, "diagnosis_id": ID, "error_code": string(), "created_at": DATE, "updated_at": DATE},
    ("id", "investigation_id", "parent_task_id", "source_diagnosis_id", "plan", "plan_digest", "status", "created_at", "updated_at"), closed=True)
S["InvestigationFollowupDecision"] = obj({"confirm": {"type": "boolean", "default": False}, "reviewed": {"type": "boolean", "const": True}},
    ("reviewed",), closed=True, description="reviewed必须为true；confirm=true表示入队，否则拒绝该计划。确认仍重验来源、目标和预算，不会在HTTP处理中直接执行采集。")
S["TelemetryCatalogItem"] = obj({"id": string(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$"), "label": string(minLength=1, maxLength=120),
    "signal_types": arr(string(enum=["metrics", "logs", "traces", "changes"]), maxItems=4)}, ("id", "label", "signal_types"), closed=True,
    description="仅公开当前用户可操作节点的已登记服务标识、名称和信号类型；不返回连接URL、查询表达式、租户或凭据路径。")
# 方法输入与只读目录分开，避免客户端伪造程序生成的ID、版本和匹配理由。
PLAYBOOK_TOOL_NAMES = ["inspect_hotspots", "check_collection_quality", "inspect_call_paths", "compare_profiles", "query_business_signals", "inspect_service_topology"]
PLAYBOOK_TEXT = string(minLength=1, maxLength=600, pattern=r"^[^\x00-\x1f\x7f-\x9f<>]+$",
    **{"allOf": [{"not": {"pattern": r"[Hh][Tt][Tt][Pp][Ss]?://"}}, {"not": {"pattern": r"^\s*$"}}]},
    description="有界纯文本，UTF-8最多600字节；禁止HTML、URL、控制字符、Unicode格式字符及纯空白。服务端额外检查UTF-8字节数与Unicode类别。")
S["DiagnosticPlaybookSkillInput"] = obj({"id": string(const=""), "version": string(const=""),
    "title": {**copy.deepcopy(PLAYBOOK_TEXT), "maxLength": 180, "description": "标题UTF-8为1至180字节。"},
    "task_kinds": arr(string(enum=KINDS), minItems=1, maxItems=18, uniqueItems=True),
    "required_tools": arr(string(enum=PLAYBOOK_TOOL_NAMES), minItems=1, maxItems=5, uniqueItems=True),
    **{key: arr(copy.deepcopy(PLAYBOOK_TEXT), minItems=1, maxItems=6, uniqueItems=True) for key in ("steps", "counter_evidence", "stop_conditions", "verification")},
    "match_reasons": arr(string(), maxItems=0)},
    ("title", "task_kinds", "required_tools", "steps", "counter_evidence", "stop_conditions", "verification"), closed=True)
S["DiagnosticPlaybookInput"] = obj({"logical_key": string(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$"),
    "version": integer(minimum=1, maximum=10000), "group_id": NULL_STRING,
    "project_id": string(pattern=r"^(?:[a-zA-Z][a-zA-Z0-9_.-]{0,79})?$"), "skill": ref("DiagnosticPlaybookSkillInput"),
    "expires_days": integer(minimum=1, maximum=90)}, ("logical_key", "version", "skill", "expires_days"), closed=True,
    description="当前可写用户创建不可变草稿；团队作用域需团队管理权限，项目作用域需平台管理员及真实登记项目。相同owner/logical_key/version仅可幂等复用相同内容和作用域，不能覆盖旧版本。")
S["DiagnosticPlaybookReview"] = obj({"action": string(enum=["publish", "retire"]), "review_confirmed": {"type": "boolean", "const": True}},
    ("action", "review_confirmed"), closed=True, description="平台管理员逐版本复核；模型不能自行发布，过期或退役版本不能重新发布。")
S["DiagnosticPlaybook"] = obj({"id": ID, "owner_id": string(), "group_id": string(), "project_id": string(),
    "logical_key": string(), "version": integer(minimum=1, maximum=10000), "skill": ref("DiagnosticSkill"),
    "content_digest": string(pattern=r"^[0-9a-f]{64}$"), "status": string(enum=["draft", "published", "retired"]),
    "reviewer_id": string(), "reviewed_at": DATE, "expires_at": DATE, "created_at": DATE, "updated_at": DATE},
    ("id", "owner_id", "logical_key", "version", "skill", "content_digest", "status", "expires_at", "created_at", "updated_at"), closed=True)
S["InvestigationInput"] = obj({"goal":string(minLength=1,maxLength=600),"target_agent_id":string(minLength=1,maxLength=128),"parameters":ref("TaskParameters"),"consent":{"const":True,"type":"boolean"},"code_project_id":string(maxLength=64),"code_consent":BOOL,
    "telemetry_service_id": string(pattern=r"^(?:[a-zA-Z][a-zA-Z0-9_.-]{0,79})?$")},("goal","target_agent_id","parameters","consent"),closed=True)
S["InvestigationInput"]["allOf"] = [{"if": {"properties": {"code_project_id": string(minLength=1)}, "required": ["code_project_id"]},
    "then": {"required": ["code_consent"], "properties": {"code_consent": {"type": "boolean", "const": True}}}}]
S["Investigation"] = obj({"id":ID,"requested_by":string(),"agent_id":string(),"goal":string(),"parameters":ref("TaskParameters"),"consent":BOOL,"input_digest":string(),
    "status":string(enum=["planning","awaiting_confirmation","queued","collecting","diagnosing","verifying","success","failed","canceled"]),"plan":{"anyOf":[ref("CollectionPlan"),{"type":"null"}]},
    "trace":arr(MAP),"report":{"type":["object","null"]},"baseline_task_id":ID,"diagnosis_id":ID,"verification_id":ID,"code_project_id":string(),"code_source_revision":string(),"code_repair_id":ID,"error_code":string(),"created_at":DATE,"updated_at":DATE,
    "telemetry_service_id": string(pattern=r"^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$")})
S["CodeRepairInput"] = obj({"source_revision":string(pattern="^[0-9a-f]{64}$"),"project_id":string(pattern="^[a-z0-9][a-z0-9_-]{0,63}$"),"goal":string(minLength=1,maxLength=600),"consent":{"const":True,"type":"boolean"}},("project_id","goal","consent"),closed=True)
SHA256 = string(pattern=r"^[0-9a-f]{64}$")
S["RepairRegressionCounts"] = obj({**{name: integer(minimum=0) for name in
    ("tests", "failures", "errors", "skipped", "unexpected_successes", "expected_failures")}, "success": BOOL},
    ("tests", "failures", "errors", "skipped", "unexpected_successes", "expected_failures", "success"), closed=True)
S["RepairRegressionValidation"] = obj({"baseline_failed": BOOL, "candidate_passed": BOOL, "same_test_files": BOOL,
    "baseline": ref("RepairRegressionCounts"), "candidate": ref("RepairRegressionCounts"),
    "test_sha256": {"type": "object", "additionalProperties": SHA256}},
    ("baseline_failed", "candidate_passed", "same_test_files", "baseline", "candidate", "test_sha256"), closed=True,
    description="同一新增测试在原版本断言失败、候选版本通过；失败/错误/跳过分开计数，不把测试未运行当作通过。")
S["RepairNegativeControl"] = obj({"kind": string(const="identical_frozen_baseline"), "ratios": arr(NUMBER, minItems=6, maxItems=6),
    "median_ratio": NUMBER, "stable": BOOL}, ("kind", "ratios", "median_ratio", "stable"), closed=True,
    description="相同冻结baseline的sham测量，只核验环境稳定性，不代表优化效果。")
S["RepairPatchBundle"] = obj({"schema_version": integer(const=1), "format": string(const="unified-diff"),
    "source_revision": SHA256, "content": string(maxLength=24576, description="统一diff，UTF-8最多24576字节。"), "sha256": SHA256,
    "code_files": arr(string()), "test_files": arr(string()), "applied_to": string(const="isolated_snapshot"),
    "production_deployed": {"type": "boolean", "const": False}},
    ("schema_version", "format", "source_revision", "content", "sha256", "code_files", "test_files", "applied_to", "production_deployed"), closed=True,
    description="程序生成的可审阅补丁交付物；只应用于隔离副本，不能当作原仓库已修改或生产已部署。")
S["RepairBenchmarkPair"] = obj({"baseline_cpu_ns": integer(minimum=0), "candidate_cpu_ns": integer(minimum=0),
    "baseline_ns": integer(minimum=0), "candidate_ns": integer(minimum=0), "cases": integer(minimum=0),
    "output_sha256": SHA256, "executed_sql_queries_per_side": integer(minimum=0)},
    ("baseline_cpu_ns", "candidate_cpu_ns", "cases", "output_sha256"), closed=True)
S["RepairPerformanceGate"] = obj({"minimum_median_speedup": NUMBER, "minimum_improved_pairs": integer(minimum=0),
    "pairs": integer(minimum=1), "negative_control_max_drift": NUMBER,
    "minimum_pairs_above_1_05": integer(minimum=0), "total_alternating_pairs": integer(minimum=1)}, closed=True,
    description="通用Python与旧CRM/Saber保留各自登记门槛，不能把受控基准阈值泛化为生产SLA。")
S["CodeProject"] = obj({"id": string(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"), "label": string(), "source_revision": SHA256,
    "editable_files": arr(string()), "readable_files": arr(string()),
    "test_preset": string(enum=["crm-brand-palette-v1", "saber-fts5-v1", "manifest-python-v1"]), "workload": string(),
    "schema_version": integer(const=1), "language": string(const="python"), "output_contract": string(const="drop-python-project-v1"),
    "symbol_mapping": string(const="python_cprofile"), "frozen_files": arr(string()), "allowed_imports": arr(string()),
    "regression_test_dir": string(), "performance_gate": ref("RepairPerformanceGate"),
    "regression_tests_required": {"type": "boolean", "const": True}, "supported_languages": arr(string(const="python"), minItems=1, maxItems=1),
    "runtime": obj({"agent_id": string(), "pid": integer(minimum=1), "uid": integer(minimum=0), "start_time_ticks": integer(minimum=0), "source_revision": SHA256},
        ("agent_id", "pid", "uid", "start_time_ticks", "source_revision"), closed=True)},
    ("id", "label", "source_revision", "editable_files", "readable_files", "test_preset", "workload"), closed=True,
    description="管理员登记的不可变源码能力目录。当前只支持Python；runtime仅在真实容器镜像、标签及进程身份核对成功时出现，不能从模型文本推断。执行argv、源码根目录和凭据不暴露给浏览器。")
S["CodeProject"]["allOf"] = [{"if": {"properties": {"test_preset": {"const": "manifest-python-v1"}}, "required": ["test_preset"]},
    "then": {"required": ["schema_version", "language", "output_contract", "symbol_mapping", "frozen_files", "allowed_imports", "regression_test_dir", "performance_gate", "regression_tests_required", "supported_languages"]}}]
S["RepairSourceEdit"] = obj({"path": string(maxLength=180), "sha256": SHA256, "old": string(), "new": string()},
    ("path", "sha256", "old", "new"), closed=True)
S["RepairCandidateProfile"] = obj({"cpu_ns": integer(minimum=0), "elapsed_ns": integer(minimum=0), "cases": integer(minimum=0),
    "output_sha256": SHA256, "output_type": string(), "mutable_output_isolated": BOOL, "measurement": string(),
    "hotspots": arr(obj({"path": string(), "line": integer(minimum=1), "function": string(), "sha256": SHA256,
        "calls": integer(minimum=0), "self_seconds": NUMBER, "total_seconds": NUMBER},
        ("path", "line", "function", "calls", "self_seconds", "total_seconds"), closed=True), maxItems=20),
    "symbol_mapping": string(), "profile_unit": string(), "profile_source": string(), "source_sha256": SHA256,
    "scope": string(), "output_comparison": string(), "median_case_ns": NUMBER, "p95_case_ns": NUMBER,
    "query_length_min": integer(minimum=0), "query_length_max": integer(minimum=0), "executed_sql_queries": integer(minimum=0),
    "sample_results": arr(MAP)}, description="真实执行的候选聚合采样。旧preset可保留附加测量字段；不是生产原始profile。")
S["CodeRepairValidation"] = obj({"success": BOOL, "accepted": BOOL, "error_code": string(), "functional_regression": string(),
    "original_tests": integer(minimum=0), "frozen_contract_tests": integer(minimum=0), "output_parity_cases": integer(minimum=0),
    "pairs": arr(ref("RepairBenchmarkPair"), maxItems=6), "median_cpu_speedup": NUMBER, "improved_pairs": integer(minimum=0, maximum=6),
    "gate": ref("RepairPerformanceGate"), "root_cause_status": string(enum=["validated_in_controlled_python_benchmark", "validated_in_controlled_crm_benchmark", "validated_in_controlled_saber_benchmark", "not_validated"]),
    "scope": string(), "candidate_profile": ref("RepairCandidateProfile"), "elapsed_seconds": NUMBER,
    "job_key": string(), "source_revision": SHA256, "project_id": string(), "executor_contract": integer(enum=[3, 4]),
    "cache_hit": BOOL, "runner_image_id": string(pattern=r"^sha256:[0-9a-f]{64}$"), "network": string(const="none"),
    "uid": integer(const=10001), "credentials_mounted": {"type": "boolean", "const": False},
    "patch": arr(ref("RepairSourceEdit"), maxItems=6), "candidate_files": {"type": "object", "additionalProperties": SHA256},
    "output_contract": string(const="drop-python-project-v1"), "language": string(const="python"),
    "regression_test_validation": ref("RepairRegressionValidation"), "original_tests_unchanged": BOOL,
    "negative_control": ref("RepairNegativeControl"), "patch_bundle": ref("RepairPatchBundle"),
    "regression_tests": arr(obj({"path": string(), "sha256": SHA256}, ("path", "sha256"), closed=True), maxItems=2)},
    ("success", "accepted"), description="执行器真实test_patch结果。新增验证结构逐项定义，旧登记preset可保留附加测量字段；success与accepted仍须同时为true才可验收，不能用前端布尔值证明。")
S["CodeRepairReport"] = obj({"summary": string(maxLength=1800), "evidence_ids": arr(string(pattern=r"^S(?:[1-9]|1[0-2])$"), minItems=1),
    "locations": arr(obj({"path": string(), "line": integer(minimum=1), "reason": string()}, ("path", "line", "reason"), closed=True), minItems=1, maxItems=5),
    "validation": ref("CodeRepairValidation"), "source_revision": SHA256, "source_project": string(), "engine": string(const="model"),
    "model": string(), "patch_applied_to": string(const="isolated_snapshot"), "production_deployed": {"type": "boolean", "const": False},
    "agent_runtime": ref("AgentRuntime")},
    ("summary", "evidence_ids", "locations", "validation", "source_revision", "source_project", "engine", "model", "patch_applied_to", "production_deployed"), closed=True)
S["CodeRepair"] = obj({"id":ID,"requested_by":string(),"project_id":string(),"source_revision":string(pattern="^[0-9a-f]{64}$"),"goal":string(),"input_digest":string(),
    "status":string(enum=["pending","running","success","failed","canceled"]),"trace":arr(MAP,maxItems=12),"report":{"anyOf":[ref("CodeRepairReport"),{"type":"null"}]},"error_code":string(),"created_at":DATE,"updated_at":DATE})
S["Feedback"] = obj({"id": ID, "suggestion_id": ID, "user_id": string(), "rating": string(enum=["useful", "not_useful"]),
    "comment": string(), "created_at": DATE, "updated_at": DATE}, ("id", "suggestion_id", "user_id", "rating", "comment"))
S["Audit"] = obj({"id": ID, "user_id": string(), "action": string(), "resource_type": string(), "resource_id": string(),
    "request_id": string(), "detail": MAP, "created_at": DATE}, ("id", "user_id", "action", "resource_type", "resource_id", "request_id", "detail", "created_at"))

# 直接复制规范化 Schema，不逐个 profiler 重写选项定义，避免多处契约漂移。
for kind in CATALOG:
    S[f"NormalizedParameters_{kind['id']}"] = copy.deepcopy(kind["parameter_schema"])


def page(schema):
    return obj({"items": arr(schema), "total": integer(minimum=0), "page": integer(minimum=1),
        "page_size": integer(minimum=0)}, ("items", "total", "page", "page_size"))


def envelope(schema):
    return obj({"request_id": string(), "data": schema, "error": {"type": "null"}}, ("request_id", "data", "error"))


def query(name, schema=None, description=""):
    return {"name": name, "in": "query", "required": False, "schema": schema or string(), "description": description}


P = {}

# 质量摘要的字段真源是共享Schema；只改写引用位置以嵌入OpenAPI。
quality_schema = json.loads((ROOT / "contracts/quality-run.schema.json").read_text(encoding="utf-8"))
S["QualityRecordID"] = quality_schema["$defs"]["id"]
quality_schema.pop("$schema")
quality_schema.pop("$defs")
S["QualityRun"] = json.loads(json.dumps(quality_schema).replace("#/$defs/id", "#/components/schemas/QualityRecordID"))
S["QualityRecord"] = obj({"run":ref("QualityRun"),"record_sha256":string(pattern="^[a-f0-9]{64}$")},("run","record_sha256"),closed=True)


def operation(method, path, handler, summary, schema, *, tag="Tasks", body=None, code=200, description="", public=False,
              idem=False, queries=(), paged=False):
    params = []
    import re
    for name in re.findall(r"\{([^}]+)\}", path):
        bigint = name in {"artifact_id", "share_id", "suggestion_id", "run_id", "followup_id"} or (
            name == "id" and any(f"/{segment}/{{id}}" in path for segment in
                ("tasks", "schedules", "composites", "investigations", "code-repairs", "diagnostic-cases", "diagnostic-playbooks")))
        params.append({"name": name, "in": "path", "required": True, "schema": ID if bigint else string(minLength=1)})
    if method != "get" and not public:
        params.append({"$ref": "#/components/parameters/CSRFToken"})
    if idem:
        params.append({"$ref": "#/components/parameters/IdempotencyKey"})
    if paged:
        params += [query("page", integer(minimum=1, maximum=100000, default=1)),
                   query("page_size", integer(minimum=1, maximum=100, default=20))]
    params += list(queries)
    op = {"operationId": handler, "summary": summary, "tags": [tag], "description": description,
          "responses": {str(code): {"description": "Success", "content": {"application/json": {"schema": envelope(schema)}}},
                        "default": {"$ref": "#/components/responses/Error"}}}
    if params:
        op["parameters"] = params
    if body is not None:
        op["requestBody"] = {"required": True, "content": {"application/json": {"schema": body}}}
    if public:
        op["security"] = []
    P.setdefault(path, {})[method] = op
    return op


BASE = "/api/v1"


def add(method, path, *args, **kwargs):
    return operation(method, BASE + path, *args, **kwargs)


add("get", "/quality/runs", "qualityRuns", "管理员只读质量执行记录", page(ref("QualityRecord")), tag="Quality",
    description="平台管理员专用；最多128份各32KiB不可变摘要，时间倒序。未配置目录返回空列表，损坏记录使整目录失败关闭。不会执行测试或返回原始profile。")
quality_detail = add("get", "/quality/runs/{run_id}", "qualityRun", "读取质量记录及原文件摘要", ref("QualityRecord"), tag="Quality",
    description="平台管理员专用；run_id为受限名称，不是任务bigint；SHA对应服务器原JSON文件字节，不是签名。")
quality_detail["parameters"] = [{"name":"run_id","in":"path","required":True,"schema":ref("QualityRecordID")}]

deleted = obj({"deleted": BOOL}, ("deleted",))
enabled = obj({"enabled": BOOL}, ("enabled",), closed=True)
permission_input = obj({"permission": PERMISSION}, ("permission",), closed=True)
add("post", "/auth/login", "login", "Local password login", ref("Auth"), tag="Authentication", body=ref("Login"), public=True,
    description="12-hour HttpOnly SameSite=Lax session; Secure according to HTTPS deployment. Login Origin must match PUBLIC_URL. PostgreSQL-shared limit 10 requests/IP/15min (all attempts).")
add("get", "/auth/providers", "providers", "Public configured feature flags", ref("AuthProviders"), tag="Authentication", public=True,
    description="Only provider display names and enabled flags. Never returns issuer, AI endpoint, credentials or internal addresses.")
for path, handler, summary in [("/auth/oidc/start", "oidcStart", "Begin OIDC authorization code + PKCE"),
                                ("/auth/oidc/callback", "oidcCallback", "Complete OIDC browser callback")]:
    op = add("get", path, handler, summary, MAP, tag="Authentication", public=True,
        description="302 browser redirect. Configured OIDC validates discovery/JWKS signature, issuer/audience/azp/expiry, one-use browser-bound state, nonce and PKCE. New identities default to viewer. Disabled returns 503 OIDC_UNAVAILABLE.",
        queries=[query("state"), query("code")] if handler == "oidcCallback" else [])
    del op["responses"]["200"]
    op["responses"]["302"] = {"description": "Redirect to IdP (start) or / (successful callback).",
        "headers": {"Location": {"schema": string(format="uri-reference"), "description": "Browser redirect location."},
                    "Set-Cookie": {"schema": string(), "description": "HttpOnly session or ten-minute browser binding cookie."}}}
add("get", "/auth/me", "currentUser", "Current identity and CSRF token", ref("Auth"), tag="Authentication")
add("post", "/auth/logout", "logout", "Revoke current session", obj({"logged_out": BOOL}, ("logged_out",)), tag="Authentication")
add("get", "/overview", "overview", "Authorized resource overview", ref("Overview"), tag="Overview")
add("get", "/agents", "agents", "List authorized Agents", page(ref("Agent")), tag="Agents", paged=True,
    queries=[query("search"), query("status", string(enum=["online", "degraded", "offline", "disabled"]))],
    description="Repository reads at most 500 authorized Agents, then applies search/status and pagination. total is within that bounded set, not an unbounded fleet count.")
add("get", "/agents/{id}", "agent", "Authorized Agent detail", ref("Agent"), tag="Agents")
add("patch", "/agents/{id}", "setAgentEnabled", "Enable or disable Agent", ref("Agent"), tag="Agents", body=enabled,
    description="Platform administrator only. Persists disabled status and audit atomically; disabled Agent cannot self-enable via registration/heartbeat or claim new tasks.")
add("get", "/agents/{id}/metrics", "agentMetrics", "Recent authorized Agent metrics", obj({"items": arr(ref("AgentMetric"))}, ("items",)),
    tag="Agents", description="Last hour, up to 720 real persisted samples; no generated interpolation.")
add("get", "/agents/{id}/events", "agentEvents", "Recent persisted Agent events", page(ref("AgentEvent")), tag="Agents",
    description="Agent scope authorization, latest 100 rows ordered occurred_at/id descending. No pagination cursor is currently exposed.")
add("get", "/agents/{id}/diagnostics", "agentDiagnostics", "Bounded control-plane diagnostics", {"oneOf": [MAP, arr(MAP)]}, tag="Agents",
    queries=[query("category", string(enum=["resources", "capabilities", "processes", "events"], default="resources"))],
    description="Agent scope authorization and 4s mTLS RPC timeout. processes/events return arrays and request at most 100 entries. resources/capabilities return the persisted bounded JSON object. Returns DataJson directly; Core generated_at/truncated metadata is not exposed by this HTTP adapter. Not a complete inventory, no arbitrary paths or commands.")
add("get", "/task-kinds", "kinds", "Shared TaskKind catalog", arr(ref("TaskKind")), tag="Tasks", queries=[query("agent_id")],
    description="All 18 catalog entries returned. With authorized agent_id, annotates available; does not filter offline-import kinds. Read parameter_schema dynamically rather than duplicating profiler options.")
add("get", "/tasks", "tasks", "List authorized tasks", page(ref("Task")), paged=True,
    queries=[query(k) for k in ("status", "kind", "agent_id", "search")], description="status matches collection_status OR analysis_status. Soft-deleted rows are omitted even for administrators.")
op = add("post", "/tasks", "createTask", "Create live or imported task", ref("Task"), body=ref("CreateTask"), code=201, idem=True,
    description="Operator/group_admin/platform_admin (legacy admin) and authorized Agent/group scope. 201 new, 200 exact idempotent replay, 409 same key/different normalized input. Shared owner quota 10 active tasks; one concurrent creation request per API process bounds import memory. Retry a 429/temporary storage error with the same key.")
op["responses"]["200"] = copy.deepcopy(op["responses"]["201"])
add("get", "/tasks/{id}", "task", "Task detail with server-calculated permissions", ref("TaskDetail"))
add("delete", "/tasks/{id}", "deleteTask", "Soft-delete terminal task", deleted,
    description="Owner, task-group administrator or platform administrator. Preserve artifacts/history and original retention. Nonterminal tasks return 409. operate share alone cannot delete.")
add("post", "/tasks/{id}/cancel", "cancelTask", "Cancel current collection or analysis", ref("Task"),
    description="Requires task operate permission and write-capable global role. Persists cancellation/event/outbox, fenced to collection attempt. No request body.")
add("post", "/tasks/{id}/retry", "retryTask", "New collection attempt for terminal task", ref("Task"), idem=True,
    description="New collection attempt, preserving history. Task operate scope and original owner's shared quota apply. Imported data cannot be recreated by an Agent retry; use a new import. No request body.")
add("post", "/tasks/{id}/reanalyze", "reanalyze", "Queue another analysis of existing RAW", obj({"id": ID, "analysis_status": string(const="PENDING")}, ("id", "analysis_status")), code=202,
    description="Platform administrator only. COLLECTED plus terminal success/failed analysis and readable unexpired RAW required. Archives old run/result/suggestions, clears current result, monotonically increases future run IDs. Refuses active pending/running/retry lease.")
add("get", "/tasks/{id}/events", "events", "Persisted task events after sequence", page(ref("Event")),
    queries=[query("after", string(pattern="^[0-9]+$", default="0"))], description="Current task read scope, up to 500 rows ascending by sequence. Returned total is this bounded batch length, not full history count.")
op = add("get", "/tasks/{id}/events/stream", "stream", "Resumable task and AI event stream", MAP,
    description="text/event-stream, transport event name task_status, decimal sequence id, Event JSON as data. Last-Event-ID replays persisted later events. 1s poll/15s keepalive; session and current task scope rechecked every poll. Remains connected after task terminal state for AI events. SUGGESTION_DELTA payload {text,ai_request_id,streamed:true}; CREATED/FAILED includes request id.")
op["parameters"].append({"name": "Last-Event-ID", "in": "header", "required": False, "schema": string(pattern="^[0-9]+$")})
op["responses"]["200"] = {"description": "SSE stream", "content": {"text/event-stream": {"schema": string()}}}
add("get", "/tasks/{id}/artifacts", "artifacts", "Authorized artifact metadata", page(ref("Artifact")))
add("get", "/tasks/{id}/artifacts/{artifact_id}/download", "download", "Authorize a short artifact download", obj({"url": string(), "expires_at": DATE}, ("url", "expires_at")),
    description="Rechecks task scope and artifact ready/download_allowed/retention state, audits each grant. 410 if expired. URL valid at most 300s or remaining retention; short URL must not be logged. No long-lived object-store credential is sent to browser.")
add("get", "/tasks/{id}/result", "result", "Current successful analysis result", ref("AnalysisResult"),
    description="409 RESULT_NOT_READY until current collection attempt's analysis is success. Never returns previous analysis result while reanalysis pending.")
add("get", "/tasks/{id}/partial-result", "partialResult", "Current failed analysis parse checkpoint", ref("PartialAnalysisResult"),
    description="Task read authorization. Only current COLLECTED/FAILED task, latest collection attempt, failed current job/run, and matching current RAW id/SHA may return a strictly validated <=16KiB parse checkpoint. 409 PARTIAL_RESULT_NOT_READY when absent, stale or unavailable. Completeness remains partial; no success status, full flamegraph, original tool report, internal work paths, AI input or old-run fallback. missing_stages lists outputs omitted from this checkpoint, not proof that later processing never ran. New run/lease claim clears the checkpoint; successful commit replaces it. Contract and imported synthetic fixture: contracts/partial-result.schema.json and contracts/fixtures/partial-result-v1.json.")
add("get", "/tasks/{id}/suggestions", "suggestions", "Current rule and model suggestions", page(ref("Suggestion")))
add("get", "/tasks/{id}/shares", "shares", "Task sharing grants", page(ref("TaskShare")), tag="Groups", description="Task manager only, not every reader.")
add("post", "/tasks/{id}/shares", "createShare", "Share a task with one user or group", ref("TaskShare"), tag="Groups", code=201, body=ref("CreateShare"),
    description="Owner, task-group administrator or platform administrator. One grant per task/principal. read share grants all read surfaces; operate adds cancel/retry but not grant management/delete.")
add("delete", "/tasks/{id}/shares/{share_id}", "deleteShare", "Revoke a task grant", deleted, tag="Groups", description="Task manager only.")
add("get", "/groups", "groups", "Authorized resource groups", page(ref("Group")), tag="Groups", description="All visible groups; response uses a List envelope with page=1 and page_size equal to returned count. This endpoint currently has no database limit or pagination.")
add("post", "/groups", "createGroup", "Create resource group", ref("Group"), tag="Groups", code=201,
    body=obj({"name": string(minLength=1, maxLength=120), "description": string(maxLength=1000)}, ("name",), closed=True), description="Platform administrator only.")
add("get", "/groups/{id}", "group", "Group members and Agent grants", ref("GroupDetail"), tag="Groups")
add("put", "/groups/{id}/members/{user_id}", "putMember", "Set group membership role", obj({"user_id": string(), "role": GROUP_ROLE}, ("user_id", "role")),
    tag="Groups", body=obj({"role": GROUP_ROLE}, ("role",), closed=True), description="That group's group_admin or platform administrator.")
add("delete", "/groups/{id}/members/{user_id}", "deleteMember", "Remove group membership", deleted, tag="Groups", description="That group's group_admin or platform administrator.")
add("put", "/groups/{id}/agents/{agent_id}", "putAgentGrant", "Set group Agent access", ref("AgentGrant"), tag="Groups", body=permission_input,
    description="That group's group_admin or platform administrator. Group admin may only delegate existing read/operate Agent rights, never elevate read to operate.")
add("delete", "/groups/{id}/agents/{agent_id}", "deleteAgentGrant", "Remove group Agent grant", deleted, tag="Groups", description="That group's group_admin or platform administrator.")
add("get", "/composites", "composites", "Authorized composite DAG runs", page(ref("CompositeRun")), tag="Composites", description="Latest 100 visible runs; bounded list, no cursor pagination.")
op = add("post", "/composites", "createComposite", "Create persistent composite DAG", ref("CompositeRun"), tag="Composites", body=ref("CreateComposite"), code=201, idem=True,
    description="No import or nested composites, maximum 16 live nodes. Node dependencies persist; failed prerequisites skip dependents. Required failure fails run. allow_partial_success admits optional failure only when some node succeeds. Overall deadline cancels unfinished child attempts. 200 idempotent replay; nodes/report are retrieved separately from detail.")
op["responses"]["200"] = copy.deepcopy(op["responses"]["201"])
add("get", "/composites/{id}", "composite", "DAG detail and bounded combined report", ref("CompositeDetail"), tag="Composites",
    description="Each node result is bounded summary; load full tree from child task result. Report references actual child results and up to 10 suggestions per node. Different profiler weights are not added.")
add("post", "/composites/{id}/cancel", "cancelComposite", "Cancel unfinished DAG children", obj({"id": ID, "status": string(const="CANCELED")}, ("id", "status")), tag="Composites")
add("get", "/schedules", "schedules", "Authorized schedules", page(ref("Schedule")), tag="Schedules", description="All visible schedules, newest first. List envelope page=1; currently no database limit or pagination.")
add("post", "/schedules", "createSchedule", "Create immutable-template Cron or interval schedule", ref("Schedule"), tag="Schedules", body=ref("CreateSchedule"), code=201,
    description="Write-capable role and requested group scope. Standard five-field Cron + full IANA zones; DST nonexistent local times skipped, repeated hours have two actual occurrences. DB row locks and schedule_id/scheduled_at uniqueness prevent duplicate runs.")
add("patch", "/schedules/{id}", "toggleSchedule", "Enable or pause schedule", obj({"id": ID, "enabled": BOOL}, ("id", "enabled")), tag="Schedules", body=enabled,
    description="Owner, group operator/admin where group-visible, or platform administrator, plus write-capable global role. Only enabled can change; template is immutable.")
add("delete", "/schedules/{id}", "deleteSchedule", "Delete authorized schedule", deleted, tag="Schedules")
add("get", "/schedules/{id}/records", "scheduleRecords", "Latest 100 schedule run records", page(ref("ScheduleRecord")), tag="Schedules")
add("get", "/rules", "rules", "Diagnostic rules and versions", page(ref("Rule")), tag="Suggestions", description="All current rules; login required. List envelope, page=1.")
add("patch", "/rules/{key}", "updateRule", "Update diagnostic rule", ref("Rule"), tag="Suggestions", body=ref("UpdateRule"), description="Platform administrator only. Current keys allocation, lock, gc.")
add("post", "/tasks/{id}/suggestions/ai", "requestAI", "Request explicitly authorized model assistance", ref("AIRequest"), tag="Suggestions", code=202,
    body=obj({"consent": {"type": "boolean", "const": True}}, ("consent",), closed=True),
    description="Task operate scope, explicit consent and successful result required. Only numeric statistics and Top20 hashed symbols sent to configured provider; no raw profile, path, PID or identity. Deduped by task/attempt/input_digest. Provider stream bounded to 30s/256KiB/256chunks and final 8KiB plain text; each persisted chunk fenced by current lease/attempt/authorization. Nonstream fallback explicitly streamed=false. Unconfigured returns 503 AI_UNAVAILABLE.")
add("get", "/tasks/{id}/suggestions/requests", "aiRequests", "Recent persistent model request states", page(ref("AIRequest")), tag="Suggestions", description="Task read scope, latest 50; use after refresh to recover status and resume task SSE.")
add("post", "/tasks/{id}/diagnoses", "requestDiagnosis", "Queue task-bound diagnostic Agent", ref("DiagnosisRun"), tag="Suggestions", code=202, idem=True, body=ref("DiagnosisInput"),
    description="当前任务操作权限和完整成功分析结果；按用户/幂等键绑定问题、来源、知识模式及可选业务服务。每用户最多3条活动诊断。新报告v2最多8次普通模型请求加1次纯格式纠错、14次工具回调，另加E1最多15条trace；旧协议保留4+1模型与7回调。启用AgentScope检查点时所有租约会话共享原150秒累计预付活跃预算和调用账本；最多3次领取不是预算翻倍，离线等待不计。来源、权限和租约变化立即拒绝恢复；未确认外部动作不能盲重放。")
add("get", "/tasks/{id}/diagnoses", "diagnoses", "Latest20 durable diagnostic runs", page(ref("DiagnosisRun")), tag="Suggestions",
    description="Task read scope, including read shares. Historical runs retain attempt and analysis job/run binding. No credentials, hidden thoughts, raw profiles or provider bodies.")
add("get", "/diagnostic-skills", "diagnosticSkills", "版本化诊断方法目录", page(ref("DiagnosticSkill")), tag="Suggestions",
    description="登录后可读，内置目录随版本发布；方法不授予额外工具或采集权限。")
add("get", "/diagnostic-cases", "diagnosticCases", "本人复核的诊断案例", page(ref("DiagnosticCase")), tag="Suggestions",
    description="最多50条，逐条重验当前来源与操作权限。可按正整数task_id过滤；不向其他用户共享个人经验。")
P["/api/v1/diagnostic-cases"]["get"].setdefault("parameters", []).append({"name": "task_id", "in": "query", "schema": ID})
add("post", "/tasks/{id}/diagnoses/{run_id}/cases", "createDiagnosticCase", "复核保存有来源的诊断案例", ref("DiagnosticCase"), tag="Suggestions", code=201, idem=True,
    body=ref("DiagnosticCaseInput"), description="当前任务操作权限、成功且未过期的诊断。保存与审计原子提交，幂等键绑定载荷。人工纠正和隔离修复验证分开标识；默认90天有效。")
add("post", "/diagnostic-cases/{id}/revoke", "revokeDiagnosticCase", "撤销本人经验的后续复用", obj({"revoked": BOOL}, ("revoked",)), tag="Suggestions",
    body=obj(closed=True), description="本人、当前来源操作权限；撤销与审计原子提交，既有测量和报告保留。")
add("post", "/tasks/{id}/diagnoses/{run_id}/cancel", "cancelDiagnosis", "Cancel diagnostic writes", obj({"canceled": BOOL}, ("canceled",)), tag="Suggestions",
    description="Task operate scope. Pending/running -> failed DIAGNOSIS_CANCELED; atomic event/audit. Late worker writes are fenced. Already terminal returns409.")
add("post", "/tasks/{id}/diagnoses/{run_id}/collect", "collectDiagnosis", "Confirm controlled follow-up collection", ref("Task"), tag="Suggestions", code=201, idem=True, body=ref("DiagnosisCollectInput"),
    description="Task operate scope and explicit confirm=true. Successful current diagnosis with server-validated plan only; original task is locked. Reuses original target identity and normal task schema/capability/auth policy. Atomic task/provenance event/audit; replay200, new201. Imported data requires manual import.")
P["/api/v1/tasks/{id}/diagnoses/{run_id}/collect"]["post"]["responses"]["200"] = copy.deepcopy(P["/api/v1/tasks/{id}/diagnoses/{run_id}/collect"]["post"]["responses"]["201"])
add("get","/tasks/{id}/verifications","verifications","Automatic candidate diagnosis and comparison records",page(ref("VerificationRun")),tag="Suggestions",description="Read authorization on requested task; records require operate authorization on both tasks. Automatic workflow is created atomically with confirmed collection. No causal claim from repetition alone.")
add("post","/investigations","requestInvestigation","Plan a capability-aware collection from natural language",ref("Investigation"),tag="Suggestions",code=202,idem=True,body=ref("InvestigationInput"),description="Operator identity, actual Agent/target authorization and consent. Immutable PID/start/UID binding. Model plans from actual catalog; review before any collection. At most 3 active investigations per user.")
add("get","/investigations","investigations","Latest30 scoped investigations",page(ref("Investigation")),tag="Suggestions")
add("get","/investigations/{id}","investigation","Read scoped investigation",ref("Investigation"),tag="Suggestions")
add("get", "/diagnostic-data-sources", "diagnosticDataSources", "当前节点可用的诊断业务信号目录", page(ref("TelemetryCatalogItem")), tag="Suggestions",
    queries=[{**query("agent_id", string(minLength=1, maxLength=100), "必须为当前用户可操作的真实登记Agent。"), "required": True}],
    description="需要登录并逐次校验Agent操作权限。返回已登记服务的安全目录；列表page=1，未配置数据源时items为空。没有URL/查询语句/凭据的浏览器登记入口。")
add("get", "/diagnostic-playbooks", "diagnosticPlaybooks", "列出有权限查看的有效方法版本", page(ref("DiagnosticPlaybook")), tag="Suggestions",
    description="最新100条有效版本，按logical_key升序、version降序、id升序。本人草稿及已发布团队版本可见；平台管理员可见全部有效版本。列表可见不等于诊断可执行，运行时还重验团队、项目、TaskKind与工具边界。")
add("post", "/diagnostic-playbooks", "createDiagnosticPlaybook", "创建不可变方法草稿", ref("DiagnosticPlaybook"), tag="Suggestions", code=201,
    body=ref("DiagnosticPlaybookInput"), description="当前可写用户；团队方法需团队管理权，项目方法需平台管理员及已登记项目。owner/logical_key/version为内容幂等身份；同版本不同内容或作用域返回冲突，不使用Idempotency-Key替代版本。")
add("post", "/diagnostic-playbooks/{id}/review", "reviewDiagnosticPlaybook", "明确复核后发布或退役方法版本", ref("DiagnosticPlaybook"), tag="Suggestions",
    body=ref("DiagnosticPlaybookReview"), description="平台管理员，且review_confirmed=true。只能发布尚未过期的draft，published不能原地修改；retired不再用于后续诊断。状态变化与审计在同一事务。")
add("get", "/investigations/{id}/followups", "investigationFollowups", "列出调查的待审及已处理补证分支", page(ref("InvestigationFollowup")), tag="Suggestions",
    description="重新授权调查与目标；按id返回至多两个分支，不向其他调查借用来源诊断或任务。")
add("post", "/investigations/{id}/followups/{followup_id}/decision", "decideInvestigationFollowup", "审阅后接受或拒绝补证分支", ref("InvestigationFollowup"), tag="Suggestions",
    body=ref("InvestigationFollowupDecision"), description="当前可写且可操作该调查的用户；reviewed=true。confirm=true仅入队，来源诊断、目标身份、真实采集能力和20分钟调查预算再次校验；同决定可重放，冲突决定拒绝。模型不能自动确认或直接创建采集任务。")
add("post","/investigations/{id}/confirm","confirmInvestigation","Confirm initial plan and bounded automatic verification",obj({"confirmed":BOOL}),tag="Suggestions",body=ref("DiagnosisCollectInput"))
add("post","/investigations/{id}/cancel","cancelInvestigation","Cancel investigation and linked pending work",obj({"canceled":BOOL}),tag="Suggestions",body=obj(closed=True))
add("get","/code-projects","codeProjects","管理员登记的不可变源码项目目录",obj({"projects":arr(ref("CodeProject"))}, ("projects",), closed=True),tag="Suggestions",description="仅平台管理员。通过私有Unix执行器读取已登记版本与受限能力；通用Python采用manifest-python-v1，旧CRM/Saber preset保留原验收。浏览器不取得源码根目录、argv、Docker socket或生产配置。")
add("post","/code-repairs","requestCodeRepair","Queue version-bound source diagnosis and isolated repair",ref("CodeRepair"),tag="Suggestions",code=202,idem=True,body=ref("CodeRepairInput"),description="Platform administrator; explicit source/model/sandbox consent. One active per user. Exact source revision, bounded tools and actual tests required; generated code only runs in a separate networkless UID10001 sandbox.")
add("get","/code-repairs","codeRepairs","Latest20 administrator-only source runs",page(ref("CodeRepair")),tag="Suggestions")
add("get","/code-repairs/{id}","codeRepair","Administrator-only source run",ref("CodeRepair"),tag="Suggestions")
add("post","/code-repairs/{id}/cancel","cancelCodeRepair","Fence canceled source run",obj({"canceled":BOOL}),tag="Suggestions",body=obj(closed=True),description="Fences later model steps/report writes and closes tool I/O; the broker observes disconnect and stops only its verified sandbox container. Hard process termination/restart recovery remains bounded by the 240s sandbox limit.")
add("post", "/tasks/{id}/suggestions/{suggestion_id}/feedback", "feedback", "Upsert current user's feedback", ref("Feedback"), tag="Suggestions", body=ref("FeedbackInput"),
    description="Task read scope suffices; viewer may submit feedback. One row per suggestion/user.")
add("get", "/tasks/{id}/suggestions/{suggestion_id}/feedback", "feedbackList", "Latest 100 suggestion feedback rows", page(ref("Feedback")), tag="Suggestions", description="Task read scope. Returns feedback for this task's suggestion.")
add("get", "/users", "users", "List up to 200 local and OIDC users", page(ref("User")), tag="Administration", description="Platform administrator only.")
add("post", "/users", "createUser", "Create local user", ref("User"), tag="Administration", body=ref("CreateUser"), code=201, description="Platform administrator only. bcrypt cost12; password12..72 bytes.")
add("patch", "/users/{id}", "updateUser", "Update user role, display name or local password", ref("User"), tag="Administration", body=ref("UpdateUser"),
    description="Platform administrator only. Transaction lock prevents removal of last platform admin. Password change revokes all sessions for this user. May assign scope/role after first OIDC login.")
add("get", "/audit-logs", "audits", "Paged administrator audit trail", page(ref("Audit")), tag="Administration", paged=True, description="Platform administrator only; runtime DB audit privileges are append-only INSERT/SELECT.")
add("post", "/client-errors", "clientError", "Bounded client failure telemetry", obj({"accepted": BOOL}, ("accepted",)), tag="Operations", code=202,
    body=obj({"code": string(enum=["FLAME_LAYOUT_FAILED", "WEB_RENDER_FAILED"])}, ("code",), closed=True),
    description="Login, CSRF and shared write limiter required. No arbitrary message/path/raw/stack fields; increments drop_web_errors_total{code}.")
for path, handler, summary in [("/health/live", "liveness", "Process liveness"), ("/health/ready", "ready", "Database/storage/control readiness")]:
    op = operation("get", path, handler, summary, MAP, tag="Operations", public=True)
    op["responses"]["200"]["content"]["application/json"]["schema"] = obj({"status": string()}, ("status",))
    if path.endswith("ready"):
        op["responses"]["503"] = {"description": "Dependency unavailable", "content": {"application/json": {"schema": obj({"status": string(const="not_ready"), "dependency": string(enum=["database", "storage", "control"])}, ("status", "dependency"))}}}
op = operation("get", "/metrics", "prometheus", "Private Prometheus scrape", MAP, tag="Operations", public=True,
    description="Private network only at deployment layer; no session cookie. Real SQL gauges and HTTP/gRPC histograms, no profile or credentials. See API development notes for metric names.")
op["responses"]["200"] = {"description": "Prometheus exposition", "content": {"text/plain": {"schema": string()}}}

DOC = {"openapi": "3.1.0", "info": {"title": "Drop Diagnostics API", "version": "2.0.0",
    "description": "2026-10-21对照Gin handler、共享TaskKind与SQL迁移审阅。包括本地/OIDC身份、任务/节点授权、持久采集分析、诊断事实v2、受审方法与案例、业务信号目录、待审补证及隔离源码修复。JSON业务响应使用request_id/data/error；health/metrics/重定向/SSE例外。JSON输入闭合；工具与数据源由控制面登记，不能从模型参数取得任意URL、查询、命令或存储凭据。静态Schema与导入夹具不代表真实模型、生产或外部数据源已验收。"},
    "servers": [{"url": "/", "description": "Same-origin deployment. UTC wire timestamps; display timezone may differ."}],
    "security": [{"SessionCookie": []}], "tags": [{"name": t} for t in ["Authentication", "Overview", "Agents", "Tasks", "Groups", "Composites", "Schedules", "Suggestions", "Administration", "Operations"]],
    "paths": P, "components": {"securitySchemes": {"SessionCookie": {"type": "apiKey", "in": "cookie", "name": "drop_session"}},
    "parameters": {"CSRFToken": {"name": "X-CSRF-Token", "in": "header", "required": True, "schema": string(), "description": "Token returned by login or auth/me. Required for every authenticated mutation, including logout/feedback/client telemetry."},
                   "IdempotencyKey": {"name": "Idempotency-Key", "in": "header", "required": True, "schema": string(minLength=8, maxLength=128), "description": "Opaque key, no CR/LF/NUL. Scoped by owner and operation. Exact replay is safe; changed normalized request conflicts."}},
    "responses": {"Error": {"description": "Business error: 400 invalid input; 401 unauthenticated; 403 forbidden/CSRF/target; 404 absent or hidden; 409 state/idempotency/result; 410 expired artifact; 429 rate/quota; 503 offline capability/storage/provider; 500 internal. No raw SQL/profile/secrets.",
                             "content": {"application/json": {"schema": ref("ErrorEnvelope")}}}}, "schemas": S}}


def main():
    destination = ROOT / "docs/openapi.yaml"
    destination.write_text("# 由api/tools/sync_openapi.py生成，并与实际路由及共享Schema核对。\n" +
                           yaml.safe_dump(DOC, allow_unicode=True, sort_keys=False, width=110), encoding="utf-8")
    print(f"Wrote {destination.relative_to(ROOT)}: {sum(len(v) for v in P.values())} operations, {len(S)} schemas")


if __name__ == "__main__":
    main()
