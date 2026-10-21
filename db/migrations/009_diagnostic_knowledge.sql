-- 诊断案例只保存受控枚举、脱敏特征和来源身份，不复制用户问题、模型叙述、原始符号或源码。
-- 成功修复与人工纠正分别标注；有效期、撤销与每次读取时的权限/来源复核共同限制跨会话复用。
-- 唯一键将并发重复保存收敛到同一案例；业务保存和审计由 API 放在同一事务中提交。
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,9);
ALTER TABLE diagnosis_runs ADD COLUMN IF NOT EXISTS knowledge_mode text NOT NULL DEFAULT 'off' CHECK(knowledge_mode IN ('off','skills','experience'));
CREATE TABLE IF NOT EXISTS diagnostic_cases (
 id bigserial PRIMARY KEY,
 owner_id text NOT NULL REFERENCES users(id),
 task_id bigint NOT NULL REFERENCES tasks(id),
 diagnosis_id bigint NOT NULL REFERENCES diagnosis_runs(id),
 attempt_id bigint NOT NULL REFERENCES task_attempts(id),
 analysis_job_id bigint NOT NULL REFERENCES analysis_jobs(id),
 analysis_run integer NOT NULL CHECK(analysis_run>=0),
 code_repair_id bigint REFERENCES code_repairs(id),
 task_kind text NOT NULL, agent_id text NOT NULL DEFAULT '',
 project_id text NOT NULL DEFAULT '', source_revision text NOT NULL DEFAULT '',
 outcome text NOT NULL CHECK(outcome IN ('verified_repair','reviewed_correction')),
 correction_code text NOT NULL DEFAULT '' CHECK(correction_code IN ('','insufficient_evidence','wrong_hotspot','causality_not_established','incomparable_measurements','regression_detected')),
 features jsonb NOT NULL, skill_refs jsonb NOT NULL,
 request_key text NOT NULL CHECK(octet_length(request_key) BETWEEN 8 AND 128),
 input_digest text NOT NULL,
 status text NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked')),
 created_at timestamptz NOT NULL DEFAULT now(),
 expires_at timestamptz NOT NULL,
 revoked_at timestamptz,
 CHECK((outcome='verified_repair' AND code_repair_id IS NOT NULL AND correction_code='' AND project_id<>'' AND length(source_revision)=64) OR
       (outcome='reviewed_correction' AND code_repair_id IS NULL AND correction_code<>'')),
 UNIQUE(owner_id,request_key),
 UNIQUE(owner_id,diagnosis_id,outcome)
);
CREATE INDEX IF NOT EXISTS diagnostic_case_candidates ON diagnostic_cases(owner_id,task_kind,agent_id,created_at DESC,id DESC) WHERE status='active';
INSERT INTO schema_migrations(version) VALUES(9) ON CONFLICT DO NOTHING;
COMMIT;
