from datetime import datetime, timezone

from flask_login import UserMixin

from .extensions import db


def utcnow():
    return datetime.now(timezone.utc)


def TZDateTime():
    return db.DateTime(timezone=True)


class User(db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": "sonde"}

    id         = db.Column(db.Integer, primary_key=True)
    username   = db.Column(db.String(80), unique=True, nullable=False)
    password   = db.Column(db.String(256), nullable=False)  # werkzeug hash
    created_at = db.Column(TZDateTime(), default=utcnow)


class Device(db.Model):
    __tablename__ = "devices"
    __table_args__ = {"schema": "sonde"}

    id          = db.Column(db.Integer, primary_key=True)
    device_sn   = db.Column(db.String, unique=True, nullable=False)  # canonical hex, see protocol.normalize_sn
    description = db.Column(db.String)
    created_at  = db.Column(TZDateTime(), default=utcnow)


class Flight(db.Model):
    __tablename__ = "flights"
    __table_args__ = {"schema": "sonde"}

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey("sonde.users.id"))
    device_id      = db.Column(db.Integer, db.ForeignKey("sonde.devices.id"))
    mission_number = db.Column(db.String(50), unique=True, nullable=False)
    equipment      = db.Column(db.String(100))
    comments       = db.Column(db.Text)
    status         = db.Column(db.String(20), nullable=False, default="pre-flight")  # pre-flight | flight | post-flight
    start_time     = db.Column(TZDateTime())  # planned launch (UTC)
    end_time       = db.Column(TZDateTime())  # when the operator ended the flight

    mask            = db.Column(db.String, nullable=False)  # 24-bit hex; token = SN ^ mask
    start_latitude  = db.Column(db.Float)
    start_longitude = db.Column(db.Float)
    elevation       = db.Column(db.Integer)  # launch site elevation, m

    user   = db.relationship("User", backref="flights")
    device = db.relationship("Device", backref="flights")
    status_row = db.relationship("FlightStatus", uselist=False, back_populates="flight")
    ground_reference = db.relationship("GroundReference", uselist=False, back_populates="flight")


class Telemetry(db.Model):
    __tablename__ = "telemetry"
    __table_args__ = (
        db.Index("ix_telemetry_flight_timestamp", "flight_id", "timestamp"),
        {"schema": "sonde"},
    )

    id        = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey("sonde.flights.id"), nullable=False)

    timestamp      = db.Column(TZDateTime(), default=utcnow)  # when the packet was received
    measurement_ts = db.Column(TZDateTime())                  # GPS time on the sonde (NULL without fix)
    processed_ts   = db.Column(TZDateTime())                  # when the parser wrote the row

    gps_latitude  = db.Column(db.Float)
    gps_longitude = db.Column(db.Float)
    gps_altitude  = db.Column(db.Integer)  # m
    hdop          = db.Column(db.Float)
    sats          = db.Column(db.Integer)

    pressure    = db.Column(db.Integer)  # hPa
    temperature = db.Column(db.Float)    # °C
    humidity    = db.Column(db.Float)    # %RH

    signal_strength = db.Column(db.Integer)  # RSSI, dBm
    speed           = db.Column(db.Float)    # ground speed, knots
    ascent_rate     = db.Column(db.Float)    # m/s

    flight = db.relationship("Flight")


class Log(db.Model):
    __tablename__ = "logs"
    __table_args__ = {"schema": "sonde"}

    id        = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey("sonde.flights.id"))
    timestamp = db.Column(TZDateTime(), default=utcnow)
    level     = db.Column(db.String(16), nullable=False, default="INFO")
    message   = db.Column(db.Text)


