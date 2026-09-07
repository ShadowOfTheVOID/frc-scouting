/**
 * The mirror's reader.
 *
 * Everything on screen comes out of one object - the last bundle the hub
 * pushed - and nothing here computes a scouting number. The solver ran on the
 * hub; the ranking ran on the hub; the CSV was written on the hub. This file
 * draws what arrived and, where it matters, says how old it is.
 *
 * That last part is the whole point of the banner at the top. A mirror that
 * quietly shows yesterday's numbers as if they were live is worse than no
 * mirror, so the age of the data is on screen at all times and turns amber and
 * then red on its own.
 */

const $ = (s) => document.querySelector(s);
const TOKEN_KEY = 'mirror.token';

const state = {
  token: null,
  events: [],
  eventKey: null,
  bundle: null,
  tab: 'teams',
  sort: { key: 'fuel', dir: -1 },
  serverSkew: 0,
};

// ---------------------------------------------------------------- plumbing

async function api(path, opts = {}) {
  const url = new URL(path, location.origin);
  const res = await fetch(url, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(state.token ? { 'X-Mirror-Token': state.token } : {}),
      ...(opts.headers || {}),
    },
  });
  if (res.status === 401) { forget(); showLock('That session expired. Enter the passcode again.'); throw new Error('locked'); }
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  return res.json();
}

/** A URL a browser fetches by itself - an <img>, a download - cannot send a
 *  header, so those carry the token in the query string instead. */
function signed(path) {
  const u = new URL(path, location.origin);
  if (state.token) u.searchParams.set('t', state.token);
  return u.toString();
}

function forget() { state.token = null; try { localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } }

const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const num = (v, d = 1) => (v === null || v === undefined || Number.isNaN(v) ? '—' : Number(v).toFixed(d));

/** Server time, not the device's. A phone whose clock is twenty minutes out
 *  would otherwise be told the data is twenty minutes fresher than it is,
 *  which is the one lie this page must not tell. */
const nowServer = () => Date.now() / 1000 + state.serverSkew;

function ago(t) {
  if (!t) return 'never';
  const s = Math.max(0, Math.round(nowServer() - t));
  if (s < 90) return `${s}s ago`;
  if (s < 5400) return `${Math.round(s / 60)}m ago`;
  if (s < 172800) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

// -------------------------------------------------------------------- lock

function showLock(msg) {
  $('#app').classList.add('hide');
  $('#lock').classList.remove('hide');
  $('#lockErr').textContent = msg || '';
  $('#pin').focus();
  // Why the site is not answering matters: a wrong passcode and a hub that
  // stopped pushing three hours ago are different problems, and only one of
  // them is the person at the keyboard's fault.
  fetch('/api/status').then((r) => r.json()).then((s) => {
    if (s.serverTime) state.serverSkew = s.serverTime - Date.now() / 1000;
    $('#lockStatus').textContent = s.lastReceivedAt
      ? `Last heard from the hub ${ago(s.lastReceivedAt)}.`
      : 'This mirror has never received anything from a hub.';
  }).catch(() => { $('#lockStatus').textContent = ''; });
}

async function unlock() {
  const btn = $('#unlock');
  btn.disabled = true;
  $('#lockErr').textContent = '';
  try {
    const res = await fetch('/api/unlock', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: $('#pin').value }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.token) {
      $('#lockErr').textContent = res.status === 429
        ? (body.error || 'Too many attempts. Wait ten minutes.')
        : 'Wrong passcode.';
      return;
    }
    state.token = body.token;
    try { localStorage.setItem(TOKEN_KEY, body.token); } catch { /* private mode */ }
    $('#pin').value = '';
    await boot();
  } catch {
    $('#lockErr').textContent = 'The mirror could not be reached.';
  } finally { btn.disabled = false; }
}

// -------------------------------------------------------------------- boot

