// Package repository：将 Outbox、逻辑任务、执行 attempt 和最新 attempt 身份进行纯比较。
// 这个栅栏让迟到 RPC 或重放消息只能影响原轮次，避免重试之后发生反向覆盖。
package repository

import "drop/api/internal/domain"

// Dispatchable 把延迟的 Outbox 消息限制在创建它的 attempt 内；新一轮重试不能被旧消息派发或取消。
func Dispatchable(o domain.Outbox, t domain.Task, a domain.Attempt, latestID int64) bool {
	if o.AttemptID != latestID || a.ID != latestID {
		return false
	}
	if o.EventType == "CANCEL" {
		return t.CollectionStatus == "CANCELED" && a.Status == "CANCELED"
	}
	return o.EventType == "DISPATCH" && t.CollectionStatus == "CREATED" && a.Status == "CREATED"
}
