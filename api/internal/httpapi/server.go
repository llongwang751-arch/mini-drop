// Package httpapi：HTTP 组合入口：注册路由和中间件，把 JSON 请求转为服务层调用，再统一封装结果与安全错误。
// 认证、CSRF、限流与请求体大小属于入口边界；业务授权、幂等与事务仍由服务层和仓储重复执行。
package httpapi

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	"gorm.io/gorm"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"drop/api/internal/service"
	"drop/api/internal/telemetry"
	"go.opentelemetry.io/otel/trace"
)

type Server struct {
	Tasks             *service.Tasks
	Accounts          *service.Accounts
	Repo              *repository.Repository
	Kinds             []map[string]any
	PublicURL         string
	SecureCookie      bool
	QualityReportsDir string
	OIDC              *service.OIDC
	Suggestions       *service.Suggestions
	Telemetry         *telemetry.Metrics
	createSlots       chan struct{}
	loginLimiter      *Limiter
	writeLimiter      *Limiter
	requests          atomic.Uint64
	errors            atomic.Uint64
}

// Router 安装恢复、请求标识、观测与认证中间件，再注册公共探针和受保护业务路由。
// 处理器只做协议适配，资源授权和状态变更仍在下层执行，避免漏挂一个路由就暴露数据。
func (s *Server) Router() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	_ = r.SetTrustedProxies([]string{"172.16.0.0/12", "10.0.0.0/8", "127.0.0.1"})
	s.loginLimiter = NewLimiter(10, 15*time.Minute)
	s.writeLimiter = NewLimiter(60, time.Minute)
	s.createSlots = make(chan struct{}, 1)
	if s.Telemetry == nil {
		s.Telemetry = telemetry.New(nil, nil)
	}
	r.Use(s.requestID(), s.recoverSafely(), s.observe())
	r.GET("/health/live", func(c *gin.Context) { c.JSON(200, gin.H{"status": "alive"}) })
	r.GET("/health/ready", s.ready)
	r.GET("/metrics", s.prometheus)
	v := r.Group("/api/v1")
	v.POST("/auth/login", s.login)
	v.GET("/auth/providers", s.providers)
	v.GET("/auth/oidc/start", s.oidcStart)
	v.GET("/auth/oidc/callback", s.oidcCallback)
	v.Use(s.authenticate())
	s.platformRoutes(v)
	v.GET("/quality/runs", s.qualityRuns)
	v.GET("/quality/runs/:run_id", s.qualityRun)
	v.GET("/auth/me", func(c *gin.Context) { s.ok(c, 200, gin.H{"user": user(c), "csrf_token": c.GetString("csrf")}) })
	v.POST("/auth/logout", s.logout)
	v.GET("/overview", s.overview)
	v.GET("/agents", s.agents)
	v.GET("/agents/:id", s.agent)
	v.GET("/agents/:id/metrics", s.agentMetrics)
	v.GET("/task-kinds", s.kinds)
	v.GET("/tasks", s.tasks)
	v.POST("/tasks", s.createTask)
	v.GET("/tasks/:id", s.task)
	v.POST("/tasks/:id/cancel", s.cancelTask)
	v.POST("/tasks/:id/retry", s.retryTask)
	v.GET("/tasks/:id/events", s.events)
	v.GET("/tasks/:id/events/stream", s.stream)
	v.GET("/tasks/:id/artifacts", s.artifacts)
	v.GET("/tasks/:id/artifacts/:artifact_id/download", s.download)
	v.GET("/tasks/:id/result", s.result)
	v.GET("/tasks/:id/partial-result", s.partialResult)
	v.GET("/tasks/:id/suggestions", s.suggestions)
	v.GET("/schedules", s.schedules)
	v.POST("/schedules", s.createSchedule)
	v.PATCH("/schedules/:id", s.toggleSchedule)
	v.DELETE("/schedules/:id", s.deleteSchedule)
	v.GET("/schedules/:id/records", s.scheduleRecords)
	v.GET("/audit-logs", s.audits)
	v.GET("/users", s.users)
	v.POST("/users", s.createUser)
	r.NoRoute(func(c *gin.Context) { s.fail(c, domain.Fail("NOT_FOUND", "接口不存在")) })
	return r
}

// recoverSafely 不输出请求头；Cookie、会话令牌和 CSRF 值绝不能进入异常日志。
func (s *Server) recoverSafely() gin.HandlerFunc {
	return func(c *gin.Context) {
		defer func() {
			if recover() != nil {
				slog.Error("request panic", "stage", "http.request", "request_id", c.GetString("request_id"), "error_code", "INTERNAL_ERROR")
				s.fail(c, domain.Fail("INTERNAL_ERROR", "服务暂时不可用"))
			}
		}()
		c.Next()
	}
}

