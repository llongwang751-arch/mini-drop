package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"gorm.io/gorm"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

func (s *Server) overview(c *gin.Context) {
	v, err := s.Repo.Overview(c.Request.Context(), user(c))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, v)
}
func liveAgent(a domain.Agent) domain.Agent {
	if a.Status != "disabled" && time.Since(a.LastSeenAt) > 45*time.Second {
		a.Status = "offline"
	}
	return a
}
func (s *Server) agents(c *gin.Context) {
	as, err := s.Repo.VisibleAgents(c.Request.Context(), user(c))
	if err != nil {
		s.fail(c, err)
		return
	}
	items := []domain.Agent{}
	for _, a := range as {
		a = liveAgent(a)
		if q := strings.ToLower(c.Query("search")); q != "" && !strings.Contains(strings.ToLower(a.Hostname+" "+a.ID+" "+a.IP), q) {
			continue
		}
		if st := c.Query("status"); st != "" && a.Status != st {
			continue
		}
		items = append(items, a)
	}
	p, size := pagination(c)
	total := len(items)
	start := min((p-1)*size, total)
	end := min(start+size, total)
	s.ok(c, 200, list(items[start:end], int64(total), p, size))
}
func (s *Server) agent(c *gin.Context) {
	a, err := s.Repo.AuthorizedAgent(c.Request.Context(), user(c), c.Param("id"), false)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, liveAgent(a))
}
func (s *Server) agentMetrics(c *gin.Context) {
	if _, err := s.Repo.AuthorizedAgent(c.Request.Context(), user(c), c.Param("id"), false); err != nil {
		s.fail(c, err)
		return
	}
	items, err := s.Repo.AgentMetrics(c.Request.Context(), c.Param("id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"items": items})
}
func (s *Server) kinds(c *gin.Context) {
	items := []map[string]any{}
	var agent *domain.Agent
	if id := c.Query("agent_id"); id != "" {
		a, err := s.Repo.AuthorizedAgent(c.Request.Context(), user(c), id, false)
		if err != nil {
			s.fail(c, err)
			return
		}
		agent = &a
	}
	for _, kind := range s.Kinds {
		k := map[string]any{}
		for key, value := range kind {
			k[key] = value
		}
		if agent != nil {
			id, _ := k["id"].(string)
			k["available"] = id == "IMPORT_COLLAPSED" || (liveAgent(*agent).Status != "offline" && agent.Status != "disabled" && agent.Supports(id))
		}
		items = append(items, k)
	}
	s.ok(c, 200, items)
}
func (s *Server) tasks(c *gin.Context) {
	p, size := pagination(c)
	items, total, err := s.Repo.Tasks(c.Request.Context(), user(c), repository.TaskFilter{Page: p, PageSize: size, Status: c.Query("status"), Kind: c.Query("kind"), AgentID: c.Query("agent_id"), Search: c.Query("search")})
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, total, p, size))
}
func (s *Server) createTask(c *gin.Context) {
	select {
	case s.createSlots <- struct{}{}:
		defer func() { <-s.createSlots }()
	default:
		s.fail(c, domain.Fail("RATE_LIMITED", "创建请求正在处理，请稍后用相同幂等键重试"))
		return
	}
	var in domain.CreateTask
	if !s.bind(c, &in) {
		return
	}
	t, replay, err := s.Tasks.Create(c.Request.Context(), user(c), in, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	status := 201
	if replay {
		status = 200
	}
	s.ok(c, status, t)
}
func (s *Server) task(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	ctx := c.Request.Context()
	t, err := s.Tasks.Authorize(ctx, user(c), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	attempts, err := s.Repo.Attempts(ctx, id)
	if err != nil {
		s.fail(c, err)
		return
	}
	artifacts, err := s.Repo.Artifacts(ctx, id)
	if err != nil {
		s.fail(c, err)
		return
	}
	events, err := s.Repo.Events(ctx, id, 0)
	if err != nil {
		s.fail(c, err)
		return
	}
	suggestions, err := s.Repo.Suggestions(ctx, id)
	if err != nil {
		s.fail(c, err)
		return
	}
	result, err := s.Repo.Result(ctx, id)
	if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
		s.fail(c, err)
		return
	}
	identity, err := s.Repo.AnalysisIdentity(ctx, id)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, struct {
		domain.Task
		Attempts         []domain.Attempt         `json:"attempts"`
		Artifacts        []domain.Artifact        `json:"artifacts"`
		Events           []domain.Event           `json:"events"`
		Suggestions      []map[string]any         `json:"suggestions"`
		Result           json.RawMessage          `json:"result,omitempty"`
		AnalysisIdentity *domain.AnalysisIdentity `json:"analysis_identity,omitempty"`
	}{t, attempts, artifacts, events, cleanMaps(suggestions), result, identity})
}
func (s *Server) cancelTask(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	t, err := s.Tasks.Cancel(c.Request.Context(), user(c), id, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, t)
}
func (s *Server) retryTask(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	t, _, err := s.Tasks.Retry(c.Request.Context(), user(c), id, c.GetHeader("Idempotency-Key"), c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, t)
}
func (s *Server) authorizeTask(c *gin.Context) (int64, bool) {
	id, ok := s.id(c, "id")
	if !ok {
		return 0, false
	}
	if _, err := s.Tasks.Authorize(c.Request.Context(), user(c), id); err != nil {
		s.fail(c, err)
		return 0, false
	}
	return id, true
}
func (s *Server) events(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	after, _ := strconv.ParseInt(c.Query("after"), 10, 64)
	items, err := s.Repo.Events(c.Request.Context(), id, after)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}
