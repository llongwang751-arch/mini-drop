package repository

import (
	"context"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"os"
	"strings"
	"testing"
	"time"
)

type afterLookupTracer struct {
	next  func()
	fired bool
}

func (t *afterLookupTracer) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	return context.WithValue(ctx, lookupTraceKey{}, strings.Contains(d.SQL, "SELECT id, request_params FROM tasks"))
}

type lookupTraceKey struct{}

func (t *afterLookupTracer) TraceQueryEnd(ctx context.Context, _ *pgx.Conn, _ pgx.TraceQueryEndData) {
	if yes, _ := ctx.Value(lookupTraceKey{}).(bool); yes && !t.fired {
		t.fired = true
		t.next()
	}
}

func TestPostgresIdempotencyRaceWithOneConnection(t *testing.T) {
	url := os.Getenv("MINI_DROP_TEST_POSTGRES_URL")
	if url == "" {
		t.Skip("dedicated PostgreSQL test URL required")
	}
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(strings.ToLower(cfg.ConnConfig.Database), "test") {
		t.Fatal("dedicated test database required")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	admin, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer admin.Close()
	schema := "idem_test_" + time.Now().Format("150405000000000")
	_, err = admin.Exec(ctx, "CREATE SCHEMA "+schema)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _, _ = admin.Exec(context.Background(), "DROP SCHEMA "+schema+" CASCADE") }()
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	peer, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer peer.Close()
	_, err = peer.Exec(ctx, `CREATE TABLE agents(id text primary key);
 INSERT INTO agents VALUES ('a');
 CREATE TABLE tasks(id text primary key,name text,agent_id text,target_pid int,collector_type text,sample_rate int,duration_sec int,status text,status_reason text,collection_status text,analysis_status text,request_params jsonb,creator_id text,idempotency_key text,process_snapshot_id text,process_binding_json json,created_at timestamptz,UNIQUE(creator_id,idempotency_key));
 CREATE TABLE task_status_events(task_id text,from_status text,to_status text,reason text,actor text,metadata jsonb,created_at timestamptz);
 CREATE TABLE outbox_messages(id text primary key,aggregate_type text,aggregate_id text,event_type text,payload_json jsonb,status text,attempts int,next_attempt_at timestamptz,created_at timestamptz,updated_at timestamptz);`)
	if err != nil {
		t.Fatal(err)
	}
	input := CreateTask{Name: "race", AgentID: "a", TargetPID: 1, CollectorType: "sys_metrics", CreatorID: "test", IdempotencyKey: "same-request-key", ProcessBinding: ProcessBinding{AgentID: "a", PID: 1, ProcessSnapshotID: "snapshot"}}
	var winningID string
	var winningErr error
	tracer := &afterLookupTracer{next: func() { winningID, _, winningErr = (&Postgres{pool: peer}).CreateTask(ctx, input) }}
	cfg = cfg.Copy()
	cfg.MaxConns = 1
	cfg.ConnConfig.Tracer = tracer
	single, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer single.Close()
	id, replay, err := (&Postgres{pool: single}).CreateTask(ctx, input)
	if winningErr != nil || err != nil || !replay || id != winningID {
		t.Fatalf("race failed: winner=%s winnerError=%v id=%s replay=%v err=%v", winningID, winningErr, id, replay, err)
	}
	for _, table := range []string{"tasks", "task_status_events", "outbox_messages"} {
		var count int
		if err := peer.QueryRow(ctx, "SELECT count(*) FROM "+table).Scan(&count); err != nil || count != 1 {
			t.Fatalf("%s: count=%d err=%v", table, count, err)
		}
	}
}
