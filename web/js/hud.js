// Phone HUD. Landscape, two thumbs: left picks the rate, right holds while shooting.
// Screens and every literal value come from design/Phone *.dc.html.

import * as db from './db.js';
import * as net from './net.js';
import * as fs from './fullscreen.js';
import { loadRules, phases, phaseAt, hubActive, matchSeconds, intensityBuckets } from './game2026.js';

const $ = (s) => document.querySelector(s);
const SCREENS = ['seat', 'standby', 'offline', 'live', 'after', 'bumped'];

// The ladder reads top-down fastest-first and uses the mock's wording.
const LADDER = [
  { id: 'dumping', label: 'DUMPING' },
  { id: 'steady',  label: 'STEADY' },
  { id: 'trickle', label: 'A TRICKLE' },
];
const DRIVE = ['rough', 'okay', 'solid', 'great', 'best'];
const DEFENSE = ['not at all', 'a little', 'some', 'a lot'];
const WRONGS = [
  { k: 'died',    a: 'STOPPED', b: 'MOVING' },
  { k: 'tipped',  a: 'TIPPED',  b: '' },
  { k: 'noShow',  a: 'NO-SHOW', b: '' },
  { k: 'fouls',   a: 'LOTS OF', b: 'FOULS' },
];
const CLIMBS = ['None', 'Level1', 'Level2', 'Level3'];

const seat = {
  scout: localStorage.getItem('scoutName') || '',
  alliance: localStorage.getItem('alliance') || '',
  station: Number(localStorage.getItem('station') || 0),
};

let screen = 'seat';
let matches = [];
let currentMatch = null;
let autoWinner = null;
let entry = null;
let rate = 'steady';
let eventKey = localStorage.getItem('eventKey') || '';
let wakeLock = null;
let history = [];
let SEATS = {};
let MATCH_CLOCKS = {};

// Practice mode: the real HUD against a fake match, saving nothing. This is how
// a lead trains someone in two minutes without touching the event's data.
const PRACTICE = new URLSearchParams(location.search).has('practice');

// ------------------------------------------------------------------ clock
const clock = {
  running: false, offset: 0, startedAt: 0,
  startedAtServer: null,   // shared origin, in server seconds
  sharedBy: null,          // who tapped first
  elapsed() {
    if (this.startedAtServer != null && this.running) {
      return net.serverNow() - this.startedAtServer;
    }
    return this.running ? this.offset + (Date.now() - this.startedAt) / 1000 : this.offset;
  },
  start() { if (!this.running) { this.startedAt = Date.now(); this.running = true; } },
  pause() {
    if (this.running) { this.offset = this.elapsed(); this.running = false; this.startedAtServer = null; }
  },
  reset() { this.running = false; this.offset = 0; this.startedAtServer = null; this.sharedBy = null; },
  /** Adopt the shared clock. Accuracy needs the three scouts to agree, not to
      match the buzzer, so whoever tapped first defines the timeline. */
  adopt(rec) {
    if (!rec || this.startedAtServer != null) return;
    this.startedAtServer = rec.startedAt;
    this.sharedBy = rec.by;
    this.running = true;
    if (entry) { entry.payload.clockShared = true; entry.payload.clockBy = rec.by; }
  },
};

