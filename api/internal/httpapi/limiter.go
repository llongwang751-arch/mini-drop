// Package httpapi：有界内存固定窗口限流器，供没有仓储依赖的路由测试使用。
// 它不承担多实例生产配额，生产准入以共享仓储为准。
package httpapi

import (
	"sync"
	"time"
)

type bucket struct {
	count int
	reset time.Time
}
type Limiter struct {
	mu      sync.Mutex
	entries map[string]bucket
	limit   int
	window  time.Duration
}

func NewLimiter(limit int, window time.Duration) *Limiter {
	return &Limiter{entries: map[string]bucket{}, limit: limit, window: window}
}
func (l *Limiter) Allow(key string, now time.Time) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if len(l.entries) > 10000 {
		for k, v := range l.entries {
			if now.After(v.reset) {
				delete(l.entries, k)
			}
		}
		if len(l.entries) > 10000 {
			return false
		}
	}
	b := l.entries[key]
	if !now.Before(b.reset) {
		b = bucket{reset: now.Add(l.window)}
	}
	b.count++
	l.entries[key] = b
	return b.count <= l.limit
}
