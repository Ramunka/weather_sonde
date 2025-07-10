#!/usr/bin/env python3
"""
ETL parser for raw.packets → sonde.telemetry (updated schema)

Reads unprocessed rows from raw.packets, parses each CSV line,
validates against active flights, computes dew point and measurement_ts
from the on-device UTC field, and then inserts into sonde.telemetry with:
  - timestamp           (when packet was received)
  - processed_ts        (when parser wrote the row)
  - measurement_ts      (UTC from device)
  - gps_latitude, gps_longitude, gps_altitude
  - pressure, temperature, dew_point, humidity
  - signal_strength
  - speed (NULL), ascent_rate (NULL)
  - tx_ts (NULL), device_sn
Finally marks raw.packets.processed = TRUE.
"""
import os
import math
import psycopg2
from datetime import datetime, timezone
import select

# Constants
gps_noise_threshold = 0.5
speed_noise_threshold = 0.5
DSN = os.getenv(
    "DATABASE_URL",
    "dbname=weather_sonde user=ingest_user password=strong_ingest_password host=localhost"
)

# State
_sample_history_by_device = {}

# Helpers
def parse_float(val):
    try:
        if val.strip().upper() == 'NAN':
            return None
        return float(val)
    except:
        return None


def generate_token(device_sn: int, mask: str) -> int:
    try:
        key = int(mask, 16)
    except ValueError:
        key = 0
    return (device_sn ^ key) & 0xFFFFFF

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def main():
    conn = psycopg2.connect(DSN)
    conn.set_session(autocommit=True)
    cur = conn.cursor()
    cur.execute("LISTEN packet_inserted;")
    print("Listening for new packets on channel 'packet_inserted'...")

    while True:
        if select.select([conn], [], [], 5) == ([], [], []):
            print("[idle] No new packets in last 5 seconds.")
        else:
            conn.poll()
            for notify in conn.notifies:
                print(f"[notify] {notify.channel}: {notify.payload}")
            conn.notifies.clear()

        cur.execute("""
            SELECT id, recv_ts, payload, rssi_dbm
              FROM raw.packets
             WHERE NOT processed
             ORDER BY id
             LIMIT 100;
        """)
        rows = cur.fetchall()
        if not rows:
            continue

        for raw_id, recv_ts, payload, rssi in rows:
            print(f"Processing raw.id={raw_id}")
            for line in payload.strip().splitlines():
                cols = line.split(',')
                if len(cols) < 11:
                    print(f"  Skipping malformed: {line!r}")
                    continue

                # Parse header
                try:
                    device_sn  = int(cols[0], 16)
                    token_recv = int(cols[1], 16)
                except ValueError:
                    print(f"  Invalid SN/token in: {line!r}")
                    continue

                # Active flight lookup
                cur.execute("""
                    SELECT f.id, f.mask
                      FROM sonde.flights f
                      JOIN sonde.devices d ON f.device_id=d.id
                     WHERE d.device_sn=%s
                       AND f.status IN ('in-flight','pre-flight')
                     LIMIT 1;
                """, (format(device_sn, 'X'),))
                flight = cur.fetchone()
                if not flight:
                    print(f"  No active flight for 0x{device_sn:X}")
                    continue

                flight_id, mask = flight
                expected = generate_token(device_sn, mask)
                if token_recv != expected:
                    print(f"  Token mismatch: got 0x{token_recv:X}, exp 0x{expected:X}")
                    continue

                utc_str = cols[2]
                try:
                    # e.g. '2025-06-18T19:05:09Z'
                    measurement_ts = datetime.strptime(utc_str, "%Y-%m-%dT%H:%M:%SZ")
                    measurement_ts = measurement_ts.replace(tzinfo=timezone.utc)
                except Exception:
                    measurement_ts = None

                # Parse sensor fields
                temp_c   = parse_float(cols[3])
                humidity = parse_float(cols[4])
                pres     = parse_float(cols[5])
                lat      = parse_float(cols[6])
                lng      = parse_float(cols[7])
                alt_m    = parse_float(cols[8])
                hdop     = parse_float(cols[9])
                sats     = parse_float(cols[10])

                # Speed & ascent (unchanged)
                history = _sample_history_by_device.setdefault(device_sn, [])
                if alt_m is not None and lat is not None and lng is not None:
                    history.append((measurement_ts, alt_m, lat, lng))
                    if len(history)>4: history.pop(0)
                ascent_rate = None
                if len(history)>=2:
                    rates = []
                    for i in range(len(history)-1):
                        t1, a1, _, _ = history[i]
                        t2, a2, _, _ = history[i+1]
                        if t1 and t2 and a1 is not None and a2 is not None:
                            dt = (t2 - t1).total_seconds()
                            if dt>0: rates.append((a2-a1)/dt)
                    if rates:
                        avg = sum(rates)/len(rates)
                        ascent_rate=round(avg,1) if abs(avg)>=gps_noise_threshold else 0.0

                ground_speed = None
                if len(history)>=2:
                    speeds=[]
                    for i in range(len(history)-1):
                        t1, _, lat1, lon1 = history[i]
                        t2, _, lat2, lon2 = history[i+1]
                        if t1 and t2:
                            dt=(t2-t1).total_seconds()
                            if dt>0:
                                dist = haversine_meters(lat1,lon1,lat2,lon2)
                                speeds.append(dist/dt)
                    if speeds:
                        avg_ms=sum(speeds)/len(speeds)
                        gs=avg_ms*1.94384
                        ground_speed=round(gs,1) if abs(gs)>=speed_noise_threshold else 0.0

                processed_ts = datetime.now(timezone.utc).replace(microsecond=0)

                # Insert telemetry
                cur.execute("""
                    INSERT INTO sonde.telemetry (
                      flight_id, timestamp, gps_latitude, gps_longitude,
                      gps_altitude, pressure, temperature,
                      signal_strength, speed, ascent_rate,
                      humidity, hdop, sats,
                      processed_ts, measurement_ts
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (
                    flight_id,
                    recv_ts,
                    lat, lng,
                    int(alt_m) if alt_m is not None else None,
                    int(pres) if pres is not None else None,
                    temp_c,
                    rssi,
                    ground_speed, ascent_rate,
                    humidity, hdop, sats,
                    processed_ts, measurement_ts
                ))

            # mark processed
            cur.execute("UPDATE raw.packets SET processed=TRUE WHERE id=%s",
                        (raw_id,))

        print("Batch complete.")

if __name__=='__main__':
    main()
