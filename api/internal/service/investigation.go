// Package service：面向自然语言问题的持久化调查状态机，串联规划、人工确认、采集、诊断、复测与结果比较。
// 状态推进依赖已落库事实和统一准入锁顺序，重启可继续；模型提出计划不能绕过目标授权或自动确认写操作。
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
)

type InvestigationInput struct {
	Goal               string            `json:"goal"`
	AgentID            string            `json:"target_agent_id"`
	Parameters         domain.Parameters `json:"parameters"`
	Consent            bool              `json:"consent"`
	CodeProjectID      string            `json:"code_project_id,omitempty"`
	CodeConsent        bool              `json:"code_consent,omitempty"`
	TelemetryServiceID string            `json:"telemetry_service_id,omitempty"`
}
type CollectionPlan struct {
	Kind            string         `json:"kind"`
	DurationSeconds int            `json:"duration_seconds"`
	FrequencyHz     int            `json:"frequency_hz"`
	Options         map[string]any `json:"options,omitempty"`
	Rationale       string         `json:"rationale"`
}

// Investigation 保存一条自然语言调查的阶段、目标和派生运行关系。
// 它引用普通采集任务、诊断和复测记录，不另造一套采集或分析状态。
type Investigation struct {
	ID                 int64           `json:"id,string"`
	RequestedBy        string          `json:"requested_by"`
	AgentID            string          `json:"agent_id"`
	Goal               string          `json:"goal"`
	Parameters         json.RawMessage `json:"parameters"`
	Consent            bool            `json:"consent"`
	RequestKey         string          `json:"-"`
	InputDigest        string          `json:"input_digest"`
	Status             string          `json:"status"`
	Plan               json.RawMessage `json:"plan"`
	Trace              json.RawMessage `json:"trace"`
	Report             json.RawMessage `json:"report"`
	BaselineTaskID     *int64          `json:"baseline_task_id,string,omitempty"`
	DiagnosisID        *int64          `json:"diagnosis_id,string,omitempty"`
	VerificationID     *int64          `json:"verification_id,string,omitempty"`
	CodeProjectID      *string         `json:"code_project_id,omitempty"`
	CodeSourceRevision *string         `json:"code_source_revision,omitempty"`
	CodeRepairID       *int64          `json:"code_repair_id,string,omitempty"`
	LeaseOwner         *string         `json:"-"`
	LeaseExpiresAt     *time.Time      `json:"-"`
	Attempts           int             `json:"-"`
	ErrorCode          *string         `json:"error_code,omitempty"`
	CreatedAt          time.Time       `json:"created_at"`
	UpdatedAt          time.Time       `json:"updated_at"`
	TelemetryServiceID string          `json:"telemetry_service_id,omitempty"`
}

