// Pit scouting, on the mirror.
//
// Students do pit scouting here, on their own phones over their own data,
// rather than on the venue wifi: the hub collects what is saved here once a
// minute. The same map, list and form as the hub's old pit tablet page - the
// venue's own floor plan from Nexus, the built-in questions, the team's own
// questions, and photos - plus the marker that stops two scouts walking to the
// same pit. And the editor for those questions, which lives here too.
//
// Everything that is not the mirror page's own plumbing is in this file;
// mirror.js hands over the few helpers it needs.

let ctx = null;            // { api, state, esc, signed, render }
const $ = (s, r) => (r || document).querySelector(s);
const $$ = (s, r) => [...(r || document).querySelectorAll(s)];

// ------------------------------------------------------------------ setup

/** Called once by mirror.js with its helpers. */
export function initPit(helpers) {
  ctx = helpers;
  // Poll while the pit tab is on screen - every fifteen seconds, so a robot
  // somebody opens shows on every other phone well inside the minute.
  setInterval(() => { if (ctx.state.tab === 'pit' && !document.hidden) pollLive(); }, 15000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && ctx.state.tab === 'pit') pollLive();
  });
  window.addEventListener('pagehide', () => { if (open.team) letGo(open.team); });
}

const esc = (s) => ctx.esc(s);
const ek = () => ctx.state.eventKey;
const bundle = () => ctx.state.bundle || {};

// The built-in questions. Four of the first five answered is "done".
const FIELDS = ['drivetrain', 'shooter', 'maxClimb', 'stockpile', 'groundPickup'];
const BUILT_IN = [
  { f: 'drivetrain', label: 'Drivetrain', opts: ['swerve', 'tank', 'mecanum', 'other'] },
  { f: 'shooter', label: 'How do they shoot?', opts: ['drum', 'flywheel', 'dump', 'none'] },
  { f: 'maxClimb', label: 'Highest tower level they claim', opts: ['L3', 'L2', 'L1', 'none'] },
  { f: 'stockpile', label: 'Can they hold fuel through an inactive shift?', opts: ['yes', 'some', 'no'],
    hint: 'The defining REBUILT skill — nothing public measures it.' },
  { f: 'groundPickup', label: 'Ground pickup?', opts: ['yes', 'no'] },
];

/** This phone, for claims. Not a person - initials are asked for separately. */
function deviceId() {
  try {
    let d = localStorage.getItem('mirrorDevice');
    if (!d) { d = Math.random().toString(36).slice(2, 12); localStorage.setItem('mirrorDevice', d); }
    return d;
  } catch { return 'phone'; }
}
function initials() {
  try { return localStorage.getItem('mirrorScout') || ''; } catch { return ''; }
}
function askInitials() {
  const v = prompt('Your initials, so other scouts can see who is on which robot', initials());
  if (v === null) return initials();
  const name = v.trim().toUpperCase().slice(0, 4);
  try { localStorage.setItem('mirrorScout', name); } catch { /* private mode */ }
  return name;
}

// ------------------------------------------------------------------- live

const live = { status: {}, claims: {}, at: 0, serverSkew: 0, mode: 'map' };
const CLAIM_SECONDS = 180;
const nowServer = () => Date.now() / 1000 + live.serverSkew;

async function pollLive() {
  // Not while the lock screen is up: a 401 there reads as "your session
  // expired" to somebody who has not signed in yet.
  if (!ctx.state.bundle || document.getElementById('app').classList.contains('hide')) return;
  try {
    const r = await ctx.api(`/api/pit/live?event=${encodeURIComponent(ek())}`);
    live.status = r.status || {};
    live.claims = r.claims || {};
    live.serverSkew = (r.serverTime || 0) - Date.now() / 1000;
    live.at = Date.now();
    paintPits();
  } catch { /* the next poll tries again; the page keeps what it had */ }
}

function status(team) {
  const s = live.status[String(team)];
  return s ? s.done : '';
}

/** The first other phone with this robot open, or null. Ours never counts. */
function otherClaim(team) {
  const holders = live.claims[String(team)] || {};
  for (const [device, c] of Object.entries(holders)) {
    if (device !== deviceId() && c && nowServer() - (c.at || 0) < CLAIM_SECONDS) return c;
  }
  return null;
}
const claimName = (c) => (c.scoutId && c.scoutId !== 'pit' ? c.scoutId : 'Another scout');