async function boot() {
  const list = await api('/api/events');
  state.events = list.events || [];
  state.serverSkew = (list.serverTime || 0) - Date.now() / 1000;
  if (!state.events.length) {
    $('#lock').classList.add('hide');
    $('#app').classList.remove('hide');
    $('#evSub').textContent = 'NOTHING MIRRORED YET';
    $('#banner').className = 'banner cold';
    $('#banner').innerHTML = '<b>No hub has pushed to this mirror.</b> On the hub laptop, '
      + 'open the setup page and fill in the mirror address and push key.';
    $('#view').innerHTML = '';
    $('#tabs').classList.add('hide');
    return;
  }
  if (!state.eventKey || !state.events.some((e) => e.eventKey === state.eventKey)) {
    state.eventKey = state.events[0].eventKey;
  }
  await load();
}

async function load() {
  state.bundle = await api(`/api/snapshot?event=${encodeURIComponent(state.eventKey)}`);
  $('#lock').classList.add('hide');
  $('#app').classList.remove('hide');
  $('#tabs').classList.remove('hide');
  render();
}

// ------------------------------------------------------------------ header

// What the banner last said. `ago()` rounds to whole minutes, so for most of
// any given minute this rewrites the identical string - and rewriting innerHTML
// means parsing that markup again and rebuilding the nodes for it. This page is
// read on phones, by people who leave it open.
let lastBanner = '';

function freshness() {
  const b = state.bundle || {};
  const at = b.capturedAt;
  const el = $('#freshness');
  const secs = at ? Math.max(0, nowServer() - at) : Infinity;
  const label = at ? ago(at) : 'NO DATA';
  if (el.textContent !== label) el.textContent = label;
  el.className = secs < 300 ? '' : secs < 3600 ? 'stale' : 'cold';
  $('#freshSub').textContent = 'FROM THE HUB';

  const banner = $('#banner');
  // Under five minutes the hub is plainly still pushing, and a banner that is
  // always on is a banner nobody reads. It still says this is a copy.
  let html;
  if (secs < 300) {
    banner.className = 'banner';
    html = `<b>This is a mirror.</b> A copy the hub pushed ${esc(ago(at))} — `
      + 'read-only, and it never sees a scout tap. Everything live happens on the hub.';
  } else if (secs < 3600) {
    banner.className = 'banner';
    html = `<b>Last update ${esc(ago(at))}.</b> The hub may have moved networks, `
      + 'or lost internet. Numbers below are that old.';
  } else {
    banner.className = 'banner cold';
    html = `<b>Stale — last update ${esc(ago(at))}.</b> Nothing here has been `
      + 'refreshed since. Treat every number as historical.';
  }
  if (html !== lastBanner) { lastBanner = html; banner.innerHTML = html; }
}

// ------------------------------------------------------------------ render

function render() {
  const b = state.bundle || {};
  const ev = b.event || {};
  $('#evName').textContent = (ev.name || b.eventKey || 'SCOUTING MIRROR').toUpperCase();
  const teams = Object.keys(b.analytics?.teams || {}).length;
  const played = (b.matches || []).filter((m) => m.breakdown).length;
  $('#evSub').textContent = [
    b.eventKey, `${teams} TEAMS`, `${played}/${(b.matches || []).length} MATCHES PLAYED`,
    `${num(b.analytics?.coverage?.pct, 0)}% COVERAGE`,
  ].join(' · ');
  freshness();
  ({
    teams: viewTeams, matches: viewMatches, picklist: viewPicklist,
    health: viewHealth, backup: viewBackup,
  }[state.tab] || viewTeams)();
}

const rows = (b) => Object.values(b.analytics?.teams || {});
const ourTeam = () => parseInt(state.bundle?.ourTeam, 10) || null;

/** The same orderings the column headers give, as a list a phone can pick from
 *  — the headers are hidden below 720px, and without this the table on a phone
 *  was stuck in whatever order it loaded in. */
