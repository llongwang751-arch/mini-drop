package cron

import (
	"testing"
	"time"
)

// Expected values are snapshots generated from server/app/cron.py (the single
// source of truth). Regenerate with:
//   python -c "from server.app.cron import next_schedule_fire; ..."
// when cron.py semantics change.
func TestNextScheduleFireParityWithPython(t *testing.T) {
	utc := time.UTC
	cases := []struct {
		name       string
		expression string
		tz         string
		after      time.Time
		want       time.Time
	}{
		{"daily 3am Shanghai", "0 3 * * *", "Asia/Shanghai",
			time.Date(2026, 8, 7, 6, 0, 0, 0, utc), time.Date(2026, 8, 7, 19, 0, 0, 0, utc)},
		{"every 5 min", "*/5 * * * *", "Asia/Shanghai",
			time.Date(2026, 8, 7, 6, 4, 37, 0, utc), time.Date(2026, 8, 7, 6, 5, 0, 0, utc)},
		{"Jan 1 yearly", "0 0 1 1 *", "Asia/Shanghai",
			time.Date(2026, 8, 7, 6, 0, 0, 0, utc), time.Date(2026, 12, 31, 16, 0, 0, 0, utc)},
		{"twice-monthly day list", "30 14 1,15 * *", "UTC",
			time.Date(2026, 8, 7, 6, 0, 0, 0, utc), time.Date(2026, 8, 15, 14, 30, 0, 0, utc)},
		{"weekday 9am", "0 9 * * 1-5", "Asia/Shanghai",
			time.Date(2026, 8, 7, 6, 0, 0, 0, utc), time.Date(2026, 8, 10, 1, 0, 0, 0, utc)},
		{"sunday 8:15", "15 8 * * 0", "Asia/Shanghai",
			time.Date(2026, 8, 7, 6, 0, 0, 0, utc), time.Date(2026, 8, 9, 0, 15, 0, 0, utc)},
		{"23:45 same evening", "45 23 * * *", "Asia/Shanghai",
			time.Date(2026, 8, 7, 15, 44, 0, 0, utc), time.Date(2026, 8, 7, 15, 45, 0, 0, utc)},
		{"noon summer EDT", "0 12 * * *", "America/New_York",
			time.Date(2026, 8, 7, 15, 0, 0, 0, utc), time.Date(2026, 8, 7, 16, 0, 0, 0, utc)},
		{"noon on DST fall-back day", "0 12 * * *", "America/New_York",
			time.Date(2026, 11, 1, 5, 0, 0, 0, utc), time.Date(2026, 11, 1, 17, 0, 0, 0, utc)},
		{"ambiguous 2am on fall-back day", "0 2 * * *", "America/New_York",
			time.Date(2026, 11, 1, 5, 0, 0, 0, utc), time.Date(2026, 11, 1, 7, 0, 0, 0, utc)},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, err := NextScheduleFire(c.expression, c.tz, c.after)
			if err != nil {
				t.Fatalf("NextScheduleFire error: %v", err)
			}
			if !got.Equal(c.want) {
				t.Errorf("got %s want %s", got.UTC().Format(time.RFC3339), c.want.UTC().Format(time.RFC3339))
			}
		})
	}
}

// Never-valid expression (Feb 30) must error rather than loop forever.
func TestNextScheduleFireNeverValidExpression(t *testing.T) {
	if _, err := NextScheduleFire("0 0 30 2 *", "UTC", time.Date(2026, 8, 7, 6, 0, 0, 0, time.UTC)); err == nil {
		t.Fatal("expected an error for Feb 30 cron expression")
	}
}
