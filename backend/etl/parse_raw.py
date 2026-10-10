#!/usr/bin/env python3
"""
ETL parser: raw.packets → sonde.telemetry

Waits for NOTIFY on 'packet_inserted' (and polls every 5 s as a fallback), reads
unprocessed rows from raw.packets, parses each CSV record (see backend/protocol.py),
authenticates it against active flights via the SN/mask token, derives ascent rate
and ground speed, and inserts one sonde.telemetry row per record.

Each packet is handled in its own transaction. A packet that fails to insert is
rolled back, logged and still marked processed, so one bad packet can't stall the queue.
"""
import select
import sys
import time
from datetime import timezone

import psycopg2

from config import INGEST_DATABASE_URL, PG_OPTIONS, psycopg2_dsn
from backend.protocol import RateEstimator, generate_token, parse_record

POLL_SECONDS = 5
BATCH_SIZE = 100

# Rate history per (device, flight) so a new flight starts with a clean window.
_estimators = {}


def find_flight(cur, record):
    """Return the id of the active flight whose mask produces this record's token."""
    cur.execute("""
        SELECT f.id, f.mask
          FROM sonde.flights f
          JOIN sonde.devices d ON f.device_id = d.id
         WHERE d.device_sn = %s
           AND f.status IN ('pre-flight', 'flight')
    """, (format(record.device_sn, "X"),))
    candidates = cur.fetchall()
    for flight_id, mask in candidates:
        if generate_token(record.device_sn, mask) == record.token:
            return flight_id
    print(f"  No matching flight for SN 0x{record.device_sn:X} token 0x{record.token:06X} "
          f"({len(candidates)} active flight(s) checked)")
    return None


def insert_record(cur, flight_id, record, recv_ts, rssi):
    estimator = _estimators.setdefault((record.device_sn, flight_id), RateEstimator())
    ascent_rate, ground_speed = estimator.add(
        record.measurement_ts, record.altitude, record.latitude, record.longitude)

    cur.execute("""
        INSERT INTO sonde.telemetry (
            flight_id, timestamp, measurement_ts, processed_ts,
            gps_latitude, gps_longitude, gps_altitude, hdop, sats,
            pressure, temperature, humidity,
            signal_strength, speed, ascent_rate
        ) VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        flight_id, recv_ts, record.measurement_ts,
        record.latitude, record.longitude,
        round(record.altitude) if record.altitude is not None else None,
        record.hdop, record.sats,
        round(record.pressure) if record.pressure is not None else None,
        record.temperature, record.humidity,
        round(rssi) if rssi is not None else None,
        ground_speed, ascent_rate,
    ))


def process_packet(cur, recv_ts, payload, rssi):
    inserted = 0
    for line in payload.strip().splitlines():
        try:
            record = parse_record(line)
        except ValueError as exc:
            print(f"  Skipping malformed record {line!r}: {exc}")
            continue
        flight_id = find_flight(cur, record)
        if flight_id is not None:
            insert_record(cur, flight_id, record, recv_ts, rssi)
            inserted += 1
    return inserted


def drain(conn):
    """Process every unprocessed packet; returns the number handled."""
    handled = 0
    while True:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, recv_ts, payload, rssi_dbm
                  FROM raw.packets
                 WHERE NOT processed
                 ORDER BY id
                 LIMIT %s
            """, (BATCH_SIZE,))
            rows = cur.fetchall()
        conn.commit()
        if not rows:
            return handled

        for raw_id, recv_ts, payload, rssi in rows:
            if recv_ts is not None and recv_ts.tzinfo is None:
                recv_ts = recv_ts.replace(tzinfo=timezone.utc)
            try:
                with conn.cursor() as cur:
                    n = process_packet(cur, recv_ts, payload, rssi)
                    cur.execute("UPDATE raw.packets SET processed = TRUE WHERE id = %s", (raw_id,))
                conn.commit()
                print(f"raw.id={raw_id}: {n} record(s) stored")
            except psycopg2.Error as exc:
                conn.rollback()
                print(f"raw.id={raw_id}: failed ({exc.pgerror or exc}); marking processed and skipping",
                      file=sys.stderr)
                with conn.cursor() as cur:
                    cur.execute("UPDATE raw.packets SET processed = TRUE WHERE id = %s", (raw_id,))
                conn.commit()
            handled += 1


def main():
    conn = psycopg2.connect(psycopg2_dsn(INGEST_DATABASE_URL), options=PG_OPTIONS)
    listener = psycopg2.connect(psycopg2_dsn(INGEST_DATABASE_URL), options=PG_OPTIONS)
    listener.autocommit = True
    with listener.cursor() as cur:
        cur.execute("LISTEN packet_inserted;")
    print("Parser listening on channel 'packet_inserted'")

    idle_since = time.monotonic()
    while True:
        if drain(conn):
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since >= 60:
            print("[idle] no new packets in the last 60 s")
            idle_since = time.monotonic()

        if select.select([listener], [], [], POLL_SECONDS) != ([], [], []):
            listener.poll()
            listener.notifies.clear()


if __name__ == "__main__":
    main()
