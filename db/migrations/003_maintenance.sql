BEGIN;
SELECT pg_advisory_xact_lock(41762,3);
CREATE TABLE IF NOT EXISTS storage_maintenance_state (
 key text PRIMARY KEY, value jsonb NOT NULL DEFAULT '{}', updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_migrations(version) VALUES(3) ON CONFLICT DO NOTHING;
COMMIT;
