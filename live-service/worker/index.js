/** SkyDelay live airspace feed. Public aircraft positions are cached for shared use. */
const FEED = 'https://api.adsb.lol/v2/lat/40.7/lon/-73.9/dist/75';
const ALLOWED_ORIGINS = new Set([
  'https://gerorgedsy.github.io',
  'http://localhost:8765',
  'http://127.0.0.1:8765',
]);
const DEFAULT_REFRESH_SECONDS = 210;
const MAX_STALE_SECONDS = 900;
const AIRPORTS = ['JFK', 'EWR', 'LGA'];
const WEATHER_URL = 'https://aviationweather.gov/api/data/metar?ids=KJFK,KEWR,KLGA&format=json';
const FAA_URL = 'https://nasstatus.faa.gov/api/airport-status-information';


function cors(origin) {
  return origin && ALLOWED_ORIGINS.has(origin) ? {
    'access-control-allow-origin': origin,
    'access-control-allow-methods': 'GET, OPTIONS',
    'access-control-allow-headers': 'content-type',
    'access-control-max-age': '3600',
    vary: 'Origin',
  } : {};
}

function reply(data, status, origin) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
      'x-content-type-options': 'nosniff',
      ...cors(origin),
    },
  });
}

function normalize(raw) {
  const snapshotMs = Number(raw.now);
  if (!Number.isFinite(snapshotMs) || !Array.isArray(raw.ac)) {
    throw new Error('Invalid upstream response');
  }
  const aircraft = raw.ac.filter((item) =>
    Number.isFinite(item.lat) && Number.isFinite(item.lon) &&
    Number(item.seen_pos ?? item.seen ?? 999) <= 90 &&
    item.lat >= 39.4 && item.lat <= 42 && item.lon >= -76 && item.lon <= -71.5
  ).map((item) => ({
    id: String(item.hex || '').toLowerCase(),
    callsign: String(item.flight || '').trim() || null,
    registration: String(item.r || '').trim() || null,
    aircraftType: String(item.t || '').trim() || null,
    lat: item.lat,
    lon: item.lon,
    altitudeFt: Number.isFinite(item.alt_baro) ? Math.round(item.alt_baro) : null,
    onGround: item.alt_baro === 'ground',
    groundSpeedKt: Number.isFinite(item.gs) ? Math.round(item.gs) : null,
    trackDeg: Number.isFinite(item.track) ? Math.round(item.track) : null,
    positionAgeSeconds: Number(item.seen_pos ?? item.seen ?? 0),
    sourceType: String(item.type || '').trim() || null,
  })).filter((item) => item.id);
  return {
    status: 'live',
    source: 'adsb.lol',
    license: 'ODbL 1.0',
    region: 'New York metro, approximately 75 nautical miles from 40.7°N, 73.9°W',
    observedAt: new Date(snapshotMs).toISOString(),
    fetchedAt: new Date().toISOString(),
    aircraft,
  };
}

async function upstream(env) {
  const headers = {accept: 'application/json'};
  if (env.ADSB_LOL_API_KEY) headers['x-api-key'] = env.ADSB_LOL_API_KEY;
  const response = await fetch(FEED, {headers, signal: AbortSignal.timeout(20000)});
  if (!response.ok) throw new Error(`Upstream HTTP ${response.status}`);
  return normalize(await response.json());
}

function xmlText(block, tag) {
  return (block.match(new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, 'i'))?.[1] || '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code))).trim();
}

function normalizeFaa(xml) {
  if (!xml.includes('<AIRPORT_STATUS_INFORMATION>')) throw new Error('Invalid FAA status response');
  const updated = Date.parse(xmlText(xml, 'Update_Time'));
  const events = Object.fromEntries(AIRPORTS.map((code) => [code, []]));
  for (const group of xml.matchAll(/<Delay_type>([\s\S]*?)<\/Delay_type>/gi)) {
    const type = xmlText(group[1], 'Name');
    for (const event of group[1].matchAll(/<(Airport|Ground_Delay|Arrival_Delay|Departure_Delay|Ground_Stop)>([\s\S]*?)<\/\1>/gi)) {
      const code = xmlText(event[2], 'ARPT').toUpperCase();
      if (!events[code]) continue;
      events[code].push({type, reason: xmlText(event[2], 'Reason') || null,
        averageDelay: xmlText(event[2], 'Avg') || null, maximumDelay: xmlText(event[2], 'Max') || null});
    }
  }
  return {status:'available', updatedAt:Number.isFinite(updated) ? new Date(updated).toISOString() : null, events};
}

