// 本文件实现个人诊断案例的事务保存、撤销与有界检索。
// 来源 task/attempt/analysis 每次使用都重新授权和校验；案例不是把历史模型回答当成可信知识复制过来。
// verified_repair 只表示同一调查中隔离源码快照的受控验证，human_reviewed 只表示明确人工纠正。
package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"sort"
	"time"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// DiagnosticCaseInput 不接收任意摘要、符号或 accepted 标志；纠正用固定枚举，机器验证只查平台事实。
type DiagnosticCaseInput struct {
	Outcome        string `json:"outcome"`
	CodeRepairID   *int64 `json:"code_repair_id,string,omitempty"`
	HumanConfirmed bool   `json:"human_confirmed"`
	CorrectionCode string `json:"correction_code,omitempty"`
}

// DiagnosticCase 的公开字段供本人审阅来源；私有匹配条件和运行目标不随 HTTP 响应序列化。
// 撤销只改变可复用状态，原诊断、测试回执和审计仍保持原样。
type DiagnosticCase struct {
	ID             int64           `json:"id,string"`
	OwnerID        string          `json:"-"`
	TaskID         int64           `json:"task_id,string"`
	DiagnosisID    int64           `json:"diagnosis_id,string"`
	AttemptID      int64           `json:"attempt_id,string"`
	AnalysisJobID  int64           `json:"analysis_job_id,string"`
	AnalysisRun    int             `json:"analysis_run"`
	CodeRepairID   *int64          `json:"code_repair_id,string,omitempty"`
	TaskKind       string          `json:"task_kind"`
	AgentID        string          `json:"-"`
	ProjectID      string          `json:"project_id,omitempty"`
	SourceRevision string          `json:"source_revision,omitempty"`
	Outcome        string          `json:"outcome"`
	CorrectionCode string          `json:"correction_code,omitempty"`
	Features       json.RawMessage `json:"-"`
	SkillRefs      json.RawMessage `json:"-"`
	RequestKey     string          `json:"-"`
	InputDigest    string          `json:"-"`
	Status         string          `json:"status"`
	CreatedAt      time.Time       `json:"created_at"`
	ExpiresAt      time.Time       `json:"expires_at"`
	RevokedAt      *time.Time      `json:"revoked_at,omitempty"`
}

func (DiagnosticCase) TableName() string { return "diagnostic_cases" }

var diagnosticCorrectionText = map[string]string{
	"insufficient_evidence":     "人工审阅认为原判断证据不足；当前任务需要重新补证据。",
	"wrong_hotspot":             "人工审阅纠正过热点解释；应重新核对当前 self、total 与调用路径。",
	"causality_not_established": "人工审阅指出因果结论尚未验证；相关信号不能作为当前根因。",
	"incomparable_measurements": "人工审阅指出采样条件不可比；比较前须重验单位、目标与负载。",
	"regression_detected":       "人工审阅标记过回归风险；当前修改仍需独立功能与性能对照。",
}

func validateDiagnosticCaseInput(in DiagnosticCaseInput) error {
	switch in.Outcome {
	case "reviewed_correction":
		if !in.HumanConfirmed || in.CodeRepairID != nil || diagnosticCorrectionText[in.CorrectionCode] == "" {
			return domain.Fail("INVALID_PARAMETERS", "人工纠正需要明确确认和有效纠正类型")
		}
	case "verified_repair":
		if !in.HumanConfirmed || in.CodeRepairID == nil || *in.CodeRepairID <= 0 || in.CorrectionCode != "" {
			return domain.Fail("INVALID_PARAMETERS", "验证案例必须人工确认并绑定真实修复记录")
		}
	default:
		return domain.Fail("INVALID_PARAMETERS", "不支持的案例结果类型")
	}
	return nil
}

