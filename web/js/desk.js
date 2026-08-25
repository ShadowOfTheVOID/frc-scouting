// Strategy dashboard. Read-only; runs on any device on the network.
// Layout and every literal value come from design/Computer *.dc.html.

import * as db from './db.js';
import * as net from './net.js';
import { loadRules, rpThresholds } from './game2026.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

let STATE = null, ANALYTICS = null, CONFIG = null, ourTeam = null;
const DNP = new Set(JSON.parse(localStorage.getItem('dnp') || '[]'));
const WEIGHTS = { climb: 30, reliability: 25, stockpile: 15, fuel: 20, defense: 10 };

const RANK_COLS = '56px minmax(120px,1fr) 84px 74px 52px 44px 64px';
const ALL_COLS  = '56px minmax(160px,1fr) 84px 74px 60px 62px 62px 56px 52px 64px';

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
function tile(k, v, c, alert) {
  return `<div class="tile${alert ? ' alert' : ''}"><div class="k">${k}</div>
    <div class="v">${v}</div><div class="c${alert === 'warn' ? ' warn' : ''}">${c}</div></div>`;
}
const shortCode = (label) => {
  const m = String(label || '').match(/(\w)\w*\s*(\d+)/);
  return m ? `${m[1].toUpperCase()}${m[2]}` : (label || '—');
};

function scoutTrust(team) {
  if (!ANALYTICS) return 1;
  // a team is only as trustworthy as the scouts who watched it
  const s = ANALYTICS.scouts || [];
  if (!s.length) return 1;
  return s.reduce((a, x) => a + x.reliability, 0) / s.length;
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

  // heads up: a station that has gone quiet is the failure that ruins a dataset
  const dark = ANALYTICS ? (ANALYTICS.scouts || []).filter((s) => s.missedMatches > 0) : [];
  if (dark.length) {
    $('#headsup').classList.remove('hide');
    $('#headsupBody').textContent =
      `${dark.map((d) => d.scoutId).join(', ')} ${dark.length === 1 ? 'has' : 'have'} missed matches — those robots are unscouted.`;
  } else $('#headsup').classList.add('hide');

  const cov = ANALYTICS ? ANALYTICS.coverage : { robotsScouted: 0, pct: 0 };
  const scouts = ANALYTICS ? ANALYTICS.scouts.length : 0;
  const flags = (STATE && STATE.flags || []).length;
  const th = rpThresholds((STATE && STATE.event && STATE.event.level) || 'regional');
  $('#tiles').innerHTML =
    tile('SCOUTED', cov.robotsScouted, `${cov.pct}% coverage`) +
    tile('MATCHES', `${played}<small>/${matches.length || 0}</small>`, 'played') +
    tile('SCOUTS', scouts, dark.length ? `${dark.length} station dark` : 'all reporting', dark.length ? 'warn' : '') +
    tile('FLAGGED', flags, 'need reconcile', flags ? true : '');

  const teams = ANALYTICS ? Object.values(ANALYTICS.teams).filter((t) => t.matchesScouted) : [];
  teams.sort((a, b) => b.estimated.avgFuel - a.estimated.avgFuel);
  $('#rankHead').style.gridTemplateColumns = RANK_COLS;
  $('#rankHead').innerHTML =
    `<span>TEAM</span><span>NAME</span><span class="num">FUEL/MATCH</span><span class="num">CLIMB</span>
     <span class="num">AUTO</span><span class="num">RP</span><span class="num">TRUST</span>`;
  $('#rankBody').innerHTML = teams.slice(0, 12).map((t) => `
    <div class="r" style="grid-template-columns:${RANK_COLS}">
      <span class="tno">${t.team}</span>
      <span class="nm">${esc(t.name || '')}</span>
      <span class="num">${t.estimated.avgFuel} <span class="band">±${t.estimated.band}</span></span>
      <span class="num">${climbCell(t)}</span>
      <span class="num">${t.exact.autoClimbs || 0}</span>
      <span class="num">${t.exact.avgRP ?? '—'}</span>
      <span class="num">${trustCell(scoutTrust(t.team))}</span>
    </div>`).join('') || '<div class="empty">No scouted teams yet.</div>';

  renderPickMini();
}

