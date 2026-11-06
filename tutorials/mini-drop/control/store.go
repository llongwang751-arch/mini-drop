package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"
)

const maxJournalBytes = 8 << 20
const maxTasks = 1000

type event struct {
	Kind    string `json:"kind"`
	Attempt int    `json:"attempt"`
	At      string `json:"at"`
}

type task struct {
	ID         string          `json:"id"`
	Attempt    int             `json:"attempt"`
	Status     string          `json:"status"`
	Iterations int             `json:"iterations"`
	CreatedAt  string          `json:"created_at"`
	UpdatedAt  string          `json:"updated_at"`
	Result     json.RawMessage `json:"result,omitempty"`
	ErrorCode  string          `json:"error_code,omitempty"`
	Events     []event         `json:"events"`
}

type keyRecord struct {
	Action     string `json:"action"`
	TaskID     string `json:"task_id"`
	Iterations int    `json:"iterations"`
}

type journal struct {
	SchemaVersion int                  `json:"schema_version"`
	NextID        int64                `json:"next_id"`
	Tasks         map[string]*task     `json:"tasks"`
	Keys          map[string]keyRecord `json:"keys"`
}

type work struct {
	ID      string
	Attempt int
}
type apiError struct {
	Status int
	Code   string
}

func (e *apiError) Error() string { return e.Code }

var errStale = errors.New("STALE_ATTEMPT")

type store struct {
	mu    sync.Mutex
	root  string
	state journal
	queue chan work
}

func stamp() string { return time.Now().UTC().Format(time.RFC3339Nano) }
func addEvent(t *task, kind string) {
	t.UpdatedAt = stamp()
	t.Events = append(t.Events, event{kind, t.Attempt, t.UpdatedAt})
}

func atomicWrite(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".mini-drop-*.tmp")
	if err != nil {
		return err
	}
	temporary := f.Name()
	defer os.Remove(temporary) // Only this call's temporary file, never a directory.
	if err = f.Chmod(0600); err == nil {
		_, err = f.Write(data)
	}
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return err
	}
	return os.Rename(temporary, path)
}

