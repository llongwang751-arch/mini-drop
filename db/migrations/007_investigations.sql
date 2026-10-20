-- 把自然语言调查与复测流程持久化，串联计划确认、基线采集、诊断及新一轮对照。
-- verification_runs显式保存基线任务、候选任务和候选诊断，不能把重复采样直接认作根因成立。
-- investigations的状态记录当前阶段，真正采集与诊断仍由各自独立任务执行并回传。

BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,7);
CREATE TABLE IF NOT EXISTS verification_runs (
 id bigserial PRIMARY KEY,
 diagnosis_id bigint NOT NULL REFERENCES diagnosis_runs(id),
 baseline_task_id bigint NOT NULL REFERENCES tasks(id),
 candidate_task_id bigint NOT NULL UNIQUE REFERENCES tasks(id),
 requested_by text NOT NULL REFERENCES users(id),
 candidate_diagnosis_id bigint REFERENCES diagnosis_runs(id),
 status text NOT NULL DEFAULT 'waiting' CHECK(status IN ('waiting','diagnosing','comparing','success','failed','canceled')),
 report jsonb, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS verification_pending ON verification_runs(id) WHERE status IN ('waiting','diagnosing','comparing');
CREATE TABLE IF NOT EXISTS investigations (
 id bigserial PRIMARY KEY, requested_by text NOT NULL REFERENCES users(id),
 agent_id text NOT NULL REFERENCES agents(id), goal text NOT NULL CHECK(octet_length(goal) BETWEEN 1 AND 2400),
 parameters jsonb NOT NULL, consent boolean NOT NULL DEFAULT false,
 request_key text NOT NULL, input_digest text NOT NULL,
 status text NOT NULL DEFAULT 'planning' CHECK(status IN ('planning','awaiting_confirmation','queued','collecting','diagnosing','verifying','success','failed','canceled')),
 plan jsonb, trace jsonb NOT NULL DEFAULT '[]', report jsonb,
 baseline_task_id bigint REFERENCES tasks(id), diagnosis_id bigint REFERENCES diagnosis_runs(id),
 verification_id bigint REFERENCES verification_runs(id),
 lease_owner text, lease_expires_at timestamptz,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3), error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(requested_by,request_key)
);
CREATE INDEX IF NOT EXISTS investigation_pending ON investigations(id) WHERE status IN ('planning','queued','collecting','diagnosing','verifying');
INSERT INTO schema_migrations(version) VALUES(7) ON CONFLICT DO NOTHING;
COMMIT;
