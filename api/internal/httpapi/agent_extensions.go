// Agent 扩展仍通过既有认证、CSRF与只写用户边界；模型本身没有这些管理接口凭据。
package httpapi

import (
	"drop/api/internal/domain"
	"drop/api/internal/service"
	"github.com/gin-gonic/gin"
)

func (s *Server) agentExtensionRoutes(v *gin.RouterGroup) {
	v.GET("/diagnostic-data-sources", s.diagnosticDataSources)
	v.GET("/diagnostic-playbooks", s.diagnosticPlaybooks)
	v.POST("/diagnostic-playbooks", s.createDiagnosticPlaybook)
	v.POST("/diagnostic-playbooks/:id/review", s.reviewDiagnosticPlaybook)
	v.GET("/investigations/:id/followups", s.investigationFollowups)
	v.POST("/investigations/:id/followups/:followup_id/decision", s.decideInvestigationFollowup)
}

func (s *Server) diagnosticDataSources(c *gin.Context) {
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	agentID := c.Query("agent_id")
	if len(agentID) < 1 || len(agentID) > 100 {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "请选择目标节点"))
		return
	}
	u, err := s.Repo.User(c.Request.Context(), user(c).ID)
	if err != nil {
		s.fail(c, err)
		return
	}
	if _, err = s.Repo.AuthorizedAgent(c.Request.Context(), u, agentID, true); err != nil {
		s.fail(c, err)
		return
	}
	rows := s.Suggestions.TelemetryCatalog(c.Request.Context(), u, agentID)
	s.ok(c, 200, list(rows, int64(len(rows)), 1, len(rows)))
}

func (s *Server) diagnosticPlaybooks(c *gin.Context) {
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	rows, err := s.Suggestions.ListDiagnosticPlaybooks(c.Request.Context(), user(c))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(rows, int64(len(rows)), 1, len(rows)))
}
func (s *Server) createDiagnosticPlaybook(c *gin.Context) {
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	var in service.DiagnosticPlaybookInput
	if !s.bind(c, &in) {
		return
	}
	row, err := s.Suggestions.CreateDiagnosticPlaybook(c.Request.Context(), user(c), in, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 201, row)
}
func (s *Server) reviewDiagnosticPlaybook(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	var in struct {
		Action          string `json:"action"`
		ReviewConfirmed bool   `json:"review_confirmed"`
	}
	if !s.bind(c, &in) {
		return
	}
	if !in.ReviewConfirmed {
		s.fail(c, domain.Fail("CONSENT_REQUIRED", "请明确复核方法内容、作用域与工具边界"))
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	row, err := s.Suggestions.ReviewDiagnosticPlaybook(c.Request.Context(), user(c), id, in.Action, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, row)
}
func (s *Server) investigationFollowups(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	rows, err := s.Suggestions.ListInvestigationFollowups(c.Request.Context(), user(c), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(rows, int64(len(rows)), 1, len(rows)))
}
func (s *Server) decideInvestigationFollowup(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	followupID, ok := s.id(c, "followup_id")
	if !ok {
		return
	}
	var in struct {
		Confirm  bool `json:"confirm"`
		Reviewed bool `json:"reviewed"`
	}
	if !s.bind(c, &in) {
		return
	}
	if !in.Reviewed {
		s.fail(c, domain.Fail("CONSENT_REQUIRED", "请审阅追加采集类型、目标与预算"))
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断服务不可用"))
		return
	}
	row, err := s.Suggestions.DecideInvestigationFollowup(c.Request.Context(), user(c), id, followupID, in.Confirm, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, row)
}
