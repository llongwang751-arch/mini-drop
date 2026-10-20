package repository

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"strconv"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"drop/api/internal/domain"
)

type Repository struct{ DB *gorm.DB }

func New(db *gorm.DB) *Repository                          { return &Repository{DB: db} }
func (r *Repository) Context(ctx context.Context) *gorm.DB { return r.DB.WithContext(ctx) }
func Digest(v string) string                               { return DigestBytes([]byte(v)) }
func DigestBytes(v []byte) string                          { s := sha256.Sum256(v); return hex.EncodeToString(s[:]) }
func (r *Repository) User(ctx context.Context, id string) (domain.User, error) {
	var u domain.User
	err := r.Context(ctx).Where("id = ?", id).First(&u).Error
	return u, err
}
func (r *Repository) UserByName(ctx context.Context, name string) (domain.User, error) {
	var u domain.User
	err := r.Context(ctx).Where("username = ?", name).First(&u).Error
	return u, err
}
func (r *Repository) Bootstrap(ctx context.Context, u domain.User) error {
	return r.Context(ctx).Clauses(clause.OnConflict{Columns: []clause.Column{{Name: "username"}}, DoNothing: true}).Create(&u).Error
}

type Session struct {
	TokenHash string `gorm:"primaryKey"`
	UserID    string
	CSRFToken string
	ExpiresAt time.Time
	CreatedAt time.Time
}

func (r *Repository) NewSession(ctx context.Context, s Session) error {
	return r.Context(ctx).Create(&s).Error
}
func (r *Repository) Session(ctx context.Context, token string) (Session, domain.User, error) {
	var s Session
	err := r.Context(ctx).Where("token_hash = ? AND expires_at > ?", Digest(token), time.Now()).First(&s).Error
	if err != nil {
		return s, domain.User{}, err
	}
	u, err := r.User(ctx, s.UserID)
	return s, u, err
}
func (r *Repository) DeleteSession(ctx context.Context, token string) error {
	return r.Context(ctx).Delete(&Session{}, "token_hash = ?", Digest(token)).Error
}
func (r *Repository) Agent(ctx context.Context, id string) (domain.Agent, error) {
	var a domain.Agent
	err := r.Context(ctx).Where("id = ?", id).First(&a).Error
	return a, err
}
func (r *Repository) Agents(ctx context.Context) ([]domain.Agent, error) {
	a := []domain.Agent{}
	err := r.Context(ctx).Order("hostname ASC,id ASC").Limit(500).Find(&a).Error
	return a, err
}
func Scope(db *gorm.DB, u domain.User) *gorm.DB {
	return ResourceScope(db, u, "tasks", false)
}
func (r *Repository) Task(ctx context.Context, u domain.User, id int64) (domain.Task, error) {
	var t domain.Task
	err := Scope(r.Context(ctx), u).Where("id = ?", id).First(&t).Error
	return t, err
}

type TaskFilter struct {
	Page, PageSize                int
	Status, Kind, AgentID, Search string
}

