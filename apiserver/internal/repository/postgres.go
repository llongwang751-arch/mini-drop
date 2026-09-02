package repository

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"

	"mini-drop/apiserver/internal/errorcode"
	"mini-drop/apiserver/internal/taskstatus"
)

var (
	ErrNotFound            = errors.New("not found")
	ErrConflict            = errors.New("conflict")
	ErrIdempotencyConflict = errors.New("idempotency key replayed with different parameters")
	ErrTargetUnavailable   = errors.New("target process is absent, stale, or ambiguous")
)

var cancellableStatuses = map[string]bool{
	"PENDING": true, "RUNNING": true, "UPLOADING": true, "ANALYZING": true,
}

var deletableStatuses = map[string]bool{
	"DONE": true, "FAILED": true, "CANCELLED": true,
}

type Postgres struct {
	pool *pgxpool.Pool
}

type Page struct {
	Limit     int
	Offset    int
	Search    string
	SortBy    string
	SortOrder string
}

type CreateTask struct {
	Name           string
	AgentID        string
	TargetPID      int
	CollectorType  string
	SampleRate     int
	DurationSec    int
	Options        map[string]any
	CreatorID      string
	IdempotencyKey string
	ProcessBinding ProcessBinding
}

type ProcessBinding struct {
	AgentID            string    `json:"agent_id"`
	PID                int       `json:"pid"`
	BootID             string    `json:"boot_id"`
	ProcessStartTicks  int64     `json:"process_start_ticks"`
	PIDNamespaceInode  int64     `json:"pid_namespace_inode"`
	NamespacePID       int       `json:"namespace_pid"`
	ExecutableIdentity string    `json:"executable_identity"`
	ProcessSnapshotID  string    `json:"process_snapshot_id"`
	SnapshotGeneration int64     `json:"snapshot_generation"`
	SnapshotReceivedAt time.Time `json:"snapshot_received_at"`
}

type Artifact struct {
	ID              int64
	TaskID          string
	ArtifactType    string
	Bucket          string
	ObjectKey       string
	Filename        string
	LocalPath       string
	ContentType     string
	SizeBytes       int64
	SHA256          string
	Manifest        map[string]any
	IntegrityStatus string
	IntegrityReason string
	Metadata        map[string]any
	CreatedAt       time.Time
}

type StatusEvent struct {
	ID            int64          `json:"-"`
	Sequence      int64          `json:"sequence"`
	TaskID        string         `json:"task_id"`
	TaskAttemptID *string        `json:"task_attempt_id"`
	FromStatus    *string        `json:"from_status"`
	ToStatus      string         `json:"to_status"`
	Reason        string         `json:"reason"`
	Actor         string         `json:"actor"`
	Source        string         `json:"source"`
	Metadata      map[string]any `json:"metadata"`
	CreatedAt     time.Time      `json:"created_at"`
}

type AuditEvent struct {
	ID        int64          `json:"-"`
	EventType string         `json:"event_type"`
	Message   string         `json:"message"`
	AgentID   *string        `json:"agent_id"`
	TaskID    *string        `json:"task_id"`
	Metadata  map[string]any `json:"metadata"`
	CreatedAt time.Time      `json:"created_at"`
}

type DropInsightEvent struct {
	Sequence int64          `json:"sequence"`
	Payload  map[string]any `json:"payload"`
}

type ProcessCandidateSnapshot struct {
	SnapshotID    string
	AgentID       string
	State         string
	Authoritative bool
	ReceivedAt    time.Time
	Fresh         bool
	Items         []map[string]any
}

type AgentCapability struct {
	Exists    bool
	Online    bool
	Supported bool
}

type TaskUploadAuthorization struct {
	TaskAttemptID string
	ObjectKey     string
	PutURL        string
	ExpiresAt     time.Time
}