const SORT_ORDER = [
  ['fuel', -1, 'Fuel — most first'],
  ['rank', 1, 'Rank — best first'],
  ['epa', -1, 'EPA — highest first'],
  ['climb', -1, 'Tower points — most first'],
  ['scouted', -1, 'Matches seen — most first'],
  ['team', 1, 'Team number'],
];

const SORTS = {
  team: (t) => t.team,
  rank: (t) => (t.exact?.rank == null ? 1e9 : t.exact.rank),
  fuel: (t) => (t.estimated?.matches ? t.estimated.avgFuel : -1),
  epa: (t) => (t.epa?.epa == null ? -1 : t.epa.epa),
  climb: (t) => (t.exact?.avgTowerPoints ?? -1),
  scouted: (t) => t.matchesScouted,
};

function viewTeams() {
  const b = state.bundle;
  const list = rows(b).slice().sort((x, y) => {
    const a = SORTS[state.sort.key](x); const c = SORTS[state.sort.key](y);
    return a === c ? x.team - y.team : (a > c ? 1 : -1) * state.sort.dir;
  });
  const cols = 'grid-template-columns:64px minmax(0,1fr) 62px 78px 60px 56px';
  const hd = [['team', 'TEAM'], ['name', 'NAME'], ['rank', 'RANK'], ['fuel', 'FUEL'],
    ['epa', 'EPA'], ['scouted', 'SEEN']];

  $('#view').innerHTML = `
    ${eventPicker()}
    <div class="narrow-only"><label>SORT BY</label><select id="sortPick">${
      SORT_ORDER.map(([k, dir, label]) =>
        `<option value="${k}:${dir}"${state.sort.key === k && state.sort.dir === dir
          ? ' selected' : ''}>${label}</option>`).join('')}</select></div>
    <div class="tbl stack-narrow">
      <div class="cap"><span class="t">TEAMS</span>
        <span class="n">${list.length} · tap a row for the detail</span></div>
      <div class="hd" style="${cols}">
        ${hd.map(([k, label]) => (SORTS[k]
          ? `<span data-sort="${k}" class="${state.sort.key === k ? 'on' : ''}${k === 'rank' || k === 'fuel' || k === 'epa' || k === 'scouted' ? ' num' : ''}">${label}</span>`
          : `<span class="nm">${label}</span>`)).join('')}
      </div>
      ${list.length ? list.map((t) => teamRow(t, cols)).join('')
        : '<div class="empty">No teams in this bundle yet.</div>'}
    </div>`;
  $('#view').querySelectorAll('[data-sort]').forEach((el) => {
    el.onclick = () => {
      const k = el.dataset.sort;
      state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : (k === 'team' || k === 'rank' ? 1 : -1) };
      render();
    };
  });
  const pick = $('#sortPick');
  if (pick) pick.onchange = () => {
    const [k, dir] = pick.value.split(':');
    state.sort = { key: k, dir: Number(dir) };
    render();
  };
  $('#view').querySelectorAll('[data-team]').forEach((el) => {
    el.onclick = () => openTeam(parseInt(el.dataset.team, 10));
  });
}

function teamRow(t, cols) {
  const est = t.estimated || {}; const ex = t.exact || {};
  const fuel = est.matches ? `${num(est.avgFuel, 0)} <span class="band">±${num(est.band, 0)}</span>` : '—';
  return `<div class="r tap${t.team === ourTeam() ? ' ours' : ''}" style="${cols}" data-team="${t.team}">
    <span class="tno lead">${t.team}</span>
    <span class="nm" data-k="NAME">${esc(t.name || '')}</span>
    <span class="num tnum" data-k="RANK">${ex.rank ?? '—'}</span>
    <span class="num tnum" data-k="FUEL">${fuel}</span>
    <span class="num tnum" data-k="EPA">${num(t.epa?.epa, 0)}</span>
    <span class="num tnum" data-k="SEEN">${t.matchesScouted}</span>
  </div>`;
}

