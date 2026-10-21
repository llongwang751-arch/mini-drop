// 检查点保存可见的 AgentScope 上下文与控制面账本，不保存 capability、密钥或隐藏思维链。
// 每笔操作先执行调用方提供的业务事务栅栏，再锁 checkpoint、最后锁工具回执；旧租约不能提交新游标。
// 工具先记 pending 再执行，确认完成后才保存响应。进程在两者之间消失时拒绝盲重放，不承诺跨外部服务 exactly-once。
package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// AgentCheckpointBinding 将恢复范围绑定到一个持久阶段及原始输入摘要；运行时 capability 每次重新生成。
type AgentCheckpointBinding struct {
	ScopeType   string
	ScopeID     int64
	InputDigest string
}

// AgentCheckpointStore 的 Fence 必须在传入事务中重新校验来源、权限、attempt 与当前 lease。
// ScopeID 只用于查表，不能代替业务 Fence；因此未提供 Fence 的存储适配器直接拒绝工作。
type AgentCheckpointStore struct {
	Repo    *repository.Repository
	Binding AgentCheckpointBinding
	Fence   func(context.Context, *gorm.DB) error
}

func NewAgentCheckpointStore(repo *repository.Repository, binding AgentCheckpointBinding, fence func(context.Context, *gorm.DB) error) *AgentCheckpointStore {
	return &AgentCheckpointStore{Repo: repo, Binding: binding, Fence: fence}
}

// agentCheckpointEnvelope 将 Go 权威账本与 Python 的可见 SDK 上下文分开；Python 不得覆盖已接受结果或结束轮次。
type agentCheckpointEnvelope struct {
	SDK         json.RawMessage `json:"sdk,omitempty"`
	EndedRounds []int           `json:"ended_rounds,omitempty"`
	Accepted    string          `json:"accepted_json,omitempty"`
	ActiveMS    int64           `json:"active_ms,omitempty"`
}
type agentCheckpointRow struct {
	ScopeType   string
	ScopeID     int64
	InputDigest string
	SpecDigest  string
	Sequence    int64
	ModelCalls  int
	ToolCalls   int
	RoundTools  int
	FormatOnly  bool
	State       json.RawMessage
	Usage       json.RawMessage
	ExpiresAt   time.Time
	UpdatedAt   time.Time
}

func (agentCheckpointRow) TableName() string { return "agent_checkpoints" }

type agentToolReceipt struct {
	ScopeType     string
	ScopeID       int64
	CallID        string
	RequestDigest string
	Round         int
	Status        string
	Response      json.RawMessage
}

func (agentToolReceipt) TableName() string { return "agent_tool_receipts" }

// agentCheckpointSnapshot 中的计数来自 Go 事务账本；恢复不能用 Python 提交的数字补回预算。
type agentCheckpointSnapshot struct {
	Sequence         int64           `json:"sequence"`
	ModelCalls       int             `json:"model_calls"`
	ToolCalls        int             `json:"tool_calls"`
	RoundTools       int             `json:"round_tools"`
	FormatOnly       bool            `json:"format_only"`
	State            json.RawMessage `json:"state"`
	Usage            json.RawMessage `json:"usage"`
	CompletedCallIDs []string        `json:"completed_call_ids"`
	Accepted         json.RawMessage `json:"-"`
	AcceptedJSON     string          `json:"accepted_json,omitempty"`
	ExpiresAt        time.Time       `json:"-"`
	EndedRounds      []int           `json:"-"`
	ActiveMS         int64           `json:"-"`
}

func agentCheckpointError(code string) error {
	return domain.Fail(code, "Agent检查点不可安全恢复，请检查本次运行的授权、来源及执行回执")
}

