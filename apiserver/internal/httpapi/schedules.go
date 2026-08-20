package httpapi

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"time"

	"mini-drop/apiserver/internal/repository"
)

// scheduleHandlers implements the /api/schedules surface natively in Go,
// replacing the Python reverse-proxy route. It depends on the ScheduleStore
// interface so it can be unit-tested with a fake (see server_test.go).
type scheduleHandlers struct {
	store repository.ScheduleStore
}

func (h *scheduleHandlers) register(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/schedules", h.list)
	mux.HandleFunc("POST /api/schedules", h.create)
	mux.HandleFunc("PUT /api/schedules/{schedule_id}", h.update)
	mux.HandleFunc("DELETE /api/schedules/{schedule_id}", h.delete)
	mux.HandleFunc("POST /api/schedules/{schedule_id}/trigger", h.trigger)
	mux.HandleFunc("GET /api/schedules/{schedule_id}/records", h.records)
}

type schedulePayload struct {
	Name           string         `json:"name"`
	CronExpression string         `json:"cron_expression"`
	Timezone       string         `json:"timezone"`
	TaskTemplate   map[string]any `json:"task_template"`
	Enabled        *bool          `json:"enabled"`
}

func decodeSchedulePayload(w http.ResponseWriter, r *http.Request) (*schedulePayload, bool) {
	var input schedulePayload
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&input); err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, "计划参数不是有效 JSON", nil)
		return nil, false
	}
	input.Name = strings.TrimSpace(input.Name)
	input.CronExpression = strings.TrimSpace(input.CronExpression)
	input.Timezone = strings.TrimSpace(input.Timezone)
	if input.Timezone == "" {
		input.Timezone = "Asia/Shanghai"
	}
	if input.Name == "" || input.CronExpression == "" {
		writeAPI(w, http.StatusBadRequest, 1400, "计划名称和 Cron 表达式不能为空", nil)
		return nil, false
	}
	if input.TaskTemplate == nil {
		input.TaskTemplate = map[string]any{}
	}
	return &input, true
}

func (h *scheduleHandlers) list(w http.ResponseWriter, r *http.Request) {
	items, err := h.store.ListSchedules(r.Context())
	if err != nil {
		writeAPI(w, http.StatusInternalServerError, 1500, "读取计划列表失败", nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{"items": items})
}

func (h *scheduleHandlers) create(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if !requireAnyRole(w, principal, "operator", "admin") {
		return
	}
	input, ok := decodeSchedulePayload(w, r)
	if !ok {
		return
	}
	if !templateAgentAllowed(principal, input.TaskTemplate) {
		writeAPI(w, http.StatusForbidden, 1403, "计划任务模板的 Agent 不在资源范围内", nil)
		return
	}
	enabled := true
	if input.Enabled != nil {
		enabled = *input.Enabled
	}
	schedule, err := h.store.CreateSchedule(r.Context(), repository.CreateScheduleInput{
		Name:           input.Name,
		CronExpression: input.CronExpression,
		Timezone:       input.Timezone,
		TaskTemplate:   input.TaskTemplate,
		Enabled:        enabled,
	})
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, err.Error(), nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", schedule)
}

func (h *scheduleHandlers) update(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if !requireAnyRole(w, principal, "operator", "admin") {
		return
	}
	input, ok := decodeSchedulePayload(w, r)
	if !ok {
		return
	}
	if !templateAgentAllowed(principal, input.TaskTemplate) {
		writeAPI(w, http.StatusForbidden, 1403, "计划任务模板的 Agent 不在资源范围内", nil)
		return
	}
	enabled := true
	if input.Enabled != nil {
		enabled = *input.Enabled
	}
	schedule, err := h.store.UpdateSchedule(r.Context(), r.PathValue("schedule_id"), repository.CreateScheduleInput{
		Name:           input.Name,
		CronExpression: input.CronExpression,
		Timezone:       input.Timezone,
		TaskTemplate:   input.TaskTemplate,
		Enabled:        enabled,
	})
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "计划不存在", nil)
		return
	}
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, err.Error(), nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", schedule)
}

func (h *scheduleHandlers) delete(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if !requireAnyRole(w, principal, "operator", "admin") {
		return
	}
	ok, err := h.store.DeleteSchedule(r.Context(), r.PathValue("schedule_id"))
	if err != nil {
		writeAPI(w, http.StatusInternalServerError, 1500, "删除计划失败", nil)
		return
	}
	if !ok {
		writeAPI(w, http.StatusNotFound, 1404, "计划不存在", nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{"deleted": true, "schedule_id": r.PathValue("schedule_id")})
}

func (h *scheduleHandlers) trigger(w http.ResponseWriter, r *http.Request) {
	principal := principalFromRequest(r)
	if !requireAnyRole(w, principal, "operator", "admin") {
		return
	}
	id := r.PathValue("schedule_id")
	schedule, err := h.store.GetSchedule(r.Context(), id)
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "计划不存在", nil)
		return
	}
	if err != nil {
		writeAPI(w, http.StatusInternalServerError, 1500, "读取计划失败", nil)
		return
	}
	if !templateAgentAllowed(principal, schedule.TaskTemplate) {
		writeAPI(w, http.StatusForbidden, 1403, "计划任务模板的 Agent 不在资源范围内", nil)
		return
	}
	taskID, err := h.store.FireSchedule(r.Context(), id, time.Now().UTC())
	if errors.Is(err, repository.ErrNotFound) {
		writeAPI(w, http.StatusNotFound, 1404, "计划任务模板的 Agent 不存在", nil)
		return
	}
	if errors.Is(err, repository.ErrConflict) {
		writeAPI(w, http.StatusConflict, 1409, "同一触发时刻已被占用，请稍后重试", nil)
		return
	}
	if err != nil {
		writeAPI(w, http.StatusBadRequest, 1400, err.Error(), nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{"task_id": taskID})
}

func (h *scheduleHandlers) records(w http.ResponseWriter, r *http.Request) {
	items, err := h.store.ListScheduleRecords(r.Context(), r.PathValue("schedule_id"))
	if err != nil {
		writeAPI(w, http.StatusInternalServerError, 1500, "读取执行记录失败", nil)
		return
	}
	writeAPI(w, http.StatusOK, 0, "ok", map[string]any{"items": items})
}

// templateAgentAllowed checks task_template.agent_id against the principal's
// resource scope (this is a new responsibility the Python proxy bypassed).
func templateAgentAllowed(principal *requestPrincipal, template map[string]any) bool {
	agentID, _ := template["agent_id"].(string)
	if agentID == "" {
		return true // 模板未指定 Agent 时交给后续校验；有 Agent 才需要范围检查
	}
	return scopeAllows(principal.AgentIDs, agentID)
}