// diagnosticCaseSource 的授权用户必须从当前数据库重读，不能沿用会话中可能已撤销的角色。
// lock=true 仅用于写事务，锁当前任务后核验成功诊断和当前结果摘要，拒绝重分析前的来源。
func diagnosticCaseSource(ctx context.Context, tx *gorm.DB, u domain.User, taskID, diagnosisID int64, lock bool) (domain.User, domain.Task, DiagnosisRun, error) {
	r := repository.New(tx)
	current, err := r.User(ctx, u.ID)
	if err != nil {
		return current, domain.Task{}, DiagnosisRun{}, err
	}
	task, err := r.AuthorizeTaskWrite(ctx, current, taskID)
	if err != nil {
		return current, task, DiagnosisRun{}, err
	}
	if lock {
		if err = tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", taskID).First(&task).Error; err != nil {
			return current, task, DiagnosisRun{}, err
		}
		// 初次授权之后可能等待任务锁；等待期间角色或共享可能已撤销。
		// 按 task→user/share 顺序固定实际授权行，再以新的语句快照重新授权，不能沿用等锁前的判断。
		current, task, err = lockDiagnosticCaseAuthorization(ctx, tx, u.ID, taskID)
		if err != nil {
			return current, task, DiagnosisRun{}, err
		}
	}
	var run DiagnosisRun
	if err = tx.Where("id=? AND task_id=? AND status='success'", diagnosisID, taskID).First(&run).Error; err != nil {
		return current, task, run, domain.Fail("INVALID_EVIDENCE", "案例必须绑定当前成功诊断")
	}
	if task.CollectionStatus != "COLLECTED" || task.AnalysisStatus != "SUCCESS" {
		return current, task, run, domain.Fail("RESULT_NOT_READY", "案例来源不再完整成功")
	}
	latest, err := r.LatestAttempt(ctx, taskID)
	if err != nil {
		return current, task, run, err
	}
	if latest.ID != run.AttemptID {
		return current, task, run, domain.Fail("STALE_ATTEMPT", "案例来源采集轮次已更新")
	}
	err = (&Suggestions{Repo: r}).ValidateDiagnosisSource(ctx, run)
	return current, task, run, err
}

// lockDiagnosticCaseAuthorization 的调用方必须已经持有来源任务锁。
// 与终态 Pin 保持相同授权锁序，保存/撤销期间已有授权行的降权或删除只能在事务提交后生效。
func lockDiagnosticCaseAuthorization(ctx context.Context, tx *gorm.DB, userID string, taskID int64) (domain.User, domain.Task, error) {
	var current domain.User
	if err := tx.WithContext(ctx).Clauses(clause.Locking{Strength: "SHARE"}).Where("id=?", userID).First(&current).Error; err != nil {
		return current, domain.Task{}, err
	}
	if err := pinDiagnosticAuthorization(ctx, tx, current, []int64{taskID}); err != nil {
		return current, domain.Task{}, err
	}
	task, err := repository.New(tx).AuthorizeTaskWrite(ctx, current, taskID)
	return current, task, err
}

// diagnosticProjectContext 只从真实调查/复测关系中获取源码项目，绝不从用户目标文本推断。
// 多条关系给出不同项目或版本时视为歧义，调用方必须排除该案例，不能挑一个凑成匹配。
func diagnosticProjectContext(ctx context.Context, tx *gorm.DB, taskID, diagnosisID int64) (string, string, error) {
	var rows []struct {
		Project  string
		Revision string
	}
	err := tx.WithContext(ctx).Raw(`SELECT DISTINCT i.code_project_id AS project,i.code_source_revision AS revision FROM investigations i
 LEFT JOIN verification_runs v ON v.id=i.verification_id
 WHERE ((i.baseline_task_id=? AND i.diagnosis_id=?) OR (v.candidate_task_id=? AND v.candidate_diagnosis_id=?))
 AND i.code_project_id IS NOT NULL AND i.code_source_revision IS NOT NULL LIMIT 2`, taskID, diagnosisID, taskID, diagnosisID).Scan(&rows).Error
	if err != nil {
		return "", "", err
	}
	if len(rows) == 0 {
		return "", "", nil
	}
	if len(rows) != 1 || rows[0].Project == "" || !diagnosticRevisionValid(rows[0].Revision) {
		return "", "", domain.Fail("INVALID_EVIDENCE", "源码项目关系不明确")
	}
	return rows[0].Project, rows[0].Revision, nil
}

func diagnosticRevisionValid(revision string) bool {
	if len(revision) != 64 {
		return false
	}
	for _, r := range revision {
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f') {
			return false
		}
	}
	return true
}

