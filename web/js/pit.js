// Pit scouting. The map is the point: a scouter should see where to walk next,
// not read team numbers off a list. Geometry comes from Nexus /event/{key}/map.

import * as db from './db.js';
import * as net from './net.js';
import * as fs from './fullscreen.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let STATE = null, entries = {}, sel = null, ourTeam = null, draft = null;

const FIELDS = ['drivetrain', 'shooter', 'maxClimb', 'stockpile', 'groundPickup'];

function pitStatus(team) {
  const e = entries[team];
  if (!e) return '';
  const p = e.payload || {};
  const filled = FIELDS.filter((f) => p[f]).length;
  return filled >= 4 ? 'done' : filled ? 'partial' : '';
}

// ------------------------------------------------------------------- map
function renderMap() {
  const map = STATE && STATE.pitMap;
  const host = $('#mapHost');
  if (!map || !map.pits) {
    host.innerHTML = `<div style="padding:26px 18px;text-align:center;font:600 13px/1.6 Barlow,sans-serif;color:var(--body)">
      No pit map for this event.<br><span style="color:var(--dim)">Nexus only has one where the event uses it for queuing. The LIST works either way.</span></div>`;
    return;
  }
  const size = map.size || { x: 1000, y: 1000 };
  const parts = [];

  // Nexus gives `position` as the CENTRE of each element, not its top-left.
  // Verified against the published example map: with centre, every element
  // lands exactly inside the declared size; with top-left it overflows by ~50%.
  const box = (o) => {
    const p = o.position || {}, s = o.size || {};
    const w = s.x || 0, h = s.y || 0;
    return { x: (p.x || 0) - w / 2, y: (p.y || 0) - h / 2, w, h, cx: p.x || 0, cy: p.y || 0 };
  };
  const rot = (o, b) => `rotate(${o.angle || 0} ${b.cx} ${b.cy})`;

  for (const [, a] of Object.entries(map.areas || {})) {
    const b = box(a);
    parts.push(`<g class="area" transform="${rot(a, b)}">
      <rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="4"></rect>
      <text x="${b.cx}" y="${b.cy}" text-anchor="middle" dominant-baseline="middle">${esc(String(a.label || '').toUpperCase())}</text></g>`);
  }
  for (const [, w] of Object.entries(map.walls || {})) {
    const b = box(w);
    parts.push(`<rect class="wall" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" transform="${rot(w, b)}"></rect>`);
  }
  // arrows mark entrances, exits and the walking route between pit rows
  for (const [, a] of Object.entries(map.arrows || {})) {
    const b = box(a);
    const head = Math.min(b.h * 0.42, b.w);
    const shaft = `M${b.cx - b.w * 0.16} ${b.y} h${b.w * 0.32} V${b.y + b.h - head} h${b.w * 0.18}
                   L${b.cx} ${b.y + b.h} L${b.x} ${b.y + b.h - head} h${b.w * 0.18} Z`;
    parts.push(`<path class="arrow" d="${shaft}" transform="${rot(a, b)}"></path>`);
    if (a.type === 'double') {
      parts.push(`<path class="arrow" d="${shaft}" transform="rotate(${(a.angle || 0) + 180} ${b.cx} ${b.cy})"></path>`);
    }
  }
  for (const [, l] of Object.entries(map.labels || {})) {
    const b = box(l);
    parts.push(`<text class="area" x="${b.cx}" y="${b.cy}" text-anchor="middle"
      dominant-baseline="middle" transform="${rot(l, b)}">${esc(l.label || '')}</text>`);
  }
  for (const [id, pit] of Object.entries(map.pits || {})) {
    const b = box(pit);
    if (!pit.team) {
      parts.push(`<rect class="emptypit" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="5"
        transform="${rot(pit, b)}"></rect>`);
      continue;
    }
    const team = Number(pit.team);
    const cls = ['pit', pitStatus(team), sel === team ? 'sel' : '', team === ourTeam ? 'ours' : '']
      .filter(Boolean).join(' ');
    parts.push(`<g class="${cls}" data-team="${team}" transform="${rot(pit, b)}">
      <rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="5"></rect>
      <text x="${b.cx}" y="${b.cy - 3}" text-anchor="middle" dominant-baseline="middle">${team}</text>
      <text class="addr" x="${b.cx}" y="${b.cy + 13}" text-anchor="middle" dominant-baseline="middle">${esc(id)}</text></g>`);
  }

  host.innerHTML = `<svg class="pitmap" viewBox="0 0 ${size.x} ${size.y}"
    preserveAspectRatio="xMidYMid meet"
    style="aspect-ratio:${size.x} / ${size.y}">${parts.join('')}</svg>`;
  for (const g of $$('#mapHost .pit')) g.onclick = () => select(Number(g.dataset.team));
}

