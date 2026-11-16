/* Airport conditions and optional schedules are operational data, not model forecasts. */
(() => {
  'use strict';
  const API = location.hostname === 'localhost' || location.hostname === '127.0.0.1'
    ? 'http://localhost:8766/api' : 'https://skyalpha-live-airspace.headybuttercup5.chatgpt.site/api';
  const airports = {
    JFK: {name:'John F. Kennedy', board:'https://www.jfkairport.com/flights'},
    EWR: {name:'Newark Liberty', board:'https://www.newarkairport.com/flights'},
    LGA: {name:'LaGuardia', board:'https://www.laguardiaairport.com/flights'},
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
    return Number.isFinite(date) ? new Intl.DateTimeFormat(undefined, {hour:'numeric', minute:'2-digit', timeZone:'America/New_York', timeZoneName:'short'}).format(date) : 'time unknown';
  };
  const age = (value) => {
    const date = Date.parse(value);
    if (!Number.isFinite(date)) return 'time unknown';
    const minutes = Math.max(0, Math.floor((Date.now() - date) / 60000));
    return minutes < 1 ? 'just now' : `${minutes}m ago`;
  };
  async function get(path) {
    const response = await fetch(`${API}${path}`, {cache:'no-store', signal:AbortSignal.timeout(20000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }
  function renderConditions(data) {
    const container = $('#airportConditions');
    container.replaceChildren();
    $('#operationsStatus').textContent = `${data.status === 'stale' ? 'Last available' : 'Latest'} update ${age(data.fetchedAt)} · FAA ${data.faa.status} · weather ${data.weather.status}`;
    Object.entries(airports).forEach(([code, airport]) => {
      const card = node('article', 'card airport-card');
      card.append(node('p', 'section-kicker', code), node('h3', null, airport.name));
      const events = data.faa.events?.[code];
      if (Array.isArray(events)) {
        if (!events.length) card.append(node('p', 'airport-normal', 'No active FAA-listed event'));
        else events.forEach((event) => card.append(node('p', 'airport-alert', `${event.type}${event.averageDelay ? ` · average ${event.averageDelay}` : ''}${event.reason ? ` · ${event.reason}` : ''}`)));
      } else card.append(node('p', 'airport-unknown', 'FAA status unavailable'));
      const weather = data.weather.airports?.[code];
      if (weather) {
        const parts = [weather.category || 'Category unknown', weather.windKt == null ? 'Wind unknown' : `Wind ${weather.windKt}${weather.gustKt ? `G${weather.gustKt}` : ''} kt`, weather.visibilityMi == null ? 'Visibility unknown' : `${weather.visibilityMi} mi visibility`];
        card.append(node('p', 'airport-weather', parts.join(' · ')), node('small', 'airport-updated', `Weather observed ${age(weather.observedAt)}`));
      } else card.append(node('p', 'airport-unknown', 'Weather observation unavailable'));
      container.append(card);
    });
  }
  async function refreshConditions() {
    try { renderConditions(await get('/operations')); }
    catch {
      $('#operationsStatus').textContent = 'Live airport conditions unavailable; try again shortly.';
      $('#airportConditions').replaceChildren(node('div', 'card', 'FAA and weather feeds could not be reached.'));
    }
  }
  let selected = 'JFK';
  let requestId = 0;
  async function refreshSchedules() {
    const current = ++requestId;
    const code = selected;
    const board = $('#officialBoard');
    board.href = airports[code].board;
    board.textContent = `Open ${code} official flight board ↗`;
    $('#scheduleList').replaceChildren(node('p', 'schedule-message', 'Checking live schedule connection…'));
    try {
      const data = await get(`/schedules?airport=${code}`);
      if (current !== requestId) return;
      const list = $('#scheduleList');
      list.replaceChildren();
      if (data.status === 'not_configured') {
        list.append(node('strong', null, 'Schedule feed not connected'), node('p', null, 'A free AirLabs key must be added to the backend before this board can show current departures. Use the official airport board for gates and flight status today.'));
        $('#scheduleSource').textContent = 'No schedule data is being shown as current.';
        return;
      }
      if (!Array.isArray(data.flights) || !data.flights.length) {
        list.append(node('strong', null, 'No departures returned'), node('p', null, 'The schedule provider has not returned departures for this airport. Check the official board.'));
      } else {
        const table = node('div', 'schedule-table');
        const head = node('div', 'schedule-row schedule-colhead');
        ['Flight / route', 'Scheduled (ET)', 'Gate', 'Provider status'].forEach((label) => head.append(node('span', null, label)));
        table.append(head);
        data.flights.forEach((flight) => {
          const row = node('div', 'schedule-row');
          row.append(node('strong', null, `${flight.flight} · ${code} → ${flight.destination || 'destination unknown'}`),
            node('span', null, flight.scheduledLocal?.slice(11, 16) || 'Unknown'),
            node('span', null, flight.gate ? `${flight.terminal ? `${flight.terminal} / ` : ''}${flight.gate}` : 'Not provided'),
            node('span', null, flight.status || 'Not provided'));
          table.append(row);
        });
        list.append(table);
      }
      $('#scheduleSource').textContent = `${data.status === 'stale' ? 'Last available' : 'AirLabs'} · fetched ${time(data.fetchedAt)} · partial coverage, at most 50 flights · gates/status may be absent`;
    } catch {
      if (current !== requestId) return;
      $('#scheduleList').replaceChildren(node('strong', null, 'Schedule feed unavailable'), node('p', null, 'Use the official airport board for current departures and gates.'));
      $('#scheduleSource').textContent = 'No current provider data available.';
    }
  }
  document.querySelectorAll('[data-airport]').forEach((button) => button.addEventListener('click', () => {
    selected = button.dataset.airport;
    document.querySelectorAll('[data-airport]').forEach((item) => item.setAttribute('aria-pressed', String(item === button)));
    refreshSchedules();
  }));
  refreshConditions();
  refreshSchedules();
  setInterval(() => { if (!document.hidden) refreshConditions(); }, 300000);
  setInterval(() => { if (!document.hidden) refreshSchedules(); }, 3600000);
})();
