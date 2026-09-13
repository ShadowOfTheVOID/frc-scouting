// Strategy dashboard. Read-only; runs on any device on the network.
// Layout and every literal value come from design/Computer *.dc.html.

import * as db from './db.js';
import * as net from './net.js';
import * as chart from './chart.js';
import * as pick from './picklist.js';
import { loadRules, rpThresholds, rules as gameRules } from './game2026.js';
import { every, coalesce } from './timers.js';

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// `red2` as the sign above the chair reads it: RED 2. The lead is matching this
// against a printed sign across the room, so every panel has to spell it the
// same way - the crew alert used to say RED2 while the row under it said RED 1.
const stationLabel = (k) => String(k || '').replace(/(\d)/, ' $1').toUpperCase();

let STATE = null, ANALYTICS = null, CONFIG = null, ourTeam = null;
const DNP = new Set(JSON.parse(localStorage.getItem('dnp') || '[]'));
// Two boards. A first pick is the best robot left; a second pick is the best
// complement to the two we already have, which is a different question and so
// a different weighting - defence and feeding matter far more down there.
const WEIGHTS = { climb: 30, reliability: 25, stockpile: 15, fuel: 20, defense: 10 };
const WEIGHTS2 = { climb: 15, reliability: 30, stockpile: 20, fuel: 15, defense: 35 };
let PICK_MODE = 'first';
const activeWeights = () => (PICK_MODE === 'first' ? WEIGHTS : WEIGHTS2);
// Climb levels in order, for anything that has to compare two of them.
const CLIMB_RANK = { Level3: 3, Level2: 2, Level1: 1, None: 0 };

const RANK_COLS = '56px minmax(120px,1fr) 84px 74px 52px 44px 86px';
const ALL_COLS  = '62px 56px minmax(160px,1fr) 84px 74px 62px 60px 62px 62px 56px 52px 70px 64px';

// ------------------------------------------------------------------ bits
/** TBA's climb for one robot, or a dash for one no result has landed for.
 *
 * NONE and an empty cell are opposite facts. NONE is the field saying this
 * robot stayed on the floor in every match it has played; an empty cell is
 * nobody having told us yet. Rendering the second as the first put a flat
 * NONE beside every opponent in a match not yet played.
 */
function climbCell(t) {
  const best = t.exact.bestClimb;
  if (!best) return '<span class="band">—</span>';
  const rate = Math.round((t.exact.climbRate || {})[best] || 0);
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
  const s = String(label || '');
  // A match KEY rather than a label. `2026demo_qm26` used to come out of the
  // rule below as "26" - the first character of the event key, then the last
  // run of digits - so the crew board's LAST MATCH column and the flag list on
  // HEALTH named no match anybody could find. Both of those are read to decide
  // where to walk.
  const key = s.match(/_([a-z]+)(\d[\w]*)$/i);
  if (key) return (key[1] + key[2]).toUpperCase();
  const m = s.match(/(\w)\w*\s*(\d+)/);
  return m ? `${m[1].toUpperCase()}${m[2]}` : (s || '—');
};

// How far back the heads-up looks. This is the LIVE tab and it is about a
// station that has gone quiet now, not about the whole morning: a hub that
// imported a part-scouted event would otherwise open with every robot in it
// named.
const UNWATCHED_LOOKBACK = 3;

/**
 * Robots that took the field in one of the last few played matches with no
 * scout entry for them.
 *
 * It used to ask whether the team had EVER been scouted, which is a different
 * question and almost always yes: a station that went quiet after Q4 named
 * nobody, and that is the failure this panel exists for. `scouted` on each
 * trend row is per match, which is what the question actually needs.
 */