// RequestInvestigation 校验用户、目标、模型同意和幂等键后建立持久化规划记录。
// 来源项目及目标绑定由控制面选择，模型问题中的 PID、路径或项目名不能改变授权范围。
func (s *Suggestions) RequestInvestigation(ctx context.Context, u domain.User, in InvestigationInput, key, request string) (Investigation, error) {
	var out Investigation
	if err := ValidateKey(key); err != nil {
		return out, err
	}
	in.Goal = strings.TrimSpace(in.Goal)
	if !u.CanWrite() {
		return out, domain.Fail("FORBIDDEN", "只读用户不能发起采集调查")
	}
	if !utf8.ValidString(in.Goal) || utf8.RuneCountInString(in.Goal) < 1 || utf8.RuneCountInString(in.Goal) > 600 || strings.IndexFunc(in.Goal, func(r rune) bool { return unicode.IsControl(r) && r != '\n' && r != '\t' }) >= 0 {
		return out, domain.Fail("INVALID_PARAMETERS", "调查问题须为1至600字")
	}
	if !in.Consent {
		return out, domain.Fail("CONSENT_REQUIRED", "请同意模型规划和确认后最多两次受控采集、自动后续分析")
	}
	if !s.AgentAvailable() {
		return out, domain.Fail("AI_UNAVAILABLE", "自然语言规划需要配置模型")
	}
	if in.Parameters.Source != "" || in.Parameters.Traceparent != "" {
		return out, domain.Fail("INVALID_PARAMETERS", "来源由平台指定")
	}
	if in.Parameters.Scope == "" {
		in.Parameters.Scope = "process"
	}
	if in.Parameters.Target != nil {
		in.Parameters.Scope = in.Parameters.Target.Type
		if in.Parameters.Target.PID > 0 {
			in.Parameters.PID = in.Parameters.Target.PID
		}
	}
	if in.Parameters.Scope == "process" && in.Parameters.PID < 2 {
		return out, domain.Fail("INVALID_PARAMETERS", "请选择有效进程")
	}
	var binding *codeRuntimeBinding
	if in.CodeProjectID != "" {
		if !u.IsPlatformAdmin() || !in.CodeConsent {
			return out, domain.Fail("FORBIDDEN", "关联源码修复需要管理员及明确的源码和隔离修改授权")
		}
		raw, err := s.Code.Invoke(ctx, "describe", in.CodeProjectID, "", map[string]any{})
		if err != nil {
			return out, err
		}
		binding = &codeRuntimeBinding{}
		if json.Unmarshal(raw, binding) != nil || binding.ID != in.CodeProjectID {
			return out, domain.Fail("INVALID_EVIDENCE", "源码运行绑定无效")
		}
	}
	original, _ := json.Marshal(in)
	digest := repository.DigestBytes(original)
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var locked domain.User
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,417626))", u.ID).Error; err != nil {
			return err
		}
		if err := tx.Where("id=?", u.ID).First(&locked).Error; err != nil {
			return err
		}
		r := repository.New(tx)
		a, err := r.AuthorizedAgent(ctx, locked, in.AgentID, true)
		if err != nil {
			return err
		}
		if a.Status != "online" || time.Since(a.LastSeenAt) > 45*time.Second {
			return domain.Fail("AGENT_OFFLINE", "目标节点离线")
		}
		probe := domain.CreateTask{Parameters: in.Parameters}
		if err := validateTargetResources(&probe, a.Resources); err != nil {
			return err
		}
		if binding != nil {
			if !locked.IsPlatformAdmin() {
				return domain.Fail("FORBIDDEN", "源码权限已撤销")
			}
			if err := binding.validate(in.AgentID, probe.Parameters); err != nil {
				return err
			}
		}
		err = tx.Where("requested_by=? AND request_key=?", u.ID, key).First(&out).Error
		if err == nil {
			if out.InputDigest != digest {
				return domain.Fail("IDEMPOTENCY_CONFLICT", "该键已用于不同调查")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		var n int64
		if err := tx.Model(&Investigation{}).Where("requested_by=? AND status NOT IN ('success','failed','canceled')", u.ID).Count(&n).Error; err != nil {
			return err
		}
		if n >= 3 {
			return domain.Fail("DIAGNOSIS_BUSY", "最多同时保留3个活跃调查")
		}
		params, _ := json.Marshal(probe.Parameters)
		if err := s.validateTelemetryService(ctx, locked, in.TelemetryServiceID, in.AgentID); err != nil {
			return err
		}
		out = Investigation{RequestedBy: u.ID, AgentID: in.AgentID, Goal: in.Goal, Parameters: params, Consent: true, RequestKey: key, InputDigest: digest, Status: "planning", Trace: json.RawMessage(`[]`), TelemetryServiceID: in.TelemetryServiceID}
		if binding != nil {
			out.CodeProjectID = &binding.ID
			out.CodeSourceRevision = &binding.Revision
		}
		if err := tx.Create(&out).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(u, "investigation.request", "investigation", fmt.Sprint(out.ID), request))).Error
	})
	return out, err
}
func (s *Suggestions) AuthorizeInvestigation(ctx context.Context, u domain.User, id int64) (Investigation, error) {
	var out Investigation
	current, err := s.Repo.User(ctx, u.ID)
	if err != nil {
		return out, err
	}
	u = current
	query := s.Repo.Context(ctx).Where("id=?", id)
	if !u.IsPlatformAdmin() {
		query = query.Where("requested_by=?", u.ID)
	}
	if err := query.First(&out).Error; err != nil {
		return out, domain.Fail("NOT_FOUND", "调查不存在或无权访问")
	}
	if out.CodeProjectID != nil && !u.IsPlatformAdmin() {
		return Investigation{}, domain.Fail("NOT_FOUND", "调查不存在或无权访问")
	}
	if _, err := s.Repo.AuthorizedAgent(ctx, u, out.AgentID, true); err != nil {
		return out, err
	}
	if err := s.authorizeInvestigationSignals(ctx, u, out); err != nil {
		return Investigation{}, err
	}
	return out, nil
}

