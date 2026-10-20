// Package repository：聚合结果、趋势、审计和调度记录的读取侧，以及启动时 schema 兼容性检查。
// 大结果与轻量摘要分开查询，用户范围必须在 SQL 聚合之前约束，不能先统计全库再在响应中过滤。
package repository

import (
	"context"
	"encoding/json"
	"time"

	"gorm.io/gorm"

	"drop/api/internal/domain"
)

func (r *Repository) Result(ctx context.Context, id int64) (json.RawMessage, error) {
	var row struct{ Result json.RawMessage }
	err := r.Context(ctx).Table("analysis_jobs").Select("result").Where("task_id = ? AND status = 'success' AND attempt_id = (SELECT id FROM task_attempts WHERE task_id = ? ORDER BY attempt DESC LIMIT 1)", id, id).First(&row).Error
	return row.Result, err
}
func (r *Repository) ResultSummary(ctx context.Context, id int64) (json.RawMessage, error) {
	var row struct{ Result json.RawMessage }
	err := r.Context(ctx).Table("analysis_jobs").Select("jsonb_build_object('schema_version',result->'schema_version','kind',result->'kind','total_samples',result->'total_samples','node_count',result->'node_count','summary',result->'summary') AS result").Where("task_id=? AND status='success' AND attempt_id=(SELECT id FROM task_attempts WHERE task_id=? ORDER BY attempt DESC LIMIT 1)", id, id).First(&row).Error
	return row.Result, err
}
func (r *Repository) Suggestions(ctx context.Context, id int64) ([]map[string]any, error) {
	items := []map[string]any{}
	err := r.Context(ctx).Table("suggestions").Where("task_id = ? AND superseded_at IS NULL AND (attempt_id IS NULL OR attempt_id=(SELECT id FROM task_attempts WHERE task_id=? ORDER BY attempt DESC LIMIT 1))", id, id).Order("id DESC").Find(&items).Error
	return items, err
}

type AgentSample struct {
	CreatedAt     time.Time `json:"created_at"`
	CPUPercent    float64   `json:"cpu_percent"`
	MemoryPercent float64   `json:"memory_percent"`
	LoadAverage   float64   `json:"load_average"`
}

func (r *Repository) AgentMetrics(ctx context.Context, id string) ([]AgentSample, error) {
	items := []AgentSample{}
	err := r.Context(ctx).Table("agent_samples").Select("created_at,cpu_percent,memory_percent,load_average").Where("agent_id = ? AND created_at > ?", id, time.Now().Add(-time.Hour)).Order("created_at ASC").Limit(720).Find(&items).Error
	return items, err
}

type Trend struct {
	Time    time.Time `json:"time"`
	Total   int64     `json:"total"`
	Success int64     `json:"success"`
}

// Overview 先套用用户可见范围，再在数据库中计算任务计数与趋势；聚合接口同样可能泄露跨用户数据。
type Overview struct {
	AgentsOnline     int64            `json:"agents_online"`
	AgentsTotal      int64            `json:"agents_total"`
	TasksTotal       int64            `json:"tasks_total"`
	TasksRunning     int64            `json:"tasks_running"`
	TasksSuccess     int64            `json:"tasks_success"`
	SuccessRate      float64          `json:"success_rate"`
	Trend            []Trend          `json:"trend"`
	RecentTasks      []domain.Task    `json:"recent_tasks"`
	CollectionCounts map[string]int64 `json:"collection_counts"`
	AnalysisCounts   map[string]int64 `json:"analysis_counts"`
	StorageBytes     int64            `json:"storage_bytes"`
}

