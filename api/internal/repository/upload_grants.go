// Package repository：管理 attempt 级临时上传授权及终态后的最小日志宽限。
// 对象键、种类、过期和所属轮次共同组成授权，不能仅凭同一个 task 前缀接纳任意文件。
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

// TerminalLogGrace 与 Core 对当前 attempt 迟到 LOG 的接纳窗口保持一致。
// 它既不延长预签名上传 URL，也不允许终态后发布 RAW 或 BINARY。
const TerminalLogGrace = 120 * time.Second

type terminalLogGrant struct {
	Name        string `json:"name"`
	Kind        string `json:"kind"`
	ObjectKey   string `json:"object_key"`
	Format      string `json:"format"`
	ContentType string `json:"content_type"`
	MaxFileSize int64  `json:"max_file_bytes"`
}

// TerminalUploadMetadata 仅保留之前已授权的一份有界公开诊断日志身份。
// 重新按白名单构造元数据，移除全部上传 URL 和未知字段；畸形、重复或跨 attempt 的授权一律拒绝。
// 实际制品大小与哈希仍由 Core 独立核验，元数据清洗不能代替完整性检查。
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

// CleanupUploadGrants 在采集结束后清理已持久化的传输 URL，
// 立即清除已被替代或删除 attempt 的授权，并在固定宽限期后清退当前 LOG 元数据。
// 每次最多锁定 200 个 attempt，避免清理任务长时间阻塞业务事务。
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
