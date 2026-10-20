// Phone HUD. Landscape, in one of two layouts:
// - pro: two thumbs - left picks how fast, right holds while they shoot.
// - simple: one big button to hold, and how fast is asked after letting go.
// Both record exactly the same intervals; simple mode only changes when the
// speed gets picked, and the phone writes down which layout made each entry.

import * as db from './db.js';
import * as net from './net.js';
import { every, idleWatch } from './timers.js';
import * as fs from './fullscreen.js';
import { SPEEDS } from './speeds.js';
import { loadRules, phases, phaseAt, hubActive, matchSeconds, intensityBuckets, rules as gameRules } from './game2026.js';

const $ = (s) => document.querySelector(s);
const SCREENS = ['seat', 'standby', 'offline', 'live', 'after', 'bumped'];

// The ladder reads top-down fastest-first. Simple mode lists the same three
// the other way up, slowest first, because there it is a question, not a dial.
// The words live in speeds.js, which the practice tutorial reads too.
const LADDER = SPEEDS;
const speedName = (id) => (LADDER.find((b) => b.id === id) || LADDER[1]).label;
const DRIVE = ['rough', 'okay', 'solid', 'great', 'best'];
const DEFENSE = ['not at all', 'a little', 'some', 'a lot'];
const WRONGS = [
  { k: 'died',      a: 'Stopped', b: 'moving' },
  { k: 'tipped',    a: 'Tipped',  b: '' },
  { k: 'noShow',    a: 'No-show', b: '' },
  { k: 'fouls',     a: 'Lots of', b: 'fouls' },
  // Distinct from STOPPED MOVING (the whole match) and NO-SHOW (never turned
  // up): the robot was there and auto did nothing.
  { k: 'autoFailed', a: 'Auto',   b: 'did nothing' },
  // A climb that was tried and lost is not the same robot as one that never
  // left the floor, and the climb button can only say which level it reached.
  // Hidden the moment a level IS set: a robot cannot both climb and fall.
  { k: 'climbFailed',     a: 'Climb',      b: 'fell off' },
  { k: 'autoClimbFailed', a: 'Auto climb', b: 'fell off' },
];
const CLIMBS = ['None', 'Level1', 'Level2', 'Level3'];
const START_ZONES = [
  { id: 'left', label: 'Left' },
  { id: 'centre', label: 'Centre' },
  { id: 'right', label: 'Right' },
];
// Which lane they lined up in, asked after the buzzer rather than before it:
// the side is visible from the stands as they roll out, the lane often is not
// until they move. Trench and bump are the field's own names - the same two
// Lovat's export uses (server/lovat.py reads them off other teams' scouting),
// so a side and a lane together say exactly what their start position says.
// Kept as a second, optional field rather than five zones in place of three,
// so every entry ever logged still reads as the side it named.
const START_LANES = [
  { id: 'trench', label: 'Trench' },
  { id: 'bump', label: 'Bump' },
];
// The second page of the after screen. All optional, all one tap, none of them
// asked during the match: unanswered stays unknown rather than becoming a no.
const TRAVERSAL = [
  { id: 'trench', label: 'Trench' },
  { id: 'bump', label: 'Bump' },
  { id: 'both', label: 'Both' },
  { id: 'none', label: 'Stayed', sub: 'their side' },
];
const BEACHED = [
  { id: 'fuel', label: 'On the', sub: 'fuel' },
  { id: 'bump', label: 'On the', sub: 'bump' },
  { id: 'both', label: 'Both' },
  { id: 'neither', label: 'No' },
];
// Which part of the tower they used. Two robots that both want the middle of
// the same face cannot climb together, and that is an alliance-selection fact
// nothing else we record can answer.
const CLIMB_SPOTS = [
  { id: 'frontSide', label: 'Front', sub: 'side' },
  { id: 'frontMiddle', label: 'Front', sub: 'middle' },
  { id: 'backSide', label: 'Back', sub: 'side' },
  { id: 'backMiddle', label: 'Back', sub: 'middle' },
];
const AFTER_FLAGS = [
  { k: 'scoresWhileMoving', label: 'Shoots', sub: 'on the move' },
  { k: 'disrupts', label: 'Knocks', sub: 'shots down' },
];
const ACCURACY = ['sprayed it', 'half in', 'mostly in', 'nearly all', 'never missed'];

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
// Simple mode is on unless this phone has been switched to the two-thumb
// layout. A phone that has never chosen is a new scout's phone.
let simple = localStorage.getItem('inputMode') !== 'pro';
// Simple mode: the shooting run that "How fast was that?" is about - an index
// into entry.payload.intervals - until the next run starts. null = none.
let pending = null;
let padHeld = false;
let eventKey = localStorage.getItem('eventKey') || '';
// Which page of the after screen is up. Always 1 when a match ends: the extra
// questions are worth asking, but never at the cost of the next match.
let afterPage = 1;
let wakeLock = null;
let history = [];

// Matches the hub has reconciled against the official totals. The lead sees
// scout reliability on the HEALTH tab; the scout never did, and a scout who
// cannot tell whether their match landed cannot get better at it. Kept as a
// marker inside the row that already exists rather than as new chrome - a
// standby screen is not the place to grow a panel.
const RECONCILED = new Set(JSON.parse(localStorage.getItem('reconciled') || '[]'));
function markReconciled(matchKey) {
  if (!matchKey || RECONCILED.has(matchKey)) return;
  RECONCILED.add(matchKey);
  // last few matches is all the standby list ever shows
  localStorage.setItem('reconciled', JSON.stringify([...RECONCILED].slice(-40)));
  if (screen === 'standby') renderStandby();
}
let SEATS = {};
let MATCH_CLOCKS = {};
// The ids of the rows still sitting in the outbound queue, so the standby list
// can say QUEUED rather than SENT. It read a `_queued` flag on the row that
// nothing ever set, so every match said SENT - including on a phone that had
// never once reached the hub, which is the exact moment a scout looks.
let QUEUED_IDS = new Set();

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
// m:ss during a match; h:mm:ss once the standby countdown is looking at a match
// several slots away, where "195:55" reads as nonsense.
const fmt = (t) => {
  const s = Math.max(0, Math.floor(t));
  const mm = Math.floor(s / 60) % 60;
  if (s >= 3600) return `${Math.floor(s / 3600)}:${String(mm).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
const rateOf = (id) => (intensityBuckets().find((b) => b.id === id) || { prior: 3.5 }).prior;
function buzz(ms = 12) { if (navigator.vibrate) { try { navigator.vibrate(ms); } catch {} } }

/**
 * Standby goes dark when nobody is touching the phone.
 *
 * The screen stays awake - a scout has to see the match arm itself, and once a
 * phone sleeps the browser suspends the page and it can neither show that nor
 * buzz about it. But there is no reason for it to be *bright* through a
 * six-minute gap between matches. The `.idle` class in hud.css drops the
 * background to black and takes down the scanlines, the two full-screen radial
 * gradients and every glow, which on the OLED panels these phones have is most
 * of the draw. Any touch brings it straight back, as does the match arming and
 * the countdown coming inside a minute.
 */
const standbyIdle = idleWatch(60000, {
  onIdle: () => { if (screen === 'standby') $('#s-standby').classList.add('idle'); },
  onWake: () => $('#s-standby').classList.remove('idle'),
});

function show(name) {
  screen = name;
  for (const s of SCREENS) $(`#s-${s}`).classList.toggle('hide', s !== name);
  if (name === 'live' || name === 'standby') acquireWake(); else releaseWake();
  // Arriving anywhere is activity: never land on an already-dark screen.
  standbyIdle.wake();
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
      intervals: [], feedIntervals: [], defenseIntervals: [], preload: null,
      autoTower: 'None', endgameTower: 'None',
      driverRating: 0, defenseRating: 0,
      died: false, tipped: false, noShow: false, fouls: false, note: '',
      // Pre-match facts, entered on standby while the robot lines up. null
      // means "nobody said", which is not the same as zero - preload used to
      // default to 0 and every team on the dashboard read "average preload 0".
      startPosition: null, autoFailed: false,
      // Which robot they were blocking. Defence was already recorded as
      // intervals but never against whom, so "they shut our shooter down" could
      // not be looked up. Asked once, after the buzzer, only if defence happened.
      defenseTarget: null,
      // The second page of the after screen. Every one of these is something
      // other teams' scouts answer every match and ours never could, so a robot
      // read "no data" where Lovat had an answer. null is "nobody said" - only
      // the two chips default to false, the same way the WRONGS chips do.
      startLane: null, traversal: null, beached: null, climbSpot: null, accuracyRating: 0,
      scoresWhileMoving: false, disrupts: false,
      climbFailed: false, autoClimbFailed: false,
      // The second the scout said a climb began. Ours is a tap, not a
      // stopwatch, so it is the moment they noticed - but it answers the
      // question every alliance captain asks about a robot that says it
      // climbs: how long before the buzzer does it have to leave?
      climbStartSecs: null, autoClimbStartSecs: null,
      // whose timeline these intervals are on. The server may only re-anchor
      // to TBA's actual_time when the phone was on the shared clock.
      clockShared: false, clockBy: null,
      // Which layout this entry was scouted in - on every entry, including
      // the ones with no shooting runs (a no-show, a defence-only robot), so
      // simple and pro can be compared on all of them.
      inputMode: simple ? 'simple' : 'pro',
    },
  };
}