func (s *AgentCheckpointStore) withRow(ctx context.Context, fn func(*gorm.DB, *agentCheckpointRow, *agentCheckpointEnvelope) error) error {
	if s == nil || s.Repo == nil || s.Fence == nil || s.Binding.ScopeID <= 0 || s.Binding.InputDigest == "" || !strings.Contains("|diagnosis|planning|repair|", "|"+s.Binding.ScopeType+"|") {
		return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
	}
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := s.Fence(ctx, tx); err != nil {
			return err
		}
		var row agentCheckpointRow
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("scope_type=? AND scope_id=?", s.Binding.ScopeType, s.Binding.ScopeID).First(&row).Error; err != nil {
			return err
		}
		if row.InputDigest != s.Binding.InputDigest {
			return agentCheckpointError("INPUT_CHANGED")
		}
		if !row.ExpiresAt.After(time.Now()) {
			return agentCheckpointError("AI_BUDGET_EXCEEDED")
		}
		var envelope agentCheckpointEnvelope
		if !decodeAgentJSON(row.State, &envelope) {
			return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
		}
		return fn(tx, &row, &envelope)
	})
}

func (s *AgentCheckpointStore) load(ctx context.Context, specDigest string, budget agentBudget) (agentCheckpointSnapshot, error) {
	var snapshot agentCheckpointSnapshot
	if s == nil || s.Repo == nil || s.Fence == nil {
		return snapshot, agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
	}
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := s.Fence(ctx, tx); err != nil {
			return err
		}
		return tx.Exec(`INSERT INTO agent_checkpoints(scope_type,scope_id,input_digest,spec_digest,expires_at) VALUES(?,?,?,?,clock_timestamp()+interval '24 hours') ON CONFLICT(scope_type,scope_id) DO NOTHING`, s.Binding.ScopeType, s.Binding.ScopeID, s.Binding.InputDigest, specDigest).Error
	})
	if err != nil {
		return snapshot, err
	}
	err = s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, envelope *agentCheckpointEnvelope) error {
		if row.SpecDigest != specDigest {
			return agentCheckpointError("INPUT_CHANGED")
		}
		if row.ModelCalls > budget.MaxModelCalls || row.ToolCalls > budget.MaxToolCalls || row.RoundTools > budget.MaxToolsPerRound {
			return agentCheckpointError("AI_BUDGET_EXCEEDED")
		}
		var pending int64
		if err := tx.Model(&agentToolReceipt{}).Where("scope_type=? AND scope_id=? AND status='pending'", row.ScopeType, row.ScopeID).Count(&pending).Error; err != nil {
			return err
		}
		if pending > 0 {
			return agentCheckpointError("AGENT_CHECKPOINT_AMBIGUOUS")
		}
		completed := []string{}
		if err := tx.Model(&agentToolReceipt{}).Where("scope_type=? AND scope_id=? AND status='complete'", row.ScopeType, row.ScopeID).Order("created_at,call_id").Pluck("call_id", &completed).Error; err != nil {
			return err
		}
		state := envelope.SDK
		if len(state) == 0 {
			state = json.RawMessage(`{}`)
		}
		snapshot = agentCheckpointSnapshot{Sequence: row.Sequence, ModelCalls: row.ModelCalls, ToolCalls: row.ToolCalls, RoundTools: row.RoundTools, FormatOnly: row.FormatOnly, State: state, Usage: row.Usage, CompletedCallIDs: completed, Accepted: json.RawMessage(envelope.Accepted), AcceptedJSON: envelope.Accepted, ExpiresAt: row.ExpiresAt, EndedRounds: envelope.EndedRounds, ActiveMS: envelope.ActiveMS}
		return nil
	})
	return snapshot, err
}

// reserveActive 先持久化小时间片再让会话运行；崩溃和提前续片的未使用部分一律不退款。
// 因此租约等待不计入执行预算，连续崩溃也不能反复获得免费的 provider/tool 执行时间。
func (s *AgentCheckpointStore) reserveActive(ctx context.Context, limit int64) (int64, error) {
	var granted int64
	err := s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		left := limit - e.ActiveMS
		if left <= 0 {
			return agentCheckpointError("AI_BUDGET_EXCEEDED")
		}
		granted = 5000
		if granted > left {
			granted = left
		}
		e.ActiveMS += granted
		return persistAgentCheckpoint(tx, row, e)
	})
	return granted, err
}

