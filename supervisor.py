"""
USAGE GUIDE
===========

This script supervises key processes:
- receiver
- parser
- analyzer
- Flask web server

It auto-restarts crashed processes and updates system status in the database.

Run with:
    python3 supervisor.py --log-mode=stdout     # Log to terminal (default)
    python3 supervisor.py --log-mode=file       # Log to 'supervisor.log'

Log format (in file mode):
    [YYYY-MM-DD HH:MM:SS] [process_name] message

Flask server runs at:
    http://0.0.0.0:5000 (requires FLASK_APP to be set)

Stop with CTRL+C to terminate all processes cleanly.
"""
import sys
import time
import subprocess
import argparse
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Database URI
DB_URI = 'postgresql://sonde_user:securepassword@localhost/weather_sonde'

# Commands to supervise
CMDs = {
    'receiver': ['python3', '-u', '-m', 'backend.ingest.receiver'],
    'parser':   ['python3', '-u', '-m', 'backend.etl.parse_raw'],
    'analyzer': ['python3', '-u', '-m', 'analyzer'],
    'flask':    ['flask', 'run', '--host=0.0.0.0', '--port=5000']
}

CHECK_INTERVAL = 2.0  # seconds

LOGFILE = 'supervisor.log'

def write_system_status(session, receiver_proc, parser_proc):
    r_state = receiver_proc.poll() is None and 'running' or 'stopped'
    p_state = parser_proc.poll() is None and 'running' or 'stopped'

    session.execute(text("""
      INSERT INTO raw.system_status AS s (id, receiver_state, parser_state, updated_at)
           VALUES (1, :r, :p, now())
      ON CONFLICT (id) DO
        UPDATE SET receiver_state=EXCLUDED.receiver_state,
                   parser_state=EXCLUDED.parser_state,
                   updated_at=now()
    """), {'r': r_state, 'p': p_state})
    session.commit()


def timestamp():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def make_logger(log_mode, tag):
    """Returns a logging function for the given tag."""
    def log(line):
        if log_mode == 'stdout':
            print(f"[{timestamp()}] [{tag}] {line}", flush=True)
        elif log_mode == 'file':
            with open(LOGFILE, 'a') as f:
                f.write(f"[{timestamp()}] [{tag}] {line}\n")
    return log


def launch_process(name, cmd, log_mode):
    """Launch a subprocess with logging, return process handle and logger."""
    log = make_logger(log_mode, name)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # Start a background thread to read output and tag lines
    import threading
    def reader():
        for line in proc.stdout:
            log(line.strip())

    threading.Thread(target=reader, daemon=True).start()

    log(f"Started {name} (pid={proc.pid})")
    return proc


def main():
    # Parse CLI args
    parser = argparse.ArgumentParser()
    parser.add_argument('--log-mode', choices=['stdout', 'file'], default='stdout',
                        help="Log output mode")
    args = parser.parse_args()

    print(f"Supervisor starting with log mode: {args.log_mode}")

    # Set up database session
    engine = create_engine(DB_URI)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Launch and track all processes
    procs = {}
    for name, cmd in CMDs.items():
        procs[name] = launch_process(name, cmd, args.log_mode)

    try:
        while True:
            # Check and restart if needed
            for name, cmd in CMDs.items():
                proc = procs[name]
                if proc.poll() is not None:
                    logger = make_logger(args.log_mode, name)
                    logger(f"Process exited (code={proc.returncode}), restarting...")
                    new_proc = launch_process(name, cmd, args.log_mode)
                    procs[name] = new_proc

            # Periodically update system status for receiver/parser
            write_system_status(session,
                                receiver_proc=procs['receiver'],
                                parser_proc=procs['parser'])

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("Shutting down all processes...")
        for proc in procs.values():
            proc.terminate()
        session.close()
        print("Supervisor exiting.")
        sys.exit(0)


if __name__ == '__main__':
    main()
