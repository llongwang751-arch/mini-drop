// Package httpapi：诊断的 HTTP 入口把只读诊断、取消和人工确认复测分成独立动作。
// 模型只能提出计划，采集写操作必须经过用户确认并重新走普通任务的授权与幂等校验。
package httpapi

import (
	"drop/api/internal/domain"
	"drop/api/internal/service"
	"github.com/gin-gonic/gin"
)

func (s *Server) requestDiagnosis(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	var in service.DiagnosisInput
	if !s.bind(c, &in) {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	run, err := s.Suggestions.RequestDiagnosis(c.Request.Context(), user(c), id, in, c.GetString("request_id"), c.GetHeader("Idempotency-Key"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 202, run)
}
func (s *Server) diagnoses(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	rows := []service.DiagnosisRun{}
	if err := s.Repo.Context(c.Request.Context()).Where("task_id=?", id).Order("id DESC").Limit(20).Find(&rows).Error; err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(rows, int64(len(rows)), 1, len(rows)))
}
func (s *Server) cancelDiagnosis(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	runID, ok := s.id(c, "run_id")
	if !ok {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	if err := s.Suggestions.CancelDiagnosis(c.Request.Context(), user(c), id, runID, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"canceled": true})
}

// collectDiagnosis 是独立的人工作业入口，模型不能直接执行采集写操作。
// 这里重新校验目标、能力和授权，并复用普通任务的幂等键约定；
// 模型提供的建议不能绕过这些校验，也不能变成任意命令参数。
func (s *Server) collectDiagnosis(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	runID, ok := s.id(c, "run_id")
	if !ok {
		return
	}
	if _, err := s.Repo.AuthorizeTaskWrite(c.Request.Context(), user(c), id); err != nil {
		s.fail(c, err)
		return
	}
	var in struct {
		Confirm bool `json:"confirm"`
	}
	if !s.bind(c, &in) {
		return
	}
	if !in.Confirm {
		s.fail(c, domain.Fail("CONSENT_REQUIRED", "请确认复测目标及采集参数"))
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	created, replay, err := s.Suggestions.CollectDiagnosis(c.Request.Context(), user(c), id, runID, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	status := 201
	if replay {
		status = 200
	}
	s.ok(c, status, created)
}
