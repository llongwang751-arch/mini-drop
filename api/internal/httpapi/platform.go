package httpapi

import (
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"drop/api/internal/domain"
	"drop/api/internal/service"
)

func (s *Server) platformRoutes(v *gin.RouterGroup) {
	v.GET("/code-projects", s.codeProjects)
	v.POST("/code-repairs", s.requestCodeRepair)
	v.GET("/code-repairs", s.codeRepairs)
	v.GET("/code-repairs/:id", s.codeRepair)
	v.POST("/code-repairs/:id/cancel", s.cancelCodeRepair)
	v.POST("/investigations", s.requestInvestigation)
	v.GET("/investigations", s.investigations)
	v.GET("/investigations/:id", s.investigation)
	v.POST("/investigations/:id/confirm", s.confirmInvestigation)
	v.POST("/investigations/:id/cancel", s.cancelInvestigation)
	v.GET("/tasks/:id/verifications", s.verifications)
	v.POST("/tasks/:id/diagnoses", s.requestDiagnosis)
	v.GET("/tasks/:id/diagnoses", s.diagnoses)
	v.POST("/tasks/:id/diagnoses/:run_id/cancel", s.cancelDiagnosis)
	v.POST("/tasks/:id/diagnoses/:run_id/collect", s.collectDiagnosis)
	v.PATCH("/agents/:id", s.setAgentEnabled)
	v.PATCH("/users/:id", s.updateUser)
	v.DELETE("/tasks/:id", s.deleteTask)
	v.POST("/tasks/:id/reanalyze", s.reanalyze)
	v.POST("/client-errors", s.clientError)
	v.GET("/agents/:id/events", s.agentEvents)
	v.GET("/agents/:id/diagnostics", s.agentDiagnostics)
	v.GET("/groups", s.groups)
	v.POST("/groups", s.createGroup)
	v.GET("/groups/:id", s.group)
	v.PUT("/groups/:id/members/:user_id", s.putMember)
	v.DELETE("/groups/:id/members/:user_id", s.deleteMember)
	v.PUT("/groups/:id/agents/:agent_id", s.putAgentGrant)
	v.DELETE("/groups/:id/agents/:agent_id", s.deleteAgentGrant)
	v.GET("/tasks/:id/shares", s.shares)
	v.POST("/tasks/:id/shares", s.createShare)
	v.DELETE("/tasks/:id/shares/:share_id", s.deleteShare)
	v.GET("/composites", s.composites)
	v.POST("/composites", s.createComposite)
	v.GET("/composites/:id", s.composite)
	v.POST("/composites/:id/cancel", s.cancelComposite)
	v.GET("/rules", s.rules)
	v.PATCH("/rules/:key", s.updateRule)
	v.POST("/tasks/:id/suggestions/ai", s.requestAI)
	v.GET("/tasks/:id/suggestions/requests", s.aiRequests)
	v.POST("/tasks/:id/suggestions/:suggestion_id/feedback", s.feedback)
	v.GET("/tasks/:id/suggestions/:suggestion_id/feedback", s.feedbackList)
}
func (s *Server) record(c *gin.Context, action, kind, id string) error {
	return s.Repo.Audit(c.Request.Context(), service.Audit(user(c), action, kind, id, c.GetString("request_id")))
}
func (s *Server) groups(c *gin.Context) {
	items, err := s.Repo.Groups(c.Request.Context(), user(c))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}
