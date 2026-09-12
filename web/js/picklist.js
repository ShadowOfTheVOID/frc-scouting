// How a robot is ranked on the draft board.
//
// One copy, imported by both the dashboard (web/js/desk.js) and the sheet the
// lead prints and carries into alliance selection (web/picklist_print.html).
// It lived in both, and the two drifted the moment one of them was corrected:
// the screen and the paper then ranked the same robots differently, which is
// found out in the one room where there is no time to find anything out.
//
// The weights are the lead's - two sets, one per board - and `maxFuel` is the
// best average at the event, passed in so ranking N teams does not walk the
// whole table N times.

/** Climb levels as a fraction of the best one. */
const CLIMB = { Level3: 1, Level2: 0.65, Level1: 0.3, None: 0 };

/**
 * One robot's score out of the weights it is being judged by.
 *
 * Every term is 0..1 before the weight is applied, so a weight of 30 means the
 * same thing in every row. Absent reads as the bottom of its scale and never
 * as an error: a robot nobody has rated for defence scores nothing for it, not
 * a fifth of a point.
 */
export function score(t, weights, maxFuel) {
  const e = t.exact, o = t.observed, s = t.estimated;
  const climb = CLIMB[e.bestClimb] || 0;
  const l3 = (e.climbRate.Level3 || 0) / 100;
  // Broke down or never turned up. Both end the same way for an alliance.
  const rel = 1 - Math.min(1, (o.diedRate + o.noShowRate) / 100);
  const stock = (o.stockpileRate || 0) / 100;
  // The phone collects 1..4 ('not at all' .. 'a lot'), so 1 is the floor of the
  // scale and not a fifth of the way up it. Divided by 5 it was, which put a
  // robot a scout had explicitly marked as NOT defending ahead of one nobody
  // had rated at all - on the second-pick board, where defence is weighted 35.
  const def = Math.max(0, ((o.defense || 1) - 1) / 3);
  return weights.climb * (climb * 0.6 + l3 * 0.4) + weights.reliability * rel +
         weights.stockpile * stock + weights.fuel * (s.avgFuel / (maxFuel || 1)) +
         weights.defense * def;
}