let saveTimer = null;
/**
 * Bank the entry in IndexedDB shortly after the scout stops fiddling.
 *
 * No `net.flush()` here any more. Every save already queues the row, and the
 * flush tick in net.js picks it up within eight seconds; forcing a send on top
 * of it meant each of the thirty-odd shooting runs in a match cost two
 * IndexedDB transactions, four more reads around them and its own HTTP POST.
 * The three places where the send really is the point - DONE after the buzzer,
 * HAND OVER, and claiming a chair - still flush explicitly.
 */
function autosave(ms = 400) {
  if (PRACTICE) return;              // nothing a trainee does is ever stored
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    if (!entry || !entry.team) return;
    try { await db.saveScout(entry); } catch (e) { console.error(e); }
  }, ms);
}

const ballsSoFar = () =>
  Math.round((entry ? entry.payload.intervals : []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0));

// ══════════════════════════════════════════════════════════ CLAIMING A CHAIR
// The claim is the only thing that tells the lead this robot is being watched.
// Tapping SIT used to fire one POST and forget it: if the hub was unreachable
// at that moment - which at a venue is most of the time - the claim was lost
// for good. The phone believed it was seated, the crew board said the station
// was empty all day, and nothing ever tried again.
//
// `seatVerified` is the safety catch on the other side of that. A phone must
// not re-assert a chair until the hub has confirmed the chair is still ours,
// or a phone that was bumped and then restarted would quietly steal it back.
let seatVerified = false;
let lastSeatSync = 0;
let lastVerify = 0;
// Nothing may settle the chair until boot has chosen a screen: verifySeat's
// answer decides between standby and the bump screen, and it must not race the
// startup that picks one.
let booted = false;

function seatKey() {
  return seat.alliance && seat.station ? `${seat.alliance}${seat.station}` : '';
}

/** `red2` as the sign above the chair reads it: RED 2. */
const seatLabel = (k) => {
  const s = String(k || 'this chair').replace(/(\d)/, ' $1');
  return s.charAt(0).toUpperCase() + s.slice(1);
};
/** red, 2 -> Red 2 */
const stationLabel = (al, n) => al ? `${al.charAt(0).toUpperCase()}${al.slice(1)} ${n}` : '';

// When the match this phone was bumped out of finishes, as wall clock. Taking a
// chair back mid-match stops whoever the lead has just put in it, and then two
// people have half a match each and neither set of numbers is worth having. The
// buzzer is when you sort out who is sitting where, so the take-back waits for
// it. Zero means we were bumped between matches and it is free to use now.
let bumpMatchEndsAt = 0;

/** Seconds until the take-back is safe to offer. 0 = now. */
function bumpMatchLeft() {
  let left = bumpMatchEndsAt ? (bumpMatchEndsAt - Date.now()) / 1000 : 0;
  // A match that got under way after we were bumped is just as bad a moment.
  const rec = currentMatch && MATCH_CLOCKS[currentMatch.matchKey];
  if (rec && rec.startedAt) {
    left = Math.max(left, matchSeconds() - (net.serverNow() - rec.startedAt));
  }
  return Math.max(0, left);
}

function paintBumpWait() {
  const left = bumpMatchLeft();
  $('#btnTakeBack').classList.toggle('hide', left > 0);
  $('#bumpWait').textContent = left > 0
    ? `A match is being played. Taking the chair back now would stop whoever is `
      + `in it, so it waits for the buzzer — ${fmt(left)}.`
    : '';
}

function syncSeat(force = false) {
  if (PRACTICE || !seatVerified) return Promise.resolve();
  // The background re-assert must never fire from the bump screen - that is the
  // one place the phone has been told to stop. IT'S STILL MY CHAIR is the
  // deliberate exception and passes force, so it is not caught by this.
  if (!force && screen === 'bumped') return Promise.resolve();
  if (!seat.scout || !seat.alliance || !seat.station) return Promise.resolve();
  const now = Date.now();
  // Re-asserting is also what keeps the chair from ageing out of the hub's map
  // while the scout sits in it, so it repeats rather than firing once.
  if (!force && (!net.state.online || now - lastSeatSync < 60000)) return Promise.resolve();
  lastSeatSync = now;
  return net.api('/api/seat', { method: 'POST', body: JSON.stringify({
    alliance: seat.alliance, station: seat.station,
    scoutId: seat.scout, deviceId: db.deviceId(),
  }) }).then((r) => {
    if (r && r.seats) { SEATS = r.seats; if (screen === 'seat') renderSeat(); }
  }).catch(() => { lastSeatSync = 0; });   // offline: the next tick tries again
}

/**
 * Ask the hub who is actually in our chair before we act as though it is ours.
 *
 * `force` re-asks for a chair this phone already believes is its own, and is
 * what a reconnection uses. A phone that was out of range while the lead freed
 * its station and sat somebody else there came back and simply re-asserted the
 * claim - bumping the scout who had just been put in the chair, on the hub's
 * own instruction. Two signals about one chair; the hub's is the one that
 * counts, and asking is the only way to hear it.
 */
