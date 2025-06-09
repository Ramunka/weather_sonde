#!/usr/bin/env python3
"""
ETL parser for raw.packets → sonde.telemetry (updated schema)

Reads unprocessed rows from raw.packets, parses each CSV line,
validates against active flights, computes dew point and measurement_ts,
and then inserts into sonde.telemetry with:
  - timestamp           (when packet was received)
  - processed_ts        (when parser wrote the row)
  - measurement_ts      (flight.start_time + tx_ts seconds)
  - gps_latitude, gps_longitude, gps_altitude
  - pressure, temperature, dew_point, humidity
  - battery (NULL), voltage (NULL)
  - signal_strength
  - speed (NULL), ascent_rate (NULL)
  - tx_ts, device_sn
Finally marks raw.packets.processed = TRUE.

Place this script in backend/etl/parse_raw.py
"""
import os
import math
import psycopg2
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm.base import state_attribute_str

gps_noise_threshold = 0.5
speed_noise_threshold = 0.5

_sample_history_by_device = {}   # device_sn → [ (tx1, alt1, lat1, lng1), … ]
# -- Configuration ---------------------------------------------------------------
# Database connection string via env or fallback
DSN = os.getenv(
    "DATABASE_URL",
    "dbname=weather_sonde user=ingest_user password=strong_ingest_password host=localhost"
)

# -- Helpers ---------------------------------------------------------------------
def parse_float(val):
    """Convert a string to float, or return None if invalid/NAN."""
    try:
        f = float(val)
        if val.strip().upper() == 'NAN':
            return None
        return f
    except Exception:
        return None

def compute_dew_point(temp_c, rh_pct):
    """
    Compute dew point (°C) from temperature (°C) and relative humidity (%),
    using the Magnus formula. Return None if inputs are invalid.
    """
    if temp_c is None or rh_pct is None:
        return None
    # Magnus‐Tetens constants (over water)
    a = 17.27
    b = 237.7
    try:
        alpha = ((a * temp_c) / (b + temp_c)) + math.log(rh_pct / 100.0)
        dew = (b * alpha) / (a - alpha)
        return dew
    except Exception:
        return None

def generate_token(device_sn: int, mask: str) -> int:
    """
    Compute token exactly as on Arduino:
      (device_sn ^ int(mask, 16)) & 0xFFFFFF
    where mask is a hex string like '5A5A5A'.
    """
    try:
        key = int(mask, 16)
    except ValueError:
        key = 0
    return (device_sn ^ key) & 0xFFFFFF