func (r *Repository) Tasks(ctx context.Context, u domain.User, f TaskFilter) ([]domain.Task, int64, error) {
	ts := []domain.Task{}
	var total int64
	db := Scope(r.Context(ctx).Model(&domain.Task{}), u)
	if f.Status != "" {
		db = db.Where("(collection_status = ? OR analysis_status = ?)", f.Status, f.Status)
	}
	if f.Kind != "" {
		db = db.Where("kind = ?", f.Kind)
	}
	if f.AgentID != "" {
		db = db.Where("target_agent_id = ?", f.AgentID)
	}
	if f.Search != "" {
		db = db.Where("name ILIKE ?", "%"+f.Search+"%")
	}
	if err := db.Count(&total).Error; err != nil {
		return ts, 0, err
	}
	err := db.Order("created_at DESC,id DESC").Limit(f.PageSize).Offset((f.Page - 1) * f.PageSize).Find(&ts).Error
	return ts, total, err
}
func (r *Repository) Existing(ctx context.Context, owner, key string) (domain.Task, error) {
	var t domain.Task
	err := r.Context(ctx).Where("creator_id = ? AND idempotency_key = ?", owner, key).First(&t).Error
	return t, err
}
func (r *Repository) CreateTask(ctx context.Context, t domain.Task, queue bool, audit domain.Audit) (domain.Task, domain.Attempt, bool, error) {
	var result domain.Task
	var a domain.Attempt
	replay := false
	err := r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", "task-quota:"+t.OwnerID).Error; err != nil {
			return err
		}
		err := tx.Where("creator_id = ? AND idempotency_key = ?", t.OwnerID, t.IdempotencyKey).First(&result).Error
		if err == nil {
			replay = true
			if result.RequestHash != t.RequestHash {
				return domain.Fail("IDEMPOTENCY_CONFLICT", "该幂等键已用于不同请求")
			}
			return tx.Where("task_id = ?", result.ID).Order("attempt DESC").First(&a).Error
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		var count int64
		if err := tx.Model(&domain.Task{}).Where("creator_id = ? AND collection_status IN ('CREATED','QUEUED','DELIVERED','RUNNING','UPLOADING')", t.OwnerID).Count(&count).Error; err != nil {
			return err
		}
		if count >= 10 {
			return domain.Fail("QUOTA_EXCEEDED", "最多同时运行 10 个采集任务")
		}
		if err := tx.Create(&t).Error; err != nil {
			return err
		}
		k, _ := domain.Kind(t.Kind)
		rawName := k.RawName
		if rawName == "" {
			rawName = "profile.folded"
		}
		a = domain.Attempt{TaskID: t.ID, Attempt: 1, AgentID: t.AgentID, Status: t.CollectionStatus, RunnerVersion: "1.0.0", RawObjectKey: domain.RawKey(t.ID, 1)[:len(domain.RawKey(t.ID, 1))-len("profile.folded")] + rawName, CreatedAt: time.Now().UTC()}
		if err := tx.Create(&a).Error; err != nil {
			return err
		}
		event := domain.EventFor(t, &a.ID, "TASK_CREATED", map[string]any{"kind": t.Kind, "collection_status": t.CollectionStatus})
		if err := tx.Create(&event).Error; err != nil {
			return err
		}
		if queue {
			out := domain.Outbox{TaskID: t.ID, AttemptID: a.ID, EventType: "DISPATCH", Payload: []byte("{}"), AvailableAt: time.Now().UTC(), CreatedAt: time.Now().UTC()}
			if err := tx.Create(&out).Error; err != nil {
				return err
			}
		}
		audit.ResourceID = strconv.FormatInt(t.ID, 10)
		if err := tx.Create(&audit).Error; err != nil {
			return err
		}
		result = t
		return nil
	})
	return result, a, replay, err
}
func (r *Repository) Attempts(ctx context.Context, id int64) ([]domain.Attempt, error) {
	a := []domain.Attempt{}
	err := r.Context(ctx).Where("task_id = ?", id).Order("attempt ASC").Find(&a).Error
	return a, err
}
func (r *Repository) LatestAttempt(ctx context.Context, id int64) (domain.Attempt, error) {
	var a domain.Attempt
	err := r.Context(ctx).Where("task_id = ?", id).Order("attempt DESC").First(&a).Error
	return a, err
}
func (r *Repository) Cancel(ctx context.Context, u domain.User, id int64, audit domain.Audit) (domain.Task, error) {
	var t domain.Task
	err := r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := ScopeWrite(tx, u).Clauses(clause.Locking{Strength: "UPDATE"}).Where("id = ?", id).First(&t).Error; err != nil {
			return err
		}
		if t.Terminal() {
			// Cancellation is idempotent: retain the terminal state and avoid
			// duplicate events, audit records, grants or cancellation outbox rows.
			return nil
		}
		if err := tx.Model(&t).Updates(map[string]any{"collection_status": "CANCELED", "analysis_status": "CANCELED", "updated_at": time.Now().UTC(), "version": gorm.Expr("version + 1")}).Error; err != nil {
			return err
		}
		var a domain.Attempt
		if err := tx.Where("task_id = ?", id).Order("attempt DESC").First(&a).Error; err != nil {
			return err
		}
		if err := tx.Model(&a).Updates(map[string]any{"status": "CANCELED", "finished_at": time.Now().UTC(), "raw_upload_url": "", "upload_targets": string(TerminalUploadMetadata(a))}).Error; err != nil {
			return err
		}
		if err := tx.Exec("UPDATE analysis_jobs SET status='failed',lease_owner=NULL,lease_expires_at=NULL,error_code='TASK_CANCELED',error_message='Task was canceled',updated_at=now() WHERE task_id=? AND attempt_id=? AND status IN ('pending','running','retry')", id, a.ID).Error; err != nil {
			return err
		}
		if err := tx.Exec("UPDATE ai_requests SET status='failed',lease_owner=NULL,lease_expires_at=NULL,error_code='TASK_CANCELED',updated_at=now() WHERE task_id=? AND attempt_id=? AND status IN ('pending','running')", id, a.ID).Error; err != nil {
			return err
		}
		event := domain.EventFor(t, &a.ID, "TASK_CANCELED", map[string]string{"collection_status": "CANCELED"})
		if err := tx.Create(&event).Error; err != nil {
			return err
		}
		out := domain.Outbox{TaskID: t.ID, AttemptID: a.ID, EventType: "CANCEL", Payload: []byte("{}"), AvailableAt: time.Now().UTC(), CreatedAt: time.Now().UTC()}
		if err := tx.Create(&out).Error; err != nil {
			return err
		}
		return tx.Create(&audit).Error
	})
	return t, err
}
func (r *Repository) Retry(ctx context.Context, u domain.User, id int64, key, requestID string) (domain.Task, bool, error) {
	var t domain.Task
	replay := false
	err := r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var owned domain.Task
		if err := ScopeWrite(tx, u).Where("id=?", id).First(&owned).Error; err != nil {
			return err
		}
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", "task-quota:"+owned.OwnerID).Error; err != nil {
			return err
		}
		if err := ScopeWrite(tx, u).Clauses(clause.Locking{Strength: "UPDATE"}).Where("id = ?", id).First(&t).Error; err != nil {
			return err
		}
		var marker int64
		if err := tx.Table("task_events").Where("task_id = ? AND type = 'TASK_RETRIED' AND payload->>'idempotency_key' = ?", id, key).Count(&marker).Error; err != nil {
			return err
		}
		if marker > 0 {
			replay = true
			return nil
		}
		if !t.Terminal() {
			return domain.Fail("INVALID_STATE", "只有终态任务可以重试")
		}
		var active int64
		if err := tx.Model(&domain.Task{}).Where("creator_id = ? AND collection_status IN ('CREATED','QUEUED','DELIVERED','RUNNING','UPLOADING')", t.OwnerID).Count(&active).Error; err != nil {
			return err
		}
		if active >= 10 {
			return domain.Fail("QUOTA_EXCEEDED", "最多同时运行 10 个采集任务")
		}
		if t.AgentID == nil {
			return domain.Fail("INVALID_STATE", "请重新导入原始文本以建立独立的分析任务")
		}
		var previous domain.Attempt
		if err := tx.Where("task_id = ?", id).Order("attempt DESC").First(&previous).Error; err != nil {
			return err
		}
		// A retry creates a different attempt: the old one cannot publish even a
		// late log, and retains neither URL nor upload authorization metadata.
		if err := tx.Model(&previous).Updates(map[string]any{"raw_upload_url": "", "upload_targets": "[]"}).Error; err != nil {
			return err
		}
		k, _ := domain.Kind(t.Kind)
		rawKey := domain.RawKey(id, previous.Attempt+1)
		if k.RawName != "" {
			rawKey = rawKey[:len(rawKey)-len("profile.folded")] + k.RawName
		}
		a := domain.Attempt{TaskID: id, Attempt: previous.Attempt + 1, AgentID: t.AgentID, Status: "CREATED", RunnerVersion: "1.0.0", RawObjectKey: rawKey, CreatedAt: time.Now().UTC()}
		if err := tx.Create(&a).Error; err != nil {
			return err
		}
		deadline := time.Now().Add(10 * time.Minute)
		if err := tx.Model(&t).Updates(map[string]any{"collection_status": "CREATED", "analysis_status": "PENDING", "error_code": nil, "error_message": nil, "request_id": requestID, "collection_deadline": deadline, "updated_at": time.Now().UTC(), "version": gorm.Expr("version + 1")}).Error; err != nil {
			return err
		}
		e := domain.EventFor(t, &a.ID, "TASK_RETRIED", map[string]string{"idempotency_key": key})
		if err := tx.Create(&e).Error; err != nil {
			return err
		}
		out := domain.Outbox{TaskID: id, AttemptID: a.ID, EventType: "DISPATCH", Payload: []byte("{}"), AvailableAt: time.Now().UTC(), CreatedAt: time.Now().UTC()}
		if err := tx.Create(&out).Error; err != nil {
			return err
		}
		return nil
	})
	return t, replay, err
}
func (r *Repository) Artifacts(ctx context.Context, id int64) ([]domain.Artifact, error) {
	a := []domain.Artifact{}
	err := r.Context(ctx).Where("task_id = ?", id).Order("created_at ASC,id ASC").Find(&a).Error
	return a, err
}
func (r *Repository) Events(ctx context.Context, id, after int64) ([]domain.Event, error) {
	e := []domain.Event{}
	err := r.Context(ctx).Where("task_id = ? AND sequence > ?", id, after).Order("sequence ASC").Limit(500).Find(&e).Error
	return e, err
}
func (r *Repository) Audit(ctx context.Context, a domain.Audit) error {
	return r.Context(ctx).Create(&a).Error
}
func (r *Repository) ImportComplete(ctx context.Context, t domain.Task, a domain.Artifact) error {
	return r.ImportWithAux(ctx, t, a, nil)
}
func (r *Repository) ImportWithAux(ctx context.Context, t domain.Task, a domain.Artifact, aux []domain.Artifact) error {
	return r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var locked domain.Task
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id = ?", t.ID).First(&locked).Error; err != nil {
			return err
		}
		if locked.CollectionStatus == "COLLECTED" {
			return nil
		}
		if locked.CollectionStatus != "CREATED" {
			return domain.Fail("INVALID_STATE", "任务已停止接收导入")
		}
		if err := tx.Create(&a).Error; err != nil {
			return err
		}
		for i := range aux {
			if err := tx.Create(&aux[i]).Error; err != nil {
				return err
			}
		}
		if err := tx.Model(&locked).Updates(map[string]any{"collection_status": "COLLECTED", "updated_at": time.Now().UTC()}).Error; err != nil {
			return err
		}
		if err := tx.Model(&domain.Attempt{}).Where("id = ?", a.AttemptID).Updates(map[string]any{"status": "COLLECTED", "finished_at": time.Now().UTC(), "raw_upload_url": "", "upload_targets": "[]"}).Error; err != nil {
			return err
		}
		if err := tx.Exec("INSERT INTO analysis_jobs(task_id,attempt_id,pipeline,status) VALUES(?,?,?,'pending') ON CONFLICT(task_id,attempt_id) DO NOTHING", t.ID, a.AttemptID, domain.PipelineFor(t.Kind)).Error; err != nil {
			return err
		}
		event := domain.EventFor(t, &a.AttemptID, "COLLECTION_SUCCEEDED", map[string]string{"collection_status": "COLLECTED", "source": "import"})
		return tx.Create(&event).Error
	})
}
func (r *Repository) DispatchOne(ctx context.Context, fn func(domain.Outbox, domain.Task, domain.Attempt) error) error {
	return r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var o domain.Outbox
		err := tx.Select("outbox.*").Joins("JOIN tasks AS dispatch_task ON dispatch_task.id=outbox.task_id").Clauses(clause.Locking{Strength: "UPDATE", Table: clause.Table{Name: "outbox"}, Options: "SKIP LOCKED"}).Where("outbox.published_at IS NULL AND outbox.available_at <= ?", time.Now()).Order("CASE WHEN outbox.event_type='CANCEL' THEN 1 ELSE 0 END DESC").Order("dispatch_task.priority DESC").Order("outbox.created_at ASC, outbox.id ASC").First(&o).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		var t domain.Task
		if err := tx.Where("id = ?", o.TaskID).First(&t).Error; err != nil {
			return err
		}
		var a domain.Attempt
		if err := tx.Where("id = ?", o.AttemptID).First(&a).Error; err != nil {
			return err
		}
		var latest domain.Attempt
		if err := tx.Where("task_id = ?", t.ID).Order("attempt DESC").First(&latest).Error; err != nil {
			return err
		}
		if !Dispatchable(o, t, a, latest.ID) {
			return tx.Model(&o).Update("published_at", time.Now().UTC()).Error
		}
		err = fn(o, t, a)
		if err == nil {
			return tx.Model(&o).Update("published_at", time.Now().UTC()).Error
		}
		attempt := o.Attempt + 1
		delay := time.Duration(min(attempt*attempt, 60)) * time.Second
		if attempt >= 10 || (t.DeadlineAt != nil && time.Now().After(*t.DeadlineAt)) {
			if o.EventType == "DISPATCH" {
				// RPC completion can race with a committed queue ACK, cancellation
				// or retry. Lock and reread only after the RPC: holding these locks
				// during the call would deadlock the Core's own state transaction.
				var current domain.Task
				if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).First(&current, t.ID).Error; err != nil {
					return err
				}
				var currentAttempt domain.Attempt
				if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("task_id=?", t.ID).Order("attempt DESC").First(&currentAttempt).Error; err != nil {
					return err
				}
				if !Dispatchable(o, current, currentAttempt, currentAttempt.ID) {
					return tx.Model(&o).Updates(map[string]any{"published_at": time.Now().UTC(), "attempt": attempt}).Error
				}
				t, a = current, currentAttempt
				if err := tx.Model(&t).Updates(map[string]any{"collection_status": "FAILED", "error_code": "DISPATCH_UNAVAILABLE", "error_message": "采集控制面不可用，调度重试已耗尽", "updated_at": time.Now().UTC()}).Error; err != nil {
					return err
				}
				if err := tx.Model(&a).Updates(map[string]any{"status": "FAILED", "finished_at": time.Now().UTC(), "error_code": "DISPATCH_UNAVAILABLE", "raw_upload_url": "", "upload_targets": TerminalUploadMetadata(a)}).Error; err != nil {
					return err
				}
				e := domain.EventFor(t, &a.ID, "TASK_FAILED", map[string]string{"error_code": "DISPATCH_UNAVAILABLE"})
				if err := tx.Create(&e).Error; err != nil {
					return err
				}
			}
			return tx.Model(&o).Updates(map[string]any{"published_at": time.Now().UTC(), "attempt": attempt, "last_error": "DISPATCH_UNAVAILABLE"}).Error
		}
		return tx.Model(&o).Updates(map[string]any{"attempt": attempt, "last_error": "DISPATCH_UNAVAILABLE", "available_at": time.Now().Add(delay)}).Error
	})
}
func (r *Repository) SetUploadURL(ctx context.Context, id int64, url string) error {
	return r.persistActiveUploadGrant(ctx, id, "raw_upload_url", url)
}
func (r *Repository) Schedules(ctx context.Context, u domain.User) ([]domain.Schedule, error) {
	s := []domain.Schedule{}
	err := ResourceScope(r.Context(ctx), u, "schedules", false).Order("created_at DESC").Find(&s).Error
	return s, err
}
func (r *Repository) CreateSchedule(ctx context.Context, s *domain.Schedule) error {
	return r.Context(ctx).Create(s).Error
}
func (r *Repository) ToggleSchedule(ctx context.Context, u domain.User, id int64, enabled bool) error {
	var sc domain.Schedule
	if err := ResourceScope(r.Context(ctx), u, "schedules", true).Where("id=?", id).First(&sc).Error; err != nil {
		return err
	}
	next, err := domain.NextSchedule(sc.Cron, sc.Timezone, sc.IntervalMinutes, time.Now())
	if err != nil {
		return err
	}
	res := ResourceScope(r.Context(ctx).Model(&domain.Schedule{}), u, "schedules", true).Where("id = ?", id).Updates(map[string]any{"enabled": enabled, "next_run_at": next, "updated_at": time.Now().UTC()})
	if res.Error == nil && res.RowsAffected == 0 {
		return gorm.ErrRecordNotFound
	}
	return res.Error
}
func (r *Repository) TickSchedule(ctx context.Context, fn func(*Repository, domain.Schedule) (int64, error)) error {
	return r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var s domain.Schedule
		err := tx.Clauses(clause.Locking{Strength: "UPDATE", Options: "SKIP LOCKED"}).Where("enabled=true AND next_run_at<=?", time.Now()).Order("next_run_at ASC").First(&s).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		taskID, callErr := fn(New(tx), s)
		if callErr != nil {
			var de *domain.Error
			if !errors.As(callErr, &de) {
				return callErr
			}
			if err := tx.Exec("INSERT INTO schedule_records(schedule_id,scheduled_at,status,error_message,template_snapshot) VALUES(?,?,'FAILED',?,?) ON CONFLICT DO NOTHING", s.ID, s.NextRunAt, de.Code, string(s.Template)).Error; err != nil {
				return err
			}
		} else {
			if err := tx.Exec("INSERT INTO schedule_records(schedule_id,scheduled_at,task_id,status,template_snapshot) VALUES(?,?,?,'CREATED',?) ON CONFLICT DO NOTHING", s.ID, s.NextRunAt, taskID, string(s.Template)).Error; err != nil {
				return err
			}
		}
		next, err := domain.NextSchedule(s.Cron, s.Timezone, s.IntervalMinutes, time.Now())
		if err != nil {
			return err
		}
		return tx.Model(&s).Updates(map[string]any{"next_run_at": next, "last_run_at": s.NextRunAt, "updated_at": time.Now().UTC()}).Error
	})
}
