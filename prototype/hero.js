/* Hero: flight search, shortcuts and the "right now" airport card. */
(() => {
  'use strict';
  const $ = (selector) => document.querySelector(selector);
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  };
  const AIRPORTS = {JFK: 'Kennedy', LGA: 'LaGuardia', EWR: 'Newark'};
  const ALIASES = {KENNEDY: 'JFK', LAGUARDIA: 'LGA', NEWARK: 'EWR'};

  function goToBoard(selection) {
    window.SkyDelayBoard?.select(selection);
    document.getElementById('forecast').scrollIntoView({behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  }

  $('#heroSearch').addEventListener('submit', (event) => {
    event.preventDefault();
    const raw = $('#heroQuery').value.trim();
    const word = raw.toUpperCase().replace(/[^A-Z]/g, '');
    const airport = raw.toUpperCase() in AIRPORTS ? raw.toUpperCase() : ALIASES[word];
    if (airport) goToBoard({airport, query: ''});
    else goToBoard({query: raw});
  });
  document.querySelectorAll('[data-try-airport]').forEach((button) => button.addEventListener('click', () => {
    goToBoard({airport: button.dataset.tryAirport, direction: button.dataset.tryDirection, query: ''});
  }));

  const ago = (iso) => {
    const minutes = Math.round((Date.now() - Date.parse(iso)) / 60000);
    if (!Number.isFinite(minutes)) return null;
    return minutes < 1 ? 'just now' : minutes < 120 ? `${minutes} min ago` : `${Math.floor(minutes / 60)} h ago`;
  };

  function airportRow(code, data) {
    const events = data?.faa?.events?.[code];
    const weather = data?.weather?.airports?.[code];
    const tone = Array.isArray(events) ? (events.length ? 'alert' : 'ok') : 'unknown';
    const row = node('li', `is-${tone}`);
    row.append(node('span', 'hero-ico', code));
    const text = node('span');
    const headline = Array.isArray(events)
      ? (events.length ? events.map((e) => e.type.replace('General Arrival/Departure Delay Info', 'Delays').replace(' Programs', '')).join(' · ') : 'No FAA delay or ground stop')
      : 'FAA status unavailable';
    text.append(node('strong', null, headline));
    if (weather) {
      const wind = weather.windKt == null ? null : `wind ${weather.windKt}${weather.gustKt ? `G${weather.gustKt}` : ''} kt`;
      text.append(node('small', null, [AIRPORTS[code], weather.category, wind, weather.visibilityMi && `${weather.visibilityMi} mi`].filter(Boolean).join(' · ')));
    } else text.append(node('small', null, AIRPORTS[code]));
    row.append(text);
    return row;
  }

  window.addEventListener('skydelay:operations', (event) => {
    const data = event.detail;
    const rows = $('#heroRows');
    const live = $('#heroLive');
    if (!data) {
      rows.replaceChildren(node('li', 'is-unknown', 'Live airport feeds are unavailable right now.'));
      live.textContent = 'Offline';
      live.className = 'hero-chip is-off';
      return;
    }
    rows.replaceChildren(...Object.keys(AIRPORTS).map((code) => airportRow(code, data)));
    const alerts = Object.values(data.faa?.events || {}).reduce((n, list) => n + (list?.length || 0), 0);
    live.textContent = alerts ? `${alerts} FAA alert${alerts > 1 ? 's' : ''}` : 'All clear';
    live.className = `hero-chip ${alerts ? 'is-alert' : 'is-ok'}`;
    const updated = ago(data.fetchedAt);
    $('#heroUpdated').textContent = updated ? `Updated ${updated}` : 'FAA NAS · NOAA METAR';
  });
})();