// Overview 先套用用户可见范围，再在数据库中计算任务计数与趋势；聚合接口同样可能泄露跨用户数据。
func (r *Repository) Overview(ctx context.Context, u domain.User) (Overview, error) {
	v := Overview{Trend: []Trend{}, RecentTasks: []domain.Task{}, CollectionCounts: map[string]int64{}, AnalysisCounts: map[string]int64{}}
	db := r.Context(ctx)
	if err := AgentScope(db.Model(&domain.Agent{}), u, false).Count(&v.AgentsTotal).Error; err != nil {
		return v, err
	}
	if err := AgentScope(db.Model(&domain.Agent{}), u, false).Where("last_seen_at > ? AND status NOT IN ('disabled','offline')", time.Now().Add(-45*time.Second)).Count(&v.AgentsOnline).Error; err != nil {
		return v, err
	}
	type count struct {
		Status string
		Count  int64
	}
	for _, field := range []string{"collection_status", "analysis_status"} {
		var rows []count
		if err := Scope(db.Model(&domain.Task{}), u).Select(field + " AS status,COUNT(*) AS count").Group(field).Find(&rows).Error; err != nil {
			return v, err
		}
		for _, row := range rows {
			if field == "collection_status" {
				v.CollectionCounts[row.Status] = row.Count
				v.TasksTotal += row.Count
			} else {
				v.AnalysisCounts[row.Status] = row.Count
				if row.Status == "SUCCESS" {
					v.TasksSuccess = row.Count
				}
			}
		}
	}
	if err := Scope(db.Model(&domain.Task{}), u).Where("collection_status IN ('CREATED','QUEUED','DELIVERED','RUNNING','UPLOADING') OR (collection_status='COLLECTED' AND analysis_status IN ('PENDING','RUNNING','RETRY'))").Count(&v.TasksRunning).Error; err != nil {
		return v, err
	}
	if v.TasksTotal > 0 {
		v.SuccessRate = float64(v.TasksSuccess) / float64(v.TasksTotal) * 100
	}
	if err := Scope(db.Model(&domain.Task{}), u).Select("date_trunc('hour',created_at) AS time,COUNT(*) AS total,COUNT(*) FILTER (WHERE analysis_status='SUCCESS') AS success").Where("created_at > ?", time.Now().Add(-24*time.Hour)).Group("date_trunc('hour',created_at)").Order("time ASC").Find(&v.Trend).Error; err != nil {
		return v, err
	}
	var err error
	v.RecentTasks, _, err = r.Tasks(ctx, u, TaskFilter{Page: 1, PageSize: 6})
	if err != nil {
		return v, err
	}
	storageDB := Scope(db.Table("artifacts a").Joins("JOIN tasks ON tasks.id=a.task_id"), u)
	if err := storageDB.Select("COALESCE(SUM(a.size),0)").Scan(&v.StorageBytes).Error; err != nil {
		return v, err
	}
	return v, nil
}
func (r *Repository) Users(ctx context.Context) ([]domain.User, error) {
	u := []domain.User{}
	err := r.Context(ctx).Order("created_at ASC").Limit(200).Find(&u).Error
	return u, err
}
func (r *Repository) CreateUser(ctx context.Context, u *domain.User) error {
	return r.Context(ctx).Create(u).Error
}
func (r *Repository) Audits(ctx context.Context, page, size int) ([]domain.Audit, int64, error) {
	items := []domain.Audit{}
	var total int64
	db := r.Context(ctx).Model(&domain.Audit{})
	if err := db.Count(&total).Error; err != nil {
		return items, 0, err
	}
	err := db.Order("id DESC").Limit(size).Offset((page - 1) * size).Find(&items).Error
	return items, total, err
}
func (r *Repository) DeleteSchedule(ctx context.Context, u domain.User, id int64) error {
	return r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var s domain.Schedule
		if err := ResourceScope(tx, u, "schedules", true).Where("id = ?", id).First(&s).Error; err != nil {
			return err
		}
		if err := tx.Exec("DELETE FROM schedule_records WHERE schedule_id = ?", id).Error; err != nil {
			return err
		}
		return tx.Delete(&s).Error
	})
}
func (r *Repository) ScheduleRecords(ctx context.Context, u domain.User, id int64) ([]map[string]any, error) {
	var s domain.Schedule
	if err := ResourceScope(r.Context(ctx), u, "schedules", false).Where("id = ?", id).First(&s).Error; err != nil {
		return nil, err
	}
	rows := []map[string]any{}
	err := r.Context(ctx).Table("schedule_records").Where("schedule_id = ?", id).Order("scheduled_at DESC").Limit(100).Find(&rows).Error
	return rows, err
}
func (r *Repository) Ready(ctx context.Context) error {
	db, err := r.DB.DB()
	if err != nil {
		return err
	}
	return db.PingContext(ctx)
}

const MinimumSchemaVersion = 8

// CheckSchema 确认数据库至少包含当前二进制依赖的迁移版本；缺少表或列时启动失败，比运行中出现部分写入更容易安全恢复。
func (r *Repository) CheckSchema(ctx context.Context) error {
	var version int
	return r.Context(ctx).Raw("SELECT max(version) FROM schema_migrations WHERE version BETWEEN 1 AND ? HAVING count(*)=?", MinimumSchemaVersion, MinimumSchemaVersion).Row().Scan(&version)
}