// diagnosticRegisteredRevision 在事务锁外请求只读登记信息；返回的不可变版本还须在锁内与本地关联核对。
// 不接收模型提供的地址或路径；执行器不可用时不能把旧版本假定成仍然有效。
func (s *Suggestions) diagnosticRegisteredRevision(ctx context.Context, project string) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		return "", diagnosticKnowledgeTimeout()
	}
	if s.Code == nil {
		return "", domain.Fail("CODE_EXECUTOR_UNAVAILABLE", "无法核实当前登记源码版本")
	}
	raw, err := s.Code.Invoke(ctx, "describe", project, "", map[string]any{})
	if ctx.Err() != nil {
		return "", diagnosticKnowledgeTimeout()
	}
	if err != nil {
		return "", err
	}
	var p struct {
		ID       string `json:"id"`
		Revision string `json:"source_revision"`
	}
	if json.Unmarshal(raw, &p) != nil || p.ID != project || !diagnosticRevisionValid(p.Revision) {
		return "", domain.Fail("INVALID_EVIDENCE", "登记源码版本无效")
	}
	return p.Revision, nil
}

// diagnosticVerifiedRepair 同时核验修复状态、同一调查关系、源码版本及真实测试回执。
// 仅有 repair success 或客户端 accepted=true 都不足；成功仍只代表隔离快照上的受控基准。
func diagnosticVerifiedRepair(ctx context.Context, tx *gorm.DB, u domain.User, taskID, diagnosisID, repairID int64, registered string) (CodeRepair, error) {
	var repair CodeRepair
	if !u.IsPlatformAdmin() {
		return repair, domain.Fail("FORBIDDEN", "验证修复案例仅平台管理员可保存或使用")
	}
	if err := tx.WithContext(ctx).Where("id=? AND status='success'", repairID).First(&repair).Error; err != nil {
		return repair, domain.Fail("INVALID_EVIDENCE", "源码修复未成功")
	}
	var count int64
	err := tx.WithContext(ctx).Table("investigations i").Joins("LEFT JOIN verification_runs v ON v.id=i.verification_id").Where("i.status='success' AND i.code_repair_id=? AND i.code_project_id=? AND i.code_source_revision=? AND ((i.baseline_task_id=? AND i.diagnosis_id=?) OR (v.candidate_task_id=? AND v.candidate_diagnosis_id=?))", repairID, repair.ProjectID, repair.SourceRevision, taskID, diagnosisID, taskID, diagnosisID).Count(&count).Error
	if err != nil {
		return repair, err
	}
	var report struct {
		Project    string `json:"source_project"`
		Revision   string `json:"source_revision"`
		Validation struct {
			Success    bool   `json:"success"`
			Accepted   bool   `json:"accepted"`
			Regression string `json:"functional_regression"`
		} `json:"validation"`
	}
	if count != 1 || registered == "" || registered != repair.SourceRevision || json.Unmarshal(repair.Report, &report) != nil || report.Project != repair.ProjectID || report.Revision != repair.SourceRevision || !report.Validation.Success || !report.Validation.Accepted || report.Validation.Regression != "passed" {
		return repair, domain.Fail("INVALID_EVIDENCE", "修复关联、源码版本或真实验证回执不成立")
	}
	if _, err = validatedCodeStatus(repair.Report); err != nil {
		return repair, err
	}
	return repair, nil
}

