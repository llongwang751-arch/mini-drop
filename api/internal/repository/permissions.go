package repository

import (
	"context"
	"drop/api/internal/domain"
	"errors"
	"gorm.io/gorm"
)

func (r *Repository) TaskPermissions(ctx context.Context, u domain.User, t domain.Task) (domain.TaskPermissions, error) {
	p := domain.TaskPermissions{Read: true, Reanalyze: u.IsPlatformAdmin()}
	if u.CanWrite() {
		if _, err := r.AuthorizeTaskWrite(ctx, u, t.ID); err == nil {
			p.Operate = true
		} else if !errors.Is(err, gorm.ErrRecordNotFound) {
			return p, err
		}
	}
	if err := r.CanManageTask(ctx, u, t); err == nil {
		p.Share = true
		p.Delete = true
	} else {
		var de *domain.Error
		if !errors.As(err, &de) || de.Code != "FORBIDDEN" {
			return p, err
		}
	}
	return p, nil
}