// ------------------------------------------------------------------ view

/** The Pit scouting tab. */
export function viewPit() {
  $('#view').innerHTML = `
    <div class="pv">
      <div class="pv-top">
        <div class="pv-toggle" role="group" aria-label="Map or list">
          <button data-m="map" class="${live.mode === 'map' ? 'on' : ''}">Map</button>
          <button data-m="list" class="${live.mode === 'list' ? 'on' : ''}">List</button>
        </div>
        <button class="pv-who" id="pvWho">${initials() ? `Scouting as <b>${esc(initials())}</b>` : 'Set your initials'}</button>
      </div>
      <div class="pv-prog"><div class="pv-lbl"><span id="pvLbl">—</span><span id="pvPct"></span></div>
        <div class="pv-bar"><i id="pvBar" style="width:0"></i></div></div>
      <div class="pv-map ${live.mode === 'map' ? '' : 'hide'}" id="pvMap"></div>
      <div class="pv-list ${live.mode === 'list' ? '' : 'hide'}" id="pvList"></div>
      <div class="hint">Saves go to the hub within a minute. A robot in blue is open on someone
        else's phone right now.</div>
    </div>`;
  for (const btn of $$('.pv-toggle button')) {
    btn.onclick = () => { live.mode = btn.dataset.m; viewPit(); };
  }
  $('#pvWho').onclick = () => { askInitials(); viewPit(); };
  paintPits();
  if (Date.now() - live.at > 10000) pollLive();
}

function paintPits() {
  if (ctx.state.tab !== 'pit' || !$('#pvMap')) return;
  renderMap();
  renderList();
  const teams = (bundle().teams || []).map((t) => t.team);
  const done = teams.filter((t) => status(t) === 'done').length;
  const pct = teams.length ? Math.round((done / teams.length) * 100) : 0;
  $('#pvLbl').textContent = teams.length ? `${done} of ${teams.length} pits done` : 'No teams yet';
  $('#pvPct').textContent = teams.length ? `${pct}%` : '';
  $('#pvBar').style.width = `${pct}%`;
}

function renderMap() {
  const map = bundle().pitMap;
  const host = $('#pvMap');
  if (!map || !map.pits) {
    host.innerHTML = `<div class="pv-empty">No pit map for this event — Nexus only has one where
      the event uses it for queuing. The list works either way.</div>`;
    return;
  }
  const size = map.size || { x: 1000, y: 1000 };
  const parts = [];
  // Nexus gives `position` as the centre of each element, not its top-left.
  const box = (o) => {
    const p = o.position || {}, s = o.size || {};
    const w = s.x || 0, h = s.y || 0;
    return { x: (p.x || 0) - w / 2, y: (p.y || 0) - h / 2, w, h, cx: p.x || 0, cy: p.y || 0 };
  };
  const rot = (o, b) => `rotate(${o.angle || 0} ${b.cx} ${b.cy})`;
  for (const a of Object.values(map.areas || {})) {
    const b = box(a);
    parts.push(`<g class="area" transform="${rot(a, b)}"><rect x="${b.x}" y="${b.y}" width="${b.w}"
      height="${b.h}" rx="4"></rect><text x="${b.cx}" y="${b.cy}" text-anchor="middle"
      dominant-baseline="middle">${esc(String(a.label || ''))}</text></g>`);
  }
  for (const w of Object.values(map.walls || {})) {
    const b = box(w);
    parts.push(`<rect class="wall" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" transform="${rot(w, b)}"></rect>`);
  }
  for (const a of Object.values(map.arrows || {})) {
    const b = box(a);
    const head = Math.min(b.h * 0.42, b.w);
    const shaft = `M${b.cx - b.w * 0.16} ${b.y} h${b.w * 0.32} V${b.y + b.h - head} h${b.w * 0.18}
                   L${b.cx} ${b.y + b.h} L${b.x} ${b.y + b.h - head} h${b.w * 0.18} Z`;
    parts.push(`<path class="arrow" d="${shaft}" transform="${rot(a, b)}"></path>`);
    if (a.type === 'double') {
      parts.push(`<path class="arrow" d="${shaft}" transform="rotate(${(a.angle || 0) + 180} ${b.cx} ${b.cy})"></path>`);
    }
  }
  for (const l of Object.values(map.labels || {})) {
    const b = box(l);
    parts.push(`<text class="area" x="${b.cx}" y="${b.cy}" text-anchor="middle"
      dominant-baseline="middle" transform="${rot(l, b)}">${esc(l.label || '')}</text>`);
  }
  const ours = parseInt(bundle().ourTeam, 10) || null;
  for (const [id, pit] of Object.entries(map.pits || {})) {
    const b = box(pit);
    if (!pit.team) {
      parts.push(`<rect class="emptypit" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="5" transform="${rot(pit, b)}"></rect>`);
      continue;
    }
    const team = Number(pit.team);
    const st = status(team);
    const busy = st !== 'done' && otherClaim(team);
    const cls = ['pit', st, busy ? 'busy' : '', team === ours ? 'ours' : ''].filter(Boolean).join(' ');
    parts.push(`<g class="${cls}" data-team="${team}" transform="${rot(pit, b)}">
      <rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="5"></rect>
      <text x="${b.cx}" y="${b.cy - 3}" text-anchor="middle" dominant-baseline="middle">${team}</text>
      <text class="addr" x="${b.cx}" y="${b.cy + 13}" text-anchor="middle" dominant-baseline="middle">${
        busy ? esc(busy.scoutId && busy.scoutId !== 'pit' ? busy.scoutId : 'in use') : esc(id)}</text></g>`);
  }
  host.innerHTML = `<svg class="pitmap" viewBox="0 0 ${size.x} ${size.y}" preserveAspectRatio="xMidYMid meet"
    style="aspect-ratio:${size.x} / ${size.y}">${parts.join('')}</svg>`;
  for (const g of $$('#pvMap .pit')) g.onclick = () => openPit(Number(g.dataset.team));
}

