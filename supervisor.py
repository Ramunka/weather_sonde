#!/usr/bin/env python3
"""
Supervisor: starts and babysits the ground-station processes.

    receiver   backend.ingest.receiver   LoRa radio → raw.packets   (needs the Pi radio)
    parser     backend.etl.parse_raw     raw.packets → sonde.telemetry
    analyzer   backend.analysis.analyzer sonde.telemetry → sonde.flight_status
    flask      flask run                 operator web UI on FLASK_RUN_PORT (default 5000)

Crashed processes are restarted with exponential backoff (2 s doubling to 60 s, reset
after a process has stayed up for a minute). Every CHECK_INTERVAL the receiver and
parser states are written to raw.system_status, which drives the dashboard lights.

Usage:
    python supervisor.py                      # everything, log to stdout
    python supervisor.py --log-mode=file      # log to supervisor.log
    python supervisor.py --no-receiver        # development machine without the radio
    python supervisor.py --only parser analyzer

Stop with Ctrl+C; all children are terminated.
"""
import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from config import DATABASE_URL, ENGINE_OPTIONS, sqlalchemy_url

ROOT = Path(__file__).resolve().parent
PY = [sys.executable, "-u", "-m"]

COMMANDS = {
    "receiver": PY + ["backend.ingest.receiver"],
    "parser":   PY + ["backend.etl.parse_raw"],
    "analyzer": PY + ["backend.analysis.analyzer"],
    "flask":    PY + ["flask", "--app", "run.py", "run", "--host", os.getenv("FLASK_RUN_HOST", "0.0.0.0"),
                      "--port", os.getenv("FLASK_RUN_PORT", "5000")],
}

CHECK_INTERVAL = 2.0
BACKOFF_MIN, BACKOFF_MAX = 2.0, 60.0
STABLE_AFTER = 60.0
LOGFILE = ROOT / "supervisor.log"

_log_lock = threading.Lock()


def make_logger(log_mode, tag):
    def log(line):
        entry = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [{tag}] {line}"
        with _log_lock:
            if log_mode == "file":
                with open(LOGFILE, "a") as f:
                    f.write(entry + "\n")
            else:
                print(entry, flush=True)
    return log


class Managed:
    def __init__(self, name, cmd, log_mode):
        self.name, self.cmd, self.log_mode = name, cmd, log_mode
        self.log = make_logger(log_mode, name)
        self.proc = None
        self.started_at = 0.0
        self.backoff = BACKOFF_MIN
        self.restart_at = 0.0

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        self.proc = subprocess.Popen(
            self.cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env={**os.environ, "PYTHONPATH": str(ROOT)},
        )
        self.started_at = time.monotonic()
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        self.log(f"started (pid={self.proc.pid})")

    def _pump(self, proc):
        for line in proc.stdout:
            self.log(line.rstrip())

    def check(self):
        now = time.monotonic()
        if self.running:
            if now - self.started_at > STABLE_AFTER:
                self.backoff = BACKOFF_MIN
            return
        if self.restart_at == 0.0:
            self.log(f"exited (code={self.proc.returncode}); restarting in {self.backoff:.0f} s")
            self.restart_at = now + self.backoff
            self.backoff = min(self.backoff * 2, BACKOFF_MAX)
        elif now >= self.restart_at:
            self.restart_at = 0.0
            self.start()

    def stop(self):
        if self.running:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def write_system_status(engine, procs):
    def state(name):
        p = procs.get(name)
        if p is None:
            return "disabled"
        return "running" if p.running else "stopped"

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO raw.system_status (id, receiver_state, parser_state, updated_at)
            VALUES (1, :r, :p, now())
            ON CONFLICT (id) DO UPDATE
               SET receiver_state = EXCLUDED.receiver_state,
                   parser_state   = EXCLUDED.parser_state,
                   updated_at     = now()
        """), {"r": state("receiver"), "p": state("parser")})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-mode", choices=["stdout", "file"], default="stdout")
    parser.add_argument("--no-receiver", action="store_true", help="don't start the radio receiver")
    parser.add_argument("--only", nargs="+", choices=list(COMMANDS), help="start only these processes")
    args = parser.parse_args()

    names = args.only or list(COMMANDS)
    if args.no_receiver and "receiver" in names:
        names.remove("receiver")

    log = make_logger(args.log_mode, "supervisor")
    log(f"starting: {', '.join(names)}")

    engine = create_engine(sqlalchemy_url(DATABASE_URL), **ENGINE_OPTIONS)
    procs = {name: Managed(name, COMMANDS[name], args.log_mode) for name in names}
    for p in procs.values():
        p.start()

    def on_sigterm(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, on_sigterm)
    db_error_logged = False
    try:
        while True:
            for p in procs.values():
                p.check()
            try:
                write_system_status(engine, procs)
                db_error_logged = False
            except SQLAlchemyError as exc:
                if not db_error_logged:
                    log(f"could not write raw.system_status: {exc.__class__.__name__}: {exc}")
                    db_error_logged = True
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        log("shutting down")
        for p in procs.values():
            p.stop()
        log("all processes stopped")


if __name__ == "__main__":
    main()
