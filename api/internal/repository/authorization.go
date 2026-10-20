// Package repository：把用户、组、共享和 Agent 授权转换为 SQL 查询范围。
// 列表、详情和聚合必须采用相同范围；写操作单独校验管理或执行权限，不能将可见性当成可写能力。
package repository

import (
	"context"
	"time"

	"drop/api/internal/domain"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// ResourceScope 为所有读取路径生成统一授权条件，聚合统计也必须使用。
// table 只能是内部常量；用户输入通过 SQL 参数绑定，不能拼进表名或条件。
func ResourceScope(db *gorm.DB, u domain.User, table string, write bool) *gorm.DB {
	if table == "tasks" {
		db = db.Where("tasks.deleted_at IS NULL")
	}
	if u.IsPlatformAdmin() {
		return db
	}
	roleClause := ""
	shareClause := ""
	if write {
		roleClause = " AND gm.role IN ('operator','group_admin')"
	}
	args := []any{u.ID, u.ID}
	if table == "tasks" {
		shareClause = " OR EXISTS(SELECT 1 FROM task_shares sh WHERE sh.task_id=tasks.id AND (sh.user_id=? OR sh.group_id IN (SELECT group_id FROM group_members WHERE user_id=?))"
		args = append(args, u.ID, u.ID)
		if write {
			shareClause += " AND sh.permission='operate'"
		}
		shareClause += ")"
	}
	return db.Where("("+table+".creator_id=? OR ("+table+".visibility='group' AND EXISTS(SELECT 1 FROM group_members gm WHERE gm.group_id="+table+".group_id AND gm.user_id=?"+roleClause+"))"+shareClause+")", args...)
}
func ScopeWrite(db *gorm.DB, u domain.User) *gorm.DB { return ResourceScope(db, u, "tasks", true) }

// AgentScope 按当前用户、所属组和有效 Agent 授权生成目标可见条件；后续采集还要检查执行权限与资源策略。
func AgentScope(db *gorm.DB, u domain.User, operate bool) *gorm.DB {
	if u.IsPlatformAdmin() {
		return db
	}
	p := ""
	if operate {
		p = " AND ga.permission='operate' AND gm.role IN ('operator','group_admin')"
	}
	return db.Where("EXISTS(SELECT 1 FROM group_agents ga JOIN group_members gm ON gm.group_id=ga.group_id WHERE ga.agent_id=agents.id AND gm.user_id=?"+p+")", u.ID)
}
func (r *Repository) AuthorizedAgent(ctx context.Context, u domain.User, id string, operate bool) (domain.Agent, error) {
	var a domain.Agent
	err := AgentScope(r.Context(ctx), u, operate).Where("agents.id=?", id).First(&a).Error
	return a, err
}
func (r *Repository) VisibleAgents(ctx context.Context, u domain.User) ([]domain.Agent, error) {
	a := []domain.Agent{}
	err := AgentScope(r.Context(ctx), u, false).Order("hostname,id").Limit(500).Find(&a).Error
	return a, err
}
func (r *Repository) CheckGroup(ctx context.Context, u domain.User, id *string, write bool) error {
	if id == nil {
		return nil
	}
	var count int64
	q := r.Context(ctx).Table("groups").Where("groups.id=?", *id)
	if !u.IsPlatformAdmin() {
		q = q.Where("EXISTS(SELECT 1 FROM group_members WHERE group_id=groups.id AND user_id=?"+map[bool]string{true: " AND role IN ('operator','group_admin')", false: ""}[write]+")", u.ID)
	}
	if err := q.Count(&count).Error; err != nil {
		return err
	}
	if count != 1 {
		return domain.Fail("FORBIDDEN", "无权访问此用户组")
	}
	return nil
}
func (r *Repository) CanManageGroup(ctx context.Context, u domain.User, id string) error {
	if !u.CanWrite() {
		return domain.Fail("FORBIDDEN", "只读角色")
	}
	if u.IsPlatformAdmin() {
		return r.CheckGroup(ctx, u, &id, false)
	}
	var n int64
	err := r.Context(ctx).Table("group_members").Where("group_id=? AND user_id=? AND role='group_admin'", id, u.ID).Count(&n).Error
	if err != nil {
		return err
	}
	if n == 0 {
		return domain.Fail("FORBIDDEN", "仅本组管理员可执行此操作")
	}
	return nil
}
func (r *Repository) CanManageTask(ctx context.Context, u domain.User, t domain.Task) error {
	if !u.CanWrite() {
		return domain.Fail("FORBIDDEN", "只读角色")
	}
	if u.IsPlatformAdmin() || u.ID == t.OwnerID {
		return nil
	}
	if t.GroupID != nil {
		return r.CanManageGroup(ctx, u, *t.GroupID)
	}
	return domain.Fail("FORBIDDEN", "仅任务所有者或所属组管理员可管理共享")
}

// AuthorizeTaskWrite 将任务写权限落实到当前 SQL 快照；能看到共享任务不代表能够取消、重试或启动派生任务。
func (r *Repository) AuthorizeTaskWrite(ctx context.Context, u domain.User, id int64) (domain.Task, error) {
	var t domain.Task
	if !u.CanWrite() {
		return t, domain.Fail("FORBIDDEN", "只读角色")
	}
	err := ScopeWrite(r.Context(ctx), u).Where("tasks.id=?", id).First(&t).Error
	return t, err
}
func (r *Repository) Groups(ctx context.Context, u domain.User) ([]domain.Group, error) {
	items := []domain.Group{}
	q := r.Context(ctx)
	if !u.IsPlatformAdmin() {
		q = q.Where("id IN (SELECT group_id FROM group_members WHERE user_id=?)", u.ID)
	}
	err := q.Order("name,id").Find(&items).Error
	return items, err
}
func (r *Repository) GroupDetail(ctx context.Context, u domain.User, id string) (domain.Group, []map[string]any, []map[string]any, error) {
	var g domain.Group
	members := []map[string]any{}
	agents := []map[string]any{}
	if err := r.CheckGroup(ctx, u, &id, false); err != nil {
		return g, members, agents, err
	}
	if err := r.Context(ctx).Where("id=?", id).First(&g).Error; err != nil {
		return g, members, agents, err
	}
	err := r.Context(ctx).Table("group_members gm").Select("gm.user_id,u.username,u.display_name,gm.role").Joins("JOIN users u ON u.id=gm.user_id").Where("gm.group_id=?", id).Order("u.username").Find(&members).Error
	if err == nil {
		err = r.Context(ctx).Table("group_agents").Select("agent_id,permission").Where("group_id=?", id).Order("agent_id").Find(&agents).Error
	}
	return g, members, agents, err
}

// 固定限流窗口保存在 PostgreSQL；多个 API 实例并发递增同一行，不能通过切换实例绕过额度。
func (r *Repository) AllowRate(ctx context.Context, key string, limit int, window time.Duration) (bool, error) {
	seconds := int64(window / time.Second)
	var n int
	err := r.Context(ctx).Raw(`INSERT INTO rate_limits(key,window_start,hits,expires_at) VALUES(?,to_timestamp(floor(extract(epoch FROM now())/?)*?),1,now()+(? * interval '1 second')) ON CONFLICT(key) DO UPDATE SET hits=CASE WHEN rate_limits.window_start=excluded.window_start THEN rate_limits.hits+1 ELSE 1 END,window_start=excluded.window_start,expires_at=excluded.expires_at RETURNING hits`, Digest(key), seconds, seconds, seconds).Scan(&n).Error
	return n <= limit, err
}

// PutGroupMember 在管理权限约束下更新组成员；权限变化必须立即影响下一次查询和长连接重验，不能只更新前端按钮。
func (r *Repository) PutGroupMember(ctx context.Context, u domain.User, gid, uid, role string) error {
	if err := r.CanManageGroup(ctx, u, gid); err != nil {
		return err
	}
	if role != "viewer" && role != "operator" && role != "group_admin" {
		return domain.Fail("INVALID_ROLE", "组成员角色无效")
	}
	return r.Context(ctx).Exec("INSERT INTO group_members(group_id,user_id,role) VALUES(?,?,?) ON CONFLICT(group_id,user_id) DO UPDATE SET role=excluded.role", gid, uid, role).Error
}
func (r *Repository) PutGroupAgent(ctx context.Context, u domain.User, gid, aid, permission string) error {
	if !u.IsPlatformAdmin() {
		if err := r.CanManageGroup(ctx, u, gid); err != nil {
			return err
		}
		if _, err := r.AuthorizedAgent(ctx, u, aid, permission == "operate"); err != nil {
			return domain.Fail("FORBIDDEN", "不能委派自身未获授权的Agent权限")
		}
	}
	if permission != "read" && permission != "operate" {
		return domain.Fail("INVALID_PARAMETERS", "授权无效")
	}
	return r.Context(ctx).Exec("INSERT INTO group_agents(group_id,agent_id,permission) VALUES(?,?,?) ON CONFLICT(group_id,agent_id) DO UPDATE SET permission=excluded.permission", gid, aid, permission).Error
}
func (r *Repository) Shares(ctx context.Context, u domain.User, id int64) ([]map[string]any, error) {
	t, err := r.Task(ctx, u, id)
	if err != nil {
		return nil, err
	}
	if err = r.CanManageTask(ctx, u, t); err != nil {
		return nil, err
	}
	rows := []map[string]any{}
	err = r.Context(ctx).Table("task_shares").Where("task_id=?", id).Order("id").Find(&rows).Error
	return rows, err
}
func (r *Repository) CreateShare(ctx context.Context, u domain.User, id int64, uid, gid *string, permission string) (map[string]any, error) {
	if (uid == nil) == (gid == nil) || (permission != "read" && permission != "operate") {
		return nil, domain.Fail("INVALID_PARAMETERS", "指定单一共享主体与权限")
	}
	t, err := r.Task(ctx, u, id)
	if err != nil {
		return nil, err
	}
	if err = r.CanManageTask(ctx, u, t); err != nil {
		return nil, err
	}
	row := map[string]any{"task_id": id, "user_id": uid, "group_id": gid, "permission": permission, "created_by": u.ID}
	err = r.Context(ctx).Table("task_shares").Clauses(clause.Returning{}).Create(&row).Error
	return row, err
}
