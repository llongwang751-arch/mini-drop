package repository

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"

	"mini-drop/apiserver/internal/cron"
)

// Schedule is the cron task template persisted in the shared PostgreSQL schema
// (server/app/models.py ScheduleModel). The Python schedule worker owns firing;
// this surface only creates/updates/reads schedules and their trigger records.
type Schedule struct {
	ID             string         `json:"id"`
	Name           string         `json:"name"`
	CronExpression string         `json:"cron_expression"`
	Timezone       string         `json:"timezone"`
	TaskTemplate   map[string]any `json:"task_template"`
	Enabled        bool           `json:"enabled"`
	NextRunAt      time.Time      `json:"next_run_at"`
	CreatedAt      time.Time      `json:"created_at"`
	UpdatedAt      time.Time      `json:"updated_at"`
}

type ScheduleRecord struct {
	ID           string    `json:"id"`
	ScheduleID   string    `json:"schedule_id"`
	ScheduledAt  time.Time `json:"scheduled_at"`
	TaskID       *string   `json:"task_id"`
	Status       string    `json:"status"`
	ErrorMessage *string   `json:"error_message"`
	CreatedAt    time.Time `json:"created_at"`
}

type CreateScheduleInput struct {
	Name           string
	CronExpression string
	Timezone       string
	TaskTemplate   map[string]any
	Enabled        bool
}

// ScheduleStore is the interface the Go schedule HTTP handlers depend on, so
// they can be unit-tested with a fake implementation (see server_test.go).
type ScheduleStore interface {
	CreateSchedule(ctx context.Context, input CreateScheduleInput) (*Schedule, error)
	UpdateSchedule(ctx context.Context, id string, input CreateScheduleInput) (*Schedule, error)
	DeleteSchedule(ctx context.Context, id string) (bool, error)
	ListSchedules(ctx context.Context) ([]Schedule, error)
	GetSchedule(ctx context.Context, id string) (*Schedule, error)
	ListScheduleRecords(ctx context.Context, scheduleID string) ([]ScheduleRecord, error)
	FireSchedule(ctx context.Context, scheduleID string, scheduledAt time.Time) (string, error)
}

var _ ScheduleStore = (*Postgres)(nil)

func newScheduleID() (string, error) {
	var suffix [4]byte
	if _, err := rand.Read(suffix[:]); err != nil {
		return "", fmt.Errorf("generate schedule id: %w", err)
	}
	return fmt.Sprintf("schedule_%s_%x", time.Now().UTC().Format("20060102_150405"), suffix), nil
}

func (p *Postgres) CreateSchedule(ctx context.Context, input CreateScheduleInput) (*Schedule, error) {
	now := time.Now().UTC()
	nextRun, err := cron.NextScheduleFire(input.CronExpression, input.Timezone, now)
	if err != nil {
		return nil, fmt.Errorf("invalid cron/timezone: %w", err)
	}
	id, err := newScheduleID()
	if err != nil {
		return nil, err
	}
	tmpl, err := json.Marshal(input.TaskTemplate)
	if err != nil {
		return nil, fmt.Errorf("marshal task template: %w", err)
	}
	_, err = p.pool.Exec(ctx, `
		INSERT INTO schedules (
			id, name, cron_expression, timezone, task_template_json, enabled,
			next_run_at, created_at, updated_at
		) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$8)`,
		id, input.Name, input.CronExpression, input.Timezone, string(tmpl),
		input.Enabled, nextRun, now,
	)
	if err != nil {
		return nil, err
	}
	return p.GetSchedule(ctx, id)
}

func (p *Postgres) UpdateSchedule(ctx context.Context, id string, input CreateScheduleInput) (*Schedule, error) {
	if _, err := p.GetSchedule(ctx, id); err != nil {
		return nil, err
	}
	now := time.Now().UTC()
	nextRun, err := cron.NextScheduleFire(input.CronExpression, input.Timezone, now)
	if err != nil {
		return nil, fmt.Errorf("invalid cron/timezone: %w", err)
	}
	tmpl, err := json.Marshal(input.TaskTemplate)
	if err != nil {
		return nil, fmt.Errorf("marshal task template: %w", err)
	}
	_, err = p.pool.Exec(ctx, `
		UPDATE schedules SET
			name=$2, cron_expression=$3, timezone=$4, task_template_json=$5::jsonb,
			enabled=$6, next_run_at=$7, updated_at=$8
		WHERE id=$1`,
		id, input.Name, input.CronExpression, input.Timezone, string(tmpl),
		input.Enabled, nextRun, now,
	)
	if err != nil {
		return nil, err
	}
	return p.GetSchedule(ctx, id)
}

func (p *Postgres) DeleteSchedule(ctx context.Context, id string) (bool, error) {
	tag, err := p.pool.Exec(ctx, `DELETE FROM schedules WHERE id=$1`, id)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() > 0, nil
}