// ---------------------------------------------------------------- helpers
const fmt = (t) => { const s = Math.max(0, Math.floor(t)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const rateOf = (id) => (intensityBuckets().find((b) => b.id === id) || { prior: 3.5 }).prior;
function buzz(ms = 12) { if (navigator.vibrate) { try { navigator.vibrate(ms); } catch {} } }

function show(name) {
  screen = name;
  for (const s of SCREENS) $(`#s-${s}`).classList.toggle('hide', s !== name);
  if (name === 'live' || name === 'standby') acquireWake(); else releaseWake();
}

// Keep-awake for the whole match; released at the buzzer.
//
// navigator.wakeLock needs a secure context, and the hub serves plain HTTP on a
// LAN address, so on a phone it is simply absent. During a match that rarely
// bites (the scout is tapping constantly, which resets the idle timer) but on
// the standby screen the phone sleeps and the scout misses the auto-arm.
// The fallback is the long-standing one: a tiny silent looping video, which
// keeps the screen awake with no secure context required.
let wakeVideo = null;

function makeWakeVideo() {
  const v = document.createElement('video');
  v.setAttribute('playsinline', '');
  v.setAttribute('muted', '');
  v.muted = true;
  v.loop = true;
  v.style.cssText = 'position:fixed;width:1px;height:1px;opacity:0;pointer-events:none;left:0;top:0';
  // a minimal silent mp4, base64 - no network fetch
  v.src = 'data:video/mp4;base64,AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAAr1tZGF0AAACrgYF//+q3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE0OCByMjYwMSBhMGNkN2QzIC0gSC4yNjQvTVBFRy00IEFWQyBjb2RlYyAtIENvcHlsZWZ0IDIwMDMtMjAxNSAtIGh0dHA6Ly93d3cudmlkZW9sYW4ub3JnL3gyNjQuaHRtbAAAAAFljb2RlYwAAAAZzdHRzAAAAAAAAAAEAAAABAAAEAAAAABxzdHNjAAAAAAAAAAEAAAABAAAAAQAAAAEAAAAUc3RzegAAAAAAAAAAAAAAAQAAABRzdGNvAAAAAAAAAAEAAAAs';
  document.body.appendChild(v);
  return v;
}

async function acquireWake() {
  try {
    if ('wakeLock' in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      return;
    }
  } catch { /* present but refused; fall through */ }
  if (!wakeVideo) wakeVideo = makeWakeVideo();
  try { await wakeVideo.play(); } catch { /* needs a gesture; the next tap gets it */ }
}

function releaseWake() {
  try { wakeLock && wakeLock.release(); } catch {}
  wakeLock = null;
  if (wakeVideo) { try { wakeVideo.pause(); } catch {} }
}

/** True when something is actually holding the screen awake. */
export function wakeHeld() {
  return !!wakeLock || !!(wakeVideo && !wakeVideo.paused);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden && screen === 'live') acquireWake(); });

function newEntry(match, team) {
  return {
    eventKey: (match && match.eventKey) || eventKey,
    matchKey: match ? match.matchKey : `manual-${Date.now()}`,
    matchLabel: match ? match.label : 'Manual entry',
    team, alliance: seat.alliance, station: seat.station, scoutId: seat.scout,
    payload: {
      intervals: [], feedIntervals: [], defenseIntervals: [], preload: 0,
      autoTower: 'None', endgameTower: 'None',
      driverRating: 0, defenseRating: 0,
      died: false, tipped: false, noShow: false, fouls: false, note: '',
      // whose timeline these intervals are on. The server may only re-anchor
      // to TBA's actual_time when the phone was on the shared clock.
      clockShared: false, clockBy: null,
    },
  };
}

let saveTimer = null;
function autosave() {
  if (PRACTICE) return;              // nothing a trainee does is ever stored
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    if (!entry || !entry.team) return;
    try { await db.saveScout(entry); net.flush(); } catch (e) { console.error(e); }
  }, 400);
}

const ballsSoFar = () =>
  Math.round((entry ? entry.payload.intervals : []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0));

// ═══════════════════════════════════════════════════════════ TAKE A SEAT
function renderSeat() {
  $('#myInitials').textContent = seat.scout || '--';
  const el = $('#stations');
  if (!el.children.length) {
    for (const al of ['red', 'blue']) {
      for (let n = 1; n <= 3; n++) {
        const d = document.createElement('div');
        d.className = `st ${al}`;
        d.dataset.al = al; d.dataset.n = String(n);
        d.innerHTML = `<span class="bar"></span><span class="al">${al.toUpperCase()}</span>
          <span class="nu">${n}</span><span class="wh"></span>`;
        d.onclick = () => { seat.alliance = al; seat.station = n; buzz(); renderSeat(); };
        el.appendChild(d);
      }
    }
  }
  for (const d of el.children) {
    const mine = d.dataset.al === seat.alliance && Number(d.dataset.n) === seat.station;
    d.classList.toggle('on', mine);
    const claim = SEATS[`${d.dataset.al}${d.dataset.n}`];
    const taken = claim && claim.deviceId !== db.deviceId();
    const wh = d.querySelector('.wh');
    wh.textContent = mine ? `ME · ${seat.scout || '--'}`
      : taken ? String(claim.scoutId || '??').toUpperCase() : 'OPEN';
    // OPEN is the only green one - a name means someone is already in that chair
    wh.classList.toggle('open', !mine && !taken);
  }
  if (seat.alliance && seat.station) {
    $('#signExample').textContent = `${seat.alliance.toUpperCase()} ${seat.station}`;
  }
  const m = pickCurrentMatch();
  const t = teamForSeat(m);
  $('#seatTeam').textContent = t || '—';
  $('#seatBlurb').textContent = t
    ? `in ${m.label.toLowerCase()}, then whoever sits in ${seat.alliance.toUpperCase()} ${seat.station} after that`
    : 'pick a station to see who you\'ll watch';
  $('#sitSub').textContent = seat.scout && seat.alliance && seat.station
    ? (m ? `${m.label.toLowerCase()} is next` : 'ready') : 'pick a station first';
  $('#seatHub').textContent = net.state.base ? `${net.state.base.replace(/^https?:\/\//, '')} · connected` : 'not found yet';
  $('#seatDot').className = 'dot' + (net.state.online ? '' : ' amber');
  $('#seatEvent').textContent = eventKey
    ? `${eventKey.toUpperCase()} · HUB ${net.state.online ? 'FOUND' : 'NOT FOUND'}`
    : 'NO EVENT SET ON THE HUB';
}

