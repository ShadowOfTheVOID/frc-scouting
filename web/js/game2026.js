// 2026 REBUILT rules. Single source of truth on the client.
// Loaded from rules2026.json so the Python server (server/rules.py) can never disagree.

let RULES = null;

export async function loadRules(fetchImpl = fetch) {
  if (RULES) return RULES;
  const res = await fetchImpl(new URL('../rules2026.json', import.meta.url));
  if (!res.ok) throw new Error(`rules2026.json: HTTP ${res.status}`);
  RULES = await res.json();
  return RULES;
}

/** Inject rules directly (tests, or a server-provided payload). */
export function setRules(r) { RULES = r; }
export function rules() {
  if (!RULES) throw new Error('rules not loaded — await loadRules() first');
  return RULES;
}

export function phases() { return rules().phases; }
export function matchSeconds() { return rules().matchSeconds; }

/** Phase object containing `elapsed` seconds, or null once the match is over. */
export function phaseAt(elapsed) {
  if (elapsed < 0) return null;
  for (const p of rules().phases) {
    if (elapsed >= p.start && elapsed < p.end) return p;
  }
  return null;
}

export function phaseById(id) {
  return rules().phases.find((p) => p.id === id) || null;
}

/**
 * Is `alliance`'s HUB active during `phaseId`?
 *
 * REBUILT catch-up mechanic: the alliance that scores MORE fuel in AUTO has its
 * hub set INACTIVE for SHIFT 1, then status alternates each shift. Both hubs are
 * active during auto, transition, and endgame.
 *
 * @param {string} phaseId
 * @param {'red'|'blue'} alliance
 * @param {'red'|'blue'|null} autoWinner - alliance that scored more fuel in auto
 * @returns {boolean|null} null when the phase is a shift and autoWinner is unknown
 */
export function hubActive(phaseId, alliance, autoWinner) {
  const p = phaseById(phaseId);
  if (!p) return null;
  if (p.bothHubsActive) return true;
  if (autoWinner !== 'red' && autoWinner !== 'blue') return null;
  const s = p.shiftIndex;
  // Auto winner sits out shift 1, then alternates; opponent is the complement.
  return autoWinner === alliance ? s % 2 === 0 : s % 2 === 1;
}

/** Points for a tower level in a given period ('auto' | 'teleop'). */
export function towerPoints(level, period) {
  const t = rules().tower[period];
  if (!t) return 0;
  return t[level] ?? 0;
}

export function rpThresholds(eventLevel = 'regional') {
  const rp = rules().rankingPoints;
  return {
    energized: rp.energized[eventLevel] ?? rp.energized.regional,
    supercharged: rp.supercharged[eventLevel] ?? rp.supercharged.regional,
    traversal: rp.traversal[eventLevel] ?? rp.traversal.regional,
  };
}

export function intensityBuckets() { return rules().intensityBuckets; }

/**
 * Points a single scouted robot is credited with, given resolved fuel counts.
 * `fuelByPhase` maps phaseId -> fuel this robot put in an ACTIVE hub.
 */
export function scoreFromScoutEntry(entry, fuelByPhase = {}) {
  let pts = 0;
  for (const n of Object.values(fuelByPhase)) pts += (n || 0) * rules().fuelPoints;
  if (entry.autoTower && entry.autoTower !== 'None') pts += towerPoints(entry.autoTower, 'auto');
  if (entry.endgameTower && entry.endgameTower !== 'None') pts += towerPoints(entry.endgameTower, 'teleop');
  return pts;
}

/** Total seconds of shooting recorded in a phase, by intensity bucket id. */
export function bucketSecondsByPhase(intervals) {
  const out = {};
  for (const iv of intervals || []) {
    const ph = iv.phase || (phaseAt(iv.start) || {}).id;
    if (!ph) continue;
    (out[ph] ||= {});
    const dur = Math.max(0, (iv.end ?? iv.start) - iv.start);
    out[ph][iv.intensity] = (out[ph][iv.intensity] || 0) + dur;
  }
  return out;
}