function renderList() {
  const b = bundle();
  const pits = b.pits || {};
  const teams = b.teams || [];
  const rows = teams.map((t) => t.team);
  // unscouted first, then by pit address, so the list reads as a walking order
  rows.sort((x, y) => {
    const dx = status(x) === 'done' ? 1 : 0, dy = status(y) === 'done' ? 1 : 0;
    if (dx !== dy) return dx - dy;
    return String(pits[x] || '~').localeCompare(String(pits[y] || '~'), undefined, { numeric: true });
  });
  $('#pvList').innerHTML = rows.map((t) => {
    const st = status(t);
    const name = (teams.find((x) => x.team === t) || {}).name || '';
    const c = st !== 'done' && otherClaim(t);
    return `<div class="pv-row ${c ? 'busy' : st}" data-team="${t}"><b>${t}</b>
      <span class="nm">${esc(name)}${pits[t] ? ' · pit ' + esc(pits[t]) : ''}</span>
      <span class="st">${c ? esc(claimName(c)) + ' is on it'
        : st === 'done' ? 'Done' : st === 'partial' ? 'Started' : 'To do'}</span></div>`;
  }).join('') || '<div class="pv-empty">No teams yet.</div>';
  for (const r of $$('#pvList .pv-row')) r.onclick = () => openPit(Number(r.dataset.team));
}

// ------------------------------------------------------------------- form

const open = { team: null, draft: null, renew: null, saving: false };

// What a scout has entered but not yet saved, kept on the phone. No signal in
// a pit aisle is normal, and a phone browser throws a tab away while the camera
// is open: either way the answers are still here when the robot is opened
// again, and only a save that reached the mirror clears them.
const draftKey = (team) => `pitDraft:${ek()}:${team}`;
function keepDraft() {
  if (!open.team || !open.draft) return;
  readInputs();
  const key = draftKey(open.team);
  // Only a change is worth keeping: a robot opened, looked at and closed again
  // must not come back claiming there are unsaved answers.
  if (canon(open.draft) === open.base) { dropDraft(open.team); return; }
  // Kept with the save it started from, so a newer save by somebody else is
  // noticed when the robot is opened again instead of silently overwritten.
  const keep = (payload) => localStorage.setItem(key, JSON.stringify({ v: 2, payload, baseAt: open.baseAt }));
  try { keep(open.draft); } catch {
    // New photos can be more than the phone will keep; the answers are worth
    // more. Photos already on the mirror are only ids and cost nothing.
    try { keep({ ...open.draft, photos: open.draft.photos.filter((x) => !String(x).startsWith('data:')) }); }
    catch { /* full */ }
  }
}
/** A pit payload as text that only differs when an answer does: keys in one
 *  order, empty answers left out, typed boxes compared as the text they show. */
