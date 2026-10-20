package httpapi

import (
	"drop/api/internal/domain"
	"drop/api/internal/service"
	"github.com/gin-gonic/gin"
)

func (s *Server) requestInvestigation(c *gin.Context) {
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "调查服务不可用"))
		return
	}
	var in service.InvestigationInput
	if !s.bind(c, &in) {
		return
	}
	run, err := s.Suggestions.RequestInvestigation(c.Request.Context(), user(c), in, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 202, run)
}
func (s *Server) investigations(c *gin.Context) {
	u := user(c)
	q := s.Repo.Context(c.Request.Context()).Model(&service.Investigation{})
	if !u.IsPlatformAdmin() {
		q = q.Where("requested_by=? AND code_project_id IS NULL", u.ID)
	}
	rows := []service.Investigation{}
	if err := q.Order("id DESC").Limit(30).Find(&rows).Error; err != nil {
		s.fail(c, err)
		return
	}
	// A revoked Agent grant hides the whole investigation, including source data.
	visible := []service.Investigation{}
	for _, r := range rows {
		if _, err := s.Repo.AuthorizedAgent(c.Request.Context(), u, r.AgentID, true); err == nil {
			visible = append(visible, r)
		}
	}
	s.ok(c, 200, list(visible, int64(len(visible)), 1, len(visible)))
}
func (s *Server) investigation(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "调查服务不可用"))
		return
	}
	run, err := s.Suggestions.AuthorizeInvestigation(c.Request.Context(), user(c), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, run)
}
func (s *Server) confirmInvestigation(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	var in struct {
		Confirm bool `json:"confirm"`
	}
	if !s.bind(c, &in) {
		return
	}
	if !in.Confirm {
		s.fail(c, domain.Fail("CONSENT_REQUIRED", "请确认目标、采集计划和自动复测预算"))
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "调查服务不可用"))
		return
	}
	if err := s.Suggestions.ConfirmInvestigation(c.Request.Context(), user(c), id, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"confirmed": true})
}
func (s *Server) cancelInvestigation(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "调查服务不可用"))
		return
	}
	if err := s.Suggestions.CancelInvestigation(c.Request.Context(), user(c), id, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"canceled": true})
}
func (s *Server) verifications(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	u := user(c)
	rows := []service.VerificationRun{}
	if err := s.Repo.Context(c.Request.Context()).Where("baseline_task_id=? OR candidate_task_id=?", id, id).Order("id DESC").Limit(20).Find(&rows).Error; err != nil {
		s.fail(c, err)
		return
	}
	visible := []service.VerificationRun{}
	for _, r := range rows {
		if _, err := s.Repo.AuthorizeTaskWrite(c.Request.Context(), u, r.BaselineTaskID); err != nil {
			continue
		}
		if _, err := s.Repo.AuthorizeTaskWrite(c.Request.Context(), u, r.CandidateTaskID); err == nil {
			visible = append(visible, r)
		}
	}
	s.ok(c, 200, list(visible, int64(len(visible)), 1, len(visible)))
}
