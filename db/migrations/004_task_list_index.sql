BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='60s';
SELECT pg_advisory_xact_lock(41762,4);
-- Match the stable order used by visible task lists. Existing owner/group
-- indexes remain sufficient; do not duplicate them without measured need.
CREATE INDEX IF NOT EXISTS idx_tasks_active_created
 ON tasks(created_at DESC,id DESC)
 WHERE deleted_at IS NULL;
INSERT INTO schema_migrations(version) VALUES(4) ON CONFLICT DO NOTHING;
COMMIT;
