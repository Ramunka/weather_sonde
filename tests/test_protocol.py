from datetime import datetime, timedelta, timezone

import pytest

from backend.protocol import (
    RateEstimator, format_record, generate_token, normalize_mask, normalize_sn, parse_record,
)

T0 = datetime(2025, 6, 18, 19, 5, 9, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw, expected", [
    ("11951", "11951"), ("0x11951", "11951"), ("011951", "11951"), (" 11951a ", "11951A"),
    ("zz", None), ("", None),
])
def test_normalize_sn(raw, expected):
    assert normalize_sn(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("d876ee", "D876EE"), ("1", "000001"), ("1000000", None), ("xyz", None),
])
def test_normalize_mask(raw, expected):
    assert normalize_mask(raw) == expected


def test_token_matches_firmware_formula():
    # provision_eeprom.ino: (NEW_SN ^ KEY_MASK) & 0xFFFFFF
    assert generate_token(0x11951, "5B5A5B") == (0x11951 ^ 0x5B5A5B) & 0xFFFFFF
    assert generate_token(0x11951, "not-hex") == 0x11951


def test_parse_firmware_record():
    r = parse_record("11951,4AC3BF,2025-06-18T19:05:09Z,21.43,58.10,1012.85,47.56180,-122.02660,101.20,0.94,8")
    assert r.device_sn == 0x11951 and r.token == 0x4AC3BF
    assert r.measurement_ts == T0
    assert (r.temperature, r.humidity, r.pressure) == (21.43, 58.10, 1012.85)
    assert (r.latitude, r.longitude, r.altitude, r.hdop, r.sats) == (47.5618, -122.0266, 101.2, 0.94, 8)
    assert r.has_fix


def test_parse_record_without_gps():
    r = parse_record("11951,4AC3BF,,21.43,58.10,1012.85,nan,nan,nan,nan,0")
    assert r.measurement_ts is None
    assert r.latitude is None and r.altitude is None and r.hdop is None
    assert not r.has_fix
    assert r.temperature == 21.43


@pytest.mark.parametrize("line", ["", "11951,4AC3BF,2025", "XYZ,4AC3BF,,1,2,3,4,5,6,7,8"])
def test_parse_record_rejects_bad_header(line):
    with pytest.raises(ValueError):
        parse_record(line)


def test_format_parse_roundtrip():
    line = format_record(0x11951, 0x4AC3BF, T0, -40.5, 12.0, 54.3, 47.6, -122.1, 21000.0, 1.1, 9)
    r = parse_record(line)
    assert (r.device_sn, r.token, r.measurement_ts) == (0x11951, 0x4AC3BF, T0)
    assert (r.temperature, r.pressure, r.altitude, r.sats) == (-40.5, 54.3, 21000.0, 9)


def test_rate_estimator_ascent_and_speed():
    est = RateEstimator()
    assert est.add(T0, 100, 47.0, -122.0) == (None, None)
    # 10 m up and ~111 m north every 2 s → 5 m/s, ~108 kt
    for i in range(1, 4):
        ascent, speed = est.add(T0 + timedelta(seconds=2 * i), 100 + 10 * i, 47.0 + 0.001 * i, -122.0)
    assert ascent == 5.0
    assert 105 < speed < 110


def test_rate_estimator_suppresses_noise_and_ignores_missing_fix():
    est = RateEstimator()
    est.add(T0, 100.0, 47.0, -122.0)
    assert est.add(T0 + timedelta(seconds=2), 100.4, 47.0, -122.0) == (0.0, 0.0)
    assert est.add(None, None, None, None) == (0.0, 0.0)  # no fix: previous estimate stands


def test_rate_estimator_resets_on_clock_jump():
    est = RateEstimator()
    est.add(T0, 100, 47.0, -122.0)
    est.add(T0 + timedelta(seconds=2), 110, 47.0, -122.0)
    assert est.add(T0 - timedelta(hours=1), 500, 47.0, -122.0) == (None, None)
