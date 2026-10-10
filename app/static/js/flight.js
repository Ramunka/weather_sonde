// Live flight dashboard: polls the JSON APIs and drives the controls, alert lights,
// pressure profile, map and antenna calculator.
(() => {
  const app = document.getElementById('flight-app');
  const flightId = app.dataset.flightId;
  let flightStatus = app.dataset.flightStatus;
  const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
  const $ = id => document.getElementById(id);

  const POLL_TELEMETRY_MS = 2000;
  const POLL_STATUS_MS = 2000;
  const POLL_GPS_MS = 4000;
  const POLL_LOGS_MS = 5000;

  async function getJSON(url) {
    const res = await fetch(url, { headers: { Accept: 'application/json' } });
    if (res.redirected || res.status === 401) {
      window.location = '/login?next=' + encodeURIComponent(location.pathname);
      throw new Error('session expired');
    }
    return res.json();
  }

  function poll(fn, ms) {
    const run = () => fn().catch(err => console.error(err));
    run();
    setInterval(run, ms);
  }

  // ── Clocks ────────────────────────────────────────────────────────────────
  function updateClocks() {
    const now = new Date();
    $('utc-time').textContent = now.toISOString().substring(11, 19);
    $('local-time').textContent = now.toTimeString().substring(0, 8);
  }
  updateClocks();
  setInterval(updateClocks, 1000);

  // ── Controls ──────────────────────────────────────────────────────────────
  const buttons = { calibrate: $('calibrateBtn'), release: $('releaseBtn'), end: $('endFlightBtn') };
  const controlStatus = $('control-status');

  function refreshButtons() {
    buttons.calibrate.disabled = flightStatus !== 'pre-flight';
    buttons.release.disabled = flightStatus !== 'pre-flight';
    buttons.end.disabled = flightStatus === 'post-flight';
  }

  async function control(action, pendingText, confirmText) {
    if (confirmText && !confirm(confirmText)) return;
    Object.values(buttons).forEach(b => (b.disabled = true));
    controlStatus.textContent = pendingText;
    try {
      const res = await fetch(`/flight/${flightId}/${action}`, {
        method: 'POST',
        headers: { 'X-CSRFToken': csrfToken, Accept: 'application/json' },
      });
      const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
      if (data.error) {
        controlStatus.textContent = `Error: ${data.error}`;
      } else {
        controlStatus.textContent = data.message;
        if (data.new_status) flightStatus = data.new_status;
        if (action === 'calibrate') $('site-calibrated').textContent = new Date().toISOString().substring(11, 19) + ' UTC';
        if (action === 'release') $('site-released').textContent = data.release_ts.substring(11, 19) + ' UTC';
        updateLogs();
      }
    } catch (err) {
      controlStatus.textContent = 'Request failed. Check the connection.';
      console.error(err);
    }
    refreshButtons();
  }

  buttons.calibrate.addEventListener('click', () => control('calibrate', 'Calibrating…'));
  buttons.release.addEventListener('click', () =>
    control('release', 'Releasing…', 'Release the sonde now? This starts flight tracking.'));
  buttons.end.addEventListener('click', () =>
    control('end', 'Ending flight…', 'End this flight? It will move to the archive. This is final.'));
  refreshButtons();

  // ── Telemetry fields ──────────────────────────────────────────────────────
  async function updateTelemetry() {
    const { fields } = await getJSON(`/api/telemetry/${flightId}`);
    for (const [id, value] of Object.entries(fields)) {
      const el = $(id);
      if (el) el.textContent = value;
    }
  }

  // ── Status: profile markers + alert lights ───────────────────────────────
  function setMarker(el, pct) {
    el.style.display = pct == null ? 'none' : 'block';
    if (pct != null) el.style.bottom = `calc(${pct}% - 0.6em)`;
  }

  function setLight(name, state) {
    // state: 'green' | 'yellow' | 'red' | 'blink' | 'off'
    const el = $(`alert-${name}`);
    if (el) el.className = `light ${state}`;
  }

  async function updateStatus() {
    const s = await getJSON(`/api/status/${flightId}`);
    if (s.error) return;

    setMarker($('balloon'), s.balloon_position);
    setMarker($('burst'), s.burst_position);
    setMarker($('parachute'), s.parachute_position);

    const live = s.has_telemetry;
    const ok = (flag, bad = 'red') => (!live ? 'off' : flag ? 'green' : bad);

    setLight('receiver', s.receiver_ok ? 'green' : 'red');
    setLight('parser', s.parser_ok ? 'green' : 'red');
    setLight('sensor', ok(s.sensor_ok));
    setLight('signal', !live || !s.signal_level ? 'off' : s.signal_level);
    setLight('packet', ok(s.packet_ok, 'yellow'));
    setLight('age', !live ? 'off' : s.age_warn ? 'red blink' : 'green');
    setLight('calibrated', s.calibrated ? 'green' : s.has_calibration ? 'yellow' : 'off');
    setLight('burst', s.burst_detected ? 'red' : 'off');
    setLight('temp-low', s.temp_low ? 'yellow' : 'off');
    setLight('data-degrad', s.data_degrad ? 'yellow' : 'off');
    setLight('gps-fix', ok(s.gps_fix));
    setLight('gps-degrad', s.gps_degrad || 'off');

    if (s.flight_status !== flightStatus) {
      flightStatus = s.flight_status;
      refreshButtons();
    }
  }

  // ── Map ───────────────────────────────────────────────────────────────────
  const map = L.map('map').setView([0, 0], 2);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '© OpenStreetMap contributors',
  }).addTo(map);
  const trackLayer = L.layerGroup().addTo(map);
  const ICON_TITLES = { start: 'Launch', burst: 'Burst', end: 'Latest' };
  let lastFix = null;
  let lastTrackLength = 0;

  async function updateGPS() {
    const points = await getJSON(`/api/gps/${flightId}`);
    if (!points.length) return;

    trackLayer.clearLayers();
    const line = L.polyline(points.map(p => p.coords), { color: '#0d6efd', weight: 3 }).addTo(trackLayer);
    for (const p of points) {
      const marker = p.icon
        ? L.marker(p.coords, { title: ICON_TITLES[p.icon] })
            .bindPopup(`<strong>${ICON_TITLES[p.icon]}</strong><br>${p.timestamp_str}<br>${p.altitude ?? '?'} m`)
        : L.circleMarker(p.coords, { radius: 3, color: '#0d6efd' });
      trackLayer.addLayer(marker);
    }
    if (points.length !== lastTrackLength) {
      map.fitBounds(line.getBounds(), { padding: [20, 20], maxZoom: 15 });
      lastTrackLength = points.length;
    }

    const last = points[points.length - 1];
    lastFix = { lat: last.coords[0], lon: last.coords[1], alt: last.altitude || 0 };
    updateAim();
  }

  // ── Antenna aiming ────────────────────────────────────────────────────────
  const rad = d => (d * Math.PI) / 180;
  const deg = r => (r * 180) / Math.PI;

  function updateAim() {
    const out = $('aim-result');
    if (!lastFix) return;
    const lat1 = parseFloat($('antenna-lat').value);
    const lon1 = parseFloat($('antenna-lon').value);
    const alt1 = parseFloat($('antenna-alt').value) || 0;
    if (Number.isNaN(lat1) || Number.isNaN(lon1)) {
      out.textContent = 'Enter the antenna position.';
      return;
    }
    const { lat: lat2, lon: lon2, alt: alt2 } = lastFix;
    const dLon = rad(lon2 - lon1);
    const y = Math.sin(dLon) * Math.cos(rad(lat2));
    const x = Math.cos(rad(lat1)) * Math.sin(rad(lat2)) - Math.sin(rad(lat1)) * Math.cos(rad(lat2)) * Math.cos(dLon);
    const azimuth = (deg(Math.atan2(y, x)) + 360) % 360;

    const R = 6371000;
    const dX = dLon * R * Math.cos(rad((lat1 + lat2) / 2));
    const dY = rad(lat2 - lat1) * R;
    const ground = Math.hypot(dX, dY);
    const elevation = deg(Math.atan2(alt2 - alt1, ground));

    out.textContent = `Az ${azimuth.toFixed(1)}°  El ${elevation.toFixed(1)}°  ` +
      `Range ${(Math.hypot(ground, alt2 - alt1) / 1000).toFixed(2)} km`;
  }
  ['antenna-lat', 'antenna-lon', 'antenna-alt'].forEach(id => $(id).addEventListener('input', updateAim));

  // ── Log ───────────────────────────────────────────────────────────────────
  async function updateLogs() {
    const { logs } = await getJSON(`/api/logs/${flightId}`);
    if (!logs.length) return;
    const body = document.querySelector('#log-entries tbody');
    body.replaceChildren(...logs.map(log => {
      const row = document.createElement('tr');
      const ts = document.createElement('td');
      ts.className = 'text-nowrap';
      ts.textContent = log.timestamp.substring(11, 19);
      const level = document.createElement('td');
      const badge = document.createElement('span');
      badge.className = `log-level log-${log.level.toLowerCase()}`;
      badge.textContent = log.level;
      level.appendChild(badge);
      const msg = document.createElement('td');
      msg.textContent = log.message;
      row.append(ts, level, msg);
      return row;
    }));
  }

  poll(updateTelemetry, POLL_TELEMETRY_MS);
  poll(updateStatus, POLL_STATUS_MS);
  poll(updateGPS, POLL_GPS_MS);
  poll(updateLogs, POLL_LOGS_MS);
})();
