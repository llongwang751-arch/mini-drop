BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,5);
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority smallint NOT NULL DEFAULT 0
 CHECK (priority BETWEEN -10 AND 10);
CREATE INDEX IF NOT EXISTS idx_tasks_collection_priority
 ON tasks(target_agent_id,priority DESC,created_at,id)
 WHERE collection_status='QUEUED';
INSERT INTO schema_migrations(version) VALUES(5) ON CONFLICT DO NOTHING;
COMMIT;
