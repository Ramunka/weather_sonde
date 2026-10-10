-- Raw ingest schema: packets straight off the radio, plus the supervisor heartbeat.
-- Not managed by Alembic. Idempotent: safe to re-run on an existing database.
--
--   psql -d weather_sonde -f db/raw_schema.sql

SET client_min_messages = warning;

CREATE SCHEMA IF NOT EXISTS raw;

-- One row per LoRa packet. The parser sets processed = TRUE once it has been handled.
CREATE TABLE IF NOT EXISTS raw.packets (
    id         bigserial PRIMARY KEY,
    recv_ts    timestamptz NOT NULL DEFAULT now(),
    payload    text        NOT NULL,
    rssi_dbm   real,
    snr_db     real,
    processed  boolean     NOT NULL DEFAULT FALSE
);

-- Upgrade path for databases created from the old pg_dump (naive recv_ts, no processed flag).
ALTER TABLE raw.packets ADD COLUMN IF NOT EXISTS processed boolean NOT NULL DEFAULT FALSE;
DO $$
BEGIN
    IF (SELECT data_type FROM information_schema.columns
         WHERE table_schema = 'raw' AND table_name = 'packets' AND column_name = 'recv_ts')
       = 'timestamp without time zone' THEN
        ALTER TABLE raw.packets ALTER COLUMN recv_ts TYPE timestamptz USING recv_ts AT TIME ZONE 'UTC';
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_raw_packets_recv_ts ON raw.packets (recv_ts);
CREATE INDEX IF NOT EXISTS idx_raw_packets_unprocessed ON raw.packets (id) WHERE NOT processed;

-- Wake the parser immediately on insert (it also polls every 5 s as a fallback).
CREATE OR REPLACE FUNCTION raw.notify_packet_inserted() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('packet_inserted', NEW.id::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS packets_notify ON raw.packets;
CREATE TRIGGER packets_notify
    AFTER INSERT ON raw.packets
    FOR EACH ROW EXECUTE FUNCTION raw.notify_packet_inserted();

-- Single-row process heartbeat written by supervisor.py (id = 1).
CREATE TABLE IF NOT EXISTS raw.system_status (
    id             integer     PRIMARY KEY,
    receiver_state text        NOT NULL DEFAULT 'idle',
    parser_state   text        NOT NULL DEFAULT 'idle',
    updated_at     timestamptz NOT NULL DEFAULT now()
);