$('#btnInitials').onclick = () => {
  const v = prompt('Your initials', seat.scout || '');
  if (v === null) return;
  seat.scout = v.trim().toUpperCase().slice(0, 4);
  localStorage.setItem('scoutName', seat.scout);
  renderSeat();
};

$('#btnSit').onclick = () => {
  if (!seat.scout || !seat.alliance || !seat.station) { buzz(30); return; }
  localStorage.setItem('alliance', seat.alliance);
  localStorage.setItem('station', String(seat.station));
  if (PRACTICE) { loadMatch(practiceMatch()); show('live'); renderLive(); return; }
  net.setIdentity({ scoutId: seat.scout, seat: `${seat.alliance}${seat.station}` });
  net.api('/api/seat', { method: 'POST', body: JSON.stringify({
    alliance: seat.alliance, station: seat.station,
    scoutId: seat.scout, deviceId: db.deviceId(),
  }) }).then((r) => { SEATS = r.seats || SEATS; }).catch(() => {});
  loadMatch(pickCurrentMatch());
  goStandbyOrLive();
};

// ═══════════════════════════════════════════════════════════ LIVE MATCH
function renderLadder() {
  const el = $('#ladder');
  if (!el.children.length) {
    for (const b of LADDER) {
      const d = document.createElement('div');
      d.className = 'rate';
      d.dataset.id = b.id;
      d.innerHTML = `<span class="nm">${b.label}</span><span class="vl">${rateOf(b.id)}</span>`;
      d.onclick = () => { rate = b.id; buzz(); renderLadder(); paintHold(); };
      el.appendChild(d);
    }
  }
  for (const d of el.children) d.classList.toggle('on', d.dataset.id === rate);
}

function paintHold(holding = false, frac = 0) {
  $('#holdpad').classList.toggle('on', holding);
  $('#holdMeter').style.height = holding ? `${Math.min(100, frac * 100)}%` : '0';
  $('#holdLine').style.bottom = holding ? `${Math.min(100, frac * 100)}%` : '0';
  $('#holdLabel').textContent = holding ? 'HOLDING' : 'HOLD WHILE SHOOTING';
  $('#holdRate').textContent = rateOf(rate);
}

/** Hold-and-release recorder. Tap and hold only — no drags (Android owns edge swipes). */
function holdRecorder(el, list, onPaint) {
  let t0 = null, raf = null;
  const begin = (ev) => {
    if (t0 !== null) return;
    ev.preventDefault();
    if (!clock.running) return;
    t0 = clock.elapsed();
    buzz(12);
    const tick = () => {
      if (t0 === null) return;
      onPaint && onPaint(true, Math.min(1, (clock.elapsed() - t0) / 5));
      raf = requestAnimationFrame(tick);
    };
    tick();
  };
  const end = () => {
    if (t0 === null) return;
    cancelAnimationFrame(raf);
    const t1 = clock.elapsed();
    const start = t0; t0 = null;
    onPaint && onPaint(false, 0);
    if (t1 - start < 0.15) return;
    const ph = phaseAt(start);
    list().push({ start: +start.toFixed(2), end: +t1.toFixed(2), phase: ph ? ph.id : null, intensity: rate });
    buzz(12);
    renderLive(); autosave();
  };
  el.addEventListener('pointerdown', begin);
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  el.addEventListener('pointerleave', end);
  el.addEventListener('contextmenu', (e) => e.preventDefault());
}

