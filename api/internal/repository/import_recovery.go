// Package repository：收敛超过截止时间的未完成导入，防止任务永久停留在创建状态。
// 同一个导入在截止前仍可按原幂等键续传，数据库不承担原始正文的长期保存。
package repository

import (
	"context"
	"errors"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"

	"drop/api/internal/domain"
)

// 导入正文有意不存入 SQL，避免数据库长期保存大文件或原始性能内容。
// 上传中断后，客户端可在有限截止时间内携带原幂等键重放；过期记录由恢复任务收敛。
func (r *Repository) SweepStaleImport(ctx context.Context) error {
	return r.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var task domain.Task
		err := tx.Clauses(clause.Locking{Strength: "UPDATE", Options: "SKIP LOCKED"}).Where("target_agent_id IS NULL AND collection_status='CREATED' AND collection_deadline < ?", time.Now()).Order("collection_deadline ASC").First(&task).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		var attempt domain.Attempt
		if err := tx.Where("task_id = ?", task.ID).Order("attempt DESC").First(&attempt).Error; err != nil {
			return err
		}
		now := time.Now().UTC()
		if err := tx.Model(&task).Updates(map[string]any{"collection_status": "FAILED", "analysis_status": "FAILED", "error_code": "IMPORT_TIMEOUT", "error_message": "导入未在期限内完成，请重新提交原始折叠栈", "updated_at": now, "version": gorm.Expr("version + 1")}).Error; err != nil {
			return err
		}
		if err := tx.Model(&attempt).Updates(map[string]any{"status": "FAILED", "error_code": "IMPORT_TIMEOUT", "error_message": "Import body was not durably uploaded before deadline", "finished_at": now, "raw_upload_url": "", "upload_targets": "[]"}).Error; err != nil {
			return err
		}
		event := domain.EventFor(task, &attempt.ID, "TASK_FAILED", map[string]string{"error_code": "IMPORT_TIMEOUT", "collection_status": "FAILED"})
		return tx.Create(&event).Error
	})
}
