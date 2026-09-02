// Strategy dashboard. Read-only; runs on any device on the network.
// Layout and every literal value come from design/Computer *.dc.html.

import * as db from './db.js';
import * as net from './net.js';
import { loadRules, rpThresholds, rules as gameRules } from './game2026.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let STATE = null, ANALYTICS = null, CONFIG = null, ourTeam = null;
const DNP = new Set(JSON.parse(localStorage.getItem('dnp') || '[]'));
// Two boards. A first pick is the best robot left; a second pick is the best
// complement to the two we already have, which is a different question and so
// a different weighting - defence and feeding matter far more down there.
const WEIGHTS = { climb: 30, reliability: 25, stockpile: 15, fuel: 20, defense: 10 };
const WEIGHTS2 = { climb: 15, reliability: 30, stockpile: 20, fuel: 15, defense: 35 };
let PICK_MODE = 'first';
const activeWeights = () => (PICK_MODE === 'first' ? WEIGHTS : WEIGHTS2);

const RANK_COLS = '56px minmax(120px,1fr) 84px 74px 52px 44px 86px';
const ALL_COLS  = '62px 56px minmax(160px,1fr) 84px 74px 62px 60px 62px 62px 56px 52px 70px 64px';

// ------------------------------------------------------------------ bits
function climbCell(t) {
  const best = t.exact.bestClimb;
  const rate = Math.round(t.exact.climbRate[best] || 0);
  const cls = { Level3: 'l3', Level2: 'l2', Level1: 'l1' }[best] || 'none';
  return best === 'None'
    ? '<span class="cl none">NONE</span>'
    : `<span class="cl ${cls}">${best.replace('Level', 'L')} · ${rate}%</span>`;
}
function trustCell(v) {
  const pct = Math.round((v ?? 0) * 100);
  return `<span class="trust"><i class="${pct < 65 ? 'low' : ''}" style="width:${pct}%"></i></span>`;
}

/**
 * How much to trust THIS ROBOT'S numbers — a property of the data, not of a
 * person. Grows with matches seen and shrinks as the band widens relative to
 * the mean. This replaced a per-scout trust bar that named individuals on a
 * screen the whole room can see.
 */
function confidence(t) {
  const n = t.estimated.matches || 0;
  if (!n) return 0;
  const rel = t.estimated.avgFuel > 0 ? t.estimated.band / t.estimated.avgFuel : 1;
  return Math.max(0, Math.min(1, Math.min(1, n / 8) * (1 - Math.min(1, rel))));
}
function confidenceCell(t) {
  const pct = Math.round(confidence(t) * 100);
  return `<span class="trust" title="${t.estimated.matches} matches, ±${t.estimated.band} fuel">`
       + `<i class="${pct < 50 ? 'low' : ''}" style="width:${pct}%"></i></span>`;
}
function tile(k, v, c, alert) {
  return `<div class="tile${alert ? ' alert' : ''}"><div class="k">${k}</div>
    <div class="v">${v}</div><div class="c${alert === 'warn' ? ' warn' : ''}">${c}</div></div>`;
}
const shortCode = (label) => {
  const m = String(label || '').match(/(\w)\w*\s*(\d+)/);
  return m ? `${m[1].toUpperCase()}${m[2]}` : (label || '—');
};

/** Robots that took the field in a played match with no scout entry for them. */
function unwatchedRobots() {
  if (!ANALYTICS || !STATE) return [];
  const out = new Set();
  const scoutedFor = new Map();
  for (const t of Object.values(ANALYTICS.teams)) {
    if (t.matchesScouted) scoutedFor.set(t.team, t.matchesScouted);
  }
  for (const m of STATE.matches || []) {
    if (!m.breakdown) continue;
    for (const t of [...(m.red || []), ...(m.blue || [])]) {
      if (!scoutedFor.has(t)) out.add(t);
    }
  }
  return [...out];
}

/** "9982 x3 · 9975 x1", or an em dash. */
function teamCounts(map) {
  const rows = Object.entries(map || {}).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return '—';
  return rows.slice(0, 3).map(([t, n]) => `${t}${n > 1 ? ` \u00d7${n}` : ''}`).join(' · ');
}

function recordLine(e) {
  const r = e.record;
  if (!r) return '';
  return ` · ${r.wins}-${r.losses}-${r.ties}${r.official ? '' : ' (scouted matches only)'}`;
}
function matchLabel(matchKey) {
  const m = ((STATE && STATE.matches) || []).find((x) => x.matchKey === matchKey);
  return (m && m.label) || matchKey;
}

// ═════════════════════════════════════════════════════════════════ LIVE
function renderLive() {
  const matches = (STATE && STATE.matches) || [];
  const live = (STATE && STATE.live) || {};
  const onField = matches.find((m) => m.status === 'On field');
  const queuing = matches.find((m) => m.status === 'Now queuing' || m.status === 'On deck');
  const played = matches.filter((m) => m.breakdown).length;
  const upcoming = matches.filter((m) => !m.breakdown && m !== onField && m !== queuing).slice(0, 1);

  const rows = [];
  const block = (m, cls, tag) => m ? `
    <div class="mrow ${cls}">
      <div class="top"><span class="l">${esc((m.label || '').toUpperCase())}${tag ? ' · ' + tag : ''}</span>
        ${cls === 'now' ? `<span class="r">${esc(live.nowQueuing ? 'QUEUING ' + live.nowQueuing : 'ON FIELD')}</span>` : ''}</div>
      <div class="teams red">${(m.red || []).join(' · ') || '—'}</div>
      <div class="teams blue">${(m.blue || []).join(' · ') || '—'}</div>
    </div>` : '';
  rows.push(block(onField, 'now', ''));
  rows.push(block(queuing, 'next', 'QUEUING'));
  rows.push(block(upcoming[0], 'later', ''));
  $('#onfield').innerHTML = rows.filter(Boolean).join('') ||
    '<div class="empty">No schedule yet — set an event key and API keys on the hub.</div>';

  // Heads up: a station that has gone quiet is the failure that ruins a dataset.
  // Named by ROBOT, not by scout — the lead acts on "9982 went unwatched" just
  // as well, and the CREW tab already says whose phone is dark.
  const unwatched = unwatchedRobots();
  if (unwatched.length) {
    $('#headsup').classList.remove('hide');
    $('#headsupBody').textContent =
      `${unwatched.slice(0, 6).join(', ')} ${unwatched.length === 1 ? 'has' : 'have'} `
      + `gone unscouted in a played match — check the stations on the CREW tab.`;
  } else $('#headsup').classList.add('hide');

  const cov = ANALYTICS ? ANALYTICS.coverage : { robotsScouted: 0, pct: 0 };
  const seated = CREW.filter((c) => c.scoutId && c.connected).length;
  const flags = (STATE && STATE.flags || []).length;
  $('#tiles').innerHTML =
    tile('SCOUTED', cov.robotsScouted, `${cov.pct}% coverage`) +
    tile('MATCHES', `${played}<small>/${matches.length || 0}</small>`, 'played') +
    tile('STATIONS', `${seated}<small>/6</small>`,
         seated < 6 ? `${6 - seated} not reporting` : 'all reporting', seated < 6 ? 'warn' : '') +
    tile('FLAGGED', flags, 'need reconcile', flags ? true : '');

  const teams = ANALYTICS ? Object.values(ANALYTICS.teams).filter((t) => t.matchesScouted) : [];
  teams.sort((a, b) => b.estimated.avgFuel - a.estimated.avgFuel);
  $('#rankHead').style.gridTemplateColumns = RANK_COLS;
  $('#rankHead').innerHTML =
    `<span>TEAM</span><span>NAME</span><span class="num">FUEL/MATCH</span><span class="num">CLIMB</span>
     <span class="num">AUTO</span><span class="num">RP</span><span class="num">CONFIDENCE</span>`;
  $('#rankBody').innerHTML = teams.slice(0, 12).map((t) => `
    <div class="r" style="grid-template-columns:${RANK_COLS}">
      <span class="tno">${t.team}</span>
      <span class="nm">${esc(t.name || '')}</span>
      <span class="num">${t.estimated.avgFuel} <span class="band">±${t.estimated.band}</span></span>
      <span class="num">${climbCell(t)}</span>
      <span class="num">${t.exact.autoClimbs || 0}</span>
      <span class="num">${t.exact.avgRP ?? '—'}</span>
      <span class="num">${confidenceCell(t)}</span>
    </div>`).join('') || '<div class="empty">No scouted teams yet.</div>';

  renderPickMini();
  renderRpOutlook();
}

/**
 * Where our own team stands on ranking points and what is still on the table.
 *
 * RP is the thing quals are actually scored on, and it is the one number the
 * dashboard could answer forward rather than backward: matches left times the
 * most we can still earn. Bounds only - it says what is reachable, never what
 * is likely, because who we are paired with decides most of it.
 */
