from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session, current_app
from flask_login import login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from .models import User, Flight, Telemetry, Log, Alarm, Device, GroundReference, FlightStatus
from datetime import datetime, timezone
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
            status         = 'pre-flight',
            device_id      = device.id,
            mask           = mask,
            start_latitude = start_latitude,
            start_longitude= start_longitude,
            elevation      = elevation
        )
        print("DEBUG: New Flight object created, attempting to commit...")

        try:
            db.session.add(new_flight)
            print("DEBUG: Flight committed with id =", new_flight.id)
            db.session.flush()
            initial_status = FlightStatus(
                flight_id=new_flight.id,
                flight_phase='pre-flight',
                # you can set any other defaults here if you like
            )
            db.session.add(initial_status)
            db.session.commit()
            print("DEBUG: FlightStatus created for flight", new_flight.id)
            # ───────────────────────────────────────────────────────

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


@bp.route('/api/telemetry/<int:flight_id>')
@login_required
def telemetry(flight_id):
    telemetry = Telemetry.query\
           .filter_by(flight_id=flight_id)\
           .order_by(Telemetry.timestamp.desc())\
           .first()

    if telemetry is None:
        # no telemetry yet → still return mission info
        flight = Flight.query.get_or_404(flight_id)
        return jsonify({
            "mission_number":  flight.mission_number,
            "equipment":       flight.equipment,
            "start_time":      flight.start_time.strftime("%H:%M:%S") if flight.start_time else None,
            "date":            flight.start_time.strftime("%Y-%m-%d") if flight.start_time else None,
            "temp":            "N/A",
            "humidity":        "N/A",
            "pressure":        "N/A",
            "gps-altitude":    "N/A",
            "altitude-delta":  "N/A",
            "peak-altitude":   "N/A",
            "speed":           "N/A",
            "signal_strength":"N/A",
            "mission-status":  flight.status,
            "telecom-status":  "N/A",
            "last-heard":      "N/A",
            "burst-altitude":  "N/A"
        })

    # grab the analyzer row once
    status = FlightStatus.query.filter_by(flight_id=telemetry.flight_id).first()

    # 1) peak-altitude
    if status and status.max_altitude is not None:
        peak_alt = f"{status.max_altitude} m"
    else:
        peak_alt = "N/A"

    # 2) last-heard (measurement_age)
    if status and status.measurement_age is not None:
        age = status.measurement_age
        last_heard = "<1s" if age < 1 else f"{age}s"
    else:
        last_heard = "N/A"

    # 3) telecom-status (Online if ≤5 s, Offline otherwise, Unknown if no data)
    if status and status.measurement_age is not None:
        telecom = "Online" if status.measurement_age <= 6 else "Offline"
    else:
        telecom = "Unknown"

    # 4) actual-burst-altitude
    if status and status.burst_altitude is not None:
        burst_actual = f"{status.burst_altitude} m"
    else:
        burst_actual = "N/A"

    data = {
        "mission_number":       telemetry.flight.mission_number,
        "equipment":            telemetry.flight.equipment,
        "start_time":           telemetry.timestamp.strftime("%H:%M:%S"),
        "date":                 telemetry.timestamp.strftime("%Y-%m-%d"),
        "temp":                 f"{telemetry.temperature} °C",
        "humidity":             f"{telemetry.humidity} %",
        "pressure":             f"{telemetry.pressure} mb",
        "gps-altitude":         f"{telemetry.gps_altitude} m",
        "altitude-delta":       f"{telemetry.ascent_rate} m/s",
        "speed":                f"{telemetry.speed} kt",
        "signal_strength":      f"{telemetry.signal_strength} dBm",
        "mission-status":       telemetry.flight.status,
        "peak-altitude":        peak_alt,
        "telecom-status":       telecom,
        "last-heard":           last_heard,
        "actual-burst-altitude": burst_actual
        # ... any other fields you still need ...
    }
    return jsonify(data)

@bp.route('/api/gps/<int:flight_id>')
@login_required
def gps_data(flight_id):
    # 1) fetch only post-cal points
    gr = GroundReference.query.filter_by(flight_id=flight_id).first()
    if not gr:
        return jsonify([])

    cutoff = gr.timestamp
    recs = Telemetry.query \
        .filter(Telemetry.flight_id == flight_id,
                Telemetry.timestamp >= cutoff) \
        .order_by(Telemetry.timestamp.asc()) \
        .all()

    # find burst timestamp if any
    status = FlightStatus.query.filter_by(flight_id=flight_id).first()
    burst_ts = status.release_ts if status and status.burst_detected is False else status.burst_ts

    out = []
    n = len(recs)
    for idx, r in enumerate(recs):
        if r.gps_latitude is None or r.gps_longitude is None:
            continue

        # decide whether to include this point
        keep = (
            idx == 0 or
            idx == n - 1 or
            (burst_ts and r.timestamp == burst_ts) or
            idx % 5 == 0
        )
        if not keep:
            continue

        out.append({
            "coords":    [r.gps_latitude, r.gps_longitude],
            "altitude":  r.gps_altitude,
            "timestamp": r.timestamp.strftime("%H:%M:%S UTC"),
            "icon":      idx == 0
                          and "start"
                          or (burst_ts and r.timestamp == burst_ts)
                            and "burst"
                          or idx == n-1
                            and "end"
                          or None
        })
    return jsonify(out)

