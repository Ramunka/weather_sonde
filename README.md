# Weather Sonde

Ground station for a home-built weather balloon radiosonde. An Arduino-based sonde
measures temperature, humidity, pressure and GPS position, and transmits them over LoRa
radio. A Raspberry Pi on the ground receives the packets, stores them in PostgreSQL,
works out what the balloon is doing (climbing, burst, falling, landed) and serves a live
operator dashboard in the browser.

![Live flight dashboard during descent](docs/images/dashboard.png)
<sub>The live flight dashboard a few minutes after burst, during a simulated flight. Each panel is explained in [The flight dashboard](#the-flight-dashboard).</sub>

**Contents:** [System overview](#system-overview) ·
[Hardware](#hardware) · [Radio protocol](#radio-protocol) ·
[Data pipeline](#data-pipeline) · [Database](#database) ·
[Flight lifecycle](#flight-lifecycle) · [Flight-phase detection](#flight-phase-detection) ·
[The flight dashboard](#the-flight-dashboard) · [Setup](#setup) ·
[Flying a mission](#flying-a-mission) · [Testing without hardware](#testing-without-hardware) ·
[Configuration](#configuration) · [Repository layout](#repository-layout)

---

## System overview

```
┌─ Balloon payload ──────────┐               ┌─ Ground station (Raspberry Pi) ────────────┐
│ Arduino                    │               │ receiver ─► raw.packets ─► parser          │
│ ├ TMP117   temperature     │ LoRa 915 MHz  │                               │            │
│ ├ SHTC3    humidity        │ ────────────► │                               ▼            │
│ ├ LPS22    pressure        │               │ analyzer ◄─ sonde.telemetry ◄─┘            │
│ ├ GPS      position, time  │               │    │                                       │
│ └ RFM95    LoRa radio      │               │    ▼                                       │
└────────────────────────────┘               │ sonde.flight_status ─► Flask dashboard     │
                                             │                                            │
                                             │ supervisor.py: starts, restarts and        │
                                             │ monitors every process                     │
                                             └────────────────────────────────────────────┘
```

The ground station is four independent processes. They never talk to each other directly;
each one reads from and writes to PostgreSQL, so any of them can crash and restart without
losing data or stopping the rest:

| Process | Reads | Writes | Job |
|---|---|---|---|
| **receiver** | LoRa radio | `raw.packets` | Store every packet exactly as received, plus its signal strength |
| **parser** | `raw.packets` | `sonde.telemetry` | Decode, authenticate and enrich each measurement |
| **analyzer** | `sonde.telemetry` | `sonde.flight_status`, `sonde.logs` | Detect flight phase and burst, compute alert lights |
| **web app** | everything | flights, devices, calibration | Operator UI, flight controls, live dashboard |

`supervisor.py` launches all four, restarts any that exit (waiting longer after each
repeated crash), and records once every 2 s whether the receiver and parser are alive. The
dashboard's RECEIVER and PARSER lights come from that.

## Hardware

**Sonde (flight unit), `firmware/sonde/sonde.ino`**

| Part | Measures | Bus |
|---|---|---|
| TMP117 | Air temperature (±0.1 °C) | I²C |
| SHTC3 | Relative humidity | I²C |
| LPS22 | Barometric pressure (rated 260–1260 hPa) | SPI, CS pin 8 |
| GPS module (TinyGPS++) | Latitude, longitude, altitude, UTC time, HDOP, satellites | Hardware serial, 9600 baud |
| RFM95 LoRa | 915 MHz transmitter, 14 dBm | SPI, CS pin 10, IRQ pin 2 |

Every 2 s the firmware reads all sensors and appends one text record to a buffer. Every
second record (so every ~4 s) it wakes the radio, sends both records in one packet, and puts
the radio back to sleep to save battery. The device serial number and authentication token
are stored in EEPROM by `firmware/provision_eeprom`.

**Ground station:** a Raspberry Pi with an RFM9x LoRa module on SPI (CS = D17, RESET = D25),
running PostgreSQL and the Python processes.

## Radio protocol

Each packet holds one or more newline-terminated CSV records (`backend/protocol.py`):

```
SN,TOKEN,UTC,TEMP,HUM,PRES,LAT,LNG,ALT,HDOP,SATS
11951,D96FBF,2025-06-18T19:05:09Z,21.43,58.10,1012.85,47.56180,-122.02660,101.20,0.94,8
```

| Field | Meaning |
|---|---|
| `SN` | Device serial number, hex |
| `TOKEN` | `(SN XOR flight mask) & 0xFFFFFF`, hex. Ties the record to one flight |
| `UTC` | GPS time of the measurement; empty until the GPS has a time fix |
| `TEMP`, `HUM`, `PRES` | °C, %RH, hPa |
| `LAT`, `LNG`, `ALT`, `HDOP`, `SATS` | GPS position, altitude (m), precision, satellites; `nan` without a fix |

**Why the token?** Several sondes (or old, still-transmitting ones) may be on the air at the
same time. When an operator creates a flight, the web app generates a random 24-bit **mask**.
The sonde is provisioned with `token = SN XOR mask`, and the parser accepts a record only if
the token matches an active flight for that device. A stray packet from last week's sonde
won't end up in today's flight. This is a filter, not encryption.

## Data pipeline

1. **Receive.** `backend/ingest/receiver.py` waits on the radio and inserts each packet
   verbatim into `raw.packets` with its receive time, RSSI and SNR. Nothing is parsed here,
   so a parser bug can never lose data: the raw packets can always be re-processed.

2. **Parse.** A database trigger fires `NOTIFY packet_inserted` on every insert. The parser
   (`backend/etl/parse_raw.py`) wakes immediately (and also polls every 5 s as a fallback),
   then for each unprocessed packet:
   - splits it into records and parses the fields (`nan` becomes NULL);
   - finds the active flight whose mask produces the record's token, and drops the record if none does;
   - derives **ascent rate** (m/s) and **ground speed** (knots) from the last four GPS fixes,
     with small values treated as noise and reported as 0;
   - inserts a `sonde.telemetry` row and marks the packet processed.

   Each packet is its own transaction. A packet that fails is logged and skipped, so it
   can't block the queue.

3. **Analyze.** Once a second `backend/analysis/analyzer.py` takes every active flight's new
   telemetry rows, runs them through a per-flight state machine (see
   [Flight-phase detection](#flight-phase-detection)) and writes the result to that flight's
   single `sonde.flight_status` row. Phase changes and the burst are also written to the event log.

4. **Display.** The dashboard page polls four JSON endpoints: telemetry and status every 2 s,
   GPS track every 4 s, event log every 5 s. It updates in place without reloading.

**Timestamps.** Every telemetry row keeps three times: `measurement_ts` (GPS time on the
sonde, when the reading was taken), `timestamp` (when the ground station received it) and
`processed_ts` (when the parser stored it). All are stored in UTC.

## Database

PostgreSQL with two schemas:

- **`raw`**, the ingest side, defined in `db/raw_schema.sql`:
  - `packets`: one row per radio packet, with a `processed` flag
  - `system_status`: supervisor heartbeat (receiver and parser states)
- **`sonde`**, the application side, managed by Alembic migrations:

| Table | Contents |
|---|---|
| `users` | Operator accounts (hashed passwords) |
| `devices` | Registered sondes by serial number |
| `flights` | One row per mission: device, mask, status, planned launch, launch site |
| `telemetry` | One row per measurement: sensors, GPS, derived ascent rate and speed, RSSI |
| `flight_status` | One row per flight, kept current by the analyzer: phase, burst, extremes, gauge positions, alert lights |
| `ground_reference` | Snapshot of the readings when the operator pressed *Calibrate ground* |
| `logs` | Event log shown on the dashboard |
| `data_selection` | Post-flight: the verified time window and excluded outliers |

## Flight lifecycle

```
 New flight ──► pre-flight ──── Release ────► flight ──── End flight ────► post-flight (Archive)
                  │                             │                               │
                  ├ parser already accepts      ├ analyzer tracks phases:       └ Verify data: pick the clean
                  │ packets: check GPS/sensors  │ ascent → burst → descent        time window, exclude
                  └ Calibrate ground            │ → landed                        outliers
                    (snapshot of pad readings)  └ map, gauge, alerts update live
```

## Flight-phase detection

The analyzer keeps a short history per flight: the last 5 ascent rates, 3 temperatures and
5 signal readings. It only reacts after the operator presses **Release**:

| Transition | Condition |
|---|---|
| pre-flight → **ascent** | Average ascent rate above +0.6 m/s **and** more than 5 m above the ground baseline |
| ascent → **descent** (burst) | One reading below −3 m/s, **or** the average drops below −0.6 m/s |
| descent → **landed** | Average vertical speed within ±0.3 m/s over a full 5-sample window |

The **ground baseline** is the calibrated altitude if available, otherwise the launch
elevation entered for the flight. The **burst** is logged at the highest point recorded,
not where it was noticed, so the burst altitude is the true peak. All history is rebuilt from
the database if the analyzer restarts mid-flight.

### Alert lights

| Light | Green when… | Otherwise |
|---|---|---|
| RECEIVER / PARSER | The process is running (supervisor heartbeat less than 15 s old) | Red |
| SENSOR | Temperature, humidity and pressure are all present | Red |
| SIGNAL | Worst RSSI of the last 5 packets ≥ −85 dBm | Orange ≥ −100 dBm, red below |
| PACKET | Gap between received packets ≤ 10 s | Orange |
| AGE | Latest measurement is less than 10 s old | Blinking red |
| CALIBRATED | Before release: calibrated in the last 5 min. After release: calibrated before launch | Orange if stale, dark if never |
| BURST | (red once burst has been detected) | Dark |
| TEMP LOW | (orange after 3 readings below −40 °C) | Dark |
| MEAS DEGRAD | (orange when pressure is outside the LPS22's rated range, which is normal above ~10 km, or humidity is outside 0–100 %) | Dark |
| GPS FIX | Position available | Red |
| GPS DEGRAD | (orange when HDOP > 3, red when HDOP > 6 or fewer than 4 satellites) | Dark |

Apart from RECEIVER and PARSER, the lights stay dark until the flight has sent its first measurement.

## The flight dashboard

What each part of the [screenshot above](#weather-sonde) shows. It was taken during a
simulated flight running about 6× faster than real time, which is why the descent rate is so
high, and on a machine without the radio, which is why RECEIVER is red.

**Header.** The mission name with two badges: the flight's status (`flight`) and the phase
the analyzer has detected (`descent`). Below are the device serial, flight mask, equipment
and planned launch time, then live UTC and local clocks. On the right are the three
controls: **Calibrate ground** and **Release sonde** are greyed out because the balloon is
already up, and **End flight** archives the mission when it's recovered.

**Live telemetry (left).** The latest values for temperature, humidity, pressure, GPS
altitude, ascent rate and ground speed, plus the flight's peak and burst altitude. Next to
them is the **pressure-profile gauge**: a vertical scale from 1000 hPa (ground) at the bottom
to 100 hPa (about 16 km) at the top. While climbing, a 🎈 rides up the scale. After burst, 💥
marks the pressure where the balloon burst (470 hPa, about 6 km, in the screenshot) and 🪂
shows where the payload is now on its way down.

**Alerts (middle).** The twelve health lights described above. In the screenshot everything
needed for tracking is green, BURST is red because the balloon has burst, and RECEIVER is red
because the radio process isn't running in this simulation. Below the lights: link status
(*Online* while measurements are less than 6 s old), how long ago the sonde was last heard,
signal strength, satellites in use and GPS precision (HDOP).

**Launch site (right).** Where the flight was launched from, and when it was calibrated and
released. **Antenna aiming** takes the position of the ground antenna (pre-filled with the
launch site) and calculates, from the latest GPS fix, the compass **azimuth** and
**elevation** angle to point a directional antenna at, plus the straight-line range. It
updates with every new position.

**GPS track (bottom left).** The flight path on OpenStreetMap, with markers at launch, burst
and the latest position (click them for time and altitude). Points are thinned to every fifth
fix to keep the map fast. After calibration, only positions from that moment on are drawn,
so testing on the bench doesn't clutter the track.

**Event log (bottom right).** A timeline of the flight. Operator actions (flight created,
calibrated, released, ended) and analyzer detections (ascent, burst with altitude and
pressure, landing) appear here automatically, newest first.

## Setup

Requires Python 3.10+ and PostgreSQL 13+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt            # on the Pi with the radio: requirements-pi.txt

cp .env.example .env                       # then set DATABASE_URL and SECRET_KEY

createdb weather_sonde
flask db upgrade                           # creates the 'sonde' schema
psql -d weather_sonde -f db/raw_schema.sql # creates the 'raw' schema + NOTIFY trigger
# optional least-privilege roles: sudo -u postgres psql -d weather_sonde -f db/roles.sql
```

### Upgrading a database from the old migration chain

The pre-2026 migrations couldn't build a fresh database, so they were squashed into one
baseline (`0001_initial`). To move an existing database over without losing data:

```bash
pg_dump weather_sonde > backup.sql
psql -d weather_sonde -f db/raw_schema.sql
psql -d weather_sonde -f db/align_existing_db.sql
flask db stamp --purge 0001_initial
flask db check                             # → "No new upgrade operations detected."
```

## Running

```bash
python supervisor.py                # receiver + parser + analyzer + web UI on :5000
python supervisor.py --no-receiver  # on a machine without the radio
python supervisor.py --log-mode=file
```

Open `http://<pi-address>:5000`, create the first operator account, then set
`ALLOW_SIGNUP=false` in `.env` so nobody else on the network can sign up.

## Flying a mission

1. **Register the device** with its hex serial number (e.g. `11951`).
2. **Create a flight**: pick the device, click **Generate** for a mask, and enter the
   mission name, planned launch time and launch site.
3. **Provision the sonde**: put the SN and the new mask into `firmware/provision_eeprom`,
   upload it, then upload `firmware/sonde` again. This is needed for every flight, because
   each flight has its own mask.
4. **On the pad**: power the sonde. Telemetry appears on the flight page as soon as the parser
   matches the token. Wait for **GPS FIX**, then press **Calibrate ground**.
5. **Launch**: press **Release sonde**. The analyzer follows ascent → burst → descent →
   landed and logs each step. Use **Antenna aiming** to keep a directional antenna on it.
6. **Recovery**: press **End flight**. The flight moves to the **Archive**, where
   **Verify data** shows gaps and outliers so you can confirm the clean data window.

## Testing without hardware

`tools/simulate_sonde.py` pretends to be a sonde. It writes realistic packets straight into
`raw.packets` (standard-atmosphere pressure and temperature, wind drift, weakening signal with
distance), so everything after the radio runs for real:

```bash
python supervisor.py --no-receiver
# register device 11951, create a flight and note its mask, then:
python -m tools.simulate_sonde --sn 11951 --mask <MASK>            # interactive: ground | release | burst | land
python -m tools.simulate_sonde --sn 11951 --mask <MASK> --auto \
       --ground-time 60 --burst-alt 8000 --speed 2                 # whole flight on its own
```

Unit tests cover the radio protocol, the flight-phase state machine, the alert lights and
the post-flight audit (no database needed):

```bash
pytest
```

## Configuration

All settings come from environment variables or `.env` (see `.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql://sonde_user:change-me@localhost/weather_sonde` | Web app, analyzer, supervisor |
| `INGEST_DATABASE_URL` | `DATABASE_URL` | Receiver and parser (least-privilege role) |
| `SECRET_KEY` | insecure dev key | Flask session signing. **Set this.** |
| `ALLOW_SIGNUP` | `true` | Allow new operator accounts (the first account is always allowed) |
| `SESSION_MINUTES` | `30` | Inactivity logout |
| `FLASK_RUN_HOST` / `FLASK_RUN_PORT` | `0.0.0.0` / `5000` | Web UI bind address used by the supervisor |

## Repository layout

| Path | What it is |
|---|---|
| `app/` | Flask web app: models, routes, templates, `static/js/flight.js` (live dashboard) |
| `backend/protocol.py` | Radio record format, auth token, SN normalisation, rate estimation |
| `backend/ingest/receiver.py` | LoRa radio → `raw.packets` (Pi only) |
| `backend/etl/parse_raw.py` | `raw.packets` → `sonde.telemetry` |
| `backend/analysis/analyzer.py` | Flight phase, burst, alerts → `sonde.flight_status` |
| `backend/archive/audit_telemetry.py` | Post-flight gap and outlier audit |
| `supervisor.py` | Runs and restarts all processes |
| `firmware/sonde/` | Flight firmware (Arduino) |
| `firmware/provision_eeprom/` | Writes the device SN and flight token to EEPROM |
| `tools/simulate_sonde.py` | Simulated sonde for testing without hardware |
| `db/` | Raw-schema SQL, optional database roles, upgrade script for old databases |
| `migrations/` | Alembic migrations for the `sonde` schema |
| `tests/` | Unit tests |