function normalizeWeather(rows) {
  if (!Array.isArray(rows)) throw new Error('Invalid aviation weather response');
  const weather = Object.fromEntries(AIRPORTS.map((code) => [code, null]));
  for (const row of rows) {
    const code = String(row.icaoId || '').replace(/^K/, '').toUpperCase();
    if (!(code in weather)) continue;
    const reported = Number(row.obsTime);
    weather[code] = {
      observedAt: Number.isFinite(reported) ? new Date(reported * 1000).toISOString() : null,
      category: row.fltCat || null,
      windKt: Number.isFinite(row.wspd) ? row.wspd : null,
      gustKt: Number.isFinite(row.wgst) ? row.wgst : null,
      visibilityMi: row.visib == null ? null : String(row.visib),
      temperatureC: Number.isFinite(row.temp) ? row.temp : null,
    };
  }
  return {status:'available', airports:weather};
}

async function getJson(url) {
  const response = await fetch(url, {headers:{accept:'application/json'}, signal:AbortSignal.timeout(15000)});
  if (!response.ok) throw new Error(`Source HTTP ${response.status}`);
  return response.json();
}

async function operationsUpstream() {
  const [faa, weather] = await Promise.allSettled([
    fetch(FAA_URL, {signal:AbortSignal.timeout(15000)}).then(async (response) => {
      if (!response.ok) throw new Error(`FAA HTTP ${response.status}`);
      return normalizeFaa(await response.text());
    }),
    getJson(WEATHER_URL).then(normalizeWeather),
  ]);
  if (faa.status === 'rejected' && weather.status === 'rejected') throw new Error('Airport sources unavailable');
  return {status:'live', source:'FAA NAS Status and NOAA Aviation Weather Center', fetchedAt:new Date().toISOString(),
    faa:faa.status === 'fulfilled' ? faa.value : {status:'unavailable', updatedAt:null, events:null},
    weather:weather.status === 'fulfilled' ? weather.value : {status:'unavailable', airports:null}};
}

function cleanSchedule(raw, airport) {
  if (!Array.isArray(raw?.response)) throw new Error('Invalid schedule response');
  const flights = raw.response.filter((item) => item.dep_iata === airport).slice(0, 50).map((item) => ({
    flight: String(item.flight_iata || '').trim() || null,
    origin: airport, destination: String(item.arr_iata || '').trim() || null,
    scheduledLocal: String(item.dep_time || '').trim() || null,
    status: String(item.status || '').trim() || null,
    gate: String(item.dep_gate || '').trim() || null,
    terminal: String(item.dep_terminal || '').trim() || null,
  })).filter((item) => item.flight && item.scheduledLocal);
  return {status:'live', source:'AirLabs', airport, fetchedAt:new Date().toISOString(),
    coverage:'Partial: free plan returns at most 50 flights per request; gate and status fields may be unavailable.',
    flights};
}

async function schedulesUpstream(env, airport) {
  const url = new URL('https://airlabs.co/api/v9/schedules');
  url.searchParams.set('dep_iata', airport);
  url.searchParams.set('limit', '50');
  url.searchParams.set('api_key', env.AIRLABS_API_KEY);
  const data = await getJson(url.toString());
  if (data.error) throw new Error('Schedule provider returned an error');
  return cleanSchedule(data, airport);
}