// ConfirmInvestigation 将用户确认作为独立持久化动作，重验计划和目标授权后再推进采集；规划完成不等于已经获准执行。
func (s *Suggestions) ConfirmInvestigation(ctx context.Context, u domain.User, id int64, request string) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var locked Investigation
		if err := tx.Raw("SELECT * FROM investigations WHERE id=? FOR UPDATE", id).Scan(&locked).Error; err != nil {
			return err
		}
		local := *s
		local.Repo = repository.New(tx)
		var userErr error
		u, userErr = local.Repo.User(ctx, u.ID)
		if userErr != nil {
			return userErr
		}
		out, err := local.AuthorizeInvestigation(ctx, u, id)
		if err != nil {
			return err
		}
		if !u.CanWrite() {
			return domain.Fail("FORBIDDEN", "只读用户不能确认采集")
		}
		if out.Status == "queued" || out.BaselineTaskID != nil {
			return nil
		}
		if out.Status != "awaiting_confirmation" {
			return domain.Fail("INVALID_STATE", "采集计划尚未就绪")
		}
		res := tx.Model(&Investigation{}).Where("id=? AND status='awaiting_confirmation'", id).Updates(map[string]any{"status": "queued", "updated_at": time.Now().UTC()})
		if res.Error != nil {
			return res.Error
		}
		if res.RowsAffected == 0 {
			return domain.Fail("INVALID_STATE", "调查状态已改变")
		}
		return tx.Create(ptrAuditDiagnosis(Audit(u, "investigation.confirm", "investigation", fmt.Sprint(id), request))).Error
	})
}

// CancelInvestigation 取消调查及其仍活动的关联工作，按当前执行身份撤销后续写入资格；已经完成的测量事实仍保留用于审计。
func (s *Suggestions) CancelInvestigation(ctx context.Context, u domain.User, id int64, request string) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var locked Investigation
		if err := tx.Raw("SELECT * FROM investigations WHERE id=? FOR UPDATE", id).Scan(&locked).Error; err != nil {
			return err
		}
		local := *s
		local.Repo = repository.New(tx)
		var userErr error
		u, userErr = local.Repo.User(ctx, u.ID)
		if userErr != nil {
			return userErr
		}
		out, err := local.AuthorizeInvestigation(ctx, u, id)
		if err != nil {
			return err
		}
		if !u.CanWrite() {
			return domain.Fail("FORBIDDEN", "只读用户不能取消调查")
		}
		if err := local.cancelInvestigationFollowups(ctx, tx, out, "DIAGNOSIS_CANCELED", "canceled"); err != nil {
			return err
		}
		if out.Status == "success" || out.Status == "failed" || out.Status == "canceled" {
			return nil
		}
		if out.CodeRepairID != nil {
			if err := tx.Exec("UPDATE code_repairs SET status='canceled',lease_owner=NULL,lease_expires_at=NULL,error_code='DIAGNOSIS_CANCELED',updated_at=now() WHERE id=? AND status IN ('pending','running')", *out.CodeRepairID).Error; err != nil {
				return err
			}
		}
		ids := []int64{}
		if out.BaselineTaskID != nil {
			ids = append(ids, *out.BaselineTaskID)
		}
		if out.VerificationID != nil {
			var v VerificationRun
			if err := tx.Raw("SELECT * FROM verification_runs WHERE id=? FOR UPDATE", *out.VerificationID).Scan(&v).Error; err != nil {
				return err
			}
			ids = append(ids, v.BaselineTaskID, v.CandidateTaskID)
		}
		if len(ids) > 0 {
			var lockedIDs []int64
			if err := tx.Raw("SELECT id FROM tasks WHERE id IN ? ORDER BY id FOR UPDATE", ids).Scan(&lockedIDs).Error; err != nil {
				return err
			}
		}
		if err := tx.Model(&Investigation{}).Where("id=?", id).Updates(map[string]any{"status": "canceled", "lease_owner": nil, "lease_expires_at": nil, "updated_at": time.Now().UTC()}).Error; err != nil {
			return err
		}
		if out.VerificationID != nil {
			var v VerificationRun
			if err := tx.Where("id=?", *out.VerificationID).First(&v).Error; err != nil {
				return err
			}
			if err := tx.Model(&VerificationRun{}).Where("id=? AND status NOT IN ('success','failed')", v.ID).Updates(map[string]any{"status": "canceled", "updated_at": time.Now().UTC()}).Error; err != nil {
				return err
			}
			if v.CandidateDiagnosisID != nil {
				if err := tx.Model(&DiagnosisRun{}).Where("id=? AND status IN ('pending','running')", *v.CandidateDiagnosisID).Updates(map[string]any{"status": "failed", "error_code": "DIAGNOSIS_CANCELED", "lease_owner": nil, "lease_expires_at": nil}).Error; err != nil {
					return err
				}
			}
			if _, err := local.TasksCancel(ctx, tx, u, v.CandidateTaskID, request); err != nil {
				return err
			}
		}
		if out.DiagnosisID != nil {
			if err := tx.Model(&DiagnosisRun{}).Where("id=? AND status IN ('pending','running')", *out.DiagnosisID).Updates(map[string]any{"status": "failed", "error_code": "DIAGNOSIS_CANCELED", "lease_owner": nil, "lease_expires_at": nil}).Error; err != nil {
				return err
			}
		}
		if out.BaselineTaskID != nil {
			if _, err := local.TasksCancel(ctx, tx, u, *out.BaselineTaskID, request); err != nil {
				return err
			}
		}
		return tx.Create(ptrAuditDiagnosis(Audit(u, "investigation.cancel", "investigation", fmt.Sprint(id), request))).Error
	})
}
func (s *Suggestions) TasksCancel(ctx context.Context, tx *gorm.DB, u domain.User, id int64, request string) (domain.Task, error) {
	r := repository.New(tx)
	return r.Cancel(ctx, u, id, Audit(u, "task.cancel", "task", fmt.Sprint(id), request))
}

