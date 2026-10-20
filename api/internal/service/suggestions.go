// Package service：管理模型建议请求的准入、脱敏输入和持久化工作器。
// 用户同意外发不代表模型获得平台写权限；模型输出仍需程序校验，失败也不能改变原测量结果。
package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"golang.org/x/net/html"
	"gorm.io/gorm"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

type AIConfig struct {
	Endpoint, APIKey, Model string
	AgentBackend            string
	AllowHTTP               bool
}
type Suggestions struct {
	Repo   *repository.Repository
	Tasks  *Tasks
	Config AIConfig
	Client *http.Client
	Code   *CodeExecutor
	Runner *AgentRunner
}

func NewSuggestions(r *repository.Repository, t *Tasks, c AIConfig) (*Suggestions, error) {
	if c.AgentBackend == "" {
		c.AgentBackend = "legacy"
	}
	if c.AgentBackend != "legacy" && c.AgentBackend != "agentscope" {
		return nil, errors.New("AI_AGENT_BACKEND must be legacy or agentscope")
	}
	if c.Endpoint != "" {
		u, err := url.Parse(c.Endpoint)
		if err != nil || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Scheme != "https" && !(c.AllowHTTP && u.Scheme == "http")) || c.Model == "" {
			return nil, errors.New("AI endpoint requires a trusted HTTPS URL and model")
		}
	}
	return &Suggestions{Repo: r, Tasks: t, Config: c, Client: &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}

// AgentAvailable 检查当前选择的模型后端及必要依赖是否就绪，避免 UI 宣称可以启动实际不可用的 Agent 工作流。
func (s *Suggestions) AgentAvailable() bool {
	if s == nil {
		return false
	}
	if s.Config.AgentBackend == "agentscope" {
		return s.Runner != nil && s.Config.Model != ""
	}
	return s.Config.Endpoint != ""
}

