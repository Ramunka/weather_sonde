"""
Post-flight telemetry audit: finds time gaps and implausible values so the operator can
confirm a clean data window on the verify page.

Rows are ordered by device time, falling back to receive time when the sonde had no GPS time.
"""
from datetime import timezone

GAP_THRESHOLD_SECONDS = 5

# field -> (predicate that flags a value, reason)
OUTLIER_RULES = {
    "humidity":        (lambda v: v < 0 or v > 100, "out of range (0–100 %)"),
    "temperature":     (lambda v: abs(v) > 100, "extreme value (|T| > 100 °C)"),
    "pressure":        (lambda v: v <= 0 or v > 1100, "out of range (0–1100 hPa)"),
    "signal_strength": (lambda v: v < -130, "very weak signal (< −130 dBm)"),
}


def audit_rows(rows):
    """rows: iterable of objects with `ts` and the OUTLIER_RULES fields, in time order."""
    rows = list(rows)
    if not rows:
        return {"error": "No telemetry found for this flight."}

    gaps, outliers = [], []
    prev_ts = None
    for row in rows:
        ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
        if prev_ts is not None:
            delta = (ts - prev_ts).total_seconds()
            if delta > GAP_THRESHOLD_SECONDS:
                gaps.append({"start": prev_ts.isoformat(), "end": ts.isoformat(), "duration": int(delta)})
        prev_ts = ts

        for field, (is_bad, reason) in OUTLIER_RULES.items():
            value = getattr(row, field)
            if value is not None and is_bad(value):
                outliers.append({"timestamp": ts.isoformat(), "field": field, "value": value, "reason": reason})

    first, last = rows[0].ts, rows[-1].ts
    return {
        "total_points": len(rows),
        "gaps": gaps,
        "outliers": outliers,
        "start_ts": first.isoformat(),
        "end_ts": last.isoformat(),
    }


def audit_flight_telemetry(flight_id):
    from sqlalchemy import func
    from app.extensions import db
    from app.models import Telemetry

    ts = func.coalesce(Telemetry.measurement_ts, Telemetry.timestamp).label("ts")
    rows = (db.session.query(ts, Telemetry.humidity, Telemetry.temperature,
                             Telemetry.pressure, Telemetry.signal_strength)
            .filter(Telemetry.flight_id == flight_id)
            .order_by(ts, Telemetry.id)
            .all())
    return audit_rows(rows)


if __name__ == "__main__":
    import json
    import sys
    from app import create_app

    with create_app().app_context():
        print(json.dumps(audit_flight_telemetry(int(sys.argv[1])), indent=2))
