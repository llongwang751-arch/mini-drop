-- Agent 能力增量保持在既有任务、轮次、授权及租约边界中：
-- 旧报告继续按协议1读取；新报告使用程序可核验的协议2。
-- 追加采集只保存审阅计划，用户确认后才由普通任务链路执行。
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,10);

ALTER TABLE diagnosis_runs ADD COLUMN IF NOT EXISTS report_protocol integer NOT NULL DEFAULT 1 CHECK(report_protocol IN (1,2));
ALTER TABLE diagnosis_runs ADD COLUMN IF NOT EXISTS telemetry_service_id text NOT NULL DEFAULT '';
ALTER TABLE investigations ADD COLUMN IF NOT EXISTS telemetry_service_id text NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS investigation_followups (
 id bigserial PRIMARY KEY,
 investigation_id bigint NOT NULL REFERENCES investigations(id),
 parent_task_id bigint NOT NULL REFERENCES tasks(id),
 source_diagnosis_id bigint NOT NULL REFERENCES diagnosis_runs(id),
 plan jsonb NOT NULL CHECK(octet_length(plan::text)<=16384),
 plan_digest text NOT NULL,
 status text NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','queued','collecting','diagnosing','success','failed','rejected','canceled')),
 task_id bigint REFERENCES tasks(id), diagnosis_id bigint REFERENCES diagnosis_runs(id),
 error_code text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(investigation_id,source_diagnosis_id,plan_digest)
);
CREATE INDEX IF NOT EXISTS investigation_followup_work ON investigation_followups(status,id);

-- 检查点不含 capability、模型密钥或隐藏思维链。通用 scope 的真实来源、轮次和租约由业务栅栏在同一事务重验。
CREATE TABLE IF NOT EXISTS agent_checkpoints (
 scope_type text NOT NULL CHECK(scope_type IN ('diagnosis','planning','repair')),
 scope_id bigint NOT NULL CHECK(scope_id>0), input_digest text NOT NULL, spec_digest text NOT NULL,
 sequence bigint NOT NULL DEFAULT 0 CHECK(sequence>=0),
 model_calls integer NOT NULL DEFAULT 0 CHECK(model_calls>=0),
 tool_calls integer NOT NULL DEFAULT 0 CHECK(tool_calls>=0),
 round_tools integer NOT NULL DEFAULT 0 CHECK(round_tools>=0),
 format_only boolean NOT NULL DEFAULT false,
 state jsonb NOT NULL DEFAULT '{}' CHECK(octet_length(state::text)<=262144),
 usage jsonb NOT NULL DEFAULT '{}' CHECK(octet_length(usage::text)<=16384),
 expires_at timestamptz NOT NULL, updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(scope_type,scope_id)
);
CREATE TABLE IF NOT EXISTS agent_tool_receipts (
 scope_type text NOT NULL, scope_id bigint NOT NULL,
 call_id text NOT NULL CHECK(octet_length(call_id) BETWEEN 1 AND 128), request_digest text NOT NULL,
 round integer NOT NULL CHECK(round>=1), status text NOT NULL CHECK(status IN ('pending','complete')),
 response jsonb CHECK(response IS NULL OR octet_length(response::text)<=65536),
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(scope_type,scope_id,call_id),
 FOREIGN KEY(scope_type,scope_id) REFERENCES agent_checkpoints(scope_type,scope_id) ON DELETE CASCADE,
 CHECK((status='pending' AND response IS NULL) OR (status='complete' AND response IS NOT NULL))
);

-- 方法库先保存草稿；每个发布版本不可原地改写，退役后不再进入新诊断。
-- 团队/项目方法仍不授予任何额外采集、数据源或执行权限。
CREATE TABLE IF NOT EXISTS diagnostic_playbooks (
 id bigserial PRIMARY KEY, owner_id text NOT NULL REFERENCES users(id),
 group_id text REFERENCES groups(id), project_id text NOT NULL DEFAULT '',
 logical_key text NOT NULL, version integer NOT NULL CHECK(version>0),
 skill jsonb NOT NULL CHECK(octet_length(skill::text)<=16384),
 content_digest text NOT NULL,
 status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','retired')),
 reviewer_id text REFERENCES users(id),
 reviewed_at timestamptz, expires_at timestamptz NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(owner_id,logical_key,version),
 CHECK(status<>'published' OR (reviewer_id IS NOT NULL AND reviewed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS diagnostic_playbook_lookup ON diagnostic_playbooks(status,group_id,project_id,expires_at);

INSERT INTO schema_migrations(version) VALUES(10) ON CONFLICT DO NOTHING;
COMMIT;