func (s *Server) createGroup(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	var in struct {
		Name        string `json:"name"`
		Description string `json:"description"`
	}
	if !s.bind(c, &in) {
		return
	}
	if in.Name == "" || len(in.Name) > 120 || len(in.Description) > 1000 {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "组名称或描述无效"))
		return
	}
	g := domain.Group{ID: uuid.NewString(), Name: in.Name, Description: in.Description, CreatedAt: time.Now().UTC()}
	if err := s.Repo.Context(c.Request.Context()).Create(&g).Error; err != nil {
		s.fail(c, err)
		return
	}
	if err := s.record(c, "group.create", "group", g.ID); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 201, g)
}
func (s *Server) group(c *gin.Context) {
	g, m, a, err := s.Repo.GroupDetail(c.Request.Context(), user(c), c.Param("id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, struct {
		domain.Group
		Members []map[string]any `json:"members"`
		Agents  []map[string]any `json:"agents"`
	}{g, cleanMaps(m), a})
}
func (s *Server) putMember(c *gin.Context) {
	var in struct {
		Role string `json:"role"`
	}
	if !s.bind(c, &in) {
		return
	}
	if err := s.Repo.PutGroupMember(c.Request.Context(), user(c), c.Param("id"), c.Param("user_id"), in.Role); err != nil {
		s.fail(c, err)
		return
	}
	if err := s.record(c, "group.member_set", "group", c.Param("id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"user_id": c.Param("user_id"), "role": in.Role})
}
func (s *Server) deleteMember(c *gin.Context) {
	if err := s.Repo.CanManageGroup(c.Request.Context(), user(c), c.Param("id")); err != nil {
		s.fail(c, err)
		return
	}
	if err := s.Repo.Context(c.Request.Context()).Exec("DELETE FROM group_members WHERE group_id=? AND user_id=?", c.Param("id"), c.Param("user_id")).Error; err != nil {
		s.fail(c, err)
		return
	}
	if err := s.record(c, "group.member_delete", "group", c.Param("id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"deleted": true})
}
func (s *Server) putAgentGrant(c *gin.Context) {
	var in struct {
		Permission string `json:"permission"`
	}
	if !s.bind(c, &in) {
		return
	}
	if err := s.Repo.PutGroupAgent(c.Request.Context(), user(c), c.Param("id"), c.Param("agent_id"), in.Permission); err != nil {
		s.fail(c, err)
		return
	}
	if err := s.record(c, "group.agent_set", "group", c.Param("id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"agent_id": c.Param("agent_id"), "permission": in.Permission})
}
func (s *Server) deleteAgentGrant(c *gin.Context) {
	if err := s.Repo.CanManageGroup(c.Request.Context(), user(c), c.Param("id")); err != nil {
		s.fail(c, err)
		return
	}
	if err := s.Repo.Context(c.Request.Context()).Exec("DELETE FROM group_agents WHERE group_id=? AND agent_id=?", c.Param("id"), c.Param("agent_id")).Error; err != nil {
		s.fail(c, err)
		return
	}
	if err := s.record(c, "group.agent_delete", "group", c.Param("id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"deleted": true})
}
func (s *Server) shares(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	rows, err := s.Repo.Shares(c.Request.Context(), user(c), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(cleanMaps(rows), int64(len(rows)), 1, len(rows)))
}
func (s *Server) createShare(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	var in struct {
		UserID     *string `json:"user_id"`
		GroupID    *string `json:"group_id"`
		Permission string  `json:"permission"`
	}
	if !s.bind(c, &in) {
		return
	}
	row, err := s.Repo.CreateShare(c.Request.Context(), user(c), id, in.UserID, in.GroupID, in.Permission)
	if err != nil {
		s.fail(c, err)
		return
	}
	if err := s.record(c, "task.share", "task", fmt.Sprint(id)); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 201, cleanMaps([]map[string]any{row})[0])
}
func (s *Server) deleteShare(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	sid, ok := s.id(c, "share_id")
	if !ok {
		return
	}
	t, err := s.Tasks.Authorize(c.Request.Context(), user(c), id)
	if err == nil {
		err = s.Repo.CanManageTask(c.Request.Context(), user(c), t)
	}
	if err == nil {
		err = s.Repo.Context(c.Request.Context()).Exec("DELETE FROM task_shares WHERE id=? AND task_id=?", sid, id).Error
	}
	if err != nil {
		s.fail(c, err)
		return
	}
	if err := s.record(c, "task.unshare", "task", fmt.Sprint(id)); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"deleted": true})
}
func (s *Server) composites(c *gin.Context) {
	runs, err := s.Tasks.Composites(c.Request.Context(), user(c))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(runs, int64(len(runs)), 1, len(runs)))
}
func (s *Server) createComposite(c *gin.Context) {
	var in domain.CreateComposite
	if !s.bind(c, &in) {
		return
	}
	run, replay, err := s.Tasks.CreateComposite(c.Request.Context(), user(c), in, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	status := 201
	if replay {
		status = 200
	}
	s.ok(c, status, run)
}
func (s *Server) composite(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	run, nodes, err := s.Tasks.Composite(c.Request.Context(), user(c), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	report := gin.H{"results": []any{}, "suggestions": []any{}, "limitations": []string{"不同采样器的权重、时长与资源维度不相加；结论仅引用各子任务实际结果。"}}
	results, suggestions := []any{}, []any{}
	for _, node := range nodes {
		if node.Task != nil && len(node.Result) > 0 {
			results = append(results, gin.H{"node_key": node.Key, "task_id": fmt.Sprint(node.Task.ID), "summary": node.Result})
			rows, err := s.Repo.Suggestions(c.Request.Context(), node.Task.ID)
			if err != nil {
				s.fail(c, err)
				return
			}
			for i, row := range cleanMaps(rows) {
				if i >= 10 {
					break
				}
				suggestions = append(suggestions, gin.H{"node_key": node.Key, "task_id": fmt.Sprint(node.Task.ID), "suggestion": row})
			}
		}
	}
	report["results"] = results
	report["suggestions"] = suggestions
	s.ok(c, 200, struct {
		domain.CompositeRun
		Nodes  []domain.CompositeNode `json:"nodes"`
		Report gin.H                  `json:"report"`
	}{run, nodes, report})
}
func (s *Server) cancelComposite(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if err := s.Tasks.CancelComposite(c.Request.Context(), user(c), id, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"id": strconv.FormatInt(id, 10), "status": "CANCELED"})
}
func (s *Server) providers(c *gin.Context) {
	name := ""
	if s.OIDC != nil {
		name = s.OIDC.Config.Name
	}
	s.ok(c, 200, gin.H{"local": true, "oidc": gin.H{"enabled": s.OIDC != nil, "name": name}, "ai": gin.H{"enabled": s.Suggestions.AgentAvailable(), "name": "模型辅助诊断"}})
}
func (s *Server) oidcStart(c *gin.Context) {
	allowed, err := s.allowRate(c, "oidc-start:"+c.ClientIP(), 10, 15*time.Minute)
	if err != nil {
		s.fail(c, err)
		return
	}
	if !allowed {
		s.fail(c, domain.Fail("RATE_LIMITED", "登录尝试过于频繁"))
		return
	}
	if s.OIDC == nil {
		s.fail(c, domain.Fail("OIDC_UNAVAILABLE", "未配置企业身份源"))
		return
	}
	redirect, browser, err := s.OIDC.Start(c.Request.Context())
	if err != nil {
		s.fail(c, err)
		return
	}
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("drop_oidc", browser, 600, "/api/v1/auth/oidc", "", s.SecureCookie, true)
	c.Redirect(302, redirect)
}
func (s *Server) oidcCallback(c *gin.Context) {
	allowed, err := s.allowRate(c, "oidc-callback:"+c.ClientIP(), 20, 15*time.Minute)
	if err != nil {
		s.fail(c, err)
		return
	}
	if !allowed {
		s.fail(c, domain.Fail("RATE_LIMITED", "登录尝试过于频繁"))
		return
	}
	if s.OIDC == nil {
		s.fail(c, domain.Fail("OIDC_UNAVAILABLE", "未配置企业身份源"))
		return
	}
	browser, _ := c.Cookie("drop_oidc")
	c.SetCookie("drop_oidc", "", -1, "/api/v1/auth/oidc", "", s.SecureCookie, true)
	_, token, _, err := s.OIDC.Callback(c.Request.Context(), c.Query("state"), browser, c.Query("code"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("drop_session", token, 12*3600, "/", "", s.SecureCookie, true)
	c.Redirect(302, "/")
}
