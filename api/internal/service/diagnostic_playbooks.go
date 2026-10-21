// 项目/团队方法库采用“草稿→明确复核发布→退役”的不可变版本流程。
// 方法只复用诊断步骤，不能赋予数据源、采集器或代码执行权限；自由说明仍按不可信上下文处理。
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

type DiagnosticPlaybook struct {
	ID            int64           `json:"id,string"`
	OwnerID       string          `json:"owner_id"`
	GroupID       *string         `json:"group_id,omitempty"`
	ProjectID     string          `json:"project_id,omitempty"`
	LogicalKey    string          `json:"logical_key"`
	Version       int             `json:"version"`
	Skill         json.RawMessage `json:"skill"`
	ContentDigest string          `json:"content_digest"`
	Status        string          `json:"status"`
	ReviewerID    *string         `json:"reviewer_id,omitempty"`
	ReviewedAt    *time.Time      `json:"reviewed_at,omitempty"`
	ExpiresAt     time.Time       `json:"expires_at"`
	CreatedAt     time.Time       `json:"created_at"`
	UpdatedAt     time.Time       `json:"updated_at"`
}

type DiagnosticPlaybookInput struct {
	LogicalKey  string          `json:"logical_key"`
	Version     int             `json:"version"`
	GroupID     *string         `json:"group_id,omitempty"`
	ProjectID   string          `json:"project_id,omitempty"`
	Skill       DiagnosticSkill `json:"skill"`
	ExpiresDays int             `json:"expires_days"`
}

func validateDiagnosticPlaybook(in DiagnosticPlaybookInput) error {
	if !telemetryIdentifier.MatchString(in.LogicalKey) || in.Version < 1 || in.Version > 10000 || in.ExpiresDays < 1 || in.ExpiresDays > 90 || len(in.ProjectID) > 80 || in.ProjectID != "" && !telemetryIdentifier.MatchString(in.ProjectID) {
		return domain.Fail("INVALID_PARAMETERS", "方法标识、版本、项目或有效期无效")
	}
	skill := in.Skill
	if skill.ID != "" || skill.Version != "" || len(skill.MatchReasons) > 0 || len(skill.Title) < 1 || len(skill.Title) > 180 || !validText(skill.Title) || len(skill.TaskKinds) < 1 || len(skill.TaskKinds) > 18 || len(skill.RequiredTools) < 1 || len(skill.RequiredTools) > 5 {
		return domain.Fail("INVALID_PARAMETERS", "方法目录字段无效")
	}
	if hasDuplicatePlaybookItems(skill.TaskKinds) || hasDuplicatePlaybookItems(skill.RequiredTools) {
		return domain.Fail("INVALID_PARAMETERS", "方法类型和工具不能重复")
	}
	for _, kind := range skill.TaskKinds {
		if _, exists := domain.Kind(kind); !exists {
			return domain.Fail("INVALID_PARAMETERS", "方法采集类型无效")
		}
	}
	for _, tool := range skill.RequiredTools {
		if !slices.Contains([]string{"inspect_hotspots", "check_collection_quality", "inspect_call_paths", "compare_profiles", "query_business_signals", "inspect_service_topology"}, tool) {
			return domain.Fail("INVALID_PARAMETERS", "方法不能增加工具权限")
		}
	}
	for _, items := range [][]string{skill.Steps, skill.CounterEvidence, skill.StopConditions, skill.Verification} {
		if len(items) < 1 || len(items) > 6 || hasDuplicatePlaybookItems(items) {
			return domain.Fail("INVALID_PARAMETERS", "方法必须包括步骤、反证、停止与验收")
		}
		for _, text := range items {
			if len(text) < 1 || len(text) > 600 || !validText(text) {
				return domain.Fail("INVALID_PARAMETERS", "方法说明必须为有界纯文本")
			}
		}
	}
	return nil
}