function canon(p) {
  const norm = (v) => {
    if (Array.isArray(v)) return v.map(norm);
    if (v && typeof v === 'object') {
      const o = {};
      for (const k of Object.keys(v).sort()) {
        const x = norm(v[k]);
        const empty = x === '' || x == null || (typeof x === 'object' && !Object.keys(x).length);
        if (!empty) o[k] = x;
      }
      return o;
    }
    return typeof v === 'number' ? String(v) : v;
  };
  return JSON.stringify(norm(p || {}));
}

/** {payload, baseAt} kept on this phone for a robot, or null. */
function savedDraft(team) {
  let d = null;
  try { d = JSON.parse(localStorage.getItem(draftKey(team)) || 'null'); } catch { return null; }
  if (!d || typeof d !== 'object') return null;
  return d.v === 2 ? { payload: d.payload || {}, baseAt: d.baseAt ?? null } : { payload: d, baseAt: null };
}
// The questions as last loaded, so a robot with a draft opens with no signal.
function cacheQuestions(qs) { try { localStorage.setItem('pitQuestionsCache', JSON.stringify(qs)); } catch { /* full */ } }
function cachedQuestions() {
  try { return JSON.parse(localStorage.getItem('pitQuestionsCache') || '[]') || []; } catch { return []; }
}
function dropDraft(team) { try { localStorage.removeItem(draftKey(team)); } catch { /* private mode */ } }

function claim(team, release = false) {
  return ctx.api('/api/pitclaim', { method: 'POST', body: JSON.stringify({
    eventKey: ek(), team, release, deviceId: deviceId(), scoutId: initials() || 'pit',
  }) }).then((r) => { if (r && r.claims) { live.claims = r.claims; paintPits(); } })
    .catch(() => { /* no signal: the form still works, it just is not announced */ });
}
function letGo(team) {
  clearInterval(open.renew);
  open.renew = null;
  if (team) claim(team, true);
}

async function openPit(team) {
  if (!initials()) askInitials();
  clearTimeout(open.closeTimer);
  open.team = team;
  open.saving = false;
  const b = bundle();
  const t = (b.teams || []).find((x) => x.team === team) || {};
  const sheet = $('#psheet');
  sheet.classList.remove('hide');
  sheet.innerHTML = `<div class="pv-sheet">
    <div class="pv-shead">
      <span class="no">${team}</span>
      <span class="mt"><div>${esc(t.name || '')}</div>
        <div class="sub2"><span>${(b.pits || {})[team] ? 'pit ' + esc(b.pits[team]) : 'pit address unknown'}</span>
        ${inspection(team)}</div></span>
      <button class="pv-close" id="pvClose">Done</button>
    </div>
    <div class="pv-sbody" id="pvBody"><div class="pv-empty">Loading…</div></div>
    <div class="pv-savebar"><button class="pv-save" id="pvSave" disabled>Save pit</button></div>
  </div>`;
  $('#pvClose').onclick = closePit;
  // Claim it now, and keep it claimed once a minute while the form is open.
  claim(team);
  clearInterval(open.renew);
  open.renew = setInterval(() => claim(team), 60000);

  let rec = null, questions = [], offline = false;
  const kept = savedDraft(team);
  try {
    const [e, q] = await Promise.all([
      ctx.api(`/api/pit/entry?event=${encodeURIComponent(ek())}&team=${team}`),
      ctx.api('/api/pitquestions'),
    ]);
    rec = e.record;
    questions = q.questions || [];
    cacheQuestions(questions);
  } catch {
    if (open.team !== team) return;
    // Answers on this phone open without signal: they are the scout's own,
    // and saving them waits for a bar of signal anyway.
    if (!kept) {
      $('#pvBody').innerHTML = '<div class="pv-empty">Could not load this robot. Check your signal and try again.</div>';
      return;
    }
    offline = true;
    questions = cachedQuestions();
  }
  if (open.team !== team) return;     // closed, or another robot opened, while loading
  // What "unchanged" means for this robot: the record as the mirror has it.
  open.base = offline ? null : canon((rec && rec.payload) || {});
  open.restored = !!kept && canon(kept.payload) !== open.base;
  open.draft = JSON.parse(JSON.stringify(open.restored ? kept.payload : (rec && rec.payload) || {}));
  if (kept && !open.restored) dropDraft(team);
  open.baseAt = open.restored ? kept.baseAt : (rec && rec.updatedAt) || null;
  // Somebody saved this robot after these answers were started here.
  open.newer = open.restored && rec && rec.updatedAt && (!kept.baseAt || rec.updatedAt > kept.baseAt)
    ? rec : null;
  open.offline = offline;
  if (!Array.isArray(open.draft.photos)) open.draft.photos = [];
  if (!open.draft.custom || typeof open.draft.custom !== 'object' || Array.isArray(open.draft.custom)) open.draft.custom = {};
  drawForm(team, questions, !!rec || offline);
  const theirs = $('#pvTheirs');
  if (theirs) {
    theirs.onclick = () => {
      dropDraft(team);
      open.draft = JSON.parse(JSON.stringify(rec.payload || {}));
      if (!Array.isArray(open.draft.photos)) open.draft.photos = [];
      if (!open.draft.custom || typeof open.draft.custom !== 'object') open.draft.custom = {};
      open.baseAt = rec.updatedAt;
      open.restored = false;
      open.newer = null;
      drawForm(team, questions, true);
    };
  }
}

