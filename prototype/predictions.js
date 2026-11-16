/* Delay / on-time predictions for JFK, LGA and EWR.
 *
 * Reads the static file data/predictions.json, which the team regenerates with
 * `python -m skydelay.models.publish_predictions` and pushes to GitHub Pages.
 * Add ?sample=1 to the page URL to preview the layout with illustrative data.
 */
(() => {
  'use strict';
  const params = new URLSearchParams(location.search);
  const SAMPLE = params.has('sample');
  const SOURCE = SAMPLE ? 'data/predictions.sample.json' : 'data/predictions.json';
  const REFRESH_MS = 5 * 60 * 1000;
  const AIRPORTS = ['JFK', 'LGA', 'EWR'];
  const SHORT = {JFK: 'Kennedy', LGA: 'LaGuardia', EWR: 'Newark'};

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
  const band = (confidence) => (confidence >= 0.8 ? 'High' : confidence >= 0.65 ? 'Medium' : 'Low');

  const state = {data: null, airport: 'JFK', filter: 'all', sort: 'time', query: '', showPast: false};

  function rebaseSample(data) {
    // Sample times are shifted so the preview always looks current.
    const shift = Date.now() - 12 * 60000 - Date.parse(data.generated_at);
    const move = (iso) => new Date(Date.parse(iso) + shift).toISOString();
    return {...data, generated_at: move(data.generated_at), flights: data.flights.map((f) => ({...f, scheduled_dep_utc: move(f.scheduled_dep_utc)}))};
  }

  function normalise(raw) {
    const data = SAMPLE ? rebaseSample(raw) : raw;
    const flights = (Array.isArray(data.flights) ? data.flights : [])
      .filter((f) => AIRPORTS.includes(f.airport) && Number.isFinite(Date.parse(f.scheduled_dep_utc)) && Number.isFinite(f.confidence))
      .map((f) => ({...f, departs: Date.parse(f.scheduled_dep_utc)}));
    return {...data, flights, generatedMs: Date.parse(data.generated_at)};
  }

  function isPast(flight) { return flight.departs < Date.now(); }

  function visible(airport) {
    return state.data.flights.filter((f) => f.airport === airport && (state.showPast || !isPast(f)));
  }

  function renderMeta() {
    const data = state.data;
    const meta = $('#predMeta');
    const banner = $('#predBanner');
    meta.replaceChildren();
    banner.hidden = true;
    banner.className = 'pred-banner';
    if (!Number.isFinite(data.generatedMs)) {
      meta.append(node('span', 'pred-pill pred-pill-wait', 'Awaiting first publish'));
      return;
    }
    const staleAfter = (data.stale_after_minutes || 180) * 60000;
    const stale = Date.now() - data.generatedMs > staleAfter;
    meta.append(
      node('span', `pred-pill ${stale ? 'pred-pill-stale' : 'pred-pill-live'}`, stale ? 'Out of date' : 'Current'),
      node('span', null, `Updated ${ago(data.generatedMs)}`),
      node('small', null, `${day.format(data.generatedMs)}, ${clock.format(data.generatedMs)} ET · model ${data.model?.name || 'unnamed'}`),
    );
    if (SAMPLE) {
      banner.hidden = false;
      banner.classList.add('pred-banner-sample');
      banner.replaceChildren(node('strong', null, 'Sample data — layout preview only. '),
        document.createTextNode('These flights and percentages are illustrative, not SkyDelay predictions. '));
      const link = node('a', null, 'View published predictions');
      link.href = location.pathname + '#forecast';
      banner.append(link);
    } else if (stale) {
      banner.hidden = false;
      banner.classList.add('pred-banner-stale');
      banner.replaceChildren(node('strong', null, 'These predictions are out of date. '),
        document.createTextNode(`They were published ${ago(data.generatedMs)} and may not reflect current conditions. Check the airport conditions below and the official flight boards.`));
    }
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
      button.append(node('strong', null, code), node('span', null, SHORT[code]));
      const risk = node('small', delayed ? 'has-delays' : null, flights.length ? `${delayed} of ${flights.length} predicted delayed` : 'No upcoming predictions');
      button.append(risk);
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
    const summary = $('#predSummary');
    summary.replaceChildren();
    const delayed = flights.filter((f) => f.prediction === 'delayed');
    const onTime = flights.length - delayed.length;
    const mean = flights.length ? flights.reduce((s, f) => s + f.confidence, 0) / flights.length : null;
    summary.append(
      tile('Departures predicted', String(flights.length), state.showPast ? 'Including departed' : 'Still to depart'),
      tile('Predicted delayed', String(delayed.length), flights.length ? `${pct(delayed.length / flights.length)} of departures · 30+ min` : '—', 'delayed'),
      tile('Predicted on time', String(onTime), flights.length ? `${pct(onTime / flights.length)} of departures` : '—', 'ontime'),
      tile('Average confidence', mean == null ? '—' : pct(mean), mean == null ? 'No flights' : `${band(mean)} on average`),
    );
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

  function renderTable(all) {
    const table = $('#predTable');
    table.replaceChildren();
    const query = state.query.trim().toUpperCase();
    let flights = all.filter((f) => state.filter === 'all' || f.prediction === state.filter)
      .filter((f) => !query || `${f.flight} ${flightLabel(f)} ${f.dest}`.toUpperCase().includes(query));
    flights = flights.slice().sort(state.sort === 'confidence'
      ? (a, b) => b.confidence - a.confidence || a.departs - b.departs
      : state.sort === 'risk'
        ? (a, b) => (b.delay_probability ?? 0) - (a.delay_probability ?? 0) || a.departs - b.departs
        : (a, b) => a.departs - b.departs || a.flight.localeCompare(b.flight));

    if (!flights.length) {
      const empty = node('div', 'pred-empty');
      empty.append(node('strong', null, all.length ? 'No flights match these filters' : 'No upcoming departures with a prediction'),
        node('p', null, all.length ? 'Clear the search or choose “All”.' : `The latest published file has no ${state.airport} departures still to leave.`));
      table.append(empty);
      return;
    }
    const head = node('div', 'pred-row pred-colhead');
    ['Departs (ET)', 'Flight', 'To', 'Prediction', 'Model confidence'].forEach((label) => head.append(node('span', null, label)));
    head.setAttribute('aria-hidden', 'true');
    table.append(head);
    const list = node('ul', 'pred-list');
    list.setAttribute('aria-label', `${state.airport} departure predictions`);
    flights.forEach((f) => {
      const row = node('li', `pred-row${isPast(f) ? ' is-past' : ''}`);
      const when = node('span', 'pred-time');
      when.append(node('strong', null, clock.format(f.departs)), node('small', null, isPast(f) ? 'Scheduled time passed' : day.format(f.departs)));
      const status = node('span', `pred-status ${f.prediction === 'delayed' ? 'is-delayed' : 'is-ontime'}`, f.prediction === 'delayed' ? 'Delayed 30+ min' : 'On time');
      row.append(when, node('strong', 'pred-flight', flightLabel(f)), node('span', 'pred-dest', `${state.airport} → ${f.dest}`), status, confidenceCell(f));
      row.title = `Probability of a 30+ minute delay: ${pct(f.delay_probability ?? (f.prediction === 'delayed' ? f.confidence : 1 - f.confidence))}`;
      row.setAttribute('aria-label', `${flightLabel(f)} to ${f.dest}, departs ${clock.format(f.departs)} Eastern: predicted ${f.prediction === 'delayed' ? 'delayed 30 minutes or more' : 'on time'}, ${pct(f.confidence)} confidence`);
      list.append(row);
    });
    table.append(list);
    if (flights.length !== all.length) table.append(node('p', 'pred-count', `Showing ${flights.length} of ${all.length} flights`));
  }

  function renderMethod() {
    const model = state.data.model || {};
    const method = $('#predMethod');
    method.replaceChildren();
    const threshold = Number.isFinite(model.threshold) ? pct(model.threshold) : '50%';
    method.append(node('p', null, `A flight is called delayed when the model’s probability of leaving 30 or more minutes late reaches ${threshold}. Confidence is the model’s probability for the call it made, so it ranges from 50% (a toss-up) to 100%. Predictions are made about ${model.horizon_hours || 2} hours before scheduled departure.`));
    const v = model.validation || {};
    if (Number.isFinite(v.accuracy)) {
      const parts = [`accuracy ${pct(v.accuracy)}`];
      if (Number.isFinite(v.recall)) parts.push(`recall on delays ${pct(v.recall)}`);
      if (Number.isFinite(v.precision)) parts.push(`precision ${pct(v.precision)}`);
      if (Number.isFinite(v.brier_score)) parts.push(`Brier score ${v.brier_score.toFixed(3)}`);
      const scope = v.test_rows ? ` on ${Number(v.test_rows).toLocaleString()} held-out flights` : '';
      method.append(node('p', 'pred-validation', `Validation${scope}: ${parts.join(' · ')}.`));
    }
  }

  function renderAwaiting() {
    $('#predMeta').replaceChildren(node('span', 'pred-pill pred-pill-wait', 'Awaiting first publish'));
    $('#predBanner').hidden = true;
    ['#predAirports', '#predSummary'].forEach((s) => $(s).replaceChildren());
    $('#predControls').hidden = true;
    const empty = node('div', 'pred-empty pred-empty-large');
    empty.append(node('strong', null, 'No predictions have been published yet'),
      node('p', null, 'Delay or on-time calls with model confidence will appear here for JFK, LGA and EWR once the team publishes a scored departure schedule.'));
    const link = node('a', 'forecast-link', 'Preview the layout with sample data →');
    link.href = '?sample=1#forecast';
    empty.append(link);
    $('#predTable').replaceChildren(empty);
    renderMethod();
  }

  function render() {
    if (!state.data) return;
    if (state.data.status === 'awaiting_model' || !Number.isFinite(state.data.generatedMs)) { renderAwaiting(); return; }
    $('#predControls').hidden = false;
    renderMeta();
    renderTabs();
    const flights = visible(state.airport);
    renderSummary(flights);
    renderTable(flights);
    renderMethod();
  }

  async function load() {
    try {
      const response = await fetch(`${SOURCE}?t=${Date.now()}`, {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.data = normalise(await response.json());
      render();
    } catch {
      if (state.data) { renderMeta(); return; }
      $('#predMeta').replaceChildren(node('span', 'pred-pill pred-pill-stale', 'Unavailable'));
      const empty = node('div', 'pred-empty pred-empty-large');
      empty.append(node('strong', null, 'Predictions could not be loaded'), node('p', null, 'Reload the page in a moment. The airport conditions below are loaded separately.'));
      $('#predTable').replaceChildren(empty);
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
  }

  if (AIRPORTS.includes((params.get('airport') || '').toUpperCase())) state.airport = params.get('airport').toUpperCase();
  bindControls();
  load();
  setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
  // Keep relative times and the "departed" cut-off current between fetches.
  setInterval(() => { if (!document.hidden) render(); }, 60000);
})();