func (p *Postgres) ReplaceTaskUploadAuthorizations(
	ctx context.Context, taskID string, items []TaskUploadAuthorization,
) error {
	if taskID == "" || len(items) == 0 {
		return errors.New("task upload authorizations require a task and at least one object")
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx,
		`DELETE FROM task_upload_authorizations WHERE task_id=$1`, taskID,
	); err != nil {
		return err
	}
	for _, item := range items {
		if item.TaskAttemptID == "" || item.ObjectKey == "" || item.PutURL == "" || !item.ExpiresAt.After(time.Now().UTC()) {
			return errors.New("invalid task upload authorization")
		}
		if _, err := tx.Exec(ctx, `
			INSERT INTO task_upload_authorizations(
				task_id,task_attempt_id,object_key,put_url,expires_at,created_at
			) VALUES($1,$2,$3,$4,$5,$6)`,
			taskID, item.TaskAttemptID, item.ObjectKey, item.PutURL,
			item.ExpiresAt, time.Now().UTC(),
		); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

// RecordControlCommand persists the accepted control-plane intent without
// storing free-form problem text or credentials in the audit log.
func (p *Postgres) RecordControlCommand(
	ctx context.Context, eventType, message string, metadata map[string]any,
) error {
	encoded, err := json.Marshal(metadata)
	if err != nil {
		return err
	}
	_, err = p.pool.Exec(ctx, `
		INSERT INTO audit_logs(event_type,message,metadata,created_at)
		VALUES ($1,$2,$3::jsonb,$4)`,
		eventType, message, string(encoded), time.Now().UTC())
	return err
}

func Open(ctx context.Context, databaseURL string) (*Postgres, error) {
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, fmt.Errorf("create postgres pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping postgres: %w", err)
	}
	return &Postgres{pool: pool}, nil
}

func (p *Postgres) Close() {
	p.pool.Close()
}

func (p *Postgres) Ping(ctx context.Context) error {
	return p.pool.Ping(ctx)
}

func (p *Postgres) CreateTask(ctx context.Context, input CreateTask) (string, bool, error) {
	var agentExists bool
	if err := p.pool.QueryRow(ctx,
		`SELECT EXISTS(SELECT 1 FROM agents WHERE id = $1)`, input.AgentID,
	).Scan(&agentExists); err != nil {
		return "", false, err
	}
	if !agentExists {
		return "", false, ErrNotFound
	}

	now := time.Now().UTC()
	requestParams, err := json.Marshal(map[string]any{
		"name": input.Name, "agent_id": input.AgentID, "target_pid": input.TargetPID,
		"collector_type": input.CollectorType, "sample_rate": input.SampleRate,
		"duration_sec": input.DurationSec, "options": input.Options,
	})
	if err != nil {
		return "", false, err
	}
	processBinding, err := json.Marshal(input.ProcessBinding)
	if err != nil {
		return "", false, err
	}
	if input.ProcessBinding.ProcessSnapshotID == "" ||
		input.ProcessBinding.AgentID != input.AgentID ||
		input.ProcessBinding.PID != input.TargetPID {
		return "", false, ErrTargetUnavailable
	}

	if input.IdempotencyKey != "" {
		replayedID, replay, err := p.resolveIdempotentTask(ctx, input, requestParams)
		if err != nil {
			return "", false, err
		}
		if replay {
			return replayedID, true, nil
		}
	}

	taskID, err := newTaskID()
	if err != nil {
		return "", false, err
	}
	// Empty idempotency key / creator must be stored as NULL, not "", otherwise
	// the (creator_id, idempotency_key) unique index collides on the second
	// task created by the same principal without an Idempotency-Key header.
	var creatorID, idemKey any
	if input.CreatorID != "" {
		creatorID = input.CreatorID
	}
	if input.IdempotencyKey != "" {
		idemKey = input.IdempotencyKey
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return "", false, err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	if _, err := tx.Exec(ctx, `
		INSERT INTO tasks (
			id, name, agent_id, target_pid, collector_type, sample_rate, duration_sec,
			status, status_reason, collection_status, analysis_status, request_params,
			creator_id, idempotency_key, process_snapshot_id, process_binding_json, created_at
		) VALUES ($1,$2,$3,$4,$5,$6,$7,'PENDING',$8,$9,$10,$11::jsonb,$12,$13,$14,$15::json,$16)`,
		taskID, input.Name, input.AgentID, input.TargetPID, input.CollectorType,
		input.SampleRate, input.DurationSec, "Go API 创建任务",
		taskstatus.CollectionQueued, taskstatus.AnalysisPending, string(requestParams),
		creatorID, idemKey, input.ProcessBinding.ProcessSnapshotID,
		string(processBinding), now,
	); err != nil {
		if input.IdempotencyKey != "" && isUniqueViolation(err) {
			// A concurrent replica won the idempotency race; reconcile instead of failing.
			replayedID, replay, reconcileErr := p.resolveIdempotentTask(ctx, input, requestParams)
			if reconcileErr != nil {
				return "", false, reconcileErr
			}
			if replay {
				return replayedID, true, nil
			}
		}
		return "", false, err
	}
	if _, err := tx.Exec(ctx, `
		INSERT INTO task_status_events (
			task_id, from_status, to_status, reason, actor, metadata, created_at
		) VALUES ($1,NULL,'PENDING',$2,'web',$3::jsonb,$4)`,
		taskID, "Go API 创建任务", `{"served_by":"go-apiserver"}`, now,
	); err != nil {
		return "", false, err
	}
	// Transactional outbox: Task + Event + Outbox commit atomically (guide §9.6).
	// A Python dispatcher claims PENDING rows with a lease and publishes the
	// event idempotently, so a crashed API never loses a task-created event.
	if _, err := tx.Exec(ctx, `
		INSERT INTO outbox_messages (
			id, aggregate_type, aggregate_id, event_type, payload_json,
			status, attempts, next_attempt_at, created_at, updated_at
		) VALUES ($1,$2,$3,$4,$5::jsonb,'PENDING',0,$6,$6,$6)
		ON CONFLICT (id) DO NOTHING`,
		"outbox_"+taskID+":task.created",
		"task", taskID, "task.created",
		string(requestParams),
		now,
	); err != nil {
		return "", false, err
	}
	if err := tx.Commit(ctx); err != nil {
		return "", false, err
	}
	return taskID, false, nil
}

// resolveIdempotentTask checks whether (creator, idempotency key) already
// produced a task. It returns (taskID, true, nil) when an identical task was
// already created, ErrIdempotencyConflict when the same key was reused with
// different parameters, and ("", false, nil) when the key is unused.
func (p *Postgres) resolveIdempotentTask(ctx context.Context, input CreateTask, requestParams []byte) (string, bool, error) {
	if input.CreatorID == "" || input.IdempotencyKey == "" {
		return "", false, nil
	}
	var existingID string
	var existingParams []byte
	err := p.pool.QueryRow(ctx,
		`SELECT id, request_params FROM tasks
		 WHERE creator_id = $1 AND idempotency_key = $2`,
		input.CreatorID, input.IdempotencyKey,
	).Scan(&existingID, &existingParams)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, err
	}
	if !sameTaskRequest(existingParams, requestParams) {
		return "", false, ErrIdempotencyConflict
	}
	return existingID, true, nil
}

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) {
		return pgErr.Code == "23505"
	}
	return false
}