// CreateDiagnosticCase 保存固定结构的个人案例，并把幂等冲突与重复操作分开处理。
// 用户级事务锁使同一诊断/结果的并发保存串行；保存与审计一起提交，任何来源变化都使事务失败。
func (s *Suggestions) CreateDiagnosticCase(ctx context.Context, u domain.User, taskID, diagnosisID int64, in DiagnosticCaseInput, key, request string) (DiagnosticCase, error) {
	var out DiagnosticCase
	if taskID <= 0 || diagnosisID <= 0 {
		return out, domain.Fail("INVALID_PARAMETERS", "案例来源无效")
	}
	if err := ValidateKey(key); err != nil {
		return out, err
	}
	if err := validateDiagnosticCaseInput(in); err != nil {
		return out, err
	}
	registered := ""
	_, _, _, sourceErr := diagnosticCaseSource(ctx, s.Repo.Context(ctx), u, taskID, diagnosisID, false)
	if sourceErr != nil {
		return out, sourceErr
	}
	preProject, preRevision, sourceErr := diagnosticProjectContext(ctx, s.Repo.Context(ctx), taskID, diagnosisID)
	if sourceErr != nil {
		return out, sourceErr
	}
	if in.Outcome == "verified_repair" {
		if !u.IsPlatformAdmin() {
			return out, domain.Fail("FORBIDDEN", "验证修复案例仅平台管理员可保存")
		}
		_, _, _, err := diagnosticCaseSource(ctx, s.Repo.Context(ctx), u, taskID, diagnosisID, false)
		if err != nil {
			return out, err
		}
		var repair CodeRepair
		if err = s.Repo.Context(ctx).Where("id=?", *in.CodeRepairID).First(&repair).Error; err != nil {
			return out, domain.Fail("INVALID_EVIDENCE", "修复来源无效")
		}
		// 先核验本地绑定，避免替别人的修复触发登记查询；外部 describe 不占有任务或用户行锁。
		if _, err = diagnosticVerifiedRepair(ctx, s.Repo.Context(ctx), u, taskID, diagnosisID, *in.CodeRepairID, repair.SourceRevision); err != nil {
			return out, err
		}
	}
	if preProject != "" {
		var err error
		registered, err = s.diagnosticRegisteredRevision(ctx, preProject)
		if err != nil {
			return out, err
		}
		if registered != preRevision {
			return out, domain.Fail("INPUT_CHANGED", "登记源码版本已变化")
		}
	}
	body, _ := json.Marshal(struct {
		Task      int64
		Diagnosis int64
		Input     DiagnosticCaseInput
	}{taskID, diagnosisID, in})
	digest := repository.DigestBytes(body)
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,417627))", u.ID).Error; err != nil {
			return err
		}
		current, task, run, err := diagnosticCaseSource(ctx, tx, u, taskID, diagnosisID, true)
		if err != nil {
			return err
		}
		project, revision, err := diagnosticProjectContext(ctx, tx, taskID, diagnosisID)
		if err != nil {
			return err
		}
		if project != preProject || revision != preRevision {
			return domain.Fail("INPUT_CHANGED", "源码关联已变化")
		}
		if in.Outcome == "verified_repair" {
			repair, e := diagnosticVerifiedRepair(ctx, tx, current, taskID, diagnosisID, *in.CodeRepairID, registered)
			if e != nil {
				return e
			}
			if project != repair.ProjectID || revision != repair.SourceRevision {
				return domain.Fail("INPUT_CHANGED", "源码关联已变化")
			}
		}
		err = tx.Where("owner_id=? AND (request_key=? OR (diagnosis_id=? AND outcome=?))", current.ID, key, diagnosisID, in.Outcome).Order("id").First(&out).Error
		if err == nil {
			if out.InputDigest != digest || out.RequestKey != key {
				return domain.Fail("IDEMPOTENCY_CONFLICT", "案例键或来源已对应不同内容")
			}
			return nil
		}
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return err
		}
		raw, err := repository.New(tx).Result(ctx, task.ID)
		if err != nil {
			return err
		}
		features, err := diagnosticFeatureSet(task, raw)
		if err != nil {
			return err
		}
		skills, err := MatchDiagnosticSkills(task, raw)
		if err != nil {
			return err
		}
		refs := []DiagnosticSkillRef{}
		for _, skill := range skills {
			refs = append(refs, DiagnosticSkillRef{skill.ID, skill.Version})
		}
		featuresJSON, _ := json.Marshal(features)
		refsJSON, _ := json.Marshal(refs)
		agent := ""
		if task.AgentID != nil {
			agent = *task.AgentID
		}
		out = DiagnosticCase{OwnerID: current.ID, TaskID: task.ID, DiagnosisID: run.ID, AttemptID: run.AttemptID, AnalysisJobID: run.AnalysisJobID, AnalysisRun: run.AnalysisRun, CodeRepairID: in.CodeRepairID, TaskKind: task.Kind, AgentID: agent, ProjectID: project, SourceRevision: revision, Outcome: in.Outcome, CorrectionCode: in.CorrectionCode, Features: featuresJSON, SkillRefs: refsJSON, RequestKey: key, InputDigest: digest, Status: "active", ExpiresAt: time.Now().UTC().Add(90 * 24 * time.Hour)}
		if err = tx.Create(&out).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(current, "diagnostic_case.create", "diagnostic_case", fmt.Sprint(out.ID), request))).Error
	})
	return out, err
}

