-- Optional: database roles with least privilege. Run as a superuser AFTER
-- `flask db upgrade` and db/raw_schema.sql. Change the passwords first.
--
--   sonde_user   web app, analyzer, supervisor, simulator (owns day-to-day data)
--   ingest_user  receiver + parser only (INGEST_DATABASE_URL)
--
--   sudo -u postgres psql -d weather_sonde -f db/roles.sql

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sonde_user') THEN
        CREATE ROLE sonde_user LOGIN PASSWORD 'change-me';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ingest_user') THEN
        CREATE ROLE ingest_user LOGIN PASSWORD 'change-me';
    END IF;
END $$;

-- Application role: full read/write on both schemas (it also runs migrations).
GRANT USAGE, CREATE ON SCHEMA sonde, raw TO sonde_user;
GRANT ALL ON ALL TABLES    IN SCHEMA sonde, raw TO sonde_user;
GRANT ALL ON ALL SEQUENCES IN SCHEMA sonde, raw TO sonde_user;
GRANT ALL ON TABLE public.alembic_version TO sonde_user;

-- Ingest role: write packets, mark them processed, store telemetry.
GRANT USAGE ON SCHEMA raw, sonde TO ingest_user;
GRANT SELECT, INSERT, UPDATE ON raw.packets TO ingest_user;
GRANT USAGE ON SEQUENCE raw.packets_id_seq TO ingest_user;
GRANT SELECT ON sonde.flights, sonde.devices TO ingest_user;
GRANT INSERT ON sonde.telemetry TO ingest_user;
GRANT USAGE ON SEQUENCE sonde.telemetry_id_seq TO ingest_user;