function renderStrip() {
  const el = $('#lvStrip');
  const t = clock.elapsed();
  const now = phaseAt(t);
  if (el.children.length !== phases().length) {
    el.innerHTML = phases().map((p) => `<span style="flex:${(p.end - p.start) / 10}"><span class="fill"></span></span>`).join('');
  }
  phases().forEach((p, i) => {
    const s = el.children[i];
    const act = hubActive(p.id, seat.alliance, autoWinner);
    s.className = now && now.id === p.id ? 'now' : (act === true ? 'live' : '');
    const fill = s.querySelector('.fill');
    if (fill) fill.style.width = (now && now.id === p.id)
      ? `${Math.min(100, ((t - p.start) / (p.end - p.start)) * 100)}%` : '0';
  });
}

function renderRunbar() {
  const el = $('#lvRunbar');
  const total = matchSeconds();
  const ivs = entry ? entry.payload.intervals : [];
  el.innerHTML = ivs.map((iv) =>
    `<span class="r ${iv.intensity}" style="left:${(iv.start / total) * 100}%;width:${Math.max(0.8, ((iv.end - iv.start) / total) * 100)}%"></span>`
  ).join('') + `<span class="cur" style="left:${Math.min(100, (clock.elapsed() / total) * 100)}%"></span>`;
}

function renderLive() {
  if (!entry) return;
  const t = clock.elapsed();
  const now = phaseAt(t);
  $('#lvSeat').textContent = `${seat.alliance.toUpperCase()} ${seat.station}`;
  $('#lvMeta').textContent = PRACTICE
    ? 'PRACTICE · NOTHING IS SAVED'
    : `${(currentMatch ? currentMatch.label : 'MANUAL').toUpperCase()} · ${seat.scout}`;
  $('#lvTeam').textContent = entry.team || '—';
  $('#lvTeamName').textContent = (teamName(entry.team) || '').toUpperCase();
  $('#lvMatchOf').textContent = matchOfText();
  $('#lvClock').textContent = fmt(t);
  $('#lvBalls').textContent = ballsSoFar();
  $('#lvRuns').textContent = entry.payload.intervals.length;
  $('#lvClimb').textContent = (now && now.id === 'auto')
    ? (entry.payload.autoTower === 'Level1' ? 'L1' : '—')
    : (entry.payload.endgameTower === 'None' ? '—' : entry.payload.endgameTower.replace('Level', 'L'));
  const inAuto = now && now.id === 'auto';
  $('#btnClimb').querySelector('.a').textContent = inAuto ? 'AUTO CLIMB' : 'CLIMB';
  $('#climbSub').textContent = inAuto
    ? (entry.payload.autoTower === 'Level1' ? 'L1 · 15 PTS' : 'TAP IF THEY DO')
    : (entry.payload.endgameTower === 'None' ? 'NOT SET' : `${entry.payload.endgameTower.replace('Level', 'L')} SET`);
  $('#btnClimb').classList.toggle('on', inAuto && entry.payload.autoTower === 'Level1');
  $('#lvLink').textContent = !net.state.online ? 'SAVING LOCALLY'
    : (clock.sharedBy && clock.sharedBy !== seat.scout) ? `CLOCK FROM ${String(clock.sharedBy).toUpperCase()}`
    : 'STATION LINKED';
  $('#lvDot').className = 'dot' + (net.state.online ? '' : ' amber');

  const st = $('#lvShotState');
  if (!clock.running && t === 0) { st.textContent = 'TAP THE PAD TO START'; st.className = 'shotstate unknown'; }
  else if (!now) { st.textContent = 'MATCH OVER'; st.className = 'shotstate dead'; }
  else {
    const act = hubActive(now.id, seat.alliance, autoWinner);
    const left = Math.ceil(now.end - t);
    if (act === true) { st.textContent = `SHOTS COUNT · ${left}s LEFT`; st.className = 'shotstate'; }
    else if (act === false) { st.textContent = `SHOTS DON'T COUNT · ${left}s LEFT`; st.className = 'shotstate dead'; }
    else { st.textContent = `${now.label} · ${left}s LEFT`; st.className = 'shotstate unknown'; }
  }
  renderStrip(); renderRunbar(); renderLadder();
}

