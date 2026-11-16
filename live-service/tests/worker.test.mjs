import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DatabaseSync} from 'node:sqlite';
import worker from '../worker/index.js';

const database = new DatabaseSync(':memory:');
database.exec(readFileSync(new URL('../drizzle/0000_feed_cache.sql', import.meta.url), 'utf8'));
const DB = {
  prepare(sql) {
    const statement = database.prepare(sql);
    return {
      bind(...values) {
        return {
          async run() { return {meta: statement.run(...values)}; },
          async first() { return statement.get(...values) || null; },
        };
      },
    };
  },
};
const originalFetch = globalThis.fetch;
let upstreamCalls = 0;
globalThis.fetch = async () => {
  upstreamCalls += 1;
  return new Response(JSON.stringify({now:Date.now(),ac:[{
    hex:'a1b2c3',flight:'DAL123 ',r:'N123AA',lat:40.7,lon:-73.9,
    alt_baro:10000,gs:280,track:90,seen_pos:1,t:'A320',type:'adsb_icao',
  }]}), {status:200});
};
try {
  const url = 'https://skyalpha-live-airspace.headybuttercup5.chatgpt.site/api/aircraft';
  const request = () => new Request(url, {headers:{Origin:'https://gerorgedsy.github.io'}});
  const first = await worker.fetch(request(), {DB});
  const firstBody = await first.json();
  assert.equal(first.status, 200);
  assert.equal(first.headers.get('access-control-allow-origin'), 'https://gerorgedsy.github.io');
  assert.equal(firstBody.aircraft[0].callsign, 'DAL123');
  assert.equal(firstBody.aircraft[0].altitudeFt, 10000);
  const secondBody = await (await worker.fetch(request(), {DB})).json();
  assert.equal(secondBody.fetchedAt, firstBody.fetchedAt);
  assert.equal(upstreamCalls, 1, 'shared cache avoids repeated source requests');
  globalThis.fetch = async (input) => {
    const target = String(input);
    if (target.includes('airport-status-information')) return new Response('<AIRPORT_STATUS_INFORMATION><Update_Time>Mon Nov 16 18:02:30 2026 GMT</Update_Time><Delay_type><Name>Ground Delay Programs</Name><Ground_Delay_List><Ground_Delay><ARPT>JFK</ARPT><Reason>low ceilings</Reason><Avg>36 minutes</Avg></Ground_Delay></Ground_Delay_List></Delay_type></AIRPORT_STATUS_INFORMATION>');
    if (target.includes('/metar?')) return new Response(JSON.stringify([{icaoId:'KJFK',obsTime:1794851460,fltCat:'IFR',wspd:12,wgst:20,visib:'2',temp:9}]));
    if (target.includes('/schedules?')) return new Response(JSON.stringify({response:[{flight_iata:'AA100',dep_iata:'JFK',arr_iata:'LAX',dep_time:'2026-11-16 19:00',dep_gate:'B36',status:'scheduled'}]}));
    throw new Error(`Unexpected source: ${target}`);
  };
  const operationsRequest = () => new Request(url.replace('aircraft', 'operations'), {headers:{Origin:'https://gerorgedsy.github.io'}});
  const operations = await (await worker.fetch(operationsRequest(), {DB})).json();
  assert.equal(operations.faa.events.JFK[0].averageDelay, '36 minutes');
  assert.equal(operations.weather.airports.JFK.category, 'IFR');
  assert.equal(operations.faa.events.EWR.length, 0);
  const unconfiguredSchedules = await (await worker.fetch(new Request(url.replace('aircraft', 'schedules?airport=JFK')), {DB})).json();
  assert.equal(unconfiguredSchedules.status, 'not_configured');
  const schedules = await (await worker.fetch(new Request(url.replace('aircraft', 'schedules?airport=JFK')), {DB, AIRLABS_API_KEY:'test-key'})).json();
  assert.equal(schedules.flights[0].flight, 'AA100');
  assert.equal(schedules.flights[0].gate, 'B36');
  assert.equal((await worker.fetch(new Request(url.replace('aircraft', 'schedules?airport=XXX')), {DB})).status, 400);
  const denied = await worker.fetch(new Request(url, {headers:{Origin:'https://unknown.example'}}), {DB});
  assert.equal(denied.status, 403);
  database.prepare('UPDATE feed_cache SET fetched_at_ms = ? WHERE cache_key = ?').run(Date.now() - 300000, 'nyc-aircraft');
  globalThis.fetch = async () => { throw new Error('feed down'); };
  const stale = await (await worker.fetch(request(), {DB})).json();
  assert.equal(stale.status, 'stale');
  assert.equal(stale.aircraft.length, 1);
  database.prepare('UPDATE feed_cache SET fetched_at_ms = 0, lease_until_ms = 0 WHERE cache_key = ?').run('nyc-aircraft');
  const unavailable = await worker.fetch(request(), {DB});
  assert.equal(unavailable.status, 503);
  const backoff = await worker.fetch(request(), {DB});
  assert.equal(backoff.status, 503);
  assert.equal(database.prepare('SELECT lease_until_ms FROM feed_cache WHERE cache_key = ?').get('nyc-aircraft').lease_until_ms > Date.now(), true);
  console.log('Worker aircraft, operations, schedules, cache, CORS, and unavailable states passed');
} finally {
  globalThis.fetch = originalFetch;
  database.close();
}