// ════════════════════════════════════════════════════════════════ TEAMS
let sortKey = 'fuel', sortDir = -1;
const ALL_HEAD = [
  ['team', 'TEAM', 0], ['name', 'NAME', 0], ['fuel', 'FUEL/MATCH', 1], ['climb', 'CLIMB', 1],
  ['tower', 'TOWER', 1], ['stock', 'STOCK', 1], ['waste', 'WASTED', 1],
  ['died', 'DIED', 1], ['drv', 'DRIVER', 1], ['n', 'MATCHES', 1],
];
function sortVal(t, k) {
  switch (k) {
    case 'team': return t.team; case 'name': return 0;
    case 'fuel': return t.estimated.avgFuel;
    case 'climb': return { Level3: 3, Level2: 2, Level1: 1, None: 0 }[t.exact.bestClimb] || 0;
    case 'tower': return t.exact.avgTowerPoints;
    case 'stock': return t.observed.stockpileRate;
    case 'waste': return -(t.observed.wastedFuelPct ?? 999);
    case 'died': return -t.observed.diedRate;
    case 'drv': return t.observed.driver ?? 0;
    case 'n': return t.matchesScouted;
    default: return 0;
  }
}
function renderTeams() {
  if (!ANALYTICS) return;
  const rows = Object.values(ANALYTICS.teams).filter((t) => t.matchesScouted);
  rows.sort((a, b) => (sortVal(a, sortKey) - sortVal(b, sortKey)) * sortDir);
  $('#allHead').style.gridTemplateColumns = ALL_COLS;
  $('#allHead').innerHTML = ALL_HEAD.map(([k, l, n]) =>
    `<span data-k="${k}" class="${n ? 'num' : ''}">${l}${sortKey === k ? (sortDir < 0 ? ' ▾' : ' ▴') : ''}</span>`).join('');
  $('#allBody').innerHTML = rows.map((t) => `
    <div class="r" style="grid-template-columns:${ALL_COLS}${t.team === ourTeam ? ';box-shadow:inset 0 0 0 1px var(--red-ring)' : ''}">
      <span class="tno">${t.team}</span>
      <span class="nm">${esc(t.name || '')}</span>
      <span class="num">${t.estimated.avgFuel} <span class="band">±${t.estimated.band}</span></span>
      <span class="num">${climbCell(t)}</span>
      <span class="num">${t.exact.avgTowerPoints}</span>
      <span class="num">${Math.round(t.observed.stockpileRate)}%</span>
      <span class="num">${t.observed.wastedFuelPct == null ? '—' : Math.round(t.observed.wastedFuelPct) + '%'}</span>
      <span class="num">${Math.round(t.observed.diedRate)}%</span>
      <span class="num">${t.observed.driver ?? '—'}</span>
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
  const e = t.exact, o = t.observed, s = t.estimated;
  const climb = ({ Level3: 1, Level2: 0.65, Level1: 0.3, None: 0 })[e.bestClimb] || 0;
  const l3 = (e.climbRate.Level3 || 0) / 100;
  const rel = 1 - Math.min(1, (o.diedRate + o.noShowRate) / 100);
  const stock = (o.stockpileRate || 0) / 100;
  const def = (o.defense || 0) / 5;
  const maxFuel = Math.max(1, ...Object.values(ANALYTICS.teams).map((x) => x.estimated.avgFuel));
  return WEIGHTS.climb * (climb * .6 + l3 * .4) + WEIGHTS.reliability * rel +
         WEIGHTS.stockpile * stock + WEIGHTS.fuel * (s.avgFuel / maxFuel) + WEIGHTS.defense * def;
}
function takenTeams() {
  const out = new Set();
  for (const a of (STATE && STATE.alliances) || []) for (const t of a || []) if (t) out.add(Number(t));
  return out;
}
function ranked() {
  if (!ANALYTICS) return [];
  return Object.values(ANALYTICS.teams).filter((t) => t.matchesScouted)
    .map((t) => ({ t, s: score(t) })).sort((a, b) => b.s - a.s);
}
function renderPickMini() {
  const taken = takenTeams();
  $('#pkMini').innerHTML = ranked().slice(0, 8).map(({ t, s }, i) => `
    <div class="pk ${i === 0 ? 'top' : ''} ${taken.has(t.team) ? 'taken' : ''} ${DNP.has(t.team) ? 'dnp' : ''}">
      <span class="i">${i + 1}</span><span class="n">${t.team}</span>
      <span class="nm">${esc(t.name || '')}</span><span class="s">${Math.round(s)}</span>
    </div>`).join('') || '<div class="empty">Nothing to rank yet.</div>';
  $('#pkHint').textContent = taken.size ? `${taken.size} taken` : 'drag to reorder';
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
    DNP.clear();
    for (const n of pl.dnp || []) DNP.add(Number(n));
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
      body: JSON.stringify({ weights: WEIGHTS, dnp: [...DNP] }) });
  } catch { /* stays local until the hub is back */ }
}

function renderPicklist() {
  renderEditBar();
  const taken = takenTeams();
  $('#pkStatus').textContent = taken.size
    ? `${taken.size} team${taken.size > 1 ? 's' : ''} already taken — crossed off live`
    : 'alliance selection not started';
  $('#pkFull').innerHTML = ranked().map(({ t, s }, i) => `
    <div class="pk ${i === 0 ? 'top' : ''} ${taken.has(t.team) ? 'taken' : ''} ${DNP.has(t.team) ? 'dnp' : ''}"
         style="margin:0;border-bottom:1px solid var(--row)">
      <span class="i">${i + 1}</span><span class="n">${t.team}</span>
      <span class="nm">${esc(t.name || '')} — ${climbCell(t)} · ${t.estimated.avgFuel}±${t.estimated.band} fuel · stock ${Math.round(t.observed.stockpileRate)}%</span>
      <span class="s">${Math.round(s)}</span>
      ${CAN_EDIT ? `<button class="x" data-dnp="${t.team}">${DNP.has(t.team) ? 'UN-DNP' : 'DNP'}</button>` : ''}
    </div>`).join('') || '<div class="empty">Nothing to rank yet.</div>';
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
}
function renderWeights() {
  $('#weights').innerHTML = Object.entries(WEIGHTS).map(([k, v]) => `
    <div class="wt"><div class="lb"><span>${k.toUpperCase()}</span><span id="w-${k}">${v}</span></div>
    <input type="range" min="0" max="50" value="${v}" data-w="${k}"${CAN_EDIT ? '' : ' disabled'}></div>`).join('')
    + (CAN_EDIT ? '' : '<div class="hint">Unlock editing on the draft board to change these.</div>');
  for (const el of $$('[data-w]')) el.oninput = () => {
    WEIGHTS[el.dataset.w] = Number(el.value);
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
  $('#healthTiles').innerHTML =
    tile('COVERAGE', `${c.pct}%`, `${c.robotsScouted} of ${c.robotsExpected} robots`) +
    tile('SCOUTS', ANALYTICS.scouts.length, 'reporting') +
    tile('FLAGGED', flags.length, 'need reconcile', flags.length ? true : '') +
    tile('CALIBRATED', (CONFIG && CONFIG.multipliersFittedFrom) || 0, 'windows fitted');

  $('#scoutHead').style.gridTemplateColumns = SCOUT_COLS;
  $('#scoutHead').innerHTML = `<span>SCOUT</span><span class="num">MATCHES</span>
    <span class="num">MISSED</span><span class="num">TRUST</span>`;
  $('#scoutBody').innerHTML = ANALYTICS.scouts.map((s) => `
    <div class="r" style="grid-template-columns:${SCOUT_COLS}">
      <span>${esc(s.scoutId)}</span>
      <span class="num">${s.matches}</span>
      <span class="num">${s.missedMatches}</span>
      <span class="num">${trustCell(s.reliability)}</span>
    </div>`).join('') || '<div class="empty">No scout data yet.</div>';

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
function allianceCard(m, side) {
  const lineup = (m && m[side]) || [];
  const label = side === 'red' ? 'RED' : 'BLUE';
  const teams = lineup.map((t) => (ANALYTICS && ANALYTICS.teams[t]) || null);
  const fuel = teams.reduce((a, t) => a + (t ? t.estimated.avgFuel : 0), 0);
  const band = Math.round(Math.sqrt(teams.reduce((a, t) => a + (t ? t.estimated.band ** 2 : 0), 0)));
  const tower = teams.reduce((a, t) => a + (t ? t.exact.avgTowerPoints : 0), 0);
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
  $('#tdMain').innerHTML = `
    <div style="display:flex;align-items:flex-end;gap:14px">
      <div style="font:700 54px/.9 'Barlow Condensed',sans-serif">${t.team}</div>
      <div style="padding-bottom:6px"><div style="font:600 14px Barlow,sans-serif;color:var(--t1)">${esc(t.name || '')}</div>
        <div class="hint">${t.matchesScouted} matches scouted · ${e.matchesWithOfficial} with official results</div></div>
    </div>
    <div class="tiles">
      ${tile('FUEL / MATCH', es.avgFuel, `± ${es.band} · estimated`)}
      ${tile('BEST CLIMB', e.bestClimb === 'None' ? '—' : e.bestClimb.replace('Level', 'L'),
             `${Math.round(e.climbRate[e.bestClimb] || 0)}% of matches · exact`)}
      ${tile('TOWER PTS', e.avgTowerPoints, `auto climb ${e.autoClimbRate ?? 0}%`)}
      ${tile('RELIABILITY', `${Math.round(100 - o.diedRate - o.noShowRate)}%`,
             `died ${Math.round(o.diedRate)}% · no-show ${Math.round(o.noShowRate)}%`,
             (o.diedRate + o.noShowRate) > 20 ? 'warn' : '')}
    </div>
    <div class="tbl"><div class="cap"><span class="t">WHAT SCOUTS SAW</span>
      <span class="n">yes/no observations — the reliable kind</span></div>
      <div style="padding:6px 16px 12px">
        <div class="kv"><span>stockpiles through an inactive shift</span><b>${Math.round(o.stockpileRate)}%</b></div>
        <div class="kv"><span>wasted fuel into a dead hub</span><b>${o.wastedFuelPct == null ? '—' : Math.round(o.wastedFuelPct) + '%'}</b></div>
        <div class="kv"><span>feeds a partner</span><b>${Math.round(o.feedRate)}%${o.feedSecs ? ` · ${o.feedSecs}s/match` : ''}</b></div>
        <div class="kv"><span>defence</span><b>${o.defenseSecs ? `${o.defenseSecs}s/match` : 'none seen'}</b></div>
        <div class="kv"><span>driver</span><b>${o.driver ?? '—'} / 5</b></div>
        <div class="kv"><span>average preload</span><b>${o.avgPreload ?? '—'}</b></div>
      </div></div>`;
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

  const rc = '1fr 90px 90px 90px';
  $('#rosterHead').style.gridTemplateColumns = rc;
  $('#rosterHead').innerHTML = '<span>NAME</span><span class="num">MATCHES</span><span class="num">MISSED</span><span class="num">TRUST</span>';
  $('#rosterBody').innerHTML = (ANALYTICS ? ANALYTICS.scouts : []).map((s) => `
    <div class="r" style="grid-template-columns:${rc}"><span>${esc(s.scoutId)}</span>
      <span class="num">${s.matches}</span><span class="num">${s.missedMatches}</span>
      <span class="num">${trustCell(s.reliability)}</span></div>`).join('')
    || '<div class="empty">Nobody has scouted yet.</div>';

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
  const bad = DIAG.services.filter((s) => s.status !== 'RUNNING').length;
  $('#srvHealth').textContent = bad ? `${bad} not running` : 'all services healthy';
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
     <div style="margin-top:10px"><a href="/api/export" download="scouting-export.json"
        style="font:800 11px Barlow,sans-serif;letter-spacing:.1em">DOWNLOAD FULL EXPORT</a></div>`;
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
  for (const t of ['nexus', 'results', 'scout', 'alliances', 'calibration', 'matchStatus', 'seats', 'matchStart']) net.on(t, refresh);
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
