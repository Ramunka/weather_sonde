from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from .models import User, Flight, Telemetry, Log, Alarm, Device, GroundReference
from datetime import datetime
from .extensions import db
from flask import flash
import requests

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    return redirect(url_for('main.login'))

@bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        existing_user = User.query.filter_by(username=username).first()

        if existing_user:
            flash("User already exists","danger")
            return redirect(url_for('main.signup'))

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_password)
        try:
            db.session.add(new_user)
            db.session.commit()
            flash("Account created successfully. You may now log in.", "success")
            return redirect(url_for('main.login'))
        except Exception as e:
            db.session.rollback()
            flash("An error occured while creating your account!","danger")
    return render_template("signup.html")

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()

        if not user or not check_password_hash(user.password, password):
            flash("Invalid username or password", "danger")
            return redirect(url_for('main.login'))

        session.permanent = True

        login_user(user)
        return redirect(url_for('main.dashboard'))

    return render_template("login.html")


from flask import render_template, request, redirect, url_for, flash
from flask_login import current_user, login_required
from datetime import datetime
from app import db
from app.models import Flight, Device

@bp.route('/init_flight', methods=['GET', 'POST'])
@login_required
def init_flight():
    if request.method == 'POST':
        # Dump the form data
        print("DEBUG: Received form data:", request.form)

        # 1) Read all form fields
        device_sn      = request.form.get('device_sn', '').strip()
        mask           = request.form.get('mask', '').strip()
        mission_number = request.form.get('mission_number', '').strip()
        equipment      = request.form.get('equipment', '').strip()
        planned_launch_str = request.form.get('planned_launch', '').strip()
        start_lat_str  = request.form.get('start_latitude', '').strip()
        start_lng_str  = request.form.get('start_longitude', '').strip()
        elevation_str  = request.form.get('elevation', '').strip()
        comments       = request.form.get('comments', '').strip()

        print(f"DEBUG: Parsed fields -> SN: {device_sn}, Mask: {mask}, "
              f"Mission: {mission_number}, Launch: {planned_launch_str}, "
              f"Lat: {start_lat_str}, Lng: {start_lng_str}, "
              f"Elev: {elevation_str}, Comments: {comments}")

        # 2) Validate required fields
        missing = []
        for name, val in [
            ('Device SN', device_sn),
            ('Mask',      mask),
            ('Mission Number', mission_number),
            ('Launch Time',    planned_launch_str),
            ('Latitude',       start_lat_str),
            ('Longitude',      start_lng_str),
            ('Elevation',      elevation_str),
            ('Comments',       comments),
        ]:
            if not val:
                missing.append(name)

        if missing:
            msg = f"Missing fields: {', '.join(missing)}"
            print("DEBUG:", msg)
            flash(msg, 'error')
            return render_template("init_flight.html")

        # 3) Parse planned_launch into datetime
        try:
            planned_launch = datetime.strptime(planned_launch_str, '%Y-%m-%dT%H:%M')
            print(f"DEBUG: Parsed planned_launch = {planned_launch}")
        except ValueError as ve:
            print("DEBUG: Launch datetime parse error:", ve)
            flash('Invalid launch datetime format.', 'error')
            return render_template("init_flight.html")

        # 4) Parse latitude, longitude, elevation
        try:
            start_latitude  = float(start_lat_str)
            start_longitude = float(start_lng_str)
            elevation       = int(elevation_str)
            print(f"DEBUG: Parsed coords = ({start_latitude}, {start_longitude}), elevation = {elevation}")
        except ValueError as ve:
            print("DEBUG: Lat/Lng/Elev parse error:", ve)
            flash('Latitude/Longitude must be numbers; Elevation must be an integer.', 'error')
            return render_template("init_flight.html")

        # 5) Look up the Device by SN
        device = Device.query.filter_by(device_sn=device_sn).first()
        if not device:
            msg = f"No device registered with SN={device_sn}."
            print("DEBUG:", msg)
            flash(msg, 'error')
            return render_template("init_flight.html")
        print(f"DEBUG: Found Device row with id={device.id}")

        # 6) Create and save the Flight
        new_flight = Flight(
            user_id        = current_user.id,
            mission_number = mission_number,
            equipment      = equipment,
            comments       = comments,
            start_time     = planned_launch,
            status         = 'in-flight',
            device_id      = device.id,
            mask           = mask,
            start_latitude = start_latitude,
            start_longitude= start_longitude,
            elevation      = elevation
        )
        print("DEBUG: New Flight object created, attempting to commit...")

        try:
            db.session.add(new_flight)
            db.session.commit()
            print("DEBUG: Flight committed with id =", new_flight.id)
            return redirect(url_for('main.dashboard'))
        except Exception as e:
            db.session.rollback()
            print("DEBUG: Exception during commit:", e)
            flash('Error creating flight; please try again.', 'error')
            return render_template("init_flight.html")

    # GET → just render form
    return render_template("init_flight.html")


@bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.login'))

@bp.route('/dashboard')
@login_required
def dashboard():
    flights = Flight.query.order_by(Flight.start_time.desc()).all()
    return render_template("dashboard.html", flights=flights)

@bp.route('/flight/<int:flight_id>')
@login_required
def flight_dashboard(flight_id):
    flight = Flight.query.get_or_404(flight_id)

    # “telemetry_latest” is just the very last row (if you still need it)
    telemetry_latest = Telemetry.query \
        .filter_by(flight_id=flight_id) \
        .order_by(Telemetry.timestamp.desc()) \
        .first()

    # Fetch _all_ telemetry rows for this flight, in chronological order:
    points = Telemetry.query \
        .filter_by(flight_id=flight_id) \
        .order_by(Telemetry.timestamp.asc()) \
        .all()

    logs = Log.query \
        .filter_by(flight_id=flight_id) \
        .order_by(Log.timestamp.desc()) \
        .limit(20) \
        .all()

    return render_template(
        "flight.html",
        flight=flight,
        telemetry=telemetry_latest,
        logs=logs,
        points=points,                # pass the entire list to the template
        balloon_position=80,
        parachute_position=60,
        burst_position=0
    )


@bp.route('/api/telemetry')
def telemetry():
    telemetry = Telemetry.query.order_by(Telemetry.timestamp.desc()).first()
    if telemetry:
        data = {
            "mission_number": telemetry.flight.mission_number,
            "equipment": telemetry.flight.equipment,
            "start_time": telemetry.timestamp.strftime("%H:%M:%S"),
            "date": telemetry.timestamp.strftime("%Y-%m-%d"),
            "temp": f"{telemetry.temperature} °C",
            "dew_point": f"{telemetry.dew_point} °C",
            "pressure": f"{telemetry.pressure} mb",
            "gps_altitude": f"{telemetry.gps_altitude} m",
            "altitude_delta": "N/A",
            "peak_altitude": "N/A",
            "peak_speed": "N/A",
            "cpu_temp": "N/A",
            "signal_strength": f"{telemetry.signal_strength} dBm",
            "mission_status": telemetry.flight.status,
            "telecom_status": "Online",
            "last_heard": "N/A",
            "expected_ascent": "N/A",
            "actual_ascent": f"{telemetry.ascent_rate} m/s",
            "helium_volume": "N/A",
            "actual_helium_volume": "N/A",
            "burst_altitude": "N/A",
            "actual_burst_altitude": "N/A"
        }
        return jsonify(data)
    else:
        return jsonify({"error": "No telemetry data available"}), 404

@bp.route('/api/gps/<int:flight_id>')
@login_required
def gps_data(flight_id):
    records = Telemetry.query \
        .filter_by(flight_id=flight_id) \
        .order_by(Telemetry.timestamp.asc()) \
        .all()

    path = []
    for r in records:
        if r.gps_latitude is None or r.gps_longitude is None:
            continue
        path.append({
            "coords": [r.gps_latitude, r.gps_longitude],
            "altitude": r.gps_altitude,
            "timestamp": r.timestamp.strftime("%H:%M:%S UTC")
        })
    return jsonify(path)

