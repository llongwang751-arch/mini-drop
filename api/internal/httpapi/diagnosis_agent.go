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

// A write tool is never executed by a model. This endpoint is a separate human
// action, reruns normal target/capability/authorization validation and uses the
// normal task idempotency contract. No arbitrary command arguments are accepted.
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
