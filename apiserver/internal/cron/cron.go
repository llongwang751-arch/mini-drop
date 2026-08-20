// Package cron implements the minimal 5-field cron next-fire computation used
// by /api/schedules. It is a faithful port of server/app/cron.py so the Go
// schedule surface and the Python schedule worker agree on the next fire time.
//
// Field semantics (matching cron.py):
//   - * / lists (a,b) / ranges (a-b) / steps (*/n, a-b/n)
//   - Day-of-week uses 0=Sunday..6=Saturday
//   - When BOTH day-of-month and day-of-week are restricted, BOTH must match
//     (some cron dialects OR them; for a * in one field behaviour is identical)
package cron

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

const searchCap = 1_000_000

type schedule struct {
	minutes map[int]bool
	hours   map[int]bool
	days    map[int]bool
	months  map[int]bool
	dows    map[int]bool
}

func parseField(text string, low, high int) (map[int]bool, error) {
	values := map[int]bool{}
	for _, part := range strings.Split(text, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			return nil, fmt.Errorf("empty cron field part in %q", text)
		}
		step := 1
		if i := strings.Index(part, "/"); i >= 0 {
			s, err := strconv.Atoi(part[i+1:])
			if err != nil || s <= 0 {
				return nil, fmt.Errorf("invalid step in cron field %q", text)
			}
			step = s
			part = part[:i]
		}
		var baseLow, baseHigh int
		switch {
		case part == "" || part == "*":
			baseLow, baseHigh = low, high
		case strings.Contains(part, "-"):
			left, right, ok := strings.Cut(part, "-")
			l, err1 := strconv.Atoi(left)
			r, err2 := strconv.Atoi(right)
			if !ok || err1 != nil || err2 != nil {
				return nil, fmt.Errorf("invalid cron field %q", text)
			}
			baseLow, baseHigh = l, r
		default:
			v, err := strconv.Atoi(part)
			if err != nil {
				return nil, fmt.Errorf("invalid cron field %q", text)
			}
			baseLow, baseHigh = v, v
		}
		for v := baseLow; v <= baseHigh; v += step {
			if low <= v && v <= high {
				values[v] = true
			}
		}
	}
	if len(values) == 0 {
		return nil, fmt.Errorf("invalid cron field %q", text)
	}
	return values, nil
}

// Parse parses a 5-field cron expression.
func Parse(expression string) (*schedule, error) {
	fields := strings.Fields(expression)
	if len(fields) != 5 {
		return nil, fmt.Errorf("cron expression must have 5 fields, got %d: %q", len(fields), expression)
	}
	s := &schedule{}
	var err error
	if s.minutes, err = parseField(fields[0], 0, 59); err != nil {
		return nil, err
	}
	if s.hours, err = parseField(fields[1], 0, 23); err != nil {
		return nil, err
	}
	if s.days, err = parseField(fields[2], 1, 31); err != nil {
		return nil, err
	}
	if s.months, err = parseField(fields[3], 1, 12); err != nil {
		return nil, err
	}
	// Cron DOW 0=Sunday..6=Saturday equals Go's time.Weekday directly, so no
	// conversion (Python's cron.py converts to Python weekday 0=Monday).
	if s.dows, err = parseField(fields[4], 0, 6); err != nil {
		return nil, err
	}
	return s, nil
}

// minValue returns the smallest value in the set (the set is never empty).
func minValue(values map[int]bool) int {
	m := -1
	for v := range values {
		if m == -1 || v < m {
			m = v
		}
	}
	return m
}

// nextInSet returns (smallest value >= current, wrapped) like cron.py.
func nextInSet(values map[int]bool, current int) (int, bool) {
	wrap := minValue(values)
	best := -1
	for v := range values {
		if v >= current && (best == -1 || v < best) {
			best = v
		}
	}
	if best != -1 {
		return best, false
	}
	return wrap, true
}

func jumpDay(t time.Time) time.Time {
	return time.Date(t.Year(), t.Month(), t.Day()+1, 0, 0, 0, 0, t.Location())
}

func jumpMonth(t time.Time) time.Time {
	return time.Date(t.Year(), t.Month()+1, 1, 0, 0, 0, 0, t.Location())
}

// NextAfter returns the first matching datetime strictly after moment, in the
// same location. Mirrors CronSchedule.next_after in cron.py.
func (s *schedule) NextAfter(moment time.Time) (time.Time, error) {
	candidate := time.Date(
		moment.Year(), moment.Month(), moment.Day(), moment.Hour(), moment.Minute(), 0, 0,
		moment.Location(),
	).Add(time.Minute)
	for range searchCap {
		if !s.months[int(candidate.Month())] {
			candidate = jumpMonth(candidate)
			continue
		}
		if !(s.days[candidate.Day()] && s.dows[int(candidate.Weekday())]) {
			candidate = jumpDay(candidate)
			continue
		}
		if !s.hours[candidate.Hour()] {
			hour, wrapped := nextInSet(s.hours, candidate.Hour())
			minute := minValue(s.minutes)
			candidate = time.Date(candidate.Year(), candidate.Month(), candidate.Day(), hour, minute, 0, 0, candidate.Location())
			if wrapped {
				candidate = jumpDay(candidate)
				candidate = time.Date(candidate.Year(), candidate.Month(), candidate.Day(), hour, minute, 0, 0, candidate.Location())
			}
			continue
		}
		if !s.minutes[candidate.Minute()] {
			minute, wrapped := nextInSet(s.minutes, candidate.Minute())
			candidate = time.Date(candidate.Year(), candidate.Month(), candidate.Day(), candidate.Hour(), minute, 0, 0, candidate.Location())
			if wrapped {
				candidate = candidate.Add(time.Hour)
				candidate = time.Date(candidate.Year(), candidate.Month(), candidate.Day(), candidate.Hour(), minute, 0, 0, candidate.Location())
			}
			continue
		}
		return candidate, nil
	}
	return time.Time{}, fmt.Errorf("no matching cron fire within search cap")
}

// NextScheduleFire computes the next cron fire time in the schedule's timezone
// and normalizes it to UTC for storage. Mirrors next_schedule_fire in cron.py.
func NextScheduleFire(expression, timezoneName string, after time.Time) (time.Time, error) {
	loc, err := time.LoadLocation(timezoneName)
	if err != nil {
		return time.Time{}, err
	}
	s, err := Parse(expression)
	if err != nil {
		return time.Time{}, err
	}
	nextLocal, err := s.NextAfter(after.In(loc))
	if err != nil {
		return time.Time{}, err
	}
	return nextLocal.UTC(), nil
}
