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
	// Dependency-free router tests only. A configured repository always fails closed.
	if strings.HasPrefix(key, "login:") {
		return s.loginLimiter.Allow(key, time.Now()), nil
	}
	return s.writeLimiter.Allow(key, time.Now()), nil
}