function eventPicker() {
  if (state.events.length < 2) return '';
  return `<div><label>EVENT</label><select id="evPick">${state.events.map((e) =>
    `<option value="${esc(e.eventKey)}"${e.eventKey === state.eventKey ? ' selected' : ''}>${
      esc(e.eventName || e.eventKey)} — ${esc(ago(e.receivedAt))}</option>`).join('')}</select></div>`;
}

// The picker is rebuilt with every view, so its handler is bound after each.
document.addEventListener('change', (e) => {
  if (e.target && e.target.id === 'evPick') {
    state.eventKey = e.target.value;
    load().catch((err) => { $('#view').innerHTML = `<div class="hint">${esc(err.message)}</div>`; });
  }
});

// ----------------------------------------------------------------- matches

function viewMatches() {
  const b = state.bundle;
  const ms = (b.matches || []).slice().reverse();     // newest first: what just happened
  const us = ourTeam();
  const mark = (list) => (list || []).map((t) => (t === us ? `<b>${t}</b>` : String(t))).join('  ');
  $('#view').innerHTML = `${eventPicker()}
    <div class="eyebrow">MATCHES — MOST RECENT FIRST</div>
    ${ms.length ? ms.map((m) => {
      const bd = m.breakdown || null;
      const rs = bd?.red?.totalPoints; const bs = bd?.blue?.totalPoints;
      return `<div class="mrow${bd ? ' played' : ''}">
        <div class="top"><span>${esc(m.label || m.matchKey)}</span>
          <span>${bd ? 'FINAL' : (m.status || 'SCHEDULED').toUpperCase()}</span></div>
        <div class="sides">
          <div class="teams red">${mark(m.red)}</div>
          <div class="sc${bd && rs > bs ? ' win' : ''}">${rs ?? ''}</div>
        </div>
        <div class="sides">
          <div class="teams blue">${mark(m.blue)}</div>
          <div class="sc${bd && bs > rs ? ' win' : ''}">${bs ?? ''}</div>
        </div>
      </div>`;
    }).join('') : '<div class="hint">No schedule in this bundle.</div>'}`;
}

// ---------------------------------------------------------------- picklist

function viewPicklist() {
  const b = state.bundle;
  const pl = b.picklist || {};
  const byTeam = b.analytics?.teams || {};
  const cols = 'grid-template-columns:36px 64px minmax(0,1fr) 78px';
  const listOf = (order, title, note) => `
    <div class="tbl stack-narrow">
      <div class="cap"><span class="t">${title}</span><span class="n">${note}</span></div>
      ${(order || []).length ? order.map((team, i) => {
        const t = byTeam[String(team)] || byTeam[team] || {};
        return `<div class="r${Number(team) === ourTeam() ? ' ours' : ''}" style="${cols}">
          <span class="tnum" style="color:var(--t5)">${i + 1}</span>
          <span class="tno lead">${team}</span>
          <span class="nm" data-k="NAME">${esc(t.name || '')}</span>
          <span class="num tnum" data-k="FUEL">${t.estimated?.matches ? num(t.estimated.avgFuel, 0) : '—'}</span>
        </div>`;
      }).join('') : '<div class="empty">Empty — nothing ordered on the hub yet.</div>'}
    </div>`;

  $('#view').innerHTML = `${eventPicker()}
    <div class="hint">The board as the strategy lead last left it on the hub. Read-only here —
      ordering happens on the dashboard, and this copy follows within the minute.</div>
    ${listOf(pl.order, 'FIRST PICK', 'best robot left')}
    ${listOf(pl.order2, 'SECOND PICK', 'best complement to ours')}
    <div class="tbl">
      <div class="cap"><span class="t">DO NOT PICK</span><span class="n">${(pl.dnp || []).length}</span></div>
      ${(pl.dnp || []).length
        ? `<div class="r" style="grid-template-columns:1fr">${(pl.dnp || []).map((t) => `<span class="chip bad" style="margin-right:6px">${t}</span>`).join('')}</div>`
        : '<div class="empty">Nobody excluded.</div>'}
    </div>`;
}

