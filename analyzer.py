# analyzer.py

import time
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Flight, Telemetry, FlightStatus, Log
from sqlalchemy.exc import SQLAlchemyError

DB_URI = 'postgresql://sonde_user:securepassword@localhost/weather_sonde'
engine = create_engine(DB_URI)
Session = sessionmaker(bind=engine)

def log_event(session, flight_id, message):
    log = Log(flight_id=flight_id, message=message)
    session.add(log)
    session.commit()

def monitor():
    while True:
        session = Session()
        try:
            flights = session.query(Flight).filter_by(status='flight').all()

            if not flights:
                print("[analyzer] No active flights in 'flight' mode.")
                session.close()
                time.sleep(3)
                continue

            print(f"[analyzer] Monitoring {len(flights)} active flight(s)...")

            for flight in flights:
                flight_id = flight.id
                print(f"[analyzer] → Flight ID {flight_id}")

                telemetry = session.query(Telemetry).filter_by(flight_id=flight_id).order_by(Telemetry.timestamp.desc()).first()
                if not telemetry:
                    print(f"[analyzer] No telemetry for flight {flight_id}")
                    continue

                now = datetime.utcnow()
                measurement_age = int((now - telemetry.timestamp).total_seconds()) if telemetry.timestamp else None
                transmission_age = int((now - telemetry.tx_ts).total_seconds()) if telemetry.tx_ts else None

                if telemetry.ascent_rate is None:
                    phase = "unknown"
                elif telemetry.ascent_rate > 0.5:
                    phase = "ascent"
                elif telemetry.ascent_rate < -0.5:
                    phase = "descent"
                elif abs(telemetry.ascent_rate) < 0.5 and telemetry.gps_altitude < 300:
                    phase = "ground"
                else:
                    phase = "unknown"

                status = session.query(FlightStatus).filter_by(flight_id=flight_id).first()
                if not status:
                    status = FlightStatus(flight_id=flight_id)
                    session.add(status)

                status.measurement_age = measurement_age
                status.transmission_age = transmission_age
                status.flight_phase = phase
                status.current_ascent_rate = telemetry.ascent_rate
                status.burst_detected = telemetry.ascent_rate is not None and telemetry.ascent_rate < -3.0
                if status.burst_detected and not status.burst_altitude:
                    status.burst_altitude = telemetry.gps_altitude

                max_alt_point = session.query(Telemetry).filter_by(flight_id=flight_id).order_by(Telemetry.gps_altitude.desc()).first()
                if max_alt_point:
                    status.max_altitude = max_alt_point.gps_altitude

                min_pressure_point = session.query(Telemetry).filter_by(flight_id=flight_id).order_by(Telemetry.pressure.asc()).first()
                if min_pressure_point:
                    status.min_pressure = min_pressure_point.pressure

                # Inside the loop for each flight
                status = session.query(FlightStatus).filter_by(flight_id=flight_id).first()
                if not status:
                    status = FlightStatus(flight_id=flight_id)
                    session.add(status)

                # Detect and store release timestamp
                if not status.release_ts and telemetry.ascent_rate is not None and telemetry.ascent_rate > 0.5:
                    status.release_ts = telemetry.timestamp
                    log_event(session, flight_id, f"Sonde released (ascent rate {telemetry.ascent_rate:.2f} m/s) at {status.release_ts.isoformat()}")
                    print(f"[analyzer] Flight {flight_id} release detected at {status.release_ts}")


                status.updated_at = datetime.utcnow()
                session.commit()

                print(f"[analyzer] Flight {flight_id} updated: phase={phase}, ascent_rate={telemetry.ascent_rate}")

        except SQLAlchemyError as e:
            print("[analyzer] DB error:", e)
            session.rollback()
        finally:
            session.close()
            time.sleep(3)

if __name__ == "__main__":
    monitor()