from app import db
from datetime import datetime, timezone
from flask_login import UserMixin

class User(db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": "sonde"}
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Device(db.Model):
    __tablename__ = "devices"
    __table_args__ = {"schema": "sonde"}
    id         = db.Column(db.Integer, primary_key=True)
    device_sn  = db.Column(db.String, unique=True, nullable=False)
    description = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Flight(db.Model):
    __tablename__ = "flights"
    __table_args__ = {"schema": "sonde"}

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('sonde.users.id'))
    mission_number = db.Column(db.String(50), unique=True, nullable=False)
    equipment      = db.Column(db.String(100))
    start_time     = db.Column(db.DateTime(timezone=True))
    end_time       = db.Column(db.DateTime(timezone=True))
    status         = db.Column(db.String(20), default='pre-flight')
    comments       = db.Column(db.Text)

    device_id        = db.Column(db.Integer, db.ForeignKey('sonde.devices.id'))
    mask             = db.Column(db.String, nullable=False, default='')
    start_latitude   = db.Column(db.Float)
    start_longitude  = db.Column(db.Float)
    elevation        = db.Column(db.Integer)

    # relationships
    user   = db.relationship("User", backref="flights")
    device = db.relationship("Device", backref="flights")

class Telemetry(db.Model):
    __tablename__ = "telemetry"
    __table_args__ = {"schema": "sonde"}
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'))
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    gps_latitude = db.Column(db.Float)
    gps_longitude = db.Column(db.Float)
    gps_altitude = db.Column(db.Integer)
    pressure = db.Column(db.Integer)
    temperature = db.Column(db.Float)
    signal_strength = db.Column(db.Integer)
    speed = db.Column(db.Float)
    ascent_rate = db.Column(db.Float)
    humidity = db.Column(db.Float)
    hdop = db.Column(db.Float)
    sats = db.Column(db.Integer)

    processed_ts = db.Column(db.DateTime(timezone=True))        # when the parser wrote the row
    measurement_ts = db.Column(db.DateTime(timezone=True))      # when measurement was actually taken

    flight = db.relationship("Flight", backref="telemetries")

class Log(db.Model):
    __tablename__ = "logs"
    __table_args__ = {"schema": "sonde"}
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'))
    timestamp = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    level = db.Column(db.String(16), default='INFO', nullable=False)
    message = db.Column(db.Text)

class Alarm(db.Model):
    __tablename__ = "alarms"
    __table_args__ = {"schema": "sonde"}
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'))
    timestamp = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    alarm_type = db.Column(db.String(50))
    message = db.Column(db.Text)
    resolved = db.Column(db.Boolean, default=False)

class FlightStatus(db.Model):
    __tablename__ = "flight_status"
    __table_args__ = {"schema": "sonde"}

    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'), unique=True)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    balloon_position   = db.Column(db.Float, nullable=True)  # 0–100%
    parachute_position = db.Column(db.Float, nullable=True)  # 0–100%
    burst_position     = db.Column(db.Float, nullable=True)  # 0–100%
    release_altitude   = db.Column(db.Float, nullable=True)

    measurement_age = db.Column(db.Integer,nullable=True)     # seconds since measurement_ts

    flight_phase = db.Column(db.String(20), default='pre-flight')     # "ground", "ascent", "descent", "unknown"

    burst_detected = db.Column(db.Boolean, default=False)
    burst_altitude = db.Column(db.Float,nullable=True)

    last_ascent_rate = db.Column(db.Float,nullable=True)
    max_altitude = db.Column(db.Float,nullable=True)
    min_pressure = db.Column(db.Integer,nullable=True)
    release_ts = db.Column(db.DateTime, nullable=True)
    end_ts = db.Column(db.DateTime, nullable=True)
    receiver_ok     = db.Column(db.Boolean, default=False)  # "Receiver" alert
    parser_ok       = db.Column(db.Boolean, default=False)  # "Parser" alert
    sensor_ok       = db.Column(db.Boolean, default=True)   # "Sensor" alert
    signal_level    = db.Column(db.String(6), default=None) # "Signal": 'green','yellow','red',None
    packet_ok       = db.Column(db.Boolean, default=True)   # "Packet" alert
    age_warn        = db.Column(db.Boolean, default=False)  # "Age" alert
    calibrated      = db.Column(db.Boolean, default=False)  # "Calibrated" alert
    temp_low        = db.Column(db.Boolean, default=False)  # "Temp Low"
    data_degrad     = db.Column(db.Boolean, default=False)  # "Meas Degrad"
    gps_fix         = db.Column(db.Boolean, default=False)  # "GPS Fix"
    gps_degrad      = db.Column(db.String(6), default=None) # 'yellow','red',None

class GroundReference(db.Model):
    __tablename__ = "ground_reference"
    __table_args__ = {"schema": "sonde"}

    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'), unique=True)
    timestamp = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Actual values from sonde
    gps_latitude = db.Column(db.Float)
    gps_longitude = db.Column(db.Float)
    gps_altitude = db.Column(db.Integer)
    temperature = db.Column(db.Float)
    pressure = db.Column(db.Integer)
    humidity = db.Column(db.Float)

    # External API reference values
    api_temperature = db.Column(db.Float)
    api_pressure = db.Column(db.Float)
    api_humidity = db.Column(db.Float)
    api_location_name = db.Column(db.String)

    flight = db.relationship("Flight", backref="ground_reference")

class SystemStatus(db.Model):
    __tablename__  = 'system_status'
    __table_args__ = {'schema': 'raw'}

    id             = db.Column(db.Integer, primary_key=True)
    receiver_state = db.Column(db.String, nullable=False, default='idle')
    parser_state   = db.Column(db.String, nullable=False, default='idle')
    updated_at     = db.Column(db.DateTime(timezone=True),
                               server_default=db.func.now(),
                               onupdate=db.func.now(),
                               nullable=False)

class DataSelection(db.Model):
    __tablename__ = 'data_selection'
    __table_args__ = {'schema': 'sonde'}

    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'), unique=True)

    start_ts = db.Column(db.DateTime(timezone=True), nullable=False)  # inclusive
    end_ts = db.Column(db.DateTime(timezone=True), nullable=False)  # inclusive or exclusive

    exclusions = db.Column(db.JSON)  # optional list of outlier rows or timestamps
    gap_info = db.Column(db.JSON)  # optional list of gaps
    verified_by = db.Column(db.String)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
