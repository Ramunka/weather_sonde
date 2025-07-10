#!/usr/bin/env python3
import threading
import time
import argparse
import random
import psycopg2
import sys
from datetime import datetime, timezone

# ── CONFIG ────────────────────────────────────────────────────────────────
DB_DSN       = "dbname=weather_sonde user=sonde_user password=securepassword host=localhost"
INTERVAL     = 2.0           # seconds between telemetry packets
GROUND_ELEV  = 100           # m
ASC_RATE     = 5.0           # m/s
DES_RATE     = -7.0          # m/s
BURST_ALT    = 30000         # m
LAT0, LNG0   = 47.5618, -122.0266
LAT_DRIFT    = 0.0001
LNG_DRIFT    = 0.0001
HUM0         = 60            # %
T0           = 20            # °C
SN           = 0x11951
MASK         = "D876EE"
# ───────────────────────────────────────────────────────────────────────────

stage   = 'ground'   # "ground", "ascent", "descent"
VERBOSE = False       # when False, simulate_loop will skip its printouts

def baro_pressure(h):
    return 1013.25 * (1 - 2.25577e-5 * h) ** 5.25588

def calc_token(device_sn: int, mask_hex: str) -> int:
    try:
        key = int(mask_hex, 16)
    except ValueError:
        key = 0
    return (device_sn ^ key) & 0xFFFFFF

def simulate_loop(flight_id, device_sn, token):
    global stage, VERBOSE
    conn = psycopg2.connect(DB_DSN)
    cur  = conn.cursor()
    tx_sec     = 0.0
    burst_done = False
    alt        = None
    lat, lng   = LAT0, LNG0

    while True:
        # altitude
        if stage == 'ground':
            alt = GROUND_ELEV + random.uniform(-1, 1)
        elif stage == 'ascent':
            alt = (alt if alt is not None else GROUND_ELEV) + ASC_RATE * INTERVAL
        elif stage == 'descent':
            alt = max(GROUND_ELEV, (alt if alt is not None else BURST_ALT) + DES_RATE * INTERVAL)
        else:
            alt = GROUND_ELEV

        # auto-burst
        if stage == 'ascent' and not burst_done and alt >= BURST_ALT:
            burst_done = True
            stage = 'descent'

        # drift
        lat += LAT_DRIFT + random.uniform(-LAT_DRIFT/2, LAT_DRIFT/2)
        lng += LNG_DRIFT + random.uniform(-LNG_DRIFT/2, LNG_DRIFT/2)

        # sensors
        pres = baro_pressure(alt)
        temp = T0 - 6.5 * (alt/1000.0) + random.uniform(-0.5,0.5)
        hum  = max(0, HUM0 - 0.01 * alt + random.uniform(-1,1))
        hdop = random.uniform(0.8,1.5)
        sats = random.randint(6,9)

        # payload
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = ",".join([
            f"{device_sn:05X}",
            f"{token:06X}",
            ts,
            f"{temp:.2f}",
            f"{hum:.2f}",
            f"{pres:.2f}",
            f"{lat:.5f}",
            f"{lng:.5f}",
            f"{alt:.1f}",
            f"{hdop:.2f}",
            f"{sats}"
        ]) + "\n"

        # write
        cur.execute("""
            INSERT INTO raw.packets (recv_ts, payload, rssi_dbm)
            VALUES (now(), %s, %s)
        """, (payload, -50))
        conn.commit()

        # conditional log
        if VERBOSE:
            print(f"[sim:{stage}] t={int(tx_sec)}s alt={alt:.1f}m pres={pres:.1f}hPa "
                  f"loc=({lat:.5f},{lng:.5f})", file=sys.stderr, flush=True)

        tx_sec += INTERVAL
        time.sleep(INTERVAL)

def main():
    global stage, VERBOSE
    p = argparse.ArgumentParser()
    p.add_argument('--flight', type=int, required=True)
    args = p.parse_args()

    token = calc_token(SN, MASK)
    print(f"Simulating flight {args.flight} → SN=0x{SN:05X}, TOK=0x{token:06X}")

    t = threading.Thread(
        target=simulate_loop,
        args=(args.flight, SN, token),
        daemon=True
    )
    t.start()

    print("Commands: ground | calibrate | release | burst | exit")
    while True:
        # mute logs while waiting
        VERBOSE = False
        cmd = input("> ").strip().lower()
        # re-enable logs immediately
        VERBOSE = True

        if cmd == 'ground':
            stage = 'ground'
            print("→ stage set to ground")
        elif cmd == 'calibrate':
            stage = 'ground'
            print("→ stage set to ground (post-calibr.)")
        elif cmd == 'release':
            stage = 'ascent'
            print("→ stage set to ascent")
        elif cmd == 'burst':
            stage = 'descent'
            print("→ stage set to descent")
        elif cmd in ('exit','quit'):
            print("Exiting…")
            break
        else:
            print("Unknown. Valid: ground, calibrate, release, burst, exit")

if __name__ == "__main__":
    main()