function inspection(team) {
  const insp = bundle().inspection || {};
  const st = String(insp[team] || insp[String(team)] || '').toLowerCase();
  if (!st) return '';
  const cls = st === 'complete' ? 'complete' : st === 'hold' ? 'hold' : 'other';
  return `<span class="pv-insp ${cls}">${esc(st.charAt(0).toUpperCase() + st.slice(1).replace(/-/g, ' '))}</span>`;
}

function chips(field, opts, get) {
  return `<div class="pv-chips" data-f="${esc(field)}">${opts.map((o) =>
    `<button class="pv-chip${get() === o ? ' on' : ''}" data-v="${esc(o)}">${esc(o)}</button>`).join('')}</div>`;
}

function drawForm(team, questions, existed) {
  const d = open.draft;
  const c = otherClaim(team);
  const ask = (q) => {
    const id = q.id;
    if (q.type === 'choice' || q.type === 'yesno') {
      return chips('c:' + id, q.type === 'yesno' ? ['yes', 'no'] : (q.options || []), () => d.custom[id]);
    }
    return `<input class="pv-in" data-c="${esc(id)}" data-kind="${esc(q.type)}"${q.type === 'number' ? ' inputmode="decimal"' : ''}
      value="${esc(d.custom[id] ?? '')}">`;
  };
  $('#pvBody').innerHTML = `
    ${open.newer ? `<div class="pv-busy"><b>${esc(open.newer.scoutId || 'Somebody')} saved this robot after you
      started.</b> Your unsaved answers are shown — saving replaces theirs.
      <button class="pv-chip" id="pvTheirs">Use theirs instead</button></div>`
    : open.restored ? `<div class="pv-busy"><b>Picked up where you left off.</b> These answers were
      on this phone but never reached the mirror — save to send them.</div>` : ''}
    ${open.offline ? `<div class="pv-busy"><b>No signal.</b> Showing the answers kept on this phone;
      save once you have a bar.</div>` : ''}
    ${c ? `<div class="pv-busy"><b>${esc(claimName(c))} is already on this robot</b> — opened ${
      Math.max(0, Math.round((nowServer() - c.at) / 60)) || 'under a'} min ago. Check with them before you both do it.</div>` : ''}
    <div class="pv-grp"><h3>The robot</h3>
      ${BUILT_IN.map((q) => `<div class="pv-q"><div class="lbl">${esc(q.label)}</div>
        ${chips(q.f, q.opts, () => d[q.f])}${q.hint ? `<div class="hint2">${esc(q.hint)}</div>` : ''}</div>`).join('')}
    </div>
    ${questions.length ? `<div class="pv-grp"><h3>Our questions</h3>
      ${questions.map((q) => `<div class="pv-q"><div class="lbl">${esc(q.label)}</div>${ask(q)}
        ${q.hint ? `<div class="hint2">${esc(q.hint)}</div>` : ''}</div>`).join('')}</div>` : ''}
    <div class="pv-grp"><h3>Autos and notes</h3>
      <div class="pv-q"><div class="lbl">Auto routines</div>
        <input class="pv-in" id="pvAutos" value="${esc(d.autos || '')}" placeholder="2 — centre 8 fuel, wall 5 + L1 climb"></div>
      <div class="pv-q"><div class="lbl">Weight (lb)</div>
        <input class="pv-in" id="pvWeight" inputmode="numeric" value="${esc(d.weight || '')}" placeholder="120"></div>
      <div class="pv-q"><div class="lbl">Notes</div>
        <textarea class="pv-in" id="pvNotes" rows="3" placeholder="what the numbers won't show">${esc(d.notes || '')}</textarea></div>
      <div class="pv-q"><div class="lbl">Photos</div>
        <div class="pv-shots" id="pvShots"></div>
        <input type="file" id="pvPhoto" accept="image/*" capture="environment" multiple class="hide"></div>
    </div>`;
  for (const group of $$('#pvBody .pv-chips')) {
    const f = group.dataset.f;
    for (const btn of $$('button', group)) {
      btn.onclick = () => {
        const v = btn.dataset.v;
        if (f.startsWith('c:')) {
          const id = f.slice(2);
          if (d.custom[id] === v) delete d.custom[id]; else d.custom[id] = v;
          for (const x of $$('button', group)) x.classList.toggle('on', d.custom[id] === x.dataset.v);
        } else {
          d[f] = d[f] === v ? null : v;
          for (const x of $$('button', group)) x.classList.toggle('on', d[f] === x.dataset.v);
        }
        keepDraft();
      };
    }
  }
  drawShots();
  $('#pvPhoto').onchange = async (e) => {
    for (const f of e.target.files) {
      try { d.photos.push(await shrink(f)); } catch { /* not an image */ }
    }
    e.target.value = '';
    drawShots();
    keepDraft();
  };
  for (const el of $$('#pvBody input, #pvBody textarea')) el.addEventListener('input', keepDraft);
  const save = $('#pvSave');
  save.disabled = false;
  save.textContent = existed ? 'Update pit' : 'Save pit';
  save.onclick = () => savePit(team);
}