// ═══════════════════════════════════════════════════════ AFTER THE BUZZER
function renderAfter() {
  if (!entry) return;
  $('#afTitle').textContent = `${(currentMatch ? currentMatch.label : 'MATCH').toUpperCase()} DONE`;
  $('#afMeta').textContent = `${entry.team} · ${ballsSoFar()} BALLS · ${entry.payload.endgameTower === 'None' ? 'NO CLIMB' : entry.payload.endgameTower.replace('Level', 'L') + ' CLIMB'}`;
  const nx = nextMatch();
  $('#afNext').textContent = nx ? `NEXT: ${nx.label.toUpperCase()}` : 'NEXT: —';
  $('#afBalls').textContent = ballsSoFar();
  $('#afClimb').textContent = entry.payload.endgameTower === 'None' ? 'none' : entry.payload.endgameTower.replace('Level', 'L');
  $('#afAuto').textContent = entry.payload.autoTower === 'None' ? 'no' : 'yes · L1';
  $('#afRuns').textContent = entry.payload.intervals.length;
  $('#sendSub').textContent = net.state.online ? 'both thumbs free' : 'saves now, sends later';

  buildSteps('#driveSteps', DRIVE, 'driverRating');
  buildSteps('#defSteps', DEFENSE, 'defenseRating');

  const w = $('#wrongs');
  if (!w.children.length) {
    for (const it of WRONGS) {
      const d = document.createElement('div');
      d.className = 'wrong'; d.dataset.k = it.k;
      d.innerHTML = it.b ? `${it.a}<br>${it.b}` : it.a;
      d.onclick = () => { entry.payload[it.k] = !entry.payload[it.k]; buzz(); renderAfter(); autosave(); };
      w.appendChild(d);
    }
  }
  for (const d of w.children) d.classList.toggle('on', !!entry.payload[d.dataset.k]);
}

function buildSteps(sel, labels, key) {
  const el = $(sel);
  if (el.children.length !== labels.length) {
    el.innerHTML = '';
    labels.forEach((lab, i) => {
      const d = document.createElement('div');
      d.className = 'step';
      // stepped heights: later options are taller, so the scale reads as a ramp
      d.style.height = `${52 + i * 14}%`;
      d.textContent = lab;
      d.onclick = () => { entry.payload[key] = i + 1; buzz(); renderAfter(); autosave(); };
      el.appendChild(d);
    });
  }
  [...el.children].forEach((d, i) => d.classList.toggle('on', entry.payload[key] === i + 1));
}

$('#afNote').oninput = () => { entry.payload.note = $('#afNote').value; autosave(); };
$('#btnSend').onclick = async () => {
  if (!entry || !entry.team) return;
  entry.payload.note = $('#afNote').value;
  if (PRACTICE) {
    buzz(30);
    loadMatch(practiceMatch());
    clock.reset();
    show('live'); renderLive();
    return;
  }
  await db.saveScout(entry);
  net.flush();
  buzz(30);
  await refreshHistory();
  loadMatch(nextMatch() || pickCurrentMatch());
  goStandbyOrLive();
};

// ═══════════════════════════════════════════════════════════ STANDBY/OFFLINE
async function refreshHistory() {
  const rows = await db.all('scout');
  history = rows
    .filter((r) => r.scoutId === seat.scout)
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, 6);
}

function shortCode(label) {
  if (!label) return '—';
  const m = String(label).match(/(\w)\w*\s*(\d+)/);
  return m ? `${m[1].toUpperCase()}${m[2]}` : label.slice(0, 4);
}

function renderStandby() {
  $('#sbSeat').textContent = `${seat.alliance.toUpperCase()} ${seat.station}`;
  $('#sbMeta').textContent = `${seat.scout} · ${history.length} ${history.length === 1 ? 'MATCH' : 'MATCHES'} IN`;
  $('#sbSync').textContent = net.state.queued ? `${net.state.queued} WAITING` : 'EVERYTHING SENT';
  $('#sbDot').className = 'dot' + (net.state.online ? '' : ' amber');

  const m = currentMatch;
  const t = teamForSeat(m);
  $('#sbTeam').textContent = t || '—';
  $('#sbTeamMeta').textContent = t ? (teamName(t) || 'not seen yet') : '—';
  $('#sbUntil').textContent = !m ? 'WAITING FOR THE SCHEDULE'
    : m.status ? `${m.label.toUpperCase()} · ${m.status.toUpperCase()}`
    : `UNTIL ${m.label.toUpperCase()} IS CALLED`;

  const eta = m && m.times && (m.times.estimatedOnFieldTime || m.times.estimatedQueueTime);
  if (eta) {
    const secs = Math.max(0, (eta - Date.now()) / 1000);
    $('#sbCountdown').textContent = fmt(secs);
  } else $('#sbCountdown').textContent = '--:--';

  $('#sbList').innerHTML = history.length ? history.map((h) => `
    <div class="lrow"><span class="code">${shortCode(h.matchLabel)}</span>
      <span class="team">${h.team}</span>
      <span class="desc">${Math.round((h.payload.intervals || []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0))} balls · ${h.payload.endgameTower === 'None' ? 'none' : h.payload.endgameTower.replace('Level', 'L')}</span>
      <span class="tag ${h._queued ? 'queued' : 'sent'}">${h._queued ? 'QUEUED' : 'SENT'}</span></div>`).join('')
    : '<div class="lrow"><span class="desc">nothing logged yet</span></div>';
}