func persistAgentCheckpoint(tx *gorm.DB, row *agentCheckpointRow, envelope *agentCheckpointEnvelope) error {
	state, err := json.Marshal(envelope)
	if err != nil || len(state) > 262144 {
		return agentCheckpointError("AGENT_CHECKPOINT_TOO_LARGE")
	}
	return tx.Model(&agentCheckpointRow{}).Where("scope_type=? AND scope_id=?", row.ScopeType, row.ScopeID).Updates(map[string]any{"sequence": row.Sequence, "model_calls": row.ModelCalls, "tool_calls": row.ToolCalls, "round_tools": row.RoundTools, "format_only": row.FormatOnly, "state": string(state), "usage": string(row.Usage), "updated_at": time.Now().UTC()}).Error
}

// saveSDK 使用独立递增游标，拒绝旧进程覆盖新上下文；计数、回执和 accepted 均由 Go 保持。
func (s *AgentCheckpointStore) saveSDK(ctx context.Context, sequence int64, state json.RawMessage) error {
	if !validAgentCheckpointSDK(state) {
		return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
	}
	return s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		if sequence != row.Sequence+1 {
			return agentCheckpointError("AGENT_CHECKPOINT_STALE")
		}
		if len(e.Accepted) > 0 {
			return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
		}
		row.Sequence = sequence
		e.SDK = append(json.RawMessage(nil), state...)
		return persistAgentCheckpoint(tx, row, e)
	})
}

func (s *AgentCheckpointStore) reserveModel(ctx context.Context, round int, budget agentBudget, phase string) error {
	return s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		if round != row.ModelCalls+1 || round > budget.MaxModelCalls || phase == "diagnosis" && round == budget.MaxModelCalls && !row.FormatOnly || len(e.Accepted) > 0 {
			return agentCheckpointError("AI_BUDGET_EXCEEDED")
		}
		row.ModelCalls = round
		row.RoundTools = 0
		return persistAgentCheckpoint(tx, row, e)
	})
}

func (s *AgentCheckpointStore) endModel(ctx context.Context, round int, usage json.RawMessage) error {
	return s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		if round != row.ModelCalls {
			return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
		}
		for _, old := range e.EndedRounds {
			if old == round {
				return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
			}
		}
		if len(usage) > 0 {
			var totals, delta map[string]int64
			if json.Unmarshal(row.Usage, &totals) != nil || json.Unmarshal(usage, &delta) != nil || !validAgentUsage(usage) {
				return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
			}
			if totals == nil {
				totals = map[string]int64{}
			}
			for key, value := range delta {
				totals[key] += value
				if totals[key] > 1000000000 {
					return agentCheckpointError("AI_BUDGET_EXCEEDED")
				}
			}
			row.Usage, _ = json.Marshal(totals)
		}
		e.EndedRounds = append(e.EndedRounds, round)
		return persistAgentCheckpoint(tx, row, e)
	})
}

func (s *AgentCheckpointStore) replay(ctx context.Context, call agentCallback) (json.RawMessage, bool, error) {
	var response json.RawMessage
	found := false
	canonical, _ := json.Marshal(call)
	canonical, _ = canonicalAgentJSON(canonical)
	digest := repository.DigestBytes(canonical)
	err := s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		var receipt agentToolReceipt
		err := tx.Where("scope_type=? AND scope_id=? AND call_id=?", row.ScopeType, row.ScopeID, call.CallID).First(&receipt).Error
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil
		}
		if err != nil {
			return err
		}
		if receipt.RequestDigest != digest {
			return agentCheckpointError("AGENT_CALLBACK_REPLAY")
		}
		if receipt.Status != "complete" {
			return agentCheckpointError("AGENT_CHECKPOINT_AMBIGUOUS")
		}
		response = append(json.RawMessage(nil), receipt.Response...)
		found = true
		return nil
	})
	return response, found, err
}