// requestID 建立有限且可关联的请求标识，供响应、审计与服务日志连接同一次调用；标识不是认证凭据。
func (s *Server) requestID() gin.HandlerFunc {
	return func(c *gin.Context) {
		id := c.GetHeader("X-Request-ID")
		if len(id) < 8 || len(id) > 100 || strings.ContainsAny(id, "\r\n") {
			id = uuid.NewString()
		}
		c.Set("request_id", id)
		c.Header("X-Request-ID", id)
		c.Header("X-Content-Type-Options", "nosniff")
		c.Header("Cache-Control", "no-store")
		s.requests.Add(1)
		start := time.Now()
		c.Next()
		if c.Writer.Status() >= 500 {
			s.errors.Add(1)
		}
		sc := trace.SpanContextFromContext(c.Request.Context())
		taskID, agentID := "", ""
		if strings.HasPrefix(c.FullPath(), "/api/v1/tasks/:id") {
			taskID = c.Param("id")
		}
		if strings.HasPrefix(c.FullPath(), "/api/v1/agents/:id") {
			agentID = c.Param("id")
		}
		slog.Info("http request", "stage", "http.request", "request_id", id, "task_id", taskID, "agent_id", agentID, "trace_id", sc.TraceID().String(), "method", c.Request.Method, "route", c.FullPath(), "status", c.Writer.Status(), "duration_ms", time.Since(start).Milliseconds(), "error_code", c.GetString("error_code"))
	}
}
func (s *Server) ok(c *gin.Context, status int, data any) {
	c.JSON(status, gin.H{"request_id": c.GetString("request_id"), "data": data, "error": nil})
}

// fail 将领域错误映射为稳定 HTTP 错误契约，未知内部错误不应把数据库、执行器或模型原始消息透给浏览器。
func (s *Server) fail(c *gin.Context, err error) {
	var de *domain.Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		de = domain.Fail("NOT_FOUND", "资源不存在或无权访问")
	} else if !errors.As(err, &de) {
		slog.Error("request failed", "stage", "http.request", "request_id", c.GetString("request_id"), "error_code", "INTERNAL_ERROR")
		de = domain.Fail("INTERNAL_ERROR", "服务暂时不可用")
	}
	c.Set("error_code", de.Code)
	status := 400
	switch de.Code {
	case "UNAUTHENTICATED", "INVALID_CREDENTIALS":
		status = 401
	case "FORBIDDEN", "CSRF_INVALID", "TARGET_NOT_ALLOWED":
		status = 403
	case "NOT_FOUND", "AGENT_NOT_FOUND":
		status = 404
	case "ARTIFACT_EXPIRED":
		status = 410
	case "INVALID_STATE", "IDEMPOTENCY_CONFLICT", "RESULT_NOT_READY", "USER_EXISTS", "STALE_ATTEMPT", "DIAGNOSIS_NOT_ACTIVE", "KNOWLEDGE_CHANGED":
		status = 409
	case "RATE_LIMITED", "QUOTA_EXCEEDED", "DIAGNOSIS_BUSY":
		status = 429
	case "AGENT_OFFLINE", "CAPABILITY_UNAVAILABLE", "STORAGE_UNAVAILABLE", "AI_UNAVAILABLE", "OIDC_UNAVAILABLE", "CODE_EXECUTOR_UNAVAILABLE", "QUALITY_REPORTS_UNAVAILABLE":
		status = 503
		de.Retryable = true
	case "INTERNAL_ERROR":
		status = 500
	case "KNOWLEDGE_TIMEOUT":
		status = 504
		de.Retryable = true
	}
	c.AbortWithStatusJSON(status, gin.H{"request_id": c.GetString("request_id"), "data": nil, "error": de})
}

// bind 限制请求体并拒绝未知字段与尾随 JSON；尽早拒绝无界或歧义输入，不能依靠模型或数据库替入口补救。
func (s *Server) bind(c *gin.Context, v any) bool {
	limit := int64(4 << 20)
	if c.FullPath() == "/api/v1/tasks" && c.Request.Method == "POST" {
		limit = 64 << 20
	}
	c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, limit)
	dec := json.NewDecoder(c.Request.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "请求 JSON 无效或含不支持的字段"))
		return false
	}
	if err := dec.Decode(new(any)); err != io.EOF {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "请求必须包含单个 JSON 对象"))
		return false
	}
	return true
}
func user(c *gin.Context) domain.User { return c.MustGet("user").(domain.User) }