function drawShots() {
  const d = open.draft;
  // A photo already on the mirror is an id; one just taken is a data: URI.
  $('#pvShots').innerHTML = d.photos.map((p) =>
    `<img alt="" src="${esc(String(p).startsWith('data:') ? p : ctx.signed('/api/photo/' + encodeURIComponent(p)))}">`).join('')
    + '<button class="pv-add" id="pvAdd">+ Photo</button>';
  $('#pvAdd').onclick = () => $('#pvPhoto').click();
}

/** Resize on the phone: a 12MP pit photo is not worth 4MB of somebody's data plan. */
function shrink(file, max = 1200, quality = 0.72) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement('canvas');
      c.width = Math.round(img.width * scale);
      c.height = Math.round(img.height * scale);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', quality));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('bad image')); };
    img.src = url;
  });
}

/** The typed answers into the draft. */
function readInputs() {
  const d = open.draft;
  if (!d || !$('#pvAutos')) return;
  d.autos = $('#pvAutos').value;
  d.weight = $('#pvWeight').value;
  d.notes = $('#pvNotes').value;
  for (const inp of $$('#pvBody input[data-c]')) {
    const v = inp.value.trim();
    if (!v) delete d.custom[inp.dataset.c];
    else if (inp.dataset.kind === 'number' && Number.isFinite(Number(v))) d.custom[inp.dataset.c] = Number(v);
    else d.custom[inp.dataset.c] = v;
  }
}

async function savePit(team) {
  if (open.saving || open.team !== team) return;
  keepDraft();
  const d = open.draft;
  const btn = $('#pvSave');
  open.saving = true;
  btn.disabled = true;
  btn.textContent = 'Saving…';
  try {
    const r = await ctx.api('/api/pit/save', { method: 'POST', body: JSON.stringify({
      eventKey: ek(), team, deviceId: deviceId(), scoutId: initials() || 'pit', payload: d,
    }) });
    dropDraft(team);
    open.base = canon(d);          // what the mirror now holds: closing keeps nothing
    open.baseAt = (r && r.updatedAt) || open.baseAt;
    btn.classList.add('saved');
    btn.textContent = 'Saved — goes to the hub within a minute';
    if (navigator.vibrate) { try { navigator.vibrate(14); } catch { /* not supported */ } }
    const n = FIELDS.filter((f) => d[f]).length;
    live.status[String(team)] = { done: n >= 4 ? 'done' : n ? 'partial' : '' };
    clearTimeout(open.closeTimer);
    open.closeTimer = setTimeout(() => { if (open.team === team) closePit(); }, 900);
  } catch (e) {
    // Kept open with everything still in it: no signal in a pit aisle is
    // normal, and the answers are not lost by trying again.
    // A fetch that never got an answer is a TypeError; anything the mirror
    // answered carries its own reason, which is the one to show.
    open.saving = false;
    btn.disabled = false;
    btn.textContent = e.message === 'locked' ? 'Signed out — enter the passcode again'
      : e instanceof TypeError ? 'Not saved — no signal? Your answers are kept. Tap to try again'
      : `Not saved — ${e.message}`;
  }
}

