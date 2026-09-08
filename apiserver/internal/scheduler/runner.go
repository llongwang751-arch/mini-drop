package scheduler

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"mini-drop/apiserver/internal/repository"
)

type Store interface {
	ListDueSchedules(context.Context, time.Time, int) ([]repository.Schedule, error)
	FireSchedule(context.Context, string, time.Time) (string, error)
}

type Runner struct {
	store    Store
	logger   *slog.Logger
	interval time.Duration
	now      func() time.Time
}

func New(store Store, logger *slog.Logger, interval time.Duration) *Runner {
	if interval < time.Second {
		interval = time.Second
	}
	return &Runner{store: store, logger: logger, interval: interval, now: time.Now}
}

func (r *Runner) Run(ctx context.Context) {
	r.runOnce(ctx)
	ticker := time.NewTicker(r.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			r.runOnce(ctx)
		}
	}
}

func (r *Runner) runOnce(ctx context.Context) int {
	now := r.now().UTC()
	due, err := r.store.ListDueSchedules(ctx, now, 100)
	if err != nil {
		if !errors.Is(err, context.Canceled) {
			r.logger.Error("schedule polling failed", "error", err)
		}
		return 0
	}
	fired := 0
	for _, schedule := range due {
		taskID, err := r.store.FireSchedule(ctx, schedule.ID, schedule.NextRunAt)
		if errors.Is(err, repository.ErrConflict) {
			continue
		}
		if err != nil {
			r.logger.Error("schedule firing failed", "schedule_id", schedule.ID, "error", err)
			continue
		}
		fired++
		r.logger.Info(
			"schedule fired",
			"schedule_id", schedule.ID,
			"scheduled_at", schedule.NextRunAt,
			"task_id", taskID,
		)
	}
	return fired
}