type AIRequest struct {
	ID             int64      `json:"id,string"`
	TaskID         int64      `json:"task_id,string"`
	AttemptID      int64      `json:"attempt_id,string"`
	RequestedBy    string     `json:"requested_by"`
	InputDigest    string     `json:"input_digest"`
	Status         string     `json:"status"`
	AvailableAt    time.Time  `json:"-"`
	LeaseOwner     *string    `json:"-"`
	LeaseExpiresAt *time.Time `json:"-"`
	ErrorCode      *string    `json:"error_code,omitempty"`
	CreatedAt      time.Time  `json:"created_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
}

// RedactedSummary 不发送原始性能文件、符号、路径、主机、PID、组或用户标识，只允许脱敏统计进入模型上下文。
func RedactedSummary(raw json.RawMessage) ([]byte, error) {
	var result struct {
		Kind         string `json:"kind"`
		TotalSamples int64  `json:"total_samples"`
		NodeCount    int64  `json:"node_count"`
		Top          []struct {
			Name         string  `json:"name"`
			SelfPercent  float64 `json:"self_percent"`
			TotalPercent float64 `json:"total_percent"`
		} `json:"top_functions"`
	}
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, err
	}
	top := []map[string]any{}
	for i, f := range result.Top {
		if i >= 20 {
			break
		}
		top = append(top, map[string]any{"symbol": "symbol_" + repository.Digest(f.Name)[:12], "self_percent": f.SelfPercent, "total_percent": f.TotalPercent})
	}
	return json.Marshal(map[string]any{"schema_version": 1, "kind": result.Kind, "total_weight": result.TotalSamples, "node_count": result.NodeCount, "top_functions": top, "limitations": []string{"Sample weights are not request latency; no root cause or improvement is proven.", "Raw profiles and symbol names are excluded. Symbol hashes are pseudonyms, not a guarantee of anonymization."}})
}
func (s *Suggestions) Request(ctx context.Context, u domain.User, id int64, consent bool, request string) (AIRequest, error) {
	var job AIRequest
	if !consent {
		return job, domain.Fail("CONSENT_REQUIRED", "外部模型仅接收脱敏统计，需明确同意")
	}
	if s.Config.Endpoint == "" {
		return job, domain.Fail("AI_UNAVAILABLE", "未配置外部模型")
	}
	if _, err := s.Repo.AuthorizeTaskWrite(ctx, u, id); err != nil {
		return job, err
	}
	raw, err := s.Repo.Result(ctx, id)
	if err != nil {
		return job, domain.Fail("RESULT_NOT_READY", "分析结果尚未完成")
	}
	summary, err := RedactedSummary(raw)
	if err != nil {
		return job, err
	}
	attempt, err := s.Repo.LatestAttempt(ctx, id)
	if err != nil {
		return job, err
	}
	digest := repository.Digest(string(summary))
	err = s.Repo.Context(ctx).Raw("INSERT INTO ai_requests(task_id,attempt_id,requested_by,input_digest) VALUES(?,?,?,?) ON CONFLICT(task_id,attempt_id,input_digest) DO UPDATE SET input_digest=excluded.input_digest RETURNING *", id, attempt.ID, u.ID, digest).Scan(&job).Error
	if err != nil {
		return job, err
	}
	return job, s.Repo.Audit(ctx, Audit(u, "suggestion.ai_request", "task", fmt.Sprint(id), request))
}
func (s *Suggestions) Run(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_ = s.process(ctx)
		}
	}
}
func (s *Suggestions) process(ctx context.Context) error {
	if s.Config.Endpoint == "" {
		return nil
	}
	owner := RandomToken()
	var job AIRequest
	if err := s.Repo.Context(ctx).Exec("UPDATE ai_requests SET status='failed',error_code='AI_RETRY_EXHAUSTED',lease_owner=NULL,lease_expires_at=NULL WHERE status='running' AND lease_expires_at<now() AND attempts>=3").Error; err != nil {
		return err
	}
	err := s.Repo.Context(ctx).Raw(`UPDATE ai_requests SET status='running',attempts=attempts+1,lease_owner=?,lease_expires_at=now()+interval '60 seconds',updated_at=now() WHERE id=(SELECT id FROM ai_requests WHERE (status='pending' OR (status='running' AND lease_expires_at<now())) AND attempts<3 ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *`, owner).Scan(&job).Error
	if err != nil || job.ID == 0 {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 35*time.Second)
	defer cancel()
	fail := func(code string) error {
		return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
			res := tx.Exec("UPDATE ai_requests SET status='failed',error_code=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=? AND lease_owner=?", code, job.ID, owner)
			if res.Error != nil {
				return res.Error
			}
			if res.RowsAffected == 0 {
				return nil
			}
			event := domain.EventFor(domain.Task{ID: job.TaskID}, &job.AttemptID, "SUGGESTION_FAILED", map[string]any{"engine": "ai", "error_code": code, "ai_request_id": fmt.Sprint(job.ID)})
			return tx.Create(&event).Error
		})
	}
	u, err := s.Repo.User(ctx, job.RequestedBy)
	if err != nil {
		return fail("AUTHORIZATION_REVOKED")
	}
	t, err := s.Repo.AuthorizeTaskWrite(ctx, u, job.TaskID)
	if err != nil {
		return fail("AUTHORIZATION_REVOKED")
	}
	attempt, err := s.Repo.LatestAttempt(ctx, t.ID)
	if err != nil || attempt.ID != job.AttemptID || t.AnalysisStatus != "SUCCESS" {
		return fail("STALE_ATTEMPT")
	}
	raw, err := s.Repo.Result(ctx, t.ID)
	if err != nil {
		return fail("RESULT_NOT_READY")
	}
	summary, err := RedactedSummary(raw)
	if err != nil || repository.Digest(string(summary)) != job.InputDigest {
		return fail("INPUT_CHANGED")
	}
	body, _ := json.Marshal(map[string]any{"model": s.Config.Model, "stream": true, "temperature": 0, "max_tokens": 1200, "messages": []map[string]string{{"role": "system", "content": "You are a performance reviewer. Return concise plain text with observations, uncertainty and next measurements. Use only supplied statistics. Never claim latency, causality, lost samples or optimization benefits from sample weights. Do not output HTML or links."}, {"role": "user", "content": string(summary)}}})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.Config.Endpoint, bytes.NewReader(body))
	if err != nil {
		return fail("AI_CONFIGURATION_ERROR")
	}
	req.Header.Set("Content-Type", "application/json")
	if s.Config.APIKey != "" {
		req.Header.Set("Authorization", "Bearer "+s.Config.APIKey)
	}
	resp, err := s.Client.Do(req)
	if err != nil {
		return fail("AI_UPSTREAM_ERROR")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fail("AI_UPSTREAM_ERROR")
	}
	content, streamed, err := s.consumeAI(ctx, resp, job, owner)
	if err != nil {
		var de *domain.Error
		if errors.As(err, &de) {
			return fail(de.Code)
		}
		return fail("AI_INVALID_RESPONSE")
	}
	finishErr := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		r := repository.New(tx)
		fresh, err := r.User(ctx, u.ID)
		if err != nil {
			return err
		}
		if _, err := r.AuthorizeTaskWrite(ctx, fresh, t.ID); err != nil {
			return domain.Fail("AUTHORIZATION_REVOKED", "授权已撤销")
		}
		var locked domain.Task
		if err := tx.Raw("SELECT * FROM tasks WHERE id=? FOR UPDATE", t.ID).Scan(&locked).Error; err != nil {
			return err
		}
		var current domain.Attempt
		if err := tx.Where("task_id=?", t.ID).Order("attempt DESC").First(&current).Error; err != nil {
			return err
		}
		if current.ID != job.AttemptID || locked.AnalysisStatus != "SUCCESS" {
			return domain.Fail("STALE_ATTEMPT", "任务执行已更新")
		}
		res := tx.Exec("UPDATE ai_requests SET status='success',lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=? AND lease_owner=? AND lease_expires_at>now()", job.ID, owner)
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected != 1 {
			return domain.Fail("STALE_LEASE", "建议生成租约失效")
		}
		evidence, _ := json.Marshal(map[string]any{"input_digest": job.InputDigest, "streamed": streamed, "confidence_calibrated": false, "redaction": "numeric_and_hashed_symbols_v1", "model": s.Config.Model, "limitations": []string{"外部模型建议，未验证因果或收益；置信度未校准"}})
		if err := tx.Exec("INSERT INTO suggestions(task_id,attempt_id,engine,version,severity,title,content,evidence,confidence,input_digest) VALUES(?,?,'ai',?,'info','模型辅助诊断',?,?,0,?)", t.ID, job.AttemptID, s.Config.Model, content, string(evidence), job.InputDigest).Error; err != nil {
			return err
		}
		if err := tx.Model(&locked).Update("updated_at", time.Now().UTC()).Error; err != nil {
			return err
		}
		event := domain.EventFor(t, &job.AttemptID, "SUGGESTION_CREATED", map[string]any{"engine": "ai", "version": s.Config.Model, "input_digest": job.InputDigest, "streamed": streamed, "ai_request_id": fmt.Sprint(job.ID)})
		return tx.Create(&event).Error
	})
	var de *domain.Error
	if errors.As(finishErr, &de) {
		return fail(de.Code)
	}
	return finishErr
}
func plainText(in string) string {
	z := html.NewTokenizer(strings.NewReader(in))
	var b strings.Builder
	skip := 0
	for {
		tt := z.Next()
		if tt == html.ErrorToken {
			break
		}
		if tt == html.StartTagToken {
			tag, _ := z.TagName()
			if string(tag) == "script" || string(tag) == "style" {
				skip++
			}
		}
		if tt == html.EndTagToken {
			tag, _ := z.TagName()
			if (string(tag) == "script" || string(tag) == "style") && skip > 0 {
				skip--
			}
		}
		if tt == html.TextToken && skip == 0 {
			b.Write(z.Text())
		}
	}
	return strings.TrimSpace(b.String())
}
