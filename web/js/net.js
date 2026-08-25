// Server discovery, SSE subscription, and queue flushing.
//
// The server may be at the pit while scouts are in the stands, well outside
// hotspot range, so being disconnected is the normal case and not an error.

import * as db from './db.js';

const LS_BASE = 'serverBase';
const CANDIDATES = ['http://scout.local:8080', 'http://192.168.137.1:8080'];

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
  const m = saved.match(/^https?:\/\/(\d+)\.(\d+)\.(\d+)\.\d+(?::(\d+))?/);
  const nets = [];
  if (m) nets.push(`${m[1]}.${m[2]}.${m[3]}`);
  for (const n of ['192.168.137', '192.168.1', '192.168.0', '10.0.0']) {
    if (!nets.includes(n)) nets.push(n);
  }
  const p = port || (m && m[4]) || '8080';

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
  if (res.status === 403) {
    const e = new Error('locked'); e.locked = true; throw e;
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
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
    const sent = new Set(items.map((i) => i.qid));
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
  if (!state.base || es) return;
  try {
    const q = new URLSearchParams(Object.entries(identity).filter(([, v]) => v));
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
    if (state.base && !es) connectStream();
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
