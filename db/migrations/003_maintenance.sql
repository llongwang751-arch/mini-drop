-- 追加维护作业需要的持久化结构，使对象保留、完整性与运维结果有记录可追踪。
-- 维护逻辑必须按任务/制品状态核对归属，不能仅根据对象名字直接删除。

BEGIN;
SELECT pg_advisory_xact_lock(41762,3);
CREATE TABLE IF NOT EXISTS storage_maintenance_state (
 key text PRIMARY KEY, value jsonb NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations(version) VALUES(3) ON CONFLICT DO NOTHING;
COMMIT;
