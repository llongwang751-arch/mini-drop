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
)

var (
	ErrNotFound            = errors.New("not found")
	ErrConflict            = errors.New("conflict")
	ErrIdempotencyConflict = errors.New("idempotency key replayed with different parameters")
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
	ID         int64          `json:"-"`
	TaskID     string         `json:"task_id"`
	FromStatus *string        `json:"from_status"`
	ToStatus   string         `json:"to_status"`
	Reason     string         `json:"reason"`
	Actor      string         `json:"actor"`
	Metadata   map[string]any `json:"metadata"`
	CreatedAt  time.Time      `json:"created_at"`
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

type DiagnosisEvent struct {
	ID          int64          `json:"-"`
	DiagnosisID string         `json:"diagnosis_id"`
	EventType   string         `json:"event_type"`
	FromStatus  *string        `json:"from_status"`
	ToStatus    *string        `json:"to_status"`
	Payload     map[string]any `json:"payload"`
	CreatedAt   time.Time      `json:"created_at"`
}

type DiagnosticCase struct {
	CaseID             string         `json:"case_id"`
	DiagnosisID        string         `json:"diagnosis_id"`
	Source             string         `json:"source"`
	Strategy           string         `json:"strategy"`
	Query              string         `json:"query"`
	Status             string         `json:"status"`
	CanonicalStatus    string         `json:"canonical_status"`
	Target             map[string]any `json:"target"`
	TimeRange          map[string]any `json:"time_range"`
	Budget             map[string]any `json:"budget"`
	HypothesisCount    int            `json:"hypothesis_count"`
	EvidenceCount      int            `json:"evidence_count"`
	ReportVersionCount int            `json:"report_version_count"`
	TaskIDs            []any          `json:"task_ids"`
	CreatedAt          time.Time      `json:"created_at"`
	UpdatedAt          time.Time      `json:"updated_at"`
	LegacyLinks        map[string]any `json:"legacy_links"`
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

func (p *Postgres) ListDiagnosisSessions(
	ctx context.Context, page Page,
) ([]map[string]any, int, error) {
	var total int
	if err := p.pool.QueryRow(ctx, `SELECT count(*) FROM diagnosis_sessions`).Scan(&total); err != nil {
		return nil, 0, err
	}
	rows, err := p.pool.Query(ctx, `
		SELECT id,case_id,creator_id,raw_query,normalized_intent_json,target_scope_json,
		       requested_time_range_json,effective_time_range_json,topology_snapshot_id,
		       baseline_snapshot_id,status,policy_profile,risk_budget_json,
		       resource_budget_json,budget_used_json,hypothesis_graph_json,
		       child_task_ids_json,conclusion_versions_json,
		       model_version,planner_version,lease_owner,lease_until,row_version,
		       deadline_at,created_at,updated_at
		FROM diagnosis_sessions ORDER BY created_at DESC LIMIT $1 OFFSET $2`,
		page.Limit, page.Offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0, page.Limit)
	for rows.Next() {
		item, scanErr := scanDiagnosisSession(rows.Scan)
		if scanErr != nil {
			return nil, 0, scanErr
		}
		items = append(items, item)
	}
	return items, total, rows.Err()
}

func (p *Postgres) GetDiagnosticCase(ctx context.Context, caseID string) (map[string]any, error) {
	session, err := p.getDiagnosisSession(ctx, caseID)
	if err == nil {
		return p.getClusterDiagnosticCase(ctx, session)
	}
	if !errors.Is(err, ErrNotFound) {
		return nil, err
	}
	insight, err := p.getInsightDiagnosticCase(ctx, caseID)
	if err == nil {
		return insight, nil
	}
	if !errors.Is(err, ErrNotFound) {
		return nil, err
	}
	return p.getLegacyDiagnosticCase(ctx, caseID)
}

func (p *Postgres) getDiagnosisSession(ctx context.Context, diagnosisID string) (map[string]any, error) {
	row := p.pool.QueryRow(ctx, `
		SELECT id,case_id,creator_id,raw_query,normalized_intent_json,target_scope_json,
		       requested_time_range_json,effective_time_range_json,topology_snapshot_id,
		       baseline_snapshot_id,status,policy_profile,risk_budget_json,
		       resource_budget_json,budget_used_json,hypothesis_graph_json,
		       child_task_ids_json,conclusion_versions_json,
		       model_version,planner_version,lease_owner,lease_until,row_version,
		       deadline_at,created_at,updated_at
		FROM diagnosis_sessions WHERE id=$1`, diagnosisID)
	item, err := scanDiagnosisSession(row.Scan)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	return item, err
}

func (p *Postgres) getClusterDiagnosticCase(
	ctx context.Context, session map[string]any,
) (map[string]any, error) {
	id, _ := session["diagnosis_id"].(string)
	events, err := p.queryJSONRows(ctx, `
		SELECT jsonb_build_object(
		  'id',id,'diagnosis_id',diagnosis_id,'event_type',event_type,
		  'from_status',from_status,'to_status',to_status,'payload',COALESCE(payload_json,'{}'::json),
		  'created_at',created_at)
		FROM diagnosis_events WHERE diagnosis_id=$1 ORDER BY id ASC`, id)
	if err != nil {
		return nil, err
	}
	probes, err := p.queryJSONRows(ctx, `
		SELECT jsonb_build_object(
		  'step_id',id,'diagnosis_id',diagnosis_id,'probe_id',probe_id,
		  'target',COALESCE(target_json,'{}'::json),'parameters',COALESCE(parameters_json,'{}'::json),
		  'reason',reason,'risk_level',risk_level,'status',status,
		  'requires_approval',(requires_approval<>0),'evidence_purpose',evidence_purpose,
		  'round_index',round_index,'task_id',task_id,'approved_by',approved_by,
		  'approved_at',approved_at,'created_at',created_at,'updated_at',updated_at,
		  'retry_count',retry_count,'error_code',error_code,'error_message',error_message)
		FROM diagnosis_probe_executions WHERE diagnosis_id=$1 ORDER BY created_at ASC`, id)
	if err != nil {
		return nil, err
	}
	evidence, err := p.queryJSONRows(ctx, `
		SELECT jsonb_build_object(
		  'evidence_id',id,'diagnosis_id',diagnosis_id,'source_type',source_type,
		  'source_system',source_system,'evidence_role',evidence_role,
		  'target',COALESCE(target_json,'{}'::json),'event_time_range',COALESCE(event_time_range_json,'{}'::json),
		  'ingestion_time',ingestion_time,'query_or_probe',query_or_probe,
		  'raw_artifact_ref',raw_artifact_ref,'derived_artifact_ref',derived_artifact_ref,
		  'derivation_version',derivation_version,'observed_value',COALESCE(observed_value_json,'{}'::json),
		  'baseline_value',COALESCE(baseline_value_json,'{}'::json),'anomaly_score',COALESCE(anomaly_score_json,'{}'::json),
		  'data_quality',COALESCE(data_quality_json,'{}'::json),'integrity_hash',integrity_hash,
		  'claim_links',COALESCE(claim_links_json,'[]'::json))
		FROM diagnosis_evidence WHERE diagnosis_id=$1 ORDER BY ingestion_time ASC`, id)
	if err != nil {
		return nil, err
	}
	snapshots, err := p.queryJSONRows(ctx, `
		SELECT jsonb_build_object(
		  'snapshot_id',id,'diagnosis_id',diagnosis_id,'round_index',round_index,
		  'evidence_role',evidence_role,'captured_at',captured_at,
		  'time_range',COALESCE(time_range_json,'{}'::json),'target',COALESCE(target_json,'{}'::json),
		  'workload_identity',COALESCE(workload_identity_json,'{}'::json),
		  'deployment_version',deployment_version,'host_fingerprint',COALESCE(host_fingerprint_json,'{}'::json),
		  'collector',collector,'collector_version',collector_version,'task_id',task_id,
		  'attempt_id',attempt_id,'task_attempt_id',attempt_id,'evidence_refs',COALESCE(evidence_refs_json,'[]'::json),
		  'artifact_refs',COALESCE(artifact_refs_json,'[]'::json),'baseline_ref',baseline_ref,
		  'quality',COALESCE(quality_json,'{}'::json),'integrity_hash',integrity_hash,'created_at',created_at)
		FROM diagnosis_evidence_snapshots WHERE diagnosis_id=$1 ORDER BY captured_at ASC`, id)
	if err != nil {
		return nil, err
	}
	pipeline, err := p.queryJSONRows(ctx, `
		SELECT jsonb_build_object(
		  'node_run_id',id,'diagnosis_id',diagnosis_id,'node_name',node_name,
		  'sequence',sequence,'status',status,'attempt',attempt,
		  'input_refs',COALESCE(input_refs_json,'[]'::json),'output_refs',COALESCE(output_refs_json,'[]'::json),
		  'metrics',COALESCE(metrics_json,'{}'::json),'error_code',error_code,'error_message',error_message,
		  'implementation_version',implementation_version,'started_at',started_at,
		  'finished_at',finished_at,'updated_at',updated_at)
		FROM diagnosis_node_runs WHERE diagnosis_id=$1 ORDER BY sequence ASC`, id)
	if err != nil {
		return nil, err
	}

	native := cloneMap(session)
	native["events"] = events
	native["probes"] = probes
	native["coverage"] = buildCoverage(probes)
	native["evidence"] = evidence
	native["evidence_snapshots"] = snapshots
	native["pipeline_nodes"] = pipeline
	native["latest_conclusion"] = latestItem(session["conclusion_versions"])
	native["topology_snapshot"] = nil
	if snapshotID := stringValue(session["topology_snapshot_id"]); snapshotID != "" {
		topology, topologyErr := p.queryJSONObject(ctx, `
			SELECT jsonb_build_object(
			  'snapshot_id',id,'effective_at',effective_at,'generated_at',generated_at,
			  'nodes',COALESCE(nodes_json,'[]'::json),'edges',COALESCE(edges_json,'[]'::json),
			  'source_versions',COALESCE(source_versions_json,'{}'::json),
			  'confidence_summary',COALESCE(confidence_summary_json,'{}'::json))
			FROM topology_snapshots WHERE id=$1`, snapshotID)
		if topologyErr != nil && !errors.Is(topologyErr, ErrNotFound) {
			return nil, topologyErr
		}
		if topologyErr == nil {
			native["topology_snapshot"] = topology
		}
	}
	caseValue := clusterCaseFromSession(session, len(evidence))
	result := diagnosticCaseMap(caseValue)
	result["native_payload"] = native
	return result, nil
}

func (p *Postgres) getInsightDiagnosticCase(ctx context.Context, diagnosisID string) (map[string]any, error) {
	row := p.pool.QueryRow(ctx, `
		SELECT id,query,target_json,time_range_json,mode,budget_json,status,version,
		       clarification_questions_json,created_at,updated_at,
		       (SELECT count(*) FROM drop_insight_hypotheses h WHERE h.diagnosis_id=s.id),
		       (SELECT count(*) FROM drop_insight_evidence e WHERE e.diagnosis_id=s.id),
		       (SELECT count(*) FROM drop_insight_reports r WHERE r.diagnosis_id=s.id)
		FROM drop_insight_sessions s WHERE id=$1`, diagnosisID)
	var id, query, mode, status string
	var target, timeRange, budget, questions []byte
	var version, hypothesisCount, evidenceCount, reportCount int
	var createdAt, updatedAt time.Time
	if err := row.Scan(
		&id, &query, &target, &timeRange, &mode, &budget, &status, &version,
		&questions, &createdAt, &updatedAt, &hypothesisCount, &evidenceCount, &reportCount,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	native := map[string]any{
		"diagnosis_id": id, "query": query, "target": jsonMap(target),
		"time_range": jsonMap(timeRange), "mode": mode, "budget": jsonMap(budget),
		"status": status, "version": version, "clarification_questions": jsonArray(questions),
		"created_at": createdAt, "updated_at": updatedAt,
	}
	caseValue := DiagnosticCase{
		CaseID: id, DiagnosisID: id, Source: "drop_insight_v2", Strategy: "EVIDENCE_HYPOTHESIS",
		Query: query, Status: status, CanonicalStatus: canonicalDiagnosisStatus(status),
		Target: jsonMap(target), TimeRange: jsonMap(timeRange),
		Budget: jsonMap(budget), HypothesisCount: hypothesisCount, EvidenceCount: evidenceCount,
		ReportVersionCount: reportCount, TaskIDs: []any{}, CreatedAt: createdAt, UpdatedAt: updatedAt,
		LegacyLinks: map[string]any{
			"detail": "/api/v2/diagnoses/" + id, "events": "/api/v2/diagnoses/" + id + "/events",
		},
	}
	result := diagnosticCaseMap(caseValue)
	result["native_payload"] = native
	return result, nil
}

func (p *Postgres) getLegacyDiagnosticCase(ctx context.Context, diagnosisID string) (map[string]any, error) {
	row := p.pool.QueryRow(ctx, `
		SELECT r.id,r.task_id,r.status,r.model_name,COALESCE(r.summary,''),
		       (r.validated<>0),r.retry_count,r.created_at,r.finished_at,
		       (SELECT count(*) FROM diagnosis_tool_results t WHERE t.diagnosis_id=r.id),
		       (SELECT count(*) FROM diagnosis_reports p WHERE p.diagnosis_id=r.id)
		FROM diagnosis_runs r WHERE r.id=$1`, diagnosisID)
	var id, taskID, status, modelName, summary string
	var validated bool
	var retryCount, evidenceCount, reportCount int
	var createdAt time.Time
	var finishedAt *time.Time
	if err := row.Scan(
		&id, &taskID, &status, &modelName, &summary, &validated, &retryCount,
		&createdAt, &finishedAt, &evidenceCount, &reportCount,
	); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	updatedAt := createdAt
	if finishedAt != nil {
		updatedAt = *finishedAt
	}
	toolResults, err := p.queryJSONRows(ctx, `
		SELECT jsonb_build_object(
		  'tool_name',tool_name,'status',status,'evidence_ref',evidence_ref,
		  'input',COALESCE(input_json,'{}'::json),'output',COALESCE(output_json,'{}'::json),
		  'error_message',error_message,'created_at',created_at)
		FROM diagnosis_tool_results WHERE diagnosis_id=$1 ORDER BY id ASC`, id)
	if err != nil {
		return nil, err
	}
	reports, err := p.queryJSONRows(ctx, `
		SELECT jsonb_build_object(
		  'id',id,'diagnosis_id',diagnosis_id,'report',COALESCE(report_json,'{}'::json),
		  'ranked_causes',COALESCE(ranked_causes_json,'[]'::json),
		  'confidence',confidence,'not_enough_evidence',(not_enough_evidence<>0),
		  'created_at',created_at)
		FROM diagnosis_reports WHERE diagnosis_id=$1 ORDER BY created_at ASC`, id)
	if err != nil {
		return nil, err
	}
	caseValue := legacyDiagnosticCase(
		id, taskID, status, summary, evidenceCount, reportCount, createdAt, updatedAt,
	)
	result := diagnosticCaseMap(caseValue)
	result["native_payload"] = map[string]any{
		"run": map[string]any{
			"id": id, "task_id": taskID, "status": status, "model_name": modelName,
			"summary": summary, "validated": validated, "retry_count": retryCount,
			"created_at": createdAt, "finished_at": finishedAt,
		},
		"tool_results": toolResults,
		"reports":      reports,
	}
	return result, nil
}

func (p *Postgres) ListContinuousDiagnosisTriggers(
	ctx context.Context, page Page,
) ([]map[string]any, int, error) {
	var total int
	if err := p.pool.QueryRow(ctx, `SELECT count(*) FROM continuous_diagnosis_triggers`).Scan(&total); err != nil {
		return nil, 0, err
	}
	rows, err := p.pool.Query(ctx, `
		SELECT id,task_id,artifact_id,detector_version,status,score_json,
		       diagnosis_id,error_message,created_at,updated_at
		FROM continuous_diagnosis_triggers
		ORDER BY created_at DESC LIMIT $1 OFFSET $2`, page.Limit, page.Offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0, page.Limit)
	for rows.Next() {
		var id, taskID, detectorVersion, status string
		var artifactID int64
		var score []byte
		var diagnosisID, errorMessage *string
		var createdAt, updatedAt time.Time
		if err := rows.Scan(
			&id, &taskID, &artifactID, &detectorVersion, &status, &score,
			&diagnosisID, &errorMessage, &createdAt, &updatedAt,
		); err != nil {
			return nil, 0, err
		}
		items = append(items, map[string]any{
			"trigger_id": id, "task_id": taskID, "artifact_id": artifactID,
			"detector_version": detectorVersion, "status": status,
			"score": decodeJSON(score, map[string]any{}), "diagnosis_id": diagnosisID,
			"error_message": errorMessage, "created_at": createdAt, "updated_at": updatedAt,
		})
	}
	return items, total, rows.Err()
}

func (p *Postgres) ListDiagnosticCases(ctx context.Context) ([]DiagnosticCase, error) {
	clusterRows, err := p.pool.Query(ctx, `
		SELECT id,raw_query,status,normalized_intent_json,target_scope_json,
		       requested_time_range_json,effective_time_range_json,policy_profile,
		       risk_budget_json,resource_budget_json,budget_used_json,
		       hypothesis_graph_json,child_task_ids_json,conclusion_versions_json,
		       created_at,updated_at,
		       (SELECT count(*) FROM diagnosis_evidence e WHERE e.diagnosis_id=s.id)
		FROM diagnosis_sessions s`)
	if err != nil {
		return nil, err
	}
	items := make([]DiagnosticCase, 0)
	for clusterRows.Next() {
		var id, query, status, policy string
		var normalized, target, requestedRange, effectiveRange []byte
		var risk, resource, used, graph, taskIDs, conclusions []byte
		var createdAt, updatedAt time.Time
		var evidenceCount int
		if err := clusterRows.Scan(
			&id, &query, &status, &normalized, &target, &requestedRange, &effectiveRange,
			&policy, &risk, &resource, &used, &graph, &taskIDs, &conclusions,
			&createdAt, &updatedAt, &evidenceCount,
		); err != nil {
			clusterRows.Close()
			return nil, err
		}
		intent := jsonMap(normalized)
		strategy, _ := intent["analysis_strategy"].(string)
		if strategy == "" {
			strategy = "CLUSTER_TOPOLOGY"
		}
		graphValue := jsonMap(graph)
		items = append(items, DiagnosticCase{
			CaseID: id, DiagnosisID: id, Source: "cluster_diagnosis_v1",
			Strategy: strategy, Query: query, Status: status,
			CanonicalStatus: canonicalDiagnosisStatus(status), Target: jsonMap(target),
			TimeRange:       firstNonEmptyMap(jsonMap(effectiveRange), jsonMap(requestedRange)),
			Budget:          map[string]any{"policy_profile": policy, "risk": jsonMap(risk), "resource": jsonMap(resource), "used": jsonMap(used)},
			HypothesisCount: countJSONArray(graphValue, "hypotheses", "nodes"),
			EvidenceCount:   evidenceCount, ReportVersionCount: len(jsonArray(conclusions)),
			TaskIDs: jsonArray(taskIDs), CreatedAt: createdAt, UpdatedAt: updatedAt,
			LegacyLinks: map[string]any{"detail": "/api/v1/diagnoses/" + id},
		})
	}
	if err := clusterRows.Err(); err != nil {
		clusterRows.Close()
		return nil, err
	}
	clusterRows.Close()

	insightRows, err := p.pool.Query(ctx, `
		SELECT s.id,s.query,s.status,s.target_json,s.time_range_json,s.budget_json,
		       s.created_at,s.updated_at,
		       (SELECT count(*) FROM drop_insight_hypotheses h WHERE h.diagnosis_id=s.id),
		       (SELECT count(*) FROM drop_insight_evidence e WHERE e.diagnosis_id=s.id),
		       (SELECT count(*) FROM drop_insight_reports r WHERE r.diagnosis_id=s.id),
		       COALESCE((SELECT json_agg(t.task_id) FILTER (WHERE t.task_id IS NOT NULL)
		                 FROM drop_insight_tool_calls t WHERE t.diagnosis_id=s.id),'[]'::json)
		FROM drop_insight_sessions s`)
	if err != nil {
		return nil, err
	}
	defer insightRows.Close()
	for insightRows.Next() {
		var id, query, status string
		var target, timeRange, budget, taskIDs []byte
		var createdAt, updatedAt time.Time
		var hypothesisCount, evidenceCount, reportCount int
		if err := insightRows.Scan(
			&id, &query, &status, &target, &timeRange, &budget, &createdAt, &updatedAt,
			&hypothesisCount, &evidenceCount, &reportCount, &taskIDs,
		); err != nil {
			return nil, err
		}
		items = append(items, DiagnosticCase{
			CaseID: id, DiagnosisID: id, Source: "drop_insight_v2", Strategy: "EVIDENCE_HYPOTHESIS",
			Query: query, Status: status, CanonicalStatus: canonicalDiagnosisStatus(status),
			Target: jsonMap(target), TimeRange: jsonMap(timeRange),
			Budget: jsonMap(budget), HypothesisCount: hypothesisCount, EvidenceCount: evidenceCount,
			ReportVersionCount: reportCount, TaskIDs: jsonArray(taskIDs), CreatedAt: createdAt,
			UpdatedAt: updatedAt, LegacyLinks: map[string]any{
				"detail": "/api/v2/diagnoses/" + id, "events": "/api/v2/diagnoses/" + id + "/events",
			},
		})
	}
	if err := insightRows.Err(); err != nil {
		return nil, err
	}
	insightRows.Close()

	legacyRows, err := p.pool.Query(ctx, `
		SELECT r.id,r.task_id,r.status,COALESCE(r.summary,''),r.created_at,
		       COALESCE(r.finished_at,r.created_at),
		       (SELECT count(*) FROM diagnosis_tool_results t WHERE t.diagnosis_id=r.id),
		       (SELECT count(*) FROM diagnosis_reports p WHERE p.diagnosis_id=r.id)
		FROM diagnosis_runs r`)
	if err != nil {
		return nil, err
	}
	defer legacyRows.Close()
	for legacyRows.Next() {
		var id, taskID, status, summary string
		var createdAt, updatedAt time.Time
		var evidenceCount, reportCount int
		if err := legacyRows.Scan(
			&id, &taskID, &status, &summary, &createdAt, &updatedAt,
			&evidenceCount, &reportCount,
		); err != nil {
			return nil, err
		}
		items = append(items, legacyDiagnosticCase(
			id, taskID, status, summary, evidenceCount, reportCount, createdAt, updatedAt,
		))
	}
	return items, legacyRows.Err()
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
			creator_id, idempotency_key, created_at
		) VALUES ($1,$2,$3,$4,$5,$6,$7,'PENDING',$8,'QUEUED','NOT_STARTED',$9::jsonb,$10,$11,$12)`,
		taskID, input.Name, input.AgentID, input.TargetPID, input.CollectorType,
		input.SampleRate, input.DurationSec, "Go API 创建任务", string(requestParams),
		creatorID, idemKey, now,
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
		       status, status_reason, collection_status, analysis_status, request_params,
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
		       status, status_reason, collection_status, analysis_status, request_params,
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
	nextCollection := "CANCELLED"
	nextAnalysis := "SKIPPED"
	if collectionStatus == "SUCCEEDED" {
		nextCollection = collectionStatus
		nextAnalysis = "CANCELLED"
	}
	if _, err := tx.Exec(ctx, `
		UPDATE tasks SET status='CANCELLED', status_reason=$2,
		collection_status=$3, analysis_status=$4, finished_at=$5
		WHERE id=$1`, taskID, reason, nextCollection, nextAnalysis, now); err != nil {
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

func (p *Postgres) ListDiagnosisEventsAfter(
	ctx context.Context, afterID int64, limit int,
) ([]DiagnosisEvent, error) {
	rows, err := p.pool.Query(ctx, `
		SELECT id,diagnosis_id,event_type,from_status,to_status,payload_json,created_at
		FROM diagnosis_events WHERE id>$1 ORDER BY id ASC LIMIT $2`, afterID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]DiagnosisEvent, 0)
	for rows.Next() {
		var item DiagnosisEvent
		var payload []byte
		if err := rows.Scan(
			&item.ID, &item.DiagnosisID, &item.EventType, &item.FromStatus,
			&item.ToStatus, &payload, &item.CreatedAt,
		); err != nil {
			return nil, err
		}
		decoded := decodeJSON(payload, map[string]any{})
		item.Payload, _ = decoded.(map[string]any)
		items = append(items, item)
	}
	return items, rows.Err()
}

func (p *Postgres) LatestDiagnosisEventID(ctx context.Context) (int64, error) {
	var id int64
	err := p.pool.QueryRow(ctx, `SELECT COALESCE(MAX(id),0) FROM diagnosis_events`).Scan(&id)
	return id, err
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

func scanDiagnosisSession(scan scanner) (map[string]any, error) {
	var id, creatorID, rawQuery, status, policyProfile, modelVersion, plannerVersion string
	var caseID, topologySnapshotID, baselineSnapshotID, leaseOwner *string
	var normalizedIntent, targetScope, requestedRange, effectiveRange []byte
	var riskBudget, resourceBudget, budgetUsed, hypothesisGraph []byte
	var childTaskIDs, conclusionVersions []byte
	var leaseUntil *time.Time
	var rowVersion int
	var deadlineAt, createdAt, updatedAt time.Time
	if err := scan(
		&id, &caseID, &creatorID, &rawQuery, &normalizedIntent, &targetScope,
		&requestedRange, &effectiveRange, &topologySnapshotID, &baselineSnapshotID,
		&status, &policyProfile, &riskBudget, &resourceBudget, &budgetUsed,
		&hypothesisGraph, &childTaskIDs, &conclusionVersions,
		&modelVersion, &plannerVersion, &leaseOwner, &leaseUntil, &rowVersion,
		&deadlineAt, &createdAt, &updatedAt,
	); err != nil {
		return nil, err
	}
	return map[string]any{
		"diagnosis_id": id, "case_id": caseID, "creator_id": creatorID, "raw_query": rawQuery,
		"normalized_intent":    decodeJSON(normalizedIntent, map[string]any{}),
		"target_scope":         decodeJSON(targetScope, map[string]any{}),
		"requested_time_range": decodeJSON(requestedRange, map[string]any{}),
		"effective_time_range": decodeJSON(effectiveRange, map[string]any{}),
		"topology_snapshot_id": topologySnapshotID, "baseline_snapshot_id": baselineSnapshotID,
		"status": status, "policy_profile": policyProfile,
		"risk_budget":         decodeJSON(riskBudget, map[string]any{}),
		"resource_budget":     decodeJSON(resourceBudget, map[string]any{}),
		"budget_used":         decodeJSON(budgetUsed, map[string]any{}),
		"hypothesis_graph":    decodeJSON(hypothesisGraph, map[string]any{}),
		"child_task_ids":      decodeJSON(childTaskIDs, []any{}),
		"conclusion_versions": decodeJSON(conclusionVersions, []any{}),
		"model_version":       modelVersion, "planner_version": plannerVersion,
		"lease_owner": leaseOwner, "lease_until": leaseUntil, "row_version": rowVersion,
		"deadline_at": deadlineAt, "created_at": createdAt, "updated_at": updatedAt,
	}, nil
}

func scanTask(scan scanner) (map[string]any, error) {
	var id, name, agentID, collectorType, status, reason, collectionStatus, analysisStatus string
	var targetPID, sampleRate, durationSec int
	var requestParams []byte
	var createdAt time.Time
	var startedAt, finishedAt *time.Time
	if err := scan(
		&id, &name, &agentID, &targetPID, &collectorType, &sampleRate, &durationSec,
		&status, &reason, &collectionStatus, &analysisStatus, &requestParams,
		&createdAt, &startedAt, &finishedAt,
	); err != nil {
		return nil, err
	}
	return map[string]any{
		"id": id, "name": name, "agent_id": agentID, "target_pid": targetPID,
		"collector_type": collectorType, "sample_rate": sampleRate, "duration_sec": durationSec,
		"status": status, "status_reason": reason, "collection_status": collectionStatus,
		"analysis_status": analysisStatus, "request_params": decodeJSON(requestParams, map[string]any{}),
		"created_at": createdAt, "started_at": startedAt, "finished_at": finishedAt,
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

func jsonMap(raw []byte) map[string]any {
	value, _ := decodeJSON(raw, map[string]any{}).(map[string]any)
	if value == nil {
		return map[string]any{}
	}
	return value
}

func jsonArray(raw []byte) []any {
	value, _ := decodeJSON(raw, []any{}).([]any)
	if value == nil {
		return []any{}
	}
	return value
}

func firstNonEmptyMap(values ...map[string]any) map[string]any {
	for _, value := range values {
		if len(value) > 0 {
			return value
		}
	}
	return map[string]any{}
}

func countJSONArray(value map[string]any, keys ...string) int {
	for _, key := range keys {
		if items, ok := value[key].([]any); ok {
			return len(items)
		}
	}
	return 0
}

func (p *Postgres) queryJSONRows(ctx context.Context, query string, args ...any) ([]map[string]any, error) {
	rows, err := p.pool.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := make([]map[string]any, 0)
	for rows.Next() {
		var raw []byte
		if err := rows.Scan(&raw); err != nil {
			return nil, err
		}
		items = append(items, jsonMap(raw))
	}
	return items, rows.Err()
}

func (p *Postgres) queryJSONObject(ctx context.Context, query string, args ...any) (map[string]any, error) {
	var raw []byte
	if err := p.pool.QueryRow(ctx, query, args...).Scan(&raw); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	return jsonMap(raw), nil
}

func cloneMap(source map[string]any) map[string]any {
	result := make(map[string]any, len(source))
	for key, value := range source {
		result[key] = value
	}
	return result
}

func latestItem(value any) any {
	items, ok := value.([]any)
	if !ok || len(items) == 0 {
		return nil
	}
	return items[len(items)-1]
}

func buildCoverage(probes []map[string]any) []map[string]any {
	statusMap := map[string]string{
		"READY": "QUEUED", "PLANNED": "PLANNED", "SCHEDULED": "SCHEDULED",
		"RUNNING": "RUNNING", "COMPLETED": "COMPLETED", "FAILED": "FAILED",
		"TIMED_OUT": "TIMED_OUT", "UNAVAILABLE": "UNAVAILABLE", "REJECTED": "REJECTED",
		"REJECTED_POLICY": "REJECTED", "WAITING_APPROVAL": "WAITING_APPROVAL", "SKIPPED": "SKIPPED",
	}
	items := make([]map[string]any, 0, len(probes))
	for _, probe := range probes {
		target := ""
		if targetMap, ok := probe["target"].(map[string]any); ok {
			target = stringValue(targetMap["instance_id"])
		}
		status := stringValue(probe["status"])
		if mapped, ok := statusMap[status]; ok {
			status = mapped
		}
		items = append(items, map[string]any{
			"target": target, "requirement": probe["probe_id"], "status": status,
			"step_id": probe["step_id"], "task_id": probe["task_id"], "error_code": probe["error_code"],
		})
	}
	return items
}

func clusterCaseFromSession(session map[string]any, evidenceCount int) DiagnosticCase {
	id := stringValue(session["diagnosis_id"])
	intent, _ := session["normalized_intent"].(map[string]any)
	strategy := stringValue(intent["analysis_strategy"])
	if strategy == "" {
		strategy = "CLUSTER_TOPOLOGY"
	}
	target, _ := session["target_scope"].(map[string]any)
	effective, _ := session["effective_time_range"].(map[string]any)
	requested, _ := session["requested_time_range"].(map[string]any)
	risk, _ := session["risk_budget"].(map[string]any)
	resource, _ := session["resource_budget"].(map[string]any)
	used, _ := session["budget_used"].(map[string]any)
	graph, _ := session["hypothesis_graph"].(map[string]any)
	conclusions, _ := session["conclusion_versions"].([]any)
	taskIDs, _ := session["child_task_ids"].([]any)
	createdAt, _ := session["created_at"].(time.Time)
	updatedAt, _ := session["updated_at"].(time.Time)
	return DiagnosticCase{
		CaseID: id, DiagnosisID: id, Source: "cluster_diagnosis_v1", Strategy: strategy,
		Query: stringValue(session["raw_query"]), Status: stringValue(session["status"]),
		CanonicalStatus: canonicalDiagnosisStatus(stringValue(session["status"])),
		Target:          target, TimeRange: firstNonEmptyMap(effective, requested),
		Budget: map[string]any{
			"policy_profile": session["policy_profile"], "risk": risk, "resource": resource, "used": used,
		},
		HypothesisCount: countJSONArray(graph, "hypotheses", "nodes"), EvidenceCount: evidenceCount,
		ReportVersionCount: len(conclusions), TaskIDs: taskIDs, CreatedAt: createdAt, UpdatedAt: updatedAt,
		LegacyLinks: map[string]any{"detail": "/api/v1/diagnoses/" + id},
	}
}

func legacyDiagnosticCase(
	id, taskID, status, summary string,
	evidenceCount, reportCount int,
	createdAt, updatedAt time.Time,
) DiagnosticCase {
	return DiagnosticCase{
		CaseID: id, DiagnosisID: id, Source: "legacy_rca", Strategy: "RULE_LLM_RCA",
		Query: summary, Status: status, CanonicalStatus: canonicalDiagnosisStatus(status),
		Target: map[string]any{"task_id": taskID}, TimeRange: map[string]any{},
		Budget: map[string]any{}, HypothesisCount: 0, EvidenceCount: evidenceCount,
		ReportVersionCount: reportCount, TaskIDs: []any{taskID}, CreatedAt: createdAt,
		UpdatedAt: updatedAt, LegacyLinks: map[string]any{
			"detail": "/api/diagnoses/" + id,
			"task":   "/api/tasks/" + taskID,
		},
	}
}

func canonicalDiagnosisStatus(native string) string {
	switch strings.ToUpper(strings.TrimSpace(native)) {
	case "CREATED", "PENDING", "UNDERSTANDING", "NEEDS_CLARIFICATION", "NEEDS_SCOPE_CONFIRMATION":
		return "CREATED"
	case "PLANNING", "HYPOTHESIZING", "PLAN_READY":
		return "PLANNING"
	case "COLLECTING", "RUNNING", "EXECUTING", "PROBING":
		return "COLLECTING"
	case "ANALYZING", "REPORTING", "VERIFYING":
		return "ANALYZING"
	case "WAITING_APPROVAL", "WAITING_FOR_APPROVAL", "APPROVAL_REQUIRED":
		return "WAITING_APPROVAL"
	case "COMPLETED", "DONE", "SUCCEEDED":
		return "COMPLETED"
	case "PARTIAL_COMPLETED", "PARTIAL", "INSUFFICIENT_EVIDENCE":
		return "PARTIAL"
	case "FAILED", "ERROR", "TIMED_OUT":
		return "FAILED"
	case "CANCELLED", "CANCELED":
		return "CANCELLED"
	default:
		return "UNKNOWN"
	}
}

func diagnosticCaseMap(value DiagnosticCase) map[string]any {
	raw, _ := json.Marshal(value)
	result := map[string]any{}
	_ = json.Unmarshal(raw, &result)
	return result
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
