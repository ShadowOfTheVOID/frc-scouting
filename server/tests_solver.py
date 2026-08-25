"""Monte Carlo regression gate for solve.py.

Asserts the accuracy claims the design rests on.  Run: python3 server/tests_solver.py
"""
import os
import random
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import solve  # noqa: E402
from rules import BUCKETS, PHASE_IDS  # noqa: E402

ARCH = {"drum": (9.0, 14.0), "steady": (2.5, 4.5), "trickle": (0.6, 1.6)}
TRUE_MULT = {"trickle": 1.2, "steady": 3.5, "dumping": 11.0}
# An alliance can only score in 5 of the 7 windows (two shifts are inactive).
SCORING = ["auto", "transition", "shift1", "shift3", "endgame"]
WIN_LEN = {"auto": 20, "transition": 10, "shift1": 25, "shift3": 25, "endgame": 30}

# scout error model
TIMING_SD, P_MISCLASS, P_MISS, P_OVERHOLD = 0.40, 0.15, 0.05, 0.10


def make_team(rng):
    a = rng.choices(["drum", "steady", "trickle"], weights=[0.3, 0.45, 0.25])[0]
    lo, hi = ARCH[a]
    return {"rate": rng.uniform(lo, hi), "duty": rng.uniform(0.25, 0.7)}


def true_bucket(r):
    return "trickle" if r < 2.2 else ("steady" if r < 6.5 else "dumping")


def sim_alliance(teams, rng, sloppy=1.0):
    """Returns (true_fuel[3], robots payload for solve_match, official_by_phase)."""
    true_f = [0.0] * 3
    robots = [{"team": i, "intervals": []} for i in range(3)]
    official = {}
    for pid in SCORING:
        wlen = WIN_LEN[pid]
        window_true = 0.0
        for i, t in enumerate(teams):
            budget = wlen * t["duty"]
            clock = 0.0
            for _ in range(rng.randint(0, 3)):
                if budget <= 0.3:
                    break
                d = min(budget, rng.uniform(1.0, 6.0))
                budget -= d
                r = max(0.2, t["rate"] * rng.uniform(0.8, 1.2))
                true_f[i] += d * r
                window_true += d * r
                # observation
                if rng.random() < P_MISS * sloppy:
                    clock += d
                    continue
                od = max(0.2, d + rng.gauss(0, TIMING_SD))
                if rng.random() < P_OVERHOLD:
                    od += rng.uniform(0.5, 2.0)
                b = true_bucket(r)
                if rng.random() < P_MISCLASS * sloppy:
                    k = BUCKETS.index(b) + rng.choice((-1, 1))
                    b = BUCKETS[max(0, min(len(BUCKETS) - 1, k))]
                robots[i]["intervals"].append(
                    {"start": clock, "end": clock + od, "phase": pid, "intensity": b}
                )
                clock += d
        official[pid] = round(window_true)
    return true_f, robots, official


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    return ok


