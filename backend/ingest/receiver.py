#!/usr/bin/env python3
"""
LoRa receiver: RFM9x radio (915 MHz) → raw.packets

Runs on the Raspberry Pi with the radio wired to SPI (CS = D17, RESET = D25).
Every received packet is stored verbatim with its RSSI; parsing happens in
backend/etl/parse_raw.py. Requires requirements-pi.txt.
"""
from datetime import datetime, timezone

import adafruit_rfm9x
import board
import busio
import digitalio
import psycopg2

from config import INGEST_DATABASE_URL, PG_OPTIONS, psycopg2_dsn

FREQUENCY_MHZ = 915.0
RECEIVE_TIMEOUT_S = 5.0


def open_radio():
    spi = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
    cs = digitalio.DigitalInOut(board.D17)
    reset = digitalio.DigitalInOut(board.D25)
    return adafruit_rfm9x.RFM9x(spi, cs, reset, FREQUENCY_MHZ)


def main():
    conn = psycopg2.connect(psycopg2_dsn(INGEST_DATABASE_URL), options=PG_OPTIONS)
    conn.autocommit = True
    radio = open_radio()
    print(f"RFM9x receiver initialized at {FREQUENCY_MHZ} MHz")

    while True:
        packet = radio.receive(timeout=RECEIVE_TIMEOUT_S)
        if packet is None:
            continue

        try:
            payload = bytes(packet).decode("utf-8")
        except UnicodeDecodeError:
            payload = repr(packet)

        recv_ts = datetime.now(timezone.utc)
        rssi = radio.last_rssi
        snr = getattr(radio, "last_snr", None)

        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO raw.packets (recv_ts, payload, rssi_dbm, snr_db) VALUES (%s, %s, %s, %s)",
                (recv_ts, payload, rssi, snr),
            )
        print(f"[RAW] {recv_ts:%H:%M:%S} RSSI={rssi} dBm payload={payload!r}")


if __name__ == "__main__":
    main()