func (s *Suggestions) validateCollectionPlan(ctx context.Context, u domain.User, run Investigation, plan CollectionPlan) (domain.CreateTask, error) {
	var p domain.Parameters
	if json.Unmarshal(run.Parameters, &p) != nil {
		return domain.CreateTask{}, domain.Fail("INVALID_EVIDENCE", "目标参数无效")
	}
	if len(plan.Rationale) < 1 || len(plan.Rationale) > 1800 || len(plan.Kind) > 40 || plan.DurationSeconds < 1 || plan.DurationSeconds > 60 || plan.FrequencyHz < 0 || plan.FrequencyHz > 999 {
		return domain.CreateTask{}, domain.Fail("AI_TOOL_REJECTED", "采集计划超出预算")
	}
	k, ok := domain.Kind(plan.Kind)
	if !ok || !k.AgentRunner {
		return domain.CreateTask{}, domain.Fail("AI_TOOL_REJECTED", "采集器不可用于现场采集")
	}
	p.DurationSeconds = plan.DurationSeconds
	p.FrequencyHz = plan.FrequencyHz
	if plan.Options != nil {
		p.Options = plan.Options
	}
	in := domain.CreateTask{Name: fmt.Sprintf("智能调查#%d / %s", run.ID, plan.Kind), Kind: plan.Kind, AgentID: run.AgentID, Parameters: p, Visibility: "private"}
	in.Normalize()
	if err := in.Validate(); err != nil {
		return in, err
	}
	a, err := s.Repo.AuthorizedAgent(ctx, u, run.AgentID, true)
	if err != nil {
		return in, err
	}
	if !a.Supports(plan.Kind) {
		return in, domain.Fail("CAPABILITY_UNAVAILABLE", "节点不具备所选采集能力")
	}
	if err := validateTargetResources(&in, a.Resources); err != nil {
		return in, err
	}
	return in, nil
}

func (s *Suggestions) RunInvestigations(ctx context.Context) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			_ = s.planNextInvestigation(ctx)
			_ = s.advanceInvestigations(ctx)
			_ = s.advanceInvestigationFollowups(ctx)
		}
	}
}
func (s *Suggestions) planNextInvestigation(ctx context.Context) error {
	var run Investigation
	owner := RandomToken()
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("UPDATE investigations SET status='failed',error_code='DIAGNOSIS_RETRY_EXHAUSTED',lease_owner=NULL,lease_expires_at=NULL WHERE status='planning' AND attempts>=3 AND lease_expires_at<now()").Error; err != nil {
			return err
		}
		return tx.Raw("UPDATE investigations SET lease_owner=?,lease_expires_at=now()+interval '180 seconds',attempts=attempts+1 WHERE id=(SELECT id FROM investigations WHERE status='planning' AND attempts<3 AND (lease_expires_at IS NULL OR lease_expires_at<now()) ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *", owner).Scan(&run).Error
	})
	if err != nil || run.ID == 0 {
		return err
	}
	work, stop := context.WithTimeout(ctx, 150*time.Second)
	defer stop()
	u, err := s.Repo.User(work, run.RequestedBy)
	var plan CollectionPlan
	steps := []DiagnosisStep{}
	if len(run.Trace) > 0 && json.Unmarshal(run.Trace, &steps) != nil {
		err = domain.Fail("INVALID_EVIDENCE", "规划轨迹无法恢复")
	}
	if err == nil {
		plan, err = s.planCollection(work, u, run, func(name string, observation any) error {
			b, _ := json.Marshal(observation)
			steps = append(steps, DiagnosisStep{EvidenceID: fmt.Sprintf("P%d", len(steps)+1), Tool: name, Observation: b})
			trace, _ := json.Marshal(steps)
			return s.Repo.Context(work).Transaction(func(tx *gorm.DB) error {
				if e := s.planningFence(work, tx, run); e != nil {
					return e
				}
				res := tx.Model(&Investigation{}).Where("id=? AND status='planning' AND lease_owner=? AND lease_expires_at>now()", run.ID, owner).Updates(map[string]any{"trace": string(trace), "updated_at": time.Now().UTC()})
				if res.Error != nil {
					return res.Error
				}
				if res.RowsAffected != 1 {
					return domain.Fail("STALE_LEASE", "调查已取消或执行租约失效")
				}
				return nil
			})
		})
	}
	finish, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
	defer cancel()
	status, code := "awaiting_confirmation", ""
	if err != nil {
		status = "failed"
		code = "PLANNING_FAILED"
		if e, ok := err.(*domain.Error); ok {
			code = e.Code
		}
	}
	b, _ := json.Marshal(plan)
	return s.Repo.Context(finish).Transaction(func(tx *gorm.DB) error {
		fresh, e := repository.New(tx).User(finish, run.RequestedBy)
		if e == nil {
			_, e = repository.New(tx).AuthorizedAgent(finish, fresh, run.AgentID, true)
		}
		if e != nil {
			status = "failed"
			code = "AUTHORIZATION_REVOKED"
		}
		res := tx.Exec("UPDATE investigations SET status=?,plan=?::jsonb,error_code=NULLIF(?,''),lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=? AND status='planning' AND lease_owner=? AND lease_expires_at>now()", status, string(b), code, run.ID, owner)
		return res.Error
	})
}