async function verifySeat({ force = false } = {}) {
  if (PRACTICE || screen === 'bumped') return;
  if (!force && seatVerified) return;
  // The plain call is reached from every connection change, so it is rate
  // limited. A reconnection is an event rather than a poll and has to get
  // through - it is the one moment this phone can have missed the news about
  // its own chair - but a radio flapping in and out must still not spin.
  if (Date.now() - lastVerify < (force ? 2000 : 15000)) return;
  lastVerify = Date.now();
  let s;
  try { s = await net.api('/api/seats'); } catch { return; }   // retried when we reconnect
  SEATS = s || {};
  const held = seatKey() ? SEATS[seatKey()] : null;
  const stolen = !!held && !!held.deviceId && held.deviceId !== db.deviceId();
  if (stolen) {
    // On the seat screen nothing is at stake yet: the tile shows the clash and
    // SIT is a deliberate take-back. Anywhere else this phone is scouting a
    // robot it no longer has the chair for - restarting the app used to walk
    // straight past that into standby, and two phones logged one robot.
    if (screen !== 'seat') { showBumped(seatKey(), held.scoutId, false); return; }
    renderSeat();
    return;
  }
  seatVerified = true;
  await syncSeat(true);
  if (screen === 'seat') renderSeat();
}

function showBumped(key, byScout, freed) {
  seatVerified = false;
  const elapsed = clock.elapsed();
  const left = matchSeconds() - elapsed;
  bumpMatchEndsAt = elapsed > 0 && left > 0 ? Date.now() + left * 1000 : 0;
  clock.pause();
  // Whatever is half-entered belongs to the scout being stopped, exactly as it
  // does on HAND OVER. Bank it now rather than trusting the debounced autosave
  // to land after the screen has been taken away from them.
  if (!PRACTICE && entry && entry.team && entry.payload.intervals.length) {
    db.saveScout(entry).then(() => net.flush()).catch(() => {});
  }
  $('#bumpSeat').textContent = `${seatLabel(key)} · ${seat.scout}`;
  $('#bumpTitle').textContent = freed
    ? `The scout lead freed ${seatLabel(key)}.`
    : `${String(byScout || 'Someone')} took ${seatLabel(key)}.`;
  $('#bumpBody').textContent = freed
    ? 'Stop logging until you have a chair again. Everything you already saved is safe and has been sent.'
    : 'Stop logging — two phones on one robot means neither set of data can be trusted. '
      + 'Everything you already saved is safe and has been sent.';
  renderBumped();
  show('bumped');
  buzz(60);
}

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
        d.innerHTML = `<span class="bar"></span><span class="al">${al === 'red' ? 'Red' : 'Blue'}</span>
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
    const ours = !!claim && claim.deviceId === db.deviceId();
    const taken = !!claim && !ours;
    const wh = d.querySelector('.wh');
    wh.textContent = mine ? `you · ${seat.scout || '--'}`
      : taken ? String(claim.scoutId || '??')
      // the chair this phone is holding right now, while a different tile is
      // selected - it used to read OPEN in green, which is a lie about our own
      : ours ? 'this phone' : 'open';
    // OPEN is the only green one - a name means someone is already in that chair
    wh.classList.toggle('open', !mine && !taken && !ours);
  }
  if (seat.alliance && seat.station) {
    $('#signExample').textContent = stationLabel(seat.alliance, seat.station);
  }
  const m = pickCurrentMatch();
  const t = teamForSeat(m);
  $('#seatTeam').textContent = t || '—';
  $('#seatBlurb').textContent = t
    ? `in ${m.label.toLowerCase()}, then whoever sits in ${stationLabel(seat.alliance, seat.station)} after that`
    : 'pick a station to see who you\'ll watch';
  $('#sitSub').textContent = seat.scout && seat.alliance && seat.station
    ? (m ? `${m.label.toLowerCase()} is next` : 'ready') : 'pick a station first';
  $('#seatHub').textContent = net.state.base ? `${net.state.base.replace(/^https?:\/\//, '')} · connected` : 'not found yet';
  $('#seatDot').className = 'dot' + (net.state.online ? '' : ' amber');
  $('#seatEvent').textContent = eventKey
    ? `${eventKey} · ${net.state.online ? 'hub found' : 'hub not found'}`
    : 'No event set on the hub';
  paintMode();
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
  // A training run must leave nothing behind, and that includes the station the
  // trainee poked at - the next person to pick this phone up gets their own.
  if (PRACTICE) { loadMatch(practiceMatch()); show('live'); renderLive(); return; }
  localStorage.setItem('alliance', seat.alliance);
  localStorage.setItem('station', String(seat.station));
  net.setIdentity({ scoutId: seat.scout, seat: `${seat.alliance}${seat.station}` });
  seatVerified = true;
  syncSeat(true);
  loadMatch(nextScoutableMatch());
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

/**
 * Simple mode's speed question.
 *
 * Asked after the run, about the run: the interval is saved the moment the
 * button is let go, at the last speed picked, and a tap here re-labels that
 * one interval. Nothing waits on an answer - a scout who ignores it keeps the
 * speed they had, and holding the button again starts the next run at once,
 * so a robot that shoots twice in a row is never missed while somebody reads.
 */
function renderSpeed() {
  const el = $('#speedOpts');
  if (!el.children.length) {
    for (const b of [...LADDER].reverse()) {
      const d = document.createElement('div');
      d.className = 'o';
      d.dataset.id = b.id;
      d.textContent = b.label;
      d.onclick = () => pickSpeed(b.id);
      el.appendChild(d);
    }
  }
  const iv = pendingRun();
  // Mid-hold the run being held is the one at `rate`; the last run's speed is
  // only what is shown once the hand is off the pad.
  const shown = padHeld ? rate : iv ? iv.intensity : rate;
  for (const d of el.children) d.classList.toggle('on', d.dataset.id === shown);
  $('#speed').classList.toggle('dim', padHeld);
  $('#speedQ').textContent = iv && !padHeld ? 'How fast was that?' : 'How fast?';
  $('#speedFoot').textContent = padHeld ? "You'll pick this when you let go"
    : iv ? `Keeps "${speedName(shown)}" if you don't tap`
    : 'You pick this after each run';
}

function pendingRun() {
  return (pending != null && entry && entry.payload.intervals[pending]) || null;
}

function pickSpeed(id) {
  rate = id;
  // Mid-hold, a tap is about the run being held - it is saved at `rate` when
  // it ends - not the one before it.
  const iv = padHeld ? null : pendingRun();
  if (iv && iv.intensity !== id) { iv.intensity = id; autosave(); }
  buzz(); renderSpeed(); renderLadder();
}

/** Whether a shot right now would score: true, false, or null for unknown. */
function hubNow() {
  const t = clock.elapsed();
  const now = phaseAt(t);
  if (!now || (!clock.running && t === 0)) return null;
  return hubActive(now.id, seat.alliance, autoWinner);
}

/**
 * The hold button's words, for each state it can be in.
 *
 * Never blocked: when the hub is off it goes grey and says so, but holding
 * still records the run. Those runs score nothing (the official count for an
 * off shift is zero, so the split gives them nothing), and they are exactly
 * what "wasted fuel" and "stockpiles" on the dashboard are measured from.
 */
function paintHold(holding = false, secs = 0) {
  const changed = padHeld !== holding;
  padHeld = holding;
  const pad = $('#holdpad');
  const t = clock.elapsed();
  const started = clock.running || t > 0;
  const over = started && !phaseAt(t);
  const off = !holding && started && !over && hubNow() === false;
  pad.classList.toggle('on', holding);
  pad.classList.toggle('off', off);
  pad.classList.toggle('idle', !holding && over);
  let lb, big = '', un;
  if (holding) {
    if (simple) { lb = `Holding · ${secs.toFixed(1)}s`; un = 'Let go when they stop shooting'; }
    else { lb = 'Holding'; big = `${secs.toFixed(1)}s`; un = `at ${speedName(rate)}`; }
  } else if (!started) {
    lb = 'Tap here when the match starts';
    un = 'then hold it down while they shoot';
  } else if (over) {
    lb = 'Match over'; un = '';
  } else if (off) {
    lb = 'Hub is off';
    un = simple ? "Shots now don't score. If they shoot anyway, hold so we can count the wasted shots."
      : 'Hold anyway if they shoot';
  } else if (simple) {
    lb = 'Hold while they shoot';
    un = pendingRun() ? 'Holding again starts a new run right away' : 'Let go when they stop';
  } else {
    lb = 'Hold while they shoot'; big = String(rateOf(rate)); un = 'balls per second';
  }
  $('#holdLabel').textContent = lb;
  $('#holdRate').textContent = big;
  $('#holdUnit').textContent = un;
  // Called every animation frame during a hold; the speed column only changes
  // when the hold starts or ends.
  if (simple && (changed || !holding)) renderSpeed();
}

/**
 * Lay the live screen out for the current mode. The clock, the hub pill and
 * the feeding/defending pair are single elements that move between the two
 * layouts, so every handler bound to them keeps working in either.
 */
function placeLive() {
  $('#s-live').classList.toggle('simple', simple);
  $('#lvBody').classList.toggle('simple', simple);
  if (simple) {
    $('#lvHdrClock').append($('#lvShotState'), $('#lvClock'));
    $('#speed').appendChild($('#fdPair'));
  } else {
    $('#lvCentre').append($('#lvClock'), $('#lvShotState'));
    $('#lvLeft').appendChild($('#fdPair'));
  }
}

function setMode(on) {
  simple = !!on;
  try { localStorage.setItem('inputMode', simple ? 'simple' : 'pro'); } catch {}
  pending = null;
  if (entry) { entry.payload.inputMode = simple ? 'simple' : 'pro'; autosave(); }
  buzz();
  paintMode();
  placeLive();
}

function paintMode() {
  const sw = $('#seatMode');
  sw.classList.toggle('on', simple);
  sw.setAttribute('aria-checked', String(simple));
  $('#sbModeState').textContent = simple ? 'on' : 'off';
}
$('#seatMode').onclick = () => setMode(!simple);
$('#sbMode').onclick = () => setMode(!simple);

/** Hold-and-release recorder. Tap and hold only — no drags (Android owns edge swipes). */
function holdRecorder(el, list, onPaint, { onRecord } = {}) {
  let t0 = null, raf = null;
  const begin = (ev) => {
    if (t0 !== null) return;
    ev.preventDefault();
    if (!clock.running) return;
    t0 = clock.elapsed();
    buzz(12);
    const tick = () => {
      if (t0 === null) return;
      onPaint && onPaint(true, clock.elapsed() - t0);
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
    onRecord && onRecord(list().length - 1);
    buzz(12);
    renderLive(); autosave();
  };
  el.addEventListener('pointerdown', begin);
  el.addEventListener('pointerup', end);
  el.addEventListener('pointercancel', end);
  el.addEventListener('pointerleave', end);
  el.addEventListener('contextmenu', (e) => e.preventDefault());
}

/** When they were shooting, across the whole match. On the after screen. */
function renderRunbar() {
  const el = $('#afRunbar');
  const total = matchSeconds();
  const ivs = entry ? entry.payload.intervals : [];
  // Rebuilt only when a run is logged or undone.
  const last = ivs[ivs.length - 1];
  const sig = `${ivs.length}:${last ? last.end : ''}`;
  if (el.dataset.sig !== sig) {
    el.dataset.sig = sig;
    el.innerHTML = ivs.map((iv) =>
      `<span class="r ${iv.intensity}" style="left:${(iv.start / total) * 100}%;width:${Math.max(0.8, ((iv.end - iv.start) / total) * 100)}%"></span>`
    ).join('');
  }
}

function renderLive() {
  if (!entry) return;
  const t = clock.elapsed();
  const now = phaseAt(t);
  if ($('#lvBody').classList.contains('simple') !== simple) placeLive();
  $('#lvSeat').textContent = stationLabel(seat.alliance, seat.station);
  const name = teamName(entry.team) || '';
  // Simple mode has no middle column, so the robot it is watching goes up top.
  $('#lvMeta').textContent = PRACTICE
    ? (simple ? `${entry.team || '—'} · practice` : 'Practice · nothing is saved')
    : simple ? `${entry.team || '—'}${name ? ' ' + name : ''}`
    : `${currentMatch ? currentMatch.label : 'Manual'} · ${seat.scout}`;
  $('#lvTeam').textContent = entry.team || '—';
  $('#lvTeamName').textContent = name;
  $('#lvMatchOf').textContent = matchOfText();
  $('#lvClock').textContent = fmt(t);
  const inAuto = now && now.id === 'auto';
  $('#btnClimb').querySelector('.a').textContent = inAuto ? 'Auto climb' : 'Climb';
  // The level only once there is one; "not set" was just more to read.
  $('#climbSub').textContent = inAuto
    ? (entry.payload.autoTower === 'Level1' ? 'L1' : '')
    : (entry.payload.endgameTower === 'None' ? '' : entry.payload.endgameTower.replace('Level', 'L'));
  $('#btnClimb').classList.toggle('on', inAuto ? entry.payload.autoTower === 'Level1' : entry.payload.endgameTower !== 'None');
  paintLink();

  const st = $('#lvShotState');
  if (!clock.running && t === 0) { st.textContent = 'Not started'; st.className = 'shotstate unknown'; }
  else if (!now) { st.textContent = 'Match over'; st.className = 'shotstate dead'; }
  else {
    const act = hubActive(now.id, seat.alliance, autoWinner);
    const left = Math.ceil(now.end - t);
    if (act === true) { st.textContent = `Hub on · ${left}s left`; st.className = 'shotstate'; }
    else if (act === false) { st.textContent = `Hub off · ${left}s left`; st.className = 'shotstate dead'; }
    else { st.textContent = `${now.label} · ${left}s left`; st.className = 'shotstate unknown'; }
  }
  if (!padHeld) paintHold();
  renderLadder();
}

/** Connection words in the live header: silent while all is well. */
function paintLink() {
  $('#lvLink').textContent = !net.state.online ? 'Saving on this phone'
    : (clock.sharedBy && clock.sharedBy !== seat.scout) ? `Clock from ${clock.sharedBy}`
    : '';
  $('#lvDot').className = 'dot' + (net.state.online ? '' : ' amber');
}

// ═══════════════════════════════════════════════════════ AFTER THE BUZZER
function renderAfter() {
  if (!entry) return;
  $('#afTitle').textContent = `${currentMatch ? currentMatch.label : 'Match'} done`;
  $('#afMeta').textContent = `${entry.team} · ${ballsSoFar()} balls · ${entry.payload.endgameTower === 'None' ? 'no climb' : entry.payload.endgameTower.replace('Level', 'L') + ' climb'}`;
  const nx = nextMatch();
  $('#afNext').textContent = nx ? `Next: ${nx.label}` : 'Next: —';
  renderRunbar();
  $('#afBalls').textContent = ballsSoFar();
  // The second they left to climb rides on the same line as the level, so the
  // scout can see the number they just produced without another row of chrome.
  const climbSecs = entry.payload.climbStartSecs;
  $('#afClimb').textContent = entry.payload.endgameTower === 'None'
    ? (entry.payload.climbFailed ? 'fell off' : 'none')
    : entry.payload.endgameTower.replace('Level', 'L')
      + (climbSecs == null ? '' : ` · ${Math.round(climbSecs)}s`);
  $('#afAuto').textContent = entry.payload.autoTower === 'None'
    ? (entry.payload.autoClimbFailed ? 'fell off' : 'no') : 'yes · L1';
  $('#afRuns').textContent = entry.payload.intervals.length;
  $('#sendSub').textContent = net.state.online ? 'both thumbs free' : 'saves now, sends later';

  buildSteps('#driveSteps', DRIVE, 'driverRating');
  buildSteps('#defSteps', DEFENSE, 'defenseRating');

  // ---- page two: optional, and only ever one tap each
  const p = entry.payload;
  buildPicks('#afStart', START_ZONES, 'startPosition', { onSet: renderAfter });
  buildPicks('#afLane', START_LANES, 'startLane', { onSet: renderAfter });
  buildPicks('#afTraversal', TRAVERSAL, 'traversal', { onSet: renderAfter });
  buildPicks('#afBeached', BEACHED, 'beached', { onSet: renderAfter });
  buildFlags('#afFlags', AFTER_FLAGS);
  buildSteps('#afAccuracy', ACCURACY, 'accuracyRating');

  // Only ask who they were blocking if they actually blocked someone.
  const defended = (p.defenseIntervals || []).length > 0;
  $('#afDefWrap').classList.toggle('hide', !defended);
  if (defended) {
    buildPicks('#afDefTarget', opponentLineup().map((t) => ({ id: t, label: String(t) })),
               'defenseTarget', { onSet: renderAfter });
  }
  // And only ask where on the tower if they got onto one.
  const climbed = p.endgameTower !== 'None';
  $('#afClimbWrap').classList.toggle('hide', !climbed);
  if (climbed) buildPicks('#afClimbSpot', CLIMB_SPOTS, 'climbSpot', { onSet: renderAfter });

  $('#afPage1a').classList.toggle('hide', afterPage !== 1);
  $('#afPage1b').classList.toggle('hide', afterPage !== 1);
  $('#afPage2a').classList.toggle('hide', afterPage !== 2);
  $('#afPage2b').classList.toggle('hide', afterPage !== 2);
  const answered = answeredExtras();
  $('#btnAfMore').querySelector('.a').textContent = afterPage === 1 ? 'A few more things' : 'Back';
  $('#afMoreSub').textContent = afterPage === 2 ? 'to driving and faults'
    : answered ? `${answered} answered` : 'optional';

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
  for (const d of w.children) {
    d.classList.toggle('on', !!p[d.dataset.k]);
    // A recorded climb answers the "fell off" chip beside it, so the chip goes
    // away rather than sitting there inviting a contradiction.
    if (d.dataset.k === 'climbFailed') d.classList.toggle('hide', climbed);
    if (d.dataset.k === 'autoClimbFailed') d.classList.toggle('hide', p.autoTower !== 'None');
  }
}

/** How many of page two's questions this entry has an answer for.
 *
 * Counted rather than scored out of a total: which questions are even asked
 * depends on the match (there is no tower question for a robot that did not
 * climb), and "3 of 6" that can never reach 6 is a nag, not a progress bar.
 */
function answeredExtras() {
  const p = entry.payload;
  return [p.startPosition, p.startLane, p.traversal, p.beached, p.climbSpot,
          p.accuracyRating || null,
          (p.scoresWhileMoving || p.disrupts) || null].filter((v) => v != null).length;
}

/**
 * A row of independent yes/no chips, each bound to its own payload flag.
 *
 * `buildPicks` is one answer to one question; this is several questions whose
 * answer is a tick. Not ticked reads as no, the same as the WRONGS chips
 * beside them - these two are things a scout notices happening, not things
 * they have to rule out.
 */
function buildFlags(sel, items) {
  const el = $(sel);
  if (!el.children.length) {
    for (const it of items) {
      const d = document.createElement('div');
      d.className = 'pick'; d.dataset.k = it.k;
      d.innerHTML = it.sub ? `${it.label}<span class="sub">${it.sub}</span>` : it.label;
      d.onclick = () => {
        if (!entry) return;
        entry.payload[it.k] = !entry.payload[it.k];
        buzz(); renderAfter(); autosave();
      };
      el.appendChild(d);
    }
  }
  for (const d of el.children) d.classList.toggle('on', !!entry.payload[d.dataset.k]);
}

/**
 * A row of one-tap choices bound to a payload field.
 *
 * Rebuilt when the options or the selection change, and skipped when they have
 * not. The rebuild is what keeps the selected state honest when the entry is
 * swapped out underneath it; doing it unconditionally is what had the standby
 * screen destroying and recreating a dozen nodes and their click handlers five
 * times a second, for hours, between matches.
 */
function buildPicks(sel, options, key, { toggle = true, onSet } = {}) {
  const el = $(sel);
  if (!el) return;
  const sig = options.map((o) => o.id).join('|') + '#' + String(entry ? entry.payload[key] : null);
  if (el.dataset.sig === sig) return;
  el.dataset.sig = sig;
  el.innerHTML = '';
  for (const opt of options) {
    const d = document.createElement('div');
    d.className = 'pick' + (entry && entry.payload[key] === opt.id ? ' on' : '');
    d.innerHTML = opt.sub ? `${opt.label}<span class="sub">${opt.sub}</span>` : opt.label;
    d.onclick = () => {
      if (!entry) return;
      entry.payload[key] = (toggle && entry.payload[key] === opt.id) ? null : opt.id;
      buzz();
      autosave();
      if (onSet) onSet();
    };
    el.appendChild(d);
  }
}

/** The three robots on the other alliance, for "who were they blocking". */
function opponentLineup() {
  if (!currentMatch || !seat.alliance) return [];
  const other = seat.alliance === 'red' ? 'blue' : 'red';
  return (currentMatch[other] || []).filter(Boolean);
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

$('#afNote').oninput = () => { entry.payload.note = $('#afNote').value; autosave(1200); };
$('#btnAfMore').onclick = () => { afterPage = afterPage === 1 ? 2 : 1; buzz(); renderAfter(); };
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
  // Which of them the hub has not taken yet. Kept beside the rows rather than
  // stamped onto them: `history[0]` is handed straight to FIX THE LAST MATCH
  // as the live entry, and a display flag welded into a record ends up saved
  // and synced with it.
  try {
    QUEUED_IDS = new Set((await db.queued())
      .filter((q) => q.kind === 'scout' && q.record)
      .map((q) => q.record.id));
  } catch (e) { console.error(e); }
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

/** Seconds until this seat's next match takes the field, or null if unknown. */
function untilNextMatch() {
  const m = currentMatch;
  const eta = m && m.times && (m.times.estimatedOnFieldTime || m.times.estimatedQueueTime);
  return eta ? Math.max(0, (eta - Date.now()) / 1000) : null;
}

/**
 * The parts of standby that move without anybody touching anything.
 *
 * Everything else on the screen changes only when something happens - a match
 * is logged, the hub says the schedule moved, the scout taps a chip - so it is
 * rendered from those events instead of from a timer. This is what is left, and
 * it is one text node a second.
 */
function tickStandby() {
  const secs = untilNextMatch();
  $('#sbCountdown').textContent = secs == null ? '--:--' : fmt(secs);
  // Bright again before the robot is on the field, so nobody comes back to a
  // dark phone at the wrong moment.
  if (secs != null && secs < 60) standbyIdle.wake();
}

function renderStandby() {
  $('#sbSeat').textContent = stationLabel(seat.alliance, seat.station);
  $('#sbMeta').textContent = `${seat.scout} · ${history.length} ${history.length === 1 ? 'match' : 'matches'} in`;
  $('#sbSync').textContent = net.state.queued ? `${net.state.queued} waiting` : 'Everything sent';
  paintMode();
  $('#sbDot').className = 'dot' + (net.state.online ? '' : ' amber');

  const m = currentMatch;
  const t = teamForSeat(m);
  $('#sbTeam').textContent = t || '—';
  $('#sbTeamMeta').textContent = t ? (teamName(t) || 'not seen yet') : '—';
  $('#sbUntil').textContent = !m ? 'Waiting for the schedule'
    : m.status ? `${m.label} · ${m.status.toLowerCase()}`
    : `until ${m.label} is called`;

  $('#openMatchSub').textContent = t
    ? `open the match screen for ${t} now`
    : 'open the match screen — it will ask which team';

  tickStandby();

  buildPicks('#sbStart', START_ZONES, 'startPosition', { onSet: renderStandby });
  buildPicks('#sbPreload',
             [...Array((gameRules().preloadMax || 8) + 1).keys()]
               .map((n) => ({ id: n, label: String(n) })),
             'preload', { onSet: renderStandby });

  $('#sbList').innerHTML = history.length ? history.map((h) => `
    <div class="lrow"><span class="code">${shortCode(h.matchLabel)}</span>
      <span class="team">${h.team}</span>
      <span class="desc">${Math.round((h.payload.intervals || []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0))} balls · ${h.payload.endgameTower === 'None' ? 'none' : h.payload.endgameTower.replace('Level', 'L')}${RECONCILED.has(h.matchKey) ? ' · reconciled' : ''}</span>
      <span class="tag ${QUEUED_IDS.has(h.id) ? 'queued' : 'sent'}">${QUEUED_IDS.has(h.id) ? 'waiting' : 'sent'}</span></div>`).join('')
    : '<div class="lrow"><span class="desc">nothing logged yet</span></div>';
}

async function renderBumped() {
  paintBumpWait();
  await refreshHistory();
  $('#bumpList').innerHTML = history.length ? history.map((h) => `
    <div class="lrow"><span class="code">${shortCode(h.matchLabel)}</span>
      <span class="team">${h.team}</span>
      <span class="desc">${Math.round((h.payload.intervals || []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0))} balls</span>
      <span class="tag sent">saved</span></div>`).join('')
    : '<div class="lrow"><span class="desc">nothing logged yet</span></div>';
}

async function renderOffline() {
  $('#offSeat').textContent = `${stationLabel(seat.alliance, seat.station)} · ${seat.scout}`;
  const q = await db.queued();
  const items = db.collapse(q);
  $('#offBlurb').textContent = items.length
    ? `${items.length} ${items.length === 1 ? 'match is' : 'matches are'} saved on this phone and will send themselves the moment the hub is back. You don't need to do anything.`
    : 'Everything you enter is saved on this phone and will send itself the moment the hub is back.';
  $('#offList').innerHTML = items.length ? items.map((it) => {
    const r = it.record;
    return `<div class="lrow"><span class="code">${shortCode(r.matchLabel)}</span>
      <span class="desc">${r.team} · ${Math.round((r.payload.intervals || []).reduce((s, i) => s + (i.end - i.start) * rateOf(i.intensity), 0))} balls</span>
      <span class="tag queued">waiting</span></div>`;
  }).join('') : '<div class="lrow"><span class="desc">nothing waiting</span></div>';
}

// ══════════════════════════════════════════════ OPENING A MATCH BY HAND
/**
 * The match screen normally arms itself: Nexus reports the match on the field,
 * the hub pushes that out, and every phone whose seat is in that match jumps
 * straight to the HUD. A phone that cannot reach the hub never hears it and had
 * no other way through, which made the whole of Plan B - "keep scouting, the
 * network is down" - impossible to actually do: the scout sat on the countdown
 * watching the match being played.
 *
 * The schedule is not what was missing. The phone caches the entire event at
 * boot - under 60KB for a 75-match regional, of which every lineup is 6KB - so
 * it already knows which robot this seat watches in every match, network or no
 * network. Only the door was missing. A phone that has never once reached the
 * hub has no schedule at all, and that one asks for the number off the robot.
 */
function openMatchByHand() {
  const m = currentMatch || nextScoutableMatch();
  let team = teamForSeat(m);
  if (!team) {
    const v = prompt('Which team are you watching? (the number on the robot)', '');
    if (v === null) return;
    team = Number(String(v).replace(/\D/g, ''));
    if (!team) { buzz(30); return; }
  }
  loadMatch(m);                 // m may be null: newEntry files that as a manual row
  if (entry && !entry.team) entry.team = team;
  buzz(20);
  show('live'); renderLive();
}
$('#btnOpenMatch').onclick = openMatchByHand;
$('#btnOffOpenMatch').onclick = openMatchByHand;

$('#btnRetry').onclick = () => { net.discover().then(net.flush); };
$('#btnTakeBack').onclick = async () => {
  // Belt and braces: the button is hidden mid-match, but a stale tap landing as
  // the next match starts must not take a chair out from under someone.
  if (bumpMatchLeft() > 0) { buzz(30); paintBumpWait(); return; }
  seatVerified = true;
  await syncSeat(true);
  loadMatch(nextScoutableMatch());
  goStandbyOrLive();
};
$('#btnPickSeat').onclick = () => {
  // Walking away from the bump screen gives the chair up. Keeping it selected
  // would let the background re-claim take it straight back off whoever the
  // lead just sat there, which is how we got here in the first place.
  seat.alliance = ''; seat.station = 0;
  localStorage.removeItem('alliance'); localStorage.removeItem('station');
  seatVerified = true;
  show('seat'); renderSeat();
};
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
  // offline: syncSeat re-asserts this the moment the hub is reachable again
  await syncSeat(true);
  // fresh entry so the new scout's work is attributed to them
  loadMatch(currentMatch);
  await refreshHistory();
  buzz(20);
  goStandbyOrLive();
};

$('#btnFixPast').onclick = () => { if (history[0]) { entry = history[0]; currentMatch = matches.find((m) => m.matchKey === entry.matchKey) || currentMatch; $('#afNote').value = entry.payload.note || ''; afterPage = 1; show('after'); renderAfter(); } };

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

/** The next match this seat can still actually scout.
 *
 * A phone joining or rejoining lands on the match on the field - but right
 * after a buzzer that match is over, and loading it adopts its finished clock,
 * which puts the scout on the after-the-buzzer screen for a match they did not
 * watch, one tap from sending an empty record for it. A robot that scored
 * nothing is what an empty record claims, and the solver believes it.
 */
function nextScoutableMatch() {
  const m = pickCurrentMatch();
  const rec = m && MATCH_CLOCKS[m.matchKey];
  if (!rec || !rec.startedAt || net.serverNow() - rec.startedAt < matchSeconds()) return m;
  const i = matches.findIndex((x) => x.matchKey === m.matchKey);
  return (i >= 0 && matches[i + 1]) || m;
}

/**
 * The match this seat is watching now.
 *
 * A queueing status and an official result are two signals about one match,
 * and when they disagree the result wins: TBA does not post a breakdown for a
 * match that is still being played. Nexus's status comes from a volunteer with
 * a tablet and it goes stale in exactly the way you would expect - a match
 * that has been played and scored can still read `On field` for the rest of
 * the day, because put_match COALESCEs a missing status and keeps the last one
 * it was told. Every phone in the building then sits on a finished match.
 */
function pickCurrentMatch() {
  if (PRACTICE) return practiceMatch();
  for (const st of ['On field', 'On deck', 'Now queuing']) {
    const m = matches.find((x) => x.status === st && !x.breakdown);
    if (m) return m;
  }
  return matches.find((m) => !m.breakdown) || matches[0] || null;
}
function nextMatch() {
  const i = matches.findIndex((m) => currentMatch && m.matchKey === currentMatch.matchKey);
  return i >= 0 ? matches[i + 1] : null;
}
function matchOfText() {
  if (PRACTICE) return 'training run';
  const mine = matches.filter((m) => teamForSeat(m));
  const i = mine.findIndex((m) => currentMatch && m.matchKey === currentMatch.matchKey);
  return mine.length ? `match ${i + 1} of ${mine.length}` : '';
}
function loadMatch(m) {
  currentMatch = m;
  const shared = m && MATCH_CLOCKS[m.matchKey];
  autoWinner = (m && m.breakdown && m.breakdown.autoWinner) || null;
  entry = newEntry(m, teamForSeat(m));
  clock.reset();
  rate = 'steady';
  pending = null;
  $('#afNote').value = '';
  // joined late? pick up the clock already running for this match
  if (shared && net.serverNow() - shared.startedAt < matchSeconds() + 30) clock.adopt(shared);
}
// The match this phone has already opened the HUD for. Arming is a one-way
// door per match: a scout who has finished a match and pressed DONE is back on
// standby while the field is still reading `On field`, and re-arming there
// would hand them a second, empty entry for a match they have already sent.
let armedMatch = null;

/**
 * Open the HUD because this seat's robot is on the field.
 *
 * Called from both places a status can reach us: the `matchStatus` push, and
 * the schedule that arrives with every /api/state. Only the push used to do
 * it, and a webhook has to reach the hub from the internet - which it cannot,
 * because the hub sits behind the venue's NAT (see server/offsite.py). So at
 * an actual event nothing armed at all: the countdown ran out, the match was
 * played, and the scout sat on standby watching it.
 */
function armIfOnField(m) {
  if (screen !== 'standby' || !m || m.status !== 'On field') return false;
  // Official results exist, so it is over whatever the queueing status says.
  if (m.breakdown) return false;
  if (armedMatch === m.matchKey) return false;
  const team = teamForSeat(m);
  if (!team) return false;
  // Already logged by this scout - the buzzer has gone and the row is sent.
  if (history.some((h) => h.matchKey === m.matchKey && h.team === team)) return false;
  armedMatch = m.matchKey;
  if (!entry || entry.matchKey !== m.matchKey || entry.team !== team) loadMatch(m);
  else {
    // The preload and the start position are entered on standby, before the
    // robot moves, against this same match. Reloading it would hand the scout
    // a blank entry at the buzzer and throw both away.
    currentMatch = m;
    autoWinner = (m.breakdown && m.breakdown.autoWinner) || autoWinner;
    const shared = MATCH_CLOCKS[m.matchKey];
    if (shared && net.serverNow() - shared.startedAt < matchSeconds() + 30) clock.adopt(shared);
  }
  show('live'); renderLive();
  return true;
}

function goStandbyOrLive() {
  // If our match is already on the field, the scout needs the HUD now, not a
  // countdown - unless it has an official result, in which case the status is
  // stale and the match is over. See pickCurrentMatch.
  if (currentMatch && currentMatch.status === 'On field' && !currentMatch.breakdown
      && entry && entry.team) {
    show('live'); renderLive(); return;
  }
  if (!net.state.online) { renderOffline(); show('offline'); return; }
  show('standby'); renderStandby();
}

// ══════════════════════════════════════════════════════════════ startup
async function main() {
  await loadRules();
  renderLadder(); placeLive(); paintMode(); paintHold();
  document.addEventListener('pointerdown', () => {
    if (screen === 'live' || screen === 'standby') acquireWake();
  }, { passive: true });

  // Landscape HUD: go chrome-free on the taps the scout already makes.
  fs.install({
    triggers: ['#btnSit', '#holdpad', '#btnStart', '#stations'],
    orientation: 'landscape',
    hintEl: $('#fsHint'),
  });

  // iPhone only, and only before anything has been entered — see fullscreen.js
  // for why the timing of this one matters more than it looks.
  fs.offerHomeScreen({ el: $('#a2hs') });

  // Tapping the pad before the match starts the clock; that is the only start control.
  // Bound before the pad's recorder, so a press that starts the clock is also
  // the start of a run when it is held: a robot shooting from the first second
  // of auto is not missed.
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

  // Right pad records shooting; the two left utility buttons record their own runs.
  // The question moves to a new run only once one is recorded. Clearing it on
  // touch let a brush too short to count wipe the question about the last run.
  holdRecorder($('#holdpad'), () => entry.payload.intervals, paintHold, {
    onRecord: (i) => { if (simple) pending = i; },
  });
  holdRecorder($('#btnFeeding'), () => entry.payload.feedIntervals,
    (on) => $('#btnFeeding').classList.toggle('on', on));
  holdRecorder($('#btnDefending'), () => entry.payload.defenseIntervals,
    (on) => $('#btnDefending').classList.toggle('on', on));


  // Undo the most recent run of ANY kind. It used to pop the shooting list
  // only, so a mis-held FEEDING or DEFENDING pad could not be taken back at all.
  $('#btnUndo').onclick = () => {
    if (!entry) return;
    const lists = [entry.payload.intervals, entry.payload.feedIntervals,
                   entry.payload.defenseIntervals].filter((l) => l && l.length);
    if (!lists.length) { buzz(30); return; }
    let newest = lists[0];
    for (const l of lists) {
      if (l[l.length - 1].end > newest[newest.length - 1].end) newest = l;
    }
    newest.pop();
    if (pending != null && pending >= entry.payload.intervals.length) pending = null;
    buzz(20); renderLive(); autosave();
  };
  $('#btnClimb').onclick = () => {
    const t = clock.elapsed();
    const ph = phaseAt(t);
    // The second of the tap, and only while a match is actually running: a
    // climb "logged" at 0:00 or after the buzzer is the scout catching up, and
    // a made-up number here is worse than no number. Unknown, not zero.
    const now = (clock.running && t > 0 && t <= matchSeconds()) ? Math.round(t * 10) / 10 : null;
    if (ph && ph.id === 'auto') {
      // Auto tower is Level 1 only and worth 15, so it is a toggle, not a cycle.
      const on = entry.payload.autoTower !== 'Level1';
      entry.payload.autoTower = on ? 'Level1' : 'None';
      entry.payload.autoClimbStartSecs = on ? now : null;
      // A recorded climb answers the chip beside it. The chip hides itself at
      // that point but its value stayed true, and a scout who tapped FELL OFF
      // first could no longer reach it to untick - so the robot went out
      // reading "climbed L1" and "fell off" at once, and the dashboard
      // believed both.
      if (on) entry.payload.autoClimbFailed = false;
    } else {
      const i = CLIMBS.indexOf(entry.payload.endgameTower);
      const next = CLIMBS[(i + 1) % CLIMBS.length];
      entry.payload.endgameTower = next;
      if (next !== 'None') entry.payload.climbFailed = false;   // see the auto case above
      // Keep the FIRST tap: cycling L1 to L2 to L3 is the scout settling on a
      // level, not the robot starting again. Back round to None clears it.
      if (next === 'None') entry.payload.climbStartSecs = null;
      else if (entry.payload.climbStartSecs == null) entry.payload.climbStartSecs = now;
    }
    buzz(); renderLive(); autosave();
  };

  let wasOnline = net.state.online;
  let wasQueued = net.state.queued;
  net.onChange(() => {
    // The queue draining is what turns a row from QUEUED to SENT, and only
    // re-reading it can say which rows went.
    if (net.state.queued !== wasQueued) {
      wasQueued = net.state.queued;
      refreshHistory().then(() => { if (screen === 'standby') renderStandby(); });
    }
    if (screen === 'live') paintLink();
    if (screen === 'standby') renderStandby();
    if (screen === 'seat') renderSeat();
    if (screen === 'offline') renderOffline();
    // Finding the hub again is the moment to settle the chair: check who holds
    // it, then re-assert ours. Both are no-ops once they have succeeded.
    const cameBack = net.state.online && !wasOnline;
    wasOnline = net.state.online;
    // ...and the moment to come off the offline screen. Nothing else ever took
    // a phone off it: it is only ever entered, so a phone that lost the hub
    // once sat there for the rest of the day - still queueing, still syncing,
    // but off the one screen the auto-arm fires from. The scout saw a working
    // phone that never opened for a match again.
    if (cameBack && screen === 'offline') goStandbyOrLive();
    if (booted && net.state.online) {
      // Coming back from a gap is a re-ask, not a re-assert: while this phone
      // was away the chair may have been freed and given to somebody else.
      if (!seatVerified || cameBack) verifySeat({ force: cameBack });
      else syncSeat(false);
    }
  });

  net.setIdentity({ deviceId: db.deviceId(), scoutId: seat.scout,
                    seat: seat.alliance && seat.station ? `${seat.alliance}${seat.station}` : '' });
  const cfg = await net.start();
  if (cfg && cfg.eventKey) { eventKey = cfg.eventKey; localStorage.setItem('eventKey', eventKey); }
  net.on('seats', (msg) => {
    // Both a claim and a FREE from the crew board arrive in this envelope; a
    // bare map is an older hub, still worth reading.
    const payload = (msg && msg.seats) ? msg : { seats: msg };
    SEATS = payload.seats || {};
    if (payload.displaced && payload.displaced === db.deviceId()) {
      // our chair is gone - stop, do not double-scout
      showBumped(payload.seat, payload.scoutId, !!payload.freed);
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
    if (!currentMatch) loadMatch(nextScoutableMatch());
    // Re-point at the row that has just arrived. `matches` is replaced whole,
    // so the old object is now a copy nothing else will ever write to again -
    // and its `status` is what standby prints and what arms the HUD below.
    else currentMatch = matches.find((m) => m.matchKey === currentMatch.matchKey) || currentMatch;
    // The status that arrives by poll has to arm the phone, exactly as the
    // pushed one does. This is the ordinary case at a venue: the hub is behind
    // the venue's NAT, so Nexus cannot reach it to push, and every `On field`
    // in the building arrives through poll_nexus and this payload instead.
    armIfOnField(pickCurrentMatch());
    // The schedule and the match statuses live on this screen, and the timer
    // no longer redraws it on the off-chance.
    if (screen === 'standby') renderStandby();
  };
  // Conditional: the hub answers 304 when nothing has moved, and then there is
  // no body to parse, no ~60KB blob to write back into IndexedDB, and no
  // re-render. That matters here because the hub broadcasts on a poll and every
  // phone in the building answers at once.
  let heardFromHub = false;
  const pullState = () => net.apiCached('/api/state')
    .then(({ changed, value }) => { heardFromHub = true; if (changed) applyState(value); })
    .catch(() => {});
  await pullState();
  // Only when the hub could not be reached at all. An event the hub says is
  // empty is an answer; yesterday's schedule out of IndexedDB is not.
  if (!heardFromHub) applyState(await db.cacheGet('state'));

  await refreshHistory();

  net.on('nexus', pullState);
  net.on('results', pullState);
  net.on('solved', ({ matchKey }) => markReconciled(matchKey));

  // another scout started this match: adopt their clock and jump into the HUD
  net.on('matchStart', (rec) => {
    if (!rec || !rec.matchKey) return;
    // Worth remembering whoever it belongs to: loadMatch adopts a clock that is
    // already running when a scout arrives late, and the bump screen reads it
    // to tell whether a match is being played right now.
    MATCH_CLOCKS[rec.matchKey] = rec;
    // A phone on the bump screen has been told to stop. Someone else starting
    // the match used to pull it straight back into the HUD - showBumped clears
    // the shared origin, so the guard below did not catch it - and the scout
    // who had just been bumped was logging the same robot again.
    if (screen === 'bumped') { paintBumpWait(); return; }
    if (!currentMatch || rec.matchKey !== currentMatch.matchKey) return;
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
    if (!armIfOnField(m) && screen === 'standby') renderStandby();
  });

  if (PRACTICE) {
    seat.alliance = seat.alliance || 'red';
    seat.station = seat.station || 2;
    seat.scout = seat.scout || 'TRAINEE';
    loadMatch(practiceMatch());
    show('live'); renderLive();
  } else if (seat.scout && seat.alliance && seat.station) {
    loadMatch(nextScoutableMatch()); goStandbyOrLive();
  } else { show('seat'); renderSeat(); }

  // Only now: verifySeat can send us to the bump screen, so it has to run after
  // the screen this boot would otherwise have landed on.
  booted = true;
  verifySeat();

  // The live screen keeps its 200ms tick. It is the match clock and the buzzer,
  // it runs about two and a half minutes at a time, and precision there is worth
  // far more than the saving. `every` pauses it while the page is hidden and
  // runs it once on the way back, so the buzzer check catches up; the clock
  // itself is computed from timestamps, so pausing the render never moves it.
  every(200, () => {
    if (screen !== 'live') return;
    renderLive();
    if (clock.running && clock.elapsed() >= matchSeconds()) {
      clock.pause(); buzz(40); releaseWake();
      $('#afNote').value = entry.payload.note || '';
      afterPage = 1;
      show('after'); renderAfter();
    }
  });

  // Standby and the bump screen are where a phone spends the day, and neither
  // has anything on it that changes five times a second. This is the countdown,
  // once a second - and not even that once the screen has gone dark with the
  // next match still minutes out. Everything else on those screens is rendered
  // from the event that changed it.
  every(1000, () => {
    if (screen === 'standby') {
      const secs = untilNextMatch();
      if (standbyIdle.idle && secs != null && secs > 60) return;
      tickStandby();
    } else if (screen === 'bumped') paintBumpWait();
  });

  window.addEventListener('beforeunload', (e) => {
    if (entry && entry.payload.intervals.length && !net.state.online) { e.preventDefault(); e.returnValue = ''; }
  });
}

main().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML('afterbegin',
    `<div style="padding:24px;font:700 15px var(--f-body);color:var(--red-alert)">Failed to start: ${e.message}</div>`);
});