func hasDuplicatePlaybookItems(items []string) bool {
	seen := map[string]bool{}
	for _, item := range items {
		if seen[item] {
			return true
		}
		seen[item] = true
	}
	return false
}

// 方法中的每个数组元素是一段单独说明，不需要换行或控制字符。
// UTF-8 校验先于 JSON 编码，避免无效字节被编码器替换后仍作为同一幂等输入接受。
func validText(text string) bool {
	if !utf8.ValidString(text) || strings.TrimSpace(text) == "" || strings.ContainsAny(text, "<>") || strings.Contains(strings.ToLower(text), "http://") || strings.Contains(strings.ToLower(text), "https://") {
		return false
	}
	for _, char := range text {
		if unicode.IsControl(char) || unicode.In(char, unicode.Cf) {
			return false
		}
	}
	return true
}

func (s *Suggestions) CreateDiagnosticPlaybook(ctx context.Context, u domain.User, in DiagnosticPlaybookInput, request string) (DiagnosticPlaybook, error) {
	var out DiagnosticPlaybook
	if err := validateDiagnosticPlaybook(in); err != nil {
		return out, err
	}
	err := s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		// 同一 owner/key 的所有版本共用事务锁，既防并发创建冲突，也让作用域检查与插入不可分割。
		// 锁键来自已认证用户标识与有界逻辑键；所有 SQL 值保持参数化。
		lockKey, _ := json.Marshal([]string{u.ID, in.LogicalKey})
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,417628))", string(lockKey)).Error; err != nil {
			return err
		}
		var versions []DiagnosticPlaybook
		// 最早版本固定作用域；当前版本用于内容幂等。无须读取这个逻辑键下所有历史正文。
		if err := tx.Clauses(clause.Locking{Strength: "SHARE"}).Where("owner_id=? AND logical_key=? AND (version=? OR id=(SELECT min(id) FROM diagnostic_playbooks WHERE owner_id=? AND logical_key=?))", u.ID, in.LogicalKey, in.Version, u.ID, in.LogicalKey).Order("id").Find(&versions).Error; err != nil {
			return err
		}
		r := repository.New(tx)
		var current domain.User
		err := tx.Clauses(clause.Locking{Strength: "SHARE"}).Where("id=?", u.ID).First(&current).Error
		if err != nil {
			return err
		}
		if !current.CanWrite() {
			return domain.Fail("FORBIDDEN", "只读用户不能创建诊断方法")
		}
		for _, existing := range versions {
			if !equalOptionalString(existing.GroupID, in.GroupID) || existing.ProjectID != in.ProjectID {
				return domain.Fail("IDEMPOTENCY_CONFLICT", "方法逻辑键不能改变团队或项目作用域")
			}
		}
		if in.GroupID != nil {
			if err = lockPlaybookGroupAuthorization(ctx, tx, current, []string{*in.GroupID}); err != nil {
				return err
			}
			if err = r.CanManageGroup(ctx, current, *in.GroupID); err != nil {
				return err
			}
		}
		if in.ProjectID != "" {
			if !current.IsPlatformAdmin() {
				return domain.Fail("FORBIDDEN", "项目方法需要管理员登记")
			}
			if _, err = s.diagnosticRegisteredRevision(ctx, in.ProjectID); err != nil {
				return err
			}
		}
		skill := in.Skill
		skill.ID = "library_" + in.LogicalKey
		skill.Version = strconv.Itoa(in.Version)
		data, _ := json.Marshal(skill)
		if len(data) > 16384 {
			return domain.Fail("INVALID_PARAMETERS", "方法编码后超过大小限制")
		}
		digest := repository.DigestBytes(data)
		for _, existing := range versions {
			if existing.Version != in.Version {
				continue
			}
			out = existing
			if out.ContentDigest != digest {
				return domain.Fail("IDEMPOTENCY_CONFLICT", "方法版本已用于不同内容或作用域")
			}
			return nil
		}
		out = DiagnosticPlaybook{OwnerID: current.ID, GroupID: in.GroupID, ProjectID: in.ProjectID, LogicalKey: in.LogicalKey, Version: in.Version, Skill: data, ContentDigest: digest, Status: "draft", ExpiresAt: time.Now().UTC().Add(time.Duration(in.ExpiresDays) * 24 * time.Hour)}
		if err = tx.Create(&out).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(current, "diagnostic_playbook.create", "diagnostic_playbook", fmt.Sprint(out.ID), request))).Error
	})
	return out, err
}

