-- 保存源码修复运行，并将调查关联到已登记的项目与固定源码版本。
-- 模型补丁在隔离副本验证，数据库里的success不代表已自动修改或发布原业务仓库。
-- lease、请求键与输入摘要继续约束恢复与幂等；本迁移只追加结构，不改历史诊断证据。

BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,8);
CREATE UNIQUE INDEX IF NOT EXISTS verification_diagnosis_unique ON verification_runs(candidate_diagnosis_id) WHERE candidate_diagnosis_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS code_repairs (
 id bigserial PRIMARY KEY, requested_by text NOT NULL REFERENCES users(id),
 project_id text NOT NULL, source_revision text NOT NULL,
 goal text NOT NULL CHECK(octet_length(goal) BETWEEN 1 AND 2400),
 request_key text NOT NULL, input_digest text NOT NULL,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','success','failed','canceled')),
 lease_owner text, lease_expires_at timestamptz,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
 trace jsonb NOT NULL DEFAULT '[]', report jsonb, error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(requested_by,request_key)
);
CREATE INDEX IF NOT EXISTS code_repair_pending ON code_repairs(id) WHERE status IN ('pending','running');
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS code_project_id text;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS code_source_revision text;
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS code_repair_id bigint REFERENCES code_repairs(id);
INSERT INTO schema_migrations(version) VALUES(8) ON CONFLICT DO NOTHING;
COMMIT;
