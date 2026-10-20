// Package service：处理密码验证、会话创建和账户管理；HTTP Cookie 保存随机会话令牌，数据库保存其摘要、独立 CSRF 值和过期边界。
// 账户降权或修改必须反映到后续请求与长连接重验中，不能让旧角色一直驻留在内存。
package service

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"regexp"
	"time"

	"github.com/google/uuid"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

type Accounts struct {
	Repo      *repository.Repository
	dummyHash []byte
}

func NewAccounts(r *repository.Repository) *Accounts {
	hash, _ := bcrypt.GenerateFromPassword([]byte("timing-equalization"), 12)
	return &Accounts{Repo: r, dummyHash: hash}
}

// RandomToken 生成不可预测的会话或临时 capability；令牌只在必要边界传递，日志和公开对象不保存原文。
func RandomToken() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic("secure random unavailable")
	}
	return hex.EncodeToString(b)
}
func ValidatePassword(password string) *domain.Error {
	if len(password) < 12 || len(password) > 72 {
		return domain.Fail("INVALID_PASSWORD", "密码长度必须为 12–72 字节")
	}
	return nil
}
func (a *Accounts) Bootstrap(ctx context.Context, name, password string) error {
	if name == "" || password == "" {
		return errors.New("ADMIN_USERNAME and ADMIN_PASSWORD are required")
	}
	if err := ValidatePassword(password); err != nil {
		return err
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), 12)
	if err != nil {
		return err
	}
	return a.Repo.Bootstrap(ctx, domain.User{ID: uuid.NewString(), Username: name, DisplayName: "平台管理员", Role: "admin", PasswordHash: string(hash), CreatedAt: time.Now().UTC()})
}

// Login 验证密码后创建新的持久化会话与 CSRF 令牌，并记录审计。
// 返回给 Cookie 的会话原令牌在查询前取摘要；CSRF 用于证明写请求的主动提交，两者不能混为一种权限。
func (a *Accounts) Login(ctx context.Context, name, password, request string) (domain.User, string, string, error) {
	u, err := a.Repo.UserByName(ctx, name)
	if err != nil && !errors.Is(err, gorm.ErrRecordNotFound) {
		return u, "", "", err
	}
	hash := a.dummyHash
	hasPassword := err == nil && u.PasswordHash != ""
	if hasPassword {
		hash = []byte(u.PasswordHash)
	}
	valid := bcrypt.CompareHashAndPassword(hash, []byte(password)) == nil && hasPassword
	if !valid || err != nil {
		_ = a.Repo.Audit(ctx, Audit(domain.User{ID: ""}, "auth.login_failed", "user", "", request))
		return domain.User{}, "", "", domain.Fail("INVALID_CREDENTIALS", "用户名或密码错误")
	}
	token, csrf := RandomToken(), RandomToken()
	session := repository.Session{TokenHash: repository.Digest(token), UserID: u.ID, CSRFToken: csrf, ExpiresAt: time.Now().Add(12 * time.Hour), CreatedAt: time.Now().UTC()}
	if err := a.Repo.NewSession(ctx, session); err != nil {
		return u, "", "", err
	}
	if err := a.Repo.Audit(ctx, Audit(u, "auth.login", "user", u.ID, request)); err != nil {
		return u, "", "", err
	}
	return u, token, csrf, nil
}
func (a *Accounts) CreateUser(ctx context.Context, admin domain.User, name, display, password, role, request string) (domain.User, error) {
	if !admin.IsPlatformAdmin() {
		return domain.User{}, domain.Fail("FORBIDDEN", "仅管理员可管理用户")
	}
	if !regexp.MustCompile(`^[a-zA-Z0-9_.-]{3,64}$`).MatchString(name) {
		return domain.User{}, domain.Fail("INVALID_USERNAME", "用户名须为 3–64 位字母、数字、点、横线或下划线")
	}
	if err := ValidatePassword(password); err != nil {
		return domain.User{}, err
	}
	if role != "viewer" && role != "operator" && role != "admin" && role != "group_admin" && role != "platform_admin" {
		return domain.User{}, domain.Fail("INVALID_ROLE", "角色无效")
	}
	if len(display) > 120 {
		return domain.User{}, domain.Fail("INVALID_PARAMETERS", "显示名称过长")
	}
	if display == "" {
		display = name
	}
	hash, err := bcrypt.GenerateFromPassword([]byte(password), 12)
	if err != nil {
		return domain.User{}, err
	}
	u := domain.User{ID: uuid.NewString(), Username: name, DisplayName: display, Role: role, PasswordHash: string(hash), CreatedAt: time.Now().UTC()}
	if err := a.Repo.CreateUser(ctx, &u); err != nil {
		if errors.Is(err, gorm.ErrDuplicatedKey) {
			return domain.User{}, domain.Fail("USER_EXISTS", "该用户名已存在")
		}
		return u, err
	}
	return u, a.Repo.Audit(ctx, Audit(admin, "user.create", "user", u.ID, request))
}

// UpdateUser 以当前管理员权限修改账户，并使角色或密码变更反映到后续认证；已打开的资源流也必须重新读取当前用户。
func (a *Accounts) UpdateUser(ctx context.Context, admin domain.User, id string, role, display, password *string, request string) (domain.User, error) {
	var out domain.User
	if !admin.IsPlatformAdmin() {
		return out, domain.Fail("FORBIDDEN", "仅平台管理员可管理用户")
	}
	changes := map[string]any{}
	if role != nil {
		switch *role {
		case "viewer", "operator", "group_admin", "platform_admin", "admin":
			changes["role"] = *role
		default:
			return out, domain.Fail("INVALID_ROLE", "角色无效")
		}
	}
	if display != nil {
		if *display == "" || len(*display) > 120 {
			return out, domain.Fail("INVALID_PARAMETERS", "显示名称无效")
		}
		changes["display_name"] = *display
	}
	if password != nil {
		if err := ValidatePassword(*password); err != nil {
			return out, err
		}
		hash, err := bcrypt.GenerateFromPassword([]byte(*password), 12)
		if err != nil {
			return out, err
		}
		changes["password_hash"] = string(hash)
	}
	err := a.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended('platform-admin-membership',0))").Error; err != nil {
			return err
		}
		if err := tx.Where("id=?", id).First(&out).Error; err != nil {
			return err
		}
		if out.IsPlatformAdmin() && role != nil && *role != "admin" && *role != "platform_admin" {
			var count int64
			if err := tx.Model(&domain.User{}).Where("role IN ('admin','platform_admin')").Count(&count).Error; err != nil {
				return err
			}
			if count <= 1 {
				return domain.Fail("INVALID_STATE", "不能移除最后一个平台管理员")
			}
		}
		if len(changes) > 0 {
			if err := tx.Model(&out).Updates(changes).Error; err != nil {
				return err
			}
		}
		if password != nil {
			if err := tx.Where("user_id=?", id).Delete(&repository.Session{}).Error; err != nil {
				return err
			}
		}
		return tx.Create(ptrAudit(Audit(admin, "user.update", "user", id, request))).Error
	})
	return out, err
}