// planningFence 在每次规划工具与最终提交前核验当前用户、目标和规划租约；模型网络等待期间的撤权或取消必须立即失效。
func (s *Suggestions) planningFence(ctx context.Context, tx *gorm.DB, run Investigation) error {
	u, err := repository.New(tx).User(ctx, run.RequestedBy)
	if err != nil || !u.CanWrite() {
		return domain.Fail("AUTHORIZATION_REVOKED", "调查授权已撤销")
	}
	if _, err := repository.New(tx).AuthorizedAgent(ctx, u, run.AgentID, true); err != nil {
		return domain.Fail("AUTHORIZATION_REVOKED", "目标授权已撤销")
	}
	if run.LeaseOwner == nil {
		return domain.Fail("STALE_LEASE", "调查执行租约无效")
	}
	var id int64
	if err := tx.Raw("SELECT id FROM investigations WHERE id=? AND status='planning' AND lease_owner=? AND lease_expires_at>now() FOR UPDATE", run.ID, *run.LeaseOwner).Scan(&id).Error; err != nil {
		return err
	}
	if id == 0 {
		return domain.Fail("STALE_LEASE", "调查已取消或租约失效")
	}
	return nil
}

// planCollection 通过受限 list_collectors/propose_collection 工具生成计划；模型只能从实际能力目录选择。
// 有效计划由 Go 校验并终止本阶段，随后等待人工确认，不能让一次规划回复直接执行采样。
func (s *Suggestions) planCollection(ctx context.Context, u domain.User, run Investigation, persist func(string, any) error) (CollectionPlan, error) {
	a, err := s.Repo.AuthorizedAgent(ctx, u, run.AgentID, true)
	if err != nil {
		return CollectionPlan{}, err
	}
	var params domain.Parameters
	_ = json.Unmarshal(run.Parameters, &params)
	catalog := []map[string]any{}
	var capabilityNames []string
	_ = json.Unmarshal(a.Capabilities, &capabilityNames)
	for _, name := range capabilityNames {
		k, ok := domain.Kind(name)
		if !ok || !k.AgentRunner {
			continue
		}
		allowed := false
		for _, scope := range k.Targets {
			if scope == params.Scope {
				allowed = true
			}
		}
		if !allowed {
			continue
		}
		catalog = append(catalog, map[string]any{"kind": name, "default_duration_seconds": k.DefaultDuration, "default_frequency_hz": k.DefaultFrequency, "schema": k.Schema})
	}
	if len(catalog) == 0 {
		return CollectionPlan{}, domain.Fail("CAPABILITY_UNAVAILABLE", "所选目标没有可用采集器")
	}
	initial, _ := json.Marshal(map[string]any{"target_scope": params.Scope, "target_identity_bound": true, "collectors": catalog})
	messages := []diagnosisMessage{{Role: "system", Content: "你是Drop性能调查规划器。根据用户问题和实际可用采集器选择现场采集计划。必须调用propose_collection提交计划。先用list_collectors核对能力；只能从返回目录选择。不可提供PID、主机、路径、shell或URL，不可改变目标。参数必须符合各采集器schema，缺失端口/模板等信息时不能捏造；可选通用采集补充证据并解释局限。时长最多60秒；先做低成本采集，复测另有最多60秒预算。rationale用中文说明为何选择和局限，不输出隐藏思维链。工具错误后修正参数重新提交，不能把错误当成功。"}, {Role: "user", Content: run.Goal + "\n已授权的目标范围与能力目录：" + string(initial)}}
	definitions := []map[string]any{{"type": "function", "function": map[string]any{"name": "list_collectors", "description": "读取当前绑定目标实际支持的采集器与参数schema。", "parameters": map[string]any{"type": "object", "properties": map[string]any{}, "additionalProperties": false}}}, {"type": "function", "function": map[string]any{"name": "propose_collection", "description": "校验现场采集计划；成功后展示给用户确认，不执行命令。", "parameters": map[string]any{"type": "object", "additionalProperties": false, "properties": map[string]any{"kind": map[string]any{"type": "string"}, "duration_seconds": map[string]any{"type": "integer", "minimum": 1, "maximum": 60}, "frequency_hz": map[string]any{"type": "integer", "minimum": 0, "maximum": 999}, "options": map[string]any{"type": "object"}, "rationale": map[string]any{"type": "string", "maxLength": 600}}, "required": []string{"kind", "duration_seconds", "frequency_hz", "rationale"}}}}}
	listed := false
	var previous []DiagnosisStep
	if len(run.Trace) > 0 && json.Unmarshal(run.Trace, &previous) != nil {
		return CollectionPlan{}, domain.Fail("INVALID_EVIDENCE", "规划轨迹无法恢复")
	}
	for _, step := range previous {
		if step.Tool == "list_collectors" {
			listed = true
		}
	}
	evidence := len(previous)
	invoke := func(work context.Context, name string, arguments json.RawMessage) (agentToolResult, error) {
		if err := s.Repo.Context(work).Transaction(func(tx *gorm.DB) error { return s.planningFence(work, tx, run) }); err != nil {
			return agentToolResult{}, err
		}
		var observation any
		var selected *CollectionPlan
		switch name {
		case "list_collectors":
			var empty struct{}
			if !decodeAgentJSON(arguments, &empty) || strings.TrimSpace(string(arguments)) == "null" {
				observation = map[string]any{"error_code": "INVALID_PARAMETERS"}
			} else {
				listed = true
				observation = map[string]any{"collectors": catalog}
			}
		case "propose_collection":
			var plan CollectionPlan
			if !decodeAgentJSON(arguments, &plan) || strings.TrimSpace(string(arguments)) == "null" || !listed {
				observation = map[string]any{"error_code": "CHECK_COLLECTORS_AND_PARAMETERS"}
			} else if _, err := s.validateCollectionPlan(work, u, run, plan); err != nil {
				observation = map[string]any{"error_code": safeAgentError(err), "next": "核对实际目录和参数schema，修正后重新提交"}
			} else {
				selected = &plan
				observation = map[string]any{"validated": true, "plan": plan}
			}
		default:
			observation = map[string]any{"error_code": "UNKNOWN_TOOL"}
		}
		if err := persist(name, observation); err != nil {
			return agentToolResult{}, err
		}
		evidence++
		observed, _ := json.Marshal(observation)
		step, _ := json.Marshal(DiagnosisStep{EvidenceID: fmt.Sprintf("P%d", evidence), Tool: name, Observation: observed})
		result := agentToolResult{Observation: step}
		if selected != nil {
			result.Terminal, _ = json.Marshal(selected)
		}
		return result, nil
	}
	if s.Config.AgentBackend == "agentscope" {
		result, metadata, err := s.Runner.Run(ctx, agentRunSpec{
			Phase: "planning", Model: s.Config.Model, SystemPrompt: messages[0].Content, Messages: messages[1:], Tools: definitions,
			Checkpoint: NewAgentCheckpointStore(s.Repo, AgentCheckpointBinding{ScopeType: "planning", ScopeID: run.ID, InputDigest: run.InputDigest}, func(work context.Context, tx *gorm.DB) error { return s.planningFence(work, tx, run) }),
			Fence: func(work context.Context) error {
				return s.Repo.Context(work).Transaction(func(tx *gorm.DB) error { return s.planningFence(work, tx, run) })
			},
			Invoke: invoke,
			Finish: func(context.Context, string) (json.RawMessage, error) {
				return nil, domain.Fail("AI_INSUFFICIENT_EVIDENCE", "必须使用propose_collection提交并验证采集计划")
			},
		})
		if err != nil {
			return CollectionPlan{}, err
		}
		if err := persist("agent_runtime", metadata); err != nil {
			return CollectionPlan{}, err
		}
		var plan CollectionPlan
		if !decodeAgentJSON(result, &plan) {
			return CollectionPlan{}, domain.Fail("AI_INVALID_REPORT", "采集计划无效")
		}
		return plan, nil
	}
	used := 0
	for round := 0; round < 6; round++ {
		body, _ := json.Marshal(map[string]any{"model": s.Config.Model, "messages": messages, "tools": definitions, "tool_choice": "required", "stream": false, "temperature": 0, "max_tokens": 1800, "thinking": map[string]string{"type": "disabled"}})
		req, err := http.NewRequestWithContext(ctx, "POST", s.Config.Endpoint, strings.NewReader(string(body)))
		if err != nil {
			return CollectionPlan{}, domain.Fail("AI_CONFIGURATION_ERROR", "模型配置无效")
		}
		req.Header.Set("Content-Type", "application/json")
		if s.Config.APIKey != "" {
			req.Header.Set("Authorization", "Bearer "+s.Config.APIKey)
		}
		resp, err := s.Client.Do(req)
		if err != nil {
			return CollectionPlan{}, domain.Fail("AI_UPSTREAM_ERROR", "规划模型调用失败")
		}
		raw, readErr := io.ReadAll(io.LimitReader(resp.Body, 65537))
		resp.Body.Close()
		if readErr != nil || resp.StatusCode != 200 || len(raw) > 65536 {
			return CollectionPlan{}, domain.Fail("AI_UPSTREAM_ERROR", "规划模型响应无效")
		}
		var answer struct {
			Choices []struct {
				Message diagnosisMessage `json:"message"`
			} `json:"choices"`
		}
		if json.Unmarshal(raw, &answer) != nil || len(answer.Choices) != 1 || len(answer.Choices[0].Message.ToolCalls) < 1 || len(answer.Choices[0].Message.ToolCalls) > 2 {
			return CollectionPlan{}, domain.Fail("AI_INVALID_RESPONSE", "规划器未提供受控工具调用")
		}
		message := answer.Choices[0].Message
		message.Role = "assistant"
		message.Content = ""
		messages = append(messages, message)
		for _, call := range message.ToolCalls {
			used++
			if used > 10 {
				return CollectionPlan{}, domain.Fail("AI_BUDGET_EXCEEDED", "规划工具预算用尽")
			}
			if len(call.ID) < 1 || len(call.ID) > 128 || call.Type != "function" || len(call.Function.Arguments) > 4096 {
				return CollectionPlan{}, domain.Fail("AI_TOOL_REJECTED", "规划工具标识或参数无效")
			}
			result, err := invoke(ctx, call.Function.Name, json.RawMessage(call.Function.Arguments))
			if err != nil {
				return CollectionPlan{}, err
			}
			if len(result.Terminal) > 0 {
				var plan CollectionPlan
				_ = json.Unmarshal(result.Terminal, &plan)
				return plan, nil
			}
			messages = append(messages, diagnosisMessage{Role: "tool", ToolCallID: call.ID, Content: string(result.Observation)})
		}
	}
	return CollectionPlan{}, domain.Fail("AI_BUDGET_EXCEEDED", "规划循环超过6轮")
}