@bp.route('/api/status/<int:flight_id>')
@login_required
def flight_status(flight_id):
    from .models import FlightStatus

    status = FlightStatus.query.filter_by(flight_id=flight_id).first()
    if not status:
        return jsonify({"error": "No status available for this flight."}), 404

    return jsonify({
        "flight_phase":        status.flight_phase,
        "measurement_age":     status.measurement_age,
        "last_ascent_rate":    status.last_ascent_rate,
        "max_altitude":        status.max_altitude,
        "min_pressure":        status.min_pressure,
        "burst_detected":      status.burst_detected,
        "burst_altitude":      status.burst_altitude,
        "balloon_position":    status.balloon_position,
        "burst_position":      status.burst_position,
        "parachute_position":  status.parachute_position,
        "updated_at":          status.updated_at.isoformat() if status.updated_at else None,
        "calibrated":          GroundReference.query.filter_by(flight_id=flight_id).first() is not None,

        "receiver_ok":          status.receiver_ok,
        "parser_ok":            status.parser_ok,
        "sensor_ok":            status.sensor_ok,
        "signal_level":         status.signal_level,
        "packet_ok":            status.packet_ok,
        "age_warn":             status.age_warn,
        "calibrated_alert":     status.calibrated,
        "temp_low":             status.temp_low,
        "data_degrad":          status.data_degrad,
        "gps_fix":              status.gps_fix,
        "gps_degrad":           status.gps_degrad
    })

@bp.route('/flight/<int:flight_id>/calibrate', methods=['POST'])
@login_required
def calibrate_ground(flight_id):
    flight = Flight.query.get_or_404(flight_id)

    if flight.status != 'pre-flight':
        return jsonify({"error": "Flight must be in 'pre-flight' status to calibrate."}), 400

    # Delete the existing ground reference if present
    if flight.ground_reference:
        db.session.delete(flight.ground_reference)
        db.session.commit()

    # Optional wait to stabilize readings
    import time
    time.sleep(3)

    telemetry = Telemetry.query.filter_by(flight_id=flight_id).order_by(Telemetry.timestamp.desc()).first()
    if not telemetry:
        return jsonify({"error": "No telemetry available to calibrate."}), 400

    ref = GroundReference(
        flight_id=flight_id,
        gps_latitude=telemetry.gps_latitude,
        gps_longitude=telemetry.gps_longitude,
        gps_altitude=telemetry.gps_altitude,
        temperature=telemetry.temperature,
        pressure=telemetry.pressure,
        humidity=telemetry.humidity,
        timestamp=telemetry.timestamp  # Optional, if your table supports it
    )
    db.session.add(ref)

    # Log it
    log = Log(
        flight_id=flight_id,
        level='INFO',
        message=f"Ground Calibrated using telemetry ID {telemetry.id}.",
    )

    db.session.add(log)
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
        status.release_ts = datetime.now(timezone.utc)
        db.session.commit()

    # Log it
    log = Log(
        flight_id=flight_id,
        level='INFO',
        message=f"Balloon released."
    )

    db.session.add(log)
    db.session.commit()

    return jsonify({"message": "Flight released.", "release_ts": now.isoformat(), "new_status": "flight"})

@bp.route('/flight/<int:flight_id>/end', methods=['POST'])
@login_required
def end_flight(flight_id):
    flight = Flight.query.get_or_404(flight_id)

    if flight.status not in ['pre-flight', 'flight']:
        return jsonify({"error": f"Cannot end flight from status '{flight.status}'."}), 400

    flight.status = 'post-flight'

    # Record end timestamp
    status = FlightStatus.query.filter_by(flight_id=flight_id).first()
    if status:
        status.end_ts = datetime.now(timezone.utc)

    log = Log(
        flight_id=flight.id,
        level='INFO',
        message=f"Flight manually ended from status '{flight.status}'."
    )

    db.session.add(log)
    db.session.commit()

    return jsonify({
        "message": "Flight marked as ended.",
        "new_status": "post-flight"
    })

@bp.route('/api/logs/<int:flight_id>')
@login_required
def get_logs(flight_id):
    logs = Log.query.filter_by(flight_id=flight_id).order_by(Log.timestamp.desc()).limit(20).all()
    return jsonify({
        "logs": [
            {
                "message": log.message,
                "timestamp": log.timestamp.isoformat(),
                "level": log.level
            } for log in logs
        ]
    })