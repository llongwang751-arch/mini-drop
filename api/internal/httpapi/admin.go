package httpapi

import (
	"encoding/json"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"drop/api/internal/domain"
	"drop/api/internal/service"
)

func (s *Server) schedules(c *gin.Context) {
	items, err := s.Repo.Schedules(c.Request.Context(), user(c))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}
func (s *Server) createSchedule(c *gin.Context) {
	if !s.write(c) {
		return
	}
	var in struct {
		Name            string            `json:"name"`
		IntervalMinutes int               `json:"interval_minutes"`
		Cron            string            `json:"cron"`
		GroupID         *string           `json:"group_id"`
		Visibility      string            `json:"visibility"`
		Timezone        string            `json:"timezone"`
		Template        domain.CreateTask `json:"template"`
	}
	if !s.bind(c, &in) {
		return
	}
	in.Template.Normalize()
	if err := in.Template.Validate(); err != nil {
		s.fail(c, err)
		return
	}
	if in.Template.Kind == "IMPORT_COLLAPSED" || in.Template.Import != nil || len(in.Name) > 120 || (in.Cron != "" && in.IntervalMinutes != 0) {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "计划间隔、时区或模板无效"))
		return
	}
	if in.Timezone == "" {
		in.Timezone = "Asia/Shanghai"
	}
	if in.Visibility == "" {
		in.Visibility = "private"
	}
	if in.Visibility != "private" && in.Visibility != "group" || in.Visibility == "group" && in.GroupID == nil {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "可见性无效"))
		return
	}
	if err := s.Repo.CheckGroup(c.Request.Context(), user(c), in.GroupID, true); err != nil {
		s.fail(c, err)
		return
	}
	next, err := domain.NextSchedule(in.Cron, in.Timezone, in.IntervalMinutes, time.Now())
	if err != nil {
		s.fail(c, err)
		return
	}
	body, _ := json.Marshal(in.Template)
	now := time.Now().UTC()
	interval := in.IntervalMinutes
	if interval == 0 {
		interval = 1
	}
	sc := domain.Schedule{Name: in.Name, OwnerID: user(c).ID, IntervalMinutes: interval, Cron: in.Cron, GroupID: in.GroupID, Visibility: in.Visibility, Timezone: in.Timezone, Template: body, Enabled: true, NextRunAt: next, CreatedAt: now, UpdatedAt: now}
	if err := s.Repo.CreateSchedule(c.Request.Context(), &sc); err != nil {
		s.fail(c, err)
		return
	}
	if err := s.Repo.Audit(c.Request.Context(), service.Audit(user(c), "schedule.create", "schedule", strconv.FormatInt(sc.ID, 10), c.GetString("request_id"))); err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 201, sc)
}
func (s *Server) toggleSchedule(c *gin.Context) {
	if !s.write(c) {
		return
	}
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	var in struct {
		Enabled *bool `json:"enabled"`
	}
	if !s.bind(c, &in) {
		return
	}
	if in.Enabled == nil {
		s.fail(c, domain.Fail("INVALID_PARAMETERS", "必须指定 enabled"))
		return
	}
	if err := s.Repo.ToggleSchedule(c.Request.Context(), user(c), id, *in.Enabled); err != nil {
		s.fail(c, err)
		return
	}
	_ = s.Repo.Audit(c.Request.Context(), service.Audit(user(c), "schedule.toggle", "schedule", strconv.FormatInt(id, 10), c.GetString("request_id")))
	s.ok(c, 200, gin.H{"id": strconv.FormatInt(id, 10), "enabled": *in.Enabled})
}
func (s *Server) deleteSchedule(c *gin.Context) {
	if !s.write(c) {
		return
	}
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	if err := s.Repo.DeleteSchedule(c.Request.Context(), user(c), id); err != nil {
		s.fail(c, err)
		return
	}
	_ = s.Repo.Audit(c.Request.Context(), service.Audit(user(c), "schedule.delete", "schedule", strconv.FormatInt(id, 10), c.GetString("request_id")))
	s.ok(c, 200, gin.H{"deleted": true})
}
func (s *Server) scheduleRecords(c *gin.Context) {
	id, ok := s.id(c, "id")
	if !ok {
		return
	}
	items, err := s.Repo.ScheduleRecords(c.Request.Context(), user(c), id)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(cleanMaps(items), int64(len(items)), 1, len(items)))
}
func (s *Server) audits(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	p, size := pagination(c)
	items, total, err := s.Repo.Audits(c.Request.Context(), p, size)
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, total, p, size))
}
func (s *Server) users(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	items, err := s.Repo.Users(c.Request.Context())
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 200, list(items, int64(len(items)), 1, len(items)))
}
func (s *Server) createUser(c *gin.Context) {
	if !s.admin(c) {
		return
	}
	var in struct {
		Username    string `json:"username"`
		Password    string `json:"password"`
		DisplayName string `json:"display_name"`
		Role        string `json:"role"`
	}
	if !s.bind(c, &in) {
		return
	}
	u, err := s.Accounts.CreateUser(c.Request.Context(), user(c), in.Username, in.DisplayName, in.Password, in.Role, c.GetString("request_id"))
	if err != nil {
		s.fail(c, err)
		return
	}
	s.ok(c, 201, u)
}
