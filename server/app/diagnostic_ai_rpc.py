"""Internal gRPC adapter for the Drop Insight V2 diagnosis domain.

The public HTTP boundary belongs to the Go API. This module deliberately has
no public HTTP dependency: it receives an internal method/path envelope,
validates it with the domain Pydantic schemas, and invokes the existing Python
diagnosis and Skill services inside the diagnosis-worker process.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from concurrent import futures
from pathlib import Path
from urllib.parse import parse_qs

import grpc
from pydantic import ValidationError

from server.app.drop_insight.exploration_tree import get_live_exploration_tree
from server.app.drop_insight.diagnosis_agent import get_agent_runtime_status
from server.app.drop_insight.fault_plaza import (
    FaultPlazaError,
    get_fault_plaza,
    start_fault_scenario,
    stop_fault_scenario,
)
from server.app.drop_insight.frozen_replay_showcase import (
    FrozenReplayShowcaseNotFound,
    get_frozen_replay_catalog,
    start_frozen_replay_run,
)
from server.app.drop_insight.schemas import (
    AddEvidenceRequest,
    ApproveDiagnosticExperimentRequest,
    AssignDiagnosticExperimentRequest,
    ClarifyDiagnosisRequest,
    CreateDiagnosticExperimentRequest,
    CreateDiagnosisRequestV2,
    CreateHypothesisRequest,
    CreateToolCallRequest,
    DecideToolCallRequest,
    DeleteOperatorPreferenceRequest,
    GenerateReportRequest,
    ImportTaskEvidenceRequest,
    InterveneDiagnosisRequest,
    PreviewToolCallRequest,
    PutOperatorPreferenceRequest,
    QuarantineDiagnosticSkillRequest,
    RecordDiagnosticExperimentOutcomeRequest,
    RecordSkillCampaignValidationRequest,
    RunPlannerRequest,
    SubmitDiagnosisFeedbackRequest,
    StartFaultScenarioRequest,
    StartFrozenReplayRequest,
    UpdateToolCallArgumentsRequest,
    VerifyFixRequest,
)
from server.app.drop_insight.service import (
    add_evidence,
    advance_diagnosis,
    clarify_diagnosis,
    create_diagnosis,
    create_hypothesis,
    decide_tool_call,
    delete_diagnosis,
    discover_target_candidates,
    generate_report,
    get_budget_usage,
    get_diagnosis,
    import_task_evidence,
    intervene_diagnosis,
    list_evidence,
    list_events,
    list_knowledge_retrievals,
    list_feedback,
    list_fix_verifications,
    list_hypotheses,
    list_diagnosis_interventions,
    list_reports,
    list_tool_calls,
    list_diagnoses,
    preview_tool_call,
    request_tool_call,
    run_diagnosis_planner,
    submit_diagnosis_feedback,
    update_tool_call_arguments,
    verify_diagnosis_fix,
)
from server.app.drop_insight.showcase import get_mentor_complex_showcase
from server.app.drop_insight.operator_memory import (
    delete_operator_preference,
    list_operator_preferences,
    put_operator_preference,
)
from server.app.drop_insight.skill_experiments import (
    approve_experiment_rollout,
    assign_experiment_diagnosis,
    create_experiment,
    evaluate_experiment,
    list_experiments,
    record_experiment_outcome,
    summarize_experiment,
)
from server.app.drop_insight.skill_evolution import (
    create_candidate_from_diagnosis,
    evaluate_skill,
    get_skill,
    list_activations,
    list_skills,
    publish_skill,
    quarantine_skill,
    record_campaign_validation,
    rollback_skill,
)
from server.app.drop_insight.tools import TOOLS
from server.app.generated import diagnostic_ai_pb2, diagnostic_ai_pb2_grpc
from server.app.logging_utils import bind_traceparent, log_event, reset_traceparent


@dataclass(frozen=True)
class _Result:
    status: int
    body: dict


def _ok(data, status: int = 200) -> _Result:
    return _Result(status, {"code": 0, "message": "ok", "data": data})


def _error(status: int, detail: str) -> _Result:
    return _Result(status, {"detail": detail})


def _body(raw: str) -> dict:
    if not raw.strip():
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("request body must be a JSON object")
    return value


def _item(value):
    return value.to_dict() if hasattr(value, "to_dict") else value


def _items(values) -> dict:
    return {"items": [_item(value) for value in values]}


def _match(path: str, pattern: str) -> tuple[str, ...] | None:
    matched = re.fullmatch(pattern, path)
    return matched.groups() if matched else None


def dispatch(method: str, path: str, query: str, raw_body: str, principal: str) -> _Result:
    """Dispatch one trusted Go-to-worker RPC to the V2 domain service."""

    method = method.upper().strip()
    path = "/" + path.strip().lstrip("/")
    principal = principal.strip() or "local-anonymous"
    params = parse_qs(query, keep_blank_values=False)

    if method == "GET" and path == "/agent-runtime/status":
        return _ok(get_agent_runtime_status())
    if method == "GET" and path == "/showcases/mentor-complex":
        return _ok(get_mentor_complex_showcase())
    if method == "GET" and path == "/showcases/fault-plaza":
        return _ok(get_fault_plaza())
    if method == "GET" and path == "/showcases/lats-replays":
        return _ok(get_frozen_replay_catalog())
    ids = _match(path, r"/showcases/lats-replays/([^/]+)/runs")
    if ids and method == "POST":
        request = StartFrozenReplayRequest.model_validate(_body(raw_body))
        try:
            value = start_frozen_replay_run(
                ids[0], request.client_run_id, principal=principal
            )
        except FrozenReplayShowcaseNotFound as exc:
            return _error(404, str(exc))
        return _ok(value, status=201 if value.get("created") else 200)
    ids = _match(path, r"/showcases/fault-plaza/([^/]+)/(start|stop)")
    if ids and method == "POST":
        scenario_id, action = ids
        try:
            if action == "start":
                request = StartFaultScenarioRequest.model_validate(_body(raw_body))
                return _ok(start_fault_scenario(scenario_id, request.duration_seconds))
            if _body(raw_body):
                raise ValueError("stop request body must be empty")
            return _ok(stop_fault_scenario(scenario_id))
        except FaultPlazaError as exc:
            return _error(503, str(exc))
    if method == "GET" and path == "/diagnostic-tools":
        return _ok({"items": TOOLS})

    if path == "/operator-memories":
        if method == "GET":
            scope = (params.get("project_scope") or [None])[0]
            return _ok(
                _items(
                    list_operator_preferences(
                        principal,
                        project_scope=scope,
                    )
                )
            )
        if method == "PUT":
            request = PutOperatorPreferenceRequest.model_validate(_body(raw_body))
            return _ok(
                put_operator_preference(
                    principal,
                    project_scope=request.project_scope,
                    memory_key=request.memory_key,
                    value=request.value,
                ).to_dict()
            )
        if method == "DELETE":
            request = DeleteOperatorPreferenceRequest.model_validate(
                _body(raw_body)
            )
            return _ok(
                {
                    "deleted": delete_operator_preference(
                        principal,
                        project_scope=request.project_scope,
                        memory_key=request.memory_key,
                    )
                }
            )

    if path == "/diagnostic-experiments":
        if method == "GET":
            return _ok(_items(list_experiments()))
        if method == "POST":
            request = CreateDiagnosticExperimentRequest.model_validate(
                _body(raw_body)
            )
            return _ok(
                create_experiment(request, created_by=principal).to_dict(),
                status=201,
            )

    ids = _match(path, r"/diagnostic-experiments/([^/]+)")
    if ids and method == "GET":
        value = summarize_experiment(ids[0])
        return (
            _ok(value)
            if value is not None
            else _error(404, "diagnostic experiment not found")
        )

    ids = _match(
        path,
        r"/diagnostic-experiments/([^/]+)/(assign|evaluate|approve)",
    )
    if ids and method == "POST":
        experiment_id, action = ids
        if action == "assign":
            request = AssignDiagnosticExperimentRequest.model_validate(
                _body(raw_body)
            )
            value = assign_experiment_diagnosis(
                experiment_id,
                request,
                principal=principal,
            )
        elif action == "evaluate":
            if _body(raw_body):
                raise ValueError("evaluate request body must be empty")
            value = evaluate_experiment(experiment_id, evaluated_by=principal)
        else:
            request = ApproveDiagnosticExperimentRequest.model_validate(
                _body(raw_body)
            )
            value = approve_experiment_rollout(
                experiment_id,
                approved_by=principal,
                reason=request.reason,
            )
        if value is None:
            return _error(404, "diagnostic experiment not found")
        return _ok(value.to_dict() if hasattr(value, "to_dict") else value)

    ids = _match(
        path,
        r"/diagnostic-experiments/([^/]+)/diagnoses/([^/]+)/outcome",
    )
    if ids and method == "POST":
        request = RecordDiagnosticExperimentOutcomeRequest.model_validate(
            _body(raw_body)
        )
        value = record_experiment_outcome(
            ids[0],
            ids[1],
            request,
            recorded_by=principal,
        )
        return (
            _ok(value.to_dict())
            if value is not None
            else _error(404, "diagnostic experiment assignment not found")
        )

    if method == "GET" and path == "/diagnostic-skills":
        return _ok(_items(list_skills()))

    ids = _match(path, r"/diagnostic-skills/([^/]+)")
    if ids and method == "GET":
        value = get_skill(ids[0])
        return _ok(value) if value is not None else _error(404, "diagnostic skill not found")
    ids = _match(path, r"/diagnostic-skills/([^/]+)/(evaluate|campaign|publish|quarantine|rollback)")
    if ids and method == "POST":
        skill_id, action = ids
        payload = _body(raw_body)
        if action == "evaluate":
            return _ok(evaluate_skill(skill_id))
        if action == "campaign":
            request = RecordSkillCampaignValidationRequest.model_validate(payload)
            return _ok(
                record_campaign_validation(
                    skill_id,
                    request.model_dump(mode="json"),
                    recorded_by=principal,
                )
            )
        if action == "publish":
            return _ok(publish_skill(skill_id))
        if action == "rollback":
            return _ok(rollback_skill(skill_id))
        request = QuarantineDiagnosticSkillRequest.model_validate(payload)
        return _ok(quarantine_skill(skill_id, reason=request.reason))

    if path == "/diagnoses":
        if method == "GET":
            return _ok(_items(list_diagnoses()))
        if method == "POST":
            request = CreateDiagnosisRequestV2.model_validate(_body(raw_body))
            return _ok(
                create_diagnosis(request, created_by=principal).to_dict(),
                status=201,
            )

    ids = _match(path, r"/diagnoses/([^/]+)")
    if ids:
        diagnosis_id = ids[0]
        if method == "GET":
            value = get_diagnosis(diagnosis_id)
            return _ok(value.to_dict()) if value is not None else _error(404, "Drop Insight diagnosis not found")
        if method == "DELETE":
            value = delete_diagnosis(
                diagnosis_id,
                deleted_by=principal,
                reason="用户在 AI 诊断会话历史中归档",
            )
            return _ok({"diagnosis_id": value.id, "deleted": True}) if value else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/events")
    if ids and method == "GET":
        if get_diagnosis(ids[0]) is None:
            return _error(404, "Drop Insight diagnosis not found")
        return _ok([value.to_dict() for value in list_events(ids[0])])

    ids = _match(path, r"/diagnoses/([^/]+)/retrievals")
    if ids and method == "GET":
        if get_diagnosis(ids[0]) is None:
            return _error(404, "Drop Insight diagnosis not found")
        return _ok(list_knowledge_retrievals(ids[0]))

    ids = _match(path, r"/diagnoses/([^/]+)/exploration-tree")
    if ids and method == "GET":
        value = get_live_exploration_tree(ids[0])
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/target-candidates")
    if ids and method == "GET":
        value = discover_target_candidates(
            ids[0],
            service=(params.get("service") or [None])[0],
            environment=(params.get("environment") or [None])[0],
        )
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/hypotheses")
    if ids:
        if method == "GET":
            return _ok(_items(list_hypotheses(ids[0])))
        if method == "POST":
            request = CreateHypothesisRequest.model_validate(_body(raw_body))
            value = create_hypothesis(ids[0], request)
            return _ok(value.to_dict()) if value else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/evidence")
    if ids:
        if method == "GET":
            return _ok(_items(list_evidence(ids[0])))
        if method == "POST":
            request = AddEvidenceRequest.model_validate(_body(raw_body))
            value = add_evidence(ids[0], request)
            return _ok(value.to_dict()) if value else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/evidence/import-task")
    if ids and method == "POST":
        request = ImportTaskEvidenceRequest.model_validate(_body(raw_body))
        value = import_task_evidence(ids[0], request)
        return _ok(_items(value)) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/reports")
    if ids:
        if method == "GET":
            return _ok(_items(list_reports(ids[0])))
        if method == "POST":
            request = GenerateReportRequest.model_validate(_body(raw_body))
            value = generate_report(ids[0], request)
            return _ok(value.to_dict()) if value else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/feedback")
    if ids:
        if method == "GET":
            return _ok(_items(list_feedback(ids[0])))
        if method == "POST":
            request = SubmitDiagnosisFeedbackRequest.model_validate(_body(raw_body))
            value = submit_diagnosis_feedback(ids[0], request, created_by=principal)
            return _ok(value.to_dict()) if value else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/interventions")
    if ids:
        if method == "GET":
            if get_diagnosis(ids[0]) is None:
                return _error(404, "Drop Insight diagnosis not found")
            return _ok(_items(list_diagnosis_interventions(ids[0])))
        if method == "POST":
            request = InterveneDiagnosisRequest.model_validate(_body(raw_body))
            value = intervene_diagnosis(ids[0], request, created_by=principal)
            return _ok(value) if value else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/tool-calls/preview")
    if ids and method == "POST":
        request = PreviewToolCallRequest.model_validate(_body(raw_body))
        value = preview_tool_call(ids[0], request)
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/tool-calls")
    if ids:
        if method == "GET":
            return _ok(_items(list_tool_calls(ids[0])))
        if method == "POST":
            request = CreateToolCallRequest.model_validate(_body(raw_body))
            value = request_tool_call(ids[0], request, requested_by=principal)
            return _ok(value.to_dict()) if value else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/tool-calls/([^/]+)/decision")
    if ids and method == "POST":
        request = DecideToolCallRequest.model_validate(_body(raw_body))
        value = decide_tool_call(ids[0], ids[1], request, decided_by=principal)
        return _ok(value.to_dict()) if value else _error(404, "Drop Insight tool call not found")

    ids = _match(path, r"/diagnoses/([^/]+)/tool-calls/([^/]+)")
    if ids and method == "PUT":
        request = UpdateToolCallArgumentsRequest.model_validate(_body(raw_body))
        value = update_tool_call_arguments(
            ids[0], ids[1], arguments=request.arguments, updated_by=principal
        )
        return _ok(value.to_dict()) if value else _error(404, "Drop Insight tool call not found")

    ids = _match(path, r"/diagnoses/([^/]+)/planner/run")
    if ids and method == "POST":
        request = RunPlannerRequest.model_validate(_body(raw_body))
        value = run_diagnosis_planner(ids[0], request, requested_by=principal)
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/budget")
    if ids and method == "GET":
        value = get_budget_usage(ids[0])
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/orchestrator/advance")
    if ids and method == "POST":
        value = advance_diagnosis(ids[0])
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/fix")
    if ids and method == "GET":
        return _ok(_items(list_fix_verifications(ids[0])))
    ids = _match(path, r"/diagnoses/([^/]+)/fix/verify")
    if ids and method == "POST":
        request = VerifyFixRequest.model_validate(_body(raw_body))
        value = verify_diagnosis_fix(
            ids[0],
            before_task_id=request.before_task_id,
            after_task_id=request.after_task_id,
            fix_summary=request.fix_summary,
        )
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/clarify")
    if ids and method == "POST":
        request = ClarifyDiagnosisRequest.model_validate(_body(raw_body))
        value = clarify_diagnosis(ids[0], request)
        return _ok(value) if value is not None else _error(404, "Drop Insight diagnosis not found")

    ids = _match(path, r"/diagnoses/([^/]+)/diagnostic-skills/candidate")
    if ids and method == "POST":
        return _ok(create_candidate_from_diagnosis(ids[0], created_by=principal))
    ids = _match(path, r"/diagnoses/([^/]+)/diagnostic-skill-activations")
    if ids and method == "GET":
        return _ok(_items(list_activations(ids[0])))

    return _error(404, "AI/Skill route not found")


class DiagnosticAIService(diagnostic_ai_pb2_grpc.DiagnosticAIServicer):
    def Invoke(self, request, context):  # noqa: N802 - generated gRPC contract
        metadata = dict(context.invocation_metadata())
        if os.getenv("MINI_DROP_GRPC_AUTH_ENABLED", "0").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            expected = os.getenv("MINI_DROP_GRPC_TOKEN", "")
            supplied = metadata.get("x-mini-drop-grpc-token", "")
            if not expected or not secrets.compare_digest(expected, supplied):
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "invalid internal gRPC token")
        trace_token = bind_traceparent(metadata.get("traceparent", ""))
        try:
            try:
                result = dispatch(
                    request.method,
                    request.path,
                    request.query,
                    request.body_json,
                    request.principal,
                )
            except (ValueError, ValidationError, json.JSONDecodeError) as exc:
                result = _error(422 if isinstance(exc, ValidationError) else 409, str(exc))
            except Exception as exc:
                log_event(
                    "error",
                    "diagnostic_ai_rpc_failed",
                    method=request.method,
                    path=request.path,
                    error=type(exc).__name__,
                    message=str(exc),
                )
                result = _error(500, "AI diagnosis worker failed")
            return diagnostic_ai_pb2.DiagnosticAIResponse(
                status_code=result.status,
                body_json=json.dumps(result.body, ensure_ascii=False, default=str),
            )
        finally:
            reset_traceparent(trace_token)


def add_diagnostic_ai_service(server: grpc.Server) -> None:
    diagnostic_ai_pb2_grpc.add_DiagnosticAIServicer_to_server(
        DiagnosticAIService(), server
    )


def start_diagnostic_ai_server(port: int) -> grpc.Server:
    """Start the private AI/Skill RPC endpoint inside diagnosis-worker."""

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
    add_diagnostic_ai_service(server)
    address = f"0.0.0.0:{port}"
    secure = os.getenv("MINI_DROP_GRPC_SECURE", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if secure:
        cert_path = os.getenv("MINI_DROP_GRPC_CERT_FILE", "")
        key_path = os.getenv("MINI_DROP_GRPC_KEY_FILE", "")
        if not cert_path or not key_path:
            raise RuntimeError("secure DiagnosticAI gRPC requires certificate and key files")
        certificate = Path(cert_path).read_bytes()
        private_key = Path(key_path).read_bytes()
        require_client = os.getenv(
            "MINI_DROP_GRPC_REQUIRE_CLIENT_CERT", "0"
        ).strip().lower() in {"1", "true", "yes", "on"}
        root_certificate = None
        if require_client:
            ca_path = os.getenv("MINI_DROP_GRPC_CA_FILE", "")
            if not ca_path:
                raise RuntimeError("mTLS DiagnosticAI gRPC requires a CA file")
            root_certificate = Path(ca_path).read_bytes()
        credentials = grpc.ssl_server_credentials(
            [(private_key, certificate)],
            root_certificates=root_certificate,
            require_client_auth=require_client,
        )
        bound_port = server.add_secure_port(address, credentials)
    else:
        bound_port = server.add_insecure_port(address)
    if bound_port == 0:
        raise RuntimeError(f"failed to bind DiagnosticAI gRPC port {port}")
    server.start()
    return server