function renderRpOutlook() {
  const host = $('#rpOutlook');
  if (!host) return;
  const t = ourTeam && ANALYTICS && ANALYTICS.teams[ourTeam];
  if (!t) {
    host.innerHTML = '<div class="hint">Set OUR TEAM on the hub settings page.</div>';
    return;
  }
  const ms = (STATE && STATE.matches) || [];
  const mine = ms.filter((m) => [...(m.red || []), ...(m.blue || [])].includes(ourTeam));
  const left = mine.filter((m) => !m.breakdown);
  const rp = t.exact.rankingPoints;
  const played = mine.length - left.length;
  const r = (gameRules().rankingPoints) || {};
  // win + every bonus RP the game offers, which is the ceiling for one match
  const perMatch = (r.win || 3) + 3;
  // TBA's first sort order is the ranking score - average RP per match - so the
  // total is that times however many we have actually played.
  const n = matchesFromRecord(t.exact.record) || played;
  const have = rp != null ? rp * n : null;
  const best = (rp != null && n + left.length)
    ? ((have + left.length * perMatch) / (n + left.length)).toFixed(2) : null;

  host.innerHTML = `
    <div class="kv"><span>rank</span><b>${t.exact.rank ?? '—'}</b></div>
    <div class="kv"><span>record</span><b>${recordText(t.exact.record)}</b></div>
    <div class="kv"><span>RP per match</span><b>${rp ?? '—'}</b></div>
    <div class="kv"><span>matches left</span><b>${left.length}</b></div>
    ${best && left.length ? `
      <div class="kv"><span>best case average</span><b>${best}</b></div>` : ''}
    <div class="hint" style="margin-top:6px">${left.length
      ? `Winning out with every bonus is worth ${perMatch} RP a match. What we actually
         get depends on who we are paired with, so this is a ceiling, not a forecast.`
      : 'Quals are done.'}</div>
    ${left.length ? `<div class="hint" style="margin-top:6px">Next: ${
      left.slice(0, 3).map((m) => esc(shortCode(m.label))).join(' · ')}</div>` : ''}`;
}
function matchesFromRecord(rec) {
  return rec ? (rec.wins || 0) + (rec.losses || 0) + (rec.ties || 0) : 0;
}
function recordText(rec) {
  return rec ? `${rec.wins}-${rec.losses}-${rec.ties}` : '—';
}

// ════════════════════════════════════════════════════════════════ TEAMS
let sortKey = 'fuel', sortDir = -1;
const ALL_HEAD = [
  ['rank', 'RANK', 2], ['team', 'TEAM', 0], ['name', 'NAME', 0], ['fuel', 'FUEL/MATCH', 1],
  ['climb', 'CLIMB', 1], ['epa', 'EPA', 1], ['tower', 'TOWER', 1], ['stock', 'STOCK', 1],
  ['waste', 'WASTED', 1], ['died', 'DIED', 1], ['drv', 'DRIVER', 1],
  ['lovat', 'LOVAT', 1], ['n', 'MATCHES', 1],
];
function sortVal(t, k) {
  switch (k) {
    // rank 1 is best, so invert it; unranked teams sort to the bottom either way
    case 'rank': return t.exact.rank == null ? -9999 : -t.exact.rank;
    case 'epa': return t.epa.epa ?? -1;
    case 'team': return t.team; case 'name': return 0;
    case 'fuel': return t.estimated.avgFuel;
    case 'climb': return { Level3: 3, Level2: 2, Level1: 1, None: 0 }[t.exact.bestClimb] || 0;
    case 'tower': return t.exact.avgTowerPoints;
    case 'stock': return t.observed.stockpileRate;
    case 'waste': return -(t.observed.wastedFuelPct ?? 999);
    case 'died': return -t.observed.diedRate;
    case 'drv': return t.observed.driver ?? 0;
    case 'lovat': return (t.lovat && t.lovat.avgFuel) ?? -1;
    case 'n': return t.matchesScouted;
    default: return 0;
  }
}
/**
 * Lovat — other teams' scouting, kept in its own column on purpose.
 *
 * It is not our data and it is not verified, so it never merges into the fuel
 * estimate beside it. Blank means nobody uploaded that robot to Lovat, which
 * is a different thing from a zero.
 */
function lovatN(t) { return (t.lovat && t.lovat.matches) || 0; }
// Like teamCounts, but the labels are free text from another team's app.
function teamRoles(map) {
  const rows = Object.entries(map || {}).sort((a, b) => b[1] - a[1]);
  if (!rows.length) return '—';
  return rows.slice(0, 3).map(([r, n]) => `${esc(r)}${n > 1 ? ` \u00d7${n}` : ''}`).join(' · ');
}
function lovatCell(t) {
  if (!lovatN(t) || t.lovat.avgFuel == null) return '<span class="band">—</span>';
  return `${Math.round(t.lovat.avgFuel)} <span class="band">n${lovatN(t)}</span>`;
}

function renderTeams() {
  if (!ANALYTICS) return;
  const rows = Object.values(ANALYTICS.teams).filter((t) => t.matchesScouted || lovatN(t));
  rows.sort((a, b) => (sortVal(a, sortKey) - sortVal(b, sortKey)) * sortDir);
  $('#allHead').style.gridTemplateColumns = ALL_COLS;
  $('#allHead').innerHTML = ALL_HEAD.map(([k, l, n]) =>
    `<span data-k="${k}" class="${n === 2 ? 'rk' : (n ? 'num' : '')}">${l}${sortKey === k ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}</span>`).join('');
  $('#allBody').innerHTML = rows.map((t) => `
    <div class="r" style="grid-template-columns:${ALL_COLS}${t.team === ourTeam ? ';box-shadow:inset 0 0 0 1px var(--red-ring)' : ''}">
      <span class="rk">${t.exact.rank ?? '—'}</span>
      <span class="tno">${t.team}</span>
      <span class="nm">${esc(t.name || '')}</span>
      <span class="num">${t.estimated.avgFuel} <span class="band">±${t.estimated.band}</span></span>
      <span class="num">${climbCell(t)}</span>
      <span class="num">${t.epa.epa ?? '—'}</span>
      <span class="num">${t.exact.avgTowerPoints}</span>
      <span class="num">${Math.round(t.observed.stockpileRate)}%</span>
      <span class="num">${t.observed.wastedFuelPct == null ? '—' : Math.round(t.observed.wastedFuelPct) + '%'}</span>
      <span class="num">${Math.round(t.observed.diedRate)}%</span>
      <span class="num">${lovatCell(t)}</span>
      <span class="num">${t.matchesScouted}</span>
    </div>`).join('') || '<div class="empty">No scouted teams yet.</div>';

  for (const r of $$('#allBody .r')) {
    r.style.cursor = 'pointer';
    r.onclick = () => {
      openTeam = Number(r.querySelector('.tno').textContent);
      renderTeamDetail();
      window.__goTab && window.__goTab('team');
    };
  }
  for (const s of $$('#allHead span[data-k]')) {
    s.onclick = () => {
      const k = s.dataset.k;
      if (sortKey === k) sortDir *= -1; else { sortKey = k; sortDir = -1; }
      renderTeams();
    };
  }
}

// ═════════════════════════════════════════════════════════════ PICKLIST
function score(t) {
  const W = activeWeights();
  const e = t.exact, o = t.observed, s = t.estimated;
  const climb = ({ Level3: 1, Level2: 0.65, Level1: 0.3, None: 0 })[e.bestClimb] || 0;
  const l3 = (e.climbRate.Level3 || 0) / 100;
  const rel = 1 - Math.min(1, (o.diedRate + o.noShowRate) / 100);
  const stock = (o.stockpileRate || 0) / 100;
  const def = (o.defense || 0) / 5;
  const maxFuel = Math.max(1, ...Object.values(ANALYTICS.teams).map((x) => x.estimated.avgFuel));
  return W.climb * (climb * .6 + l3 * .4) + W.reliability * rel +
         W.stockpile * stock + W.fuel * (s.avgFuel / maxFuel) + W.defense * def;
}
function takenTeams() {
  const out = new Set();
  for (const a of (STATE && STATE.alliances) || []) for (const t of a || []) if (t) out.add(Number(t));
  return out;
}

// The computed score is where a picklist starts, never where it ends. ORDER is
// the lead's hand-ordering; it wins outright, and `was` carries the computed
// rank alongside so the board shows what has drifted since they moved things.
let ORDER = [], ORDER2 = [];
const activeOrder = () => (PICK_MODE === 'first' ? ORDER : ORDER2);
function setActiveOrder(v) { if (PICK_MODE === 'first') ORDER = v; else ORDER2 = v; }