// authenticate 每次从持久化会话读取当前用户，写请求额外校验 CSRF 并执行共享限流。
// Cookie 自动附带不等于用户主动提交；认证成功也不等于有权读取某个 task，资源处理器还要单独授权。
func (s *Server) authenticate() gin.HandlerFunc {
	return func(c *gin.Context) {
		token, err := c.Cookie("drop_session")
		if err != nil || len(token) != 64 {
			s.fail(c, domain.Fail("UNAUTHENTICATED", "请先登录"))
			return
		}
		session, u, err := s.Repo.Session(c.Request.Context(), token)
		if err != nil {
			s.fail(c, domain.Fail("UNAUTHENTICATED", "登录状态已过期"))
			return
		}
		c.Set("user", u)
		c.Set("csrf", session.CSRFToken)
		if c.Request.Method != "GET" && c.Request.Method != "HEAD" {
			if subtle.ConstantTimeCompare([]byte(c.GetHeader("X-CSRF-Token")), []byte(session.CSRFToken)) != 1 {
				s.fail(c, domain.Fail("CSRF_INVALID", "安全校验失败，请刷新页面"))
				return
			}
			allowed, rateErr := s.allowRate(c, "write:"+u.ID, 60, time.Minute)
			if rateErr != nil {
				s.fail(c, rateErr)
				return
			}
			if !allowed {
				s.fail(c, domain.Fail("RATE_LIMITED", "操作过于频繁，请稍后再试"))
				return
			}
		}
		c.Next()
	}
}
func (s *Server) login(c *gin.Context) {
	allowed, rateErr := s.allowRate(c, "login:"+c.ClientIP(), 10, 15*time.Minute)
	if rateErr != nil {
		s.fail(c, rateErr)
		return
	}
	if !allowed {
		c.Header("Retry-After", "900")
		s.fail(c, domain.Fail("RATE_LIMITED", "登录尝试过于频繁，请 15 分钟后再试"))
		return
	}
	if origin := c.GetHeader("Origin"); origin != "" && strings.TrimRight(origin, "/") != strings.TrimRight(s.PublicURL, "/") {
		s.fail(c, domain.Fail("CSRF_INVALID", "登录来源校验失败"))
		return
	}
	var in struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if !s.bind(c, &in) {
		return
	}
	if len(in.Username) > 64 || len(in.Password) > 72 {
		s.fail(c, domain.Fail("INVALID_CREDENTIALS", "用户名或密码错误"))
		return
	}
	u, token, csrf, err := s.Accounts.Login(c.Request.Context(), in.Username, in.Password, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("drop_session", token, 12*3600, "/", "", s.SecureCookie, true)
	s.ok(c, 200, gin.H{"user": u, "csrf_token": csrf})
}
func (s *Server) logout(c *gin.Context) {
	token, _ := c.Cookie("drop_session")
	if err := s.Repo.DeleteSession(c.Request.Context(), token); err != nil {
		s.fail(c, err)
		return
	}
	_ = s.Repo.Audit(c.Request.Context(), service.Audit(user(c), "auth.logout", "user", user(c).ID, c.GetString("request_id")))
	c.SetSameSite(http.SameSiteLaxMode)
	c.SetCookie("drop_session", "", -1, "/", "", s.SecureCookie, true)
	s.ok(c, 200, gin.H{"logged_out": true})
}

// ready 核对数据库 schema 与关键下游依赖，表达当前是否能处理业务；它与仅证明进程存活的 liveness 不同。
func (s *Server) ready(c *gin.Context) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 4*time.Second)
	defer cancel()
	if err := s.Repo.Ready(ctx); err != nil {
		c.JSON(503, gin.H{"status": "not_ready", "dependency": "database"})
		return
	}
	if err := s.Tasks.Storage.Ready(ctx); err != nil {
		c.JSON(503, gin.H{"status": "not_ready", "dependency": "storage"})
		return
	}
	if err := s.Tasks.Control.Ready(ctx); err != nil {
		c.JSON(503, gin.H{"status": "not_ready", "dependency": "control"})
		return
	}
	c.JSON(200, gin.H{"status": "ready"})
}
func (s *Server) metrics(c *gin.Context) {
	c.Header("Content-Type", "text/plain; version=0.0.4")
	_, _ = fmt.Fprintf(c.Writer, "# TYPE drop_api_http_requests_total counter\ndrop_api_http_requests_total %d\n# TYPE drop_api_http_errors_total counter\ndrop_api_http_errors_total %d\n", s.requests.Load(), s.errors.Load())
}
func pagination(c *gin.Context) (int, int) {
	page, _ := strconv.Atoi(c.DefaultQuery("page", "1"))
	size, _ := strconv.Atoi(c.DefaultQuery("page_size", "20"))
	return max(1, min(page, 100000)), max(1, min(size, 100))
}
func list(items any, total int64, page, size int) gin.H {
	return gin.H{"items": items, "total": total, "page": page, "page_size": size}
}
func (s *Server) id(c *gin.Context, key string) (int64, bool) {
	id, err := strconv.ParseInt(c.Param(key), 10, 64)
	if err != nil || id < 1 {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "资源 ID 无效"))
		return 0, false
	}
	return id, true
}
func (s *Server) write(c *gin.Context) bool {
	if !user(c).CanWrite() {
		s.fail(c, domain.Fail("FORBIDDEN", "该角色只有只读权限"))
		return false
	}
	return true
}
func (s *Server) admin(c *gin.Context) bool {
	if !user(c).IsPlatformAdmin() {
		s.fail(c, domain.Fail("FORBIDDEN", "仅管理员可访问"))
		return false
	}
	return true
}