func (s *Server) artifacts(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	items, err := s.Repo.Artifacts(c.Request.Context(), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}
func (s *Server) download(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	aid, ok := s.id(c, "artifact_id")
	if !ok {
		return
	}
	url, expires, err := s.Tasks.DownloadGrant(c.Request.Context(), user(c), id, aid, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, gin.H{"url": url, "expires_at": expires})
}
func (s *Server) result(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	result, err := s.Repo.Result(c.Request.Context(), id)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		s.fail(c, domain.Fail("RESULT_NOT_READY", "当前执行尚未生成分析结果"))
		return
	}
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, result)
}
func (s *Server) partialResult(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	result, err := s.Repo.PartialResult(c.Request.Context(), id)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		s.fail(c, domain.Fail("RESULT_NOT_READY", "本次失败执行没有可查看的部分分析结果"))
		return
	}
	if err != nil {
		s.fail(c, err)
		return
	}
	// The contract bounds canonical UTF-8 JSON. HTML escaping can expand valid
	// C++ symbols beyond that bound; this remains application/json with nosniff.
	c.PureJSON(200, gin.H{"request_id": c.GetString("request_id"), "data": result, "error": nil})
}
func cleanMaps(items []map[string]any) []map[string]any {
	for _, item := range items {
		for key, value := range item {
			if b, ok := value.([]byte); ok {
				if json.Valid(b) {
					var decoded any
					_ = json.Unmarshal(b, &decoded)
					item[key] = decoded
				} else {
					item[key] = string(b)
				}
			}
			if key == "id" || strings.HasSuffix(key, "_id") {
				if value != nil {
					item[key] = fmt.Sprint(value)
				}
			}
		}
	}
	return items
}
func (s *Server) suggestions(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	items, err := s.Repo.Suggestions(c.Request.Context(), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(cleanMaps(items), int64(len(items)), 1, len(items)))
}
func (s *Server) stream(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	after, _ := strconv.ParseInt(c.GetHeader("Last-Event-ID"), 10, 64)
	if after < 0 {
		after = 0
	}
	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("X-Accel-Buffering", "no")
	flusher, ok := c.Writer.(interface{ Flush() })
	if !ok {
		return
	}
	_, _ = fmt.Fprint(c.Writer, ": connected\n\n")
	flusher.Flush()
	poll := time.NewTicker(time.Second)
	defer poll.Stop()
	keep := time.NewTicker(15 * time.Second)
	defer keep.Stop()
	for {
		token, _ := c.Cookie("drop_session")
		_, currentUser, sessionErr := s.Repo.Session(c.Request.Context(), token)
		if sessionErr != nil {
			return
		}
		if _, err := s.Tasks.Authorize(c.Request.Context(), currentUser, id); err != nil {
			return
		}
		c.Set("user", currentUser)
		events, err := s.Repo.Events(c.Request.Context(), id, after)
		if err != nil {
			return
		}
		for _, ev := range events {
			b, _ := json.Marshal(ev)
			if _, err := fmt.Fprintf(c.Writer, "id: %d\nevent: task_status\ndata: %s\n\n", ev.Sequence, b); err != nil {
				return
			}
			after = ev.Sequence
		}
		if len(events) > 0 {
			flusher.Flush()
		}
		select {
		case <-c.Request.Context().Done():
			return
		case <-poll.C:
		case <-keep.C:
			if _, err := s.Tasks.Authorize(c.Request.Context(), user(c), id); err != nil {
				return
			}
			if _, err := fmt.Fprint(c.Writer, ": keepalive\n\n"); err != nil {
				return
			}
			flusher.Flush()
			token, _ := c.Cookie("drop_session")
			if _, _, err := s.Repo.Session(c.Request.Context(), token); err != nil {
				return
			}
		}
	}
}