// sameTaskRequest reports whether two stored request_params JSON documents
// describe the same CreateTask input. json.Marshal sorts map keys, so
// re-marshaling both documents canonicalizes away key-order differences.
func sameTaskRequest(a, b []byte) bool {
	var am, bm map[string]any
	if json.Unmarshal(a, &am) != nil || json.Unmarshal(b, &bm) != nil {
		return false
	}
	ca, errA := json.Marshal(am)
	cb, errB := json.Marshal(bm)
	if errA != nil || errB != nil {
		return false
	}
	return string(ca) == string(cb)
}

func (p *Postgres) ListAgents(ctx context.Context, page Page) ([]map[string]any, int, error) {
	const countSQL = `SELECT count(*) FROM agents`
	var total int
	if err := p.pool.QueryRow(ctx, countSQL).Scan(&total); err != nil {
		return nil, 0, err
	}
	rows, err := p.pool.Query(ctx, `
		SELECT id, hostname, ip_addr, version, os_info, capabilities, status,
		       last_heartbeat_at, created_at, updated_at
		FROM agents
		ORDER BY created_at DESC
		LIMIT $1 OFFSET $2`, page.Limit, page.Offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	items := make([]map[string]any, 0, page.Limit)
	for rows.Next() {
		var id, hostname, ipAddr, version, osInfo, status string
		var capabilities []byte
		var lastHeartbeat, createdAt, updatedAt time.Time
		if err := rows.Scan(
			&id, &hostname, &ipAddr, &version, &osInfo, &capabilities, &status,
			&lastHeartbeat, &createdAt, &updatedAt,
		); err != nil {
			return nil, 0, err
		}
		items = append(items, map[string]any{
			"id": id, "hostname": hostname, "ip_addr": ipAddr, "version": version,
			"os_info": osInfo, "capabilities": decodeJSON(capabilities, []any{}),
			"status": status, "last_heartbeat_at": lastHeartbeat,
			"created_at": createdAt, "updated_at": updatedAt,
			"latest_metrics": map[string]any{},
		})
	}
	return items, total, rows.Err()
}

func (p *Postgres) AgentSupportsCollector(
	ctx context.Context, agentID, collector string,
) (AgentCapability, error) {
	var status string
	var rawCapabilities []byte
	err := p.pool.QueryRow(ctx, `
		SELECT status,capabilities FROM agents WHERE id=$1`, agentID,
	).Scan(&status, &rawCapabilities)
	if errors.Is(err, pgx.ErrNoRows) {
		return AgentCapability{}, nil
	}
	if err != nil {
		return AgentCapability{}, err
	}
	var capabilities []string
	if err := json.Unmarshal(rawCapabilities, &capabilities); err != nil {
		return AgentCapability{}, fmt.Errorf("decode Agent capabilities: %w", err)
	}
	supported := false
	for _, item := range capabilities {
		if item == collector {
			supported = true
			break
		}
	}
	return AgentCapability{
		Exists: true, Online: status == "ONLINE", Supported: supported,
	}, nil
}

func (p *Postgres) ResolveFreshProcessCandidate(
	ctx context.Context, agentID string, pid int, maxAge time.Duration,
) (ProcessBinding, error) {
	maxAgeSeconds := int(maxAge / time.Second)
	if maxAgeSeconds < 1 {
		maxAgeSeconds = 30
	}
	rows, err := p.pool.Query(ctx, `
		WITH latest AS (
			SELECT id,agent_id,generation,boot_id,received_at,authoritative
			FROM process_candidate_snapshots
			WHERE agent_id=$1
			ORDER BY received_at DESC,id DESC
			LIMIT 1
		)
		SELECT l.agent_id,p.pid,l.boot_id,p.process_start_ticks,
		       p.pid_namespace_inode,p.namespace_pid,p.executable_identity,
		       l.id,l.generation,l.received_at
		FROM latest l
		JOIN process_candidates p ON p.snapshot_id=l.id
		WHERE l.authoritative=true
		  AND l.received_at >= now()-($3::int * interval '1 second')
		  AND p.pid=$2
		ORDER BY p.id
		LIMIT 2`, agentID, pid, maxAgeSeconds)
	if err != nil {
		return ProcessBinding{}, err
	}
	defer rows.Close()
	bindings := make([]ProcessBinding, 0, 2)
	for rows.Next() {
		var binding ProcessBinding
		if err := rows.Scan(
			&binding.AgentID, &binding.PID, &binding.BootID,
			&binding.ProcessStartTicks, &binding.PIDNamespaceInode,
			&binding.NamespacePID, &binding.ExecutableIdentity,
			&binding.ProcessSnapshotID, &binding.SnapshotGeneration,
			&binding.SnapshotReceivedAt,
		); err != nil {
			return ProcessBinding{}, err
		}
		bindings = append(bindings, binding)
	}
	if err := rows.Err(); err != nil {
		return ProcessBinding{}, err
	}
	if len(bindings) != 1 {
		return ProcessBinding{}, ErrTargetUnavailable
	}
	return bindings[0], nil
}

// LatestProcessCandidates projects the newest Agent-attested process snapshot.
// It deliberately reads the latest snapshot even when that snapshot is
// partial: falling back to an older authoritative snapshot could resurrect a
// PID that has already exited or been reused.
func (p *Postgres) LatestProcessCandidates(
	ctx context.Context, agentID string, limit int, maxAge time.Duration,
) (ProcessCandidateSnapshot, error) {
	rows, err := p.pool.Query(ctx, `
		WITH latest AS (
			SELECT id,agent_id,state,authoritative,received_at
			FROM process_candidate_snapshots
			WHERE agent_id=$1
			ORDER BY received_at DESC,id DESC
			LIMIT 1
		)
		SELECT l.id,l.agent_id,l.state,l.authoritative,l.received_at,
		       COALESCE(p.pid,0),COALESCE(p.comm,''),
		       COALESCE(p.service_hint,''),COALESCE(p.instance_hint,''),
		       COALESCE(p.collector_capabilities,'[]'::json)
		FROM latest l
		LEFT JOIN process_candidates p ON p.snapshot_id=l.id
		ORDER BY CASE WHEN COALESCE(p.service_hint,'')<>'' THEN 0 ELSE 1 END,
		         COALESCE(p.comm,''),COALESCE(p.pid,0)
		LIMIT $2`, agentID, limit)
	if err != nil {
		return ProcessCandidateSnapshot{}, err
	}
	defer rows.Close()

	result := ProcessCandidateSnapshot{AgentID: agentID, Items: []map[string]any{}}
	for rows.Next() {
		var pid int
		var comm, serviceHint, instanceHint string
		var capabilities []byte
		if err := rows.Scan(
			&result.SnapshotID, &result.AgentID, &result.State,
			&result.Authoritative, &result.ReceivedAt, &pid, &comm,
			&serviceHint, &instanceHint, &capabilities,
		); err != nil {
			return ProcessCandidateSnapshot{}, err
		}
		if pid > 0 {
			result.Items = append(result.Items, map[string]any{
				"pid": pid, "comm": comm, "service_hint": serviceHint,
				"instance_hint":          instanceHint,
				"collector_capabilities": decodeJSON(capabilities, []any{}),
			})
		}
	}
	if err := rows.Err(); err != nil {
		return ProcessCandidateSnapshot{}, err
	}
	if !result.ReceivedAt.IsZero() {
		age := time.Since(result.ReceivedAt)
		result.Fresh = age >= -5*time.Second && age <= maxAge
	}
	return result, nil
}

func (p *Postgres) ListTasks(ctx context.Context, page Page) ([]map[string]any, int, error) {
	search := strings.TrimSpace(page.Search)
	searchPattern := "%" + search + "%"
	var total int
	if err := p.pool.QueryRow(ctx, `
		SELECT count(*) FROM tasks
		WHERE deleted_at IS NULL AND ($1 = '' OR name ILIKE $2 OR id ILIKE $2)`,
		search, searchPattern,
	).Scan(&total); err != nil {
		return nil, 0, err
	}

	sortColumns := map[string]string{
		"name": "name", "status": "status", "created_at": "created_at",
		"agent_id": "agent_id", "collector_type": "collector_type", "target_pid": "target_pid",
	}
	sortColumn := sortColumns[page.SortBy]
	if sortColumn == "" {
		sortColumn = "created_at"
	}
	order := "DESC"
	if strings.EqualFold(page.SortOrder, "asc") {
		order = "ASC"
	}
	query := `
		SELECT id, name, agent_id, target_pid, collector_type, sample_rate, duration_sec,
		       status, status_reason, collection_status, analysis_status,
		       error_code,error_message,request_params,
		       created_at, started_at, finished_at
		FROM tasks
		WHERE deleted_at IS NULL AND ($1 = '' OR name ILIKE $2 OR id ILIKE $2)
		ORDER BY ` + sortColumn + ` ` + order + `
		LIMIT $3 OFFSET $4`
	rows, err := p.pool.Query(ctx, query, search, searchPattern, page.Limit, page.Offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0, page.Limit)
	for rows.Next() {
		item, err := scanTask(rows.Scan)
		if err != nil {
			return nil, 0, err
		}
		items = append(items, item)
	}
	return items, total, rows.Err()
}

func (p *Postgres) GetTask(ctx context.Context, taskID string) (map[string]any, error) {
	row := p.pool.QueryRow(ctx, `
		SELECT id, name, agent_id, target_pid, collector_type, sample_rate, duration_sec,
		       status, status_reason, collection_status, analysis_status,
		       error_code,error_message,request_params,
		       created_at, started_at, finished_at
		FROM tasks WHERE id = $1 AND deleted_at IS NULL`, taskID)
	item, err := scanTask(row.Scan)
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	return item, nil
}

func (p *Postgres) CancelTask(ctx context.Context, taskID, reason string) (map[string]any, error) {
	reason = strings.TrimSpace(reason)
	if reason == "" {
		reason = "用户在控制台主动停止任务"
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var status, collectionStatus string
	if err := tx.QueryRow(ctx, `
		SELECT status, collection_status FROM tasks
		WHERE id=$1 AND deleted_at IS NULL FOR UPDATE`, taskID,
	).Scan(&status, &collectionStatus); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	if !cancellableStatuses[status] {
		return nil, fmt.Errorf("%w: task status %s is terminal", ErrConflict, status)
	}
	now := time.Now().UTC()
	nextCollection := taskstatus.CollectionCanceled
	nextAnalysis := taskstatus.AnalysisCanceled
	if collectionStatus == taskstatus.CollectionCollected {
		nextCollection = collectionStatus
		nextAnalysis = taskstatus.AnalysisCanceled
	}
	if _, err := tx.Exec(ctx, `
		UPDATE tasks SET status='CANCELLED', status_reason=$2,
		collection_status=$3, analysis_status=$4, error_code=$5,
		error_message=$2, finished_at=$6
		WHERE id=$1`, taskID, reason, nextCollection, nextAnalysis,
		string(errorcode.TaskCanceled), now); err != nil {
		return nil, err
	}
	metadata, _ := json.Marshal(map[string]any{
		"previous_status": status, "served_by": "go-apiserver",
	})
	if _, err := tx.Exec(ctx, `
		INSERT INTO task_status_events
		(task_id,from_status,to_status,reason,actor,metadata,created_at)
		VALUES ($1,$2,'CANCELLED',$3,'web',$4::jsonb,$5)`,
		taskID, status, reason, string(metadata), now); err != nil {
		return nil, err
	}
	if _, err := tx.Exec(ctx, `
		UPDATE task_attempts SET status='CANCELLED', reason=$2, finished_at=$3,
		metadata_json=(COALESCE(metadata_json,'{}'::json)::jsonb || $4::jsonb)::json
		WHERE id=(SELECT id FROM task_attempts WHERE task_id=$1
		ORDER BY attempt_no DESC LIMIT 1)`,
		taskID, reason, now, string(metadata)); err != nil {
		return nil, err
	}
	auditMetadata, _ := json.Marshal(map[string]any{
		"reason": reason, "actor": "web", "served_by": "go-apiserver",
	})
	if _, err := tx.Exec(ctx, `
		INSERT INTO audit_logs(event_type,message,task_id,metadata,created_at)
		VALUES ('TASK_CANCELLED',$2,$1,$3::jsonb,$4)`,
		taskID, "任务 "+taskID+" 已取消", string(auditMetadata), now); err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return map[string]any{
		"task_id": taskID, "status": "CANCELLED", "reason": reason,
		"served_by": "go-apiserver",
	}, nil
}

func (p *Postgres) DeleteTask(ctx context.Context, taskID, reason string) (map[string]any, error) {
	reason = strings.TrimSpace(reason)
	if reason == "" {
		reason = "用户在控制台归档任务"
	}
	tx, err := p.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	var status, name string
	if err := tx.QueryRow(ctx, `
		SELECT status,name FROM tasks
		WHERE id=$1 AND deleted_at IS NULL FOR UPDATE`, taskID,
	).Scan(&status, &name); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	if !deletableStatuses[status] {
		return nil, fmt.Errorf("%w: task status %s is active", ErrConflict, status)
	}
	now := time.Now().UTC()
	if _, err := tx.Exec(ctx, `
		UPDATE tasks SET deleted_at=$2,deleted_by='web',delete_reason=$3 WHERE id=$1`,
		taskID, now, reason); err != nil {
		return nil, err
	}
	metadata, _ := json.Marshal(map[string]any{
		"deletion_mode": "soft", "evidence_retained": true,
		"artifact_retained": true, "served_by": "go-apiserver",
	})
	message := "任务 " + name + " 已归档，审计、产物与 AI 证据继续保留"
	if _, err := tx.Exec(ctx, `
		INSERT INTO audit_logs(event_type,message,task_id,metadata,created_at)
		VALUES ('TASK_ARCHIVED',$2,$1,$3::jsonb,$4)`,
		taskID, message, string(metadata), now); err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return map[string]any{
		"task_id": taskID, "deleted": true, "deletion_mode": "soft",
		"evidence_retained": true, "served_by": "go-apiserver",
	}, nil
}

func (p *Postgres) ListTaskEvents(ctx context.Context, taskID string) ([]StatusEvent, error) {
	var exists bool
	if err := p.pool.QueryRow(ctx,
		`SELECT EXISTS(SELECT 1 FROM tasks WHERE id=$1 AND deleted_at IS NULL)`, taskID,
	).Scan(&exists); err != nil {
		return nil, err
	}
	if !exists {
		return nil, ErrNotFound
	}
	rows, err := p.pool.Query(ctx, `
		SELECT id,task_id,from_status,to_status,reason,actor,metadata,created_at
		FROM task_status_events WHERE task_id=$1 ORDER BY id ASC`, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanStatusEvents(rows)
}

func (p *Postgres) ListTaskAttempts(ctx context.Context, taskID string) ([]map[string]any, error) {
	exists, err := p.taskExists(ctx, taskID)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, ErrNotFound
	}
	rows, err := p.pool.Query(ctx, `
		SELECT id,task_id,attempt_no,agent_id,status,reason,lease_expires_at,
		       metadata_json,created_at,started_at,finished_at
		FROM task_attempts WHERE task_id=$1 ORDER BY attempt_no ASC,id ASC`, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0)
	for rows.Next() {
		var id, itemTaskID, agentID, status string
		var attemptNo int
		var reason *string
		var leaseExpiresAt, startedAt, finishedAt *time.Time
		var metadata []byte
		var createdAt time.Time
		if err := rows.Scan(
			&id, &itemTaskID, &attemptNo, &agentID, &status, &reason,
			&leaseExpiresAt, &metadata, &createdAt, &startedAt, &finishedAt,
		); err != nil {
			return nil, err
		}
		items = append(items, map[string]any{
			"id": id, "task_id": itemTaskID, "attempt_no": attemptNo,
			"agent_id": agentID, "status": status, "reason": reason,
			"lease_expires_at": leaseExpiresAt,
			"metadata":         decodeJSON(metadata, map[string]any{}),
			"created_at":       createdAt, "started_at": startedAt, "finished_at": finishedAt,
		})
	}
	return items, rows.Err()
}

func (p *Postgres) ListTaskArtifacts(ctx context.Context, taskID string) ([]map[string]any, error) {
	exists, err := p.taskExists(ctx, taskID)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, ErrNotFound
	}
	rows, err := p.pool.Query(ctx, `
		SELECT id,task_id,artifact_type,bucket,object_key,filename,local_path,
		       content_type,size_bytes,sha256,manifest_json,integrity_status,
		       integrity_reason,metadata,created_at
		FROM artifacts WHERE task_id=$1 ORDER BY id ASC`, taskID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0)
	for rows.Next() {
		var id int64
		var itemTaskID, artifactType, objectKey string
		var bucket, filename, localPath, contentType, sha256, integrityStatus, integrityReason *string
		var sizeBytes *int64
		var manifest, metadata []byte
		var createdAt time.Time
		if err := rows.Scan(
			&id, &itemTaskID, &artifactType, &bucket, &objectKey, &filename,
			&localPath, &contentType, &sizeBytes, &sha256, &manifest,
			&integrityStatus, &integrityReason, &metadata, &createdAt,
		); err != nil {
			return nil, err
		}
		items = append(items, map[string]any{
			"id": id, "task_id": itemTaskID, "artifact_type": artifactType,
			"bucket": bucket, "object_key": objectKey, "filename": filename,
			"local_path": localPath, "content_type": contentType, "size_bytes": sizeBytes,
			"sha256": sha256, "manifest": decodeJSON(manifest, map[string]any{}),
			"integrity_status": integrityStatus, "integrity_reason": integrityReason,
			"metadata": decodeJSON(metadata, map[string]any{}), "created_at": createdAt,
		})
	}
	return items, rows.Err()
}

func (p *Postgres) GetTaskArtifact(
	ctx context.Context, taskID, artifactType string, windowIndex *int,
) (Artifact, error) {
	items, err := p.ListTaskArtifacts(ctx, taskID)
	if err != nil {
		return Artifact{}, err
	}
	for _, item := range items {
		if stringValue(item["artifact_type"]) != artifactType {
			continue
		}
		metadata, _ := item["metadata"].(map[string]any)
		manifest, _ := item["manifest"].(map[string]any)
		if windowIndex != nil && metadataInt(metadata, "window_index") != *windowIndex {
			continue
		}
		artifact := Artifact{
			ID: int64Value(item["id"]), TaskID: stringValue(item["task_id"]),
			ArtifactType: artifactType, Bucket: stringValue(item["bucket"]),
			ObjectKey: stringValue(item["object_key"]), Filename: stringValue(item["filename"]),
			LocalPath: stringValue(item["local_path"]), ContentType: stringValue(item["content_type"]),
			SizeBytes: int64Value(item["size_bytes"]), Metadata: metadata,
			SHA256: stringValue(item["sha256"]), Manifest: manifest,
			IntegrityStatus: stringValue(item["integrity_status"]),
			IntegrityReason: stringValue(item["integrity_reason"]),
		}
		if created, ok := item["created_at"].(time.Time); ok {
			artifact.CreatedAt = created
		}
		return artifact, nil
	}
	return Artifact{}, ErrNotFound
}

func stringValue(value any) string {
	switch v := value.(type) {
	case string:
		return v
	case *string:
		if v != nil {
			return *v
		}
	}
	return ""
}

func int64Value(value any) int64 {
	switch v := value.(type) {
	case int64:
		return v
	case *int64:
		if v != nil {
			return *v
		}
	case int:
		return int64(v)
	case float64:
		return int64(v)
	}
	return 0
}

func metadataInt(metadata map[string]any, key string) int {
	if metadata == nil {
		return 0
	}
	switch v := metadata[key].(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		return int(v)
	case json.Number:
		n, _ := strconv.Atoi(v.String())
		return n
	case string:
		n, _ := strconv.Atoi(v)
		return n
	}
	return 0
}

func (p *Postgres) taskExists(ctx context.Context, taskID string) (bool, error) {
	var exists bool
	err := p.pool.QueryRow(ctx, `
		SELECT EXISTS(SELECT 1 FROM tasks WHERE id=$1 AND deleted_at IS NULL)`, taskID,
	).Scan(&exists)
	return exists, err
}

func (p *Postgres) ListStatusEventsAfter(
	ctx context.Context, afterID int64, limit int,
) ([]StatusEvent, error) {
	rows, err := p.pool.Query(ctx, `
		SELECT id,task_id,from_status,to_status,reason,actor,metadata,created_at
		FROM task_status_events WHERE id>$1 ORDER BY id ASC LIMIT $2`, afterID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanStatusEvents(rows)
}

func (p *Postgres) LatestStatusEventID(ctx context.Context) (int64, error) {
	var id int64
	err := p.pool.QueryRow(ctx, `SELECT COALESCE(MAX(id),0) FROM task_status_events`).Scan(&id)
	return id, err
}

func (p *Postgres) ListAuditEventsAfter(
	ctx context.Context, afterID int64, limit int,
) ([]AuditEvent, error) {
	rows, err := p.pool.Query(ctx, `
		SELECT id,event_type,message,agent_id,task_id,metadata,created_at
		FROM audit_logs WHERE id>$1 ORDER BY id ASC LIMIT $2`, afterID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]AuditEvent, 0)
	for rows.Next() {
		var item AuditEvent
		var metadata []byte
		if err := rows.Scan(
			&item.ID, &item.EventType, &item.Message, &item.AgentID,
			&item.TaskID, &metadata, &item.CreatedAt,
		); err != nil {
			return nil, err
		}
		decoded := decodeJSON(metadata, map[string]any{})
		item.Metadata, _ = decoded.(map[string]any)
		items = append(items, item)
	}
	return items, rows.Err()
}

func (p *Postgres) LatestAuditEventID(ctx context.Context) (int64, error) {
	var id int64
	err := p.pool.QueryRow(ctx, `SELECT COALESCE(MAX(id),0) FROM audit_logs`).Scan(&id)
	return id, err
}

func (p *Postgres) ListDropInsightEventsAfter(
	ctx context.Context, diagnosisID string, afterSequence int64, limit int,
) ([]DropInsightEvent, error) {
	rows, err := p.pool.Query(ctx, `
		SELECT id,sequence,event_type,actor,payload_json,occurred_at
		FROM drop_insight_events
		WHERE diagnosis_id=$1 AND sequence>$2
		ORDER BY sequence ASC LIMIT $3`, diagnosisID, afterSequence, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]DropInsightEvent, 0)
	for rows.Next() {
		var id, eventType, actor string
		var sequence int64
		var raw []byte
		var occurredAt time.Time
		if err := rows.Scan(&id, &sequence, &eventType, &actor, &raw, &occurredAt); err != nil {
			return nil, err
		}
		decoded := decodeJSON(raw, map[string]any{})
		payload, _ := decoded.(map[string]any)
		items = append(items, DropInsightEvent{
			Sequence: sequence,
			Payload: map[string]any{
				"event_id": id, "diagnosis_id": diagnosisID, "sequence": sequence,
				"event_type": eventType, "actor": actor, "payload": payload,
				"occurred_at": occurredAt,
			},
		})
	}
	return items, rows.Err()
}

