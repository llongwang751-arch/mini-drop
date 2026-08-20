from fastapi import APIRouter, HTTPException, Request

from server.app.schemas import APIResponse

from .schemas import (
    AddEvidenceRequest,
    ClarifyDiagnosisRequest,
    CreateDiagnosisRequestV2,
    CreateHypothesisRequest,
    CreateToolCallRequest,
    DecideToolCallRequest,
    GenerateReportRequest,
    UpdateToolCallArgumentsRequest,
    ImportTaskEvidenceRequest,
    PreviewToolCallRequest,
    RunPlannerRequest,
    SubmitDiagnosisFeedbackRequest,
    VerifyFixRequest,
)
from .service import (
    add_evidence,
    advance_diagnosis,
    clarify_diagnosis,
    create_diagnosis,
    create_hypothesis,
    decide_tool_call,
    delete_diagnosis,
    generate_report,
    get_diagnosis,
    list_diagnoses,
    list_evidence,
    list_events,
    list_hypotheses,
    get_budget_usage,
    list_tool_calls,
    list_reports,
    list_feedback,
    import_task_evidence,
    preview_tool_call,
    request_tool_call,
    run_diagnosis_planner,
    submit_diagnosis_feedback,
    update_tool_call_arguments,
    verify_diagnosis_fix,
    list_fix_verifications,
)
from .tools import TOOLS

router = APIRouter(prefix="/api/v2", tags=["drop-insight-v2"])


@router.post("/diagnoses")
def create(payload: CreateDiagnosisRequestV2) -> APIResponse:
    diagnosis = create_diagnosis(payload)
    return APIResponse(data=diagnosis.to_dict())


@router.get("/diagnoses")
def list_all() -> APIResponse:
    return APIResponse(data={"items": [item.to_dict() for item in list_diagnoses()]})


@router.delete("/diagnoses/{diagnosis_id}")
def delete(diagnosis_id: str, request: Request) -> APIResponse:
    """软归档一个诊断会话：列表隐藏，证据与审计保留可追溯。"""
    result = delete_diagnosis(
        diagnosis_id,
        deleted_by=_principal(request),
        reason="用户在 AI 诊断会话历史中归档",
    )
    if result is None:
        raise HTTPException(status_code=404, detail="诊断会话不存在")
    return APIResponse(data={"diagnosis_id": result.id, "deleted": True})


@router.get("/diagnoses/{diagnosis_id}")
def detail(diagnosis_id: str) -> APIResponse:
    diagnosis = get_diagnosis(diagnosis_id)
    if diagnosis is None:
        raise HTTPException(status_code=404, detail="Drop Insight 诊断不存在")
    return APIResponse(data=diagnosis.to_dict())


@router.get("/diagnoses/{diagnosis_id}/events")
def events(diagnosis_id: str) -> APIResponse:
    if get_diagnosis(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="Drop Insight 诊断不存在")
    return APIResponse(data=[item.to_dict() for item in list_events(diagnosis_id)])


@router.get("/diagnostic-tools")
def tools() -> APIResponse:
    return APIResponse(data={"items": TOOLS})


@router.post("/diagnoses/{diagnosis_id}/hypotheses")
def hypotheses_create(
    diagnosis_id: str,
    payload: CreateHypothesisRequest,
) -> APIResponse:
    try:
        result = create_hypothesis(diagnosis_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result.to_dict())


@router.get("/diagnoses/{diagnosis_id}/hypotheses")
def hypotheses_list(diagnosis_id: str) -> APIResponse:
    if get_diagnosis(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data={"items": [item.to_dict() for item in list_hypotheses(diagnosis_id)]})


@router.post("/diagnoses/{diagnosis_id}/evidence")
def evidence_add(diagnosis_id: str, payload: AddEvidenceRequest) -> APIResponse:
    try:
        result = add_evidence(diagnosis_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result.to_dict())


@router.get("/diagnoses/{diagnosis_id}/evidence")
def evidence_list(diagnosis_id: str) -> APIResponse:
    if get_diagnosis(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data={"items": [item.to_dict() for item in list_evidence(diagnosis_id)]})


@router.post("/diagnoses/{diagnosis_id}/reports")
def reports_generate(
    diagnosis_id: str,
    payload: GenerateReportRequest,
) -> APIResponse:
    try:
        result = generate_report(diagnosis_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result.to_dict())


@router.get("/diagnoses/{diagnosis_id}/reports")
def reports_list(diagnosis_id: str) -> APIResponse:
    if get_diagnosis(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data={"items": [item.to_dict() for item in list_reports(diagnosis_id)]})


@router.get("/diagnoses/{diagnosis_id}/feedback")
def feedback_list(diagnosis_id: str) -> APIResponse:
    if get_diagnosis(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data={"items": [item.to_dict() for item in list_feedback(diagnosis_id)]})


