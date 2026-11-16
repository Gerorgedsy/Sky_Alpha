/* Delay / on-time predictions for departures from and arrivals at JFK, LGA and EWR.
 *
 * Reads the static file data/predictions.json, which the team regenerates with
 * `python -m skydelay.models.publish_predictions` and pushes to GitHub Pages.
 * URL options: ?sample=1 previews the layout with illustrative data,
 * ?airport=LGA opens an airport, ?view=arrivals opens the arrivals board.
 */
(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const SAMPLE = params.has('sample');
  // Published predictions live on the live-data branch, refreshed by the
  // "Live airport data" workflow; the copy in this folder is the fallback.
  const SNAPSHOT = (params.get('liveData') || 'https://raw.githubusercontent.com/Gerorgedsy/Sky_Alpha/live-data').replace(/\/$/, '');
  const SOURCES = SAMPLE ? ['data/predictions.sample.json'] : [`${SNAPSHOT}/predictions.json`, 'data/predictions.json'];
  const REFRESH_MS = 5 * 60 * 1000;
  const AIRPORTS = ['JFK', 'LGA', 'EWR'];
  const SHORT = {JFK: 'Kennedy', LGA: 'LaGuardia', EWR: 'Newark'};
  const WORDS = {
    departure: {plural: 'Departures', lower: 'departures', time: 'Departs (ET)', other: 'To', verb: 'leaving', still: 'Still to depart', done: 'Including departed', toggle: 'Show departed'},
    arrival: {plural: 'Arrivals', lower: 'arrivals', time: 'Arrives (ET)', other: 'From', verb: 'landing', still: 'Still to land', done: 'Including landed', toggle: 'Show landed'},
  };

  const $ = (selector) => document.querySelector(selector);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  };
  const clock = new Intl.DateTimeFormat('en-US', {hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York'});
  const day = new Intl.DateTimeFormat('en-US', {weekday: 'short', month: 'short', day: 'numeric', timeZone: 'America/New_York'});
  const pct = (value) => `${Math.round(value * 100)}%`;
  const ago = (ms) => {
    const minutes = Math.max(0, Math.round((Date.now() - ms) / 60000));
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    return hours < 48 ? `${hours} h ${minutes % 60} min ago` : `${Math.floor(hours / 24)} days ago`;
  };
  const flightLabel = (f) => (f.carrier && f.flight.startsWith(f.carrier) ? `${f.carrier} ${f.flight.slice(f.carrier.length)}` : f.flight);
  const minutes = (value) => (Math.abs(value) >= 60 ? `${Math.floor(Math.abs(value) / 60)} h ${Math.abs(value) % 60} min` : `${Math.abs(value)} min`);
  const hasMinutes = (f) => Number.isFinite(f.predicted_delay_min);
  const band = (confidence) => (confidence >= 0.8 ? 'High' : confidence >= 0.65 ? 'Medium' : 'Low');
  const route = (f) => (f.direction === 'arrival' ? `${f.origin || '—'} → ${f.airport}` : `${f.airport} → ${f.dest || '—'}`);

  const state = {
    data: null,
    direction: params.get('view') === 'arrivals' ? 'arrival' : 'departure',
    airport: 'JFK', filter: 'all', sort: 'time', query: '', showPast: false,
  };

  function rebaseSample(data, flights) {
    // Sample times are shifted so the preview always looks current.
    const shift = Date.now() - 12 * 60000 - Date.parse(data.generated_at);
    flights.forEach((f) => { f.scheduled += shift; });
    return new Date(Date.parse(data.generated_at) + shift).toISOString();
  }

  function normalise(data) {
    // Schema 1 held departures only, with scheduled_dep_utc and one model.
    const models = data.models || {departure: data.model || null, arrival: null};
    const flights = (Array.isArray(data.flights) ? data.flights : []).map((f) => {
      const direction = f.direction === 'arrival' ? 'arrival' : 'departure';
      return {
        ...f, direction,
        origin: f.origin || (direction === 'departure' ? f.airport : undefined),
        scheduled: Date.parse(f.scheduled_utc || f.scheduled_dep_utc),
      };
    }).filter((f) => AIRPORTS.includes(f.airport) && Number.isFinite(f.scheduled) && Number.isFinite(f.confidence));
    const generatedAt = SAMPLE ? rebaseSample(data, flights) : data.generated_at;
    return {...data, models, flights, generatedMs: Date.parse(generatedAt)};
  }

  const isPast = (flight) => flight.scheduled < Date.now();
  const model = () => state.data.models[state.direction];
  const visible = (airport) => state.data.flights.filter((f) => f.direction === state.direction && f.airport === airport && (state.showPast || !isPast(f)));

  function renderMeta() {
    const data = state.data;
    const meta = $('#predMeta');
    const banner = $('#predBanner');
    meta.replaceChildren();
    banner.hidden = true;
    if (!Number.isFinite(data.generatedMs)) {
      meta.append(node('span', 'pred-pill pred-pill-wait', 'Awaiting first publish'));
      return;
    }
    const stale = Date.now() - data.generatedMs > (data.stale_after_minutes || 180) * 60000;
    if (!SAMPLE) meta.append(node('span', `pred-pill ${stale ? 'pred-pill-stale' : 'pred-pill-live'}`, stale ? 'Out of date' : 'Current'));
    meta.append(
      node('span', null, `Updated ${ago(data.generatedMs)}`),
      node('small', null, `${day.format(data.generatedMs)}, ${clock.format(data.generatedMs)} ET`),
    );
    if (stale && !SAMPLE) {
      banner.hidden = false;
      banner.replaceChildren(node('strong', null, 'These predictions are out of date. '),
        document.createTextNode(`They were published ${ago(data.generatedMs)} and may not reflect current conditions. Check the airport conditions below and the official flight boards.`));
    }
  }

  function renderDirection() {
    document.querySelectorAll('[data-pred-direction]').forEach((button) => {
      const direction = button.dataset.predDirection;
      button.setAttribute('aria-pressed', String(direction === state.direction));
      const count = state.data ? state.data.flights.filter((f) => f.direction === direction && (state.showPast || !isPast(f))).length : 0;
      button.querySelector('small').textContent = state.data?.models[direction] ? `${count} flights` : 'Not published';
    });
    $('#predPastLabel').textContent = WORDS[state.direction].toggle;
    const verb = $('#pageTitleVerb');
    if (verb) verb.textContent = state.direction === 'arrival' ? 'arrive on time?' : 'leave on time?';
  }

  function renderTabs() {
    const tabs = $('#predAirports');
    tabs.replaceChildren();
    AIRPORTS.forEach((code) => {
      const flights = visible(code);
      const delayed = flights.filter((f) => f.prediction === 'delayed').length;
      const button = node('button', 'pred-airport');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(code === state.airport));
      button.append(node('strong', null, code), node('span', null, SHORT[code]),
        node('small', delayed ? 'has-delays' : null, flights.length ? `${delayed} of ${flights.length} predicted delayed` : `No upcoming ${WORDS[state.direction].lower}`));
      button.addEventListener('click', () => { state.airport = code; render(); });
      tabs.append(button);
    });
  }

  function tile(label, value, detail, tone) {
    const card = node('div', `pred-tile${tone ? ` pred-tile-${tone}` : ''}`);
    card.append(node('small', null, label), node('strong', null, value), node('span', null, detail));
    return card;
  }

  function renderSummary(flights) {
    const words = WORDS[state.direction];
    const summary = $('#predSummary');
    summary.replaceChildren();
    const delayed = flights.filter((f) => f.prediction === 'delayed').length;
    const onTime = flights.length - delayed;
    const mean = flights.length ? flights.reduce((s, f) => s + f.confidence, 0) / flights.length : null;
    summary.append(
      tile(`${words.plural} predicted`, String(flights.length), state.showPast ? words.done : words.still),
      tile('Predicted delayed', String(delayed), flights.length ? `${pct(delayed / flights.length)} of ${words.lower}${lateAverage(flights)}` : '—', 'delayed'),
      tile('Predicted on time', String(onTime), flights.length ? `${pct(onTime / flights.length)} of ${words.lower}` : '—', 'ontime'),
      tile('Average confidence', mean == null ? '—' : pct(mean), mean == null ? 'No flights' : `${band(mean)} on average`),
    );
  }

  function lateAverage(flights) {
    const late = flights.filter((f) => f.prediction === 'delayed' && hasMinutes(f));
    if (!late.length) return ' · 30+ min';
    return ` · about ${minutes(Math.round(late.reduce((s, f) => s + Math.max(f.predicted_delay_min, 0), 0) / late.length))} late`;
  }

  function expectedCell(flight) {
    const cell = node('span', 'pred-expect');
    if (!hasMinutes(flight)) { cell.append(node('span', 'pred-muted', '—')); return cell; }
    const median = flight.predicted_delay_min;
    const text = median >= 5 ? `+${minutes(median)}` : median <= -5 ? `${minutes(median)} early` : 'On schedule';
    cell.append(node('strong', median >= 15 ? 'is-late' : null, text));
    if (Number.isFinite(flight.delay_low_min) && Number.isFinite(flight.delay_high_min)) {
      const fmt = (v) => (v > 0 ? `+${v}` : String(v));
      cell.append(node('small', null, `${flight.prediction === 'delayed' ? 'if late' : 'typically'} ${fmt(flight.delay_low_min)} to ${fmt(flight.delay_high_min)} min`));
    }
    return cell;
  }

  function confidenceCell(flight) {
    const cell = node('div', 'pred-conf');
    const bar = node('div', 'pred-bar');
    bar.setAttribute('aria-hidden', 'true');
    const fill = node('i', flight.prediction === 'delayed' ? 'is-delayed' : 'is-ontime');
    fill.style.width = `${Math.max(0, Math.min(100, flight.confidence * 100))}%`;
    bar.append(fill);
    const label = node('span', 'pred-conf-label');
    label.append(node('strong', null, pct(flight.confidence)), node('small', null, band(flight.confidence)));
    cell.append(bar, label);
    return cell;
  }

  function emptyState(title, text, large) {
    const empty = node('div', `pred-empty${large ? ' pred-empty-large' : ''}`);
    empty.append(node('strong', null, title), node('p', null, text));
    return empty;
  }

  function renderTable(all) {
    const words = WORDS[state.direction];
    const table = $('#predTable');
    table.replaceChildren();
    const query = state.query.trim().toUpperCase();
    let flights = all.filter((f) => state.filter === 'all' || f.prediction === state.filter)
      .filter((f) => !query || `${f.flight} ${flightLabel(f)} ${f.origin} ${f.dest}`.toUpperCase().includes(query));
    flights = flights.slice().sort(state.sort === 'confidence'
      ? (a, b) => b.confidence - a.confidence || a.scheduled - b.scheduled
      : state.sort === 'longest'
        ? (a, b) => (b.predicted_delay_min ?? -999) - (a.predicted_delay_min ?? -999) || a.scheduled - b.scheduled
      : state.sort === 'risk'
        ? (a, b) => (b.delay_probability ?? 0) - (a.delay_probability ?? 0) || a.scheduled - b.scheduled
        : (a, b) => a.scheduled - b.scheduled || a.flight.localeCompare(b.flight));

    if (!flights.length) {
      table.append(all.length
        ? emptyState('No flights match these filters', 'Clear the search or choose “All”.')
        : emptyState(`No upcoming ${words.lower} with a prediction`, `The latest published file has no ${state.airport} ${words.lower} still to come.`));
      return;
    }
    const head = node('div', 'pred-row pred-colhead');
    [words.time, 'Flight', words.other, 'Prediction', 'Expected delay', 'Model confidence'].forEach((label) => head.append(node('span', null, label)));
    head.setAttribute('aria-hidden', 'true');
    table.append(head);
    const list = node('ul', 'pred-list');
    list.setAttribute('aria-label', `${state.airport} ${words.lower} predictions`);
    flights.forEach((f) => {
      const past = isPast(f);
      const row = node('li', `pred-row${past ? ' is-past' : ''}`);
      const when = node('span', 'pred-time');
      const estimate = hasMinutes(f) && f.predicted_delay_min >= 5 ? `est. ${clock.format(f.scheduled + f.predicted_delay_min * 60000)}` : null;
      when.append(node('strong', null, clock.format(f.scheduled)), node('small', estimate && f.predicted_delay_min >= 15 ? 'is-late' : null, past ? 'Scheduled time passed' : estimate || day.format(f.scheduled)));
      const delayed = f.prediction === 'delayed';
      row.append(when, node('strong', 'pred-flight', flightLabel(f)), node('span', 'pred-dest', route(f)),
        node('span', `pred-status ${delayed ? 'is-delayed' : 'is-ontime'}`, delayed ? 'Delayed 30+ min' : 'On time'), expectedCell(f), confidenceCell(f));
      row.title = `Probability of a 30+ minute delay: ${pct(f.delay_probability ?? (delayed ? f.confidence : 1 - f.confidence))}`;
      row.setAttribute('aria-label', `${flightLabel(f)} ${route(f).replace('→', 'to')}, ${state.direction === 'arrival' ? 'arrives' : 'departs'} ${clock.format(f.scheduled)} Eastern: predicted ${delayed ? 'delayed 30 minutes or more' : 'on time'}, ${pct(f.confidence)} confidence`);
      list.append(row);
    });
    table.append(list);
    if (flights.length !== all.length) table.append(node('p', 'pred-count', `Showing ${flights.length} of ${all.length} flights`));
  }

  function renderMethod() {
    const current = model() || {};
    const verb = WORDS[state.direction].verb;
    const method = $('#predMethod');
    method.replaceChildren();
    const threshold = Number.isFinite(current.threshold) ? pct(current.threshold) : '50%';
    const timing = Number.isFinite(current.horizon_hours)
      ? `Predictions are made about ${current.horizon_hours} hours before the scheduled time.`
      : 'Predictions cover the whole day and use the schedule alone (airline, route, local time, airport traffic and holidays), not live weather or delays.';
    method.append(node('p', null, `A flight is called delayed when the model’s probability of ${verb} 30 or more minutes late reaches ${threshold}. Confidence is the model’s probability for the call it made, from 50% (a toss-up) to 100%. Expected delay is the model’s median for flights like this one that went the way of the call, with the range 80% of them fell in. ${timing}`));
    if (state.data.schedule?.note) method.append(node('p', null, state.data.schedule.note));
    const v = current.validation || {};
    if (Number.isFinite(v.accuracy)) {
      const parts = [`accuracy ${pct(v.accuracy)}`];
      if (Number.isFinite(v.recall)) parts.push(`recall on delays ${pct(v.recall)}`);
      if (Number.isFinite(v.precision)) parts.push(`precision ${pct(v.precision)}`);
      if (Number.isFinite(v.brier_score)) parts.push(`Brier score ${v.brier_score.toFixed(3)}`);
      if (Number.isFinite(v.mae_minutes)) parts.push(`typical delay error ${Math.round(v.mae_minutes)} min`);
      const scope = v.test_rows ? ` on ${Number(v.test_rows).toLocaleString()} held-out flights` : '';
      method.append(node('p', 'pred-validation', `${WORDS[state.direction].plural} model validation${scope}: ${parts.join(' · ')}.`));
    }
  }

  function renderAwaiting() {
    $('#predMeta').replaceChildren(node('span', 'pred-pill pred-pill-wait', 'Awaiting first publish'));
    $('#predBanner').hidden = true;
    ['#predAirports', '#predSummary'].forEach((s) => $(s).replaceChildren());
    $('#predControls').hidden = true;
    const empty = emptyState('No predictions have been published yet', 'Delay or on-time calls with model confidence will appear here for departures and arrivals at JFK, LGA and EWR once the team publishes a scored schedule.', true);
    const link = node('a', 'forecast-link', 'Preview the layout with sample data →');
    link.href = '?sample=1#forecast';
    empty.append(link);
    $('#predTable').replaceChildren(empty);
    renderMethod();
  }

  function render() {
    if (!state.data) return;
    renderDirection();
    if (state.data.status === 'awaiting_model' || !Number.isFinite(state.data.generatedMs)) { renderAwaiting(); return; }
    renderMeta();
    if (!model()) {
      $('#predControls').hidden = true;
      ['#predAirports', '#predSummary'].forEach((s) => $(s).replaceChildren());
      $('#predTable').replaceChildren(emptyState(`${WORDS[state.direction].plural} predictions are not published yet`, `The latest file includes ${state.direction === 'arrival' ? 'departure' : 'arrival'} predictions only.`, true));
      renderMethod();
      return;
    }
    $('#predControls').hidden = false;
    renderTabs();
    const flights = visible(state.airport);
    renderSummary(flights);
    renderTable(flights);
    renderMethod();
  }

  async function fetchFirst() {
    for (const source of SOURCES) {
      try {
        const response = await fetch(`${source}?t=${Math.floor(Date.now() / 60000)}`, {cache: 'no-store'});
        if (!response.ok) continue;
        const body = await response.json();
        if (body && Array.isArray(body.flights)) return body;
      } catch { /* try the next source */ }
    }
    throw new Error('No predictions source answered');
  }

  async function load() {
    try {
      state.data = normalise(await fetchFirst());
      render();
    } catch {
      if (state.data) { renderMeta(); return; }
      $('#predMeta').replaceChildren(node('span', 'pred-pill pred-pill-stale', 'Unavailable'));
      $('#predTable').replaceChildren(emptyState('Predictions could not be loaded', 'Reload the page in a moment. The airport conditions below are loaded separately.', true));
    }
  }

  function bindControls() {
    $('#predSearch').addEventListener('input', (event) => { state.query = event.target.value; render(); });
    $('#predSort').addEventListener('change', (event) => { state.sort = event.target.value; render(); });
    $('#predPast').addEventListener('change', (event) => { state.showPast = event.target.checked; render(); });
    document.querySelectorAll('[data-pred-filter]').forEach((button) => button.addEventListener('click', () => {
      state.filter = button.dataset.predFilter;
      document.querySelectorAll('[data-pred-filter]').forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
      render();
    }));
    document.querySelectorAll('[data-pred-direction]').forEach((button) => button.addEventListener('click', () => {
      state.direction = button.dataset.predDirection;
      render();
    }));
  }

  if (AIRPORTS.includes((params.get('airport') || '').toUpperCase())) state.airport = params.get('airport').toUpperCase();
  // Lets the hero search and its shortcuts drive the board.
  window.SkyDelayBoard = {
    select({airport, direction, query} = {}) {
      if (AIRPORTS.includes(airport)) state.airport = airport;
      if (direction === 'arrival' || direction === 'departure') state.direction = direction;
      if (query != null) { state.query = query; $('#predSearch').value = query; }
      render();
    },
  };
  bindControls();
  load();
  setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  // Keep relative times and the "departed" cut-off current between fetches.
  setInterval(() => { if (!document.hidden) render(); }, 60000);
})();
