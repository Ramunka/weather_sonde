#!/usr/bin/env python3
"""
Supervisor script to launch and maintain receiver and parser processes,
and record their states in raw.system_status.
Run this at system startup (e.g., via systemd) to keep the pipeline alive.
"""
import sys
import time
import subprocess
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database URI
DB_URI = 'postgresql://sonde_user:securepassword@localhost/weather_sonde'

# Commands to supervise
CMDs = {
    'receiver': ['python3', '-m', 'backend.ingest.receiver'],
    'parser':   ['python3', '-m', 'backend.etl.parse_raw'],
    'analyzer': ['python3', '-m', 'analyzer']
}

# How often (seconds) to check process health
CHECK_INTERVAL = 2.0


def write_system_status(session, receiver_proc, parser_proc):
    """Upsert the one-and-only system_status row (id=1)."""
    r_state = receiver_proc.poll() is None and 'running' or 'stopped'
    p_state = parser_proc.poll()   is None and 'running' or 'stopped'

    session.execute(text("""
      INSERT INTO raw.system_status AS s (id, receiver_state, parser_state, updated_at)
           VALUES (1, :r, :p, now())
      ON CONFLICT (id) DO
        UPDATE SET receiver_state=EXCLUDED.receiver_state,
                   parser_state=EXCLUDED.parser_state,
                   updated_at=now()
    """), {'r': r_state, 'p': p_state})
    session.commit()


def main():
    # Set up database session
    engine = create_engine(DB_URI)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Launch initial processes
    procs = {}
    for name, cmd in CMDs.items():
        proc = subprocess.Popen(cmd)
        procs[name] = proc
        print(f"[{datetime.now().isoformat()}] Launched {name} (pid={proc.pid})")

    try:
        while True:
            # check & (re)start each
            for name, proc in list(procs.items()):
                ret = proc.poll()
                if ret is not None:
                    print(f"[{datetime.now().isoformat()}] {name} exited (code={ret}), restarting...")
                    proc = subprocess.Popen(CMDs[name])
                    procs[name] = proc
                    print(f"[{datetime.now().isoformat()}] Relaunched {name} (pid={proc.pid})")

            # write raw.system_status for receiver & parser
            write_system_status(session,
                                receiver_proc=procs['receiver'],
                                parser_proc=procs['parser'])

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("Shutting down supervised processes...")
        for proc in procs.values():
            proc.terminate()
        session.close()
        print("Supervisor exiting.")
        sys.exit(0)


if __name__ == '__main__':
    main()