// ------------------------------------------------------------------ health

function viewHealth() {
  const b = state.bundle;
  const cov = b.analytics?.coverage || {};
  const rep = b.analytics?.scoreReport || {};
  const flags = b.flags || [];
  const hot = rep.biasPct;
  $('#view').innerHTML = `${eventPicker()}
    <div class="tiles">
      <div class="tile"><div class="k">COVERAGE</div><div class="v tnum">${num(cov.pct, 0)}<small>%</small></div>
        <div class="c">${cov.robotsScouted || 0} of ${cov.robotsExpected || 0} robot-matches</div></div>
      <div class="tile"><div class="k">SCOUTS VS OFFICIAL</div><div class="v tnum">${num(rep.medianPct, 0)}<small>%</small></div>
        <div class="c">median over ${rep.compared || 0} alliance-matches</div></div>
      <div class="tile"><div class="k">CALIBRATION</div><div class="v tnum">${b.multipliersFittedFrom || 0}</div>
        <div class="c">${b.multipliersFittedFrom ? 'windows fitted' : 'priors only — not yet fitted'}</div></div>
      <div class="tile${flags.length ? ' alert' : ''}"><div class="k">FLAGGED MATCHES</div>
        <div class="v tnum">${flags.length}</div><div class="c">${flags.length ? 'listed below' : 'nothing flagged'}</div></div>
    </div>
    ${hot == null ? '' : `<div class="hint">Scouts are calling shooting
      <b>${hot > 0 ? `${num(hot, 0)}% hot` : `${num(-hot, 0)}% cold`}</b> against the official
      totals. The solver corrects for it either way — this is the number to mention at a
      scout briefing, not a reason to distrust a fuel column.</div>`}
    <div class="tbl stack-narrow">
      <div class="cap"><span class="t">FLAGS</span><span class="n">${flags.length}</span></div>
      ${flags.length ? flags.map((f) => `<div class="r" style="grid-template-columns:minmax(0,1fr) 120px">
          <span class="lead">${esc(f.match_key || f.matchKey || '')}
            <span class="chip warn" style="margin-left:6px">${esc(f.kind)}</span></span>
          <span class="nm" data-k="DETAIL">${esc(f.detail || '')}</span>
        </div>`).join('') : '<div class="empty">No match has been flagged.</div>'}
    </div>
    <div class="hint">Per-scout quality scores are not mirrored. They name people and grade
      them, the hub only ever shows them to the lead, and a copy on the open internet behind
      one shared code is not the place to relax that.</div>`;
}

// ------------------------------------------------------------------ backup