function closePit() {
  clearTimeout(open.closeTimer);
  keepDraft();
  const team = open.team;
  open.team = null;
  open.draft = null;
  letGo(team);
  $('#psheet').classList.add('hide');
  $('#psheet').innerHTML = '';
  paintPits();
  pollLive();
}

// ------------------------------------------------------- question editor

const Q_TYPES = [['choice', 'Pick one'], ['yesno', 'Yes or no'], ['number', 'Number'], ['text', 'Short answer']];
const Q_MAX = 30;
const ed = { doc: null, draft: null, saving: false, msg: '' };
const qChanged = () => ed.draft && ed.doc && JSON.stringify(ed.draft) !== JSON.stringify(ed.doc.questions || []);

/** The Pit questions tab. Leaves a half-typed edit alone on a background refresh. */
export async function viewQuestions() {
  // Unsaved edits survive a background refresh and a trip to another tab.
  if (qChanged()) {
    if (!$('#qList')) drawEditor();
    return;
  }
  $('#view').innerHTML = '<div class="pv-empty">Loading…</div>';
  let doc;
  try {
    doc = await ctx.api('/api/pitquestions');
  } catch (e) {
    if (ctx.state.tab === 'questions') $('#view').innerHTML = `<div class="hint">${esc(e.message)}</div>`;
    return;
  }
  if (ctx.state.tab !== 'questions' || qChanged()) return;   // left, or started editing, meanwhile
  ed.doc = doc;
  ed.draft = JSON.parse(JSON.stringify(doc.questions || []));
  drawEditor();
}