function computedRank() {
  if (!ANALYTICS) return [];
  return Object.values(ANALYTICS.teams).filter((t) => t.matchesScouted)
    .map((t) => ({ t, s: score(t) })).sort((a, b) => b.s - a.s)
    .map((r, i) => ({ ...r, was: i + 1 }));
}
function ranked() {
  const base = computedRank();
  const order = activeOrder();
  if (!order.length) return base;
  const left = new Map(base.map((r) => [r.t.team, r]));   // still in score order
  const out = [];
  for (const n of order) {
    const r = left.get(n);
    if (r) { out.push(r); left.delete(n); }
  }
  return out.concat([...left.values()]);                  // newly scouted teams fall in below
}
function moveInOrder(team, before) {
  // First edit freezes the current board, so a drag moves one team and leaves
  // everyone else exactly where the lead was looking at them.
  let order = activeOrder();
  if (!order.length) order = ranked().map((r) => r.t.team);
  order = order.filter((n) => n !== team);
  const at = before == null ? order.length : order.indexOf(before);
  order.splice(at < 0 ? order.length : at, 0, team);
  setActiveOrder(order);
  savePicklist();
}
function renderPickMini() {
  const taken = takenTeams();
  $('#pkMini').innerHTML = ranked().slice(0, 8).map(({ t, s }, i) => `
    <div class="pk ${i === 0 ? 'top' : ''} ${taken.has(t.team) ? 'taken' : ''} ${DNP.has(t.team) ? 'dnp' : ''}">
      <span class="i">${i + 1}</span><span class="n">${t.team}</span>
      <span class="nm">${esc(t.name || '')}</span><span class="s">${Math.round(s)}</span>
    </div>`).join('') || '<div class="empty">Nothing to rank yet.</div>';
  $('#pkHint').textContent = PICK_MODE === 'second' ? 'second pick'
    : (taken.size ? `${taken.size} taken` : 'drag to reorder');
}
// Everyone can read the board; the passcode only unlocks *changing* it.
let CAN_EDIT = true;
let PIN_SET = false;

function renderEditBar() {
  const bar = $('#pkEditBar');
  if (!PIN_SET || CAN_EDIT) {
    bar.innerHTML = CAN_EDIT && PIN_SET
      ? `<div class="callout" style="margin:0 0 10px;border-color:var(--green-border);background:rgba(52,168,106,.07)">
           <div class="h" style="color:var(--green-soft)">EDITING UNLOCKED</div>
           <div class="b">Weights and do-not-pick flags will save to the hub for everyone.</div></div>`
      : '';
  } else {
    bar.innerHTML = `<div class="callout" style="margin:0 0 10px">
      <div class="h">READ ONLY</div>
      <div class="b" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span>Anyone can look at the board. Changing it needs the strategy passcode.</span>
        <input id="pinInput" type="password" inputmode="numeric" autocomplete="off" placeholder="passcode"
          style="width:130px;background:var(--raised);border:1px solid var(--btn);border-radius:8px;
          padding:8px 10px;color:var(--t0);font:700 13px Barlow,sans-serif;text-align:center;letter-spacing:.2em">
        <button id="pinGo" style="padding:9px 14px;border-radius:8px;background:var(--red);border:none;
          color:#fff;font:800 10.5px Barlow,sans-serif;letter-spacing:.12em;cursor:pointer">UNLOCK EDITING</button>
        <span class="hint" id="pinMsg"></span>
      </div></div>`;
    $('#pinGo').onclick = () => tryUnlock($('#pinInput').value.trim());
    $('#pinInput').onkeydown = (e) => { if (e.key === 'Enter') tryUnlock($('#pinInput').value.trim()); };
  }
  $('#pinLock').classList.toggle('hide', !(PIN_SET && CAN_EDIT));
}

async function tryUnlock(pin) {
  const msg = $('#pinMsg');
  if (msg) msg.textContent = 'checking…';
  try {
    const r = await net.api('/api/unlock', { method: 'POST', body: JSON.stringify({ pin }) });
    localStorage.setItem('strategyToken', r.token);
    CAN_EDIT = true;
    await loadPicklistState();
    renderPicklist(); renderPickMini(); renderWeights();
  } catch (e) {
    if (msg) msg.textContent = e.locked ? 'Wrong passcode.' : 'Could not reach the hub.';
  }
}

async function loadPicklistState() {
  try {
    const pl = await net.api('/api/picklist');
    if (pl.weights && Object.keys(pl.weights).length) Object.assign(WEIGHTS, pl.weights);
    if (pl.weights2 && Object.keys(pl.weights2).length) Object.assign(WEIGHTS2, pl.weights2);
    DNP.clear();
    for (const n of pl.dnp || []) DNP.add(Number(n));
    ORDER = (pl.order || []).map(Number);
    ORDER2 = (pl.order2 || []).map(Number);
    CAN_EDIT = pl.canEdit !== false;
    PIN_SET = !!pl.locked;
    renderWeights();
  } catch { /* hub unreachable: keep whatever we last had */ }
  renderEditBar();
}

/** Picklist state lives on the hub so every authorised screen agrees. */
async function savePicklist() {
  if (!CAN_EDIT) return;
  try {
    await net.api('/api/picklist', { method: 'POST',
      body: JSON.stringify({ weights: WEIGHTS, weights2: WEIGHTS2, dnp: [...DNP],
                             order: ORDER, order2: ORDER2 }) });
  } catch { /* stays local until the hub is back */ }
}

function driftCell(was, i) {
  // Only meaningful once the board is hand-ordered; before that `was` is `i+1`.
  // Moving one team shifts everyone it passed by a place, and marking all of
  // them buries the move that was actually made - so only call out real gaps.
  if (was == null || Math.abs(was - (i + 1)) < 2) return '';
  return ` · <span class="hint">computed ${was}</span>`;
}

let dragTeam = null;
function wireDrag() {
  for (const row of $$('#pkFull .pk[data-team]')) {
    row.ondragstart = (ev) => {
      dragTeam = Number(row.dataset.team);
      ev.dataTransfer.effectAllowed = 'move';
      // Firefox will not start a drag without payload on the transfer
      ev.dataTransfer.setData('text/plain', row.dataset.team);
    };
    row.ondragover = (ev) => { ev.preventDefault(); ev.dataTransfer.dropEffect = 'move'; };
    row.ondrop = (ev) => {
      ev.preventDefault();
      const onto = Number(row.dataset.team);
      if (!dragTeam || dragTeam === onto) return;
      moveInOrder(dragTeam, onto);
      dragTeam = null;
      renderPicklist(); renderPickMini();
    };
    row.ondragend = () => { dragTeam = null; };
  }
}

function wirePickMode() {
  for (const b of $$('#pkMode button')) {
    b.classList.toggle('on', b.dataset.mode === PICK_MODE);
    b.onclick = () => {
      PICK_MODE = b.dataset.mode;
      renderPicklist(); renderPickMini(); renderWeights();
    };
  }
  const head = $('#weightsHead'), hint = $('#weightsHint');
  if (head) head.textContent = `WEIGHTS · ${PICK_MODE === 'first' ? 'FIRST' : 'SECOND'} PICK`;
  if (hint) {
    hint.textContent = PICK_MODE === 'first'
      ? 'Exact fields carry the ranking. Fuel is estimated, so it only breaks ties.'
      : 'A second pick complements the alliance rather than repeating it — defence, '
        + 'feeding and not breaking down carry more here than raw fuel.';
  }
}

function renderPicklist() {
  renderEditBar();
  wirePickMode();
  const taken = takenTeams();
  $('#pkStatus').textContent = taken.size
    ? `${taken.size} team${taken.size > 1 ? 's' : ''} already taken — crossed off live`
    : (activeOrder().length ? 'hand-ordered — new data no longer reorders the board'
                            : 'alliance selection not started');
  $('#pkFull').innerHTML = ranked().map(({ t, s, was }, i) => `
    <div class="pk ${i === 0 ? 'top' : ''} ${taken.has(t.team) ? 'taken' : ''} ${DNP.has(t.team) ? 'dnp' : ''}"
         style="margin:0;border-bottom:1px solid var(--row)"
         data-team="${t.team}"${CAN_EDIT ? ' draggable="true"' : ''}>
      <span class="i">${i + 1}</span><span class="n">${t.team}</span>
      <span class="nm">${esc(t.name || '')} — ${climbCell(t)} · ${t.estimated.avgFuel}±${t.estimated.band} fuel · stock ${Math.round(t.observed.stockpileRate)}%${driftCell(was, i)}</span>
      <span class="s">${Math.round(s)}</span>
      ${CAN_EDIT ? `<button class="x" data-dnp="${t.team}">${DNP.has(t.team) ? 'UN-DNP' : 'DNP'}</button>` : ''}
    </div>`).join('') || '<div class="empty">Nothing to rank yet.</div>';
  if (CAN_EDIT) wireDrag();
  for (const b of $$('[data-dnp]')) b.onclick = () => {
    const n = Number(b.dataset.dnp);
    DNP.has(n) ? DNP.delete(n) : DNP.add(n);
    localStorage.setItem('dnp', JSON.stringify([...DNP]));
    savePicklist();
    renderPicklist(); renderPickMini();
  };
  $('#dnpList').innerHTML = [...DNP].length
    ? [...DNP].map((n) => `<div class="kv"><span>${n}</span><b>do not pick</b></div>`).join('')
    : '<div class="hint">nobody flagged</div>';
  // The order is the board's; this only ever explains it.
  const order = ranked().slice(0, 10).map(({ t }) => t.team);
  peekAi('#pkWhyBody', 'picklist', { order }, 'Not written yet.');
  wireAi('#pkWhyGo', '#pkWhyBody', 'picklist', () => ({ order }), 'Not written yet.');

  const reset = $('#pkReset');
  if (reset) {
    reset.classList.toggle('hide', !(CAN_EDIT && activeOrder().length));
    reset.onclick = () => {
      setActiveOrder([]);
      savePicklist();
      renderPicklist(); renderPickMini();
    };
  }
}
function renderWeights() {
  const W = activeWeights();
  $('#weights').innerHTML = Object.entries(W).map(([k, v]) => `
    <div class="wt"><div class="lb"><span>${k.toUpperCase()}</span><span id="w-${k}">${v}</span></div>
    <input type="range" min="0" max="50" value="${v}" data-w="${k}"${CAN_EDIT ? '' : ' disabled'}></div>`).join('')
    + (CAN_EDIT ? '' : '<div class="hint">Unlock editing on the draft board to change these.</div>');
  for (const el of $$('[data-w]')) el.oninput = () => {
    activeWeights()[el.dataset.w] = Number(el.value);
    $(`#w-${el.dataset.w}`).textContent = el.value;
    savePicklist();
    renderPicklist(); renderPickMini();
  };
}

