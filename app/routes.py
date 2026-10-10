from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from backend.archive.audit_telemetry import audit_flight_telemetry
from backend.protocol import normalize_mask, normalize_sn
from .extensions import db
from .models import DataSelection, Device, Flight, FlightStatus, GroundReference, Log, Telemetry, User

bp = Blueprint("main", __name__)

MIN_PASSWORD_LENGTH = 8
TELECOM_ONLINE_SEC = 6
GPS_DECIMATE = 5  # keep every Nth GPS point on the map (plus start, burst and latest)


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(ts):
    if ts is not None and ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def fmt(value, unit, prec=None):
    if value is None:
        return "N/A"
    return f"{value:.{prec}f} {unit}" if prec is not None else f"{value} {unit}"


def get_flight(flight_id):
    return db.get_or_404(Flight, flight_id)


def add_log(flight_id, message, level="INFO"):
    db.session.add(Log(flight_id=flight_id, level=level, message=message))


def latest_telemetry(flight_id):
    return (Telemetry.query.filter_by(flight_id=flight_id)
            .order_by(Telemetry.timestamp.desc(), Telemetry.id.desc()).first())


# ── Auth ──────────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    return redirect(url_for("main.dashboard" if current_user.is_authenticated else "main.login"))


def signup_allowed():
    # The very first account can always be created so a fresh install isn't locked out.
    return current_app.config["ALLOW_SIGNUP"] or db.session.query(User.id).first() is None


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if not signup_allowed():
        flash("Sign-up is disabled. Ask an existing operator for an account.", "warning")
        return redirect(url_for("main.login"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username:
            flash("Username is required.", "danger")
        elif len(password) < MIN_PASSWORD_LENGTH:
            flash(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "danger")
        elif User.query.filter_by(username=username).first():
            flash("That username is taken.", "danger")
        else:
            db.session.add(User(username=username, password=generate_password_hash(password)))
            db.session.commit()
            flash("Account created. You can log in now.", "success")
            return redirect(url_for("main.login"))

    return render_template("signup.html")


def safe_next(target):
    """Only allow relative redirects back into this app."""
    if target and not urlsplit(target).netloc and target.startswith("/"):
        return target
    return url_for("main.dashboard")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if not user or not check_password_hash(user.password, request.form.get("password", "")):
            flash("Invalid username or password.", "danger")
            return redirect(url_for("main.login", next=request.args.get("next")))

        session.permanent = True
        login_user(user)
        return redirect(safe_next(request.args.get("next")))

    return render_template("login.html")


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("main.login"))


# ── Dashboard, devices, flight setup ─────────────────────────────────────────

@bp.route("/dashboard")
@login_required
def dashboard():
    flights = (Flight.query.filter(Flight.status.in_(["pre-flight", "flight"]))
               .order_by(Flight.start_time.desc()).all())
    return render_template("dashboard.html", flights=flights)


@bp.route("/archive")
@login_required
def archive():
    flights = (Flight.query.filter_by(status="post-flight")
               .order_by(Flight.end_time.desc()).all())
    return render_template("archive.html", flights=flights)


@bp.route("/init_device", methods=["GET", "POST"])
@login_required
def init_device():
    if request.method == "POST":
        raw_sn = request.form.get("device_sn", "")
        device_sn = normalize_sn(raw_sn)
        description = request.form.get("description", "").strip()

        if device_sn is None:
            flash("Device serial number must be a hexadecimal value, e.g. 11951.", "danger")
        elif Device.query.filter_by(device_sn=device_sn).first():
            flash(f"Device {device_sn} is already registered.", "danger")
        else:
            db.session.add(Device(device_sn=device_sn, description=description or None))
            db.session.commit()
            flash(f"Device {device_sn} registered.", "success")
            return redirect(url_for("main.dashboard"))

    return render_template("init_device.html")


def parse_flight_form(form):
    """Validate the init_flight form. Returns (values, errors)."""
    errors = []
    values = {
        "mission_number": form.get("mission_number", "").strip(),
        "equipment": form.get("equipment", "").strip() or None,
        "comments": form.get("comments", "").strip() or None,
    }
    if not values["mission_number"]:
        errors.append("Mission number is required.")
    elif Flight.query.filter_by(mission_number=values["mission_number"]).first():
        errors.append("A flight with that mission number already exists.")

    device_sn = normalize_sn(form.get("device_sn", ""))
    device = Device.query.filter_by(device_sn=device_sn).first() if device_sn else None
    if device is None:
        errors.append("Unknown device SN. Register the device first.")
    values["device"] = device

    values["mask"] = normalize_mask(form.get("mask", ""))
    if values["mask"] is None:
        errors.append("Generate a mask (6 hex digits) for this flight.")

    try:
        launch = datetime.strptime(form.get("planned_launch", ""), "%Y-%m-%dT%H:%M")
        values["start_time"] = launch.replace(tzinfo=timezone.utc)
    except ValueError:
        errors.append("Planned launch time is required.")

    try:
        values["start_latitude"] = float(form.get("start_latitude", ""))
        values["start_longitude"] = float(form.get("start_longitude", ""))
        if not (-90 <= values["start_latitude"] <= 90 and -180 <= values["start_longitude"] <= 180):
            raise ValueError
    except ValueError:
        errors.append("Launch latitude/longitude must be valid coordinates.")

    try:
        values["elevation"] = int(form.get("elevation", ""))
    except ValueError:
        errors.append("Launch elevation must be a whole number of metres.")

    return values, errors


@bp.route("/init_flight", methods=["GET", "POST"])
@login_required
def init_flight():
    devices = Device.query.order_by(Device.device_sn).all()
    if request.method == "POST":
        values, errors = parse_flight_form(request.form)
        if errors:
            for error in errors:
                flash(error, "danger")
            return render_template("init_flight.html", devices=devices, form=request.form)

        device = values.pop("device")
        flight = Flight(user_id=current_user.id, device_id=device.id, status="pre-flight", **values)
        db.session.add(flight)
        db.session.flush()
        db.session.add(FlightStatus(flight_id=flight.id, flight_phase="pre-flight"))
        add_log(flight.id, f"Flight created for device {device.device_sn} with mask {flight.mask}.")
        db.session.commit()
        flash(f"Flight {flight.mission_number} created. Flash mask {flight.mask} to device "
              f"{device.device_sn} before launch.", "success")
        return redirect(url_for("main.flight_dashboard", flight_id=flight.id))

    return render_template("init_flight.html", devices=devices, form={})


# ── Flight dashboard and controls ────────────────────────────────────────────

@bp.route("/flight/<int:flight_id>")
@login_required
def flight_dashboard(flight_id):
    flight = get_flight(flight_id)
    logs = (Log.query.filter_by(flight_id=flight_id)
            .order_by(Log.timestamp.desc()).limit(20).all())
    return render_template("flight.html", flight=flight, status=flight.status_row,
                           ground_ref=flight.ground_reference, logs=logs)


@bp.route("/flight/<int:flight_id>/calibrate", methods=["POST"])
@login_required
def calibrate_ground(flight_id):
    flight = get_flight(flight_id)
    if flight.status != "pre-flight":
        return jsonify({"error": "Calibration is only possible before release."}), 400

    telemetry = latest_telemetry(flight_id)
    if telemetry is None:
        return jsonify({"error": "No telemetry received yet."}), 400

    if flight.ground_reference is not None:
        db.session.delete(flight.ground_reference)
        db.session.flush()

    db.session.add(GroundReference(
        flight_id=flight_id,
        timestamp=telemetry.timestamp,
        gps_latitude=telemetry.gps_latitude,
        gps_longitude=telemetry.gps_longitude,
        gps_altitude=telemetry.gps_altitude,
        temperature=telemetry.temperature,
        pressure=telemetry.pressure,
        humidity=telemetry.humidity,
    ))
    add_log(flight_id, f"Ground calibrated from telemetry #{telemetry.id} "
                       f"({fmt(telemetry.gps_altitude, 'm')}, {fmt(telemetry.pressure, 'hPa')}).")
    db.session.commit()
    return jsonify({"message": "Ground reference calibrated."})


@bp.route("/flight/<int:flight_id>/release", methods=["POST"])
@login_required
def release_flight(flight_id):
    flight = get_flight(flight_id)
    if flight.status != "pre-flight":
        return jsonify({"error": "Only a pre-flight flight can be released."}), 400

    now = utcnow()
    flight.status = "flight"
    status = flight.status_row or FlightStatus(flight_id=flight_id)
    db.session.add(status)
    status.release_ts = now

    telemetry = latest_telemetry(flight_id)
    if telemetry is not None:
        status.release_altitude = telemetry.gps_altitude
        status.release_pressure = telemetry.pressure

    add_log(flight_id, "Balloon released.")
    db.session.commit()
    return jsonify({"message": "Flight released.", "release_ts": now.isoformat(), "new_status": flight.status})


@bp.route("/flight/<int:flight_id>/end", methods=["POST"])
@login_required
def end_flight(flight_id):
    flight = get_flight(flight_id)
    if flight.status not in ("pre-flight", "flight"):
        return jsonify({"error": f"Cannot end a flight in status '{flight.status}'."}), 400

    now = utcnow()
    flight.status = "post-flight"
    flight.end_time = now
    status = flight.status_row or FlightStatus(flight_id=flight_id)
    db.session.add(status)
    status.end_ts = now

    add_log(flight_id, "Flight ended by operator.")
    db.session.commit()
    return jsonify({"message": "Flight ended.", "end_ts": now.isoformat(), "new_status": flight.status})


# ── JSON APIs polled by the flight dashboard ─────────────────────────────────

@bp.route("/api/telemetry/<int:flight_id>")
@login_required
def api_telemetry(flight_id):
    """Display-ready strings keyed by the element IDs in flight.html."""
    flight = get_flight(flight_id)
    t = latest_telemetry(flight_id)
    status = flight.status_row

    # Computed live (not from the analyzer) so it stays correct after the flight has ended.
    age = max(0, int((utcnow() - as_utc(t.measurement_ts or t.timestamp)).total_seconds())) if t else None
    fields = {
        "mission-status": flight.status,
        "temp": fmt(t and t.temperature, "°C", 1),
        "humidity": fmt(t and t.humidity, "%", 1),
        "pressure": fmt(t and t.pressure, "hPa"),
        "gps-altitude": fmt(t and t.gps_altitude, "m"),
        "ascent-rate": fmt(t and t.ascent_rate, "m/s", 1),
        "speed": fmt(t and t.speed, "kt", 1),
        "signal-strength": fmt(t and t.signal_strength, "dBm"),
        "gps-sats": fmt(t and t.sats, "sats"),
        "gps-hdop": fmt(t and t.hdop, "", 2).strip(),
        "last-packet": as_utc(t.timestamp).strftime("%H:%M:%S UTC") if t else "N/A",
        "peak-altitude": fmt(status and status.max_altitude, "m", 0),
        "burst-altitude": fmt(status and status.burst_altitude, "m", 0),
        "flight-phase": (status.flight_phase if status else None) or "N/A",
        "last-heard": "N/A" if age is None else ("<1 s" if age < 1 else f"{age} s"),
        "telecom-status": "Unknown" if age is None else ("Online" if age <= TELECOM_ONLINE_SEC else "Offline"),
    }
    return jsonify({"fields": fields})


@bp.route("/api/gps/<int:flight_id>")
@login_required
def api_gps(flight_id):
    """Decimated GPS track. After calibration, only points from calibration onwards are shown."""
    flight = get_flight(flight_id)
    query = Telemetry.query.filter(
        Telemetry.flight_id == flight_id,
        Telemetry.gps_latitude.isnot(None),
        Telemetry.gps_longitude.isnot(None),
    )
    if flight.ground_reference is not None:
        query = query.filter(Telemetry.timestamp >= flight.ground_reference.timestamp)
    recs = query.order_by(Telemetry.timestamp, Telemetry.id).all()
    if not recs:
        return jsonify([])

    # The burst marker goes on the point closest to the recorded burst time.
    status = flight.status_row
    burst_idx = None
    if status is not None and status.burst_detected and status.burst_ts is not None:
        burst_ts = as_utc(status.burst_ts)
        burst_idx = min(range(len(recs)), key=lambda i: abs(
            (as_utc(recs[i].measurement_ts or recs[i].timestamp) - burst_ts).total_seconds()))

    last = len(recs) - 1
    out = []
    for i, r in enumerate(recs):
        icon = "start" if i == 0 else "burst" if i == burst_idx else "end" if i == last else None
        if icon is None and i % GPS_DECIMATE:
            continue
        ts = as_utc(r.timestamp)
        out.append({
            "coords": [r.gps_latitude, r.gps_longitude],
            "altitude": r.gps_altitude,
            "timestamp": ts.isoformat(),
            "timestamp_str": ts.strftime("%H:%M:%S UTC"),
            "icon": icon,
        })
    return jsonify(out)


ALERT_FIELDS = ("receiver_ok", "parser_ok", "sensor_ok", "signal_level", "packet_ok", "age_warn",
                "calibrated", "temp_low", "data_degrad", "gps_fix", "gps_degrad")


@bp.route("/api/status/<int:flight_id>")
@login_required
def api_status(flight_id):
    flight = get_flight(flight_id)
    status = flight.status_row
    if status is None:
        return jsonify({"error": "No status available for this flight."}), 404

    data = {
        "flight_status": flight.status,
        "flight_phase": status.flight_phase,
        "measurement_age": status.measurement_age,
        "has_telemetry": status.measurement_age is not None,
        "burst_detected": bool(status.burst_detected),
        "balloon_position": status.balloon_position,
        "burst_position": status.burst_position,
        "parachute_position": status.parachute_position,
        "has_calibration": flight.ground_reference is not None,
        "updated_at": as_utc(status.updated_at).isoformat() if status.updated_at else None,
    }
    data.update({name: getattr(status, name) for name in ALERT_FIELDS})
    return jsonify(data)


@bp.route("/api/logs/<int:flight_id>")
@login_required
def api_logs(flight_id):
    logs = Log.query.filter_by(flight_id=flight_id).order_by(Log.timestamp.desc()).limit(20).all()
    return jsonify({"logs": [
        {"message": log.message, "timestamp": as_utc(log.timestamp).isoformat(), "level": log.level}
        for log in logs
    ]})


# ── Post-flight verification ─────────────────────────────────────────────────

@bp.route("/flight/<int:flight_id>/verify")
@login_required
def verify_flight_data(flight_id):
    flight = get_flight(flight_id)
    audit = audit_flight_telemetry(flight_id)
    if "error" in audit:
        flash(audit["error"], "danger")
        return redirect(url_for("main.archive"))

    selection = DataSelection.query.filter_by(flight_id=flight_id).first()
    return render_template("verify_data.html", flight=flight, audit=audit, selection=selection)


@bp.route("/flight/<int:flight_id>/confirm_data_selection", methods=["POST"])
@login_required
def confirm_data_selection(flight_id):
    get_flight(flight_id)
    try:
        start_ts = as_utc(datetime.fromisoformat(request.form["start_ts"]))
        end_ts = as_utc(datetime.fromisoformat(request.form["end_ts"]))
    except (KeyError, ValueError):
        abort(400)
    if end_ts < start_ts:
        flash("The end of the data window must be after its start.", "danger")
        return redirect(url_for("main.verify_flight_data", flight_id=flight_id))

    audit = audit_flight_telemetry(flight_id)
    selection = DataSelection.query.filter_by(flight_id=flight_id).first() or DataSelection(flight_id=flight_id)
    selection.start_ts = start_ts
    selection.end_ts = end_ts
    selection.exclusions = [{"key": key} for key in request.form.getlist("exclude_outlier")]
    selection.gap_info = audit.get("gaps", [])
    selection.verified_by = current_user.username
    selection.created_at = utcnow()
    db.session.add(selection)
    add_log(flight_id, f"Data selection verified by {current_user.username}.")
    db.session.commit()

    flash("Data selection saved.", "success")
    return redirect(url_for("main.archive"))