async function cached(env, key, refreshSeconds, maxStaleSeconds, load, origin) {
  if (!env.DB) return reply({status:'unavailable', error:'Shared cache unavailable'}, 503, origin);
  const now = Date.now();
  await env.DB.prepare('INSERT OR IGNORE INTO feed_cache (cache_key, fetched_at_ms, payload, lease_until_ms) VALUES (?, 0, ?, 0)')
    .bind(key, '{}').run();
  const read = () => env.DB.prepare('SELECT fetched_at_ms, payload, lease_until_ms FROM feed_cache WHERE cache_key = ?').bind(key).first();
  let row = await read();
  let previous = null;
  try { previous = JSON.parse(row.payload); } catch { /* No usable snapshot yet. */ }
  if (!previous?.fetchedAt) previous = null;
  const age = previous ? (now - row.fetched_at_ms) / 1000 : Infinity;
  if (age < refreshSeconds) return reply(previous, 200, origin);
  const lease = await env.DB.prepare('UPDATE feed_cache SET lease_until_ms = ? WHERE cache_key = ? AND lease_until_ms < ?')
    .bind(now + 30000, key, now).run();
  if (lease.meta.changes === 0) {
    if (previous && age < maxStaleSeconds) return reply({...previous, status:'stale'}, 200, origin);
    await new Promise((resolve) => setTimeout(resolve, 700));
    row = await read();
    try { previous = JSON.parse(row.payload); } catch { previous = null; }
    const refreshedAge = (Date.now() - row.fetched_at_ms) / 1000;
    if (previous?.fetchedAt && refreshedAge < maxStaleSeconds) {
      return reply(refreshedAge < refreshSeconds ? previous : {...previous, status:'stale'}, 200, origin);
    }
    return reply({status:'unavailable', error:'Feed is starting'}, 503, origin);
  }
  try {
    const data = await load();
    await env.DB.prepare('UPDATE feed_cache SET fetched_at_ms = ?, payload = ?, lease_until_ms = 0 WHERE cache_key = ?')
      .bind(Date.now(), JSON.stringify(data), key).run();
    return reply(data, 200, origin);
  } catch (error) {
    console.error('Feed refresh failed:', key, error?.message || String(error));
    await env.DB.prepare('UPDATE feed_cache SET lease_until_ms = ? WHERE cache_key = ?').bind(Date.now() + 60000, key).run();
    if (previous && age < maxStaleSeconds) return reply({...previous, status:'stale', error:'Feed temporarily unavailable'}, 200, origin);
    return reply({status:'unavailable', error:'Feed unavailable'}, 503, origin);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin');
    if (url.pathname === '/') {
      return new Response('SkyDelay live airspace API. GET /api/aircraft for current observations.', {
        headers: {'content-type': 'text/plain; charset=utf-8', ...cors(origin)},
      });
    }
    if (!['/api/aircraft', '/api/operations', '/api/schedules'].includes(url.pathname)) return reply({error:'Not found'}, 404, origin);
    if (origin && !ALLOWED_ORIGINS.has(origin)) return reply({error:'Origin not allowed'}, 403, null);
    if (request.method === 'OPTIONS') return new Response(null, {status:204, headers:cors(origin)});
    if (request.method !== 'GET') return reply({error:'Method not allowed'}, 405, origin);

    if (url.pathname === '/api/operations') return cached(env, 'nyc-operations', 300, 1800, operationsUpstream, origin);
    if (url.pathname === '/api/schedules') {
      const airport = String(url.searchParams.get('airport') || '').toUpperCase();
      if (!AIRPORTS.includes(airport)) return reply({error:'Choose JFK, EWR, or LGA'}, 400, origin);
      if (!env.AIRLABS_API_KEY) return reply({status:'not_configured', source:'AirLabs', airport, flights:[],
        error:'Schedule provider key has not been configured'}, 200, origin);
      return cached(env, `schedules-${airport}`, 14400, 21600, () => schedulesUpstream(env, airport), origin);
    }
    if (!env.DB) return reply({status:'unavailable', error:'Shared cache unavailable', observedAt:null, aircraft:[]}, 503, origin);
    const refreshSeconds = Math.max(30, Math.min(3600, Number(env.REFRESH_SECONDS) || DEFAULT_REFRESH_SECONDS));
    const now = Date.now();
    const key = 'nyc-aircraft';
    await env.DB.prepare('INSERT OR IGNORE INTO feed_cache (cache_key, fetched_at_ms, payload, lease_until_ms) VALUES (?, 0, ?, 0)')
      .bind(key, '{}').run();
    const read = () => env.DB.prepare('SELECT fetched_at_ms, payload, lease_until_ms FROM feed_cache WHERE cache_key = ?').bind(key).first();
    let row = await read();
    let previous = null;
    try { previous = JSON.parse(row.payload); } catch { /* No usable snapshot yet. */ }
    if (!Array.isArray(previous?.aircraft)) previous = null;
    const age = previous ? (now - row.fetched_at_ms) / 1000 : Infinity;
    if (age < refreshSeconds) return reply(previous, 200, origin);

    const lease = await env.DB.prepare('UPDATE feed_cache SET lease_until_ms = ? WHERE cache_key = ? AND lease_until_ms < ?')
      .bind(now + 30000, key, now).run();
    if (lease.meta.changes === 0) {
      if (previous && age < MAX_STALE_SECONDS) return reply({...previous, status:'stale'}, 200, origin);
      // A concurrent request is fetching the first snapshot. Give it a moment.
      await new Promise((resolve) => setTimeout(resolve, 700));
      row = await read();
      try { previous = JSON.parse(row.payload); } catch { previous = null; }
      const refreshedAge = (Date.now() - row.fetched_at_ms) / 1000;
      if (Array.isArray(previous?.aircraft) && refreshedAge < MAX_STALE_SECONDS) {
        return reply(refreshedAge < refreshSeconds ? previous : {...previous, status:'stale'}, 200, origin);
      }
      return reply({status:'unavailable', error:'Live aircraft feed is starting', observedAt:null, aircraft:[]}, 503, origin);
    }

    try {
      const data = await upstream(env);
      await env.DB.prepare('UPDATE feed_cache SET fetched_at_ms = ?, payload = ?, lease_until_ms = 0 WHERE cache_key = ?')
        .bind(Date.now(), JSON.stringify(data), key).run();
      return reply(data, 200, origin);
    } catch (error) {
      console.error('Live feed refresh failed:', error?.message || String(error));
      await env.DB.prepare('UPDATE feed_cache SET lease_until_ms = ? WHERE cache_key = ?').bind(Date.now() + 60000, key).run();
      if (previous && age < MAX_STALE_SECONDS) {
        return reply({...previous, status:'stale', error:'Live feed temporarily unavailable'}, 200, origin);
      }
      return reply({status:'unavailable', error:'Live aircraft feed unavailable', observedAt:null, aircraft:[]}, 503, origin);
    }
  },
};
