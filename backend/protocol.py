"""
Sonde radio protocol: shared by the parser, the simulator and the web app.

Each LoRa packet carries one or more newline-terminated CSV records:

    SN,TOKEN,UTC,TEMP,HUM,PRES,LAT,LNG,ALT,HDOP,SATS

    SN     device serial number, hex (e.g. 11951)
    TOKEN  (SN XOR flight mask) & 0xFFFFFF, hex; authenticates the record for one flight
    UTC    GPS time 'YYYY-MM-DDTHH:MM:SSZ', empty until the GPS has a time fix
    TEMP   °C          HUM  %RH          PRES  hPa
    LAT    deg         LNG  deg          ALT   m (GPS)
    HDOP   GPS dilution of precision     SATS  satellites in use
Invalid sensor/GPS values are sent as 'nan'.
"""
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

FIELD_COUNT = 11
TOKEN_BITS = 0xFFFFFF


def normalize_sn(value):
    """Canonical device SN: uppercase hex, no '0x', no leading zeros. None if not hex."""
    text = (value or "").strip().upper()
    if text.startswith("0X"):
        text = text[2:]
    try:
        return format(int(text, 16), "X")
    except ValueError:
        return None


def normalize_mask(value):
    """Canonical 24-bit flight mask as 6 uppercase hex digits. None if invalid."""
    try:
        mask = int((value or "").strip(), 16)
    except ValueError:
        return None
    return format(mask, "06X") if 0 <= mask <= TOKEN_BITS else None


def generate_token(device_sn: int, mask: str) -> int:
    try:
        key = int(mask, 16)
    except (TypeError, ValueError):
        key = 0
    return (device_sn ^ key) & TOKEN_BITS


def _float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


@dataclass
class Record:
    device_sn: int
    token: int
    measurement_ts: Optional[datetime]
    temperature: Optional[float]
    humidity: Optional[float]
    pressure: Optional[float]
    latitude: Optional[float]
    longitude: Optional[float]
    altitude: Optional[float]
    hdop: Optional[float]
    sats: Optional[int]

    @property
    def has_fix(self):
        return self.latitude is not None and self.longitude is not None


def parse_record(line):
    """Parse one CSV record; raises ValueError if the header (SN/token) is malformed."""
    cols = [c.strip() for c in line.strip().split(",")]
    if len(cols) < FIELD_COUNT:
        raise ValueError(f"expected {FIELD_COUNT} fields, got {len(cols)}")

    device_sn = int(cols[0], 16)
    token = int(cols[1], 16)

    try:
        measurement_ts = datetime.strptime(cols[2], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        measurement_ts = None

    sats = _float(cols[10])
    return Record(
        device_sn=device_sn,
        token=token,
        measurement_ts=measurement_ts,
        temperature=_float(cols[3]),
        humidity=_float(cols[4]),
        pressure=_float(cols[5]),
        latitude=_float(cols[6]),
        longitude=_float(cols[7]),
        altitude=_float(cols[8]),
        hdop=_float(cols[9]),
        sats=int(sats) if sats is not None else None,
    )


def format_record(device_sn, token, ts, temp, hum, pres, lat, lng, alt, hdop, sats):
    """Inverse of parse_record, matching the firmware's output format."""
    def num(value, prec):
        return "nan" if value is None else f"{value:.{prec}f}"

    utc = ts.strftime("%Y-%m-%dT%H:%M:%SZ") if ts else ""
    return ",".join([
        f"{device_sn:05X}", f"{token:06X}", utc,
        num(temp, 2), num(hum, 2), num(pres, 2),
        num(lat, 5), num(lng, 5), num(alt, 2), num(hdop, 2),
        str(sats or 0),
    ])


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


class RateEstimator:
    """
    Ascent rate (m/s) and ground speed (knots) averaged over the last few GPS samples.
    Values below the noise thresholds are reported as 0.0.
    """
    MS_TO_KNOTS = 1.94384

    def __init__(self, window=4, ascent_noise=0.5, speed_noise=0.5):
        self.window = window
        self.ascent_noise = ascent_noise
        self.speed_noise = speed_noise
        self.history = []  # (ts, alt, lat, lng)

    def add(self, ts, alt, lat, lng):
        """Add a sample and return (ascent_rate, ground_speed); either may be None."""
        if ts is not None and None not in (alt, lat, lng):
            if self.history and ts <= self.history[-1][0]:
                self.history.clear()  # clock jumped backwards / duplicate
            self.history.append((ts, alt, lat, lng))
            del self.history[:-self.window]

        if len(self.history) < 2:
            return None, None

        ascent, speed = [], []
        for (t1, a1, la1, lo1), (t2, a2, la2, lo2) in zip(self.history, self.history[1:]):
            dt = (t2 - t1).total_seconds()
            ascent.append((a2 - a1) / dt)
            speed.append(haversine_m(la1, lo1, la2, lo2) / dt)

        avg_ascent = sum(ascent) / len(ascent)
        avg_knots = sum(speed) / len(speed) * self.MS_TO_KNOTS
        return (
            round(avg_ascent, 1) if abs(avg_ascent) >= self.ascent_noise else 0.0,
            round(avg_knots, 1) if avg_knots >= self.speed_noise else 0.0,
        )
