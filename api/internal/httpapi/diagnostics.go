// Package httpapi：提供授权范围内的 Agent 事件与固定诊断字段，避免浏览器获取采集进程原始 stderr 或私有运行配置。
package httpapi

import (
	"context"
	"drop/api/internal/domain"
	"encoding/json"
	"github.com/gin-gonic/gin"
	"time"
)

func (s *Server) agentEvents(c *gin.Context) {
	id := c.Param("id")
	if _, err := s.Repo.AuthorizedAgent(c.Request.Context(), user(c), id, false); err != nil {
		s.fail(c, err)
		return
	}
	rows := []map[string]any{}
	if err := s.Repo.Context(c.Request.Context()).Table("agent_events").Where("agent_id=?", id).Order("occurred_at DESC,id DESC").Limit(100).Find(&rows).Error; err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(cleanMaps(rows), int64(len(rows)), 1, len(rows)))
}
func (s *Server) agentDiagnostics(c *gin.Context) {
	id := c.Param("id")
	if _, err := s.Repo.AuthorizedAgent(c.Request.Context(), user(c), id, false); err != nil {
		s.fail(c, err)
		return
	}
	category := c.DefaultQuery("category", "resources")
	if category != "resources" && category != "capabilities" && category != "processes" && category != "events" {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "不支持此诊断类别"))
		return
	}
	client, ok := s.Tasks.Control.(interface {
		FetchData(context.Context, string, string, int) (json.RawMessage, error)
	})
	if !ok {
		s.fail(c, domain.Fail("CAPABILITY_UNAVAILABLE", "控制面诊断暂不可用"))
		return
	}
	ctx, cancel := context.WithTimeout(c.Request.Context(), 4*time.Second)
	defer cancel()
	data, err := client.FetchData(ctx, id, category, 100)
	if err != nil {
		s.fail(c, domain.Fail("CAPABILITY_UNAVAILABLE", "控制面诊断暂不可用"))
		return
	}
	s.ok(c, 200, data)
}