// ═══════════════════════════════════════════════════════════════ HEALTH
const SCOUT_COLS = '1fr 90px 90px 90px';
function renderHealth() {
  if (!ANALYTICS) return;
  const c = ANALYTICS.coverage;
  const flags = (STATE && STATE.flags) || [];
  const rep = ANALYTICS.scoreReport || null;
  $('#healthTiles').innerHTML =
    tile('COVERAGE', `${c.pct}%`, `${c.robotsScouted} of ${c.robotsExpected} robots`) +
    tile('MEDIAN ERROR', rep && rep.medianPct != null ? `${rep.medianPct}%` : '—',
         rep && rep.compared ? `over ${rep.compared} alliance-matches` : 'no results yet',
         rep && rep.medianPct > 25 ? 'warn' : '') +
    tile('FLAGGED', flags.length, 'need reconcile', flags.length ? true : '') +
    tile('CALIBRATED', (CONFIG && CONFIG.multipliersFittedFrom) || 0, 'windows fitted');

  renderScoreReport(rep);
  renderScoutPanel();

  $('#flags').innerHTML = flags.length ? flags.map((f) => `
    <div class="callout"><div class="h">${esc(shortCode(f.match_key))} · ${esc(f.kind)}</div>
      <div class="b">${esc(f.detail || '')}</div></div>`).join('')
    : '<div class="hint">nothing flagged</div>';

  const m = (CONFIG && CONFIG.multipliers) || {};
  $('#calib').innerHTML = Object.entries(m).map(([k, v]) => `
    <div class="kv"><span>${k}</span><b class="tnum">${Number(v).toFixed(2)}</b></div>`).join('') +
    `<div class="hint" style="margin-top:6px">${
      (CONFIG && CONFIG.multipliersFittedFrom)
        ? `fitted from ${CONFIG.multipliersFittedFrom} official windows`
        : 'still on shipped priors — fits itself after ~30 matches'}</div>`;
}

/**
 * SCOUTS vs TBA — the honest accuracy readout.
 *
 * Compares the RAW scout estimate (duration x intensity, active windows only,
 * computed server-side without ever looking at TBA) against the official
 * result. The solved fuel would be useless here: the solver distributes TBA's
 * totals, so adding it back up reproduces TBA exactly no matter how wrong the
 * scouts were.
 */
const REP_COLS = '92px 56px 110px 110px 82px';
function renderScoreReport(rep) {
  if (!rep || !rep.rows || !rep.rows.length) {
    $('#repHead').innerHTML = '';
    $('#repHead').style.gridTemplateColumns = '1fr';
    $('#repBody').innerHTML = '<div class="empty">Nothing to compare until a match has an official result.</div>';
    $('#repSummary').innerHTML = '<div class="hint">No played matches yet.</div>';
    return;
  }
  $('#repCap').textContent =
    `raw scout estimate vs the official result · ${rep.compared} fully-watched alliance-matches`;
  $('#repHead').style.gridTemplateColumns = REP_COLS;
  $('#repHead').innerHTML = `<span>MATCH</span><span>SIDE</span>
    <span class="num">OFFICIAL</span><span class="num">SCOUTS SAID</span><span class="num">OFF BY</span>`;
  $('#repBody').innerHTML = rep.rows.slice(0, 40).map((r) => {
    const d = r.deltaPct;
    const col = d == null ? 'var(--t5)'
      : Math.abs(d) <= 15 ? 'var(--green-soft)'
      : Math.abs(d) <= 30 ? 'var(--amber)' : 'var(--red-alert)';
    const partial = r.robotsScouted < 3
      ? `<span class="hint" style="display:block">${r.robotsScouted} of 3 watched</span>` : '';
    return `<div class="r" style="grid-template-columns:${REP_COLS}">
      <span class="tno" style="font-size:13px">${esc(shortCode(r.label))}</span>
      <span style="color:${r.alliance === 'red' ? 'var(--red-label)' : 'var(--blue-label)'};
        font:800 10.5px Barlow,sans-serif;letter-spacing:.1em">${r.alliance.toUpperCase()}</span>
      <span class="num">${r.officialFuel} <span class="band">fuel</span>${partial}</span>
      <span class="num">${Math.round(r.scoutFuel)} <span class="band">fuel</span></span>
      <span class="num" style="color:${col}">${d == null ? '—' : (d > 0 ? '+' : '') + Math.round(d) + '%'}</span>
    </div>`;
  }).join('');

  const bias = rep.biasPct;
  $('#repSummary').innerHTML = `
    <div class="kv"><span>median error</span><b>${rep.medianPct ?? '—'}%</b></div>
    <div class="kv"><span>worst 10%</span><b>${rep.p90Pct ?? '—'}%</b></div>
    <div class="kv"><span>running</span><b>${bias == null ? '—'
      : bias > 0 ? `${bias}% hot` : `${Math.abs(bias)}% cold`}</b></div>
    <div class="kv"><span>picked the winner</span><b>${
      rep.calledPct == null ? '—' : `${rep.calledIt} of ${rep.decided}`}</b></div>
    <div class="hint" style="margin-top:6px">${bias == null ? ''
      : Math.abs(bias) > 20
        ? `Scouts are calling shooting ${bias > 0 ? 'heavier' : 'lighter'} than it scores.
           Worth a word about the rate ladder — the solver corrects for it, but the
           single-match numbers get noisier.`
        : 'Within the range the solver was tuned for.'}</div>
    <div class="hint" style="margin-top:6px">Compares the raw scout estimate against TBA.
      It never uses the solved numbers, which are derived from TBA and would always agree.</div>`;
}

/**
 * Scout quality data is lead-only.
 *
 * The hub omits it unless the request carries the strategy passcode or comes
 * from the hub machine itself, so on a dashboard in the stands `scouts` is
 * simply absent and this panel says so rather than rendering an empty table.
 */
function renderScoutPanel() {
  const scouts = ANALYTICS && ANALYTICS.scouts;
  if (!scouts) {
    $('#scoutHead').innerHTML = '';
    $('#scoutHead').style.gridTemplateColumns = '1fr';
    $('#scoutBody').innerHTML = `<div class="hint" style="padding:12px 16px">
      Per-scout data is only shown on the hub machine, or after unlocking with the
      strategy passcode on the PICKLIST tab. It is coaching material for the lead,
      not a scoreboard for the room.</div>`;
    return;
  }
  $('#scoutHead').style.gridTemplateColumns = SCOUT_COLS;
  $('#scoutHead').innerHTML = `<span>SCOUT</span><span class="num">MATCHES</span>
    <span class="num">MISSED</span><span class="num">RECONCILES</span>`;
  $('#scoutBody').innerHTML = scouts.map((s) => `
    <div class="r" style="grid-template-columns:${SCOUT_COLS}">
      <span>${esc(s.scoutId)}</span>
      <span class="num">${s.matches}</span>
      <span class="num">${s.missedMatches}</span>
      <span class="num">${trustCell(s.reliability)}</span>
    </div>`).join('') || '<div class="empty">No scout data yet.</div>';
}

// ════════════════════════════════════════════════════════════════ CREW
let CREW = [];
let SEATLOG = [];
const CREW_COLS = '80px 1fr 110px 130px 120px';
const ago = (s) => s == null ? '—' : s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;

