#!/usr/bin/env python3
"""
Analyzer: sonde.telemetry → sonde.flight_status

Once a second, for every active flight (pre-flight or flight) it computes:
  * the flight phase: pre-flight → ascent → descent → landed (burst is the ascent→descent event)
  * burst time/altitude/pressure (taken at the observed peak), max altitude, min pressure
  * pressure-gauge marker positions for the dashboard
  * the alert lights (receiver, parser, sensor, signal, packet, age, calibrated, temp low,
    measurement degraded, GPS fix, GPS degraded)
and writes them to the flight's single flight_status row. Phase changes and bursts are
also written to sonde.logs.

Per-flight history lives in a FlightTracker that survives between loops. A tracker only
consumes telemetry rows it hasn't seen, so polling faster than packets arrive doesn't skew
the moving averages. FlightTracker has no DB dependencies and is covered by tests/.
"""
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

# ── Thresholds ────────────────────────────────────────────────────────────────
TOP_PRESSURE_HPA = 100    # top of the dashboard pressure gauge
BOT_PRESSURE_HPA = 1000   # bottom of the gauge

RATE_WINDOW     = 5       # samples in the ascent-rate moving average
ASCENT_RATE     = 0.6     # m/s, average above this = climbing
DESCENT_RATE    = -0.6    # m/s, average below this = falling
BURST_RATE      = -3.0    # m/s, a single sample below this during ascent = burst
LANDED_RATE     = 0.3     # m/s, |average| below this over a full window = landed
ASCENT_MIN_GAIN = 5       # m above ground before ascent is confirmed

SIG_GREEN  = -85          # dBm, worst of the last SIGNAL_WINDOW samples
SIG_YELLOW = -100
SIGNAL_WINDOW = 5

TEMP_LOW_THRESH = -40     # °C
TEMP_LOW_COUNT  = 3       # consecutive readings

AGE_WARN_SEC     = 10     # measurement older than this lights AGE
PACKET_GAP_SEC   = 10     # gap between received packets that lights PACKET
CAL_AGE_SEC      = 300    # pre-flight calibration must be fresher than this
PROCESS_STALE_SEC = 15    # supervisor heartbeat older than this = process unknown

PRESSURE_RANGE = (260, 1260)  # LPS22 rated range, hPa
HDOP_YELLOW, HDOP_RED = 3, 6
MIN_SATS = 4

LOOP_SECONDS = 1


def as_utc(ts):
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def pressure_to_percent(p_hpa):
    if p_hpa is None:
        return None
    frac = (BOT_PRESSURE_HPA - p_hpa) / (BOT_PRESSURE_HPA - TOP_PRESSURE_HPA)
    return min(max(frac * 100, 0), 100)


@dataclass
class Sample:
    id: int
    received: datetime
    measured: Optional[datetime]
    altitude: Optional[float]
    pressure: Optional[float]
    temperature: Optional[float]
    humidity: Optional[float]
    latitude: Optional[float]
    longitude: Optional[float]
    hdop: Optional[float]
    sats: Optional[int]
    rssi: Optional[float]
    ascent_rate: Optional[float]

    @classmethod
    def from_row(cls, t):
        return cls(
            id=t.id, received=as_utc(t.timestamp), measured=as_utc(t.measurement_ts),
            altitude=t.gps_altitude, pressure=t.pressure, temperature=t.temperature,
            humidity=t.humidity, latitude=t.gps_latitude, longitude=t.gps_longitude,
            hdop=t.hdop, sats=t.sats, rssi=t.signal_strength, ascent_rate=t.ascent_rate,
        )

    @property
    def time(self):
        return self.measured or self.received


