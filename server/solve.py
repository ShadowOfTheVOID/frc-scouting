"""Turn scout shooting-intervals into per-robot fuel counts.

The scout never produces an absolute number.  TBA publishes the official fuel
count per alliance per window (auto / transition / shift1-4 / endgame), and this
module distributes that exact total across the three robots in proportion to
`duration x intensity`.  Integers are rounded by largest remainder so they sum
to the official count exactly.

Accuracy, from the Monte Carlo in tests_solver.py: ~13% median per-match error
per robot, ~8% on a 12-match average, Spearman 0.97 for ranking.  Single-match
numbers therefore always carry a band and are never shown as bare integers.
"""
import random

from rules import BUCKET_PRIORS, BUCKETS, PHASE_IDS


# ---------------------------------------------------------------- allocation

def largest_remainder(total, weights):
    """Split integer `total` across `weights`, summing to exactly `total`."""
    n = len(weights)
    if n == 0:
        return []
    s = float(sum(weights))
    if total <= 0:
        return [0] * n
    if s <= 0:  # nobody observed shooting but fuel was scored - split evenly
        base = total // n
        out = [base] * n
        for i in range(total - base * n):
            out[i] += 1
        return out
    exact = [total * w / s for w in weights]
    floors = [int(x) for x in exact]
    rem = total - sum(floors)
    order = sorted(range(n), key=lambda i: -(exact[i] - floors[i]))
    for i in range(rem):
        floors[order[i % n]] += 1
    return floors


def interval_weight(intervals, mult):
    """Sum of duration * bucket-multiplier over one robot's intervals."""
    w = 0.0
    for iv in intervals or []:
        dur = max(0.0, float(iv.get("end", iv["start"])) - float(iv["start"]))
        w += dur * mult.get(iv.get("intensity"), mult.get("steady", 1.0))
    return w


def solve_window(total, per_robot_intervals, mult=None):
    """Allocate one window's official fuel count across three robots."""
    mult = mult or dict(BUCKET_PRIORS)
    weights = [interval_weight(ivs, mult) for ivs in per_robot_intervals]
    return largest_remainder(int(total), weights), weights


def _intervals_for_phase(intervals, phase_id):
    return [iv for iv in (intervals or []) if iv.get("phase") == phase_id]


def solve_match(official_by_phase, robots, mult=None, bootstrap=200, seed=None):
    """Resolve one alliance's match.

    official_by_phase : {phase_id: fuel count from TBA}
    robots            : [{"team":..., "intervals":[{start,end,phase,intensity}]}] x3
    returns           : [{"team", "fuel", "band", "byPhase", "provisional"}]
    """
    mult = mult or dict(BUCKET_PRIORS)
    n = len(robots)
    totals = [0] * n
    by_phase = [dict() for _ in range(n)]

    for pid in PHASE_IDS:
        total = official_by_phase.get(pid)
        if not total:
            continue
        per_robot = [_intervals_for_phase(r.get("intervals"), pid) for r in robots]
        counts, _ = solve_window(total, per_robot, mult)
        for i, c in enumerate(counts):
            totals[i] += c
            by_phase[i][pid] = c

    bands = _bootstrap_band(official_by_phase, robots, mult, bootstrap, seed)
    return [
        {
            "team": robots[i].get("team"),
            "fuel": totals[i],
            "band": bands[i],
            "byPhase": by_phase[i],
            "provisional": False,
        }
        for i in range(n)
    ]


def _bootstrap_band(official_by_phase, robots, mult, iters, seed):
    """Empirical +/- band: resample bucket calls and multipliers, take the spread.

    Bucket misclassification is the dominant error source, so the band widens
    exactly where a robot's fuel came from contested windows with ambiguous
    intensity - which is what we want it to communicate.
    """
    n = len(robots)
    if iters <= 0:
        return [0] * n
    rng = random.Random(seed)
    samples = [[] for _ in range(n)]
    idx = {b: i for i, b in enumerate(BUCKETS)}

    for _ in range(iters):
        jitter = {b: mult[b] * rng.uniform(0.85, 1.15) for b in mult}
        totals = [0] * n
        for pid in PHASE_IDS:
            total = official_by_phase.get(pid)
            if not total:
                continue
            per_robot = []
            for r in robots:
                out = []
                for iv in _intervals_for_phase(r.get("intervals"), pid):
                    j = dict(iv)
                    if rng.random() < 0.15:  # observed misclassification rate
                        k = idx.get(iv.get("intensity"), 1) + rng.choice((-1, 1))
                        j["intensity"] = BUCKETS[max(0, min(len(BUCKETS) - 1, k))]
                    out.append(j)
                per_robot.append(out)
            counts, _ = solve_window(total, per_robot, jitter)
            for i, c in enumerate(counts):
                totals[i] += c
        for i in range(n):
            samples[i].append(totals[i])

    bands = []
    for s in samples:
        s.sort()
        lo = s[int(0.16 * len(s))]
        hi = s[min(len(s) - 1, int(0.84 * len(s)))]
        bands.append(int(round((hi - lo) / 2.0)))
    return bands


def provisional_match(robots, mult=None):
    """No official data yet (practice, offseason, TBA lagging).

    Falls back to raw rate x duration - the same estimate Lovat produces - and
    marks it provisional so the UI can show it differently and the solver can
    replace it once TBA posts.
    """
    mult = mult or dict(BUCKET_PRIORS)
    out = []
    for r in robots:
        w = interval_weight(r.get("intervals"), mult)
        out.append({
            "team": r.get("team"),
            "fuel": int(round(w)),
            "band": int(round(w * 0.35)),
            "byPhase": {},
            "provisional": True,
        })
    return out


# ------------------------------------------------------------- calibration

def calibrate_multipliers(rows, ridge=1e-3, min_rows=40):
    """Least-squares fit bucket multipliers against official window totals.

    Each row is (bucket_seconds_dict, official_total) for one window summed over
    the whole alliance.  Miscalibrated multipliers were the largest controllable
    error source in simulation (19.1% vs 12.9%), and this converges in roughly
    30 matches, so the shipped priors are only ever a cold start.

    Returns None when there is not yet enough data to beat the prior.
    """
    rows = [r for r in rows if sum(r[0].values()) > 0]
    if len(rows) < min_rows:
        return None

    k = len(BUCKETS)
    ata = [[0.0] * k for _ in range(k)]
    aty = [0.0] * k
    for secs, total in rows:
        v = [float(secs.get(b, 0.0)) for b in BUCKETS]
        for i in range(k):
            if v[i] == 0.0:
                continue
            aty[i] += v[i] * float(total)
            for j in range(k):
                ata[i][j] += v[i] * v[j]
    for i in range(k):
        ata[i][i] += ridge

    m = [ata[i][:] + [aty[i]] for i in range(k)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            return None
        for r in range(k):
            if r != c and m[r][c] != 0.0:
                f = m[r][c] / m[c][c]
                for j in range(c, k + 1):
                    m[r][j] -= f * m[c][j]

    fit = {}
    for i, b in enumerate(BUCKETS):
        val = m[i][k] / m[i][i]
        if val <= 0 or val > 60:  # implausible - keep the prior for this bucket
            return None
        fit[b] = val
    # multipliers must stay monotonic in intensity or the fit is junk
    vals = [fit[b] for b in BUCKETS]
    if vals != sorted(vals):
        return None
    return fit