func (s *Suggestions) advanceInvestigations(ctx context.Context) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var rows []Investigation
		if err := tx.Raw("SELECT * FROM investigations WHERE status IN ('queued','collecting','diagnosing','verifying') ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 8").Scan(&rows).Error; err != nil {
			return err
		}
		for _, run := range rows {
			err := s.advanceInvestigation(ctx, tx, &run)
			if err != nil {
				run.Status = "failed"
				code := "INVESTIGATION_FAILED"
				if e, ok := err.(*domain.Error); ok {
					code = e.Code
				}
				run.ErrorCode = &code
			}
			if err := tx.Model(&Investigation{}).Where("id=?", run.ID).Updates(map[string]any{"status": run.Status, "baseline_task_id": run.BaselineTaskID, "diagnosis_id": run.DiagnosisID, "verification_id": run.VerificationID, "code_repair_id": run.CodeRepairID, "report": run.Report, "error_code": run.ErrorCode, "updated_at": time.Now().UTC()}).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

// advanceInvestigation 在事务内依据已落库阶段推进采集、诊断、复测与比较关联。
// 先获取统一准入锁再锁来源任务，避免与直接诊断及复测工作器互锁；每个派生动作都要可幂等恢复。
func (s *Suggestions) advanceInvestigation(ctx context.Context, tx *gorm.DB, run *Investigation) error {
	if time.Since(run.CreatedAt) > 20*time.Minute {
		return domain.Fail("DIAGNOSIS_TIMEOUT", "调查超过20分钟预算")
	}
	// 先获取共享准入锁再获取任务锁；关联调查、复测工作器和直接诊断请求必须采用相同顺序。
	if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,417626))", run.RequestedBy).Error; err != nil {
		return err
	}
	r := repository.New(tx)
	u, err := r.User(ctx, run.RequestedBy)
	if err != nil {
		return err
	}
	if _, err := r.AuthorizedAgent(ctx, u, run.AgentID, true); err != nil {
		return domain.Fail("AUTHORIZATION_REVOKED", "目标授权已撤销")
	}
	local := *s
	local.Repo = r
	local.Tasks = &Tasks{Repo: r, Storage: s.Tasks.Storage, Control: s.Tasks.Control}
	if run.Status == "queued" {
		var plan CollectionPlan
		if json.Unmarshal(run.Plan, &plan) != nil {
			return domain.Fail("INVALID_EVIDENCE", "采集计划不可读")
		}
		input, err := local.validateCollectionPlan(ctx, u, *run, plan)
		if err != nil {
			return err
		}
		task, _, err := local.Tasks.Create(ctx, u, input, fmt.Sprintf("investigation:%d:baseline", run.ID), "investigation-worker")
		if err != nil {
			return err
		}
		run.BaselineTaskID = &task.ID
		run.Status = "collecting"
		return nil
	}
	if run.BaselineTaskID == nil {
		return domain.Fail("INVALID_STATE", "调查缺少原任务")
	}
	task, err := r.AuthorizeTaskWrite(ctx, u, *run.BaselineTaskID)
	if err != nil {
		return domain.Fail("AUTHORIZATION_REVOKED", "任务权限已撤销")
	}
	if task.CollectionStatus == "FAILED" || task.CollectionStatus == "CANCELED" || task.AnalysisStatus == "FAILED" {
		return domain.Fail("COLLECTION_FAILED", "采集或解析失败")
	}
	if run.Status == "collecting" {
		if task.CollectionStatus != "COLLECTED" || task.AnalysisStatus != "SUCCESS" {
			return nil
		}
		diagnosis, err := local.RequestDiagnosis(ctx, u, task.ID, DiagnosisInput{Goal: run.Goal, Mode: "model", Consent: run.Consent, TelemetryServiceID: run.TelemetryServiceID}, "investigation-worker", fmt.Sprintf("investigation:%d:diagnosis", run.ID))
		if err != nil {
			return err
		}
		run.DiagnosisID = &diagnosis.ID
		run.Status = "diagnosing"
		return nil
	}
	if run.Status == "diagnosing" {
		if run.DiagnosisID == nil {
			return domain.Fail("INVALID_STATE", "调查缺少诊断")
		}
		var diagnosis DiagnosisRun
		if err := tx.Where("id=?", *run.DiagnosisID).First(&diagnosis).Error; err != nil {
			return err
		}
		if diagnosis.Status == "failed" {
			return domain.Fail("DIAGNOSIS_FAILED", "原任务诊断失败")
		}
		if diagnosis.Status != "success" {
			return nil
		}
		if err := local.ValidateDiagnosisSource(ctx, diagnosis); err != nil {
			return err
		}
		var report DiagnosisReport
		if json.Unmarshal(diagnosis.Report, &report) != nil {
			return domain.Fail("INVALID_EVIDENCE", "诊断报告格式无效")
		}
		if report.Plan == nil {
			run.Status = "success"
			run.Report, _ = json.Marshal(map[string]any{"diagnosis": report, "verification_status": "manual_configuration_required"})
			return nil
		}
		candidate, _, err := local.CollectDiagnosis(ctx, u, task.ID, diagnosis.ID, fmt.Sprintf("investigation:%d:verification", run.ID), "investigation-worker")
		if err != nil {
			return err
		}
		var v VerificationRun
		if err := tx.Where("candidate_task_id=?", candidate.ID).First(&v).Error; err != nil {
			return err
		}
		run.VerificationID = &v.ID
		run.Status = "verifying"
		return nil
	}
	if run.VerificationID == nil {
		return domain.Fail("INVALID_STATE", "调查缺少复测记录")
	}
	var v VerificationRun
	if err := tx.Where("id=?", *run.VerificationID).First(&v).Error; err != nil {
		return err
	}
	if v.Status == "failed" || v.Status == "canceled" {
		return domain.Fail("VERIFICATION_FAILED", "复测闭环失败或取消")
	}
	if v.Status == "success" {
		if _, _, err := verificationBaseline(ctx, tx, v); err != nil {
			return err
		}
		if run.CodeProjectID != nil {
			return local.advanceLinkedCodeRepair(ctx, tx, u, run, v.Report)
		}
		run.Report = v.Report
		run.Status = "success"
	}
	return nil
}