function renderList() {
  const pits = (STATE && STATE.pits) || {};
  const teams = (STATE && STATE.teams) || [];
  const rows = teams.length ? teams.map((t) => t.team) : Object.keys(pits).map(Number);
  // unscouted first, then by pit address, so the list doubles as a walking order
  rows.sort((a, b) => {
    const da = pitStatus(a) === 'done' ? 1 : 0, dbb = pitStatus(b) === 'done' ? 1 : 0;
    if (da !== dbb) return da - dbb;
    return String(pits[a] || '~').localeCompare(String(pits[b] || '~'), undefined, { numeric: true });
  });
  $('#listHost').innerHTML = rows.map((t) => {
    const st = pitStatus(t);
    const name = (teams.find((x) => x.team === t) || {}).name || '';
    return `<div class="qrow ${st}" data-team="${t}"><b>${t}</b>
      <span class="nm">${esc(name)}${pits[t] ? ' · pit ' + esc(pits[t]) : ''}</span>
      <span class="st">${st === 'done' ? 'DONE' : st === 'partial' ? 'STARTED' : 'TO DO'}</span></div>`;
  }).join('') || '<div class="qrow"><span class="nm">No teams yet.</span></div>';
  for (const r of $$('#listHost .qrow')) r.onclick = () => select(Number(r.dataset.team));
}

function renderProgress() {
  const teams = (STATE && STATE.teams || []).map((t) => t.team);
  const done = teams.filter((t) => pitStatus(t) === 'done').length;
  const pct = teams.length ? Math.round(done / teams.length * 100) : 0;
  $('#progLbl').textContent = teams.length ? `${done} OF ${teams.length} PITS DONE` : 'NO TEAMS YET';
  $('#progPct').textContent = teams.length ? `${pct}%` : '';
  $('#progBar').style.width = `${pct}%`;
}

// ------------------------------------------------------------------ form
function select(team) {
  sel = team;
  const teams = (STATE && STATE.teams) || [];
  const pits = (STATE && STATE.pits) || {};
  const insp = (STATE && STATE.inspection) || {};
  const prev = entries[team];
  draft = JSON.parse(JSON.stringify((prev && prev.payload) || { photos: [] }));
  draft.photos = draft.photos || [];

  $('#sheet').classList.add('open');
  $('#pTeam').textContent = team;
  $('#pName').textContent = (teams.find((x) => x.team === team) || {}).name || '';
  $('#pAddr').textContent = pits[team] ? `pit ${pits[team]}` : 'pit address unknown';

  const st = String(insp[team] || insp[String(team)] || '').toLowerCase();
  const badge = $('#pInsp');
  badge.textContent = st ? st.toUpperCase().replace(/-/g, ' ') : 'INSPECTION —';
  badge.className = 'insp ' + (st === 'complete' ? 'complete' : st === 'hold' ? 'hold' : 'other');

  for (const holder of $$('.chips')) {
    const f = holder.dataset.field;
    if (!holder.children.length) {
      for (const opt of holder.dataset.opts.split(',')) {
        const b = document.createElement('button');
        b.className = 'chip'; b.textContent = opt; b.dataset.v = opt;
        b.onclick = () => { draft[f] = draft[f] === opt ? null : opt; paintChips(); };
        holder.appendChild(b);
      }
    }
  }
  paintChips();
  $('#pAutos').value = draft.autos || '';
  $('#pWeight').value = draft.weight || '';
  $('#pNotes').value = draft.notes || '';
  renderShots();
  const save = $('#pSave');
  save.classList.remove('saved');
  save.textContent = prev ? 'UPDATE PIT' : 'SAVE PIT';
  renderMap(); renderList();
}

