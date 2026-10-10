from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from backend.analysis.analyzer import (
    FlightTracker, Sample, apply_to_status, pressure_to_percent, signal_level,
)

T0 = datetime(2025, 8, 1, 12, 0, tzinfo=timezone.utc)


def sample(i, alt, rate, pressure=1000, temp=20.0, rssi=-60, lat=47.0, hdop=1.0, sats=8, dt=2):
    ts = T0 + timedelta(seconds=dt * i)
    return Sample(id=i, received=ts, measured=ts, altitude=alt, pressure=pressure, temperature=temp,
                  humidity=50.0, latitude=lat, longitude=-122.0 if lat is not None else None,
                  hdop=hdop, sats=sats, rssi=rssi, ascent_rate=rate)


def blank_status():
    return SimpleNamespace(burst_detected=False, burst_altitude=None, burst_pressure=None, burst_ts=None)


def fly(tracker, samples, released=True):
    events = []
    for s in samples:
        events += tracker.update(s, released)
    return events


def test_pressure_gauge_mapping():
    assert pressure_to_percent(1000) == 0
    assert pressure_to_percent(100) == 100
    assert pressure_to_percent(550) == 50
    assert pressure_to_percent(1013) == 0 and pressure_to_percent(10) == 100
    assert pressure_to_percent(None) is None


def test_no_phase_change_before_release():
    t = FlightTracker(ground_alt=100)
    fly(t, [sample(i, 100 + 10 * i, 5.0) for i in range(10)], released=False)
    assert t.phase == "pre-flight"


def test_full_flight_phases_and_burst_at_peak():
    t = FlightTracker(ground_alt=100)
    i = 0
    samples = []
    for _ in range(5):                                   # on the pad
        samples.append(sample(i, 100, 0.0)); i += 1
    alt = 100
    for _ in range(40):                                  # climbing at 5 m/s
        alt += 10
        samples.append(sample(i, alt, 5.0, pressure=1000 - alt // 20)); i += 1
    for rate in (1.0, -3.0, -7.0, -7.0):                 # parser's averaged rate ramps through burst
        alt += rate * 2
        samples.append(sample(i, alt, rate)); i += 1
    peak = max(s.altitude for s in samples)
    events = fly(t, samples)

    assert t.phase == "descent" and t.burst_detected
    assert t.peak[0] == peak
    assert [lvl for lvl, _ in events] == ["INFO", "WARNING"]
    assert "Ascent" in events[0][1] and f"{peak:.0f} m" in events[1][1]

    landing = [sample(i + k, 100, 0.0) for k in range(5)]
    events = fly(t, landing)
    assert t.phase == "landed"
    assert "Landing" in events[0][1]


def test_same_row_is_not_counted_twice():
    t = FlightTracker(ground_alt=100)
    s = sample(1, 120, 5.0)
    t.update(s, True)
    assert t.update(s, True) == []
    assert len(t.rates) == 1


def test_missing_gps_does_not_crash():
    t = FlightTracker()
    events = fly(t, [sample(i, None, None, pressure=None, temp=None, lat=None) for i in range(6)])
    assert events == [] and t.phase == "pre-flight"
    status = blank_status()
    apply_to_status(status, t, T0 + timedelta(seconds=20), True, None, None, None)
    assert status.gps_fix is False and status.sensor_ok is False and status.gps_degrad is None


def test_restart_mid_descent_keeps_burst():
    t = FlightTracker(phase="descent", burst_detected=True, ground_alt=100, peak_alt=30000)
    fly(t, [sample(i, 20000 - 14 * i, -7.0) for i in range(5)])
    assert t.phase == "descent" and t.burst_detected and t.peak[0] == 30000


def test_signal_level_uses_worst_recent_sample():
    assert signal_level([-60, -70]) == "green"
    assert signal_level([-60, -90]) == "yellow"
    assert signal_level([-60, -101]) == "red"
    assert signal_level([None]) is None


def test_alert_lights():
    t = FlightTracker(ground_alt=100)
    fly(t, [sample(i, 100, 0.0, temp=-45.0, hdop=7.0, rssi=-95, pressure=200) for i in range(3)])
    now = T0 + timedelta(seconds=30)
    sysstat = SimpleNamespace(receiver_state="running", parser_state="stopped", updated_at=now)
    status = blank_status()
    apply_to_status(status, t, now, False, calibration_ts=now - timedelta(seconds=10),
                    release_ts=None, sysstat=sysstat)

    assert status.receiver_ok and not status.parser_ok
    assert status.age_warn                   # last sample is 26 s old
    assert status.temp_low                   # 3 readings below −40 °C
    assert status.gps_fix and status.gps_degrad == "red"
    assert status.signal_level == "yellow"
    assert status.data_degrad                # 200 hPa is below the LPS22 range
    assert status.calibrated                 # fresh calibration before release
    assert status.packet_ok


def test_calibration_must_be_fresh_before_release_only():
    t = FlightTracker()
    now = T0 + timedelta(hours=1)
    old_cal = T0
    status = blank_status()
    apply_to_status(status, t, now, False, old_cal, None, None)
    assert status.calibrated is False
    apply_to_status(status, t, now, True, old_cal, release_ts=T0 + timedelta(minutes=10), sysstat=None)
    assert status.calibrated is True
