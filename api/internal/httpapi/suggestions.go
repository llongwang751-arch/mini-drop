package httpapi

import (
	"encoding/json"
	"fmt"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/service"
	"github.com/gin-gonic/gin"
	"gorm.io/gorm"
)

func (s *Server) rules(c *gin.Context) {
	rows := []map[string]any{}
	if err := s.Repo.Context(c.Request.Context()).Table("diagnostic_rules").Order("key").Find(&rows).Error; err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(cleanMaps(rows), int64(len(rows)), 1, len(rows)))
}
func (s *Server) updateRule(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	var in struct {
		Enabled    *bool          `json:"enabled"`
		Severity   *string        `json:"severity"`
		MinSamples *int64         `json:"min_samples"`
		Threshold  *float64       `json:"threshold"`
		Config     map[string]any `json:"config"`
	}
	if !s.bind(c, &in) {
		return
	}
	changes := map[string]any{"updated_by": user(c).ID, "updated_at": time.Now().UTC()}
	if in.Enabled != nil {
		changes["enabled"] = *in.Enabled
	}
	if in.Severity != nil {
		if *in.Severity != "info" && *in.Severity != "warning" && *in.Severity != "critical" {
			s.fail(c, domain.Fail("INVALID_PARAMETERS", "规则严重度无效"))
			return
		}
		changes["severity"] = *in.Severity
	}
	if in.MinSamples != nil {
		if *in.MinSamples < 0 || *in.MinSamples > 1e12 {
			s.fail(c, domain.Fail("INVALID_PARAMETERS", "规则样本门限无效"))
			return
		}
		changes["min_samples"] = *in.MinSamples
	}
	if in.Threshold != nil {
		if *in.Threshold < 0 || *in.Threshold > 100 {
			s.fail(c, domain.Fail("INVALID_PARAMETERS", "规则百分比无效"))
			return
		}
		changes["threshold"] = *in.Threshold
	}
	if in.Config != nil {
		b, _ := json.Marshal(in.Config)
		if len(b) > 8192 {
			s.fail(c, domain.Fail("INVALID_PARAMETERS", "规则配置过大"))
			return
		}
		changes["config"] = string(b)
	}
	changes["version"] = gorm.Expr("version+1")
	err := s.Repo.Context(c.Request.Context()).Transaction(func(tx *gorm.DB) error {
		res := tx.Table("diagnostic_rules").Where("key=?", c.Param("key")).Updates(changes)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return domain.Fail("NOT_FOUND", "规则不存在")
		}
		return tx.Create(ptrAuditHTTP(service.Audit(user(c), "rule.update", "rule", c.Param("key"), c.GetString("request_id")))).Error
	})
	if err != nil {
		s.fail(c, err)
		return
	}
	var row map[string]any
	s.Repo.Context(c.Request.Context()).Table("diagnostic_rules").Where("key=?", c.Param("key")).Take(&row)
	s.ok(c, 200, cleanMaps([]map[string]any{row})[0])
}
func ptrAuditHTTP(a domain.Audit) *domain.Audit { return &a }
func (s *Server) requestAI(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if _, err := s.Repo.AuthorizeTaskWrite(c.Request.Context(), user(c), id); err != nil {
		s.fail(c, err)
		return
	}
	var in struct {
		Consent bool `json:"consent"`
	}
	if !s.bind(c, &in) {
		return
	}
	if s.Suggestions == nil {
		s.fail(c, domain.Fail("AI_UNAVAILABLE", "未配置外部模型"))
		return
	}
	job, err := s.Suggestions.Request(c.Request.Context(), user(c), id, in.Consent, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 202, job)
}
func (s *Server) aiRequests(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	jobs := []service.AIRequest{}
	if err := s.Repo.Context(c.Request.Context()).Where("task_id=?", id).Order("id DESC").Limit(50).Find(&jobs).Error; err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(jobs, int64(len(jobs)), 1, len(jobs)))
}
func (s *Server) feedback(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	sid, ok := s.id(c, "suggestion_id")
	if !ok {
		return
	}
	var in struct {
		Rating  string `json:"rating"`
		Comment string `json:"comment"`
	}
	if !s.bind(c, &in) {
		return
	}
	if (in.Rating != "useful" && in.Rating != "not_useful") || len(in.Comment) > 1000 {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "反馈内容无效"))
		return
	}
	var row map[string]any
	err := s.Repo.Context(c.Request.Context()).Raw(`INSERT INTO suggestion_feedback(suggestion_id,user_id,rating,comment) SELECT id,?,?,? FROM suggestions WHERE id=? AND task_id=? ON CONFLICT(suggestion_id,user_id) DO UPDATE SET rating=excluded.rating,comment=excluded.comment,updated_at=now() RETURNING *`, user(c).ID, in.Rating, in.Comment, sid, id).Scan(&row).Error
	if err != nil {
		s.fail(c, err)
		return
	}
	if row == nil {
		s.fail(c, domain.Fail("NOT_FOUND", "建议不存在"))
		return
	}
	if err := s.record(c, "suggestion.feedback", "suggestion", fmt.Sprint(sid)); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, cleanMaps([]map[string]any{row})[0])
}
func (s *Server) feedbackList(c *gin.Context) {
	id, ok := s.authorizeTask(c)
	if !ok {
		return
	}
	sid, ok := s.id(c, "suggestion_id")
	if !ok {
		return
	}
	rows := []map[string]any{}
	err := s.Repo.Context(c.Request.Context()).Table("suggestion_feedback f").Select("f.*").Joins("JOIN suggestions s ON s.id=f.suggestion_id").Where("s.id=? AND s.task_id=?", sid, id).Order("f.created_at DESC").Limit(100).Find(&rows).Error
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(cleanMaps(rows), int64(len(rows)), 1, len(rows)))
}