class FlightStatus(db.Model):
    """One row per flight, owned by the analyzer (release_ts/end_ts are set by the web app)."""
    __tablename__ = "flight_status"
    __table_args__ = {"schema": "sonde"}

    id         = db.Column(db.Integer, primary_key=True)
    flight_id  = db.Column(db.Integer, db.ForeignKey("sonde.flights.id"), unique=True, nullable=False)
    updated_at = db.Column(TZDateTime(), default=utcnow, onupdate=utcnow)

    flight_phase     = db.Column(db.String(20), default="pre-flight")  # pre-flight | ascent | descent | landed
    measurement_age  = db.Column(db.Integer)  # seconds since the last measurement
    last_ascent_rate = db.Column(db.Float)
    max_altitude     = db.Column(db.Float)
    min_pressure     = db.Column(db.Integer)

    release_ts       = db.Column(TZDateTime())
    release_altitude = db.Column(db.Float)
    release_pressure = db.Column(db.Float)
    burst_detected   = db.Column(db.Boolean, default=False)
    burst_ts         = db.Column(TZDateTime())
    burst_altitude   = db.Column(db.Float)
    burst_pressure   = db.Column(db.Float)
    end_ts           = db.Column(TZDateTime())

    # Pressure gauge marker heights, 0–100 % of the 1000→100 hPa scale (NULL = hidden)
    balloon_position   = db.Column(db.Float)
    parachute_position = db.Column(db.Float)
    burst_position     = db.Column(db.Float)

    # Alert lights
    receiver_ok  = db.Column(db.Boolean, default=False)
    parser_ok    = db.Column(db.Boolean, default=False)
    sensor_ok    = db.Column(db.Boolean, default=True)
    signal_level = db.Column(db.String(6))   # green | yellow | red | NULL
    packet_ok    = db.Column(db.Boolean, default=True)
    age_warn     = db.Column(db.Boolean, default=False)
    calibrated   = db.Column(db.Boolean, default=False)
    temp_low     = db.Column(db.Boolean, default=False)
    data_degrad  = db.Column(db.Boolean, default=False)
    gps_fix      = db.Column(db.Boolean, default=False)
    gps_degrad   = db.Column(db.String(6))   # yellow | red | NULL

    flight = db.relationship("Flight", back_populates="status_row")


class GroundReference(db.Model):
    """Snapshot of the telemetry taken when the operator presses 'Calibrate Ground'."""
    __tablename__ = "ground_reference"
    __table_args__ = {"schema": "sonde"}

    id        = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey("sonde.flights.id"), unique=True, nullable=False)
    timestamp = db.Column(TZDateTime(), default=utcnow)

    gps_latitude  = db.Column(db.Float)
    gps_longitude = db.Column(db.Float)
    gps_altitude  = db.Column(db.Integer)
    temperature   = db.Column(db.Float)
    pressure      = db.Column(db.Integer)
    humidity      = db.Column(db.Float)

    flight = db.relationship("Flight", back_populates="ground_reference")


class DataSelection(db.Model):
    """Post-flight verified data window and excluded outliers."""
    __tablename__ = "data_selection"
    __table_args__ = {"schema": "sonde"}

    id          = db.Column(db.Integer, primary_key=True)
    flight_id   = db.Column(db.Integer, db.ForeignKey("sonde.flights.id"), unique=True, nullable=False)
    start_ts    = db.Column(TZDateTime(), nullable=False)
    end_ts      = db.Column(TZDateTime(), nullable=False)
    exclusions  = db.Column(db.JSON)  # [{"key": "<iso timestamp>::<field>"}]
    gap_info    = db.Column(db.JSON)
    verified_by = db.Column(db.String)
    created_at  = db.Column(TZDateTime(), default=utcnow)


class SystemStatus(db.Model):
    """Single row (id=1) written by the supervisor. Lives in the raw schema (db/raw_schema.sql)."""
    __tablename__ = "system_status"
    __table_args__ = {"schema": "raw"}

    id             = db.Column(db.Integer, primary_key=True)
    receiver_state = db.Column(db.String, nullable=False, default="idle")
    parser_state   = db.Column(db.String, nullable=False, default="idle")
    updated_at     = db.Column(TZDateTime(), server_default=db.func.now(), nullable=False)
