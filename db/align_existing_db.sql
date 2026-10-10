-- One-off upgrade for a database created by the OLD migration chain (head 067963756649).
-- Brings the 'sonde' schema in line with app/models.py so it can be stamped with the new
-- squashed baseline. Idempotent: safe to re-run. Runs in a single transaction.
--
--   pg_dump weather_sonde > backup.sql                 # back up first
--   psql -d weather_sonde -f db/raw_schema.sql
--   psql -d weather_sonde -f db/align_existing_db.sql
--   flask db stamp --purge 0001_initial              # old revision ids no longer exist
--   flask db check                                     # expect "No new upgrade operations detected."
--
-- Naive timestamps are assumed to hold UTC wall-clock time. If your Pi ran in a local time
-- zone, edit the time zone in sonde._to_tz() below before running.

SET client_min_messages = warning;
BEGIN;

CREATE SCHEMA IF NOT EXISTS sonde;

CREATE OR REPLACE PROCEDURE sonde._to_tz(tbl text, col text) AS $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
         WHERE table_schema = 'sonde' AND table_name = tbl AND column_name = col)
       = 'timestamp without time zone' THEN
        EXECUTE format('ALTER TABLE sonde.%I ALTER COLUMN %I TYPE timestamptz USING %I AT TIME ZONE ''UTC''',
                       tbl, col, col);
    END IF;
END $$ LANGUAGE plpgsql;

CREATE OR REPLACE PROCEDURE sonde._not_null_if_clean(tbl text, col text) AS $$
DECLARE nulls bigint;
BEGIN
    EXECUTE format('SELECT count(*) FROM sonde.%I WHERE %I IS NULL', tbl, col) INTO nulls;
    IF nulls = 0 THEN
        EXECUTE format('ALTER TABLE sonde.%I ALTER COLUMN %I SET NOT NULL', tbl, col);
    ELSE
        RAISE WARNING 'sonde.%.% has % NULL row(s); left nullable', tbl, col, nulls;
    END IF;
END $$ LANGUAGE plpgsql;

-- users
ALTER TABLE sonde.users ALTER COLUMN password TYPE varchar(256);
CALL sonde._to_tz('users', 'created_at');

-- devices
CALL sonde._to_tz('devices', 'created_at');
ALTER TABLE sonde.devices ALTER COLUMN created_at DROP DEFAULT;

-- flights
CALL sonde._to_tz('flights', 'start_time');
CALL sonde._to_tz('flights', 'end_time');
UPDATE sonde.flights SET status = 'pre-flight' WHERE status IS NULL;
ALTER TABLE sonde.flights ALTER COLUMN status SET NOT NULL;
ALTER TABLE sonde.flights ALTER COLUMN mask DROP DEFAULT;

-- telemetry
ALTER TABLE sonde.telemetry
    DROP COLUMN IF EXISTS tx_ts,
    DROP COLUMN IF EXISTS dew_point,
    DROP COLUMN IF EXISTS device_sn,
    ADD COLUMN IF NOT EXISTS humidity double precision,
    ADD COLUMN IF NOT EXISTS hdop double precision,
    ADD COLUMN IF NOT EXISTS sats integer,
    ADD COLUMN IF NOT EXISTS processed_ts timestamptz,
    ADD COLUMN IF NOT EXISTS measurement_ts timestamptz;
ALTER TABLE sonde.telemetry ALTER COLUMN sats DROP DEFAULT;
CALL sonde._to_tz('telemetry', 'timestamp');
CALL sonde._to_tz('telemetry', 'processed_ts');
CALL sonde._to_tz('telemetry', 'measurement_ts');
CALL sonde._not_null_if_clean('telemetry', 'flight_id');
CREATE INDEX IF NOT EXISTS ix_telemetry_flight_timestamp ON sonde.telemetry (flight_id, "timestamp");

-- logs
ALTER TABLE sonde.logs ADD COLUMN IF NOT EXISTS level varchar(16) NOT NULL DEFAULT 'INFO';
ALTER TABLE sonde.logs ALTER COLUMN level DROP DEFAULT;
CALL sonde._to_tz('logs', 'timestamp');