class FlightTracker:
    """In-memory state machine for one flight."""

    def __init__(self, phase="pre-flight", burst_detected=False, ground_alt=None,
                 peak_alt=None, min_pressure=None):
        self.phase = phase if phase in ("pre-flight", "ascent", "descent", "landed") else "pre-flight"
        self.burst_detected = bool(burst_detected)
        self.ground_alt = ground_alt
        self.rates = deque(maxlen=RATE_WINDOW)
        self.temps = deque(maxlen=TEMP_LOW_COUNT)
        self.rssi = deque(maxlen=SIGNAL_WINDOW)
        self.last_id = None
        self.last_received = None
        self.packet_gap = None
        self.latest: Optional[Sample] = None
        # Peak of the flight: (altitude, pressure, time). Burst is reported here.
        self.peak = (peak_alt, None, None)
        self.min_pressure = min_pressure

    @property
    def avg_rate(self):
        return sum(self.rates) / len(self.rates) if self.rates else 0.0

    def update(self, sample: Sample, released: bool):
        """Consume one new telemetry sample. Returns a list of (level, message) events."""
        if sample.id == self.last_id:
            return []
        self.last_id = sample.id
        self.latest = sample
        events = []

        if self.last_received is not None:
            self.packet_gap = (sample.received - self.last_received).total_seconds()
        self.last_received = sample.received

        self.rates.append(sample.ascent_rate or 0.0)
        self.temps.append(sample.temperature)
        self.rssi.append(sample.rssi)

        if sample.altitude is not None:
            if self.ground_alt is None:
                self.ground_alt = sample.altitude
            if self.peak[0] is None or sample.altitude >= self.peak[0]:
                self.peak = (sample.altitude, sample.pressure, sample.time)
        if sample.pressure is not None and (self.min_pressure is None or sample.pressure < self.min_pressure):
            self.min_pressure = sample.pressure

        if released:
            events.extend(self._advance_phase(sample))
        return events

    def _advance_phase(self, s):
        gain = (s.altitude - self.ground_alt) if s.altitude is not None and self.ground_alt is not None else 0
        rate = s.ascent_rate or 0.0
        avg = self.avg_rate

        if self.phase == "pre-flight":
            if avg > ASCENT_RATE and gain > ASCENT_MIN_GAIN:
                self.phase = "ascent"
                return [("INFO", f"Ascent detected at {_alt(s.altitude)}.")]
            if avg < DESCENT_RATE and gain > ASCENT_MIN_GAIN:
                # Joined mid-flight (e.g. analyzer restarted after burst).
                self.phase = "descent"
                return [("INFO", f"Descent detected at {_alt(s.altitude)}.")]

        elif self.phase == "ascent":
            # Parser rates are averaged, so after burst they ramp down; accept either a sharp
            # single sample or a sustained negative average.
            if rate < BURST_RATE or avg < DESCENT_RATE:
                self.phase = "descent"
                self.burst_detected = True
                alt, pres, _ = self.peak
                return [("WARNING", f"Burst detected at {_alt(alt)} / {_hpa(pres)}.")]

        elif self.phase == "descent":
            if len(self.rates) == RATE_WINDOW and abs(avg) < LANDED_RATE:
                self.phase = "landed"
                return [("INFO", f"Landing detected at {_alt(s.altitude)}.")]

        return []


def _alt(value):
    return f"{value:.0f} m" if value is not None else "unknown altitude"


def _hpa(value):
    return f"{value:.0f} hPa" if value is not None else "unknown pressure"


def measurement_age(sample, now):
    if sample is None:
        return None
    return max(0, int((now - sample.time).total_seconds()))


def signal_level(rssi_values):
    values = [r for r in rssi_values if r is not None]
    if not values:
        return None
    worst = min(values)
    if worst < SIG_YELLOW:
        return "red"
    if worst < SIG_GREEN:
        return "yellow"
    return "green"


def gps_degradation(sample):
    if sample.latitude is None or sample.longitude is None:
        return None
    if (sample.hdop is not None and sample.hdop > HDOP_RED) or (sample.sats is not None and sample.sats < MIN_SATS):
        return "red"
    if sample.hdop is not None and sample.hdop > HDOP_YELLOW:
        return "yellow"
    return None


def process_ok(state, updated_at, now):
    return state == "running" and updated_at is not None and (now - as_utc(updated_at)).total_seconds() < PROCESS_STALE_SEC


