// Package repository：按当前采集 attempt、RAW 和分析运行身份读取不完整结果。
// 重分析或重试会改变身份，旧检查点即使仍在磁盘或数据库中，也不能作为当前页面证据返回。
package repository

import (
	"context"
	"encoding/json"

	"drop/api/internal/domain"
	"gorm.io/gorm"
)

// 分析身份必须属于最新采集 attempt 和最新 ready RAW 制品。
// 此查询边界不向浏览器暴露对象键、租约持有者或解析器检查点路径。
const currentAnalysisIdentity = `
 FROM analysis_jobs j
 JOIN tasks t ON t.id=j.task_id
 JOIN task_attempts a ON a.id=j.attempt_id AND a.task_id=t.id
 JOIN LATERAL (
   SELECT id,sha256 FROM artifacts
   WHERE task_id=t.id AND attempt_id=a.id AND kind='RAW' AND status='ready'
   ORDER BY id DESC LIMIT 1
 ) raw ON true
 WHERE t.id=? AND t.deleted_at IS NULL AND t.collection_status='COLLECTED'
 AND a.id=(SELECT id FROM task_attempts WHERE task_id=t.id ORDER BY attempt DESC LIMIT 1)
 AND j.attempt BETWEEN 1 AND 2147483647`

func (r *Repository) AnalysisIdentity(ctx context.Context, id int64) (*domain.AnalysisIdentity, error) {
	var value domain.AnalysisIdentity
	query := r.Context(ctx).Raw(`SELECT j.id::text AS analysis_job_id,j.attempt AS analysis_run,
 raw.id::text AS input_artifact_id,lower(raw.sha256) AS input_digest`+currentAnalysisIdentity, id).Scan(&value)
	if query.Error != nil {
		return nil, query.Error
	}
	if query.RowsAffected == 0 {
		return nil, nil
	}
	return &value, nil
}

// PartialResult 读取当前分析身份绑定的部分结果，再通过领域白名单解码。
// 身份不匹配或结果不可用时不回退到历史内容，避免旧 attempt 的排障线索冒充当前测量。
func (r *Repository) PartialResult(ctx context.Context, id int64) (*domain.PartialResult, error) {
	var row struct{ Result json.RawMessage }
	query := r.Context(ctx).Raw(`SELECT j.result`+currentAnalysisIdentity+`
 AND t.analysis_status='FAILED' AND j.status='failed'
 AND j.lease_owner IS NULL AND j.lease_expires_at IS NULL
 AND octet_length(j.result::text)<=?
 AND j.result->>'completeness'='partial'
 AND j.result->>'task_id'=t.id::text
 AND j.result->>'attempt_id'=a.id::text
 AND j.result->>'analysis_job_id'=j.id::text
 AND j.result->>'analysis_run'=j.attempt::text
 AND j.result->>'kind'=t.kind
 AND j.result->>'input_artifact_id'=raw.id::text
 AND j.result->>'input_digest'=lower(raw.sha256)`, id, domain.MaxPartialResultBytes).Scan(&row)
	if query.Error != nil {
		return nil, query.Error
	}
	if query.RowsAffected == 0 {
		return nil, gorm.ErrRecordNotFound
	}
	value, err := domain.DecodePartialResult(row.Result)
	if err != nil {
		return nil, gorm.ErrRecordNotFound
	}
	return &value, nil
}
