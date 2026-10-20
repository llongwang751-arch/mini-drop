// 本文件验证只读角色应在访问仓储前被拒绝，以及幂等键、密码等入口值的长度边界。
// 测试名称标明具体场景；涉及真实数据库的用例需显式配置测试连接，协议夹具不代表生产或模型实测。
package service

import (
	"context"
	"testing"

	"drop/api/internal/domain"
)

func TestViewerCannotCreateOrCancelBeforeRepositoryAccess(t *testing.T) {
	s := &Tasks{}
	viewer := domain.User{ID: "viewer", Role: "viewer"}
	if _, _, err := s.Create(context.Background(), viewer, domain.CreateTask{}, "key-123456", "request"); err == nil {
		t.Fatal("viewer create accepted")
	}
	if _, err := s.Cancel(context.Background(), viewer, 1, "request"); err == nil {
		t.Fatal("viewer cancel accepted")
	}
	if _, _, err := s.Retry(context.Background(), viewer, 1, "key-123456", "request"); err == nil {
		t.Fatal("viewer retry accepted")
	}
}
func TestIdempotencyKeysAreBounded(t *testing.T) {
	for _, key := range []string{"", "short", "request\r\nheader"} {
		if ValidateKey(key) == nil {
			t.Fatalf("accepted %q", key)
		}
	}
	if err := ValidateKey("unit-request-1234"); err != nil {
		t.Fatal(err)
	}
}
func TestPasswordBounds(t *testing.T) {
	if ValidatePassword("short") == nil {
		t.Fatal("accepted weak password")
	}
	if err := ValidatePassword("valid-long-password-123"); err != nil {
		t.Fatal(err)
	}
}
