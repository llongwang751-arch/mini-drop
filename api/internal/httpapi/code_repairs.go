// Package httpapi：已登记源码项目与隔离修复运行的 HTTP 边界。
// 读取源码和申请修复都要检查当前权限；修复结果只描述隔离副本中的验证，不代表已经修改生产代码。
package httpapi

import (
	"drop/api/internal/domain"
	"drop/api/internal/service"
	"github.com/gin-gonic/gin"
)

func (s *Server) codeProjects(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	if s.Suggestions == nil || s.Suggestions.Code == nil {
		s.fail(c, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "未配置隔离执行器"))
		return
	}
	data, err := s.Suggestions.Code.Invoke(c.Request.Context(), "projects", "", "", map[string]any{})
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, data)
}
func (s *Server) requestCodeRepair(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "修复服务不可用"))
		return
	}
	var in service.CodeRepairInput
	if !s.bind(c, &in) {
		return
	}
	run, err := s.Suggestions.RequestCodeRepair(c.Request.Context(), user(c), in, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 202, run)
}
func (s *Server) codeRepairs(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	rows := []service.CodeRepair{}
	if err := s.Repo.Context(c.Request.Context()).Order("id DESC").Limit(20).Find(&rows).Error; err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(rows, int64(len(rows)), 1, len(rows)))
}
func (s *Server) codeRepair(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	var row service.CodeRepair
	if err := s.Repo.Context(c.Request.Context()).Where("id=?", id).First(&row).Error; err != nil {
		s.fail(c, domain.Fail("NOT_FOUND", "源码修复任务不存在"))
		return
	}
	s.ok(c, 200, row)
}
func (s *Server) cancelCodeRepair(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "修复服务不可用"))
		return
	}
	if err := s.Suggestions.CancelCodeRepair(c.Request.Context(), user(c), id, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"canceled": true})
}
