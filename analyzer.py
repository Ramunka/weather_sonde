# analyzer.py

import time
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from app.models import Flight, Telemetry, FlightStatus, Log, GroundReference, SystemStatus

# Database setup
DB_URI = 'postgresql://sonde_user:securepassword@localhost/weather_sonde'
engine = create_engine(DB_URI)
Session = sessionmaker(bind=engine)

# Pressure‐to‐altitude gauge limits
TOP_PRESSURE_MB = 100    # corresponds to top of chart (100 mb)
BOT_PRESSURE_MB = 1000   # corresponds to ground (1000 mb)

# Phase hysteresis thresholds
ASC_IN, ASC_OUT =  0.6, 0.4
DES_IN, DES_OUT = -0.6, -0.4
GROUND_ALT      = 300    # meters
GROUND_RATE     = 0.3    # m/s

# Signal thresholds (dBm)
SIG_GREEN  = -85
SIG_YELLOW = -100

# Temperature low threshold
TEMP_LOW_THRESH = -40
TEMP_LOW_COUNT  = 3      # last N readings

# Age warning threshold
AGE_WARN_SEC = 10

# Calibration age threshold
CAL_AGE_SEC = 300


def log_event(session, flight_id, message):
    log = Log(flight_id=flight_id, message=message)
    session.add(log)
    session.commit()


def pressure_to_percent(p_mb):
    frac = (BOT_PRESSURE_MB - p_mb) / (BOT_PRESSURE_MB - TOP_PRESSURE_MB)
    return min(max(frac * 100, 0), 100)


