#!/usr/bin/env python3
import os
from datetime import datetime, timezone
import psycopg2
import board
import busio
import digitalio
import adafruit_rfm9x

from app.models import FlightStatus

# ── DATABASE SETUP ────────────────────────────────────────────────────────────

# Use an env-var for your connection; set DATABASE_URL like:
#   postgres://username:password@host:port/dbname
dsn = os.getenv(
    "DATABASE_URL",
    "dbname=weather_sonde user=ingest_user password=strong_ingest_password host=localhost")
conn = psycopg2.connect(dsn)
conn.autocommit = True
cur = conn.cursor()

# ── RADIO SETUP (unchanged) ───────────────────────────────────────────────────
spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
cs  = digitalio.DigitalInOut(board.D17)
rst = digitalio.DigitalInOut(board.D25)
erf = adafruit_rfm9x.RFM9x(spi, cs, rst, 915.0)
erf.tx_power = 14

print("RFM9x receiver initialized at 915 MHz")

# ── MAIN LOOP ─────────────────────────────────────────────────────────────────
while True:
    packet = erf.receive(timeout=5.0)
    if packet is None:
        print(".", end="", flush=True)
        continue

    # 1) Decode raw payload
    try:
        payload = bytes(packet).decode("utf-8")
    except UnicodeDecodeError:
        payload = repr(packet)

    # 2) Note reception metadata
    recv_ts = datetime.now(timezone.utc).replace(microsecond=0)
    rssi    = erf.rssi

    # 3) INSERT into raw.packets
    cur.execute(
        """
        INSERT INTO raw.packets (recv_ts, payload, rssi_dbm)
         VALUES (%s, %s, %s)
        """,
        (recv_ts, payload, rssi),
    )
    print(f"\n[RAW] {recv_ts}  RSSI={rssi}dBm  payload={payload!r}")
