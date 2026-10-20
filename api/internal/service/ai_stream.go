package service

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"unicode"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

func (s *Suggestions) consumeAI(ctx context.Context, resp *http.Response, job AIRequest, owner string) (string, bool, error) {
	invalid := func() (string, bool, error) {
		return "", false, domain.Fail("AI_INVALID_RESPONSE", "模型响应不完整或超出资源限制")
	}
	if !strings.HasPrefix(strings.ToLower(resp.Header.Get("Content-Type")), "text/event-stream") {
		b, err := io.ReadAll(io.LimitReader(resp.Body, (64<<10)+1))
		if err != nil || len(b) > 64<<10 {
			return invalid()
		}
		var out struct {
			Choices []struct {
				Message struct {
					Content string `json:"content"`
				} `json:"message"`
			} `json:"choices"`
		}
		if json.Unmarshal(b, &out) != nil || len(out.Choices) == 0 {
			return invalid()
		}
		text := plainText(out.Choices[0].Message.Content)
		if len(text) == 0 || len(text) > 8000 {
			return invalid()
		}
		return text, false, nil
	}
	scanner := bufio.NewScanner(io.LimitReader(resp.Body, (256<<10)+1))
	scanner.Buffer(make([]byte, 4096), 16<<10)
	raw, emitted := "", ""
	chunks, total := 0, 0
	done := false
	for scanner.Scan() {
		line := scanner.Text()
		total += len(line) + 1
		if total > 256<<10 || ctx.Err() != nil {
			return invalid()
		}
		if line == "" || strings.HasPrefix(line, ":") {
			continue
		}
		if !strings.HasPrefix(line, "data:") {
			continue
		}
		data := strings.TrimSpace(strings.TrimPrefix(line, "data:"))
		if data == "[DONE]" {
			done = true
			break
		}
		var out struct {
			Error   json.RawMessage `json:"error"`
			Choices []struct {
				Delta struct {
					Content string `json:"content"`
				} `json:"delta"`
			} `json:"choices"`
		}
		if json.Unmarshal([]byte(data), &out) != nil || len(out.Error) > 0 {
			return invalid()
		}
		if len(out.Choices) == 0 {
			continue
		}
		piece := out.Choices[0].Delta.Content
		if piece == "" {
			continue
		}
		chunks++
		if chunks > 256 || len(raw)+len(piece) > 16000 {
			return invalid()
		}
		raw += piece
		safe := plainText(raw)
		if len(safe) > 8000 {
			return invalid()
		}
		cut := strings.LastIndexFunc(safe, unicode.IsSpace)
		if cut < 0 {
			continue
		}
		safe = safe[:cut+1]
		if !strings.HasPrefix(safe, emitted) {
			return invalid()
		}
		delta := strings.TrimPrefix(safe, emitted)
		if delta != "" {
			if err := s.streamEvent(ctx, job, owner, delta); err != nil {
				return "", true, err
			}
			emitted = safe
		}
	}
	if scanner.Err() != nil || !done {
		return invalid()
	}
	content := plainText(raw)
	if len(content) == 0 || len(content) > 8000 || !strings.HasPrefix(content, strings.TrimRightFunc(emitted, unicode.IsSpace)) {
		return invalid()
	}
	tail := strings.TrimPrefix(content, emitted)
	if tail != "" {
		if err := s.streamEvent(ctx, job, owner, tail); err != nil {
			return "", true, err
		}
	}
	return content, true, nil
}
func (s *Suggestions) streamEvent(ctx context.Context, job AIRequest, owner, text string) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var task domain.Task
		if err := tx.Raw("SELECT * FROM tasks WHERE id=? FOR UPDATE", job.TaskID).Scan(&task).Error; err != nil {
			return err
		}
		var current domain.Attempt
		if err := tx.Where("task_id=?", job.TaskID).Order("attempt DESC").First(&current).Error; err != nil {
			return err
		}
		if current.ID != job.AttemptID || task.AnalysisStatus != "SUCCESS" || task.CollectionStatus != "COLLECTED" {
			return domain.Fail("STALE_ATTEMPT", "执行已更新")
		}
		r := repository.New(tx)
		u, err := r.User(ctx, job.RequestedBy)
		if err != nil {
			return err
		}
		if _, err := r.AuthorizeTaskWrite(ctx, u, job.TaskID); err != nil {
			return domain.Fail("AUTHORIZATION_REVOKED", "授权已撤销")
		}
		var n int64
		if err := tx.Raw("SELECT id FROM ai_requests WHERE id=? AND status='running' AND lease_owner=? AND lease_expires_at>now() FOR UPDATE", job.ID, owner).Scan(&n).Error; err != nil {
			return err
		}
		if n == 0 {
			return domain.Fail("STALE_LEASE", "建议生成租约失效")
		}
		event := domain.EventFor(task, &job.AttemptID, "SUGGESTION_DELTA", map[string]any{"text": text, "ai_request_id": fmt.Sprint(job.ID), "streamed": true})
		return tx.Create(&event).Error
	})
}