// RevokeDiagnosticCase 锁定案例后重新核对操作者和任务写权限；撤销可重复调用，不会删除来源或复活旧案例。
func (s *Suggestions) RevokeDiagnosticCase(ctx context.Context, u domain.User, id int64, request string) error {
	return s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		var c DiagnosticCase
		if err := tx.Where("id=?", id).First(&c).Error; err != nil {
			return domain.Fail("NOT_FOUND", "案例不存在")
		}
		current, err := repository.New(tx).User(ctx, u.ID)
		if err != nil {
			return err
		}
		if c.OwnerID != current.ID && !current.IsPlatformAdmin() {
			return domain.Fail("FORBIDDEN", "不能撤销他人案例")
		}
		if _, err = repository.New(tx).AuthorizeTaskWrite(ctx, current, c.TaskID); err != nil {
			return err
		}
		// 与保存流程保持 task→case 锁顺序，避免一个撤销事务反向等待正在保存的来源任务。
		var task domain.Task
		if err = tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", c.TaskID).First(&task).Error; err != nil {
			return err
		}
		if err = tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", id).First(&c).Error; err != nil {
			return err
		}
		current, _, err = lockDiagnosticCaseAuthorization(ctx, tx, u.ID, c.TaskID)
		if err != nil {
			return err
		}
		if c.OwnerID != current.ID && !current.IsPlatformAdmin() {
			return domain.Fail("FORBIDDEN", "不能撤销他人案例")
		}
		if c.Status == "revoked" {
			return nil
		}
		now := time.Now().UTC()
		if err = tx.Model(&DiagnosticCase{}).Where("id=? AND status='active'", c.ID).Updates(map[string]any{"status": "revoked", "revoked_at": now}).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(current, "diagnostic_case.revoke", "diagnostic_case", fmt.Sprint(id), request))).Error
	})
}

// diagnosticCaseFresh 在每次检索时重验来源，不把保存时的授权当作永久读取许可。
// 原诊断摘要校验发现来源被替换；项目案例必须继续匹配已登记版本，不能跨项目迁移。
func (s *Suggestions) diagnosticCaseFresh(ctx context.Context, u domain.User, c DiagnosticCase, registered map[string]string) bool {
	if c.OwnerID != u.ID || !c.ExpiresAt.After(time.Now()) {
		return false
	}
	current, task, run, err := diagnosticCaseSource(ctx, s.Repo.Context(ctx), u, c.TaskID, c.DiagnosisID, false)
	if err != nil || run.AttemptID != c.AttemptID || run.AnalysisJobID != c.AnalysisJobID || run.AnalysisRun != c.AnalysisRun || task.Kind != c.TaskKind {
		return false
	}
	agent := ""
	if task.AgentID != nil {
		agent = *task.AgentID
	}
	if agent != c.AgentID {
		return false
	}
	project, revision, err := diagnosticProjectContext(ctx, s.Repo.Context(ctx), task.ID, run.ID)
	if err != nil || project != c.ProjectID || revision != c.SourceRevision {
		return false
	}
	if project != "" {
		actual, exists := registered[project]
		if !exists {
			actual, err = s.diagnosticRegisteredRevision(ctx, project)
			if err != nil {
				actual = ""
			}
			registered[project] = actual
		}
		if actual != revision {
			return false
		}
	}
	if c.Outcome == "verified_repair" {
		if c.CodeRepairID == nil {
			return false
		}
		if _, err = diagnosticVerifiedRepair(ctx, s.Repo.Context(ctx), current, c.TaskID, c.DiagnosisID, *c.CodeRepairID, registered[project]); err != nil {
			return false
		}
	}
	if c.Outcome == "reviewed_correction" && diagnosticCorrectionText[c.CorrectionCode] == "" {
		return false
	}
	return true
}

// ListDiagnosticCases 返回本人最多 50 个可审计来源，taskID=0 表示全本人；逐条来源重验后才可返回。
// 已撤销但仍有授权的案例保留展示，便于解释为何不再检索；过期或来源失效的案例不会返回。
func (s *Suggestions) ListDiagnosticCases(ctx context.Context, u domain.User, taskID int64) ([]DiagnosticCase, error) {
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		return nil, diagnosticKnowledgeTimeout()
	}
	items, err := s.listDiagnosticCases(ctx, u, taskID)
	if ctx.Err() != nil {
		return nil, diagnosticKnowledgeTimeout()
	}
	return items, err
}

