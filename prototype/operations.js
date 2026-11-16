/* Airport conditions and flight schedules are operational data, not model forecasts.
 *
 * Each view tries the live backend and falls back to the snapshot that the
 * "Live airport data" GitHub workflow publishes on the live-data branch.
 * ?liveData=<url> points the snapshot at another location (for testing).
 */
(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const API = location.hostname === 'localhost' || location.hostname === '127.0.0.1'
    ? 'http://localhost:8766/api' : 'https://skyalpha-live-airspace.headybuttercup5.chatgpt.site/api';
  const SNAPSHOT = (params.get('liveData') || 'https://raw.githubusercontent.com/Gerorgedsy/Sky_Alpha/live-data').replace(/\/$/, '');
  const OPERATIONS_STALE_MIN = 120;
  const SCHEDULES_STALE_MIN = 12 * 60;
  const airports = {
    JFK: {name: 'John F. Kennedy', board: 'https://www.jfkairport.com/flights'},
    EWR: {name: 'Newark Liberty', board: 'https://www.newarkairport.com/flights'},
    LGA: {name: 'LaGuardia', board: 'https://www.laguardiaairport.com/flights'},
  };
  const $ = (selector) => document.querySelector(selector);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  };
  const time = (value) => {
    const date = Date.parse(value);
    return Number.isFinite(date) ? new Intl.DateTimeFormat(undefined, {hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York', timeZoneName: 'short'}).format(date) : 'time unknown';
  };
  const minutesOld = (value) => {
    const date = Date.parse(value);
    return Number.isFinite(date) ? Math.max(0, Math.floor((Date.now() - date) / 60000)) : Infinity;
  };
  const age = (value) => {
    const minutes = minutesOld(value);
    if (!Number.isFinite(minutes)) return 'time unknown';
    if (minutes < 1) return 'just now';
    if (minutes < 120) return `${minutes} min ago`;
    return `${Math.floor(minutes / 60)} h ago`;
  };
  const localClock = (value) => {
    // AirLabs gives airport-local "YYYY-MM-DD HH:MM"; all three airports are in ET.
    const match = /\d{4}-\d{2}-\d{2}[ T](\d{2}):(\d{2})/.exec(value || '');
    if (!match) return 'Unknown';
    const hour = Number(match[1]);
    return `${hour % 12 || 12}:${match[2]} ${hour < 12 ? 'AM' : 'PM'}`;
  };

  async function getJson(url) {
    const response = await fetch(url, {cache: 'no-store', signal: AbortSignal.timeout(20000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  const backend = (path) => getJson(`${API}${path}`);
  const snapshot = (name) => getJson(`${SNAPSHOT}/${name}?t=${Math.floor(Date.now() / 60000)}`);

  // ---- Airport conditions -------------------------------------------------
  function renderConditions(data, fromSnapshot) {
    const container = $('#airportConditions');
    container.replaceChildren();
    const old = minutesOld(data.fetchedAt) > OPERATIONS_STALE_MIN;
    const label = fromSnapshot ? (old ? 'Last snapshot (may be out of date)' : 'Snapshot') : data.status === 'stale' ? 'Last available' : 'Latest';
    $('#operationsStatus').textContent = `${label} · updated ${age(data.fetchedAt)} · FAA ${data.faa?.status || 'unavailable'} · weather ${data.weather?.status || 'unavailable'}`;
    Object.entries(airports).forEach(([code, airport]) => {
      const card = node('article', 'card airport-card');
      card.append(node('p', 'section-kicker', code), node('h3', null, airport.name));
      const events = data.faa?.events?.[code];
      if (Array.isArray(events)) {
        if (!events.length) card.append(node('p', 'airport-normal', 'No active FAA-listed event'));
        else events.forEach((event) => card.append(node('p', 'airport-alert', `${event.type}${event.averageDelay ? ` · average ${event.averageDelay}` : ''}${event.reason ? ` · ${event.reason}` : ''}`)));
      } else card.append(node('p', 'airport-unknown', 'FAA status unavailable'));
      const weather = data.weather?.airports?.[code];
      if (weather) {
        const parts = [weather.category || 'Category unknown',
          weather.windKt == null ? 'Wind unknown' : `Wind ${weather.windKt}${weather.gustKt ? `G${weather.gustKt}` : ''} kt`,
          weather.visibilityMi == null ? 'Visibility unknown' : `${weather.visibilityMi} mi visibility`];
        if (weather.temperatureC != null) parts.push(`${Math.round(weather.temperatureC)}°C`);
        card.append(node('p', 'airport-weather', parts.join(' · ')), node('small', 'airport-updated', `Weather observed ${age(weather.observedAt)}`));
      } else card.append(node('p', 'airport-unknown', 'Weather observation unavailable'));
      container.append(card);
    });
  }

  function renderConditionsFallback() {
    $('#operationsStatus').textContent = 'Airport conditions are not currently available here.';
    const fallback = node('div', 'card airport-fallback');
    fallback.append(node('strong', null, 'Check the official sources for current conditions.'), node('p', null, 'SkyDelay is not showing a current FAA or weather reading here.'));
    for (const [label, href] of [['FAA NAS Status', 'https://nasstatus.faa.gov/'], ['NOAA Aviation Weather', 'https://aviationweather.gov/']]) {
      const link = node('a', 'text-link', `${label} ↗`);
      link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      fallback.append(link);
    }
    $('#airportConditions').replaceChildren(fallback);
  }

  async function refreshConditions() {
    try { renderConditions(await backend('/operations'), false); return; } catch { /* try the snapshot */ }
    try {
      const data = await snapshot('operations.json');
      if (!data.fetchedAt) throw new Error('empty snapshot');
      renderConditions(data, true);
    } catch { renderConditionsFallback(); }
  }

  // ---- Flight schedules ---------------------------------------------------
  const schedule = {airport: 'JFK', direction: 'departures', data: null, source: null};
  const predictions = new Map();
  window.addEventListener('skydelay:predictions', (event) => {
    predictions.clear();
    (event.detail || []).forEach((f) => predictions.set(`${f.direction}:${f.flight}`, f));
    if (schedule.data) renderSchedule();
  });

  function normaliseBackend(data, airport) {
    // The backend returns departures for one airport in its own shape.
    return {
      status: data.status, source: 'AirLabs via SkyDelay backend', fetchedAt: data.fetchedAt, coverage: data.coverage,
      airports: {[airport]: {departures: (data.flights || []).map((f) => ({...f, origin: airport, otherAirport: f.destination})), arrivals: null}},
    };
  }

  async function loadSchedules() {
    try {
      const data = await snapshot('schedules.json');
      if (data.status === 'live' && data.airports) return {data, source: 'snapshot'};
    } catch { /* try the backend */ }
    try {
      const data = await backend(`/schedules?airport=${schedule.airport}`);
      if (data.status === 'not_configured') return {data: null, source: 'not_configured'};
      return {data: normaliseBackend(data, schedule.airport), source: 'backend'};
    } catch {
      return {data: null, source: 'unavailable'};
    }
  }

  function message(title, text) {
    $('#scheduleList').replaceChildren(node('strong', null, title), node('p', null, text));
  }

  function predictionCell(flight) {
    const key = `${schedule.direction === 'arrivals' ? 'arrival' : 'departure'}:${String(flight.flight || '').replace(/\s/g, '')}`;
    const match = predictions.get(key);
    if (!match) return node('span', 'schedule-muted', '—');
    const delayed = match.prediction === 'delayed';
    const cell = node('span', `schedule-pred ${delayed ? 'is-delayed' : 'is-ontime'}`, `${delayed ? 'Delayed' : 'On time'} · ${Math.round(match.confidence * 100)}%`);
    cell.title = 'SkyDelay model prediction and confidence';
    return cell;
  }

  function renderSchedule() {
    const code = schedule.airport;
    const arrivals = schedule.direction === 'arrivals';
    const board = $('#officialBoard');
    board.href = airports[code].board;
    board.textContent = `Open ${code} official flight board ↗`;
    document.querySelectorAll('[data-airport]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.airport === code)));
    document.querySelectorAll('[data-schedule-direction]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.scheduleDirection === schedule.direction)));

    if (schedule.source === 'not_configured' || schedule.source === 'unavailable' || !schedule.data) {
      if (schedule.source === 'not_configured') message('Schedule feed not connected', 'Add a free AirLabs API key as the AIRLABS_API_KEY repository secret to show current flights, gates and status here. Until then, use the official airport board.');
      else message('Schedule data unavailable here', 'Use the official airport board for current flights and gates. An in-page board needs a schedule provider key.');
      $('#scheduleSource').textContent = 'No schedule data is being shown as current.';
      return;
    }
    const flights = schedule.data.airports?.[code]?.[schedule.direction];
    if (flights == null) {
      message(`${arrivals ? 'Arrivals' : 'Departures'} not available from this source`, 'Use the official airport board for this list.');
    } else {
      const upcoming = flights.filter((f) => {
        const when = Date.parse(f.scheduledUtc ? `${f.scheduledUtc.replace(' ', 'T')}Z` : '');
        return !Number.isFinite(when) || when > Date.now() - 30 * 60000;
      });
      if (!upcoming.length) {
        message(`No ${arrivals ? 'arrivals' : 'departures'} returned`, 'The schedule provider has not returned upcoming flights for this airport. Check the official board.');
      } else {
        const table = node('div', 'schedule-table');
        const head = node('div', 'schedule-row schedule-colhead');
        ['Flight / route', arrivals ? 'Arrives (ET)' : 'Departs (ET)', arrivals ? 'Terminal / baggage' : 'Terminal / gate', 'Provider status', 'SkyDelay prediction']
          .forEach((label) => head.append(node('span', null, label)));
        table.append(head);
        upcoming.forEach((f) => {
          const row = node('div', 'schedule-row');
          const route = arrivals ? `${f.origin || f.otherAirport || '?'} → ${code}` : `${code} → ${f.destination || f.otherAirport || '?'}`;
          const where = [f.terminal && `T${String(f.terminal).replace(/^T/i, '')}`, arrivals ? f.baggage && `Bag ${f.baggage}` : f.gate && `Gate ${f.gate}`].filter(Boolean).join(' · ');
          const status = [f.status ? f.status[0].toUpperCase() + f.status.slice(1) : null, f.delayMin ? `+${f.delayMin} min` : null].filter(Boolean).join(' · ');
          const when = node('span', null, localClock(f.scheduledLocal));
          if (f.estimatedLocal && f.estimatedLocal !== f.scheduledLocal) when.append(node('small', 'schedule-muted', ` est. ${localClock(f.estimatedLocal)}`));
          row.append(node('strong', null, `${f.flight} · ${route}`), when, node('span', where ? null : 'schedule-muted', where || 'Not provided'),
            node('span', status ? null : 'schedule-muted', status || 'Not provided'), predictionCell(f));
          table.append(row);
        });
        $('#scheduleList').replaceChildren(table);
      }
    }
    const old = minutesOld(schedule.data.fetchedAt) > SCHEDULES_STALE_MIN;
    $('#scheduleSource').textContent = `${schedule.data.source || 'AirLabs'} · fetched ${time(schedule.data.fetchedAt)}${old ? ' (may be out of date)' : ''} · partial coverage, at most 50 flights per list · gates/status may be absent`;
  }

  let requestId = 0;
  async function refreshSchedules() {
    const current = ++requestId;
    if (!schedule.data) $('#scheduleList').replaceChildren(node('p', 'schedule-message', 'Checking schedule data…'));
    const result = await loadSchedules();
    if (current !== requestId) return;
    schedule.data = result.data;
    schedule.source = result.source;
    renderSchedule();
  }

  document.querySelectorAll('[data-airport]').forEach((button) => button.addEventListener('click', () => {
    schedule.airport = button.dataset.airport;
    // Backend data covers one airport at a time; the snapshot covers all three.
    if (schedule.source === 'backend' || !schedule.data) refreshSchedules(); else renderSchedule();
  }));
  document.querySelectorAll('[data-schedule-direction]').forEach((button) => button.addEventListener('click', () => {
    schedule.direction = button.dataset.scheduleDirection;
    if (schedule.data || schedule.source) renderSchedule();
  }));
  refreshConditions();
  refreshSchedules();
  setInterval(() => { if (!document.hidden) refreshConditions(); }, 300000);
  setInterval(() => { if (!document.hidden) refreshSchedules(); }, 900000);
})();