@bp.route('/api/status/<int:flight_id>')
@login_required
def flight_status(flight_id):
    from .models import FlightStatus

    status = FlightStatus.query.filter_by(flight_id=flight_id).first()
    if not status:
        return jsonify({"error": "No status available for this flight."}), 404

    return jsonify({
        "flight_phase": status.flight_phase,
        "burst_detected": status.burst_detected,
        "burst_altitude": status.burst_altitude,
        "measurement_age": status.measurement_age,
        "transmission_age": status.transmission_age,
        "current_ascent_rate": status.current_ascent_rate,
        "max_altitude": status.max_altitude,
        "min_pressure": status.min_pressure,
        "updated_at": status.updated_at.isoformat() if status.updated_at else None
    })

@bp.route('/flight/<int:flight_id>/start', methods=['POST'])
@login_required
def start_flight(flight_id):
    flight = Flight.query.get_or_404(flight_id)
    if flight.status != 'pre-flight':
        return jsonify({"error": "Flight must be in 'pre-flight' state to start."}), 400

    flight.status = 'flight'
    db.session.commit()
    return jsonify({"message": "Flight started.", "new_status": flight.status})


@bp.route('/flight/<int:flight_id>/end', methods=['POST'])
@login_required
def end_flight(flight_id):
    flight = Flight.query.get_or_404(flight_id)
    if flight.status != 'flight':
        return jsonify({"error": "Flight must be in 'flight' state to end."}), 400

    flight.status = 'post-flight'
    db.session.commit()
    return jsonify({"message": "Flight ended.", "new_status": flight.status})

@bp.route('/flight/<int:flight_id>/calibrate', methods=['POST'])
#@login_required
def calibrate_ground(flight_id):
    flight = Flight.query.get_or_404(flight_id)

    if flight.status != 'pre-flight':
        return jsonify({"error": "Flight must be in 'pre-flight' status to calibrate."}), 400

    if flight.ground_reference:
        return jsonify({"error": "Ground reference already set."}), 400

    # Optional wait to stabilize readings
    import time
    time.sleep(3)

    telemetry = Telemetry.query.filter_by(flight_id=flight_id).order_by(Telemetry.timestamp.desc()).first()
    if not telemetry:
        return jsonify({"error": "No telemetry available to calibrate."}), 400

    # Fetch weather data from Wunderground API
    lat = flight.start_latitude
    lon = flight.start_longitude
    api_key = "your_api_key_here"

    try:
        res = requests.get(
            "https://api.weather.com/v3/location/point",
            params={
                "geocode": f"{lat},{lon}",
                "language": "en-US",
                "format": "json",
                "apiKey": api_key
            }
        )
        api_data = res.json()
        api_temp = api_data.get("temperature")
        api_pres = api_data.get("pressure")
        api_hum = api_data.get("humidity")
        api_name = api_data.get("displayName", "unknown")
    except Exception as e:
        print("[calibrate] Warning: Wunderground fetch failed:", e)
        api_temp = api_pres = api_hum = None
        api_name = None

    ref = GroundReference(
        flight_id=flight_id,
        gps_latitude=telemetry.gps_latitude,
        gps_longitude=telemetry.gps_longitude,
        gps_altitude=telemetry.gps_altitude,
        temperature=telemetry.temperature,
        pressure=telemetry.pressure,
        humidity=telemetry.humidity,
        dew_point=telemetry.dew_point,
        api_temperature=api_temp,
        api_pressure=api_pres,
        api_humidity=api_hum,
        api_location_name=api_name,
        timestamp=telemetry.timestamp  # Optional, if your table supports it
    )

    db.session.add(ref)
    db.session.commit()

    return jsonify({"message": "Ground reference calibrated."})

@bp.route('/flight/<int:flight_id>/release', methods=['POST'])
@login_required
def release_flight(flight_id):
    flight = Flight.query.get_or_404(flight_id)

    if flight.status != 'pre-flight':
        return jsonify({"error": "Flight must be in 'pre-flight' state to release."}), 400

    # Update status
    flight.status = 'flight'

    # Record release timestamp
    status = FlightStatus.query.filter_by(flight_id=flight_id).first()
    if status:
        status.release_ts = datetime.utcnow()
        db.session.commit()

    now = datetime.utcnow()
    status.release_ts = now

    # Log it
    log = Log(
        flight_id=flight_id,
        level='INFO',
        message=f"Baloon released at {now.isoformat()} UTC"
    )

    db.session.add(log)
    db.session.commit()

    return jsonify({"message": "Flight released.", "release_ts": now.isoformat(), "new_status": "flight"})

