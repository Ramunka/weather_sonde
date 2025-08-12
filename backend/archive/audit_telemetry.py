# audit_telemetry.py

import psycopg2
from datetime import timedelta

DSN = "dbname=weather_sonde user=ingest_user password=strong_ingest_password host=localhost"
GAP_THRESHOLD_SECONDS = 5


def audit_flight_telemetry(flight_id):
    conn = psycopg2.connect(DSN)
    cur = conn.cursor()

    cur.execute("""
                SELECT measurement_ts, gps_altitude, temperature, humidity, pressure, signal_strength
                FROM sonde.telemetry
                WHERE flight_id = %s
                ORDER BY measurement_ts ASC
                """, (flight_id,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {"error": "No telemetry found."}

    total_points = len(rows)
    gaps = []
    outliers = []

    prev_ts = None
    for row in rows:
        ts, alt, temp, hum, pres, signal = row

        # Gap detection
        if prev_ts and (ts - prev_ts).total_seconds() > GAP_THRESHOLD_SECONDS:
            gaps.append({
                "start": prev_ts.isoformat(),
                "end": ts.isoformat(),
                "duration": int((ts - prev_ts).total_seconds())
            })
        prev_ts = ts

        # Outlier detection
        if hum is not None and (hum < 0 or hum > 100):
            outliers.append({"timestamp": ts.isoformat(), "field": "humidity", "value": hum, "reason": "out-of-range"})
        if temp is not None and abs(temp) > 100:
            outliers.append(
                {"timestamp": ts.isoformat(), "field": "temperature", "value": temp, "reason": "extreme value"})
        if signal is not None and signal < -130:
            outliers.append({"timestamp": ts.isoformat(), "field": "signal_strength", "value": signal,
                             "reason": "very weak signal"})

    return {
        "total_points": total_points,
        "gaps": gaps,
        "outliers": outliers,
        "start_ts": rows[0][0].isoformat(),
        "end_ts": rows[-1][0].isoformat()
    }


# Optional: allow CLI testing
if __name__ == "__main__":
    import sys

    result = audit_flight_telemetry(int(sys.argv[1]))
    import json

    print(json.dumps(result, indent=2))
