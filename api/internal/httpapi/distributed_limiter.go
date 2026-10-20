// Package httpapi：把请求限流接到 PostgreSQL 的共享窗口；多实例共享额度，数据库故障不能切回宽松的本地计数。
package httpapi

import (
	"github.com/gin-gonic/gin"
	"strings"
	"time"
)

func (s *Server) allowRate(c *gin.Context, key string, limit int, window time.Duration) (bool, error) {
	if s.Repo != nil {
		return s.Repo.AllowRate(c.Request.Context(), key, limit, window)
	}
	// 仅无数据库的路由单元测试可以使用内存限流器；一旦配置仓储，数据库故障就必须拒绝请求，不能降级绕过限额。
	if strings.HasPrefix(key, "login:") {
		return s.loginLimiter.Allow(key, time.Now()), nil
	}
	return s.writeLimiter.Allow(key, time.Now()), nil
}
