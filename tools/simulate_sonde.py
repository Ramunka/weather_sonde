#!/usr/bin/env python3
"""
Sonde simulator: writes realistic fake packets into raw.packets, bypassing the radio,
so the parser → analyzer → dashboard pipeline can be exercised on any machine.

The SN and mask must match a registered device and an active flight:

    python -m tools.simulate_sonde --sn 11951 --mask D876EE
    python -m tools.simulate_sonde --sn 11951 --mask D876EE --auto     # full flight, no prompt

Interactive commands:  ground | release | burst | land | status | exit
"""
import argparse
import random
import sys
import threading
from datetime import datetime, timezone

import psycopg2

from config import DATABASE_URL, PG_OPTIONS, psycopg2_dsn
from backend.protocol import format_record, generate_token


def baro_pressure(h):
    """International standard atmosphere, hPa at altitude h metres (troposphere formula)."""
    return 1013.25 * max(0.0, 1 - 2.25577e-5 * h) ** 5.25588


class Sonde:
    def __init__(self, args):
        self.args = args
        self.sn = int(args.sn, 16)
        self.token = generate_token(self.sn, args.mask)
        self.stage = "ground"
        self.alt = args.ground_elev
        self.lat, self.lng = args.lat, args.lng
        self.lock = threading.Lock()

    def step(self, dt):
        a = self.args
        with self.lock:
            if self.stage == "ascent":
                self.alt += a.ascent_rate * dt * random.uniform(0.9, 1.1)
                if self.alt >= a.burst_alt:
                    self.stage = "descent"
            elif self.stage == "descent":
                # Parachute descent is fast in thin air and slows as it gets denser.
                rate = a.descent_rate * (1 + self.alt / 15000)
                self.alt = max(a.ground_elev, self.alt - rate * dt)
                if self.alt <= a.ground_elev:
                    self.stage = "landed"
            if self.stage in ("ground", "landed"):
                self.alt = a.ground_elev + random.uniform(-1, 1)
            else:
                wind = min(1.0, self.alt / 10000)
                self.lat += 0.00008 * wind * dt + random.uniform(-2e-6, 2e-6)
                self.lng += 0.00012 * wind * dt + random.uniform(-2e-6, 2e-6)

            alt = self.alt
            pressure = baro_pressure(alt)
            temp = max(-56.5, a.ground_temp - 6.5 * alt / 1000) + random.uniform(-0.3, 0.3)
            hum = min(100, max(0, a.ground_humidity - 0.004 * alt + random.uniform(-1, 1)))
            rssi = -50 - 50 * min(1.0, alt / 30000) + random.uniform(-3, 3)

        line = format_record(
            self.sn, self.token, datetime.now(timezone.utc),
            temp, hum, pressure, self.lat, self.lng, alt,
            random.uniform(0.7, 1.4), random.randint(7, 11))
        return line, rssi


def transmit_loop(sonde, interval, batch, stop):
    conn = psycopg2.connect(psycopg2_dsn(DATABASE_URL), options=PG_OPTIONS)
    conn.autocommit = True
    records, rssi = [], None
    while not stop.is_set():
        line, rssi = sonde.step(interval)
        records.append(line)
        if len(records) >= batch:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO raw.packets (recv_ts, payload, rssi_dbm) VALUES (now(), %s, %s)",
                            ("\n".join(records) + "\n", round(rssi, 1)))
            records.clear()
            if sonde.args.verbose:
                print(f"[sim] {sonde.stage:8s} alt={sonde.alt:8.1f} m", file=sys.stderr)
        stop.wait(interval)
    conn.close()


def auto_script(sonde, stop):
    """Sit on the pad for --ground-time seconds, then release; burst and landing follow."""
    stop.wait(sonde.args.ground_time)
    print("→ releasing")
    sonde.stage = "ascent"
    while not stop.is_set() and sonde.stage != "landed":
        stop.wait(2)
    print("→ landed")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sn", required=True, help="device SN (hex), as registered")
    p.add_argument("--mask", required=True, help="flight mask (hex), as shown on the flight page")
    p.add_argument("--interval", type=float, default=2.0, help="seconds between measurements")
    p.add_argument("--batch", type=int, default=2, help="measurements per packet (firmware uses 2)")
    p.add_argument("--speed", type=float, default=1.0, help="simulation speed-up for climb/descent")
    p.add_argument("--auto", action="store_true", help="run a whole flight without prompting")
    p.add_argument("--ground-time", type=float, default=20, help="seconds on the pad before --auto releases")
    p.add_argument("--burst-alt", type=float, default=30000)
    p.add_argument("--ascent-rate", type=float, default=5.0)
    p.add_argument("--descent-rate", type=float, default=7.0)
    p.add_argument("--ground-elev", type=float, default=100)
    p.add_argument("--ground-temp", type=float, default=20)
    p.add_argument("--ground-humidity", type=float, default=60)
    p.add_argument("--lat", type=float, default=47.5618)
    p.add_argument("--lng", type=float, default=-122.0266)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()
    args.ascent_rate *= args.speed
    args.descent_rate *= args.speed

    sonde = Sonde(args)
    print(f"Simulating SN=0x{sonde.sn:05X} token=0x{sonde.token:06X} every {args.interval}s")

    stop = threading.Event()
    tx = threading.Thread(target=transmit_loop, args=(sonde, args.interval, args.batch, stop), daemon=True)
    tx.start()

    try:
        if args.auto:
            auto_script(sonde, stop)
            stop.wait(10)
            return

        print("Commands: ground | release | burst | land | status | exit")
        stages = {"ground": "ground", "release": "ascent", "burst": "descent", "land": "landed"}
        while True:
            cmd = input("> ").strip().lower()
            if cmd in stages:
                with sonde.lock:
                    sonde.stage = stages[cmd]
                print(f"→ stage: {sonde.stage}")
            elif cmd == "status":
                print(f"stage={sonde.stage} alt={sonde.alt:.1f} m pos=({sonde.lat:.5f}, {sonde.lng:.5f})")
            elif cmd in ("exit", "quit"):
                break
            elif cmd:
                print("Unknown command. Valid: ground, release, burst, land, status, exit")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        stop.set()
        tx.join(timeout=5)


if __name__ == "__main__":
    main()
