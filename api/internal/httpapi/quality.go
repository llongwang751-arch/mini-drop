package httpapi

import (
	"drop/api/internal/domain"
	"drop/api/internal/quality"
	"errors"
	"github.com/gin-gonic/gin"
	"os"
)

// 质量记录是管理员审阅后发布的离线执行摘要，只读接口不会触发采集或执行任意命令。
func (s *Server) qualityRuns(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	items, err := quality.List(s.QualityReportsDir)
	if err != nil {
		s.fail(c, domain.Fail("QUALITY_REPORTS_UNAVAILABLE", "质量记录暂时不可读取"))
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}
func (s *Server) qualityRun(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	item, err := quality.Get(s.QualityReportsDir, c.Param("run_id"))
	if errors.Is(err, os.ErrNotExist) || errors.Is(err, quality.ErrInvalid) {
		s.fail(c, domain.Fail("NOT_FOUND", "质量记录不存在"))
		return
	}
	if err != nil {
		s.fail(c, domain.Fail("QUALITY_REPORTS_UNAVAILABLE", "质量记录暂时不可读取"))
		return
	}
	s.ok(c, 200, item)
}