func equalOptionalString(a, b *string) bool {
	return a == nil && b == nil || a != nil && b != nil && *a == *b
}

func (s *Suggestions) ListDiagnosticPlaybooks(ctx context.Context, u domain.User) ([]DiagnosticPlaybook, error) {
	items := []DiagnosticPlaybook{}
	current, err := s.Repo.User(ctx, u.ID)
	if err != nil {
		return items, err
	}
	q := s.Repo.Context(ctx).Where("expires_at>now()")
	if !current.IsPlatformAdmin() {
		q = q.Where("owner_id=? OR (status='published' AND group_id IN (SELECT group_id FROM group_members WHERE user_id=?))", current.ID, current.ID)
	}
	err = q.Order("logical_key,version DESC,id").Limit(100).Find(&items).Error
	return items, err
}

// 发布需管理员明确复核；模型不能把自己的草稿自动提升为团队经验。
// 已发布内容不可原地修改；新版本通过逻辑键明确替代旧版本，新诊断只取最高有效版本。
func (s *Suggestions) ReviewDiagnosticPlaybook(ctx context.Context, u domain.User, id int64, action, request string) (DiagnosticPlaybook, error) {
	var out DiagnosticPlaybook
	if action != "publish" && action != "retire" {
		return out, domain.Fail("INVALID_PARAMETERS", "方法操作无效")
	}
	// 非管理员先拒绝，避免通过不存在的方法编号探测管理接口；这个早筛不代替锁后的再次授权。
	current, err := s.Repo.User(ctx, u.ID)
	if err != nil {
		return out, err
	}
	if !current.IsPlatformAdmin() {
		return out, domain.Fail("FORBIDDEN", "方法发布或退役需要管理员复核")
	}
	err = s.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		// 先等方法行，再锁并重读管理员。若等待期间账号降权，旧的调用参数不能继续授权发布。
		if err := tx.Clauses(clause.Locking{Strength: "UPDATE"}).Where("id=?", id).First(&out).Error; err != nil {
			return err
		}
		var current domain.User
		if err := tx.Clauses(clause.Locking{Strength: "SHARE"}).Where("id=?", u.ID).First(&current).Error; err != nil {
			return err
		}
		if !current.IsPlatformAdmin() {
			return domain.Fail("FORBIDDEN", "方法发布或退役需要管理员复核")
		}
		var now time.Time
		if err := tx.Raw("SELECT clock_timestamp()").Scan(&now).Error; err != nil {
			return err
		}
		if action == "publish" {
			if !out.ExpiresAt.After(now) {
				return domain.Fail("INVALID_STATE", "只能发布有效草稿")
			}
			if out.Status == "published" {
				return nil
			}
			if out.Status != "draft" {
				return domain.Fail("INVALID_STATE", "只能发布有效草稿")
			}
			out.Status = "published"
			out.ReviewerID = &current.ID
			out.ReviewedAt = &now
		} else {
			out.Status = "retired"
		}
		if err := tx.Model(&out).Updates(map[string]any{"status": out.Status, "reviewer_id": out.ReviewerID, "reviewed_at": out.ReviewedAt, "updated_at": now}).Error; err != nil {
			return err
		}
		return tx.Create(ptrAuditDiagnosis(Audit(current, "diagnostic_playbook."+action, "diagnostic_playbook", fmt.Sprint(id), request))).Error
	})
	return out, err
}

