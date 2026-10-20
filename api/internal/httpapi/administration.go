// Package httpapi：承接启停 Agent、重分析、删除任务和修改账户等管理操作。
// 这些请求会改变后续工作器的合法性，真正状态变化与审计由服务层在事务中协调。
package httpapi

import (
	"drop/api/internal/domain"
	"fmt"
	"github.com/gin-gonic/gin"
)

func (s *Server) setAgentEnabled(c *gin.Context) {
	var in struct {
		Enabled *bool `json:"enabled"`
	}
	if !s.bind(c, &in) {
		return
	}
	if in.Enabled == nil {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "必须指定enabled"))
		return
	}
	agent, err := s.Tasks.SetAgentEnabled(c.Request.Context(), user(c), c.Param("id"), *in.Enabled, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, agent)
}
func (s *Server) reanalyze(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if err := s.Tasks.Reanalyze(c.Request.Context(), user(c), id, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 202, gin.H{"id": fmt.Sprint(id), "analysis_status": "PENDING"})
}
func (s *Server) clientError(c *gin.Context) {
	var in struct {
		Code string `json:"code"`
	}
	if !s.bind(c, &in) {
		return
	}
	if in.Code != "FLAME_LAYOUT_FAILED" && in.Code != "WEB_RENDER_FAILED" {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "不支持此客户端错误码"))
		return
	}
	s.Telemetry.WebErrors.WithLabelValues(in.Code).Inc()
	s.ok(c, 202, gin.H{"accepted": true})
}
func (s *Server) deleteTask(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if err := s.Tasks.Delete(c.Request.Context(), user(c), id, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"deleted": true})
}
func (s *Server) updateUser(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	var in struct {
		Role        *string `json:"role"`
		DisplayName *string `json:"display_name"`
		Password    *string `json:"password"`
	}
	if !s.bind(c, &in) {
		return
	}
	u, err := s.Accounts.UpdateUser(c.Request.Context(), user(c), c.Param("id"), in.Role, in.DisplayName, in.Password, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, u)
}