async function viewBackup() {
  const b = state.bundle;
  const ek = encodeURIComponent(state.eventKey);
  $('#view').innerHTML = `${eventPicker()}
    <div class="eyebrow">IF THE HUB IS GONE</div>
    <div class="hint">These are the files that put the event back. The JSON is the same shape
      the hub's own export is, so it goes straight into <b>/api/import</b> on a fresh laptop —
      re-importing is a no-op if some of it is already there, so it is always safe to try.
      The CSVs are the dashboard's, carried across unchanged.</div>
    <div class="btns">
      <a class="btn go" href="${esc(signed(`/api/export?event=${ek}`))}" download>DOWNLOAD JSON — RESTORES A HUB</a>
      <a class="btn" href="${esc(signed(`/api/export.csv?event=${ek}&table=teams`))}" download>TEAM SUMMARY CSV</a>
      <a class="btn" href="${esc(signed(`/api/export.csv?event=${ek}&table=lovat`))}" download>LOVAT CSV</a>
    </div>
    <div class="tiles">
      <div class="tile"><div class="k">SCOUT ENTRIES HELD</div><div class="v tnum">${(b.scout || []).length}</div></div>
      <div class="tile"><div class="k">PIT ENTRIES HELD</div><div class="v tnum">${(b.pit || []).length}</div></div>
      <div class="tile"><div class="k">PHOTOS HELD</div><div class="v tnum">${b.mirror?.photos ?? 0}<small>/${(b.photoIds || []).length}</small></div></div>
      <div class="tile"><div class="k">CAPTURED</div><div class="v tnum" style="font-size:19px">${esc(ago(b.capturedAt))}</div></div>
    </div>
    <div class="tbl stack-narrow" id="hist">
      <div class="cap"><span class="t">EVERY COPY THIS MIRROR HOLDS</span><span class="n">loading…</span></div>
    </div>
    <div class="hint">Older copies are kept because "the database looks wrong" is one of the
      failures this exists for. Downloading yesterday's is a link, not a recovery procedure.</div>`;

  const h = await api(`/api/history?event=${ek}`).catch(() => ({ revisions: [] }));
  const cols = 'grid-template-columns:minmax(0,1fr) 76px 76px 96px';
  $('#hist').innerHTML = `
    <div class="cap"><span class="t">EVERY COPY THIS MIRROR HOLDS</span>
      <span class="n">${(h.revisions || []).length}</span></div>
    <div class="hd" style="${cols}"><span>RECEIVED</span><span class="num">SCOUTED</span>
      <span class="num">PLAYED</span><span class="num">DOWNLOAD</span></div>
    ${(h.revisions || []).map((r) => `<div class="r" style="${cols}">
      <span class="lead">${esc(ago(r.receivedAt))}
        <span class="band">· rev ${r.revision} · ${Math.round((r.bytes || 0) / 1024)} KB</span></span>
      <span class="num tnum" data-k="SCOUTED">${r.scoutEntries ?? '—'}</span>
      <span class="num tnum" data-k="PLAYED">${r.matchesPlayed ?? '—'}</span>
      <span class="num" data-k="DOWNLOAD"><a href="${esc(signed(`/api/export?event=${ek}&rev=${r.revision}`))}" download>JSON</a></span>
    </div>`).join('') || '<div class="empty">Nothing stored yet.</div>'}`;
}

// ------------------------------------------------------------ team detail

function openTeam(team) {
  const b = state.bundle;
  const t = (b.analytics?.teams || {})[String(team)] || (b.analytics?.teams || {})[team];
  if (!t) return;
  const ex = t.exact || {}; const est = t.estimated || {}; const ob = t.observed || {};
  const pit = (b.pit || []).find((p) => Number(p.team) === Number(team));
  const shots = (b.photoIds || []).filter((p) => Number(p.team) === Number(team));
  const rec = ex.record ? `${ex.record.wins}-${ex.record.losses}-${ex.record.ties}` : '—';

  const cell = (k, v) => `<div><div class="k">${k}</div><div class="v tnum">${v}</div></div>`;
  $('#sheetCard').innerHTML = `
    <button class="x" id="sheetX">CLOSE</button>
    <h2>${team} <span style="color:var(--t4);font-size:16px">${esc(t.name || '')}</span></h2>
    <div class="who">${t.matchesScouted} match(es) scouted · ${ex.matchesWithOfficial || 0} with an official result</div>
    <div class="kv">
      ${cell('RANK', ex.rank ?? '—')}
      ${cell('RECORD', rec)}
      ${cell('AVG FUEL', est.matches ? `${num(est.avgFuel, 0)}<span class="band"> ±${num(est.band, 0)}</span>` : '—')}
      ${cell('EPA', num(t.epa?.epa, 0))}
      ${cell('BEST CLIMB', esc(ex.bestClimb || '—'))}
      ${cell('TOWER PTS', num(ex.avgTowerPoints, 1))}
      ${cell('AUTO CLIMB', `${num(ex.autoClimbRate, 0)}<small>%</small>`)}
      ${cell('AVG RP', num(ex.avgRP, 2))}
      ${cell('STOCKPILES', `${num(ob.stockpileRate, 0)}<small>%</small>`)}
      ${cell('FEEDS', `${num(ob.feedRate, 0)}<small>%</small>`)}
      ${cell('DEFENCE PLAYED', `${num(ob.defenseSecs, 0)}<small>s</small>`)}
      ${cell('DEFENCE FACED', ob.defenseFacedSecs == null ? '—' : `${num(ob.defenseFacedSecs, 0)}<small>s</small>`)}
      ${cell('DIED', `${num(ob.diedRate, 0)}<small>%</small>`)}
      ${cell('NO SHOW', `${num(ob.noShowRate, 0)}<small>%</small>`)}
      ${cell('START ZONE', esc(ob.startZone || '—'))}
      ${cell('LOVAT FUEL', t.lovat?.matches ? num(t.lovat.avgFuel, 0) : '—')}
    </div>
    ${pit ? pitBlock(pit) : ''}
    ${shots.length ? `<div class="eyebrow" style="margin-top:16px">PIT PHOTOS</div>
      <div class="shots">${shots.map((p) =>
        `<img loading="lazy" alt="pit photo of team ${team}" src="${esc(signed(`/api/photo/${encodeURIComponent(p.photoId)}`))}">`).join('')}</div>` : ''}
    ${(t.notes || []).length ? `<div class="eyebrow" style="margin-top:16px">SCOUT NOTES</div>
      <div class="notes">${t.notes.slice(0, 40).map((n) => `<div class="note">
        <div class="m">${esc(n.matchKey || '')} · ${esc(n.scoutId || '')}</div>${esc(n.note)}</div>`).join('')}</div>` : ''}`;
  $('#sheet').classList.remove('hide');
  $('#sheetX').onclick = closeSheet;
}

