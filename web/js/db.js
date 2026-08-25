// IndexedDB store + outbound write queue.
//
// Every scouting write lands here FIRST and is queued for the server. Scouts in
// the stands are routinely out of range of a laptop at the pit, so nothing is
// ever entered directly against the network. IndexedDB works over plain HTTP,
// so entered data is never at risk even though we can't run a service worker.

const DB_NAME = 'frc2026scouting';
const DB_VERSION = 1;

let _db = null;

export function deviceId() {
  let id = localStorage.getItem('deviceId');
  if (!id) {
    id = 'd-' + Math.random().toString(36).slice(2, 10);
    localStorage.setItem('deviceId', id);
  }
  return id;
}

export function open() {
  if (_db) return Promise.resolve(_db);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('scout')) {
        const s = db.createObjectStore('scout', { keyPath: 'id' });
        s.createIndex('match', 'matchKey');
        s.createIndex('team', 'team');
      }
      if (!db.objectStoreNames.contains('pit')) {
        db.createObjectStore('pit', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('queue')) {
        db.createObjectStore('queue', { keyPath: 'qid', autoIncrement: true });
      }
      if (!db.objectStoreNames.contains('cache')) {
        db.createObjectStore('cache', { keyPath: 'key' });
      }
    };
    req.onsuccess = () => { _db = req.result; resolve(_db); };
    req.onerror = () => reject(req.error);
  });
}

function tx(store, mode = 'readonly') {
  return open().then((db) => db.transaction(store, mode).objectStore(store));
}

function wrap(req) {
  return new Promise((res, rej) => {
    req.onsuccess = () => res(req.result);
    req.onerror = () => rej(req.error);
  });
}

export async function put(store, value) {
  const os = await tx(store, 'readwrite');
  return wrap(os.put(value));
}

export async function get(store, key) {
  const os = await tx(store);
  return wrap(os.get(key));
}

export async function all(store) {
  const os = await tx(store);
  return wrap(os.getAll());
}

export async function del(store, key) {
  const os = await tx(store, 'readwrite');
  return wrap(os.delete(key));
}

// ------------------------------------------------------------ cached state
export async function cacheSet(key, value) {
  return put('cache', { key, value, at: Date.now() });
}

export async function cacheGet(key) {
  const r = await get('cache', key);
  return r ? r.value : null;
}

// ------------------------------------------------------ scouting records
export function scoutId(matchKey, team, scout) {
  return `${matchKey}|${team}|${scout}`;
}

/** Save a scouting record locally and enqueue it for the server. */
export async function saveScout(rec) {
  const full = {
    ...rec,
    id: scoutId(rec.matchKey, rec.team, rec.scoutId),
    deviceId: deviceId(),
    updatedAt: Date.now() / 1000,
  };
  await put('scout', full);
  await enqueue('scout', full);
  return full;
}

export async function savePit(rec) {
  const full = {
    ...rec,
    id: `${rec.eventKey}|${rec.team}`,
    deviceId: deviceId(),
    updatedAt: Date.now() / 1000,
  };
  await put('pit', full);
  await enqueue('pit', full);
  return full;
}

// --------------------------------------------------------------- the queue
async function enqueue(kind, record) {
  const os = await tx('queue', 'readwrite');
  return wrap(os.add({ kind, record, at: Date.now() }));
}

export async function queued() {
  return all('queue');
}

export async function queueCount() {
  const os = await tx('queue');
  return wrap(os.count());
}

export async function dropQueued(qids) {
  const os = await tx('queue', 'readwrite');
  await Promise.all(qids.map((q) => wrap(os.delete(q))));
}

/**
 * Collapse the queue to the newest record per (kind,id) before sending.
 * A scout who edits one match ten times should cost one row, not ten.
 */
export function collapse(items) {
  const best = new Map();
  for (const it of items) {
    const k = `${it.kind}|${it.record.id}`;
    const prev = best.get(k);
    if (!prev || it.record.updatedAt >= prev.record.updatedAt) best.set(k, it);
  }
  return [...best.values()];
}

export async function localScoutFor(matchKey, team, scout) {
  return get('scout', scoutId(matchKey, team, scout));
}