function renderCrew() {
  const seated = CREW.filter((c) => c.scoutId);
  const live = seated.filter((c) => c.connected);
  const empty = CREW.filter((c) => !c.scoutId);
  // "stale" = seated, but the phone has not been heard from in a while
  $('#crewSub').textContent = `${live.length} of 6 phones live`;

  // The two failures need different actions from the lead, so name them apart:
  // app closed means go tell them to reopen it; gone quiet means check their wifi.
  const problems = [];
  if (empty.length) {
    problems.push(`${empty.map((c) => c.seat.toUpperCase()).join(', ')} — nobody seated, those robots are unwatched`);
  }
  for (const c of seated) {
    const who = `${String(c.scoutId).toUpperCase()} on ${c.seat.toUpperCase()}`;
    if (!c.connected) problems.push(`${who} — app is not open on their phone`);
    else if (c.lastSeenSec != null && c.lastSeenSec > 180) problems.push(`${who} — gone quiet ${ago(c.lastSeenSec)}, check their wifi`);
    else if (c.lastMatchAgoSec != null && c.lastMatchAgoSec > 25 * 60) problems.push(`${who} — nothing logged in ${ago(c.lastMatchAgoSec)}`);
  }
  $('#crewAlert').innerHTML = problems.length
    ? `<div class="callout" style="margin:0 0 4px"><div class="h">GO TALK TO SOMEONE</div>
       ${problems.map((p) => `<div class="b">${esc(p)}</div>`).join('')}</div>`
    : `<div class="callout" style="margin:0 0 4px;border-color:var(--green-border);background:rgba(52,168,106,.07)">
       <div class="h" style="color:var(--green-soft)">ALL SIX STATIONS COVERED</div>
       <div class="b">Every robot has a scout and every phone is reporting.</div></div>`;

  $('#crewHead').style.gridTemplateColumns = CREW_COLS;
  $('#crewHead').innerHTML = `<span>STATION</span><span>SCOUT</span><span class="num">PHONE</span>
    <span class="num">LAST HEARD</span><span class="num">LAST MATCH</span>`;
  $('#crewBody').innerHTML = CREW.map((c) => {
    const side = c.seat.startsWith('red') ? 'var(--red-label)' : 'var(--blue-label)';
    const ok = c.connected;
    return `<div class="r" style="grid-template-columns:${CREW_COLS}">
      <span style="color:${side};font:800 12px Barlow,sans-serif;letter-spacing:.1em">${c.seat.replace(/(\d)/, ' $1').toUpperCase()}</span>
      <span>${c.scoutId ? esc(String(c.scoutId).toUpperCase()) : '<span style="color:var(--t6)">nobody seated</span>'}
        ${c.scoutId ? `<button class="x" data-unseat="${c.seat}" style="margin-left:8px">FREE</button>` : ''}</span>
      <span class="num" style="color:${ok ? 'var(--green-soft)' : 'var(--red-alert)'};font:800 10.5px Barlow,sans-serif;letter-spacing:.1em">
        ${c.scoutId ? (ok ? 'LIVE' : 'NOT SEEN') : '—'}</span>
      <span class="num">${ago(c.lastSeenSec)}</span>
      <span class="num">${c.lastMatch ? esc(shortCode(c.lastMatch)) + ' · ' + ago(c.lastMatchAgoSec) : '—'}</span>
    </div>`;
  }).join('');

  for (const b of $$('[data-unseat]')) b.onclick = async () => {
    await net.api('/api/unseat', { method: 'POST', body: JSON.stringify({ seat: b.dataset.unseat }) }).catch(() => {});
    refresh();
  };

  // which robot in the current match has nobody on it
  const ms = (STATE && STATE.matches) || [];
  const m = ms.find((x) => x.status === 'On field') || ms.find((x) => !x.breakdown);
  $('#crewMatch').innerHTML = m ? ['red', 'blue'].map((side) =>
    `<div class="r" style="grid-template-columns:70px repeat(3,1fr)">
      <span style="color:${side === 'red' ? 'var(--red-label)' : 'var(--blue-label)'};font:800 11px Barlow,sans-serif;letter-spacing:.12em">${side.toUpperCase()}</span>
      ${[1, 2, 3].map((n) => {
        const team = (m[side] || [])[n - 1];
        const c = CREW.find((x) => x.seat === side + n) || {};
        const watched = c.scoutId && c.connected;
        return `<span style="opacity:${watched ? 1 : .55}">${team || '—'}
          <span class="hint" style="display:block;color:${watched ? 'var(--t5)' : 'var(--red-alert)'}">
          ${watched ? esc(String(c.scoutId).toUpperCase()) : 'UNWATCHED'}</span></span>`;
      }).join('')}
    </div>`).join('') : '<div class="empty">No upcoming match.</div>';

  const cov = ANALYTICS ? ANALYTICS.coverage : { pct: 0, robotsScouted: 0, robotsExpected: 0 };
  $('#crewCoverage').innerHTML =
    `<div class="kv"><span>robots scouted</span><b>${cov.robotsScouted} / ${cov.robotsExpected}</b></div>
     <div class="kv"><span>coverage</span><b>${cov.pct}%</b></div>
     <div class="kv"><span>flagged</span><b>${((STATE && STATE.flags) || []).length}</b></div>`;

  $('#crewSwaps').innerHTML = SEATLOG.length ? SEATLOG.slice(0, 6).map((e) => `
    <div class="kv"><span>${esc(e.seat.replace(/(\d)/, ' $1').toUpperCase())}</span>
      <b>${e.from ? esc(String(e.from).toUpperCase()) + ' → ' : ''}${esc(String(e.scoutId).toUpperCase())}</b></div>`).join('')
    : '<div class="hint">nobody has swapped yet</div>';

  const base = net.state.base || location.origin;
  $('#joinMini').innerHTML = `<div class="kv"><span>scouts open</span><b class="mono" style="font-size:11.5px">${esc(base)}/scout</b></div>`;
}

// ═══════════════════════════════════════════════════════ MATCH PREVIEW
// Standard normal CDF (Abramowitz & Stegun 7.1.26). No library on this page.
function normCdf(z) {
  const s = z < 0 ? -1 : 1;
  const x = Math.abs(z) / Math.SQRT2;
  const t = 1 / (1 + 0.3275911 * x);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t
    - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x);
  return 0.5 * (1 + s * y);
}

/** Projected points for one alliance, with the spread of a single match. */
function project(m, side) {
  const lineup = (m && m[side]) || [];
  const teams = lineup.map((t) => (ANALYTICS && ANALYTICS.teams[t]) || null);
  const fuel = teams.reduce((a, t) => a + (t ? t.estimated.avgFuel : 0), 0);
  // matchBand, not band: band is how well we know a team's average, this is how
  // much one match swings. Independent robots, so the variances add.
  const spread = Math.sqrt(teams.reduce((a, t) => a + (t ? (t.estimated.matchBand || 0) ** 2 : 0), 0));
  const band = Math.sqrt(teams.reduce((a, t) => a + (t ? t.estimated.band ** 2 : 0), 0));
  const tower = teams.reduce((a, t) => a + (t ? t.exact.avgTowerPoints : 0), 0);
  const fp = gameRules().fuelPoints;
  const scouted = teams.filter(Boolean).length;
  return { teams, lineup, fuel, band, spread, tower, points: fuel * fp + tower, scouted };
}

/**
 * P(red wins), from the two projections and their single-match spreads.
 *
 * Only the fuel side carries a spread - climb is exact per match but its
 * match-to-match variation is not modelled, and neither are fouls - so this is
 * a lean, not a forecast. It is shown with the margin it came from for exactly
 * that reason.
 */
function winProbability(m) {
  const r = project(m, 'red'), b = project(m, 'blue');
  if (!r.scouted || !b.scouted) return null;
  const sd = Math.sqrt(r.spread ** 2 + b.spread ** 2);
  if (!(sd > 0)) return null;
  return { red: normCdf((r.points - b.points) / sd), margin: r.points - b.points, sd, r, b };
}

function verdictBanner(m, side) {
  const w = winProbability(m);
  if (!w) return `<div class="callout" style="margin:0"><div class="h">NOT ENOUGH DATA</div>
    <div class="b">Some robots on this match have not been scouted yet.</div></div>`;
  const pct = Math.round((side === 'red' ? w.red : 1 - w.red) * 100);
  const lead = side === 'red' ? w.margin : -w.margin;
  const strong = pct >= 50;
  return `<div class="callout" style="margin:0;border-color:${strong ? 'var(--green-border)' : 'var(--line)'};
      background:${strong ? 'rgba(52,168,106,.07)' : 'transparent'}">
    <div class="h" style="color:${strong ? 'var(--green-soft)' : 'var(--t4)'}">${pct}% TO WIN</div>
    <div class="b">${lead >= 0 ? '+' : '−'}${Math.abs(Math.round(lead))} projected points,
      margin of error ±${Math.round(w.sd)}. Fuel is estimated; fouls are not modelled.</div></div>`;
}