func newStore(root string) (*store, error) {
	if !filepath.IsAbs(root) {
		return nil, errors.New("WORKDIR_MUST_BE_ABSOLUTE")
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	info, err := os.Lstat(root)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("INVALID_WORKDIR")
	}
	s := &store{root: root, queue: make(chan work, 100), state: journal{1, 1, map[string]*task{}, map[string]keyRecord{}}}
	f, err := os.Open(filepath.Join(root, "journal.json"))
	if errors.Is(err, os.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()
	data, err := io.ReadAll(io.LimitReader(f, maxJournalBytes+1))
	if err != nil || len(data) > maxJournalBytes {
		return nil, errors.New("INVALID_JOURNAL")
	}
	if err = json.Unmarshal(data, &s.state); err != nil || s.state.SchemaVersion != 1 || s.state.NextID < 1 || s.state.Tasks == nil || s.state.Keys == nil || len(s.state.Tasks) > maxTasks || len(s.state.Keys) > 5000 {
		return nil, errors.New("INVALID_JOURNAL")
	}
	queued := 0
	for id, t := range s.state.Tasks {
		n, parseErr := strconv.ParseInt(id, 10, 64)
		if t == nil || parseErr != nil || n <= 0 || n >= s.state.NextID || t.ID != id || t.Attempt < 1 || t.Attempt > 100 || t.Iterations < 1000 || t.Iterations > 10000000 || len(t.Events) > 400 {
			return nil, errors.New("INVALID_JOURNAL_TASK")
		}
		switch t.Status {
		case "QUEUED":
			queued++
		case "RUNNING", "SUCCESS", "FAILED":
		default:
			return nil, errors.New("INVALID_JOURNAL_STATUS")
		}
	}
	if queued > cap(s.queue) {
		return nil, errors.New("JOURNAL_QUEUE_LIMIT")
	}
	return s, nil
}

// commitLocked publishes memory only AFTER the complete new journal is durable.
// This is a single-process teaching journal, not a multi-process database.
func (s *store) commitLocked(change func(*journal) error) error {
	data, err := json.Marshal(s.state)
	if err != nil {
		return err
	}
	var next journal
	if err = json.Unmarshal(data, &next); err != nil {
		return err
	}
	if err = change(&next); err != nil {
		return err
	}
	data, err = json.Marshal(next)
	if err != nil || len(data) > maxJournalBytes {
		return errors.New("JOURNAL_SIZE_LIMIT")
	}
	if err = atomicWrite(filepath.Join(s.root, "journal.json"), data); err != nil {
		return err
	}
	s.state = next
	return nil
}

func (s *store) get(id string) *task {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.state.Tasks[id] // Existing snapshots are never mutated after commit.
}
func (s *store) list() []*task {
	s.mu.Lock()
	defer s.mu.Unlock()
	items := make([]*task, 0, len(s.state.Tasks))
	for _, t := range s.state.Tasks {
		items = append(items, t)
	}
	sort.Slice(items, func(i, j int) bool {
		a, _ := strconv.ParseInt(items[i].ID, 10, 64)
		b, _ := strconv.ParseInt(items[j].ID, 10, 64)
		return a > b
	})
	return items
}

func (s *store) create(iterations int, key string) (*task, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if prior, ok := s.state.Keys[key]; ok {
		if prior.Action != "create" || prior.Iterations != iterations {
			return nil, false, &apiError{409, "IDEMPOTENCY_CONFLICT"}
		}
		return s.state.Tasks[prior.TaskID], true, nil
	}
	if len(s.queue) == cap(s.queue) {
		return nil, false, &apiError{429, "QUEUE_FULL"}
	}
	if len(s.state.Tasks) >= maxTasks || len(s.state.Keys) >= 5000 {
		return nil, false, &apiError{429, "JOURNAL_CAPACITY"}
	}
	id := strconv.FormatInt(s.state.NextID, 10)
	err := s.commitLocked(func(next *journal) error {
		now := stamp()
		t := &task{ID: id, Attempt: 1, Status: "QUEUED", Iterations: iterations, CreatedAt: now, UpdatedAt: now, Events: []event{}}
		addEvent(t, "TASK_QUEUED")
		next.Tasks[id] = t
		next.NextID++
		next.Keys[key] = keyRecord{"create", id, iterations}
		return nil
	})
	if err != nil {
		return nil, false, err
	}
	s.queue <- work{id, 1}
	return s.state.Tasks[id], false, nil
}

func (s *store) retry(id, key string) (*task, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	t := s.state.Tasks[id]
	if t == nil {
		return nil, false, &apiError{404, "NOT_FOUND"}
	}
	if prior, ok := s.state.Keys[key]; ok {
		if prior.Action != "retry" || prior.TaskID != id {
			return nil, false, &apiError{409, "IDEMPOTENCY_CONFLICT"}
		}
		return t, true, nil
	}
	if t.Status != "SUCCESS" && t.Status != "FAILED" {
		return nil, false, &apiError{409, "INVALID_STATE"}
	}
	if t.Attempt >= 100 || len(s.state.Keys) >= 5000 {
		return nil, false, &apiError{429, "JOURNAL_CAPACITY"}
	}
	if len(s.queue) == cap(s.queue) {
		return nil, false, &apiError{429, "QUEUE_FULL"}
	}
	err := s.commitLocked(func(next *journal) error {
		current := next.Tasks[id]
		current.Attempt++
		current.Status = "QUEUED"
		current.Result = nil
		current.ErrorCode = ""
		addEvent(current, "TASK_RETRIED")
		next.Keys[key] = keyRecord{"retry", id, current.Iterations}
		return nil
	})
	if err != nil {
		return nil, false, err
	}
	t = s.state.Tasks[id]
	s.queue <- work{id, t.Attempt}
	return t, false, nil
}

func (s *store) begin(job work) (*task, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	err := s.commitLocked(func(next *journal) error {
		t := next.Tasks[job.ID]
		if t == nil || t.Attempt != job.Attempt || t.Status != "QUEUED" {
			return errStale
		}
		t.Status = "RUNNING"
		addEvent(t, "TASK_RUNNING")
		return nil
	})
	if err != nil {
		return nil, err
	}
	return s.state.Tasks[job.ID], nil
}

func (s *store) finish(job work, result json.RawMessage, code string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.commitLocked(func(next *journal) error {
		t := next.Tasks[job.ID]
		if t == nil || t.Attempt != job.Attempt || t.Status != "RUNNING" {
			return errStale
		}
		t.Result = result
		t.ErrorCode = code
		if code != "" {
			t.Status = "FAILED"
			t.Result = nil
			addEvent(t, "TASK_FAILED")
		} else {
			t.Status = "SUCCESS"
			addEvent(t, "TASK_SUCCEEDED")
		}
		return nil
	})
}

func (s *store) recover() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.commitLocked(func(next *journal) error {
		for _, t := range next.Tasks {
			if t.Status == "RUNNING" {
				t.Status = "FAILED"
				t.ErrorCode = "INTERRUPTED_RESTART"
				t.Result = nil
				addEvent(t, "TASK_INTERRUPTED")
			}
		}
		return nil
	}); err != nil {
		return err
	}
	for _, t := range s.state.Tasks {
		if t.Status == "QUEUED" {
			s.queue <- work{t.ID, t.Attempt}
		}
	}
	return nil
}

func inputDigest(raw []byte) string {
	digest := sha256.Sum256(raw)
	return hex.EncodeToString(digest[:])
}
func (s *store) attemptDir(job work) string {
	return filepath.Join(s.root, "tasks", job.ID, fmt.Sprintf("attempt-%d", job.Attempt))
}
