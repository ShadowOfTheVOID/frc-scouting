// Server discovery, SSE subscription, and queue flushing.
//
// The server may be at the pit while scouts are in the stands, well out of
// wifi range, so being disconnected is the normal case and not an error.
// On a venue network that isolates clients it is the case all day, and the
// queue below is the only path the data takes.

import * as db from './db.js';

const LS_BASE = 'serverBase';
// Our team number, so the hub is not sitting on a port some other tool on
// the laptop already wanted.
const PORT = '6059';
const CANDIDATES = [`http://scout.local:${PORT}`, `http://192.168.137.1:${PORT}`];

export const state = {
  base: null,
  online: false,
  queued: 0,
  lastSync: null,
  lastEvent: null,
  skew: 0,          // serverNow - phoneNow, seconds
};

/** Server time in seconds, corrected for this phone's clock skew. */
export function serverNow() {
  return Date.now() / 1000 + state.skew;
}

const listeners = new Set();
export function onChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function emit() { for (const fn of listeners) { try { fn(state); } catch (e) { console.error(e); } } }

function sameOrigin() {
  return location.protocol.startsWith('http') ? location.origin : null;
}

/** Candidate server URLs, best first: where we're served from, last good, then guesses. */
function candidates() {
  const out = [];
  const here = sameOrigin();
  if (here) out.push(here);
  const saved = localStorage.getItem(LS_BASE);
  if (saved && !out.includes(saved)) out.push(saved);
  for (const c of CANDIDATES) if (!out.includes(c)) out.push(c);
  return out;
}