function allianceCard(m, side) {
  const pr = project(m, side);
  const { lineup, teams, fuel, tower } = pr;
  const band = Math.round(pr.band);
  const label = side === 'red' ? 'RED' : 'BLUE';
  const th = rpThresholds((STATE && STATE.event && STATE.event.level) || 'regional');
  const rows = lineup.map((t) => {
    const d = teams[lineup.indexOf(t)];
    if (!d) return `<div class="r" style="grid-template-columns:56px 1fr 90px 80px"><span class="tno">${t}</span>
      <span class="nm">not scouted</span><span class="num">—</span><span class="num">—</span></div>`;
    return `<div class="r" style="grid-template-columns:56px 1fr 90px 80px">
      <span class="tno">${t}</span><span class="nm">${esc(d.name || '')}</span>
      <span class="num">${d.estimated.avgFuel} <span class="band">±${d.estimated.band}</span></span>
      <span class="num">${climbCell(d)}</span></div>`;
  }).join('');
  return `<div style="display:flex;flex-direction:column;gap:14px">
    <div class="eyebrow" style="color:${side === 'red' ? 'var(--red-label)' : 'var(--blue-label)'}">
      ${label} · ${esc((m && m.label) || '')}</div>
    ${verdictBanner(m, side)}
    ${autoClashNote(teams)}
    ${defenseNote(m, side, teams)}
    <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
      ${tile('PROJECTED FUEL', Math.round(fuel), `±${band} · Energized at ${th.energized}`,
             fuel >= th.energized ? '' : 'warn')}
      ${tile('TOWER POINTS', Math.round(tower), `Traversal at ${th.traversal}`,
             tower >= th.traversal ? '' : 'warn')}
      ${tile('SUPERCHARGED', fuel >= th.supercharged ? 'YES' : 'NO', `needs ${th.supercharged}`)}
    </div>
    <div class="tbl">
      <div class="hd" style="grid-template-columns:56px 1fr 90px 80px">
        <span>TEAM</span><span>NAME</span><span class="num">FUEL</span><span class="num">CLIMB</span></div>
      ${rows || '<div class="empty">No lineup yet.</div>'}
    </div></div>`;
}
/** Two robots that habitually start in the same zone will meet there. */
function autoClashNote(teams) {
  const byZone = {};
  for (const t of teams) {
    if (!t || !t.observed.startZone) continue;
    (byZone[t.observed.startZone] ||= []).push(t.team);
  }
  const clash = Object.entries(byZone).filter(([, ts]) => ts.length > 1);
  if (!clash.length) return '';
  return clash.map(([zone, ts]) => `<div class="callout" style="margin:0">
    <div class="h">AUTO — ${esc(zone.toUpperCase())}</div>
    <div class="b">${ts.join(' and ')} both usually start ${esc(zone)}. Worth asking before the
      match rather than watching it happen.</div></div>`).join('');
}

/** Has anyone on the other alliance made a habit of defending these robots? */
function defenseNote(m, side, teams) {
  const opp = (m && m[side === 'red' ? 'blue' : 'red']) || [];
  const hits = [];
  for (const t of teams) {
    if (!t) continue;
    for (const [who, n] of Object.entries(t.observed.defendedBy || {})) {
      if (opp.includes(Number(who))) hits.push(`${who} has defended ${t.team} ${n}\u00d7`);
    }
  }
  if (!hits.length) return '';
  return `<div class="callout" style="margin:0"><div class="h">EXPECT DEFENCE</div>
    <div class="b">${hits.slice(0, 4).map(esc).join(' · ')}.</div></div>`;
}

function renderMatchPreview() {
  const ms = (STATE && STATE.matches) || [];
  const m = ms.find((x) => x.status === 'On field')
        || ms.find((x) => x.status === 'Now queuing' || x.status === 'On deck')
        || ms.find((x) => !x.breakdown);
  $('#mvRed').innerHTML = m ? allianceCard(m, 'red') : '<div class="empty">No upcoming match.</div>';
  $('#mvBlue').innerHTML = m ? allianceCard(m, 'blue') : '';
}

// ═════════════════════════════════════════════════════════ TEAM DETAIL
let openTeam = null;
function renderTeamDetail() {
  const t = openTeam && ANALYTICS && ANALYTICS.teams[openTeam];
  if (!t) {
    $('#tdMain').innerHTML = '<div class="empty">Pick a team from the TEAMS tab.</div>';
    $('#tdSide').innerHTML = '';
    return;
  }
  const pit = ((STATE && STATE.pitEntries) || []).find((p) => p.team === openTeam);
  const o = t.observed, e = t.exact, es = t.estimated;
  const lv = t.lovat || {}, lvN = lovatN(t);
  $('#tdMain').innerHTML = `
    <div style="display:flex;align-items:flex-end;gap:14px">
      <div style="font:700 54px/.9 'Barlow Condensed',sans-serif">${t.team}</div>
      <div style="padding-bottom:6px"><div style="font:600 14px Barlow,sans-serif;color:var(--t1)">${esc(t.name || '')}</div>
        <div class="hint">${t.matchesScouted} matches scouted · ${e.matchesWithOfficial} with official results${recordLine(e)}</div></div>
    </div>
    <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
      ${tile('FUEL / MATCH', es.avgFuel, `± ${es.band} · estimated`)}
      ${tile('BEST CLIMB', e.bestClimb === 'None' ? '—' : e.bestClimb.replace('Level', 'L'),
             `${Math.round(e.climbRate[e.bestClimb] || 0)}% of matches · exact`)}
      ${tile('TOWER PTS', e.avgTowerPoints, `auto climb ${e.autoClimbRate ?? 0}%`)}
      ${tile('RELIABILITY', `${Math.round(100 - o.diedRate - o.noShowRate)}%`,
             `died ${Math.round(o.diedRate)}% · no-show ${Math.round(o.noShowRate)}%`,
             (o.diedRate + o.noShowRate) > 20 ? 'warn' : '')}
      ${tile('EPA', t.epa.epa ?? '—', t.epa.epa == null ? 'statbotics unavailable'
             : `auto ${t.epa.auto ?? '—'} · teleop ${t.epa.teleop ?? '—'} · statbotics`)}
      ${tile('OPR', e.opr ?? '—', e.rank ? `rank ${e.rank} · exact` : 'no rankings yet')}
    </div>
    <div class="tbl"><div class="cap"><span class="t">WHAT SCOUTS SAW</span>
      <span class="n">yes/no observations — the reliable kind</span></div>
      <div style="padding:6px 16px 12px">
        <div class="kv"><span>stockpiles through an inactive shift</span><b>${Math.round(o.stockpileRate)}%</b></div>
        <div class="kv"><span>wasted fuel into a dead hub</span><b>${o.wastedFuelPct == null ? '—' : Math.round(o.wastedFuelPct) + '%'}</b></div>
        <div class="kv"><span>feeds a partner</span><b>${Math.round(o.feedRate)}%${o.feedSecs ? ` · ${o.feedSecs}s/match` : ''}</b></div>
        <div class="kv"><span>defence</span><b>${o.defenseSecs ? `${o.defenseSecs}s/match` : 'none seen'}</b></div>
        <div class="kv"><span>defends</span><b>${teamCounts(o.defenseAgainst)}</b></div>
        <div class="kv"><span>defended by</span><b>${teamCounts(o.defendedBy)}</b></div>
        <div class="kv"><span>starts</span><b>${o.startZone
          ? `${o.startZone.toUpperCase()} · ${Math.round(o.startZonePct)}%` : '—'}</b></div>
        <div class="kv"><span>auto did nothing</span><b>${o.autoFailRate
          ? Math.round(o.autoFailRate) + '%' : 'never seen'}</b></div>
        <div class="kv"><span>lots of fouls</span><b>${o.foulRate
          ? Math.round(o.foulRate) + '%' : 'never seen'}</b></div>
        <div class="kv"><span>driver</span><b>${o.driver ?? '—'} / 5</b></div>
        <div class="kv"><span>average preload</span><b>${o.avgPreload ?? 'not asked'}</b></div>
      </div></div>
    ${lvN ? `
    <div class="tbl"><div class="cap"><span class="t">FROM LOVAT</span>
      <span class="n">other teams' scouts — not ours, and not verified</span></div>
      <div style="padding:6px 16px 12px">
        <div class="kv"><span>matches uploaded</span><b>${lvN} · ${lv.scouters || '?'} scout${lv.scouters === 1 ? '' : 's'}</b></div>
        <div class="kv"><span>fuel / match</span><b>${lv.avgFuel ?? '—'}</b></div>
        <div class="kv"><span>fuel / second</span><b>${lv.fuelPerSec ?? '—'}</b></div>
        <div class="kv"><span>accuracy</span><b>${lv.accuracy == null ? '—' : lv.accuracy}</b></div>
        <div class="kv"><span>best climb seen</span><b>${esc(lv.bestClimb || '—')}${
          lv.autoClimbRate == null ? '' : ` · auto ${Math.round(lv.autoClimbRate)}%`}</b></div>
        <div class="kv"><span>driver</span><b>${lv.driver ?? '—'}</b></div>
        <div class="kv"><span>feeding</span><b>${lv.feedSecs == null ? '—' : lv.feedSecs + 's/match'}</b></div>
        <div class="kv"><span>defence</span><b>${lv.defenseSecs == null ? '—' : lv.defenseSecs + 's/match'}${
          lv.defenseEffectiveness == null ? '' : ` · effect ${lv.defenseEffectiveness}`}</b></div>
        <div class="kv"><span>roles</span><b>${teamRoles(lv.roles)}</b></div>
        ${(lv.unmatched || []).length ? `<div class="hint" style="margin-top:6px">${
          lv.unmatched.length} row${lv.unmatched.length > 1 ? 's' : ''} we could not place on our
          schedule (${esc(lv.unmatched.join(', '))}) — counted here, not joined to a match.</div>` : ''}
      </div></div>` : ''}
    <div class="tbl"><div class="cap"><span class="t">WHAT THE NOTES ADD UP TO</span>
      <span class="n">generated — a reading of the notes below, not a measurement</span></div>
      <div style="padding:6px 16px 12px" id="aiNotesBody"></div>
      <div style="padding:0 16px 12px"><button id="aiNotesGo"
        style="padding:8px 12px;border-radius:8px;background:transparent;border:1px solid var(--btn);
        color:var(--t4);font:700 10.5px Barlow,sans-serif;letter-spacing:.12em;cursor:pointer"
        >SUMMARISE THE NOTES</button></div></div>
    <div class="tbl"><div class="cap"><span class="t">WHAT SCOUTS WROTE</span>
      <span class="n">${t.notes.length ? `${t.notes.length} note${t.notes.length > 1 ? 's' : ''}` : 'nothing typed'}</span></div>
      <div style="padding:6px 16px 12px">
        ${t.notes.length ? t.notes.map((nt) => `
          <div class="kv" style="align-items:flex-start">
            <span style="flex:1">${esc(nt.note)}</span>
            <b style="white-space:nowrap">${esc(shortCode(matchLabel(nt.matchKey)))} · ${esc(nt.scoutId || '?')}</b>
          </div>`).join('')
          : '<div class="hint">Scouts can type a note on the after-the-buzzer screen.</div>'}
        ${(lv.notes || []).map((nt) => `
          <div class="kv" style="align-items:flex-start;opacity:.78">
            <span style="flex:1">${esc(nt.note)}</span>
            <b style="white-space:nowrap">${esc(nt.match || '?')} · ${esc(nt.scouter || '?')} · lovat</b>
          </div>`).join('')}
      </div></div>`;
  const team = openTeam;
  peekAi('#aiNotesBody', `notes/${team}`, {}, 'Not summarised yet.');
  wireAi('#aiNotesGo', '#aiNotesBody', `notes/${team}`, () => ({}), 'Not summarised yet.');
  const p = (pit && pit.payload) || null;
  $('#tdSide').innerHTML = `<div class="eyebrow">FROM THE PIT</div>` + (p ? `
    <div class="kv"><span>drivetrain</span><b>${esc(p.drivetrain || '—')}</b></div>
    <div class="kv"><span>shooter</span><b>${esc(p.shooter || '—')}</b></div>
    <div class="kv"><span>claims climb</span><b>${esc(p.maxClimb || '—')}</b></div>
    <div class="kv"><span>can stockpile</span><b>${esc(p.stockpile || '—')}</b></div>
    <div class="kv"><span>ground pickup</span><b>${esc(p.groundPickup || '—')}</b></div>
    <div class="kv"><span>weight</span><b>${esc(p.weight || '—')}</b></div>
    <div style="margin-top:8px" class="hint">${esc(p.autos || '')}</div>
    <div style="margin-top:6px" class="hint">${esc(p.notes || '')}</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">
      ${(p.photos || []).map((id) => `<img src="/api/photo/${id}" style="width:88px;height:88px;object-fit:cover;border-radius:8px;border:1px solid var(--btn)">`).join('')}
    </div>`
    : '<div class="hint">Not pit scouted yet.</div>');
}

