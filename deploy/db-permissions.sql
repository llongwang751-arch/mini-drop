-- Execute as the migration/object-owner administrator on PostgreSQL 17.
-- The password is read inside PostgreSQL from a read-only Docker secret.
-- Do not expand it in a shell, a connection argument, or a generated SQL file.
BEGIN;
SET LOCAL search_path = pg_catalog;
SET LOCAL password_encryption = 'scram-sha-256';
SET LOCAL log_statement = 'none';
SET LOCAL log_duration = off;
SET LOCAL log_min_duration_statement = -1;
SET LOCAL log_min_error_statement = 'panic';
SET LOCAL log_error_verbosity = 'terse';

DO $drop_runtime_role$
DECLARE
    app_password text;
    membership record;
BEGIN
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('drop-app-permissions-v1', 0));
    app_password := pg_catalog.btrim(
        pg_catalog.pg_read_file('/run/secrets/app_database_password'), E' \t\r\n');
    IF pg_catalog.octet_length(app_password) < 16
       OR pg_catalog.octet_length(app_password) > 1024 THEN
        RAISE EXCEPTION 'Runtime database password length is invalid';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'drop_app') THEN
        CREATE ROLE drop_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
    EXECUTE pg_catalog.format(
        'ALTER ROLE drop_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L VALID UNTIL %L',
        app_password, 'infinity');
    app_password := NULL;

    -- Remove inherited or SET ROLE elevation left by a previous configuration.
    FOR membership IN
        SELECT granted.rolname AS role_name, grantor.rolname AS grantor_name
        FROM pg_catalog.pg_auth_members AS memberships
        JOIN pg_catalog.pg_roles AS granted ON granted.oid = memberships.roleid
        JOIN pg_catalog.pg_roles AS member ON member.oid = memberships.member
        JOIN pg_catalog.pg_roles AS grantor ON grantor.oid = memberships.grantor
        WHERE member.rolname = 'drop_app'
    LOOP
        EXECUTE pg_catalog.format('REVOKE %I FROM drop_app GRANTED BY %I',
            membership.role_name, membership.grantor_name);
    END LOOP;

    EXECUTE pg_catalog.format('REVOKE ALL PRIVILEGES ON DATABASE %I FROM drop_app',
        pg_catalog.current_database());
    EXECUTE pg_catalog.format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC',
        pg_catalog.current_database());
    EXECUTE pg_catalog.format('GRANT CONNECT ON DATABASE %I TO drop_app',
        pg_catalog.current_database());
EXCEPTION WHEN OTHERS THEN
    app_password := NULL;
    -- Do not propagate a nested ALTER ROLE statement containing the secret.
    RAISE EXCEPTION 'Runtime database role initialization failed; check administrator privileges and the protected secret'
        USING ERRCODE = 'P0001';
END;
$drop_runtime_role$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL PRIVILEGES ON SCHEMA public FROM drop_app;
GRANT USAGE ON SCHEMA public TO drop_app;

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM drop_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO drop_app;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM drop_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO drop_app;

-- Defaults apply to objects subsequently created by this migration owner.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON TABLES FROM drop_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO drop_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL PRIVILEGES ON SEQUENCES FROM drop_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO drop_app;
-- Audit history is append-only for every runtime component.
REVOKE UPDATE, DELETE, TRUNCATE ON public.audit_logs FROM drop_app;
COMMIT;