func (p *Postgres) ListSchedules(ctx context.Context) ([]Schedule, error) {
	rows, err := p.pool.Query(ctx, `
		SELECT id, name, cron_expression, timezone, task_template_json, enabled,
		       next_run_at, created_at, updated_at
		FROM schedules ORDER BY created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Schedule
	for rows.Next() {
		var s Schedule
		var tmpl []byte
		if err := rows.Scan(&s.ID, &s.Name, &s.CronExpression, &s.Timezone, &tmpl, &s.Enabled,
			&s.NextRunAt, &s.CreatedAt, &s.UpdatedAt); err != nil {
			return nil, err
		}
		_ = json.Unmarshal(tmpl, &s.TaskTemplate)
		out = append(out, s)
	}
	return out, rows.Err()
}

func (p *Postgres) GetSchedule(ctx context.Context, id string) (*Schedule, error) {
	row := p.pool.QueryRow(ctx, `
		SELECT id, name, cron_expression, timezone, task_template_json, enabled,
		       next_run_at, created_at, updated_at
		FROM schedules WHERE id = $1`, id)
	var s Schedule
	var tmpl []byte
	if err := row.Scan(&s.ID, &s.Name, &s.CronExpression, &s.Timezone, &tmpl, &s.Enabled,
		&s.NextRunAt, &s.CreatedAt, &s.UpdatedAt); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	_ = json.Unmarshal(tmpl, &s.TaskTemplate)
	return &s, nil
}

func (p *Postgres) ListScheduleRecords(ctx context.Context, scheduleID string) ([]ScheduleRecord, error) {
	rows, err := p.pool.Query(ctx, `
		SELECT id, schedule_id, scheduled_at, task_id, status, error_message, created_at
		FROM schedule_records WHERE schedule_id = $1 ORDER BY scheduled_at DESC`, scheduleID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ScheduleRecord
	for rows.Next() {
		var r ScheduleRecord
		if err := rows.Scan(&r.ID, &r.ScheduleID, &r.ScheduledAt, &r.TaskID, &r.Status,
			&r.ErrorMessage, &r.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, r)
	}
	return out, rows.Err()
}

// FireSchedule materializes one task from a schedule's immutable template and
// records the (schedule_id, scheduled_at) firing slot atomically, advancing
// next_run_at. Mirrors SqlRepository.fire_schedule; the unique slot keeps
// concurrent workers from firing the same minute twice.
func (p *Postgres) FireSchedule(ctx context.Context, scheduleID string, scheduledAt time.Time) (string, error) {
	schedule, err := p.GetSchedule(ctx, scheduleID)
	if err != nil {
		return "", err
	}
	nextRun, err := cron.NextScheduleFire(schedule.CronExpression, schedule.Timezone, scheduledAt)
	if err != nil {
		return "", err
	}
	tmpl := schedule.TaskTemplate
	name, _ := tmpl["name"].(string)
	agentID, _ := tmpl["agent_id"].(string)
	collectorType, _ := tmpl["collector_type"].(string)
	if name == "" || agentID == "" || collectorType == "" {
		return "", fmt.Errorf("计划任务模板缺少 name/agent_id/collector_type")
	}
	targetPID := intField(tmpl["target_pid"], 1)
	sampleRate := intField(tmpl["sample_rate"], 99)
	durationSec := intField(tmpl["duration_sec"], 15)

	var agentExists bool
	if err := p.pool.QueryRow(ctx,
		`SELECT EXISTS(SELECT 1 FROM agents WHERE id = $1)`, agentID,
	).Scan(&agentExists); err != nil {
		return "", err
	}
	if !agentExists {
		return "", ErrNotFound
	}

	now := time.Now().UTC()
	requestParams, _ := json.Marshal(map[string]any{
		"name": name, "agent_id": agentID, "target_pid": targetPID,
		"collector_type": collectorType, "sample_rate": sampleRate,
		"duration_sec": durationSec,
	})
	taskID, err := newTaskID()
	if err != nil {
		return "", err
	}

	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	if _, err := tx.Exec(ctx, `
		INSERT INTO tasks (
			id, name, agent_id, target_pid, collector_type, sample_rate, duration_sec,
			status, status_reason, collection_status, analysis_status, request_params,
			created_at
		) VALUES ($1,$2,$3,$4,$5,$6,$7,'PENDING',$8,'QUEUED','NOT_STARTED',$9::jsonb,$10)`,
		taskID, name, agentID, targetPID, collectorType, sampleRate, durationSec,
		"计划任务触发", string(requestParams), now,
	); err != nil {
		return "", err
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO task_status_events (
			task_id, from_status, to_status, reason, actor, metadata, created_at
		) VALUES ($1,NULL,'PENDING',$2,'schedule',$3::jsonb,$4)`,
		taskID, "计划任务触发", `{"served_by":"go-apiserver"}`, now,
	); err != nil {
		return "", err
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO outbox_messages (
			id, aggregate_type, aggregate_id, event_type, payload_json,
			status, attempts, next_attempt_at, created_at, updated_at
		) VALUES ($1,$2,$3,$4,$5::jsonb,'PENDING',0,$6,$6,$6)
		ON CONFLICT (id) DO NOTHING`,
		"outbox_"+taskID+":task.created", "task", taskID, "task.created",
		string(requestParams), now,
	); err != nil {
		return "", err
	}
	recordID := "schedrec_" + now.UTC().Format("20060102_150405") + taskID[len(taskID)-6:]
	if _, err := tx.Exec(ctx, `
		INSERT INTO schedule_records (
			id, schedule_id, scheduled_at, task_id, status, created_at
		) VALUES ($1,$2,$3,$4,'created',$5)`,
		recordID, scheduleID, scheduledAt, taskID, now,
	); err != nil {
		if isUniqueViolation(err) {
			return "", ErrConflict
		}
		return "", err
	}
	if _, err := tx.Exec(ctx, `
		UPDATE schedules SET next_run_at=$2, updated_at=$3 WHERE id=$1`,
		scheduleID, nextRun, now,
	); err != nil {
		return "", err
	}
	if err := tx.Commit(ctx); err != nil {
		if isUniqueViolation(err) {
			return "", ErrConflict
		}
		return "", err
	}
	return taskID, nil
}

func intField(v any, fallback int) int {
	switch t := v.(type) {
	case float64:
		return int(t)
	case int:
		return t
	case string:
		if n, err := strconv.Atoi(t); err == nil {
			return n
		}
	}
	return fallback
}