def apply_to_status(status, tracker, now, released, calibration_ts, release_ts, sysstat):
    """Copy tracker results and alert lights onto a FlightStatus row."""
    s = tracker.latest
    age = measurement_age(s, now)

    status.flight_phase = tracker.phase
    status.measurement_age = age
    status.min_pressure = tracker.min_pressure
    status.max_altitude = tracker.peak[0]

    if tracker.burst_detected and not status.burst_detected:
        status.burst_detected = True
        status.burst_altitude, status.burst_pressure, status.burst_ts = tracker.peak

    # Gauge markers
    status.balloon_position = status.parachute_position = status.burst_position = None
    if s is not None:
        status.last_ascent_rate = s.ascent_rate
        if tracker.phase == "ascent":
            status.balloon_position = pressure_to_percent(s.pressure)
        elif tracker.phase in ("descent", "landed"):
            status.parachute_position = pressure_to_percent(s.pressure)
            status.burst_position = pressure_to_percent(status.burst_pressure)

    # Alert lights
    if sysstat is not None:
        status.receiver_ok = process_ok(sysstat.receiver_state, sysstat.updated_at, now)
        status.parser_ok = process_ok(sysstat.parser_state, sysstat.updated_at, now)
    else:
        status.receiver_ok = status.parser_ok = False

    status.age_warn = age is not None and age >= AGE_WARN_SEC
    status.packet_ok = tracker.packet_gap is None or tracker.packet_gap <= PACKET_GAP_SEC
    status.signal_level = signal_level(tracker.rssi)
    status.temp_low = (len(tracker.temps) == TEMP_LOW_COUNT and
                       all(t is not None and t < TEMP_LOW_THRESH for t in tracker.temps))

    if calibration_ts is None:
        status.calibrated = False
    elif released:
        status.calibrated = release_ts is None or calibration_ts <= release_ts
    else:
        status.calibrated = (now - calibration_ts).total_seconds() < CAL_AGE_SEC

    if s is None:
        status.sensor_ok = True
        status.gps_fix = False
        status.gps_degrad = None
        status.data_degrad = False
        return

    status.sensor_ok = all(v is not None for v in (s.temperature, s.humidity, s.pressure))
    status.gps_fix = s.latitude is not None and s.longitude is not None
    status.gps_degrad = gps_degradation(s)
    status.data_degrad = (
        (s.pressure is not None and not PRESSURE_RANGE[0] <= s.pressure <= PRESSURE_RANGE[1]) or
        (s.humidity is not None and not 0 <= s.humidity <= 100)
    )


# ── DB loop ───────────────────────────────────────────────────────────────────

def make_tracker(flight, status, ground_ref):
    ground_alt = None
    if ground_ref is not None and ground_ref.gps_altitude is not None:
        ground_alt = ground_ref.gps_altitude
    elif flight.elevation is not None:
        ground_alt = flight.elevation
    tracker = FlightTracker(
        phase=status.flight_phase, burst_detected=status.burst_detected,
        ground_alt=ground_alt, peak_alt=status.max_altitude, min_pressure=status.min_pressure)
    if status.burst_detected:
        tracker.peak = (status.burst_altitude, status.burst_pressure, as_utc(status.burst_ts))
    return tracker


def analyze_once(session, trackers):
    from app.models import Flight, FlightStatus, GroundReference, Log, SystemStatus, Telemetry

    now = datetime.now(timezone.utc)
    sysstat = session.get(SystemStatus, 1)
    flights = session.query(Flight).filter(Flight.status.in_(("pre-flight", "flight"))).all()

    for flight in flights:
        status = session.query(FlightStatus).filter_by(flight_id=flight.id).first()
        if status is None:
            status = FlightStatus(flight_id=flight.id, flight_phase="pre-flight")
            session.add(status)
            session.flush()
        ground_ref = session.query(GroundReference).filter_by(flight_id=flight.id).first()

        tracker = trackers.get(flight.id)
        if tracker is None:
            tracker = trackers[flight.id] = make_tracker(flight, status, ground_ref)
        elif ground_ref is not None and ground_ref.gps_altitude is not None and tracker.phase == "pre-flight":
            tracker.ground_alt = ground_ref.gps_altitude  # re-calibrated

        released = flight.status == "flight"
        query = session.query(Telemetry).filter(Telemetry.flight_id == flight.id)
        if tracker.last_id is not None:
            query = query.filter(Telemetry.id > tracker.last_id)
        else:
            # Fresh tracker: replay a short window of history to warm up the averages.
            recent = query.order_by(Telemetry.id.desc()).limit(RATE_WINDOW).all()
            query = query.filter(Telemetry.id.in_([t.id for t in recent]))

        for row in query.order_by(Telemetry.id).all():
            for level, message in tracker.update(Sample.from_row(row), released):
                session.add(Log(flight_id=flight.id, level=level, message=message))

        apply_to_status(
            status, tracker, now, released,
            calibration_ts=as_utc(ground_ref.timestamp) if ground_ref else None,
            release_ts=as_utc(status.release_ts),
            sysstat=sysstat,
        )
        status.updated_at = now

    session.commit()

    active = {f.id for f in flights}
    for fid in list(trackers):
        if fid not in active:
            del trackers[fid]


def main():
    from config import DATABASE_URL, ENGINE_OPTIONS, sqlalchemy_url

    engine = create_engine(sqlalchemy_url(DATABASE_URL), **ENGINE_OPTIONS)
    Session = sessionmaker(bind=engine)
    trackers = {}
    print("Analyzer running")

    while True:
        session = Session()
        try:
            analyze_once(session, trackers)
        except SQLAlchemyError as exc:
            session.rollback()
            print(f"Database error: {exc}")
            trackers.clear()  # rebuild from the DB on the next pass
        finally:
            session.close()
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