-- flight_status
ALTER TABLE sonde.flight_status
    DROP COLUMN IF EXISTS transmission_age,
    DROP COLUMN IF EXISTS current_ascent_rate,
    DROP COLUMN IF EXISTS anchor_ts,
    ADD COLUMN IF NOT EXISTS last_ascent_rate double precision,
    ADD COLUMN IF NOT EXISTS release_ts timestamptz,
    ADD COLUMN IF NOT EXISTS release_altitude double precision,
    ADD COLUMN IF NOT EXISTS release_pressure double precision,
    ADD COLUMN IF NOT EXISTS burst_ts timestamptz,
    ADD COLUMN IF NOT EXISTS burst_pressure double precision,
    ADD COLUMN IF NOT EXISTS end_ts timestamptz,
    ADD COLUMN IF NOT EXISTS balloon_position double precision,
    ADD COLUMN IF NOT EXISTS parachute_position double precision,
    ADD COLUMN IF NOT EXISTS burst_position double precision,
    ADD COLUMN IF NOT EXISTS receiver_ok boolean,
    ADD COLUMN IF NOT EXISTS parser_ok boolean,
    ADD COLUMN IF NOT EXISTS sensor_ok boolean,
    ADD COLUMN IF NOT EXISTS signal_level varchar(6),
    ADD COLUMN IF NOT EXISTS packet_ok boolean,
    ADD COLUMN IF NOT EXISTS age_warn boolean,
    ADD COLUMN IF NOT EXISTS calibrated boolean,
    ADD COLUMN IF NOT EXISTS temp_low boolean,
    ADD COLUMN IF NOT EXISTS data_degrad boolean,
    ADD COLUMN IF NOT EXISTS gps_fix boolean,
    ADD COLUMN IF NOT EXISTS gps_degrad varchar(6);
CALL sonde._to_tz('flight_status', 'updated_at');
CALL sonde._to_tz('flight_status', 'release_ts');
CALL sonde._to_tz('flight_status', 'end_ts');
CALL sonde._not_null_if_clean('flight_status', 'flight_id');
-- The old analyzer stored 'burst' as a phase; the new one uses 'descent' after burst.
UPDATE sonde.flight_status SET flight_phase = 'descent' WHERE flight_phase = 'burst';

-- ground_reference (api_* and dew_point were never populated)
ALTER TABLE sonde.ground_reference
    DROP COLUMN IF EXISTS dew_point,
    DROP COLUMN IF EXISTS api_temperature,
    DROP COLUMN IF EXISTS api_pressure,
    DROP COLUMN IF EXISTS api_humidity,
    DROP COLUMN IF EXISTS api_location_name;
CALL sonde._to_tz('ground_reference', 'timestamp');
CALL sonde._not_null_if_clean('ground_reference', 'flight_id');

-- data_selection (never had a migration)
CREATE TABLE IF NOT EXISTS sonde.data_selection (
    id          serial PRIMARY KEY,
    flight_id   integer NOT NULL UNIQUE REFERENCES sonde.flights (id),
    start_ts    timestamptz NOT NULL,
    end_ts      timestamptz NOT NULL,
    exclusions  json,
    gap_info    json,
    verified_by varchar,
    created_at  timestamptz
);
CALL sonde._to_tz('data_selection', 'start_ts');
CALL sonde._to_tz('data_selection', 'end_ts');
CALL sonde._to_tz('data_selection', 'created_at');

-- alarms was never read or written; drop it only if it is empty.
DO $$
BEGIN
    IF to_regclass('sonde.alarms') IS NOT NULL THEN
        IF (SELECT count(*) FROM sonde.alarms) = 0 THEN
            DROP TABLE sonde.alarms;
        ELSE
            RAISE WARNING 'sonde.alarms has rows; left in place';
        END IF;
    END IF;
END $$;

-- Leftover from migration 57a3fe8af121
DROP TABLE IF EXISTS sonde.system_logs;

DROP PROCEDURE sonde._to_tz(text, text);
DROP PROCEDURE sonde._not_null_if_clean(text, text);

COMMIT;