func (s *AgentCheckpointStore) beginTool(ctx context.Context, call agentCallback, budget agentBudget) error {
	return s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		if call.Round != row.ModelCalls || row.FormatOnly || row.ToolCalls >= budget.MaxToolCalls || row.RoundTools >= budget.MaxToolsPerRound {
			return agentCheckpointError("AI_BUDGET_EXCEEDED")
		}
		canonical, _ := json.Marshal(call)
		canonical, _ = canonicalAgentJSON(canonical)
		receipt := agentToolReceipt{ScopeType: row.ScopeType, ScopeID: row.ScopeID, CallID: call.CallID, RequestDigest: repository.DigestBytes(canonical), Round: call.Round, Status: "pending"}
		if err := tx.Create(&receipt).Error; err != nil {
			return err
		}
		row.ToolCalls++
		row.RoundTools++
		return persistAgentCheckpoint(tx, row, e)
	})
}

func (s *AgentCheckpointStore) completeTool(ctx context.Context, call agentCallback, response map[string]any) error {
	encoded, err := json.Marshal(response)
	if err != nil || len(encoded) > 65536 {
		return agentCheckpointError("AGENT_CHECKPOINT_TOO_LARGE")
	}
	return s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		result := tx.Model(&agentToolReceipt{}).Where("scope_type=? AND scope_id=? AND call_id=? AND status='pending'", row.ScopeType, row.ScopeID, call.CallID).Updates(map[string]any{"status": "complete", "response": string(encoded), "updated_at": time.Now().UTC()})
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return agentCheckpointError("AGENT_CHECKPOINT_REJECTED")
		}
		return nil
	})
}

func (s *AgentCheckpointStore) finish(ctx context.Context, accepted json.RawMessage, formatOnly bool) error {
	return s.withRow(ctx, func(tx *gorm.DB, row *agentCheckpointRow, e *agentCheckpointEnvelope) error {
		if len(accepted) > 0 {
			if !json.Valid(accepted) || len(accepted) > 65536 {
				return agentCheckpointError("AGENT_CHECKPOINT_TOO_LARGE")
			}
			e.Accepted = string(accepted)
		}
		row.FormatOnly = formatOnly
		return persistAgentCheckpoint(tx, row, e)
	})
}

// validAgentCheckpointSDK 只接受可见文本、工具调用与工具结果的投影；模型不能把配置、隐藏推理或任意 SDK 内部对象写入恢复状态。
func validAgentCheckpointSDK(raw json.RawMessage) bool {
	if len(raw) == 0 || len(raw) > 131072 {
		return false
	}
	var state struct {
		Context     []json.RawMessage `json:"context"`
		Cursor      string            `json:"cursor"`
		PendingText string            `json:"pending_text"`
		SeenIDs     []string          `json:"seen_ids"`
	}
	if !decodeAgentJSON(raw, &state) || len(state.Context) > 64 || len(state.SeenIDs) > 4096 || len(state.PendingText) > 49152 {
		return false
	}
	if state.Cursor != "model" && state.Cursor != "tools" && state.Cursor != "finish" {
		return false
	}
	for _, message := range state.Context {
		var m struct {
			ID      string            `json:"id"`
			Name    string            `json:"name"`
			Role    string            `json:"role"`
			Content []json.RawMessage `json:"content"`
		}
		if !decodeAgentJSON(message, &m) || (m.Role != "user" && m.Role != "assistant") || len(m.ID) > 128 || len(m.Name) > 128 || len(m.Content) > 256 {
			return false
		}
		for _, block := range m.Content {
			var b map[string]json.RawMessage
			if json.Unmarshal(block, &b) != nil {
				return false
			}
			var kind string
			if json.Unmarshal(b["type"], &kind) != nil {
				return false
			}
			allowed := "|"
			switch kind {
			case "text":
				allowed = "|type|text|"
			case "tool_call":
				allowed = "|type|id|name|input|state|"
			case "tool_result":
				allowed = "|type|id|name|output|state|"
			default:
				return false
			}
			for key := range b {
				if !strings.Contains(allowed, "|"+key+"|") {
					return false
				}
			}
			if m.Role == "user" && kind != "text" {
				return false
			}
			if kind == "tool_result" {
				var output []struct {
					Type string `json:"type"`
					Text string `json:"text"`
				}
				if !decodeAgentJSON(b["output"], &output) {
					return false
				}
				for _, o := range output {
					if o.Type != "text" {
						return false
					}
				}
			}
		}
	}
	return !bytes.Contains(raw, []byte(`"type":"thinking"`))
}
