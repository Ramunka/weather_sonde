from app import db
from datetime import datetime
from flask_login import UserMixin

class User(db.Model, UserMixin):
    __tablename__ = "users"
    __table_args__ = {"schema": "sonde"}
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Device(db.Model):
    __tablename__ = "devices"
    __table_args__ = {"schema": "sonde"}
    id         = db.Column(db.Integer, primary_key=True)
    device_sn  = db.Column(db.String, unique=True, nullable=False)
    description = db.Column(db.String)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Flight(db.Model):
    __tablename__ = "flights"
    __table_args__ = {"schema": "sonde"}

    id             = db.Column(db.Integer, primary_key=True)
    user_id        = db.Column(db.Integer, db.ForeignKey('sonde.users.id'))
    mission_number = db.Column(db.String(50), unique=True, nullable=False)
    equipment      = db.Column(db.String(100))
    start_time     = db.Column(db.DateTime)
    end_time       = db.Column(db.DateTime)
    status         = db.Column(db.String(20), default='pre-flight')
    comments       = db.Column(db.Text)

    # NEW COLUMNS:
    device_id        = db.Column(db.Integer, db.ForeignKey('sonde.devices.id'))
    mask             = db.Column(db.String, nullable=False, default='')
    start_latitude   = db.Column(db.Float)    # optional, if you added these
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
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    gps_latitude = db.Column(db.Float)
    gps_longitude = db.Column(db.Float)
    gps_altitude = db.Column(db.Integer)
    pressure = db.Column(db.Integer)
    temperature = db.Column(db.Float)
    dew_point = db.Column(db.Float)
    #battery = db.Column(db.Integer)
    #voltage = db.Column(db.Float)
    signal_strength = db.Column(db.Integer)
    speed = db.Column(db.Float)
    ascent_rate = db.Column(db.Float)

    flight = db.relationship("Flight", backref="telemetries")

class Log(db.Model):
    __tablename__ = "logs"
    __table_args__ = {"schema": "sonde"}
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    message = db.Column(db.Text)

class Alarm(db.Model):
    __tablename__ = "alarms"
    __table_args__ = {"schema": "sonde"}
    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    alarm_type = db.Column(db.String(50))
    message = db.Column(db.Text)
    resolved = db.Column(db.Boolean, default=False)

class FlightStatus(db.Model):
    __tablename__ = "flight_status"
    __table_args__ = {"schema": "sonde"}

    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'), unique=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    measurement_age = db.Column(db.Integer)     # seconds since measurement_ts
    transmission_age = db.Column(db.Integer)    # seconds since tx_ts

    flight_phase = db.Column(db.String(20))     # "ground", "ascent", "descent", "unknown"

    burst_detected = db.Column(db.Boolean, default=False)
    burst_altitude = db.Column(db.Float)

    current_ascent_rate = db.Column(db.Float)
    max_altitude = db.Column(db.Float)
    min_pressure = db.Column(db.Integer)
    release_ts = db.Column(db.DateTime, nullable=True)
    end_ts = db.Column(db.DateTime, nullable=True)

class GroundReference(db.Model):
    __tablename__ = "ground_reference"
    __table_args__ = {"schema": "sonde"}

    id = db.Column(db.Integer, primary_key=True)
    flight_id = db.Column(db.Integer, db.ForeignKey('sonde.flights.id'), unique=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    # Actual values from sonde
    gps_latitude = db.Column(db.Float)
    gps_longitude = db.Column(db.Float)
    gps_altitude = db.Column(db.Integer)
    temperature = db.Column(db.Float)
    pressure = db.Column(db.Integer)
    humidity = db.Column(db.Float)
    dew_point = db.Column(db.Float)

    # External API reference values
    api_temperature = db.Column(db.Float)
    api_pressure = db.Column(db.Float)
    api_humidity = db.Column(db.Float)
    api_location_name = db.Column(db.String)

    flight = db.relationship("Flight", backref="ground_reference")