// listDiagnosticCases 的全部数据库和登记查询共用外层八秒预算；单条来源失败可排除，整体超时必须显式失败。
func (s *Suggestions) listDiagnosticCases(ctx context.Context, u domain.User, taskID int64) ([]DiagnosticCase, error) {
	items := []DiagnosticCase{}
	current, err := s.Repo.User(ctx, u.ID)
	if err != nil {
		return items, err
	}
	if taskID < 0 {
		return items, domain.Fail("INVALID_PARAMETERS", "任务来源无效")
	}
	if taskID > 0 {
		if _, err = s.Repo.AuthorizeTaskWrite(ctx, current, taskID); err != nil {
			return items, err
		}
	}
	var candidates []DiagnosticCase
	q := s.Repo.Context(ctx).Where("owner_id=? AND expires_at>now()", current.ID)
	if taskID > 0 {
		q = q.Where("task_id=?", taskID)
	}
	if err = q.Order("created_at DESC,id DESC").Limit(50).Find(&candidates).Error; err != nil {
		return items, err
	}
	registered := map[string]string{}
	for _, c := range candidates {
		if s.diagnosticCaseFresh(ctx, current, c, registered) {
			items = append(items, c)
		}
	}
	return items, nil
}

func diagnosticSimilarity(current, old diagnosticFeatures) (int, []string) {
	if current.Unit != old.Unit || !slices.Equal(current.Quality, old.Quality) {
		return 0, nil
	}
	score := 0
	reasons := []string{}
	for _, symbol := range current.Symbols {
		if slices.Contains(old.Symbols, symbol) {
			score += 10
		}
	}
	if score > 0 {
		reasons = append(reasons, "hotspot_fingerprint_overlap")
	}
	hintMatches := 0
	for _, hint := range current.Hints {
		if slices.Contains(old.Hints, hint) {
			hintMatches++
		}
	}
	if hintMatches > 0 {
		score += hintMatches * 2
		reasons = append(reasons, "symbol_hint_overlap")
	}
	if score == 0 {
		return 0, nil
	}
	reasons = append(reasons, "same_kind_agent_project_version", "same_quality_conditions")
	return score, reasons
}

// ReadDiagnosticKnowledge 只能读取当前绑定任务；空参数不能指定历史任务、项目或任意查询。
// 个人案例候选最多 50、返回最多 3，排名只看脱敏指纹和条件；读取失败不静默冒充“没有案例”。
func (s *Suggestions) ReadDiagnosticKnowledge(ctx context.Context, u domain.User, run DiagnosisRun, args json.RawMessage, includeCases bool) (DiagnosticKnowledge, error) {
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	if ctx.Err() != nil {
		return DiagnosticKnowledge{}, diagnosticKnowledgeTimeout()
	}
	out, err := s.readDiagnosticKnowledge(ctx, u, run, args, includeCases)
	if ctx.Err() != nil {
		return DiagnosticKnowledge{}, diagnosticKnowledgeTimeout()
	}
	return out, err
}

