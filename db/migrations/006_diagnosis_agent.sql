BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,6);
CREATE TABLE IF NOT EXISTS diagnosis_runs (
 id bigserial PRIMARY KEY,
 task_id bigint NOT NULL REFERENCES tasks(id),
 attempt_id bigint NOT NULL REFERENCES task_attempts(id),
 analysis_job_id bigint NOT NULL REFERENCES analysis_jobs(id),
 analysis_run integer NOT NULL CHECK(analysis_run>=0),
 requested_by text NOT NULL REFERENCES users(id),
 goal text NOT NULL CHECK(octet_length(goal) BETWEEN 1 AND 2400),
 mode text NOT NULL CHECK(mode IN ('evidence','model')),
 input_digest text NOT NULL,
 request_key text NOT NULL CHECK(octet_length(request_key) BETWEEN 8 AND 128),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','success','failed')),
 lease_owner text, lease_expires_at timestamptz,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 trace jsonb NOT NULL DEFAULT '[]', report jsonb,
 error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(requested_by,request_key)
);
CREATE INDEX IF NOT EXISTS diagnosis_queue ON diagnosis_runs(id) WHERE status IN ('pending','running');
INSERT INTO schema_migrations(version) VALUES(6) ON CONFLICT DO NOTHING;
COMMIT;