function paintChips() {
  for (const holder of $$('.chips')) {
    const f = holder.dataset.field;
    for (const b of holder.children) b.classList.toggle('on', draft[f] === b.dataset.v);
  }
}

function renderShots() {
  $('#pShots').innerHTML = (draft.photos || []).map((p) =>
    `<img src="${p.startsWith('data:') ? p : '/api/photo/' + p}" alt="">`).join('')
    + '<div class="addshot" id="addShot">+ PHOTO</div>';
  const add = $('#addShot');
  if (add) add.onclick = () => $('#pPhoto').click();
}

/** Resize client-side: a 12MP pit photo is not worth 4MB on a venue hotspot. */
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

$('#pPhoto').onchange = async (e) => {
  for (const f of e.target.files) {
    try { draft.photos.push(await shrink(f)); } catch {}
  }
  e.target.value = '';
  renderShots();
};

$('#pSave').onclick = async () => {
  if (!sel) return;
  draft.autos = $('#pAutos').value;
  draft.weight = $('#pWeight').value;
  draft.notes = $('#pNotes').value;
  const rec = {
    eventKey: (STATE && STATE.eventKey) || localStorage.getItem('eventKey') || '',
    team: sel,
    scoutId: localStorage.getItem('scoutName') || 'pit',
    payload: draft,
  };
  await db.savePit(rec);
  entries[sel] = rec;
  const b = $('#pSave');
  b.classList.add('saved');
  b.textContent = net.state.online ? 'SAVED · SENT' : 'SAVED ON THIS PHONE';
  if (navigator.vibrate) { try { navigator.vibrate(14); } catch {} }
  net.flush();
  renderMap(); renderList(); renderProgress();
  setTimeout(() => { $('#sheet').classList.remove('open'); sel = null; }, 550);
};

$('#pClose').onclick = () => { $('#sheet').classList.remove('open'); sel = null; renderMap(); };

// --------------------------------------------------------------- refresh
async function refresh() {
  try { STATE = await net.api('/api/state'); db.cacheSet('state', STATE); }
  catch { STATE = await db.cacheGet('state'); }
  entries = {};
  for (const e of (STATE && STATE.pitEntries) || []) entries[e.team] = e;
  // anything saved locally but not yet accepted still counts as scouted
  for (const r of await db.all('pit')) if (!entries[r.team]) entries[r.team] = r;

  $('#phdr').textContent = STATE
    ? `${(STATE.eventKey || '').toUpperCase()}${STATE.pitMap ? '' : ' · NO MAP'}`
    : 'HUB NOT REACHABLE';
  $('#pdot').className = 'dot' + (net.state.online ? '' : ' amber');
  renderMap(); renderList(); renderProgress();
}

$('#btnMap').onclick = () => {
  $('#btnMap').classList.add('on'); $('#btnList').classList.remove('on');
  $('#mapHost').classList.remove('hide'); $('#listHost').classList.add('hide');
};
$('#btnList').onclick = () => {
  $('#btnList').classList.add('on'); $('#btnMap').classList.remove('on');
  $('#listHost').classList.remove('hide'); $('#mapHost').classList.add('hide');
};

async function main() {
  fs.install({ triggers: ['#mapHost', '#listHost', '#btnMap', '#btnList'],
               orientation: 'portrait', hintEl: $('#fsHint') });
  net.setIdentity({ deviceId: db.deviceId(),
                    scoutId: localStorage.getItem('scoutName') || 'pit', seat: 'pit' });
  const cfg = await net.start();
  ourTeam = Number((cfg && cfg.ourTeam) || 0) || null;
  await refresh();
  net.onChange(() => { $('#pdot').className = 'dot' + (net.state.online ? '' : ' amber'); });
  net.on('pits', refresh); net.on('pitMap', refresh);
  net.on('inspection', refresh); net.on('scout', refresh);
  setInterval(refresh, 30000);
}
main().catch((e) => { console.error(e); $('#phdr').textContent = 'FAILED: ' + e.message; });