func (s *Suggestions) matchedDiagnosticPlaybooks(ctx context.Context, u domain.User, task domain.Task, run DiagnosisRun) ([]DiagnosticSkill, error) {
	items := []DiagnosticSkill{}
	project, _, err := diagnosticProjectContext(ctx, s.Repo.Context(ctx), task.ID, run.ID)
	if err != nil {
		return items, err
	}
	current, err := s.Repo.User(ctx, u.ID)
	if err != nil {
		return items, err
	}
	// 先在 SQL 中选每个逻辑键的最高有效发布版本，再按采集类型和数据源能力筛选。
	// 新版整体替代旧版：新版不适配当前任务时不回退。筛选前不套列表页的100条截断，避免无关版本饿死匹配。
	base := s.Repo.Context(ctx).Model(&DiagnosticPlaybook{}).Select("DISTINCT ON (owner_id,logical_key) *").
		Where("status='published' AND expires_at>clock_timestamp() AND created_at<=? AND reviewed_at<=?", run.CreatedAt, run.CreatedAt).
		Where("project_id='' OR project_id=?", project)
	if current.IsPlatformAdmin() {
		base = base.Where("owner_id=? OR group_id IS NOT NULL", current.ID)
	} else {
		base = base.Where("(owner_id=? AND group_id IS NULL) OR group_id IN (SELECT group_id FROM group_members WHERE user_id=? AND role IN ('operator','group_admin'))", current.ID, current.ID)
	}
	base = base.Order("owner_id,logical_key,version DESC,id")
	kindJSON, _ := json.Marshal([]string{task.Kind})
	q := s.Repo.Context(ctx).Table("(?) AS eligible_playbooks", base).Where("skill->'task_kinds' @> ?::jsonb", string(kindJSON))
	if run.TelemetryServiceID == "" {
		q = q.Where("NOT (skill->'required_tools' @> ?::jsonb OR skill->'required_tools' @> ?::jsonb)", `["query_business_signals"]`, `["inspect_service_topology"]`)
	}
	var rows []DiagnosticPlaybook
	if err := q.Order("logical_key,owner_id,id").Limit(2).Find(&rows).Error; err != nil {
		return items, err
	}
	for _, row := range rows {
		var skill DiagnosticSkill
		if json.Unmarshal(row.Skill, &skill) != nil || !slices.Contains(skill.TaskKinds, task.Kind) {
			continue
		}
		if run.TelemetryServiceID == "" && (slices.Contains(skill.RequiredTools, "query_business_signals") || slices.Contains(skill.RequiredTools, "inspect_service_topology")) {
			continue
		}
		skill.ID = "playbook_" + fmt.Sprint(row.ID)
		skill.MatchReasons = []string{"reviewed_playbook", "scope_and_kind_match"}
		items = append(items, skill)
		if len(items) >= 2 {
			break
		}
	}
	return items, nil
}