function pitBlock(pit) {
  const p = pit.payload || {};
  const skip = new Set(['photos', 'photo', 'photoIds', 'images']);
  const fields = Object.entries(p)
    .filter(([k, v]) => !skip.has(k) && v !== null && v !== '' && typeof v !== 'object');
  if (!fields.length) return '';
  return `<div class="eyebrow" style="margin-top:16px">PIT SCOUTING</div>
    <div class="kv">${fields.map(([k, v]) =>
      `<div><div class="k">${esc(k.replace(/([A-Z])/g, ' $1').toUpperCase())}</div>
       <div class="v" style="font-size:15px">${esc(typeof v === 'boolean' ? (v ? 'yes' : 'no') : v)}</div></div>`).join('')}</div>`;
}

function closeSheet() { $('#sheet').classList.add('hide'); $('#sheetCard').innerHTML = ''; }
$('#sheet').addEventListener('click', (e) => { if (e.target.id === 'sheet') closeSheet(); });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeSheet(); });

// -------------------------------------------------------------------- wire

$('#unlock').onclick = unlock;
$('#pin').addEventListener('keydown', (e) => { if (e.key === 'Enter') unlock(); });
$('#tabs').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-tab]');
  if (!btn) return;
  state.tab = btn.dataset.tab;
  $('#tabs').querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === btn));
  render();
});

/** Pull again on a timer, and whenever the phone comes back from the lock
 *  screen - a page left open in a pocket all afternoon must not be the reason
 *  somebody reads a two-hour-old fuel number as current. */
async function refresh() {
  if ($('#app').classList.contains('hide')) return;   // still on the lock screen
  try { await boot(); } catch { freshness(); }
}
setInterval(() => { if (!document.hidden) refresh(); }, 60000);
document.addEventListener('visibilitychange', () => {
  if (document.hidden) return;
  freshness();          // the age is wrong the instant it comes back
  refresh();
});
// The age on screen has to keep moving even when nothing new arrives - but not
// while the page is in a pocket. The 60s pull above already checks this; this
// one did not, so a tab left open kept rewriting the banner all afternoon.
// Repainted on the way back so it is never showing a stale age on screen.
setInterval(() => { if (state.bundle && !document.hidden) freshness(); }, 15000);

try { state.token = localStorage.getItem(TOKEN_KEY); } catch { /* private mode */ }
boot().catch(() => showLock());