# Haversine helper to compute distance (meters) between two lat/lon points:
def haversine_meters(lat1, lon1, lat2, lon2):
    # approximate radius of Earth in meters
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c
# -- Main ------------------------------------------------------------------------
def main():
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()

    # 2) Fetch up to 100 unprocessed raw packets
    cur.execute("""
        SELECT id, recv_ts, payload, rssi_dbm
          FROM raw.packets
         WHERE NOT processed
         ORDER BY id
         LIMIT 100;
    """)
    rows = cur.fetchall()
    if not rows:
        print("No new raw packets to process.")
        return

    for raw_id, recv_ts, payload, rssi in rows:
        print(f"Processing raw.id={raw_id}")

        # Split into individual CSV lines (batch may contain multiple)
        for line in payload.strip().splitlines():
            fields = line.split(',')
            if len(fields) < 11:
                print(f"  Skipping malformed line: {line!r}")
                continue

            # 3) Parse device serial (hex) and token (hex)
            try:
                device_sn  = int(fields[0], 16)
                token_recv = int(fields[1], 16)
            except ValueError:
                print(f"  Invalid header in line: {line!r}")
                continue

            # 4) Lookup active flight for this device SN, retrieving start_time
            cur.execute("""
                SELECT f.id, f.mask, f.start_time
                  FROM sonde.flights AS f
                  JOIN sonde.devices AS d
                    ON f.device_id = d.id
                 WHERE d.device_sn = %s
                   AND f.status = 'in-flight'
                 LIMIT 1;
            """, (format(device_sn, 'X'),))  # compare without '0x' prefix
            flight = cur.fetchone()
            if not flight:
                print(f"  No active flight for device 0x{device_sn:X}")
                continue
            flight_id, mask, start_time = flight

            # 5) Verify token
            expected = generate_token(device_sn, mask)
            if token_recv != expected:
                print(f"  Token mismatch: recv=0x{token_recv:X}, exp=0x{expected:X}")
                continue

            # 6) Parse sensor fields
            try:
                tx_sec = int(fields[2])
            except ValueError:
                tx_sec = None
            temp_c   = parse_float(fields[3])
            humidity = parse_float(fields[4])
            pres     = parse_float(fields[5])
            lat      = parse_float(fields[6])
            lng      = parse_float(fields[7])
            alt_m    = parse_float(fields[8])
            hdop     = parse_float(fields[9])
            sats     = parse_float(fields[10])

            # .) ascent and gs
            history = _sample_history_by_device.setdefault(device_sn, [])
            if alt_m is not None and tx_sec is not None and lat is not None and lng is not None:
                history.append((tx_sec, alt_m, lat, lng))
                if len(history) > 4:
                    history.pop(0)

            # Compute smoothed ascent_rate via pairwise‐average over the 4‐point window
            ascent_rate = None
            if len(history) >= 2:
                rates = []
                # iterate over consecutive pairs in history, compute each short‐interval rate
                for i in range(len(history) - 1):
                    t1, a1, _, _ = history[i]
                    t2, a2, _, _ = history[i + 1]
                    dt = t2 - t1
                    if dt > 0:
                        rates.append((a2 - a1) / float(dt))
                if rates:
                    ascent_rate = round(sum(rates) / len(rates),1)
                    if ascent_rate is not None and abs(ascent_rate) < gps_noise_threshold:
                        ascent_rate = 0.0

            ground_speed = None
            if len(history) >= 2:
                speeds = []
                # Compute pairwise ground speeds (m/s) for each consecutive pair
                for i in range(len(history) - 1):
                    t1, _, lat1, lon1 = history[i]
                    t2, _, lat2, lon2 = history[i + 1]
                    dt = t2 - t1
                    if dt > 0:
                        dist_m = haversine_meters(lat1, lon1, lat2, lon2)
                        speeds.append(dist_m / float(dt))
                # Average those pairwise speeds, convert to knots, then round to one decimal
                if speeds:
                    avg_ms = sum(speeds) / len(speeds)
                    ground_speed = round(avg_ms * 1.94384, 1)  # in knots, 1 decimal place
                    if ground_speed is not None and abs(ground_speed) < gps_noise_threshold:
                        ground_speed = 0.0


            # 7) Compute dew point from (temp_c, humidity)
            dew_pt = compute_dew_point(temp_c, humidity)
            if dew_pt is not None:
                dew_pt = round(dew_pt, 2)

            # 8) Compute absolute measurement time: start_time + tx_sec seconds
            if start_time is not None and tx_sec is not None:
                measurement_ts = start_time + timedelta(seconds=tx_sec)
            else:
                measurement_ts = None

            # 9) Prepare processed_ts = now()
            processed_ts = datetime.now(timezone.utc)

            # 10) Insert into sonde.telemetry
            cur.execute("""
                INSERT INTO sonde.telemetry (
                  flight_id,
                  timestamp,         -- time packet was received
                  gps_latitude,
                  gps_longitude,
                  gps_altitude,
                  pressure,
                  temperature,
                  dew_point,
                  signal_strength,
                  speed,
                  ascent_rate,
                  tx_ts,             -- raw device‐side seconds
                  device_sn,
                  humidity,
                  hdop,
                  sats,
                  processed_ts,      -- parser‐side time
                  measurement_ts     -- computed from flight.start_time
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                flight_id,
                recv_ts,                            # timestamp (packet received)
                lat,                                # gps_latitude
                lng,                                # gps_longitude
                int(alt_m) if alt_m is not None else None,  # gps_altitude
                int(pres) if pres is not None else None,    # pressure
                temp_c,                             # temperature
                dew_pt,                             # dew_point
                rssi,                               # signal_strength
                ground_speed,                       # ground speed
                ascent_rate,                        # ascent_rate
                tx_sec,                             # tx_ts
                format(device_sn, 'X'),             # device_sn (no '0x' prefix)
                humidity,                           # humidity
                hdop,
                sats,
                processed_ts,                      # processed_ts
                measurement_ts                     # measurement_ts
            ))

        # 11) Mark raw.packets row as processed
        cur.execute(
            "UPDATE raw.packets SET processed = TRUE WHERE id = %s",
            (raw_id,)
        )

    print("Batch processing complete.")

if __name__ == '__main__':
    main()