async function renderBumped() {
  await refreshHistory();
  $('#bumpList').innerHTML = history.length ? history.map((h) => `
    <div class="lrow"><span class="code">${shortCode(h.matchLabel)}</span>
      <span class="team">${h.team}</span>
      <span class="desc">${Math.round((h.payload.intervals || []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0))} balls</span>
      <span class="tag sent">SAVED</span></div>`).join('')
    : '<div class="lrow"><span class="desc">nothing logged yet</span></div>';
}

async function renderOffline() {
  $('#offSeat').textContent = `${seat.alliance.toUpperCase()} ${seat.station} · ${seat.scout}`;
  const q = await db.queued();
  const items = db.collapse(q);
  $('#offBlurb').textContent = items.length
    ? `${items.length} ${items.length === 1 ? 'match is' : 'matches are'} saved on this phone and will send themselves the moment the hub is back. You don't need to do anything.`
    : 'Everything you enter is saved on this phone and will send itself the moment the hub is back.';
  $('#offList').innerHTML = items.length ? items.map((it) => {
    const r = it.record;
    return `<div class="lrow"><span class="code">${shortCode(r.matchLabel)}</span>
      <span class="desc">${r.team} · ${Math.round((r.payload.intervals || []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0))} balls</span>
      <span class="tag queued">QUEUED</span></div>`;
  }).join('') : '<div class="lrow"><span class="desc">nothing waiting</span></div>';
}