async function reachable(base, ms = 2500) {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), ms);
  const t0 = Date.now();
  try {
    const r = await fetch(base + '/api/config', { signal: ctl.signal, cache: 'no-store' });
    if (!r.ok) return null;
    const cfg = await r.json();
    if (cfg && cfg.serverTime) {
      // phones' clocks drift apart; correct so every device shares one timeline
      const rtt = (Date.now() - t0) / 1000;
      state.skew = cfg.serverTime + rtt / 2 - Date.now() / 1000;
    }
    return cfg;
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

/**
 * Sweep the subnet we were last seen on.
 *
 * A native app would UDP-broadcast "where is the hub?"; a browser cannot send
 * UDP at all. The browser equivalent is to try every host on the /24 at once
 * with a short timeout — 254 requests, but they are parallel, tiny, and only
 * happen when every known address has already failed. This is what lets a
 * phone find the hub again after the laptop's DHCP lease moves it.
 */
async function sweepSubnet(port) {
  const saved = localStorage.getItem(LS_BASE) || '';
  let u = null;
  try { u = new URL(saved); } catch { /* nothing saved yet, or not a URL */ }
  // The /24 can only come from an address that IS one. The port can come from
  // any of them, and used to be read out of the same IP-shaped match: a phone
  // whose last good address was `http://scout.local:7000` swept for 6059 and
  // could never find a hub the lead had started on --port 7000, which is the
  // one case the port is remembered for.
  const m = u && u.hostname.match(/^(\d+)\.(\d+)\.(\d+)\.\d+$/);
  const nets = [];
  if (m) nets.push(`${m[1]}.${m[2]}.${m[3]}`);
  for (const n of ['192.168.137', '192.168.1', '192.168.0', '10.0.0']) {
    if (!nets.includes(n)) nets.push(n);
  }
  // The port we last reached a hub on wins: a lead who runs --port keeps
  // working. Only a phone that has never seen one falls back to the default.
  const p = port || (u && u.port) || PORT;

  for (const net of nets.slice(0, 2)) {      // two subnets is already 508 probes
    const tries = [];
    for (let i = 1; i < 255; i++) tries.push(`http://${net}.${i}:${p}`);
    const hit = await Promise.any(tries.map(async (base) => {
      const cfg = await reachable(base, 1200);
      if (!cfg) throw new Error('no');
      return { base, cfg };
    })).catch(() => null);
    if (hit) return hit;
  }
  return null;
}

export async function discover({ sweep = false } = {}) {
  for (const base of candidates()) {
    const cfg = await reachable(base);
    if (cfg) {
      state.base = base;
      state.online = true;
      localStorage.setItem(LS_BASE, base);
      emit();
      return cfg;
    }
  }
  // Everything we knew about is gone - the hub has probably moved. Go looking.
  if (sweep) {
    const hit = await sweepSubnet();
    if (hit) {
      state.base = hit.base;
      state.online = true;
      localStorage.setItem(LS_BASE, hit.base);
      emit();
      return hit.cfg;
    }
  }
  state.online = false;
  emit();
  return null;
}

export async function api(path, opts = {}) {
  if (!state.base) {
    const ok = await discover();
    if (!ok) throw new Error('offline');
  }
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  const tok = localStorage.getItem('strategyToken');
  if (tok) headers['X-Strategy-Token'] = tok;
  const res = await fetch(state.base + path, { cache: 'no-store', ...opts, headers });
  if (!res.ok) {
    // The hub says why in the body - which box was wrong, which key it looked
    // like instead, whether the settings are locked. Throwing the status alone
    // threw all of that away, and the Setup page had nothing to show but
    // "HTTP 400".
    const body = await res.json().catch(() => null);
    const e = new Error((body && body.error) || (res.status === 403 ? 'locked' : `HTTP ${res.status}`));
    e.status = res.status;
    e.body = body;
    // `locked` is what every caller of a passcode-gated route already reads.
    if (res.status === 403) e.locked = true;
    throw e;
  }
  return res.json();
}

// ------------------------------------------------------------------ queue
let flushing = false;

export async function flush() {
  state.queued = await db.queueCount();
  emit();
  if (flushing || !state.queued) return;
  flushing = true;
  try {
    const items = db.collapse(await db.queued());
    if (!items.length) return;
    const payload = { scout: [], pit: [] };
    for (const it of items) payload[it.kind].push(it.record);

    await api('/api/sync', { method: 'POST', body: JSON.stringify({ ...payload, who: identity }) });

    // Only drop what we actually sent; anything queued mid-flight survives.
    // Every row a collapsed item stands for goes, not just the newest of them.
    const sent = new Set(items.flatMap((i) => i.qids || [i.qid]));
    await db.dropQueued([...sent]);
    state.lastSync = Date.now();
    state.online = true;
  } catch (e) {
    state.online = false;   // stay queued, try again on the next tick
  } finally {
    flushing = false;
    state.queued = await db.queueCount();
    emit();
  }
}

// -------------------------------------------------------------------- SSE
let es = null;
let esBase = null;      // the address that stream is actually open against
const streamHandlers = new Map();

export function on(type, fn) {
  if (!streamHandlers.has(type)) streamHandlers.set(type, new Set());
  streamHandlers.get(type).add(fn);
  return () => streamHandlers.get(type).delete(fn);
}

/** Who this device is, for the lead's crew board. Set by the scouting app. */
export let identity = {};
export function setIdentity(who) {
  identity = { ...identity, ...who };
  if (es) { try { es.close(); } catch {} es = null; connectStream(); }
}

export function connectStream() {
  if (!state.base) return;
  // A stream already open against the address we now believe in is the one we
  // want; anything else has to be replaced. Bailing out on `es` alone left the
  // phone streaming from the address it had just concluded was dead: a moved
  // hub was rediscovered, records synced to it fine, and the stream sat on the
  // old URL retrying forever. EventSource keeps a dead connection in
  // CONNECTING rather than CLOSED, so nothing ever cleared it. The scout saw a
  // working phone that had stopped hearing seat claims, match starts and the
  // shared clock - and the shared clock is what the solver's accuracy rests on.
  if (es && esBase === state.base) return;
  if (es) { try { es.close(); } catch {} es = null; }
  try {
    const q = new URLSearchParams(Object.entries(identity).filter(([, v]) => v));
    esBase = state.base;
    es = new EventSource(state.base + '/api/stream' + (q.toString() ? '?' + q : ''));
    es.onopen = () => { state.online = true; emit(); };
    es.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      state.lastEvent = Date.now();
      state.online = true;
      emit();
      for (const fn of streamHandlers.get(msg.type) || []) {
        try { fn(msg.data); } catch (e) { console.error(e); }
      }
      for (const fn of streamHandlers.get('*') || []) {
        try { fn(msg); } catch (e) { console.error(e); }
      }
    };
    es.onerror = () => {
      state.online = false;
      emit();
      // EventSource reconnects on its own; drop ours only if it fully closed.
      if (es && es.readyState === EventSource.CLOSED) { es = null; }
    };
  } catch {
    es = null;
  }
}

/** Start discovery, streaming, and a periodic flush. Safe to call once per page. */
export async function start({ flushMs = 8000, rediscoverMs = 20000 } = {}) {
  const cfg = await discover();
  if (cfg) connectStream();
  await flush();

  let misses = 0;
  setInterval(async () => {
    if (!state.online || !state.base) {
      misses += 1;
      // three quiet ticks in a row means the address we know is dead, not busy
      const cfg = await discover({ sweep: misses >= 3 });
      if (cfg) misses = 0;
    } else misses = 0;
    connectStream();   // self-guards; re-points itself if the hub has moved
    await flush();
  }, flushMs);

  // If we've heard nothing at all for a while, the server may have moved.
  setInterval(() => {
    if (state.lastEvent && Date.now() - state.lastEvent > rediscoverMs * 3) discover();
  }, rediscoverMs);

  window.addEventListener('online', () => { discover({ sweep: true }).then(flush); });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) { discover().then(flush); }
  });
  return cfg;
}

/** Render the persistent connection chip. */
export function mountChip(el) {
  const paint = () => {
    const q = state.queued;
    let cls = 'chip ok', txt = 'connected';
    if (!state.online) { cls = 'chip bad'; txt = q ? `offline · ${q} queued` : 'offline'; }
    else if (q) { cls = 'chip warn'; txt = `syncing ${q}`; }
    el.className = cls;
    el.innerHTML = `<span class="dot"></span><span>${txt}</span>`;
  };
  onChange(paint);
  paint();
}
