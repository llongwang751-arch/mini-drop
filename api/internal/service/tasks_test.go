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