$('#btnRetry').onclick = () => { net.discover().then(net.flush); };
$('#btnTakeBack').onclick = async () => {
  try {
    await net.api('/api/seat', { method: 'POST', body: JSON.stringify({
      alliance: seat.alliance, station: seat.station,
      scoutId: seat.scout, deviceId: db.deviceId(),
    }) });
  } catch {}
  loadMatch(pickCurrentMatch());
  goStandbyOrLive();
};
$('#btnPickSeat').onclick = () => { show('seat'); renderSeat(); };
$('#btnBackup').onclick = async () => {
  const items = db.collapse(await db.queued());
  const payload = {
    kind: 'frc-rebuilt-scouting-export', version: 1,
    eventKey, exportedAt: Date.now() / 1000,
    scout: items.filter((i) => i.kind === 'scout').map((i) => i.record),
    pit: items.filter((i) => i.kind === 'pit').map((i) => i.record),
  };
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(payload)], { type: 'application/json' }));
  a.download = `scouting-${seat.scout || 'phone'}-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '')}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
};
$('#btnHandover').onclick = async () => {
  const who = prompt('Who is taking over this chair? (initials)', '');
  if (who === null) return;
  const name = who.trim().toUpperCase().slice(0, 4);
  if (!name) return;
  // Anything half-entered belongs to the outgoing scout, so bank it first.
  if (entry && entry.team && entry.payload.intervals.length) {
    await db.saveScout(entry); net.flush();
  }
  seat.scout = name;
  localStorage.setItem('scoutName', name);
  net.setIdentity({ scoutId: name });
  try {
    await net.api('/api/seat', { method: 'POST', body: JSON.stringify({
      alliance: seat.alliance, station: seat.station, scoutId: name, deviceId: db.deviceId(),
    }) });
  } catch { /* offline: the claim syncs when the hub is back */ }
  // fresh entry so the new scout's work is attributed to them
  loadMatch(currentMatch);
  await refreshHistory();
  buzz(20);
  goStandbyOrLive();
};

$('#btnFixPast').onclick = () => { if (history[0]) { entry = history[0]; currentMatch = matches.find((m) => m.matchKey === entry.matchKey) || currentMatch; $('#afNote').value = entry.payload.note || ''; show('after'); renderAfter(); } };

// ══════════════════════════════════════════════════════════════ matches
function teamForSeat(m) {
  if (!m || !seat.alliance || !seat.station) return null;
  const lineup = m[seat.alliance];
  return Array.isArray(lineup) ? lineup[seat.station - 1] : null;
}
function teamName(n) { const t = (window.__teams || []).find((x) => x.team === n); return t ? t.name : ''; }
function practiceMatch() {
  return {
    matchKey: 'practice', label: 'Practice', status: 'On field',
    red: [6059, 9970, 9971], blue: [9972, 9973, 9974],
    breakdown: { autoWinner: 'blue' },   // so the shift strip has real live/dead states
    eventKey: 'practice',
  };
}

function pickCurrentMatch() {
  if (PRACTICE) return practiceMatch();
  for (const st of ['On field', 'On deck', 'Now queuing']) {
    const m = matches.find((x) => x.status === st);
    if (m) return m;
  }
  return matches.find((m) => !m.breakdown) || matches[0] || null;
}
function nextMatch() {
  const i = matches.findIndex((m) => currentMatch && m.matchKey === currentMatch.matchKey);
  return i >= 0 ? matches[i + 1] : null;
}
function matchOfText() {
  if (PRACTICE) return 'TRAINING RUN';
  const mine = matches.filter((m) => teamForSeat(m));
  const i = mine.findIndex((m) => currentMatch && m.matchKey === currentMatch.matchKey);
  return mine.length ? `MATCH ${i + 1} OF ${mine.length}` : 'MATCH — OF —';
}
function loadMatch(m) {
  currentMatch = m;
  const shared = m && MATCH_CLOCKS[m.matchKey];
  autoWinner = (m && m.breakdown && m.breakdown.autoWinner) || null;
  entry = newEntry(m, teamForSeat(m));
  clock.reset();
  rate = 'steady';
  $('#afNote').value = '';
  // joined late? pick up the clock already running for this match
  if (shared && net.serverNow() - shared.startedAt < matchSeconds() + 30) clock.adopt(shared);
}
function goStandbyOrLive() {
  // If our match is already on the field, the scout needs the HUD now, not a countdown.
  if (currentMatch && currentMatch.status === 'On field' && entry && entry.team) {
    show('live'); renderLive(); return;
  }
  if (!net.state.online) { renderOffline(); show('offline'); return; }
  show('standby'); renderStandby();
}

// ══════════════════════════════════════════════════════════════ startup
async function main() {
  await loadRules();
  renderLadder(); paintHold();
  document.addEventListener('pointerdown', () => {
    if (screen === 'live' || screen === 'standby') acquireWake();
  }, { passive: true });

  // Landscape HUD: go chrome-free on the taps the scout already makes.
  fs.install({
    triggers: ['#btnSit', '#holdpad', '#btnStart', '#stations'],
    orientation: 'landscape',
    hintEl: $('#fsHint'),
  });

  // Right pad records shooting; the two left utility buttons record their own runs.
  holdRecorder($('#holdpad'), () => entry.payload.intervals, paintHold);
  holdRecorder($('#btnFeeding'), () => entry.payload.feedIntervals,
    (on) => $('#btnFeeding').classList.toggle('on', on));
  holdRecorder($('#btnDefending'), () => entry.payload.defenseIntervals,
    (on) => $('#btnDefending').classList.toggle('on', on));

  // Tapping the pad before the match starts the clock; that is the only start control.
  $('#holdpad').addEventListener('pointerdown', () => {
    if (clock.running || clock.elapsed() !== 0 || !entry || !entry.team) return;
    clock.start(); buzz(30);
    // Tell the hub so the other scouts on this match share our timeline. If the
    // hub answers with an earlier start (someone beat us to it), we take theirs.
    if (currentMatch && !PRACTICE) {
      net.api('/api/matchstart', { method: 'POST', body: JSON.stringify({
        matchKey: currentMatch.matchKey, scoutId: seat.scout,
      }) }).then((r) => {
        if (r && r.clock) { clock.startedAtServer = null; clock.adopt(r.clock); renderLive(); }
      }).catch(() => { /* offline: our own clock stands */ });
    }
  });

  $('#btnUndo').onclick = () => {
    if (!entry || !entry.payload.intervals.length) { buzz(30); return; }
    entry.payload.intervals.pop(); buzz(20); renderLive(); autosave();
  };
  $('#btnClimb').onclick = () => {
    const ph = phaseAt(clock.elapsed());
    if (ph && ph.id === 'auto') {
      // Auto tower is Level 1 only and worth 15, so it is a toggle, not a cycle.
      entry.payload.autoTower = entry.payload.autoTower === 'Level1' ? 'None' : 'Level1';
    } else {
      const i = CLIMBS.indexOf(entry.payload.endgameTower);
      entry.payload.endgameTower = CLIMBS[(i + 1) % CLIMBS.length];
    }
    buzz(); renderLive(); autosave();
  };

  net.onChange(() => {
    if (screen === 'live') { $('#lvLink').textContent = net.state.online ? 'STATION LINKED' : 'SAVING LOCALLY'; $('#lvDot').className = 'dot' + (net.state.online ? '' : ' amber'); }
    if (screen === 'standby') renderStandby();
    if (screen === 'seat') renderSeat();
    if (screen === 'offline') renderOffline();
  });

  net.setIdentity({ deviceId: db.deviceId(), scoutId: seat.scout,
                    seat: seat.alliance && seat.station ? `${seat.alliance}${seat.station}` : '' });
  const cfg = await net.start();
  if (cfg && cfg.eventKey) { eventKey = cfg.eventKey; localStorage.setItem('eventKey', eventKey); }
  net.api('/api/seats').then((s) => { SEATS = s || {}; if (screen === 'seat') renderSeat(); }).catch(() => {});
  net.on('seats', (msg) => {
    const payload = (msg && msg.seats) ? msg : { seats: msg };
    SEATS = payload.seats || {};
    if (payload.displaced && payload.displaced === db.deviceId()) {
      // our chair was claimed by another phone - stop, do not double-scout
      clock.pause();
      $('#bumpSeat').textContent = `${(payload.seat || '').toUpperCase()} · ${seat.scout}`;
      $('#bumpTitle').textContent =
        `${String(payload.scoutId || 'Someone').toUpperCase()} took ${(payload.seat || 'this chair').toUpperCase()}.`;
      renderBumped();
      show('bumped');
      buzz(60);
      return;
    }
    if (screen === 'seat') renderSeat();
  });

  const applyState = (s) => {
    if (!s || !s.matches) return;
    matches = s.matches.map((m) => ({ ...m, eventKey: s.eventKey }));
    MATCH_CLOCKS = s.matchClocks || {};
    window.__teams = s.teams || [];
    db.cacheSet('state', s);
    if (!currentMatch) loadMatch(pickCurrentMatch());
  };
  try { applyState(await net.api('/api/state')); }
  catch { applyState(await db.cacheGet('state')); }

  await refreshHistory();

  net.on('nexus', () => net.api('/api/state').then(applyState).catch(() => {}));
  net.on('results', () => net.api('/api/state').then(applyState).catch(() => {}));

  // another scout started this match: adopt their clock and jump into the HUD
  net.on('matchStart', (rec) => {
    if (!rec || !currentMatch || rec.matchKey !== currentMatch.matchKey) return;
    if (clock.startedAtServer != null) return;
    clock.adopt(rec);
    if (screen !== 'live' && entry && entry.team) { show('live'); }
    renderLive();
  });
  net.on('matchStatus', ({ match }) => {
    if (!match || !match.label) return;
    const m = matches.find((x) => x.label === match.label);
    if (!m) return;
    m.status = match.status;
    // Nexus arms the screen: when our match takes the field, go straight to the HUD.
    if (match.status === 'On field' && screen === 'standby' && teamForSeat(m)) {
      loadMatch(m); show('live'); renderLive();
    }
  });

  if (PRACTICE) {
    seat.alliance = seat.alliance || 'red';
    seat.station = seat.station || 2;
    seat.scout = seat.scout || 'TRAINEE';
    loadMatch(practiceMatch());
    show('live'); renderLive();
  } else if (seat.scout && seat.alliance && seat.station) {
    loadMatch(pickCurrentMatch()); goStandbyOrLive();
  } else { show('seat'); renderSeat(); }

  setInterval(() => {
    if (screen === 'live') {
      renderLive();
      if (clock.running && clock.elapsed() >= matchSeconds()) {
        clock.pause(); buzz(40); releaseWake();
        $('#afNote').value = entry.payload.note || '';
        show('after'); renderAfter();
      }
    } else if (screen === 'standby') renderStandby();
  }, 200);

  window.addEventListener('beforeunload', (e) => {
    if (entry && entry.payload.intervals.length && !net.state.online) { e.preventDefault(); e.returnValue = ''; }
  });
}

main().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML('afterbegin',
    `<div style="padding:24px;font:600 14px Barlow,sans-serif;color:#ff5c66">Failed to start: ${e.message}</div>`);
});
