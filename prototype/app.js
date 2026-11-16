/* SkyDelay historical replay UI. DATA is embedded in index.html at build time. */
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const create = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  if (typeof DATA === 'undefined' || !DATA || !Array.isArray(DATA.rows) || !DATA.rows.length) {
    $('#list').replaceChildren(create('div', 'empty', 'Replay data is unavailable. Please reload the page.'));
    $('#listSub').textContent = 'Dataset unavailable';
    return;
  }

  const columns = DATA.cols;
  const flights = DATA.rows.map((values) => Object.fromEntries(columns.map((name, index) => [name, values[index]])));
  const airports = DATA.airports;
  const airlines = DATA.airlines;
  const tolerance = DATA.tol;
  const dates = [...new Set(flights.map((flight) => flight.date))].sort();
  const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const pageSize = 20;
  const state = {mode: 'flight', date: dates[0], page: 1, sort: 'default'};
  let lastFocus = null;
  let closeTimer = null;

  const signed = (value) => (value > 0 ? '+' : value < 0 ? '−' : '') + Math.abs(value);
  const tone = (value) => value <= 5 ? 'g' : value <= 20 ? 'y' : 'r';
  const displayDate = (value) => { const [month, day] = value.split('-').map(Number); return `${monthNames[month - 1]} ${day}`; };
  const airportName = (code) => (airports[code] || code).replace(/ International| Intl| Airport| Regional|Municipal/gi, '').trim();
  const parseFlight = (query) => {
    const clean = query.toUpperCase().replace(/\s+/g, '');
    const match = clean.match(/^([A-Z0-9]{2})?(\d{1,4})$/) || clean.match(/^([A-Z]{2})(\d{0,4})$/);
    return match ? {carrier: match[1] || null, number: match[2] || ''} : null;
  };
  const matchDestination = (query, code) => {
    const value = query.trim().toLowerCase();
    if (!value) return false;
    const first = value.split(/[\s·]/)[0];
    return (first.length === 3 && code.toLowerCase() === first) || (airports[code] || '').toLowerCase().includes(value);
  };

  function renderDates() {
    const container = $('#dates');
    container.replaceChildren();
    dates.forEach((value) => {
      const [month, day] = value.split('-').map(Number);
      const weekday = dayNames[new Date(Date.UTC(2023, month - 1, day)).getUTCDay()];
      const button = create('button', 'date');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(value === state.date));
      button.setAttribute('aria-label', `${monthNames[month - 1]} ${day}, 2023, ${weekday}`);
      button.append(create('b', null, `${month}/${day}`), create('span', null, weekday));
      button.addEventListener('click', () => { state.date = value; state.page = 1; render(); });
      container.append(button);
    });
  }

  function renderCards(day) {
    const container = $('#cards');
    container.replaceChildren();
    if (!day.length) return;
    const mean = day.reduce((sum, flight) => sum + flight.pred, 0) / day.length;
    const severe = day.filter((flight) => flight.hi >= 60).length;
    const data = [
      ['Flights in replay', day.length.toLocaleString(), 'Scheduled departures'],
      ['Mean forecast', `${mean >= 0 ? '+' : '−'}${Math.abs(mean).toFixed(1)} min`, 'Average of median predictions'],
      ['Severe-delay exposure', `${(severe / day.length * 100).toFixed(1)}%`, `${severe.toLocaleString()} flights with 90th percentile ≥60 min`]
    ];
    data.forEach(([label, value, description]) => {
      const card = create('div', 'card');
      card.append(create('div', 'k', label), create('div', 'v', value), create('div', 'd', description));
      container.append(card);
    });
    $('#overviewSub').textContent = `${displayDate(state.date)}, 2023 · EWR, JFK, and LGA`;
  }

  function sortFlights(items, defaultSort) {
    const sort = state.sort === 'default' ? defaultSort : state.sort;
    const compare = {
      high: (a, b) => b.pred - a.pred || b.hi - a.hi,
      low: (a, b) => a.pred - b.pred || b.conf - a.conf || a.time.localeCompare(b.time),
      worst: (a, b) => b.hi - a.hi || b.pred - a.pred,
      time: (a, b) => a.time.localeCompare(b.time) || a.carrier.localeCompare(b.carrier)
    };
    return items.sort(compare[sort]);
  }

  function flightRow(flight) {
    const button = create('button', 'row');
    button.type = 'button';
    button.setAttribute('aria-label', `${flight.carrier} ${flight.flight}, ${flight.origin} to ${flight.dest}, ${flight.time}. Predicted delay ${signed(flight.pred)} minutes. Confidence ${flight.conf} percent. 90th percentile ${signed(flight.hi)} minutes. Open details.`);
    const identity = create('span', 'flight-cell');
    const icon = create('span', 'ico', flight.carrier);
    const labels = create('span', 'flight-main');
    const name = create('strong', null, `${flight.carrier} ${flight.flight}`);
    labels.append(name, create('span', 'route-tag', `${flight.origin} → ${flight.dest}`), create('span', 'flight-sub', `${flight.time} · ${airportName(flight.dest)}`), create('span', 'mobile-risk', `90th percentile ${signed(flight.hi)} min`));
    identity.append(icon, labels);
    const confidence = create('span', 'cell-number', `${flight.conf}%`);
    const worst = create('span', 'cell-number worst-cell', `${signed(flight.hi)} min`);
    const predicted = create('span', 'pred-cell');
    predicted.append(create('span', `pill ${tone(flight.pred)}`, `${signed(flight.pred)} min`));
    button.append(identity, confidence, worst, predicted);
    button.addEventListener('click', () => openDetails(flight));
    return button;
  }

  function renderList(items, title, subtitle, emptyMessage) {
    $('#listTitle').textContent = title;
    $('#listSub').textContent = subtitle;
    const container = $('#list');
    container.replaceChildren();
    if (!items.length) {
      const empty = create('div', 'empty');
      empty.append(create('strong', null, emptyMessage || 'No matching flights'), create('p', null, state.mode === 'flight' ? 'Try a different flight number or departure date.' : 'Try an airport code, city, origin, or departure date.'));
      const clear = create('button', null, 'Clear search');
      clear.type = 'button';
      clear.addEventListener('click', () => {
        $('#qFlight').value = '';
        $('#qTo').value = '';
        $('#qFrom').value = 'ALL';
        state.page = 1;
        render();
        (state.mode === 'flight' ? $('#qFlight') : $('#qTo')).focus();
      });
      empty.append(clear);
      container.append(empty);
      $('#more').hidden = true;
      return;
    }
    items.slice(0, state.page * pageSize).forEach((flight) => container.append(flightRow(flight)));
    const remaining = items.length - state.page * pageSize;
    $('#more').hidden = remaining <= 0;
    $('#more').textContent = `Show more flights (${Math.max(remaining, 0)} remaining)`;
  }

  function render() {
    renderDates();
    const day = flights.filter((flight) => flight.date === state.date);
    renderCards(day);
    const dateLabel = `${displayDate(state.date)}, 2023`;
    if (state.mode === 'flight') {
      const query = $('#qFlight').value.trim();
      if (!query) {
        const items = sortFlights([...day], 'high');
        renderList(items, 'Highest predicted delays', `${dateLabel} · ${items.length.toLocaleString()} flights in replay · Select a row for details`);
        return;
      }
      const parsed = parseFlight(query);
      const items = parsed ? day.filter((flight) => (!parsed.carrier || flight.carrier === parsed.carrier) && String(flight.flight).startsWith(parsed.number)) : [];
      sortFlights(items, 'time');
      renderList(items, `Flight search: ${query.toUpperCase()}`, `${dateLabel} · ${items.length} matching flight${items.length === 1 ? '' : 's'}`, 'No flight matches this search');
      return;
    }
    const origin = $('#qFrom').value;
    const destination = $('#qTo').value.trim();
    if (!destination) {
      renderList([], 'Search by route', 'Choose an origin and enter a destination code or city.', 'Enter a destination to see routes');
      return;
    }
    const items = day.filter((flight) => (origin === 'ALL' || flight.origin === origin) && matchDestination(destination, flight.dest));
    sortFlights(items, 'low');
    const destinations = [...new Set(items.map((flight) => flight.dest))];
    const routeLabel = destinations.length === 1 ? `${destinations[0]} ${airportName(destinations[0])}` : destination;
    renderList(items, `${origin === 'ALL' ? 'NYC' : origin} → ${routeLabel}`, `${dateLabel} · ${items.length} matching flight${items.length === 1 ? '' : 's'}`, 'No flights found for this route');
  }

  function detailCard(label, value, description) {
    const card = create('div', 'card');
    card.append(create('div', 'k', label), create('div', 'v', value), create('div', 'd', description));
    return card;
  }

  function openDetails(flight) {
    clearTimeout(closeTimer);
    lastFocus = document.activeElement;
    const title = $('#sheetTitle');
    title.replaceChildren();
    const header = create('div', 'dh');
    const labels = create('div', 'flight-main');
    labels.append(create('strong', null, `${flight.carrier} ${flight.flight}`), create('span', 'route-tag', `${flight.origin} → ${flight.dest}`), create('div', 'flight-sub', `${airlines[flight.carrier] || flight.carrier} · ${displayDate(flight.date)}, 2023 at ${flight.time} · ${airportName(flight.dest)}`));
    header.append(create('span', 'ico', flight.carrier), labels);
    title.append(header);
    const body = $('#sheetBody');
    body.replaceChildren();
    const big = create('div', 'big');
    const value = create('span', 'n', signed(flight.pred));
    value.style.color = `var(--${{g:'green',y:'amber',r:'red'}[tone(flight.pred)]})`;
    big.append(value, create('span', 'u', 'min predicted departure delay'));
    body.append(big);

    const minimum = Math.min(-15, flight.lo, flight.actual ?? 0);
    const maximum = Math.max(60, flight.hi, Math.min(flight.actual ?? 0, 300));
    const position = (number) => `${(Math.max(minimum, Math.min(maximum, number)) - minimum) / (maximum - minimum) * 100}%`;
    const range = create('div', 'range');
    range.append(create('strong', null, `80% forecast range: ${signed(flight.lo)} to ${signed(flight.hi)} min`));
    const bar = create('div', 'bar');
    const segment = create('div', 'seg2');
    segment.style.left = position(flight.lo);
    segment.style.width = `calc(${position(flight.hi)} - ${position(flight.lo)})`;
    const predicted = create('div', 'dot');
    predicted.style.left = position(flight.pred);
    predicted.style.background = 'var(--teal)';
    const predictedLabel = create('div', 'lab', 'Predicted');
    predictedLabel.style.left = position(flight.pred);
    bar.append(segment, predicted, predictedLabel);
    if (flight.actual != null) {
      const actual = create('div', 'dot');
      actual.style.left = position(flight.actual);
      actual.style.background = 'var(--navy)';
      const actualLabel = create('div', 'lab', 'Actual');
      actualLabel.style.left = position(flight.actual);
      actualLabel.style.top = '17px';
      bar.append(actual, actualLabel);
    }
    const ticks = create('div', 'ticks');
    ticks.style.marginTop = flight.actual != null ? '23px' : '0';
    ticks.append(create('span', null, `${signed(minimum)} min`), create('span', null, `${signed(maximum)} min`));
    range.append(bar, ticks);
    body.append(range);
    const summary = create('div', 'kv');
    summary.append(detailCard('Confidence', `${flight.conf}%`, `Actual within ±${tolerance} min`), detailCard('90th percentile', `${signed(flight.hi)} min`, 'Upper forecast threshold'));
    body.append(summary);
    body.append(create('div', 'sec', 'Main forecast drivers'), create('p', 'sub', 'Contribution in minutes versus the model average; these are model explanations, not causal claims.'));
    const maxDriver = Math.max(...flight.drivers.map((driver) => Math.abs(driver[1])), 1);
    flight.drivers.forEach(([name, impact]) => {
      const line = create('div', 'drv');
      const track = create('span', 'bb');
      const fill = create('i');
      fill.style.width = `${Math.abs(impact) / maxDriver * 50}%`;
      fill.style.background = impact > 0 ? 'var(--red)' : 'var(--green)';
      fill.style[impact > 0 ? 'left' : 'right'] = '50%';
      track.append(fill);
      const number = create('b', null, `${impact > 0 ? '+' : '−'}${Math.abs(impact).toFixed(1)}`);
      number.style.color = impact > 0 ? 'var(--red)' : 'var(--green)';
      line.append(create('span', null, name), track, number);
      body.append(line);
    });
    const backtest = create('div', 'bt');
    if (flight.actual == null) {
      backtest.append(create('span', 'bad', 'Backtest outcome: flight cancelled. No actual departure delay is available.'));
    } else {
      const error = flight.actual - flight.pred;
      const within = Math.abs(error) <= tolerance;
      backtest.append(document.createTextNode(`Backtest outcome: actual delay ${signed(flight.actual)} min; prediction error ${signed(error)} min · `), create('span', within ? 'ok' : 'bad', within ? `within ±${tolerance} min` : `outside ±${tolerance} min`));
    }
    body.append(backtest);
    if (flight.tail) body.append(create('p', 'sub', `Aircraft tail number: ${flight.tail}`));
    $('#mask').classList.add('open');
    $('#sheet').setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => $('#sheet').classList.add('open'));
    document.body.style.overflow = 'hidden';
    $('#closeSheet').focus();
  }

  function closeDetails() {
    if (!$('#sheet').classList.contains('open')) return;
    $('#sheet').classList.remove('open');
    $('#sheet').setAttribute('aria-hidden', 'true');
    document.body.style.overflow = '';
    closeTimer = setTimeout(() => $('#mask').classList.remove('open'), 180);
    if (lastFocus && lastFocus.isConnected) lastFocus.focus();
  }

  function setMode(mode) {
    state.mode = mode;
    state.page = 1;
    state.sort = 'default';
    $('#sort').value = 'default';
    $('#tabFlight').setAttribute('aria-selected', String(mode === 'flight'));
    $('#tabRoute').setAttribute('aria-selected', String(mode === 'route'));
    $('#searchFlight').hidden = mode !== 'flight';
    $('#searchRoute').hidden = mode !== 'route';
    render();
  }

  function setup() {
    const origin = $('#qFrom');
    [['ALL','All NYC airports'], ['EWR','EWR · Newark'], ['JFK','JFK · Kennedy'], ['LGA','LGA · LaGuardia']].forEach(([value,label]) => origin.append(new Option(label, value)));
    const destinations = $('#destList');
    [...new Set(flights.map((flight) => flight.dest))].sort().forEach((code) => {
      const option = new Option(`${code} · ${airports[code] || code}`, code);
      destinations.append(option);
    });
    $('#tabFlight').addEventListener('click', () => setMode('flight'));
    $('#tabRoute').addEventListener('click', () => setMode('route'));
    $('#qFlight').addEventListener('input', () => { state.page = 1; render(); });
    $('#qFrom').addEventListener('change', () => { state.page = 1; render(); });
    $('#qTo').addEventListener('input', () => { state.page = 1; render(); });
    $('#sort').addEventListener('change', (event) => { state.sort = event.target.value; state.page = 1; render(); });
    $('#clearRoute').addEventListener('click', () => { $('#qTo').value = ''; state.page = 1; render(); $('#qTo').focus(); });
    $('#more').addEventListener('click', () => { state.page += 1; render(); });
    $('#closeSheet').addEventListener('click', closeDetails);
    $('#mask').addEventListener('click', closeDetails);
    document.addEventListener('keydown', (event) => {
      if ($('#sheet').classList.contains('open')) {
        if (event.key === 'Escape') closeDetails();
        if (event.key === 'Tab') {
          event.preventDefault();
          $('#closeSheet').focus();
        }
      } else if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && document.activeElement?.getAttribute('role') === 'tab') {
        event.preventDefault();
        const next = state.mode === 'flight' ? $('#tabRoute') : $('#tabFlight');
        next.click();
        next.focus();
      }
    });
    $('#tolTxt').textContent = tolerance;
    const evaluation = DATA.eval;
    const stats = [
      ['Flights tested', evaluation.test_flights.toLocaleString()],
      ['Mean absolute error', `${evaluation.mae_model.toFixed(1)} min`],
      [`Within ±${tolerance} min`, `${Math.round(evaluation.hit15_model * 100)}%`],
      ['80% range coverage', `${Math.round(evaluation.interval80_coverage * 100)}%`]
    ];
    const container = $('#evalStats');
    stats.forEach(([label,value]) => { const line = create('div', 'stat'); line.append(create('span', null, label), create('strong', null, value)); container.append(line); });
    render();
  }
  setup();
})();