// readDiagnosticKnowledge 的有界来源扫描共享调用方预算，不能将取消后排除的候选伪装为正常空集。
func (s *Suggestions) readDiagnosticKnowledge(ctx context.Context, u domain.User, run DiagnosisRun, args json.RawMessage, includeCases bool) (DiagnosticKnowledge, error) {
	out := DiagnosticKnowledge{Skills: []DiagnosticSkill{}, Cases: []DiagnosticCasePrior{}, PriorOnly: true, HistoryEnabled: includeCases && run.KnowledgeMode == "experience"}
	if err := emptyDiagnosticKnowledgeArgs(args); err != nil {
		return out, err
	}
	current, err := s.Repo.User(ctx, u.ID)
	if err != nil {
		return out, err
	}
	task, err := s.Repo.AuthorizeTaskWrite(ctx, current, run.TaskID)
	if err != nil {
		return out, err
	}
	latest, err := s.Repo.LatestAttempt(ctx, run.TaskID)
	if err != nil {
		return out, err
	}
	if latest.ID != run.AttemptID {
		return out, domain.Fail("STALE_ATTEMPT", "诊断知识来源采集轮次已更新")
	}
	if err = s.ValidateDiagnosisSource(ctx, run); err != nil {
		return out, err
	}
	if run.KnowledgeMode == "" || run.KnowledgeMode == "off" {
		return out, nil
	}
	if run.KnowledgeMode != "skills" && run.KnowledgeMode != "experience" {
		return out, domain.Fail("INVALID_PARAMETERS", "诊断知识模式无效")
	}
	raw, err := s.Repo.Result(ctx, task.ID)
	if err != nil {
		return out, err
	}
	out.Skills, err = MatchDiagnosticSkills(task, raw)
	if err != nil {
		return out, err
	}
	methods, methodErr := s.matchedDiagnosticPlaybooks(ctx, current, task, run)
	if methodErr != nil {
		return out, methodErr
	}
	out.Skills = append(out.Skills, methods...)
	if !out.HistoryEnabled {
		return out, err
	}
	features, err := diagnosticFeatureSet(task, raw)
	if err != nil {
		return out, err
	}
	project, revision, err := diagnosticProjectContext(ctx, s.Repo.Context(ctx), task.ID, run.ID)
	if err != nil {
		return out, err
	}
	agent := ""
	if task.AgentID != nil {
		agent = *task.AgentID
	}
	var candidates []DiagnosticCase
	if err = s.Repo.Context(ctx).Where("owner_id=? AND status='active' AND expires_at>now() AND created_at<=? AND task_kind=? AND agent_id=? AND project_id=? AND source_revision=? AND diagnosis_id<>?", current.ID, run.CreatedAt, task.Kind, agent, project, revision, run.ID).Order("created_at DESC,id DESC").Limit(50).Find(&candidates).Error; err != nil {
		return out, err
	}
	type scored struct {
		c       DiagnosticCase
		score   int
		reasons []string
		refs    []DiagnosticSkillRef
	}
	matches := []scored{}
	registered := map[string]string{}
	for _, c := range candidates {
		if !s.diagnosticCaseFresh(ctx, current, c, registered) {
			continue
		}
		var old diagnosticFeatures
		var refs []DiagnosticSkillRef
		if json.Unmarshal(c.Features, &old) != nil || json.Unmarshal(c.SkillRefs, &refs) != nil {
			continue
		}
		// 任一方法被移除或版本改变就排除整个案例；不能只保留部分引用后暗示旧案例遵循新目录。
		catalog, _ := BuiltinDiagnosticSkills()
		allCurrent := len(refs) > 0
		for _, ref := range refs {
			if !slices.ContainsFunc(catalog, func(skill DiagnosticSkill) bool { return skill.ID == ref.ID && skill.Version == ref.Version }) {
				allCurrent = false
				break
			}
		}
		if !allCurrent {
			continue
		}
		score, reasons := diagnosticSimilarity(features, old)
		if score > 0 {
			matches = append(matches, scored{c, score, reasons, refs})
		}
	}
	sort.SliceStable(matches, func(i, j int) bool {
		if matches[i].score != matches[j].score {
			return matches[i].score > matches[j].score
		}
		return matches[i].c.ID > matches[j].c.ID
	})
	for i, m := range matches {
		if i >= 3 {
			break
		}
		assurance := "human_reviewed"
		text := diagnosticCorrectionText[m.c.CorrectionCode]
		if m.c.Outcome == "verified_repair" {
			assurance = "controlled_benchmark_verified"
			text = "同一登记源码版本曾在隔离快照完成受控功能与性能验证；本次根因和收益仍须当前证据与独立复测。"
		}
		out.Cases = append(out.Cases, DiagnosticCasePrior{ID: m.c.ID, SourceDiagnosisID: m.c.DiagnosisID, Outcome: m.c.Outcome, Assurance: assurance, Conclusion: text, SkillRefs: m.refs, MatchReasons: m.reasons, PriorOnly: true})
	}
	// 超出上游工具观测预算时失败，不截断 JSON 或任意丢失案例的验证边界。
	encoded, _ := json.Marshal(out)
	if len(encoded) > 15<<10 {
		return DiagnosticKnowledge{}, domain.Fail("AI_BUDGET_EXCEEDED", "诊断知识输出超过预算")
	}
	return out, nil
}

func diagnosticKnowledgeTimeout() error {
	return domain.Fail("KNOWLEDGE_TIMEOUT", "诊断知识读取超时或已取消，请重试")
}
