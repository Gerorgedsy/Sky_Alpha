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
  const denied = await worker.fetch(new Request(url, {headers:{Origin:'https://unknown.example'}}), {DB});
  assert.equal(denied.status, 403);
  database.prepare('UPDATE feed_cache SET fetched_at_ms = ?').run(Date.now() - 300000);
  globalThis.fetch = async () => { throw new Error('feed down'); };
  const stale = await (await worker.fetch(request(), {DB})).json();
  assert.equal(stale.status, 'stale');
  assert.equal(stale.aircraft.length, 1);
  database.prepare('UPDATE feed_cache SET fetched_at_ms = 0, lease_until_ms = 0').run();
  const unavailable = await worker.fetch(request(), {DB});
  assert.equal(unavailable.status, 503);
  const backoff = await worker.fetch(request(), {DB});
  assert.equal(backoff.status, 503);
  assert.equal(database.prepare('SELECT lease_until_ms FROM feed_cache').get().lease_until_ms > Date.now(), true);
  console.log('Worker cache, normalization, CORS, and unavailable state passed');
} finally {
  globalThis.fetch = originalFetch;
  database.close();
}
