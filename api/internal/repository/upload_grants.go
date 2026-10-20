package repository

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"drop/api/internal/domain"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// TerminalLogGrace matches the Core's current-attempt-only late LOG admission.
// It never extends a presigned upload URL or allows RAW/BINARY publication.
const TerminalLogGrace = 120 * time.Second

type terminalLogGrant struct {
	Name        string `json:"name"`
	Kind        string `json:"kind"`
	ObjectKey   string `json:"object_key"`
	Format      string `json:"format"`
	ContentType string `json:"content_type"`
	MaxFileSize int64  `json:"max_file_bytes"`
}

// TerminalUploadMetadata retains the identity of one previously authorized,
// bounded public diagnostic log. A fresh whitelist-shaped value removes every
// upload URL/unknown field; malformed, duplicate, or cross-attempt grants fail
// closed. Actual artifact size/hash are verified separately by Core.
func TerminalUploadMetadata(a domain.Attempt) json.RawMessage {
	if a.TaskID <= 0 || a.Attempt <= 0 || a.AgentID == nil || *a.AgentID == "" || len(a.UploadTargets) > 65536 {
		return json.RawMessage("[]")
	}
	var grants []terminalLogGrant
	if err := json.Unmarshal(a.UploadTargets, &grants); err != nil || len(grants) > 16 {
		return json.RawMessage("[]")
	}
	expected := fmt.Sprintf("raw/tasks/%d/attempts/%d/runner.log", a.TaskID, a.Attempt)
	var retained *terminalLogGrant
	for _, grant := range grants {
		if grant.Name != "runner.log" || grant.Kind != "LOG" || grant.ObjectKey != expected || grant.Format != "log_jsonl" || grant.ContentType != "text/plain" || grant.MaxFileSize != 16384 {
			continue
		}
		if retained != nil {
			return json.RawMessage("[]")
		}
		copy := grant
		retained = &copy
	}
	if retained == nil {
		return json.RawMessage("[]")
	}
	encoded, _ := json.Marshal([]terminalLogGrant{*retained})
	return encoded
}

func (r *Repository) persistActiveUploadGrant(ctx context.Context, id int64, column string, value string) error {
	result := r.Context(ctx).Model(&domain.Attempt{}).Where("id=? AND status='CREATED' AND EXISTS(SELECT 1 FROM tasks t WHERE t.id=task_attempts.task_id AND t.collection_status='CREATED' AND t.deleted_at IS NULL) AND NOT EXISTS(SELECT 1 FROM task_attempts newer WHERE newer.task_id=task_attempts.task_id AND newer.attempt>task_attempts.attempt)", id).Update(column, value)
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return domain.Fail("INVALID_STATE", "任务已结束或执行尝试已过期")
	}
	return nil
}

func (r *Repository) SetUploadTargets(ctx context.Context, id int64, grants json.RawMessage) error {
	if len(grants) > 65536 || !json.Valid(grants) {
		return domain.Fail("INVALID_PARAMETERS", "上传授权元数据无效")
	}
	return r.persistActiveUploadGrant(ctx, id, "upload_targets", string(grants))
}

// CleanupUploadGrants removes persisted transport URLs after collection ends,
// clears superseded/deleted attempts immediately, and retires the current LOG
// metadata after its fixed grace. Each pass locks at most 200 attempt rows.
func (r *Repository) CleanupUploadGrants(ctx context.Context) error {
	return r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var rows []struct {
			domain.Attempt
			TaskDeadline  *time.Time
			TaskDeletedAt *time.Time
			LatestAttempt int
		}
		if err := tx.Table("task_attempts AS a").Select("a.*, t.collection_deadline AS task_deadline, t.deleted_at AS task_deleted_at, (SELECT max(x.attempt) FROM task_attempts x WHERE x.task_id=a.task_id) AS latest_attempt").Joins("JOIN tasks t ON t.id=a.task_id").Where("(a.raw_upload_url<>'' OR a.upload_targets<>'[]'::jsonb) AND (a.status IN ('COLLECTED','FAILED','CANCELED') OR t.collection_status IN ('COLLECTED','FAILED','CANCELED') OR t.deleted_at IS NOT NULL OR a.attempt<>(SELECT max(x.attempt) FROM task_attempts x WHERE x.task_id=a.task_id))").Clauses(clause.Locking{Strength: "UPDATE", Table: clause.Table{Name: "a"}, Options: "SKIP LOCKED"}).Order("a.finished_at ASC NULLS FIRST,a.id ASC").Limit(200).Find(&rows).Error; err != nil {
			return err
		}
		now := time.Now().UTC()
		for _, row := range rows {
			metadata := json.RawMessage("[]")
			finished := row.FinishedAt
			if finished == nil {
				finished = row.TaskDeadline
			}
			if row.Attempt.Attempt == row.LatestAttempt && row.TaskDeletedAt == nil && finished != nil && now.Before(finished.Add(TerminalLogGrace)) {
				metadata = TerminalUploadMetadata(row.Attempt)
			}
			if err := tx.Model(&domain.Attempt{}).Where("id=?", row.ID).Updates(map[string]any{"raw_upload_url": "", "upload_targets": string(metadata)}).Error; err != nil {
				return err
			}
		}
		return nil
	})
}
