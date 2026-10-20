-- 任务、执行attempt、节点、制品与分析队列的初始结构，是跨服务状态的持久化真源。
-- task描述业务任务，attempt描述一次具体执行；采集状态和分析状态分开保存。
-- 迁移顺序由版本表控制；此处仅补说明，不能重排历史列、约束或已有版本编号。

BEGIN;
CREATE TABLE IF NOT EXISTS schema_migrations(version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS users (
 id text PRIMARY KEY, username text UNIQUE NOT NULL, display_name text NOT NULL, password_hash text,
 role text NOT NULL CHECK(role IN ('viewer','operator','admin')), oidc_subject text UNIQUE,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash text PRIMARY KEY, user_id text NOT NULL REFERENCES users(id), csrf_token text NOT NULL,
 expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agents (
 id text PRIMARY KEY, hostname text NOT NULL, ip text NOT NULL DEFAULT '', version text NOT NULL,
 platform text NOT NULL, capabilities jsonb NOT NULL DEFAULT '[]', labels jsonb NOT NULL DEFAULT '{}',
 status text NOT NULL DEFAULT 'online' CHECK(status IN ('online','degraded','offline','disabled')),
 cpu_percent double precision NOT NULL DEFAULT 0, memory_percent double precision NOT NULL DEFAULT 0,
 load_average double precision NOT NULL DEFAULT 0, resources jsonb NOT NULL DEFAULT '{}',
 last_seen_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS tasks (
 id bigserial PRIMARY KEY, name text NOT NULL, kind text NOT NULL,
 target_agent_id text REFERENCES agents(id), params jsonb NOT NULL DEFAULT '{}',
 collection_status text NOT NULL DEFAULT 'CREATED' CHECK(collection_status IN ('CREATED','QUEUED','DELIVERED','RUNNING','UPLOADING','COLLECTED','FAILED','CANCELED')),
 analysis_status text NOT NULL DEFAULT 'PENDING' CHECK(analysis_status IN ('PENDING','RUNNING','RETRY','SUCCESS','FAILED','CANCELED')),
 creator_id text NOT NULL REFERENCES users(id), request_id text NOT NULL, idempotency_key text,
 request_hash text NOT NULL DEFAULT '', parent_task_id bigint REFERENCES tasks(id),
 collection_deadline timestamptz, error_code text, error_message text,
 version bigint NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(creator_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS task_attempts (
 id bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), attempt integer NOT NULL DEFAULT 1,
 agent_id text REFERENCES agents(id), status text NOT NULL DEFAULT 'QUEUED', runner_version text NOT NULL DEFAULT '1.0.0',
 raw_object_key text NOT NULL, raw_upload_url text NOT NULL DEFAULT '',
 started_at timestamptz, finished_at timestamptz, exit_code integer, error_code text, error_message text,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(task_id,attempt)
);
CREATE TABLE IF NOT EXISTS task_events (
 sequence bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), attempt_id bigint REFERENCES task_attempts(id),
 type text NOT NULL, source text NOT NULL, payload jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS artifacts (
 id bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), attempt_id bigint REFERENCES task_attempts(id),
 kind text NOT NULL, name text NOT NULL, object_key text UNIQUE NOT NULL, size bigint NOT NULL CHECK(size>=0),
 sha256 text NOT NULL, content_type text NOT NULL DEFAULT 'application/octet-stream',
 status text NOT NULL DEFAULT 'ready', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS analysis_jobs (
 id bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), attempt_id bigint REFERENCES task_attempts(id),
 pipeline text NOT NULL DEFAULT 'folded_v1', status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','retry','success','failed')),
 priority integer NOT NULL DEFAULT 0, attempt integer NOT NULL DEFAULT 0, max_attempts integer NOT NULL DEFAULT 3,
 lease_owner text, lease_expires_at timestamptz, available_at timestamptz NOT NULL DEFAULT now(),
 analyzer_version text, error_code text, error_message text, result jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(task_id,attempt_id)
);
CREATE TABLE IF NOT EXISTS suggestions (
 id bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), engine text NOT NULL DEFAULT 'rules',
 version text NOT NULL DEFAULT '1.0.0', severity text NOT NULL DEFAULT 'info', title text NOT NULL,
 content text NOT NULL, evidence jsonb NOT NULL DEFAULT '{}', confidence double precision NOT NULL,
 feedback text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox (
 id bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), attempt_id bigint REFERENCES task_attempts(id),
 event_type text NOT NULL DEFAULT 'DISPATCH', payload jsonb NOT NULL DEFAULT '{}', attempt integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz, last_error text,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(attempt_id,event_type)
);
CREATE TABLE IF NOT EXISTS schedules (
 id bigserial PRIMARY KEY, name text NOT NULL, creator_id text NOT NULL REFERENCES users(id),
 interval_minutes integer NOT NULL CHECK(interval_minutes BETWEEN 1 AND 10080), timezone text NOT NULL DEFAULT 'Asia/Shanghai',
 template jsonb NOT NULL, enabled boolean NOT NULL DEFAULT true, next_run_at timestamptz NOT NULL,
 last_run_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS schedule_records (
 id bigserial PRIMARY KEY, schedule_id bigint NOT NULL REFERENCES schedules(id), scheduled_at timestamptz NOT NULL,
 task_id bigint REFERENCES tasks(id), status text NOT NULL, error_message text, UNIQUE(schedule_id,scheduled_at)
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id bigserial PRIMARY KEY, user_id text, action text NOT NULL, resource_type text NOT NULL,
 resource_id text, request_id text NOT NULL, detail jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent_samples (
 id bigserial PRIMARY KEY, agent_id text NOT NULL REFERENCES agents(id), cpu_percent double precision NOT NULL,
 memory_percent double precision NOT NULL, load_average double precision NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tasks_creator_created ON tasks(creator_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_target_status ON tasks(target_agent_id,collection_status,created_at);
CREATE INDEX IF NOT EXISTS idx_task_events_task_sequence ON task_events(task_id,sequence);
CREATE INDEX IF NOT EXISTS idx_artifacts_task_kind ON artifacts(task_id,kind);
CREATE INDEX IF NOT EXISTS idx_analysis_jobs_claim ON analysis_jobs(status,available_at,priority DESC,created_at);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(available_at) WHERE published_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_agent_samples_time ON agent_samples(agent_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(next_run_at) WHERE enabled;
INSERT INTO schema_migrations(version) VALUES(1) ON CONFLICT DO NOTHING;
COMMIT;