def monitor():
    # Persistent in‐memory history: { flight_id: [last_rssi,...] }
    signal_hist = {}

    while True:
        session = Session()
        try:
            sysstat = session.query(SystemStatus).first()
            flights = session.query(Flight).filter_by(status='flight').all()
            if not flights:
                session.close()
                time.sleep(3)
                continue

            for flight in flights:
                fid = flight.id
                # latest telemetry
                tel = (session.query(Telemetry)
                         .filter_by(flight_id=fid)
                         .order_by(Telemetry.timestamp.desc())
                         .first())
                if not tel:
                    continue

                now = datetime.now(timezone.utc)
                # measurement age (seconds)
                if tel.timestamp:
                    age_f = (now - tel.timestamp).total_seconds()
                    meas_age = 0 if age_f < 1 else int(age_f)
                else:
                    meas_age = None

                status = session.query(FlightStatus).filter_by(flight_id=fid).first()
                if not status:
                    status = FlightStatus(flight_id=fid)
                    session.add(status)

                # --- Burst detection (edge trigger) ---
                prev_rate = getattr(status, 'last_ascent_rate', None)
                curr_rate = tel.ascent_rate
                burst_edge = (prev_rate is not None and prev_rate >= 0 and
                              curr_rate is not None and curr_rate < -3.0)
                if burst_edge and not status.burst_detected:
                    status.burst_detected  = True
                    status.burst_altitude  = tel.gps_altitude
                    status.burst_pressure  = tel.pressure
                    log_event(session, fid,
                              f"Burst detected at {tel.gps_altitude:.1f} m / {tel.pressure:.1f} mb")

                status.last_ascent_rate = curr_rate

                # --- Phase detection with moving average & hysteresis ---
                if not hasattr(status, '_rate_hist'):
                    status._rate_hist = []
                status._rate_hist.append(curr_rate or 0.0)
                if len(status._rate_hist) > 5:
                    status._rate_hist.pop(0)
                avg_rate = sum(status._rate_hist) / len(status._rate_hist)

                if not hasattr(status, '_alt0'):
                    status._alt0 = tel.gps_altitude or 0.0
                alt_delta = (tel.gps_altitude or 0.0) - status._alt0

                prev_phase = status.flight_phase or 'unknown'
                phase = prev_phase
                if status.burst_detected:
                    phase = 'burst'
                elif prev_phase == 'ascent':
                    if avg_rate < DES_IN: phase = 'descent'
                elif prev_phase == 'descent':
                    if avg_rate > GROUND_RATE and tel.gps_altitude < GROUND_ALT:
                        phase = 'ground'
                elif prev_phase == 'ground':
                    if avg_rate > ASC_IN and alt_delta > 5:
                        phase = 'ascent'
                else:
                    if avg_rate > ASC_IN and alt_delta > 5:
                        phase = 'ascent'
                    elif avg_rate < DES_IN:
                        phase = 'descent'
                    elif tel.gps_altitude < GROUND_ALT and abs(avg_rate) < GROUND_RATE:
                        phase = 'ground'

                status.flight_phase = phase

                # --- Release detection (edge trigger) ---
                if prev_rate is not None and prev_rate <= 0 and curr_rate is not None and curr_rate > 0.5:
                    if status.release_ts is None:
                        status.release_ts       = tel.timestamp
                        status.release_altitude = tel.gps_altitude
                        status.release_pressure = tel.pressure
                        log_event(session, fid,
                                  f"Release detected at {tel.gps_altitude:.1f} m / {tel.pressure:.1f} mb")

                # --- Positions ---
                status.balloon_position   = None
                status.parachute_position = None
                status.burst_position     = None
                if phase == 'ascent':
                    status.balloon_position = pressure_to_percent(tel.pressure)
                elif phase == 'burst':
                    status.burst_position   = pressure_to_percent(status.burst_pressure)
                elif phase == 'descent':
                    status.burst_position     = pressure_to_percent(status.burst_pressure)
                    status.parachute_position = pressure_to_percent(tel.pressure)

                # --- Extremes ---
                if tel.gps_altitude is not None:
                    if status.max_altitude is None or tel.gps_altitude > status.max_altitude:
                        status.max_altitude = tel.gps_altitude
                if tel.pressure is not None:
                    if status.min_pressure is None or tel.pressure < status.min_pressure:
                        status.min_pressure = tel.pressure

                # --- Alerts ---
                # Age state
                status.age_state = 'warn' if (meas_age is not None and meas_age >= AGE_WARN_SEC) else 'ok'
                # Signal level remains same
                sig = tel.signal_strength
                hist = signal_hist.setdefault(fid, [])
                hist.append(sig if sig is not None else -999)
                if len(hist) > 5: hist.pop(0)
                if any(h < SIG_YELLOW for h in hist):
                    status.signal_level = 'red'
                elif any(h < SIG_GREEN for h in hist):
                    status.signal_level = 'yellow'
                else:
                    status.signal_level = 'green'
                # Packet state (always good here)
                status.packet_state = 'good'
                # Sensor state
                status.sensor_state = 'ok' if all(x is not None for x in (tel.temperature, tel.humidity, tel.pressure)) else 'fault'
                # Calibrated boolean
                gr = session.query(GroundReference).filter_by(flight_id=fid).first()
                status.calibrated = bool(gr and (now - gr.timestamp).total_seconds() < CAL_AGE_SEC)
                # Temp low boolean
                if not hasattr(status, '_temp_hist'):
                    status._temp_hist = []
                status._temp_hist.append(tel.temperature or 0)
                if len(status._temp_hist) > TEMP_LOW_COUNT:
                    status._temp_hist.pop(0)
                status.temp_low = all(t < TEMP_LOW_THRESH for t in status._temp_hist)
                # Meas degrade boolean
                status.data_degrad = (
                    tel.pressure is not None and not (300 <= tel.pressure <= 1100)
                    or tel.gps_latitude is None or tel.gps_longitude is None
                )
                # GPS fix boolean & degrade level
                status.gps_fix = (tel.gps_latitude is not None and tel.gps_longitude is not None)
                if status.gps_fix:
                    hd = tel.hdop or 0
                    status.gps_degrad = 'red'    if hd > 6 else (
                                         'yellow' if hd > 3 else None)
                else:
                    status.gps_degrad = None

                if sysstat:
                    status.receiver_state = sysstat.receiver_state
                    status.parser_state   = sysstat.parser_state

                status.measurement_age     = meas_age
                status.current_ascent_rate = tel.ascent_rate
                status.updated_at          = now

                session.commit()

        except SQLAlchemyError:
            session.rollback()
        finally:
            session.close()
            time.sleep(1)

if __name__ == '__main__':
    monitor()
