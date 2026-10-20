-- 为授权任务列表的稳定排序增加索引，不改变哪些用户能看到哪些任务。
-- 索引是性能优化，不能替代API的owner/group/share范围过滤。

BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,4);
-- 索引顺序与授权任务列表的稳定排序一致；既有owner/group
-- 索引仍能承担对应过滤，未经实际测量不重复建立相同用途索引。
CREATE INDEX IF NOT EXISTS idx_tasks_active_created
 ON tasks(created_at DESC,id DESC)
 WHERE deleted_at IS NULL;
INSERT INTO schema_migrations(version) VALUES(4) ON CONFLICT DO NOTHING;
COMMIT;