def main():
    rng = random.Random(1234)
    passed = True

    # ---- 1. allocations sum exactly to the official total, always
    exact = True
    for _ in range(3000):
        teams = [make_team(rng) for _ in range(3)]
        _, robots, official = sim_alliance(teams, rng)
        res = solve.solve_match(official, robots, bootstrap=0)
        for pid in SCORING:
            got = sum(r["byPhase"].get(pid, 0) for r in res)
            if got != official[pid]:
                exact = False
                break
        if not exact:
            break
    passed &= check("allocations sum exactly to official window totals", exact)

    # ---- 2. per-match accuracy
    errs = []
    for _ in range(4000):
        teams = [make_team(rng) for _ in range(3)]
        tf, robots, official = sim_alliance(teams, rng)
        res = solve.solve_match(official, robots, bootstrap=0)
        for t, r in zip(tf, res):
            if t > 1:
                errs.append(abs(r["fuel"] - t) / t * 100)
    med = st.median(errs)
    passed &= check("per-match median error under 15%", med < 15.0, f"({med:.1f}%)")

    # ---- 3. ranking quality over a simulated 40-team event
    NT, NM = 40, 12
    teams = [make_team(rng) for _ in range(NT)]
    true_tot, est_tot, cnt = [0.0] * NT, [0.0] * NT, [0] * NT
    for _ in range(NT * NM // 3):
        trio = rng.sample(range(NT), 3)
        tf, robots, official = sim_alliance([teams[i] for i in trio], rng)
        res = solve.solve_match(official, robots, bootstrap=0)
        for k, i in enumerate(trio):
            true_tot[i] += tf[k]
            est_tot[i] += res[k]["fuel"]
            cnt[i] += 1
    ta = [true_tot[i] / max(1, cnt[i]) for i in range(NT)]
    ea = [est_tot[i] / max(1, cnt[i]) for i in range(NT)]

    def rank(xs):
        order = sorted(range(len(xs)), key=lambda i: -xs[i])
        r = [0] * len(xs)
        for p, i in enumerate(order):
            r[i] = p
        return r

    rt, re = rank(ta), rank(ea)
    d2 = sum((rt[i] - re[i]) ** 2 for i in range(NT))
    rho = 1 - 6 * d2 / (NT * (NT * NT - 1))
    passed &= check("ranking Spearman rho above 0.95", rho > 0.95, f"(rho={rho:.3f})")

    top8_true = set(sorted(range(NT), key=lambda i: -ta[i])[:8])
    top8_est = set(sorted(range(NT), key=lambda i: -ea[i])[:8])
    hits = len(top8_true & top8_est)
    passed &= check("recovers at least 6 of the true top 8", hits >= 6, f"({hits}/8)")

    # ---- 4. calibrator recovers planted multipliers
    rows = []
    for _ in range(400):
        t3 = [make_team(rng) for _ in range(3)]
        _, robots, official = sim_alliance(t3, rng)
        for pid in SCORING:
            secs = {b: 0.0 for b in BUCKETS}
            for r in robots:
                for iv in r["intervals"]:
                    if iv["phase"] == pid:
                        secs[iv["intensity"]] += iv["end"] - iv["start"]
            rows.append((secs, official[pid]))
    fit = solve.calibrate_multipliers(rows)
    if fit is None:
        passed &= check("calibrator returns a fit", False)
    else:
        devs = {b: abs(fit[b] - TRUE_MULT[b]) / TRUE_MULT[b] * 100 for b in BUCKETS}
        worst = max(devs.values())
        passed &= check(
            "calibrator recovers planted multipliers within 20%",
            worst < 20.0,
            f"({ {b: round(fit[b], 2) for b in BUCKETS} }, worst {worst:.1f}%)",
        )

    # ---- 5. calibrator refuses to fit on thin data rather than guessing
    passed &= check("calibrator declines with too few rows", solve.calibrate_multipliers(rows[:5]) is None)

    # ---- 6. sloppy scouts degrade gracefully, not catastrophically
    sloppy_errs = []
    for _ in range(2000):
        t3 = [make_team(rng) for _ in range(3)]
        tf, robots, official = sim_alliance(t3, rng, sloppy=2.0)
        res = solve.solve_match(official, robots, bootstrap=0)
        for t, r in zip(tf, res):
            if t > 1:
                sloppy_errs.append(abs(r["fuel"] - t) / t * 100)
    smed = st.median(sloppy_errs)
    passed &= check("2x-sloppy scouts stay under 25%", smed < 25.0, f"({smed:.1f}%)")

    # ---- 7. bands are produced and are non-trivial
    t3 = [make_team(rng) for _ in range(3)]
    _, robots, official = sim_alliance(t3, rng)
    res = solve.solve_match(official, robots, bootstrap=150, seed=7)
    passed &= check("bootstrap produces a band", all("band" in r for r in res),
                    f"(bands={[r['band'] for r in res]})")

    print()
    print("ALL PASS" if passed else "FAILURES ABOVE")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
