package scheduler

import (
	"context"
	"io"
	"log/slog"
	"testing"
	"time"

	"mini-drop/apiserver/internal/repository"
)

type fakeStore struct {
	due   []repository.Schedule
	fired []string
}

func (f *fakeStore) ListDueSchedules(context.Context, time.Time, int) ([]repository.Schedule, error) {
	return f.due, nil
}

func (f *fakeStore) FireSchedule(_ context.Context, id string, _ time.Time) (string, error) {
	f.fired = append(f.fired, id)
	return "task-" + id, nil
}

func TestRunOnceFiresEveryDueSlot(t *testing.T) {
	store := &fakeStore{due: []repository.Schedule{
		{ID: "one", NextRunAt: time.Unix(100, 0)},
		{ID: "two", NextRunAt: time.Unix(200, 0)},
	}}
	runner := New(store, slog.New(slog.NewTextHandler(io.Discard, nil)), time.Second)
	runner.now = func() time.Time { return time.Unix(300, 0) }
	if got := runner.runOnce(context.Background()); got != 2 {
		t.Fatalf("runOnce fired %d schedules, want 2", got)
	}
	if len(store.fired) != 2 || store.fired[0] != "one" || store.fired[1] != "two" {
		t.Fatalf("unexpected firing order: %#v", store.fired)
	}
}