func (s *Suggestions) pinDiagnosticPlaybooks(ctx context.Context, tx *gorm.DB, run DiagnosisRun, loaded *DiagnosticKnowledge) error {
	if loaded == nil {
		return nil
	}
	byID := map[int64]DiagnosticSkill{}
	ids := []int64{}
	for _, skill := range loaded.Skills {
		if !strings.HasPrefix(skill.ID, "playbook_") {
			continue
		}
		id, err := strconv.ParseInt(strings.TrimPrefix(skill.ID, "playbook_"), 10, 64)
		if err != nil || id <= 0 || byID[id].ID != "" || len(ids) >= 2 {
			return domain.Fail("KNOWLEDGE_CHANGED", "方法版本身份无效")
		}
		byID[id] = skill
		ids = append(ids, id)
	}
	if len(ids) == 0 {
		return nil
	}
	slices.Sort(ids)
	var rows []DiagnosticPlaybook
	if err := tx.Clauses(clause.Locking{Strength: "SHARE"}).Where("id IN ?", ids).Order("id").Find(&rows).Error; err != nil {
		return err
	}
	changed := func() error {
		return domain.Fail("KNOWLEDGE_CHANGED", "方法已撤销、过期、改变或不再可见")
	}
	if len(rows) != len(ids) {
		return changed()
	}
	// 方法行全部按升序锁定后，才锁实际用户与成员行。即使没有案例，也不能跳过权限锁。
	var current domain.User
	if err := tx.Clauses(clause.Locking{Strength: "SHARE"}).Where("id=?", run.RequestedBy).First(&current).Error; err != nil {
		return err
	}
	if !current.CanWrite() {
		return changed()
	}
	groups := []string{}
	for _, row := range rows {
		if row.GroupID != nil {
			groups = append(groups, *row.GroupID)
		}
	}
	if err := lockPlaybookGroupAuthorization(ctx, tx, current, groups); err != nil {
		return changed()
	}
	var now time.Time
	if err := tx.Raw("SELECT clock_timestamp()").Scan(&now).Error; err != nil {
		return err
	}
	for _, row := range rows {
		skill := byID[row.ID]
		// clock_timestamp 必须在所有锁等待之后读取；SELECT WHERE 中的时间条件可能在等锁前已判真。
		if row.Status != "published" || !row.ExpiresAt.After(now) || row.ReviewedAt == nil || row.ReviewedAt.After(run.CreatedAt) || strconv.Itoa(row.Version) != skill.Version || row.CreatedAt.After(run.CreatedAt) {
			return domain.Fail("KNOWLEDGE_CHANGED", "方法版本已变化")
		}
		var original DiagnosticSkill
		if json.Unmarshal(row.Skill, &original) != nil {
			return domain.Fail("KNOWLEDGE_CHANGED", "方法内容不可读")
		}
		original.ID = skill.ID
		original.MatchReasons = skill.MatchReasons
		a, _ := json.Marshal(original)
		b, _ := json.Marshal(skill)
		if string(a) != string(b) {
			return domain.Fail("KNOWLEDGE_CHANGED", "方法内容摘要不一致")
		}
		if row.OwnerID != current.ID && row.GroupID == nil {
			return domain.Fail("KNOWLEDGE_CHANGED", "方法不再可见")
		}
		if row.GroupID != nil {
			if err := repository.New(tx).CheckGroup(ctx, current, row.GroupID, true); err != nil {
				return domain.Fail("KNOWLEDGE_CHANGED", "方法团队权限已撤销")
			}
		}
	}
	return nil
}

// 锁住授权实际依赖的成员行后再检查权限，阻止“检查后删除成员、终态仍成功”的窗口。
// 管理员的权限来自已锁定的用户行；普通用户需要每个指定团队的 operator/group_admin 成员身份。
func lockPlaybookGroupAuthorization(ctx context.Context, tx *gorm.DB, u domain.User, groups []string) error {
	if len(groups) == 0 {
		return nil
	}
	slices.Sort(groups)
	groups = slices.Compact(groups)
	if !u.IsPlatformAdmin() {
		var members []struct {
			GroupID string
			Role    string
		}
		if err := tx.Raw("SELECT group_id,role FROM group_members WHERE user_id=? AND group_id IN ? ORDER BY group_id FOR SHARE", u.ID, groups).Scan(&members).Error; err != nil {
			return err
		}
		if len(members) != len(groups) {
			return domain.Fail("FORBIDDEN", "方法团队权限已撤销")
		}
		for _, member := range members {
			if member.Role != "operator" && member.Role != "group_admin" {
				return domain.Fail("FORBIDDEN", "方法团队权限已撤销")
			}
		}
	}
	for _, group := range groups {
		if err := repository.New(tx).CheckGroup(ctx, u, &group, true); err != nil {
			return err
		}
	}
	return nil
}