@router.post("/diagnoses/{diagnosis_id}/feedback")
def feedback_submit(
    diagnosis_id: str,
    payload: SubmitDiagnosisFeedbackRequest,
    request: Request,
) -> APIResponse:
    try:
        result = submit_diagnosis_feedback(
            diagnosis_id, payload, created_by=_principal(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result.to_dict())


@router.post("/diagnoses/{diagnosis_id}/tool-calls/preview")
def tool_call_preview(
    diagnosis_id: str,
    payload: PreviewToolCallRequest,
) -> APIResponse:
    result = preview_tool_call(diagnosis_id, payload)
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result)


@router.post("/diagnoses/{diagnosis_id}/evidence/import-task")
def evidence_import_task(
    diagnosis_id: str,
    payload: ImportTaskEvidenceRequest,
) -> APIResponse:
    try:
        result = import_task_evidence(diagnosis_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data={"items": [item.to_dict() for item in result]})


@router.post("/diagnoses/{diagnosis_id}/tool-calls")
def tool_calls_create(
    diagnosis_id: str,
    payload: CreateToolCallRequest,
    request: Request,
) -> APIResponse:
    try:
        result = request_tool_call(
            diagnosis_id,
            payload,
            requested_by=_principal(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result.to_dict())


@router.get("/diagnoses/{diagnosis_id}/tool-calls")
def tool_calls_list(diagnosis_id: str) -> APIResponse:
    if get_diagnosis(diagnosis_id) is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data={"items": [item.to_dict() for item in list_tool_calls(diagnosis_id)]})


@router.post("/diagnoses/{diagnosis_id}/tool-calls/{tool_call_id}/decision")
def tool_calls_decide(
    diagnosis_id: str,
    tool_call_id: str,
    payload: DecideToolCallRequest,
    request: Request,
) -> APIResponse:
    try:
        result = decide_tool_call(
            diagnosis_id,
            tool_call_id,
            payload,
            decided_by=_principal(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight tool call not found")
    return APIResponse(data=result.to_dict())


@router.put("/diagnoses/{diagnosis_id}/tool-calls/{tool_call_id}")
def tool_calls_update_arguments(
    diagnosis_id: str,
    tool_call_id: str,
    payload: UpdateToolCallArgumentsRequest,
    request: Request,
) -> APIResponse:
    """修改待审批工具调用的参数（方案 §6.2「修改参数」）。"""
    try:
        result = update_tool_call_arguments(
            diagnosis_id,
            tool_call_id,
            arguments=payload.arguments,
            updated_by=_principal(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight tool call not found")
    return APIResponse(data=result.to_dict())


@router.post("/diagnoses/{diagnosis_id}/planner/run")
def planner_run(
    diagnosis_id: str,
    payload: RunPlannerRequest,
    request: Request,
) -> APIResponse:
    try:
        result = run_diagnosis_planner(
            diagnosis_id,
            payload,
            requested_by=_principal(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result)


def _principal(request: Request) -> str:
    """Use the server-authenticated actor for mutating AI operations."""
    return getattr(request.state, "authenticated_principal", "local-anonymous")


@router.get("/diagnoses/{diagnosis_id}/budget")
def budget_detail(diagnosis_id: str) -> APIResponse:
    result = get_budget_usage(diagnosis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result)


@router.post("/diagnoses/{diagnosis_id}/orchestrator/advance")
def orchestrator_advance(diagnosis_id: str) -> APIResponse:
    try:
        result = advance_diagnosis(diagnosis_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result)


@router.post("/diagnoses/{diagnosis_id}/fix/verify")
def verify_fix(diagnosis_id: str, payload: VerifyFixRequest) -> APIResponse:
    """Apply fix -> same-load re-test -> VERIFIED/REJECTED (guide #4.6)."""
    try:
        record = verify_diagnosis_fix(
            diagnosis_id,
            before_task_id=payload.before_task_id,
            after_task_id=payload.after_task_id,
            fix_summary=payload.fix_summary,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=record)


@router.get("/diagnoses/{diagnosis_id}/fix")
def list_fix(diagnosis_id: str) -> APIResponse:
    return APIResponse(data={"items": list_fix_verifications(diagnosis_id)})


@router.post("/diagnoses/{diagnosis_id}/clarify")
def clarify(diagnosis_id: str, payload: ClarifyDiagnosisRequest) -> APIResponse:
    """补齐范围信息后恢复规划（方案 §6.1 范围确认卡）。"""
    try:
        result = clarify_diagnosis(diagnosis_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Drop Insight diagnosis not found")
    return APIResponse(data=result)
