BEGIN;
SELECT pg_advisory_xact_lock(41762,2);
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK(role IN ('viewer','operator','group_admin','platform_admin','admin'));
CREATE TABLE IF NOT EXISTS groups (
 id text PRIMARY KEY, name text UNIQUE NOT NULL, description text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS group_members (
 group_id text REFERENCES groups(id), user_id text REFERENCES users(id), role text NOT NULL CHECK(role IN ('viewer','operator','group_admin')),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(group_id,user_id)
);
CREATE TABLE IF NOT EXISTS group_agents (
 group_id text REFERENCES groups(id), agent_id text REFERENCES agents(id), permission text NOT NULL CHECK(permission IN ('read','operate')),
 created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(group_id,agent_id)
);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS group_id text REFERENCES groups(id);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','group'));
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS trace_parent text NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
CREATE TABLE IF NOT EXISTS task_shares (
 id bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), user_id text REFERENCES users(id), group_id text REFERENCES groups(id),
 permission text NOT NULL CHECK(permission IN ('read','operate')), created_by text NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(),
 CHECK((user_id IS NULL) <> (group_id IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS task_shares_user ON task_shares(task_id,user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS task_shares_group ON task_shares(task_id,group_id) WHERE group_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS tasks_group_created ON tasks(group_id,created_at DESC);
ALTER TABLE agents ADD COLUMN IF NOT EXISTS capability_details jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS agent_events (
 id bigserial PRIMARY KEY, agent_id text NOT NULL REFERENCES agents(id), event_id text NOT NULL UNIQUE,
 type text NOT NULL, payload jsonb NOT NULL DEFAULT '{}', occurred_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_events_agent_time ON agent_events(agent_id,occurred_at DESC);
ALTER TABLE task_attempts ADD COLUMN IF NOT EXISTS runner_metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE task_attempts ADD COLUMN IF NOT EXISTS upload_targets jsonb NOT NULL DEFAULT '[]';
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS format text NOT NULL DEFAULT 'folded';
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS metadata jsonb NOT NULL DEFAULT '{}';
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS retention_class text NOT NULL DEFAULT '';
ALTER TABLE artifacts ADD COLUMN IF NOT EXISTS download_allowed boolean NOT NULL DEFAULT true;
CREATE OR REPLACE FUNCTION artifact_retention_defaults() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.retention_class='' THEN NEW.retention_class=lower(NEW.kind); END IF;
 IF NEW.expires_at IS NULL THEN
  NEW.expires_at=NEW.created_at+CASE WHEN NEW.kind='RAW' THEN interval '14 days' WHEN NEW.kind='INTERMEDIATE' THEN interval '3 days' WHEN NEW.kind='LOG' THEN interval '14 days' ELSE interval '90 days' END;
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS artifacts_retention ON artifacts;
CREATE TRIGGER artifacts_retention BEFORE INSERT ON artifacts FOR EACH ROW EXECUTE FUNCTION artifact_retention_defaults();
UPDATE artifacts SET retention_class=lower(kind), expires_at=created_at+CASE WHEN kind='RAW' THEN interval '14 days' WHEN kind='INTERMEDIATE' THEN interval '3 days' WHEN kind='LOG' THEN interval '14 days' ELSE interval '90 days' END WHERE expires_at IS NULL;
CREATE INDEX IF NOT EXISTS artifacts_expiry ON artifacts(expires_at) WHERE status='ready';
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS cron text NOT NULL DEFAULT '';
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS group_id text REFERENCES groups(id);
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','group'));
ALTER TABLE schedule_records ADD COLUMN IF NOT EXISTS template_snapshot jsonb NOT NULL DEFAULT '{}';
CREATE TABLE IF NOT EXISTS composite_runs (
 id bigserial PRIMARY KEY, name text NOT NULL, creator_id text NOT NULL REFERENCES users(id), group_id text REFERENCES groups(id),
 visibility text NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','group')), status text NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','RUNNING','SUCCESS','PARTIAL_SUCCESS','FAILED','CANCELED','TIMED_OUT')),
 allow_partial_success boolean NOT NULL DEFAULT false, deadline_at timestamptz NOT NULL, definition jsonb NOT NULL,
 summary jsonb NOT NULL DEFAULT '{}', idempotency_key text NOT NULL, request_hash text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(creator_id,idempotency_key)
);
CREATE TABLE IF NOT EXISTS composite_nodes (
 id bigserial PRIMARY KEY, run_id bigint NOT NULL REFERENCES composite_runs(id), key text NOT NULL, required boolean NOT NULL,
 depends_on jsonb NOT NULL DEFAULT '[]', template jsonb NOT NULL, task_id bigint REFERENCES tasks(id),
 status text NOT NULL DEFAULT 'WAITING' CHECK(status IN ('WAITING','RUNNING','SUCCESS','FAILED','SKIPPED')), error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(run_id,key)
);
CREATE INDEX IF NOT EXISTS composite_runs_pending ON composite_runs(created_at) WHERE status IN ('PENDING','RUNNING');
CREATE TABLE IF NOT EXISTS diagnostic_rules (
 key text PRIMARY KEY, enabled boolean NOT NULL DEFAULT true, severity text NOT NULL DEFAULT 'warning' CHECK(severity IN ('info','warning','critical')),
 min_samples bigint NOT NULL DEFAULT 100 CHECK(min_samples>=0), threshold double precision NOT NULL DEFAULT 20 CHECK(threshold>=0 AND threshold<=100),
 config jsonb NOT NULL DEFAULT '{}', version integer NOT NULL DEFAULT 1 CHECK(version>0), updated_by text REFERENCES users(id), updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO diagnostic_rules(key,min_samples,threshold) VALUES('allocation',100,20),('lock',100,20),('gc',100,15) ON CONFLICT DO NOTHING;
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS input_digest text NOT NULL DEFAULT '';
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS attempt_id bigint REFERENCES task_attempts(id);
ALTER TABLE suggestions ADD COLUMN IF NOT EXISTS superseded_at timestamptz;
CREATE TABLE IF NOT EXISTS analysis_job_runs (
 id bigserial PRIMARY KEY, job_id bigint NOT NULL REFERENCES analysis_jobs(id), task_id bigint NOT NULL REFERENCES tasks(id), attempt_id bigint REFERENCES task_attempts(id),
 run_number integer NOT NULL, status text NOT NULL, result jsonb, error_code text, analyzer_version text, archived_at timestamptz NOT NULL DEFAULT now(), UNIQUE(job_id,run_number)
);
CREATE TABLE IF NOT EXISTS suggestion_feedback (
 id bigserial PRIMARY KEY, suggestion_id bigint NOT NULL REFERENCES suggestions(id), user_id text NOT NULL REFERENCES users(id),
 rating text NOT NULL CHECK(rating IN ('useful','not_useful')), comment text NOT NULL DEFAULT '' CHECK(octet_length(comment)<=1000),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(suggestion_id,user_id)
);
CREATE TABLE IF NOT EXISTS ai_requests (
 id bigserial PRIMARY KEY, task_id bigint NOT NULL REFERENCES tasks(id), attempt_id bigint NOT NULL REFERENCES task_attempts(id),
 requested_by text NOT NULL REFERENCES users(id), input_digest text NOT NULL, status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','success','failed')),
 available_at timestamptz NOT NULL DEFAULT now(), lease_owner text, lease_expires_at timestamptz, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE(task_id,attempt_id,input_digest)
);
ALTER TABLE ai_requests ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS oidc_states (
 state_hash text PRIMARY KEY, browser_hash text NOT NULL, nonce text NOT NULL, verifier text NOT NULL, expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS oidc_identities (
 issuer text NOT NULL, subject text NOT NULL, user_id text NOT NULL REFERENCES users(id), created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(issuer,subject)
);
CREATE TABLE IF NOT EXISTS rate_limits (
 key text PRIMARY KEY, window_start timestamptz NOT NULL, hits integer NOT NULL, expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_limits_expiry ON rate_limits(expires_at);
CREATE INDEX IF NOT EXISTS oidc_states_expiry ON oidc_states(expires_at);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
INSERT INTO schema_migrations(version) VALUES(2) ON CONFLICT DO NOTHING;
COMMIT;
