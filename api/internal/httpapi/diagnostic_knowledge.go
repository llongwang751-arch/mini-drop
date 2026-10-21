// Package httpapi：诊断方法目录、个人经验复核与撤销的 HTTP 适配。
// 浏览器不能上传可执行 Skill 或任意历史提示；经验资格、来源和权限由服务层重新核实。
package httpapi

import (
	"strconv"

	"drop/api/internal/domain"
	"drop/api/internal/service"
	"github.com/gin-gonic/gin"
)

func (s *Server) diagnosticSkills(c *gin.Context) {
	items, err := service.BuiltinDiagnosticSkills()
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}

func (s *Server) diagnosticCases(c *gin.Context) {
	var taskID int64
	if value, exists := c.GetQuery("task_id"); exists {
		id, err := strconv.ParseInt(value, 10, 64)
		if err != nil || id < 1 {
			s.fail(c, domain.Fail("INVALID_PARAMETERS", "任务编号无效"))
			return
		}
		taskID = id
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断知识服务不可用"))
		return
	}
	items, err := s.Suggestions.ListDiagnosticCases(c.Request.Context(), user(c), taskID)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}

func (s *Server) createDiagnosticCase(c *gin.Context) {
	taskID, ok := s.id(c, "id")
	if !ok {
		return
	}
	runID, ok := s.id(c, "run_id")
	if !ok {
		return
	}
	var in service.DiagnosticCaseInput
	if !s.bind(c, &in) {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断知识服务不可用"))
		return
	}
	item, err := s.Suggestions.CreateDiagnosticCase(c.Request.Context(), user(c), taskID, runID, in, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 201, item)
}

func (s *Server) revokeDiagnosticCase(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	var in struct{}
	if !s.bind(c, &in) {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "诊断知识服务不可用"))
		return
	}
	if err := s.Suggestions.RevokeDiagnosticCase(c.Request.Context(), user(c), id, c.GetString("request_id")); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"revoked": true})
}