function drawEditor() {
  const locked = !ed.doc.editable;
  const dis = locked ? ' disabled' : '';
  $('#view').innerHTML = `
    <div class="qed">
      <div>
        <div class="eyebrow" style="margin-bottom:4px">Your own pit questions</div>
        <div class="hint">These show on the Pit scouting tab under <b>Our questions</b>, after the
          built-in ones. Answers go to the hub with the rest of the pit record, show on each
          team's page, and get a column of their own in the pit spreadsheet.</div>
      </div>
      ${locked ? `<div class="banner cold" style="margin:0">${esc(ed.doc.why
        || 'This mirror has no view passcode, so nobody can edit here. Set MIRROR_VIEW_PASSCODE on the server.')}</div>` : ''}
      <div class="qlist" id="qList">${ed.draft.length ? ed.draft.map((q, i) => `
        <div class="qcard" data-i="${i}">
          <input data-k="label" value="${esc(q.label || '')}" maxlength="120"
            placeholder="The question — e.g. What colour are their bumpers?"${dis}>
          <select data-k="type" aria-label="Kind of answer"${dis}>${Q_TYPES.map(([v, l]) =>
            `<option value="${v}"${(q.type || 'text') === v ? ' selected' : ''}>${l}</option>`).join('')}</select>
          <div class="tools">
            <button class="qbtn sm" data-act="up" aria-label="Move up"${i === 0 || locked ? ' disabled' : ''}>↑</button>
            <button class="qbtn sm" data-act="down" aria-label="Move down"${i === ed.draft.length - 1 || locked ? ' disabled' : ''}>↓</button>
            <button class="qbtn sm" data-act="del"${dis}>Remove</button>
          </div>
          ${q.type === 'choice' ? `<div class="wide"><div class="sub">Answers to pick from, separated by commas (2 to 8)</div>
            <input data-k="options" value="${esc((q.options || []).join(', '))}" placeholder="red, blue, both"${dis}></div>` : ''}
          <div class="wide"><input data-k="hint" value="${esc(q.hint || '')}" maxlength="160"
            placeholder="Optional: a help line under the question"${dis}></div>
        </div>`).join('')
        : '<div class="pv-empty">No questions of your own yet. Add one and it shows up for every pit scout.</div>'}
      </div>
      <div class="qbar">
        <button class="qbtn" id="qAdd"${locked || ed.draft.length >= Q_MAX ? ' disabled' : ''}>+ Add a question</button>
        <span class="hint" id="qMsg" style="flex:1"></span>
        <button class="qbtn" id="qUndo">Discard changes</button>
        <button class="qbtn go" id="qSave">Save questions</button>
      </div>
      <div class="eyebrow" style="margin-top:6px">How it looks to a pit scout</div>
      <div class="qprev" id="qPreview"></div>
      <ul class="hint" style="margin:0;padding-left:18px;display:flex;flex-direction:column;gap:4px">
        <li>Renaming a question keeps the answers already given.</li>
        <li>Removing one hides it from pit scouting and the spreadsheet. The answers stay in the full export.</li>
        <li>The hub collects a save within a minute.</li>
      </ul>
    </div>`;
  for (const card of $$('#qList .qcard')) {
    const i = Number(card.dataset.i);
    const q = ed.draft[i];
    for (const el of $$('[data-k]', card)) {
      el.oninput = () => {
        const k = el.dataset.k;
        if (k === 'options') q.options = el.value.split(',').map((x) => x.trim()).filter(Boolean);
        else q[k] = el.value;
        if (k === 'type') { if (q.type === 'choice' && !q.options) q.options = []; drawEditor(); return; }
        paintEditorState(); drawPreview();
      };
    }
    for (const btn of $$('[data-act]', card)) {
      btn.onclick = () => {
        if (btn.dataset.act === 'del') ed.draft.splice(i, 1);
        else {
          const j = btn.dataset.act === 'up' ? i - 1 : i + 1;
          [ed.draft[i], ed.draft[j]] = [ed.draft[j], ed.draft[i]];
        }
        drawEditor();
      };
    }
  }
  $('#qAdd').onclick = () => {
    ed.draft.push({ label: '', type: 'choice', options: [] });
    drawEditor();
    const inputs = $$('#qList .qcard input[data-k="label"]');
    if (inputs.length) inputs[inputs.length - 1].focus();
  };
  $('#qUndo').onclick = () => { ed.draft = JSON.parse(JSON.stringify(ed.doc.questions || [])); ed.msg = ''; drawEditor(); };
  $('#qSave').onclick = saveQuestions;
  paintEditorState();
  drawPreview();
}

function paintEditorState() {
  const changed = qChanged();
  $('#qSave').disabled = !ed.doc.editable || ed.saving || !changed;
  $('#qUndo').disabled = !changed || ed.saving;
  $('#qMsg').textContent = ed.msg || (changed ? 'Unsaved changes' : '');
}

function drawPreview() {
  const qs = (ed.draft || []).filter((q) => (q.label || '').trim());
  $('#qPreview').innerHTML = qs.length ? qs.map((q) => {
    const opts = q.type === 'yesno' ? ['yes', 'no'] : q.type === 'choice' ? (q.options || []) : null;
    return `<div><div class="lbl">${esc(q.label)}</div>${opts
      ? `<div class="chips">${opts.map((o) => `<span class="pchip">${esc(o)}</span>`).join('')
        || '<span class="h2">add some answers</span>'}</div>`
      : '<div class="box"></div>'}${q.hint ? `<div class="h2">${esc(q.hint)}</div>` : ''}</div>`;
  }).join('') : '<div class="hint">Nothing yet — only the built-in questions will show.</div>';
}

async function saveQuestions() {
  ed.saving = true;
  ed.msg = 'Saving…';
  paintEditorState();
  try {
    const send = ed.draft.filter((q) => (q.label || '').trim());
    const r = await ctx.api('/api/pitquestions', { method: 'POST',
      body: JSON.stringify({ questions: send, rev: ed.doc.rev }) });
    ed.doc = { ...ed.doc, id: r.id, rev: r.rev, questions: r.questions };
    ed.draft = JSON.parse(JSON.stringify(r.questions));
    ed.msg = 'Saved. The hub collects it within a minute.';
  } catch (e) {
    ed.msg = e.message || 'Could not reach the mirror. Nothing was saved.';
  }
  ed.saving = false;
  drawEditor();
}