// ══════════════════════════════════════════════════════════════════ AI
/**
 * Generated text, and how it is allowed to appear.
 *
 * Three rules the markup enforces rather than merely mentions: it is always
 * labelled with the model that wrote it, it is never rendered where a
 * measurement goes, and it is never fetched without a button press — opening a
 * page must not spend the team's API credit. A peek reads the hub's cache and
 * stops there.
 */
async function aiCall(path, body) {
  try {
    return await net.api('/api/ai/' + path,
                         { method: 'POST', body: JSON.stringify(body || {}) });
  } catch (e) {
    if (e.locked) return { configured: true, text: null, reason: 'strategy passcode required' };
    return { configured: true, text: null, reason: 'hub unreachable' };
  }
}

function aiBlock(res, idle) {
  if (!res) return `<div class="hint">${idle}</div>`;
  if (res.configured === false) {
    return '<div class="hint">No AI model chosen — pick one in Setup on the hub machine.</div>';
  }
  if (res.text) {
    const when = res.at ? new Date(res.at * 1000).toLocaleTimeString() : '';
    return `<div style="white-space:pre-wrap;font:400 12.5px/1.55 Barlow,sans-serif;color:var(--t1)"
      >${esc(res.text)}</div>
      <div class="hint" style="margin-top:8px">generated by ${esc(res.model || res.provider || 'the model')}${
        when ? ` · ${esc(when)}` : ''}${res.cached ? ' · cached' : ''} — read it against the numbers above</div>`;
  }
  if (res.peek) {
    return `<div class="hint">${res.stale ? 'The data moved since this was last written.' : idle}</div>`;
  }
  return `<div class="hint">${esc(res.reason || 'unavailable')}</div>`;
}

function wireAi(btnSel, bodySel, path, bodyFn, idle) {
  const btn = $(btnSel);
  if (!btn) return;
  btn.onclick = async () => {
    const label = btn.textContent;
    btn.textContent = 'THINKING…'; btn.disabled = true;
    $(bodySel).innerHTML = aiBlock(await aiCall(path, { ...bodyFn(), force: true }), idle);
    btn.textContent = label; btn.disabled = false;
  };
}

async function peekAi(bodySel, path, body, idle) {
  const el = $(bodySel);
  if (!el) return;
  el.innerHTML = `<div class="hint">${idle}</div>`;
  el.innerHTML = aiBlock(await aiCall(path, { ...(body || {}), peek: true }), idle);
}

function wireAsk() {
  const box = $('#askBox'), go = $('#askGo');
  if (!box || !go) return;
  const ask = async () => {
    const question = box.value.trim();
    if (!question) return;
    go.textContent = 'THINKING…'; go.disabled = true;
    $('#askOut').innerHTML = aiBlock(await aiCall('ask', { question }), '');
    go.textContent = 'ASK'; go.disabled = false;
  };
  go.onclick = ask;
  box.onkeydown = (ev) => { if (ev.key === 'Enter') ask(); };
}

// ═══════════════════════════════════════════════════════════════ SEATS
const SEAT_KEYS = ['red1', 'red2', 'red3', 'blue1', 'blue2', 'blue3'];
function renderSeats() {
  const seats = (STATE && STATE.seats) || {};
  const ms = ((STATE && STATE.matches) || []).filter((m) => !m.breakdown).slice(0, 4);
  const cols = `90px repeat(6, 1fr)`;
  $('#seatHead').style.gridTemplateColumns = cols;
  $('#seatHead').innerHTML = '<span>MATCH</span>' +
    SEAT_KEYS.map((k) => `<span>${k.replace(/(\d)/, ' $1').toUpperCase()}</span>`).join('');
  $('#seatBody').innerHTML = ms.map((m) => `
    <div class="r" style="grid-template-columns:${cols}">
      <span class="tno" style="font-size:15px">${esc(shortCode(m.label))}</span>
      ${SEAT_KEYS.map((k) => {
        const side = k.startsWith('red') ? 'red' : 'blue';
        const idx = Number(k.slice(-1)) - 1;
        const team = (m[side] || [])[idx];
        const who = seats[k];
        return `<span style="color:${side === 'red' ? 'var(--red-label)' : 'var(--blue-label)'}">
          ${team || '—'}<span class="hint" style="display:block">${who ? esc(String(who.scoutId).toUpperCase()) : 'nobody'}</span></span>`;
      }).join('')}
    </div>`).join('') || '<div class="empty">No upcoming matches.</div>';
  $('#seatSub').textContent = `${Object.keys(seats).length} of 6 stations claimed`;

  // Who is sitting where and how much they have logged - no quality score.
  const rc = '1fr 120px';
  const roster = CREW.filter((c) => c.scoutId);
  $('#rosterHead').style.gridTemplateColumns = rc;
  $('#rosterHead').innerHTML = '<span>NAME</span><span class="num">STATION</span>';
  $('#rosterBody').innerHTML = roster.map((c) => `
    <div class="r" style="grid-template-columns:${rc}"><span>${esc(String(c.scoutId).toUpperCase())}</span>
      <span class="num">${esc(c.seat.replace(/(\d)/, ' $1').toUpperCase())}</span></div>`).join('')
    || '<div class="empty">Nobody seated yet.</div>';

  const c = ANALYTICS ? ANALYTICS.coverage : { pct: 0, robotsScouted: 0, robotsExpected: 0 };
  $('#seatCoverage').innerHTML =
    `<div class="kv"><span>robots scouted</span><b>${c.robotsScouted} / ${c.robotsExpected}</b></div>
     <div class="kv"><span>coverage</span><b>${c.pct}%</b></div>`;
  const empty = SEAT_KEYS.filter((k) => !seats[k]);
  $('#seatWarn').innerHTML = empty.length
    ? `<div class="callout" style="margin-top:10px"><div class="h">${empty.length} STATION${empty.length > 1 ? 'S' : ''} EMPTY</div>
       <div class="b">${empty.map((k) => k.toUpperCase()).join(', ')} — those robots go unwatched.</div></div>` : '';
}