function unwatchedRobots() {
  if (!ANALYTICS) return [];
  const played = [];
  for (const m of (STATE && STATE.matches) || []) if (m.breakdown) played.push(m.matchKey);
  const recent = new Set(played.slice(-UNWATCHED_LOOKBACK));
  if (!recent.size) return [];
  const out = new Set();
  for (const t of Object.values(ANALYTICS.teams)) {
    for (const r of t.trend || []) {
      if (r.played && r.scouted === false && recent.has(r.matchKey)) out.add(t.team);
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
  // A played match is never "on field", whatever Nexus last said about it: the
  // status comes from a volunteer with a tablet and goes stale, the breakdown
  // comes from the field and does not. Without this the board can spend the
  // afternoon showing a match that finished at lunchtime as the live one.
  const onField = matches.find((m) => m.status === 'On field' && !m.breakdown);
  const queuing = matches.find((m) => !m.breakdown
    && (m.status === 'Now queuing' || m.status === 'On deck'));
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
      + `gone unscouted in the last ${UNWATCHED_LOOKBACK} played matches — check the `
      + `stations on the CREW tab.`;
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
    case 'climb': return CLIMB_RANK[t.exact.bestClimb] || 0;
    case 'tower': return t.exact.avgTowerPoints ?? -1;
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
/** The interesting half of a yes/no rate: which kind, when it was a yes.
 *
 * "beached 40%" tells a strategist nothing they can act on; "beached 40% - on
 * the bump" tells them to send the robot the long way round. `skip` is that
 * column's word for "it did not happen", which the rate beside this already
 * says.
 */
function kinds(map, skip) {
  const rows = Object.entries(map || {}).filter(([k]) => k !== skip && k !== 'N/A');
  if (!rows.length) return '';
  rows.sort((a, b) => b[1] - a[1]);
  return ` <span class="band">${rows.slice(0, 3).map(([k]) => esc(k.toLowerCase().replace(/_/g, ' '))).join(', ')}</span>`;
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
      <span class="num">${t.exact.avgTowerPoints ?? '—'}</span>
      <span class="num">${Math.round(t.observed.stockpileRate)}%</span>
      <span class="num">${t.observed.wastedFuelPct == null ? '—' : Math.round(t.observed.wastedFuelPct) + '%'}</span>
      <span class="num">${Math.round(t.observed.diedRate)}%</span>
      <span class="num">${t.observed.driver ?? '—'}</span>
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
// The formula itself is in web/js/picklist.js, because the sheet the lead
// prints and carries into alliance selection ranks with the same one - and
// when it lived in both files, correcting one of them made the paper and the
// screen disagree about the same robots.
function score(t) {
  return pick.score(t, activeWeights(), maxFuelAcross(ANALYTICS));
}
/**
 * The best average fuel at the event, for normalising one team against it.
 *
 * This used to be computed inside score(), which is called once per team - so
 * ranking N teams built N copies of an N-element array and spread all N into
 * Math.max, for one number that is the same every time. Quadratic, and the
 * spread would eventually hit the argument limit outright on a big event.
 */
let maxFuelFor = null, maxFuelVal = 1;
function maxFuelAcross(an) {
  if (an === maxFuelFor) return maxFuelVal;
  let m = 1;
  for (const t of Object.values(an.teams)) {
    const v = t.estimated.avgFuel;
    if (v > m) m = v;
  }
  maxFuelFor = an; maxFuelVal = m;
  return m;
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
// Which version of the board this tab has seen. The hub bumps it on every
// write and names it in the broadcast, so a tab can tell the echo of its own
// edit from somebody else's.
let PICK_REV = 0;
const saveWeightsSoon = coalesce(
  () => savePicklist(PICK_MODE === 'first' ? { weights: WEIGHTS } : { weights2: WEIGHTS2 }), 400);
const activeOrder = () => (PICK_MODE === 'first' ? ORDER : ORDER2);
function setActiveOrder(v) { if (PICK_MODE === 'first') ORDER = v; else ORDER2 = v; }

function computedRank() {
  if (!ANALYTICS) return [];
  return Object.values(ANALYTICS.teams).filter((t) => t.matchesScouted)
    .map((t) => ({ t, s: score(t) })).sort((a, b) => b.s - a.s)
    .map((r, i) => ({ ...r, was: i + 1 }));
}

// Scoring and sorting every team, from scratch, on every call - and it is
// called at least twice per refresh, plus once per keystroke in the picklist
// search and once per filter toggle. The answer only depends on the analytics
// object, which board is showing, and that board's hand-ordering.
let rankCache = null;
function ranked() {
  const order = activeOrder();
  // The weights are mutated in place by the sliders on this very panel, so they
  // have to be in the key - the object identity alone would never change.
  const key = [PICK_MODE, Object.values(activeWeights()).join(','), order.join(',')].join('|');
  if (rankCache && rankCache.an === ANALYTICS && rankCache.key === key) return rankCache.out;
  const out = rankedUncached(order);
  rankCache = { an: ANALYTICS, key, out };
  return out;
}
function rankedUncached(order) {
  const base = computedRank();
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
  savePicklist(PICK_MODE === 'first' ? { order } : { order2: order });
}
// ------------------------------------------------------------- filters
//
// A view over the board, never the board. Filters only decide which rows this
// screen draws: the score, the saved order and the rank beside each team are
// all computed against the whole list, so a filtered board is the same board
// with rows hidden - not a shorter one that has been renumbered.
//
// They live in localStorage rather than on the hub on purpose. The order is
// shared because everyone has to be reading the same list; who is squinting at
// the L3 climbers right now is nobody else's business, and a filter somebody
// forgot to clear must never travel to the laptop running alliance selection.
const FILTER_OFF = { q: '', climb: 0, rate: 0, minMatches: 0, start: '',
                     hideTaken: false, hideDnp: false, reliable: false,
                     autoWorks: false, noClash: false, defends: false, stockpiles: false };
const FILTERS = { ...FILTER_OFF, ...readFilters() };

function readFilters() {
  try { return JSON.parse(localStorage.getItem('pkFilters') || '{}'); } catch { return {}; }
}
function saveFilters() {
  try { localStorage.setItem('pkFilters', JSON.stringify(FILTERS)); } catch { /* private mode */ }
}
/**
 * The zone our own robot usually starts in, or null if nobody has scouted us.
 *
 * Alliance selection is where auto compatibility gets settled, and the match
 * preview already warns when two robots share a zone. NO AUTO CLASH is that
 * warning moved a day earlier - the point at which you can still pick someone
 * else. Without OUR TEAM set there is nothing to clash with, so the filter is
 * inert rather than wrong.
 */
function ourStartZone() {
  const t = ourTeam && ANALYTICS && ANALYTICS.teams[ourTeam];
  return (t && t.observed.startZone) || null;
}
const filtersOn = () => Object.keys(FILTER_OFF).some((k) =>
  FILTERS[k] !== FILTER_OFF[k] && !(k === 'noClash' && !ourStartZone()));

/** Does this team survive the current filters? `taken` and `ourZone` are passed
    in so a render works each of them out once for the whole board. */
function passesFilters(t, taken, ourZone) {
  const f = FILTERS, o = t.observed, s = t.estimated;
  if (f.hideTaken && taken.has(t.team)) return false;
  if (f.hideDnp && DNP.has(t.team)) return false;
  if (f.minMatches && t.matchesScouted < f.minMatches) return false;
  if (f.climb && (CLIMB_RANK[t.exact.bestClimb] || 0) < f.climb) return false;
  // Fuel per second of active time: volume says how much a robot ends a match
  // with, this says how fast it gets there, and a picklist wants both. It is
  // estimated - it divides a solved number - so it belongs beside the fuel it
  // came from and never above an exact field. No measured rate is not a fast
  // rate, so a robot we cannot time never clears a floor.
  if (f.rate && !(s.cycleRate >= f.rate)) return false;
  if (f.start && o.startZone !== f.start) return false;
  // Broke down or never turned up. Both end the same way for an alliance.
  if (f.reliable && (o.diedRate + o.noShowRate) > 10) return false;
  if (f.autoWorks && (o.autoFailRate || 0) >= 20) return false;
  if (f.noClash && ourZone && o.startZone === ourZone) return false;
  // A rating is a scout's opinion and seconds are a count; either is enough to
  // say this robot will play defence if you ask it to.
  if (f.defends && !((o.defense || 0) >= 3 || (o.defenseSecs || 0) > 0)) return false;
  if (f.stockpiles && (o.stockpileRate || 0) < 50) return false;
  const q = f.q.trim().toLowerCase();
  if (q && !String(t.team).includes(q) && !String(t.name || '').toLowerCase().includes(q)) return false;
  return true;
}

/** Wired once, at startup: the bar is static markup, so re-rendering the board
    under a lead who is mid-word in the search box never takes the caret away. */
function wireFilters() {
  const q = $('#pkSearch');
  if (!q) return;
  // Debounced. renderPicklist re-scores and re-sorts every team and ends in a
  // POST to /api/ai/picklist, and the hub rebuilds the whole event's analytics
  // to answer that - all of it, per character typed into a search box.
  const search = coalesce(() => { saveFilters(); renderPicklist(); }, 200);
  q.oninput = () => { FILTERS.q = q.value; search(); };
  for (const [sel, key] of [['#pkClimb', 'climb'], ['#pkRate', 'rate'], ['#pkMinN', 'minMatches']]) {
    const el = $(sel);
    el.onchange = () => { FILTERS[key] = Number(el.value); saveFilters(); renderPicklist(); };
  }
  const zone = $('#pkStart');
  zone.onchange = () => { FILTERS.start = zone.value; saveFilters(); renderPicklist(); };
  for (const b of $$('#pkFilters [data-f]')) {
    b.onclick = () => { FILTERS[b.dataset.f] = !FILTERS[b.dataset.f]; saveFilters(); renderPicklist(); };
  }
  $('#pkFilterClear').onclick = () => {
    Object.assign(FILTERS, FILTER_OFF);
    saveFilters();
    renderPicklist();
  };
  syncFilterBar();
}

function syncFilterBar(shown, total) {
  const q = $('#pkSearch');
  if (!q) return;
  if (q.value !== FILTERS.q) q.value = FILTERS.q;
  $('#pkClimb').value = String(FILTERS.climb);
  $('#pkRate').value = String(FILTERS.rate);
  $('#pkMinN').value = String(FILTERS.minMatches);
  $('#pkStart').value = FILTERS.start;
  for (const b of $$('#pkFilters [data-f]')) b.classList.toggle('on', !!FILTERS[b.dataset.f]);
  // Nothing to clash with until somebody has scouted our own robot, so the chip
  // says which zone it is working from rather than filtering on a guess.
  const ourZone = ourStartZone();
  const clash = $('#pkNoClash');
  clash.disabled = !ourZone;
  clash.style.opacity = ourZone ? '' : '.45';
  clash.title = ourZone
    ? `Hide robots that usually start ${ourZone}, where ${ourTeam} does`
    : 'Needs OUR TEAM set on the hub settings page, and a scouted start zone for it';
  const on = filtersOn();
  $('#pkFilterClear').classList.toggle('hide', !on);
  // Say the rank is the board's, not the visible list's, wherever rows are
  // hidden - a "3" against the fourth visible row is otherwise a misreading
  // waiting to happen in the ten minutes it matters most.
  $('#pkFilterCount').textContent = on && total != null
    ? `${shown} of ${total} shown \u00b7 numbers are board ranks` : '';
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
    PICK_REV = pl.rev || 0;
    CAN_EDIT = pl.canEdit !== false;
    PIN_SET = !!pl.locked;
    renderWeights();
  } catch { /* hub unreachable: keep whatever we last had */ }
  renderEditBar();
}

/**
 * Picklist state lives on the hub so every authorised screen agrees.
 *
 * One field at a time, never the whole board. Two leads work this during
 * alliance selection - that is what the second dashboard is for - and sending
 * the whole document meant whichever of them clicked second silently undid the
 * other: measured, one lead marking 254 do-not-pick while the other dragged
 * 1678 to the top left the hub with one of the two edits, three runs out of
 * three. The flags go as add/remove for the same reason: two leads flagging
 * two different robots is not a conflict and must not be resolved as one.
 */
async function savePicklist(patch) {
  if (!CAN_EDIT) return;
  try {
    const r = await net.api('/api/picklist', { method: 'POST', body: JSON.stringify(patch) });
    if (r && r.picklist) PICK_REV = r.picklist.rev || 0;
  } catch (e) {
    // A refused write is the one thing that must not be swallowed. The
    // passcode is rotated during an event and a token lasts sixteen hours, so
    // a board can sit there saying EDITING UNLOCKED while every drag is thrown
    // away by the hub and nothing on screen says so.
    if (e.locked) {
      localStorage.removeItem('strategyToken');
      CAN_EDIT = false;
      renderEditBar();
      const msg = $('#pinMsg');
      if (msg) msg.textContent = 'That passcode session has expired — unlock again.';
    }
    /* offline: stays local until the hub is back */
  }
}

// Both of these are filterable, so both have to be readable on the row - a
// board that hides teams on a number it never shows is a board you cannot
// argue with. Absent means nobody has measured it, which is not a zero.
function rateBit(t) {
  const r = t.estimated.cycleRate;
  return r ? ` · ${r}/s` : '';
}
function startBit(t) {
  const z = t.observed.startZone;
  return z ? ` · starts ${esc(z)}` : '';
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
  // `at` is the row's place on the whole board and it travels with the row, so
  // hiding teams never renumbers the ones still showing - and a drag drops
  // against the real order, hidden teams included.
  const board = ranked().map((r, i) => ({ ...r, at: i }));
  const ourZone = ourStartZone();
  const rows = board.filter(({ t }) => passesFilters(t, taken, ourZone));
  syncFilterBar(rows.length, board.length);
  $('#pkFull').innerHTML = rows.map(({ t, s, was, at }) => `
    <div class="pk ${at === 0 ? 'top' : ''} ${taken.has(t.team) ? 'taken' : ''} ${DNP.has(t.team) ? 'dnp' : ''}"
         style="margin:0;border-bottom:1px solid var(--row)"
         data-team="${t.team}"${CAN_EDIT ? ' draggable="true"' : ''}>
      <span class="i">${at + 1}</span><span class="n">${t.team}</span>
      <span class="nm">${esc(t.name || '')} — ${climbCell(t)} · ${t.estimated.avgFuel}±${t.estimated.band} fuel${rateBit(t)} · stock ${Math.round(t.observed.stockpileRate)}%${startBit(t)}${driftCell(was, at)}</span>
      <span class="s">${Math.round(s)}</span>
      ${CAN_EDIT ? `<button class="x" data-dnp="${t.team}">${DNP.has(t.team) ? 'UN-DNP' : 'DNP'}</button>` : ''}
    </div>`).join('')
    || (board.length ? '<div class="empty">No team on the board matches these filters.</div>'
                     : '<div class="empty">Nothing to rank yet.</div>');
  if (CAN_EDIT) wireDrag();
  for (const b of $$('[data-dnp]')) b.onclick = () => {
    const n = Number(b.dataset.dnp);
    const on = !DNP.has(n);
    on ? DNP.add(n) : DNP.delete(n);
    localStorage.setItem('dnp', JSON.stringify([...DNP]));
    savePicklist(on ? { dnpAdd: [n] } : { dnpRemove: [n] });
    renderPicklist(); renderPickMini();
  };
  $('#dnpList').innerHTML = [...DNP].length
    ? [...DNP].map((n) => `<div class="kv"><span>${n}</span><b>do not pick</b></div>`).join('')
    : '<div class="hint">nobody flagged</div>';
  // The order is the board's; this only ever explains it - and it explains the
  // whole board, not whatever one reader has filtered down to.
  const order = board.slice(0, 10).map(({ t }) => t.team);
  peekAi('#pkWhyBody', 'picklist', { order }, 'Not written yet.');
  wireAi('#pkWhyGo', '#pkWhyBody', 'picklist', () => ({ order }), 'Not written yet.');

  const reset = $('#pkReset');
  if (reset) {
    reset.classList.toggle('hide', !(CAN_EDIT && activeOrder().length));
    reset.onclick = () => {
      setActiveOrder([]);
      savePicklist(PICK_MODE === 'first' ? { order: [] } : { order2: [] });
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
    // Every pixel of a slider drag used to be its own POST of the whole board.
    // The board still re-ranks per pixel - that is the point of the slider -
    // but only the weights this pane owns are sent, and only once the hand
    // stops moving.
    saveWeightsSoon();
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
    <div class="callout"><div class="h">${esc(shortCode(matchLabel(f.match_key)))} · ${esc(f.kind)}</div>
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
const ago = (s) => s == null ? '—' : s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
// The hub sends instants; the age is worked out here. It used to send the age
// itself, which meant two identical polls a second apart were different bytes
// and the endpoint could never answer "nothing has changed" - and the board
// froze between polls instead of counting up.
const secsSince = (at) => (at == null ? null : Math.max(0, net.serverNow() - at));
const playedCount = () => ((STATE && STATE.matches) || []).filter((m) => m.breakdown).length;

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
    problems.push(`${empty.map((c) => stationLabel(c.seat)).join(', ')} — nobody seated, those robots are unwatched`);
  }
  for (const c of seated) {
    const who = `${String(c.scoutId).toUpperCase()} on ${stationLabel(c.seat)}`;
    if (!c.connected) problems.push(`${who} — app is not open on their phone`);
    // 240s, not 180: an idle stream now writes its keepalive every 45s and the
    // hub records "last heard" off that at most once a minute, so the quietest
    // a perfectly healthy phone can look is about a minute and a half.
    else if (secsSince(c.lastSeenAt) > 240) problems.push(`${who} — gone quiet ${ago(secsSince(c.lastSeenAt))}, check their wifi`);
    else if (c.lastMatchAt == null) {
      // Never logged anything at all. The rule below reads an age, and an age
      // nobody has is null, so the scout who has not sent one row all day -
      // the one who has not understood the app - was the only one it could
      // not see.
      if (playedCount() > 0) problems.push(`${who} — nothing logged yet`);
    } else if (secsSince(c.lastMatchAt) > 25 * 60) problems.push(`${who} — nothing logged in ${ago(secsSince(c.lastMatchAt))}`);
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
      <span style="color:${side};font:800 12px Barlow,sans-serif;letter-spacing:.1em">${stationLabel(c.seat)}</span>
      <span>${c.scoutId ? esc(String(c.scoutId).toUpperCase()) : '<span style="color:var(--t6)">nobody seated</span>'}
        ${c.scoutId ? `<button class="x" data-unseat="${c.seat}" data-device="${esc(c.deviceId || '')}" style="margin-left:8px">FREE</button>` : ''}</span>
      <span class="num" style="color:${ok ? 'var(--green-soft)' : 'var(--red-alert)'};font:800 10.5px Barlow,sans-serif;letter-spacing:.1em">
        ${c.scoutId ? (ok ? 'LIVE' : 'NOT SEEN') : '—'}</span>
      <span class="num">${ago(secsSince(c.lastSeenAt))}</span>
      <span class="num">${c.lastMatch ? esc(shortCode(matchLabel(c.lastMatch))) + ' · ' + ago(secsSince(c.lastMatchAt)) : '—'}</span>
    </div>`;
  }).join('');

  for (const b of $$('[data-unseat]')) b.onclick = async () => {
    // Sending the phone as well as the station: the click frees the scout whose
    // row the lead clicked, or nobody. Freeing by station alone threw out
    // whoever happened to be in the chair by the time the click landed.
    await net.api('/api/unseat', { method: 'POST', body: JSON.stringify({
      seat: b.dataset.unseat, deviceId: b.dataset.device || undefined,
    }) }).catch(() => {});
    refresh();
  };

  // which robot in the current match has nobody on it
  const ms = (STATE && STATE.matches) || [];
  const m = ms.find((x) => x.status === 'On field' && !x.breakdown)
    || ms.find((x) => !x.breakdown);
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
    <div class="kv"><span>${esc(stationLabel(e.seat))}</span>
      <b>${e.from ? esc(String(e.from).toUpperCase()) + ' → ' : ''}${
        e.scoutId ? esc(String(e.scoutId).toUpperCase()) : 'FREED'}</b></div>`).join('')
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
  // `estimated.matches`, not "is there a row" - the same test analytics.py's
  // match_projection() uses, and the two are meant to be the same sum. Every
  // team AT the event has a row (analytics unions store.teams()), so counting
  // rows meant every robot read as scouted: the tile could never say "2 of 3",
  // the "not scouted" line below could never fire, and a fuel total quietly
  // missing a robot was presented as a weak alliance rather than an unknown one.
  const records = lineup.map((t) => (ANALYTICS && ANALYTICS.teams[t]) || null);
  const teams = records.map((r) => (r && r.estimated && r.estimated.matches ? r : null));
  const fuel = teams.reduce((a, t) => a + (t ? t.estimated.avgFuel : 0), 0);
  // matchBand, not band: band is how well we know a team's average, this is how
  // much one match swings. Independent robots, so the variances add.
  const spread = Math.sqrt(teams.reduce((a, t) => a + (t ? (t.estimated.matchBand || 0) ** 2 : 0), 0));
  const band = Math.sqrt(teams.reduce((a, t) => a + (t ? t.estimated.band ** 2 : 0), 0));
  const tower = teams.reduce((a, t) => a + ((t && t.exact.avgTowerPoints) || 0), 0);
  const fp = gameRules().fuelPoints;
  const scouted = teams.filter(Boolean).length;
  return { teams, records, lineup, fuel, band, spread, tower,
           points: fuel * fp + tower, scouted };
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
  const { lineup, teams, records, fuel, tower } = pr;
  const band = Math.round(pr.band);
  const label = side === 'red' ? 'RED' : 'BLUE';
  const th = rpThresholds((STATE && STATE.event && STATE.event.level) || 'regional');
  // Two records per robot on purpose. `d` is the one the projection was allowed
  // to use - our own solved fuel, or null - and `rec` is everything the hub
  // knows about that team, which exists whether or not a scout of ours ever sat
  // on it. TBA's climb and Lovat's count come off `rec`, so the robot nobody
  // watched stops being a blank row. It is the robot you most need something
  // about, and this is the screen that gets read out loud.
  const rows = lineup.map((t, i) => {
    const d = teams[i];
    const rec = records[i];
    const fuelCell = d
      ? `${d.estimated.avgFuel} <span class="band">±${d.estimated.band}</span>`
      : '<span class="band">not scouted</span>';
    return `<div class="r" style="grid-template-columns:56px 1fr 90px 80px 78px">
      <span class="tno">${t}</span><span class="nm">${esc((rec && rec.name) || '')}</span>
      <span class="num">${fuelCell}</span>
      <span class="num">${rec ? climbCell(rec) : '<span class="band">—</span>'}</span>
      <span class="num">${rec ? lovatCell(rec) : '<span class="band">—</span>'}</span></div>`;
  }).join('');
  // Robots our own scouts have nothing on that Lovat does. Named on the tile
  // because the tile is what gets read out - and never added to the sum:
  // somebody else's scouting is a second opinion, not a measurement of ours.
  const lovatFills = records.filter((r, i) => !teams[i] && lovatN(r || {})).length;
  return `<div style="display:flex;flex-direction:column;gap:14px">
    <div class="eyebrow" style="color:${side === 'red' ? 'var(--red-label)' : 'var(--blue-label)'}">
      ${label} · ${esc((m && m.label) || '')}</div>
    ${verdictBanner(m, side)}
    ${autoClashNote(records)}
    ${defenseNote(m, side, records)}
    <div class="tiles" style="grid-template-columns:repeat(3,1fr)">
      ${tile('PROJECTED FUEL', Math.round(fuel),
             // A robot nobody has scouted contributes nothing to this sum, so
             // an alliance with one unknown robot reads as a weak alliance
             // rather than an unknown one. The table below names them; the
             // tile has to say it too, because the tile is the thing that gets
             // read out loud.
             pr.scouted < lineup.length
               ? `${pr.scouted} of ${lineup.length} robots scouted — incomplete`
                 + (lovatFills
                    ? ` · lovat has ${lovatFills === 1 ? 'another' : lovatFills}` : '')
               : `±${band} · Energized at ${th.energized}`,
             fuel >= th.energized ? '' : 'warn')}
      ${tile('TOWER POINTS', Math.round(tower), `Traversal at ${th.traversal}`,
             tower >= th.traversal ? '' : 'warn')}
      ${tile('SUPERCHARGED', fuel >= th.supercharged ? 'YES' : 'NO', `needs ${th.supercharged}`)}
    </div>
    <div class="tbl">
      <div class="hd" style="grid-template-columns:56px 1fr 90px 80px 78px">
        <span>TEAM</span><span>NAME</span><span class="num">FUEL</span><span class="num">CLIMB</span>
        <span class="num">LOVAT</span></div>
      ${rows || '<div class="empty">No lineup yet.</div>'}
    </div></div>`;
}
/** Two robots that habitually start in the same zone will meet there.
 *
 * Grouped by the side, not by the lane, even now that scouts can record a lane:
 * a side is the answer every entry has, a lane is one only the after screen's
 * second page produces, and a warning that goes quiet because half the data is
 * finer than the other half is worse than one that fires and gets read. The
 * lanes go in the text instead, where they say how hard the clash really is.
 */
function autoClashNote(teams) {
  const byZone = {};
  for (const t of teams) {
    if (!t || !t.observed.startZone) continue;
    (byZone[t.observed.startZone] ||= []).push(t);
  }
  const clash = Object.entries(byZone).filter(([, ts]) => ts.length > 1);
  if (!clash.length) return '';
  return clash.map(([zone, ts]) => {
    const lanes = ts.map((t) => t.observed.startLane);
    const named = ts.map((t, i) => lanes[i] ? `${t.team} (${esc(lanes[i])})` : String(t.team));
    const apart = lanes.every(Boolean) && new Set(lanes).size === lanes.length;
    return `<div class="callout" style="margin:0">
    <div class="h">AUTO — ${esc(zone.toUpperCase())}</div>
    <div class="b">${named.join(' and ')} both usually start ${esc(zone)}.${apart
      ? ' Different lanes, so ask rather than assume — but they are still both going that way.'
      : ' Worth asking before the match rather than watching it happen.'}</div></div>`;
  }).join('');
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

// Which match the tab is showing. Null follows the field - the next match is
// what a lead wants nine times out of ten - and a pick from the dropdown holds
// still, so a refresh in the middle of reading a match does not move it.
let openMatch = null;

function nextMatch() {
  const ms = (STATE && STATE.matches) || [];
  const live = ms.filter((x) => !x.breakdown);      // results beat a queueing status
  return live.find((x) => x.status === 'On field')
      || live.find((x) => x.status === 'Now queuing' || x.status === 'On deck')
      || live[0]
      || ms[ms.length - 1];
}

function renderMatchPreview() {
  const ms = (STATE && STATE.matches) || [];
  const m = (openMatch && ms.find((x) => x.matchKey === openMatch)) || nextMatch();
  $('#mvRed').innerHTML = m ? allianceCard(m, 'red') : '<div class="empty">No upcoming match.</div>';
  $('#mvBlue').innerHTML = m ? allianceCard(m, 'blue') : '';

  const pick = $('#mvPick');
  if (pick) {
    pick.innerHTML = ms.map((x) => `<option value="${esc(x.matchKey)}"${
      m && x.matchKey === m.matchKey ? ' selected' : ''}>${esc(x.label || x.matchKey)}${
      x.breakdown ? ' · played' : ''}</option>`).join('')
      || '<option>no schedule yet</option>';
    pick.onchange = () => { openMatch = pick.value; renderMatchPreview(); };
    $('#mvHint').textContent = m
      ? (openMatch ? 'showing the match you picked' : 'following the field')
      : 'set an event key on the hub to get a schedule';
  }

  // Generated text never arrives on its own: opening this tab must not spend
  // the team's API credit, so this only reads the hub's cache.
  const body = '#aiMatchBody';
  if ($(body) && m) {
    const idle = 'Not read yet.';
    peekAi(body, `match/${m.matchKey}`, {}, idle);
    wireAi('#aiMatchGo', body, `match/${m.matchKey}`, () => ({}), idle);
  }
}

/**
 * One robot, match by match: fuel from every source that has an opinion, and
 * the defence it played and took.
 *
 * The x axis here is this robot's own matches, so a gap really is a missing
 * measurement - a match Lovat has no row for, or one our scouts missed - and
 * the lines break rather than bridging it.
 */
function teamCharts(t) {
  const rows = t.trend || [];
  if (!rows.length) return '';
  const x = rows.map((r) => ({ key: r.matchKey, label: r.label || r.matchKey,
                               short: shortCode(r.label) }));
  const lovatFuel = rows.map((r) => r.lovatFuel ?? null);
  const hasLovat = lovatFuel.some((v) => v != null);
  const fuel = chart.line({
    x,
    series: [
      { label: 'ours', color: 'var(--s1)',
        values: rows.map((r) => r.fuel ?? null),
        band: rows.map((r) => r.band ?? null) },
      ...(hasLovat ? [{ label: 'lovat', color: 'var(--s2)', values: lovatFuel }] : []),
    ],
    unit: 'fuel this robot put up',
    caption: 'The shaded band is the solver’s uncertainty on that single match, not the '
           + 'spread of the season.' + (hasLovat
             ? ' Lovat is other teams’ scouts counting the same robot.' : ''),
  });

  const played = rows.map((r) => r.defenseSecs ?? null);
  const faced = rows.map((r) => r.defenseFacedSecs ?? null);
  const lovatDef = rows.map((r) => r.lovatDefenseSecs ?? null);
  const defSeries = [];
  if (played.some((v) => v)) defSeries.push({ label: 'played', color: 'var(--s1)', values: played });
  if (faced.some((v) => v)) defSeries.push({ label: 'faced', color: 'var(--s2)', values: faced });
  if (lovatDef.some((v) => v)) defSeries.push({ label: 'played (lovat)', color: 'var(--s3)', values: lovatDef });

  return `
    <div class="tbl"><div class="cap"><span class="t">FUEL BY MATCH</span>
      <span class="n">estimated — one point per match this robot played</span></div>
      <div style="padding:12px 16px 16px">${fuel}</div></div>
    ${defSeries.length ? `
    <div class="tbl"><div class="cap"><span class="t">DEFENCE BY MATCH</span>
      <span class="n">seconds of contact — played, and taken from the other alliance</span></div>
      <div style="padding:12px 16px 16px">${chart.line({
        x, series: defSeries, unit: 'seconds',
        caption: 'Zero is a scout watching and seeing no defence. A gap is no scout entry at all.',
        height: 170,
      })}</div></div>` : ''}`;
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
      ${tile('FUEL / MATCH', es.avgFuel,
             `± ${es.band}${es.cycleRate ? ` · ${es.cycleRate}/s` : ''} · estimated`)}
      ${tile('BEST CLIMB', !e.bestClimb || e.bestClimb === 'None' ? '—'
               : e.bestClimb.replace('Level', 'L'),
             e.matchesWithOfficial
               ? `${Math.round((e.climbRate || {})[e.bestClimb] || 0)}% of matches · exact`
               : 'no official result yet')}
      ${tile('TOWER PTS', e.avgTowerPoints ?? '—',
             e.autoClimbRate == null ? 'no official result yet'
               : `auto climb ${e.autoClimbRate}%`)}
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
          ? `${o.startZone.toUpperCase()}${o.startLane ? ` · ${o.startLane.toUpperCase()}` : ''} · ${Math.round(o.startZonePct)}%`
          : '—'}</b></div>
        <div class="kv"><span>auto did nothing</span><b>${o.autoFailRate
          ? Math.round(o.autoFailRate) + '%' : 'never seen'}</b></div>
        <div class="kv"><span>lots of fouls</span><b>${o.foulRate
          ? Math.round(o.foulRate) + '%' : 'never seen'}</b></div>
        <div class="kv"><span>driver</span><b>${o.driver ?? '—'} / 5</b></div>
        <div class="kv"><span>average preload</span><b>${o.avgPreload ?? 'not asked'}</b></div>
        <div class="kv"><span>how much went in</span><b>${o.accuracy == null
          ? 'not asked' : `${o.accuracy} / 5`}</b></div>
        <div class="kv"><span>crosses the field</span><b>${o.traversalRate == null
          ? 'not asked' : Math.round(o.traversalRate) + '%'}${kinds(o.traversalKinds, 'none')}</b></div>
        <div class="kv"><span>gets stuck</span><b>${o.beachedRate == null
          ? 'not asked' : Math.round(o.beachedRate) + '%'}${kinds(o.beachedKinds, 'neither')}</b></div>
        <div class="kv"><span>scores while moving</span><b>${Math.round(o.scoresWhileMovingRate || 0)}%</b></div>
        <div class="kv"><span>knocks shots down</span><b>${Math.round(o.disruptRate || 0)}%</b></div>
        <div class="kv"><span>leaves to climb at</span><b>${o.climbStartSecs == null
          ? 'nobody timed one' : `${Math.round(o.climbStartSecs)}s${o.climbsTimed
            ? ` · ${o.climbsTimed} match${o.climbsTimed === 1 ? '' : 'es'}` : ''}`}</b></div>
        <div class="kv"><span>climbs at</span><b>${o.climbSpot
          ? esc(o.climbSpot.replace(/([A-Z])/g, ' $1').toLowerCase()) : 'not asked'}</b></div>
        <div class="kv"><span>tried a climb and fell</span><b>${o.climbFailRate
          ? Math.round(o.climbFailRate) + '%' : 'never seen'}</b></div>
      </div></div>
    ${teamCharts(t)}
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
        <div class="kv"><span>leaves to climb at</span><b>${lv.climbStartSecs == null ? '—'
          : `${Math.round(lv.climbStartSecs)}s${lv.autoClimbStartSecs == null ? ''
             : ` · auto ${Math.round(lv.autoClimbStartSecs)}s`}`}</b></div>
        <div class="kv"><span>scores while moving</span><b>${lv.scoresWhileMovingRate == null
          ? '—' : Math.round(lv.scoresWhileMovingRate) + '%'}</b></div>
        <div class="kv"><span>crosses the field</span><b>${lv.traversalRate == null
          ? '—' : Math.round(lv.traversalRate) + '%'}${
          kinds(lv.traversalKinds, 'NONE')}</b></div>
        <div class="kv"><span>gets beached</span><b>${lv.beachedRate == null
          ? '—' : Math.round(lv.beachedRate) + '%'}${
          kinds(lv.beachedKinds, 'NEITHER')}</b></div>
        <div class="kv"><span>disrupts</span><b>${lv.disruptRate == null
          ? '—' : Math.round(lv.disruptRate) + '%'}</b></div>
        <div class="kv"><span>outpost intakes</span><b>${lv.outpostIntakes ?? '—'}</b></div>
        <div class="kv"><span>roles</span><b>${teamRoles(lv.roles)}</b></div>
        <div class="kv"><span>intake</span><b>${teamRoles(lv.intakeTypes)}</b></div>
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
  chart.wire($('#tdMain'));
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
      ${(p.photos || []).map((id) => `<img src="/api/photo/${esc(encodeURIComponent(id))}" style="width:88px;height:88px;object-fit:cover;border-radius:8px;border:1px solid var(--btn)">`).join('')}
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
    return '<div class="hint">No AI model chosen — pick one in the admin panel, at / on the '
         + 'hub machine.</div>';
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

/**
 * Bring phone backup files in from the SERVER tab.
 *
 * The path that matters when the venue gave us no usable network: scouts run
 * offline all day, each phone writes a JSON file, and they all land here. The
 * hub already merges last-write-wins, so importing the same file twice is a
 * no-op and the lead does not have to track which ones are done.
 */
function wireImport() {
  const input = $('#impFile'), out = $('#impOut');
  if (!input || !out) return;
  input.onchange = async () => {
    const files = [...(input.files || [])];
    if (!files.length) return;
    input.disabled = true;
    const lines = [];
    let applied = 0;
    for (const f of files) {
      try {
        const body = JSON.parse(await f.text());
        const r = await net.api('/api/import', { method: 'POST', body: JSON.stringify(body) });
        applied += r.applied || 0;
        lines.push(`${esc(f.name)} — ${r.applied || 0} in`
          + ((r.rejected ? `, ${r.rejected} already had a newer copy` : '')));
      } catch (e) {
        // Reached only for a file that is not JSON at all, or a hub that went
        // away mid-import. A backup with nothing queued in it parses fine and
        // comes back as "0 in", which is not an error and does not land here.
        const why = /JSON/i.test(String(e && e.message)) ? 'not a scouting backup file'
          : String((e && e.message) || 'could not be read');
        lines.push(`${esc(f.name)} — ${esc(why)}`);
      }
    }
    out.innerHTML = lines.map((l) => `<div style="padding:2px 0">${l}</div>`).join('');
    input.disabled = false;
    input.value = '';
    if (applied) await refresh();
  };
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

// ══════════════════════════════════════════════════════════════ GRAPHS
/**
 * The graphs tab: the same numbers the tables carry, drawn over time.
 *
 * A table answers "how good is this robot"; a line answers "is it getting
 * better, and did something change on Saturday morning" - which is the question
 * a picklist meeting actually turns on, and the one an average cannot answer.
 *
 * Every source keeps its own line. Our solver, our scouts and Lovat's scouts
 * are never averaged into one number here for the same reason they are kept in
 * separate blocks everywhere else: where two of them disagree about a robot,
 * that disagreement is the finding.
 */

// Up to six teams, held in fixed slots. Removing a team leaves a hole rather
// than shuffling the rest along, so every other team keeps the colour the
// reader has already learned.
let gSlots = [];

function graphTeams() {
  // A slot can outlive its team - the event key changes, or a team drops out -
  // and every chart below indexes straight into the record.
  return gSlots.filter((t) => t && ANALYTICS && ANALYTICS.teams[t]);
}
function toggleGraphTeam(team) {
  const at = gSlots.indexOf(team);
  if (at >= 0) { gSlots[at] = null; return; }
  const hole = gSlots.indexOf(null);
  if (hole >= 0) gSlots[hole] = team;
  else if (gSlots.length < chart.SLOTS) gSlots.push(team);
}
function graphColor(team) {
  const at = gSlots.indexOf(team);
  return at < 0 ? 'var(--t5)' : chart.slot(at);
}

/** Our own team first, then the biggest scorers — a sensible opening view. */
function defaultGraphTeams() {
  const rows = Object.values((ANALYTICS && ANALYTICS.teams) || {})
    .filter((t) => (t.trend || []).length)
    .sort((a, b) => b.estimated.avgFuel - a.estimated.avgFuel);
  const out = [];
  if (ourTeam && rows.some((t) => t.team === ourTeam)) out.push(ourTeam);
  for (const t of rows) {
    if (out.length >= chart.SLOTS) break;
    if (!out.includes(t.team)) out.push(t.team);
  }
  return out;
}

/** The schedule, in order, as the x axis every team's line is drawn on. */
function graphAxis(teams) {
  const wanted = new Set();
  for (const team of teams) {
    for (const r of (((ANALYTICS.teams[team] || {}).trend) || [])) wanted.add(r.matchKey);
  }
  return ((STATE && STATE.matches) || [])
    .filter((m) => wanted.has(m.matchKey))
    .map((m) => ({ key: m.matchKey, label: m.label || m.matchKey, short: shortCode(m.label) }));
}

/** A team's trend, indexed by match key, so a chart can look one match up. */
function trendBy(team) {
  const out = new Map();
  for (const r of (((ANALYTICS.teams[team] || {}).trend) || [])) out.set(r.matchKey, r);
  return out;
}

function renderGraphs() {
  const host = $('#gPick');
  if (!host || !ANALYTICS) return;
  if (!gSlots.length) gSlots = defaultGraphTeams();

  const all = Object.values(ANALYTICS.teams)
    .filter((t) => (t.trend || []).length || lovatN(t))
    .sort((a, b) => a.team - b.team);
  const picked = graphTeams();
  const full = picked.length >= chart.SLOTS;

  host.innerHTML = all.map((t) => {
    const on = picked.includes(t.team);
    return `<button data-team="${t.team}" class="${on ? 'on' : ''}"
      ${!on && full ? 'disabled style="opacity:.35"' : ''}
      >${on ? `<i style="background:${graphColor(t.team)}"></i>` : ''}${t.team}</button>`;
  }).join('') || '<div class="hint">No scouted teams yet.</div>';
  for (const b of $$('#gPick button')) {
    b.onclick = () => { toggleGraphTeam(Number(b.dataset.team)); renderGraphs(); };
  }

  const x = graphAxis(picked);
  const byTeam = new Map(picked.map((t) => [t, trendBy(t)]));

  // ---- fuel per match, one line per team
  $('#gFuel').innerHTML = picked.length ? chart.line({
    x,
    // A team is absent from most matches because it was not in them, so its own
    // consecutive matches join up: the gap is the schedule, not missing data.
    series: picked.map((team) => ({
      label: String(team), color: graphColor(team), connect: true,
      values: x.map((p) => (byTeam.get(team).get(p.key) || {}).fuel ?? null),
    })),
    unit: 'fuel, estimated by our solver',
    empty: 'None of the picked teams has a solved match yet.',
    caption: 'Estimated. Each point is what the solver gave that robot out of its alliance’s '
           + 'official window totals, so it already agrees with TBA at the alliance level.',
  }) : '<div class="empty">Pick a team above.</div>';

  // ---- defence, played and faced
  const anyLovatDef = picked.some((t) => (ANALYTICS.teams[t].lovat || {}).defenseSecs != null);
  const defSeries = [
    { label: 'played (our scouts)', color: 'var(--s1)' },
    { label: 'faced (our scouts)', color: 'var(--s2)' },
    ...(anyLovatDef ? [{ label: 'played (lovat)', color: 'var(--s3)' }] : []),
  ];
  const defRows = picked.map((team) => {
    const t = ANALYTICS.teams[team], o = t.observed, lv = t.lovat || {};
    return {
      label: `${team}`,
      values: [o.defenseSecs || 0, o.defenseFacedSecs ?? null,
               ...(anyLovatDef ? [lv.defenseSecs ?? null] : [])],
    };
  });
  $('#gDefense').innerHTML = picked.length ? chart.bars({
    rows: defRows, series: defSeries, unit: 'seconds per match',
    empty: 'No scout has logged defence for any of these robots yet.',
    caption: 'Seconds of contact per match, averaged. "Faced" counts only defence a scout '
           + 'attributed to a named robot, so it is a floor rather than a total.',
  }) : '<div class="empty">Pick a team above.</div>';

  // ---- our numbers against the two sources from outside
  const rows = Object.values(ANALYTICS.teams);
  $('#gVsLovat').innerHTML = chart.scatter({
    points: rows.filter((t) => t.estimated.matches && lovatN(t) && t.lovat.avgFuel != null)
      .map((t) => ({ label: `${t.team}`, x: t.estimated.avgFuel, y: t.lovat.avgFuel })),
    xLabel: 'our fuel / match', yLabel: 'lovat fuel / match', diagonal: true,
    height: 250, width: 400,
    empty: 'No team here has both our fuel and Lovat’s — set a Lovat key on the hub.',
    caption: 'The dashes are agreement. A robot well off them is one the two sets of scouts '
           + 'read differently — usually a robot one of them has seen fewer times.',
  });
  $('#gVsEpa').innerHTML = chart.scatter({
    points: rows.filter((t) => t.estimated.matches && t.epa.epa != null)
      .map((t) => ({ label: `${t.team}`, x: t.estimated.avgFuel, y: t.epa.epa })),
    xLabel: 'our fuel / match', yLabel: 'statbotics EPA', diagonal: false,
    height: 250, width: 400,
    empty: 'Statbotics has nothing for this event yet.',
    caption: 'Different units, so there is no agreement line to draw — only a shape. EPA is '
           + 'a whole-season fit and counts climb and auto; our fuel column does not.',
  });

  // ---- the side pane
  const withLovat = rows.filter((t) => lovatN(t)).length;
  const withEpa = rows.filter((t) => t.epa.epa != null).length;
  $('#gSources').innerHTML = `
    <div class="kv"><span>teams our scouts have seen</span><b>${rows.filter((t) => t.matchesScouted).length}</b></div>
    <div class="kv"><span>teams lovat has</span><b>${withLovat || '—'}</b></div>
    <div class="kv"><span>teams statbotics has</span><b>${withEpa || '—'}</b></div>`;

  const defenders = rows.filter((t) => t.observed.defenseSecs)
    .sort((a, b) => b.observed.defenseSecs - a.observed.defenseSecs).slice(0, 5);
  const pressured = rows.filter((t) => t.observed.defenseFacedSecs)
    .sort((a, b) => b.observed.defenseFacedSecs - a.observed.defenseFacedSecs).slice(0, 3);
  $('#gDefSummary').innerHTML =
    (defenders.length
      ? defenders.map((t) => `<div class="kv"><span>${t.team} plays defence</span>
          <b>${t.observed.defenseSecs}s</b></div>`).join('')
      : '<div class="hint">No scout has logged defence yet.</div>')
    + (pressured.length ? `<div class="hint" style="margin-top:6px">Most defended:
        ${pressured.map((t) => `${t.team} (${t.observed.defenseFacedSecs}s)`).join(', ')}.</div>` : '');

  const timed = rows.filter((t) => (t.lovat || {}).climbStartSecs != null)
    .sort((a, b) => a.lovat.climbStartSecs - b.lovat.climbStartSecs).slice(0, 6);
  $('#gClimbTiming').innerHTML = timed.length
    ? timed.map((t) => `<div class="kv"><span>${t.team} leaves to climb</span>
        <b>${Math.round(t.lovat.climbStartSecs)}s${t.lovat.bestClimb
          ? ` · ${esc(String(t.lovat.bestClimb).replace('Level', 'L'))}` : ''}</b></div>`).join('')
    : '<div class="hint">No Lovat data for this event — set a Lovat key on the hub.</div>';

  chart.wire($('#t-graphs'));
}

// ═══════════════════════════════════════════════════════════════ SEATS
const SEAT_KEYS = ['red1', 'red2', 'red3', 'blue1', 'blue2', 'blue3'];
function renderSeats() {
  const seats = (STATE && STATE.seats) || {};
  const ms = ((STATE && STATE.matches) || []).filter((m) => !m.breakdown).slice(0, 4);
  const cols = `90px repeat(6, 1fr)`;
  $('#seatHead').style.gridTemplateColumns = cols;
  $('#seatHead').innerHTML = '<span>MATCH</span>' +
    SEAT_KEYS.map((k) => `<span>${stationLabel(k)}</span>`).join('');
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
  $('#seatSub').textContent = `${SEAT_KEYS.filter((k) => seats[k]).length} of 6 stations claimed`;

  // Who is sitting where and how much they have logged - no quality score.
  const rc = '1fr 120px';
  const roster = CREW.filter((c) => c.scoutId);
  $('#rosterHead').style.gridTemplateColumns = rc;
  $('#rosterHead').innerHTML = '<span>NAME</span><span class="num">STATION</span>';
  $('#rosterBody').innerHTML = roster.map((c) => `
    <div class="r" style="grid-template-columns:${rc}"><span>${esc(String(c.scoutId).toUpperCase())}</span>
      <span class="num">${esc(stationLabel(c.seat))}</span></div>`).join('')
    || '<div class="empty">Nobody seated yet.</div>';

  const c = ANALYTICS ? ANALYTICS.coverage : { pct: 0, robotsScouted: 0, robotsExpected: 0 };
  $('#seatCoverage').innerHTML =
    `<div class="kv"><span>robots scouted</span><b>${c.robotsScouted} / ${c.robotsExpected}</b></div>
     <div class="kv"><span>coverage</span><b>${c.pct}%</b></div>`;
  const empty = SEAT_KEYS.filter((k) => !seats[k]);
  $('#seatWarn').innerHTML = empty.length
    ? `<div class="callout" style="margin-top:10px"><div class="h">${empty.length} STATION${empty.length > 1 ? 'S' : ''} EMPTY</div>
       <div class="b">${empty.map(stationLabel).join(', ')} — those robots go unwatched.</div></div>` : '';
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
    + `<div class="kv"><span>host</span><b class="mono" style="font-size:11.5px">${esc(DIAG.host)}</b></div>`
    // Where the event key, the API keys and the mirror are set. It opens on the
    // hub laptop and nowhere else, and it says so politely from anywhere else -
    // but nothing on any screen used to say where it was at all.
    + `<div class="kv"><span>admin panel</span><b class="mono" style="font-size:11.5px">`
    + `<a href="/">the hub laptop, at /</a></b></div>`;
  $('#srvData').innerHTML =
    `<div class="kv"><span>python</span><b>${esc(DIAG.python)}</b></div>
     <div class="kv"><span>stations claimed</span><b>${Object.keys(DIAG.seats || {}).length} / 6</b></div>
     <div style="margin-top:10px;display:flex;flex-direction:column;gap:7px;
                 font:800 11px Barlow,sans-serif;letter-spacing:.1em">
       <a href="/api/export" download="scouting-export.json">DOWNLOAD FULL EXPORT · JSON</a>
       <a href="/api/export.csv?table=teams">TEAM SUMMARY · CSV</a>
       <a href="/api/export.csv?table=scout">EVERY SCOUT ENTRY · CSV</a>
       <a href="/api/export.csv?table=pit">PIT SCOUTING · CSV</a>
       <a href="/api/export.csv?table=lovat">WHAT LOVAT HAS · CSV</a>
       <a href="/picklist/print" target="_blank">PRINTABLE PICKLIST</a>
       <a href="/picklist/print?list=second" target="_blank">PRINTABLE SECOND-PICK LIST</a>
     </div>
     <div class="hint" style="margin-top:8px">JSON is the one that imports back in — the
       same shape the phones write, so a full export and a single phone's backup both go
       in below. CSV is for a spreadsheet, and the printed picklist is the paper fallback
       for alliance selection.</div>`;
}

// ══════════════════════════════════════════════════════════════ refresh
// One render function per tab. Tabs are switched with a class, so a hidden pane
// is still in the document and was still being rebuilt: every refresh generated,
// parsed and inserted the markup for all ten, and re-bound every handler on
// them, when nine were display:none. Scatter plots with a circle per team,
// full team tables, the log listing - all of it, for nobody.
const TAB_RENDER = {
  crew: () => renderCrew(),
  live: () => renderLive(),
  teams: () => renderTeams(),
  graphs: () => renderGraphs(),
  picklist: () => renderPicklist(),
  health: () => renderHealth(),
  seats: () => renderSeats(),
  server: () => renderServer(),
  match: () => renderMatchPreview(),
  team: () => renderTeamDetail(),
};
let currentTab = 'crew';
const dirtyTabs = new Set();

/** Draw one tab now, whether or not it was marked dirty. */
function renderTab(name) {
  const fn = TAB_RENDER[name];
  if (!fn) return;
  dirtyTabs.delete(name);
  try { fn(); } catch (e) { console.error(e); }
}

function renderHeader() {
  $('#evTitle').textContent = STATE && STATE.event && STATE.event.name
    ? `${STATE.event.name.toUpperCase()} · STRATEGY` : 'REBUILT · STRATEGY';
  const played = STATE ? (STATE.matches || []).filter((m) => m.breakdown).length : 0;
  const total = STATE ? (STATE.matches || []).length : 0;
  const ago = net.state.lastSync ? Math.round((Date.now() - net.state.lastSync) / 1000) : null;
  $('#evSub').textContent = STATE
    ? `QUAL ${played} / ${total} · ${(STATE.teams || []).length} TEAMS${ago != null ? ` · SYNC ${ago}s` : ''}`
    : 'NO EVENT · 0 TEAMS';
}

function renderAll() {
  renderHeader();
  for (const name of Object.keys(TAB_RENDER)) {
    if (name === currentTab) renderTab(name);
    else dirtyTabs.add(name);   // drawn on the way in, from data already here
  }
}

/**
 * Pull whatever has changed, and redraw only if something did.
 *
 * Three things were wrong with doing this the obvious way. The six fetches were
 * sequentially awaited, so the round trips added up instead of overlapping.
 * Nothing was conditional, so ~165KB of identical JSON crossed the wire every
 * time. And it ended in a blanket re-render whether or not a single byte had
 * moved. At idle this ran on a 30s timer, a 7s timer and every one of twelve
 * broadcast types - about twenty requests a minute to say nothing had happened.
 */
async function refresh() {
  const pull = async (path, fallback) => {
    try { return await net.apiCached(path); }
    catch { return { changed: false, value: undefined, failed: true, fallback }; }
  };
  const [st, an, crew, seatlog, diag] = await Promise.all([
    pull('/api/state'), pull('/api/analytics'), pull('/api/crew'), pull('/api/seatlog'),
    // The one endpoint worth asking for only when it is on screen: building it
    // forks ifconfig/ip/ipconfig on the hub laptop.
    currentTab === 'server' ? pull('/api/diag') : Promise.resolve({ changed: false }),
  ]);

  let moved = false;
  if (st.changed) { STATE = st.value; db.cacheSet('state', STATE); moved = true; }
  else if (st.failed && !STATE) { STATE = await db.cacheGet('state'); moved = true; }
  if (an.changed) { ANALYTICS = an.value; db.cacheSet('analytics', ANALYTICS); moved = true; }
  else if (an.failed && !ANALYTICS) { ANALYTICS = await db.cacheGet('analytics'); moved = true; }
  if (crew.changed) { CREW = crew.value; moved = true; }
  if (seatlog.changed) { SEATLOG = seatlog.value; moved = true; }
  if (diag.changed) { DIAG = diag.value; moved = true; }

  // The header carries the sync age, which moves with the clock rather than
  // with the data, so it is repainted either way. It is three text nodes.
  renderHeader();
  if (moved) renderAll();
}

/** Settings, which change only when somebody saves them or the solver refits. */
async function refreshConfig() {
  try { CONFIG = await net.api('/api/config'); } catch {}
}

async function main() {
  await loadRules();
  CONFIG = await net.start();
  ourTeam = Number((CONFIG && CONFIG.ourTeam) || 0) || null;

  const TABS = ['crew', 'live', 'teams', 'graphs', 'picklist', 'health', 'seats', 'server',
                'match', 'team'];
  const goTab = (name) => {
    for (const x of $$('#tabs button')) x.classList.toggle('on', x.dataset.tab === name);
    for (const t of TABS) { const el = $(`#t-${t}`); if (el) el.classList.toggle('hide', t !== name); }
    currentTab = name;
    // Panes are only drawn while they are the one on screen, so a tab that
    // went stale in the background is caught up here, from data already in
    // hand - no fetch, and nothing to wait for.
    if (dirtyTabs.has(name)) renderTab(name);
    if (name === 'picklist') renderEditBar();
    // The SERVER tab is the only reader of /api/diag, and refresh() skips it
    // otherwise; opening the tab should not mean waiting 30s to see anything.
    if (name === 'server') refresh();
  };
  window.__goTab = goTab;
  for (const b of $$('#tabs button')) b.onclick = () => goTab(b.dataset.tab);

  renderWeights();
  wireFilters();
  $('#pinLock').onclick = async () => {
    localStorage.removeItem('strategyToken');
    await loadPicklistState();
    renderPicklist(); renderWeights();
  };
  await loadPicklistState();
  await refresh();
  // 'alliances' was dropped from this list on the grounds that alliance
  // selection arrives inside 'nexus'. It does not: poll_nexus sends it as its
  // own message (hub.py `_nexus_side`), and the 'nexus' message carries only
  // the queueing status, the schedule and the announcements. So during
  // selection this board learned that a team had been picked when the
  // ten-second poll came round and not before - on the one screen, in the one
  // twenty minutes of the event, where being ten seconds behind the room is
  // the whole complaint the push exists to answer.
  //
  // The other three names the hub sends - 'pits', 'pitMap' and 'inspection' -
  // are deliberately not here: nothing on this dashboard draws them, and the
  // pit tablet is the screen that does.
  // Coalesced. The hub fires several of these together - a TBA poll that lands
  // new results broadcasts `results`, `solved` and `scout` within milliseconds
  // of each other - and each one used to be its own full refresh.
  const nudge = coalesce(refresh, 750);
  for (const t of ['nexus', 'alliances', 'results', 'scout', 'calibration', 'matchStatus',
                   'seats', 'matchStart', 'lovat', 'solved', 'rankings', 'epa', 'earlyScores'])
    net.on(t, nudge);
  // Settings are not in refresh() any more: /api/config carries serverTime, so
  // it can never answer 304, and nothing on it changes without one of these.
  net.on('calibration', () => refreshConfig().then(renderAll));

  // The picklist is the one thing refresh() does not re-read, so it needs its
  // own listener - and it is the one that matters most. Two dashboards are open
  // during alliance selection; the second one kept its boot-time copy all
  // afternoon, and the next edit made on it wrote that stale copy back over
  // everyone else's DNP flags and ordering. Nothing said a thing.
  net.on('picklist', async (msg) => {
    if (dragTeam) return;              // mid-drag: the drop re-renders anyway
    // Not our own edit coming back. renderWeights() rebuilds the sliders, so
    // the echo of a save landed on the pane the lead still had a finger on and
    // snapped the handle back to the value the hub had a moment ago.
    if (msg && msg.rev && msg.rev === PICK_REV) return;
    await loadPicklistState();
    renderPicklist(); renderPickMini(); renderWeights();
  });
  wireAsk();
  wireImport();
  // One timer now, not two. It used to be a 30s full refresh plus a 7s crew
  // poll, on top of a refresh per broadcast; every one of those sent complete
  // payloads. This is a single conditional pass, so a quiet ten seconds costs
  // four empty 304s, and anything that actually happens arrives over the stream
  // in between. It stops while the tab is hidden and runs once on the way back -
  // a dashboard on a second monitor with the lid shut is not being read.
  //
  // Kept at ten seconds rather than relaxed further because a phone dropping
  // off the wifi is not something the hub can broadcast: the crew board only
  // learns it by asking.
  every(10000, refresh);
}

main().catch((e) => { console.error(e); $('#evSub').textContent = 'FAILED: ' + e.message; });