func scanStatusEvents(rows pgx.Rows) ([]StatusEvent, error) {
	items := make([]StatusEvent, 0)
	for rows.Next() {
		var item StatusEvent
		var metadata []byte
		if err := rows.Scan(
			&item.ID, &item.TaskID, &item.FromStatus, &item.ToStatus,
			&item.Reason, &item.Actor, &metadata, &item.CreatedAt,
		); err != nil {
			return nil, err
		}
		decoded := decodeJSON(metadata, map[string]any{})
		item.Metadata, _ = decoded.(map[string]any)
		item.Sequence = item.ID
		item.Source = item.Actor
		if value, ok := item.Metadata["task_attempt_id"].(string); ok && value != "" {
			item.TaskAttemptID = &value
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func (p *Postgres) ListAuditLogs(ctx context.Context, page Page) ([]map[string]any, int, error) {
	var total int
	if err := p.pool.QueryRow(ctx, `SELECT count(*) FROM audit_logs`).Scan(&total); err != nil {
		return nil, 0, err
	}
	rows, err := p.pool.Query(ctx, `
		SELECT event_type,message,agent_id,task_id,metadata,created_at
		FROM audit_logs ORDER BY created_at DESC,id DESC LIMIT $1 OFFSET $2`,
		page.Limit, page.Offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0, page.Limit)
	for rows.Next() {
		var eventType, message string
		var agentID, taskID *string
		var metadata []byte
		var createdAt time.Time
		if err := rows.Scan(&eventType, &message, &agentID, &taskID, &metadata, &createdAt); err != nil {
			return nil, 0, err
		}
		items = append(items, map[string]any{
			"event_type": eventType, "message": message, "agent_id": agentID,
			"task_id": taskID, "metadata": decodeJSON(metadata, map[string]any{}),
			"created_at": createdAt,
		})
	}
	return items, total, rows.Err()
}

type scanner func(dest ...any) error

func scanTask(scan scanner) (map[string]any, error) {
	var id, name, agentID, collectorType, status, reason, collectionStatus, analysisStatus string
	var targetPID, sampleRate, durationSec int
	var requestParams []byte
	var errorCode, errorMessage *string
	var createdAt time.Time
	var startedAt, finishedAt *time.Time
	if err := scan(
		&id, &name, &agentID, &targetPID, &collectorType, &sampleRate, &durationSec,
		&status, &reason, &collectionStatus, &analysisStatus,
		&errorCode, &errorMessage, &requestParams,
		&createdAt, &startedAt, &finishedAt,
	); err != nil {
		return nil, err
	}
	return map[string]any{
		"id": id, "name": name, "agent_id": agentID, "target_pid": targetPID,
		"collector_type": collectorType, "sample_rate": sampleRate, "duration_sec": durationSec,
		"status": status, "status_reason": reason, "collection_status": collectionStatus,
		"analysis_status": analysisStatus, "error_code": errorCode,
		"error_message":  errorMessage,
		"request_params": decodeJSON(requestParams, map[string]any{}),
		"created_at":     createdAt, "started_at": startedAt, "finished_at": finishedAt,
	}, nil
}

func decodeJSON(raw []byte, fallback any) any {
	if len(raw) == 0 {
		return fallback
	}
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return fallback
	}
	return value
}

func newTaskID() (string, error) {
	var suffix [4]byte
	if _, err := rand.Read(suffix[:]); err != nil {
		return "", fmt.Errorf("generate task id: %w", err)
	}
	return fmt.Sprintf(
		"task_%s_%x", time.Now().UTC().Format("20060102_150405"), suffix,
	), nil
}

func ParsePage(limitRaw, offsetRaw string) (int, int) {
	limit, _ := strconv.Atoi(limitRaw)
	offset, _ := strconv.Atoi(offsetRaw)
	if limit < 1 {
		limit = 1000
	}
	if limit > 1000 {
		limit = 1000
	}
	if offset < 0 {
		offset = 0
	}
	return limit, offset
}