// ══════════════════════════════════════════════════════════════ SERVER
let DIAG = null;
function renderServer() {
  if (!DIAG) { $('#srvTiles').innerHTML = '<div class="empty">No diagnostics.</div>'; return; }
  const up = DIAG.uptimeSec;
  const upTxt = up > 3600 ? `${Math.floor(up / 3600)}h ${Math.floor(up % 3600 / 60)}m`
    : up > 60 ? `${Math.floor(up / 60)}m` : `${up}s`;
  $('#srvTiles').innerHTML =
    tile('UPTIME', upTxt, `${esc(DIAG.platform)}`) +
    tile('MEMORY', DIAG.memoryMB ? `${DIAG.memoryMB}` : '—', 'MB resident') +
    tile('WRITES / MIN', DIAG.writesPerMin, 'rows accepted') +
    tile('DEVICES', DIAG.sseClients, 'streaming now');
  // A service with no API key is a choice, not a fault - counting those as
  // "not running" turns an unconfigured hub into an alarming one.
  const retrying = DIAG.services.filter((s) => s.status === 'RETRYING').length;
  const idle = DIAG.services.filter((s) => s.status === 'IDLE').length;
  $('#srvHealth').textContent = retrying
    ? `${retrying} retrying`
    : idle ? `${idle} idle · no key` : 'all services healthy';
  const sc = '1fr 110px';
  $('#srvHead').style.gridTemplateColumns = sc;
  $('#srvHead').innerHTML = '<span>SERVICE</span><span class="num">STATUS</span>';
  $('#srvBody').innerHTML = DIAG.services.map((s) => `
    <div class="r" style="grid-template-columns:${sc}">
      <span>${esc(s.name)}<span class="hint" style="display:block">${esc(s.detail)}</span></span>
      <span class="num" style="color:${s.status === 'RUNNING' ? 'var(--green-soft)' : s.status === 'RETRYING' ? 'var(--amber)' : 'var(--t5)'};
        font:800 10.5px Barlow,sans-serif;letter-spacing:.1em">${s.status}</span></div>`).join('');
  $('#srvLog').innerHTML = DIAG.log.map((l) => `
    <div style="padding:7px 16px;border-bottom:1px solid var(--row);font:400 11.5px 'JetBrains Mono',monospace;color:var(--t2)">
      <span style="color:${l.level === 'error' ? 'var(--red-alert)' : l.level === 'warn' ? 'var(--amber)' : 'var(--t5)'}">${l.level.toUpperCase().padEnd(5)}</span>
      ${esc(new Date(l.at * 1000).toLocaleTimeString())} ${esc(l.msg)}</div>`).join('')
    || '<div class="empty">Nothing logged.</div>';
  $('#srvNet').innerHTML = DIAG.addresses.map((u) =>
    `<div class="kv"><span class="mono" style="font-size:11.5px">${esc(u)}</span><b>open</b></div>`).join('')
    + `<div class="kv"><span>host</span><b class="mono" style="font-size:11.5px">${esc(DIAG.host)}</b></div>`;
  $('#srvData').innerHTML =
    `<div class="kv"><span>python</span><b>${esc(DIAG.python)}</b></div>
     <div class="kv"><span>stations claimed</span><b>${Object.keys(DIAG.seats || {}).length} / 6</b></div>
     <div style="margin-top:10px;display:flex;flex-direction:column;gap:7px;
                 font:800 11px Barlow,sans-serif;letter-spacing:.1em">
       <a href="/api/export" download="scouting-export.json">DOWNLOAD FULL EXPORT · JSON</a>
       <a href="/api/export.csv?table=teams">TEAM SUMMARY · CSV</a>
       <a href="/api/export.csv?table=scout">EVERY SCOUT ENTRY · CSV</a>
       <a href="/api/export.csv?table=pit">PIT SCOUTING · CSV</a>
       <a href="/picklist/print" target="_blank">PRINTABLE PICKLIST</a>
       <a href="/picklist/print?list=second" target="_blank">PRINTABLE SECOND-PICK LIST</a>
     </div>
     <div class="hint" style="margin-top:8px">JSON is the one that imports back in.
       CSV is for a spreadsheet, and the printed picklist is the paper fallback for
       alliance selection.</div>`;
}

// ══════════════════════════════════════════════════════════════ refresh
function renderAll() {
  $('#evTitle').textContent = STATE && STATE.event && STATE.event.name
    ? `${STATE.event.name.toUpperCase()} · STRATEGY` : 'REBUILT · STRATEGY';
  const played = STATE ? (STATE.matches || []).filter((m) => m.breakdown).length : 0;
  const total = STATE ? (STATE.matches || []).length : 0;
  const ago = net.state.lastSync ? Math.round((Date.now() - net.state.lastSync) / 1000) : null;
  $('#evSub').textContent = STATE
    ? `QUAL ${played} / ${total} · ${(STATE.teams || []).length} TEAMS${ago != null ? ` · SYNC ${ago}s` : ''}`
    : 'NO EVENT · 0 TEAMS';
  renderLive(); renderTeams(); renderPicklist(); renderHealth();
  renderMatchPreview(); renderTeamDetail(); renderSeats(); renderServer(); renderCrew();
}

async function refresh() {
  try { STATE = await net.api('/api/state'); db.cacheSet('state', STATE); }
  catch { STATE = await db.cacheGet('state'); }
  try { ANALYTICS = await net.api('/api/analytics'); db.cacheSet('analytics', ANALYTICS); }
  catch { ANALYTICS = await db.cacheGet('analytics'); }
  try { CONFIG = await net.api('/api/config'); } catch {}
  try { DIAG = await net.api('/api/diag'); } catch {}
  try { CREW = await net.api('/api/crew'); } catch { CREW = CREW || []; }
  try { SEATLOG = await net.api('/api/seatlog'); } catch { SEATLOG = SEATLOG || []; }
  renderAll();
}

async function main() {
  await loadRules();
  CONFIG = await net.start();
  ourTeam = Number((CONFIG && CONFIG.ourTeam) || 0) || null;

  const TABS = ['crew', 'live', 'teams', 'picklist', 'health', 'seats', 'server', 'match', 'team'];
  const goTab = (name) => {
    for (const x of $$('#tabs button')) x.classList.toggle('on', x.dataset.tab === name);
    for (const t of TABS) { const el = $(`#t-${t}`); if (el) el.classList.toggle('hide', t !== name); }
    if (name === 'picklist') renderEditBar();
  };
  window.__goTab = goTab;
  for (const b of $$('#tabs button')) b.onclick = () => goTab(b.dataset.tab);

  renderWeights();
  $('#pinLock').onclick = async () => {
    localStorage.removeItem('strategyToken');
    await loadPicklistState();
    renderPicklist(); renderWeights();
  };
  await loadPicklistState();
  await refresh();
  for (const t of ['nexus', 'results', 'scout', 'alliances', 'calibration', 'matchStatus', 'seats', 'matchStart', 'lovat']) net.on(t, refresh);
  wireAsk();
  setInterval(refresh, 30000);
  // the crew board is the lead's live view; keep it fresher than the rest
  setInterval(async () => {
    try {
      CREW = await net.api('/api/crew');
      SEATLOG = await net.api('/api/seatlog');
      renderCrew();
    } catch {}
  }, 7000);
}

main().catch((e) => { console.error(e); $('#evSub').textContent = 'FAILED: ' + e.message; });
