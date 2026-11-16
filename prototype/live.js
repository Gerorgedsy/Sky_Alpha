/* Live observations are separate from the historical delay forecast replay. */
(() => {
  'use strict';
  const API_URL = location.hostname === 'localhost' || location.hostname === '127.0.0.1'
    ? 'http://localhost:8766/api/aircraft'
    : 'https://skyalpha-live-airspace.headybuttercup5.chatgpt.site/api/aircraft';
  const $ = (selector) => document.querySelector(selector);
  const svgNS = 'http://www.w3.org/2000/svg';
  const center = {lat: 40.7, lon: -73.9};
  const bounds = {latMin: 39.4, latMax: 42, lonMin: -76, lonMax: -71.5};
  const state = {data: null, selected: null, page: 1, loading: false, error: null};
  const create = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const svg = (tag, attributes) => {
    const node = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    return node;
  };
  const position = (aircraft) => ({
    x: (aircraft.lon - bounds.lonMin) / (bounds.lonMax - bounds.lonMin) * 860,
    y: (bounds.latMax - aircraft.lat) / (bounds.latMax - bounds.latMin) * 520,
  });
  const distanceNM = (aircraft) => {
    const rad = Math.PI / 180;
    const dLat = (aircraft.lat - center.lat) * rad;
    const dLon = (aircraft.lon - center.lon) * rad;
    const a = Math.sin(dLat / 2) ** 2 + Math.cos(center.lat * rad) * Math.cos(aircraft.lat * rad) * Math.sin(dLon / 2) ** 2;
    return Math.round(3440.065 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
  };
  const ageLabel = (seconds) => seconds < 60 ? `${Math.round(seconds)}s ago` : `${Math.floor(seconds / 60)}m ago`;
  const altitudeLabel = (aircraft) => aircraft.onGround ? 'Ground' : aircraft.altitudeFt == null ? 'Unknown' : `${aircraft.altitudeFt.toLocaleString()} ft`;
  const speedLabel = (aircraft) => aircraft.groundSpeedKt == null ? 'Unknown' : `${aircraft.groundSpeedKt} kt`;
  const identity = (aircraft) => aircraft.callsign || aircraft.registration || aircraft.id.toUpperCase();

  function renderAirports() {
    const layer = $('#airportMarkers');
    const airports = [
      ['EWR', 40.6895, -74.1745], ['JFK', 40.6413, -73.7781], ['LGA', 40.7769, -73.8740]
    ];
    airports.forEach(([code, lat, lon]) => {
      const point = position({lat, lon});
      layer.append(svg('circle', {cx: point.x, cy: point.y, r: 6, class:'map-airport'}), svg('text', {x: point.x + 11, y: point.y + 5, class:'map-airport-label'}));
      layer.lastElementChild.textContent = code;
    });
  }

  function filtered() {
    if (!state.data) return [];
    const query = $('#liveSearch').value.trim().toUpperCase().replace(/\s+/g, '');
    const mode = $('#groundFilter').value;
    return state.data.aircraft.filter((aircraft) => {
      if (mode === 'ground' && !aircraft.onGround) return false;
      if (mode === 'airborne' && aircraft.onGround) return false;
      return !query || [aircraft.callsign, aircraft.registration, aircraft.id].some((value) => (value || '').toUpperCase().replace(/\s+/g, '').includes(query));
    }).sort((a, b) => identity(a).localeCompare(identity(b)));
  }

  function renderStatus() {
    if (!state.data) {
      $('#statusTitle').textContent = state.error ? 'Aircraft feed unavailable' : 'Connecting to aircraft feed…';
      $('#statusTitle').className = state.error ? 'status-bad' : '';
      $('#statusDetail').textContent = state.error || 'Waiting for the first observation';
      return;
    }
    const observed = Date.parse(state.data.observedAt);
    const age = Number.isFinite(observed) ? Math.max(0, (Date.now() - observed) / 1000) : Infinity;
    const stale = state.data.status === 'stale' || !!state.error || age > 600;
    $('#statusTitle').textContent = stale ? 'Showing last available observation' : 'Aircraft observations available';
    $('#statusTitle').className = stale ? 'status-stale' : 'status-good';
    const time = Number.isFinite(observed) ? new Intl.DateTimeFormat(undefined, {hour:'numeric',minute:'2-digit',second:'2-digit',timeZoneName:'short'}).format(observed) : 'unknown time';
    $('#statusDetail').textContent = `Observed ${Number.isFinite(age) ? ageLabel(age) : 'at unknown time'} · ${time} · ${state.data.source} · ${stale ? 'feed stale or unavailable' : 'checking every 30 seconds'}`;
  }

  function renderStats() {
    if (!state.data) return;
    const all = state.data.aircraft;
    $('#statTotal').textContent = all.length.toLocaleString();
    $('#statAirborne').textContent = all.filter((aircraft) => !aircraft.onGround).length.toLocaleString();
    $('#statGround').textContent = all.filter((aircraft) => aircraft.onGround).length.toLocaleString();
  }

  function selectAircraft(id) {
    state.selected = id;
    renderSelection();
    renderMap();
    renderList();
  }

  function renderMap() {
    const layer = $('#aircraftMarkers');
    layer.replaceChildren();
    filtered().forEach((aircraft) => {
      const point = position(aircraft);
      const marker = svg('circle', {
        cx: point.x, cy: point.y, r: aircraft.id === state.selected ? 7 : 5,
        fill: aircraft.onGround ? '#f5bd62' : '#5fd0c5',
        class: `aircraft-marker${aircraft.id === state.selected ? ' selected' : ''}`,
        role: 'button', tabindex: '0',
        'aria-label': `${identity(aircraft)}, ${altitudeLabel(aircraft)}, ${speedLabel(aircraft)}`,
      });
      marker.append(svg('title', {}));
      marker.lastElementChild.textContent = `${identity(aircraft)} · ${altitudeLabel(aircraft)}`;
      marker.addEventListener('click', () => selectAircraft(aircraft.id));
      marker.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectAircraft(aircraft.id); } });
      layer.append(marker);
    });
  }

  function makeRow(aircraft) {
    const row = create('button', `live-row${aircraft.id === state.selected ? ' selected' : ''}`);
    row.type = 'button';
    row.setAttribute('aria-label', `${identity(aircraft)}, altitude ${altitudeLabel(aircraft)}, speed ${speedLabel(aircraft)}, position ${ageLabel(aircraft.positionAgeSeconds)}. Open details.`);
    const info = create('span', 'live-id');
    const text = create('span');
    text.append(create('strong', null, identity(aircraft)), create('small', null, `${aircraft.registration || aircraft.id.toUpperCase()} · ${distanceNM(aircraft)} NM from NYC`));
    info.append(create('span', 'live-icon', '✈'), text);
    row.append(info, create('span', 'live-number', altitudeLabel(aircraft)), create('span', 'live-number', speedLabel(aircraft)), create('span', 'live-number age-cell', ageLabel(aircraft.positionAgeSeconds)));
    row.addEventListener('click', () => selectAircraft(aircraft.id));
    return row;
  }

  function renderList() {
    const container = $('#aircraftList');
    container.replaceChildren();
    if (!state.data) {
      const error = create('div', 'feed-error');
      error.append(create('strong', null, state.error ? 'Live positions are unavailable' : 'Loading current aircraft…'), create('p', null, state.error || 'Connecting to the live feed.'));
      container.append(error);
      $('#listDetail').textContent = 'No current observation';
      $('#showMore').hidden = true;
      return;
    }
    const items = filtered();
    $('#listDetail').textContent = `${items.length.toLocaleString()} shown from ${state.data.aircraft.length.toLocaleString()} positioned aircraft · callsigns may be missing`;
    if (!items.length) {
      const empty = create('div', 'feed-error');
      empty.append(create('strong', null, 'No observed aircraft match these filters'), create('p', null, 'Try clearing the search or selecting all aircraft. Coverage is incomplete.'));
      container.append(empty);
    } else {
      items.slice(0, state.page * 40).forEach((aircraft) => container.append(makeRow(aircraft)));
    }
    const remaining = items.length - state.page * 40;
    $('#showMore').hidden = remaining <= 0;
    $('#showMore').textContent = `Show more aircraft (${remaining} remaining)`;
  }

  function renderSelection() {
    const card = $('#selectedCard');
    card.replaceChildren();
    const aircraft = state.data?.aircraft.find((item) => item.id === state.selected);
    if (!aircraft) {
      card.append(create('h3', null, state.selected ? 'Aircraft no longer observed' : 'Select an aircraft'), create('p', null, state.selected ? 'This aircraft is absent from the latest snapshot. Search again or choose another aircraft.' : 'Choose a dot or aircraft row to see its latest reported position and motion.'));
      return;
    }
    card.append(create('h3', null, 'Latest observation'), create('div', 'selected-code', identity(aircraft)), create('p', 'selected-sub', `${aircraft.registration || 'Registration unavailable'} · ICAO hex ${aircraft.id.toUpperCase()}`));
    const details = [
      ['Position', `${aircraft.lat.toFixed(4)}°, ${aircraft.lon.toFixed(4)}°`],
      ['From NYC center', `${distanceNM(aircraft)} NM`],
      ['Altitude', altitudeLabel(aircraft)],
      ['Ground speed', speedLabel(aircraft)],
      ['Heading', aircraft.trackDeg == null ? 'Unknown' : `${aircraft.trackDeg}°`],
      ['Position age at feed', ageLabel(aircraft.positionAgeSeconds)],
      ['Aircraft type', aircraft.aircraftType || 'Unknown'],
    ];
    details.forEach(([label, value]) => {
      const line = create('div', 'stat');
      line.append(create('span', null, label), create('strong', null, value));
      card.append(line);
    });
    const link = create('a', 'text-link', 'View on adsb.lol ↗');
    link.href = `https://adsb.lol/?icao=${encodeURIComponent(aircraft.id)}`;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    card.append(link);
  }

  function render() {
    renderStatus();
    renderStats();
    renderMap();
    renderList();
    renderSelection();
  }

  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    $('#refresh').disabled = true;
    $('#refresh').textContent = 'Checking…';
    try {
      const response = await fetch(API_URL, {cache:'no-store', signal:AbortSignal.timeout(25000)});
      if (!response.ok) throw new Error(`Feed returned HTTP ${response.status}`);
      const payload = await response.json();
      if (!Array.isArray(payload.aircraft) || !payload.observedAt) throw new Error('Invalid live feed response');
      state.data = payload;
      state.error = payload.status === 'stale' ? 'Live feed temporarily unavailable' : null;
      render();
    } catch (error) {
      state.error = 'Could not reach the aircraft feed. Try again shortly.';
      render();
    } finally {
      state.loading = false;
      $('#refresh').disabled = false;
      $('#refresh').textContent = 'Refresh view';
    }
  }

  $('#liveSearch').addEventListener('input', () => { state.page = 1; renderMap(); renderList(); });
  $('#groundFilter').addEventListener('change', () => { state.page = 1; renderMap(); renderList(); });
  $('#showMore').addEventListener('click', () => { state.page += 1; renderList(); });
  $('#refresh').addEventListener('click', refresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
  renderAirports();
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 30000);
})();
