"""Per-team aggregates for the dashboard.

Deliberately separates EXACT fields (straight from TBA, no estimation) from
ESTIMATED ones (solver output, always carrying a band).  The picklist leans on
the exact side; fuel volume only breaks ties.
"""
import math
import statistics as st

import rules


def _mean(xs):
    return st.mean(xs) if xs else 0.0


def _stdev(xs):
    return st.pstdev(xs) if len(xs) > 1 else 0.0


def event_summary(store, event_key):
    matches = store.matches(event_key)
    entries = store.scout_entries(event_key)
    solved = store.solved(event_key)
    teams = {t["team"]: t for t in store.teams(event_key)}

    by_match = {m["matchKey"]: m for m in matches}
    solved_by = {}
    for s in solved:
        solved_by.setdefault(s["team"], []).append(s)

    entries_by_team = {}
    for e in entries:
        entries_by_team.setdefault(e["team"], []).append(e)

    out = {}
    for team in sorted(set(list(teams) + list(entries_by_team) + list(solved_by))):
        out[team] = _team_summary(team, teams.get(team, {}), entries_by_team.get(team, []),
                                  solved_by.get(team, []), by_match)

    return {
        "eventKey": event_key,
        "teams": out,
        "scouts": _scout_reliability(entries, by_match, solved),
        "coverage": _coverage(matches, entries),
    }


def _team_summary(team, meta, entries, solved, by_match):
    # ---------------------------------------------- EXACT (from TBA)
    climbs = {"Level1": 0, "Level2": 0, "Level3": 0, "None": 0}
    auto_climbs = 0
    tower_pts = []
    wins = losses = ties = 0
    rps = []
    official_matches = 0

    for e in entries:
        m = by_match.get(e["matchKey"])
        if not m or not m.get("breakdown"):
            continue
        alliance = e.get("alliance")
        info = (m["breakdown"] or {}).get(alliance)
        lineup = m.get(alliance) or []
        if not info or team not in lineup:
            continue
        official_matches += 1
        idx = lineup.index(team)
        eg = (info.get("endgameTower") or [None, None, None])[idx] or "None"
        climbs[eg] = climbs.get(eg, 0) + 1
        tower_pts.append(rules.tower_points(eg, "teleop"))
        at = (info.get("autoTower") or [None, None, None])[idx]
        if at and at != "None":
            auto_climbs += 1
            tower_pts[-1] += rules.tower_points(at, "auto")
        if info.get("rp") is not None:
            rps.append(info["rp"])

    # ---------------------------------------------- ESTIMATED (solver)
    fuels = [s["fuel"] for s in solved if not s.get("provisional")]
    bands = [s["band"] for s in solved if not s.get("provisional")]
    avg_fuel = _mean(fuels)
    # combine per-match band with match-to-match spread
    band = math.sqrt(_mean([b * b for b in bands]) + _stdev(fuels) ** 2) / math.sqrt(max(1, len(fuels)))

    # ---------------------------------- OBSERVED (categorical, scout-reliable)
    wasted = []
    stockpiles = 0
    active_secs = []
    defense = []
    driver = []
    died = no_show = tipped = 0
    feeds = 0
    feed_secs = []
    defense_secs = []
    preloads = []

    for e in entries:
        p = e.get("payload") or {}
        m = by_match.get(e["matchKey"])
        auto_winner = ((m or {}).get("breakdown") or {}).get("autoWinner")
        alliance = e.get("alliance")
        ivs = p.get("intervals") or []

        waste_s = act_s = 0.0
        for iv in ivs:
            dur = max(0.0, float(iv.get("end", iv["start"])) - float(iv["start"]))
            act = rules.hub_active(iv.get("phase"), alliance, auto_winner)
            if act is False:
                waste_s += dur
            elif act is True:
                act_s += dur
        if ivs:
            wasted.append(waste_s / max(0.001, waste_s + act_s) * 100.0)
            active_secs.append(act_s)

        if _stockpiled(ivs, alliance, auto_winner):
            stockpiles += 1
        fi = p.get("feedIntervals") or []
        if fi:
            feeds += 1
            feed_secs.append(sum(max(0.0, float(iv.get("end", iv["start"])) - float(iv["start"])) for iv in fi))
        di = p.get("defenseIntervals") or []
        if di:
            defense_secs.append(sum(max(0.0, float(iv.get("end", iv["start"])) - float(iv["start"])) for iv in di))
        if p.get("preload") is not None:
            preloads.append(int(p.get("preload") or 0))
        if p.get("defenseRating"):
            defense.append(p["defenseRating"])
        if p.get("driverRating"):
            driver.append(p["driverRating"])
        died += 1 if p.get("died") else 0
        tipped += 1 if p.get("tipped") else 0
        no_show += 1 if p.get("noShow") else 0

    n = max(1, len(entries))
    return {
        "team": team,
        "name": meta.get("name"),
        "matchesScouted": len(entries),
        "exact": {
            "matchesWithOfficial": official_matches,
            "climbs": climbs,
            "climbRate": {k: (v / official_matches * 100.0 if official_matches else 0.0)
                          for k, v in climbs.items()},
            "autoClimbs": auto_climbs,
            "autoClimbRate": round(auto_climbs / official_matches * 100.0, 1) if official_matches else 0.0,
            "avgTowerPoints": round(_mean(tower_pts), 1),
            "bestClimb": _best_climb(climbs),
            "avgRP": round(_mean(rps), 2) if rps else None,
        },
        "estimated": {
            "avgFuel": round(avg_fuel, 1),
            "band": round(band, 1),
            "matches": len(fuels),
            "consistency": round(_stdev(fuels), 1),
            "cycleRate": round(avg_fuel / _mean(active_secs), 2) if _mean(active_secs) > 0 else None,
        },
        "observed": {
            "wastedFuelPct": round(_mean(wasted), 1) if wasted else None,
            "stockpileRate": round(stockpiles / n * 100.0, 1),
            "feedRate": round(feeds / n * 100.0, 1),
            "feedSecs": round(_mean(feed_secs), 1) if feed_secs else 0.0,
            "defenseSecs": round(_mean(defense_secs), 1) if defense_secs else 0.0,
            "avgPreload": round(_mean(preloads), 1) if preloads else None,
            "defense": round(_mean(defense), 1) if defense else None,
            "driver": round(_mean(driver), 1) if driver else None,
            "diedRate": round(died / n * 100.0, 1),
            "tippedRate": round(tipped / n * 100.0, 1),
            "noShowRate": round(no_show / n * 100.0, 1),
        },
    }


def _best_climb(climbs):
    for lvl in ("Level3", "Level2", "Level1"):
        if climbs.get(lvl):
            return lvl
    return "None"


def _stockpiled(intervals, alliance, auto_winner):
    """Held fuel through an inactive shift, then dumped when the hub flipped active.

    The defining skill of REBUILT and invisible to every public metric, because
    it needs to know what a robot did while scoring was worth nothing.
    """
    if not intervals or auto_winner not in ("red", "blue"):
        return False
    shifts = [p for p in rules.PHASES if p.get("shiftIndex")]
    for i, sh in enumerate(shifts[:-1]):
        nxt = shifts[i + 1]
        if rules.hub_active(sh["id"], alliance, auto_winner) is not False:
            continue
        if rules.hub_active(nxt["id"], alliance, auto_winner) is not True:
            continue
        quiet = not any(iv.get("phase") == sh["id"] for iv in intervals)
        burst = any(iv.get("phase") == nxt["id"]
                    and iv.get("intensity") == "dumping"
                    and float(iv["start"]) - nxt["start"] <= 6.0
                    for iv in intervals)
        if quiet and burst:
            return True
    return False


def _scout_reliability(entries, by_match, solved):
    """Score each scout by how well their intervals reconcile with official totals.

    A scout claiming heavy shooting in a window that officially scored 4 fuel is
    the signal we act on: their contributions get downweighted and their matches
    flagged.
    """
    agg = {}
    for e in entries:
        sid = e.get("scoutId") or "unknown"
        rec = agg.setdefault(sid, {"scoutId": sid, "matches": 0, "residuals": [], "empty": 0})
        rec["matches"] += 1
        m = by_match.get(e["matchKey"])
        info = ((m or {}).get("breakdown") or {}).get(e.get("alliance"))
        ivs = (e.get("payload") or {}).get("intervals") or []
        if not info:
            continue
        official = sum((info.get("windows") or {}).values())
        if official > 20 and not ivs:
            rec["empty"] += 1
            continue
        for pid, total in (info.get("windows") or {}).items():
            secs = sum(max(0.0, float(iv.get("end", iv["start"])) - float(iv["start"]))
                       for iv in ivs if iv.get("phase") == pid)
            if total == 0 and secs > 3.0:
                rec["residuals"].append(1.0)   # shooting claimed where nothing scored
            elif total > 0 and secs == 0:
                rec["residuals"].append(0.25)  # plausible: a partner scored it

    out = []
    for rec in agg.values():
        penalty = _mean(rec["residuals"]) + (rec["empty"] / max(1, rec["matches"]))
        out.append({
            "scoutId": rec["scoutId"],
            "matches": rec["matches"],
            "reliability": round(max(0.0, min(1.0, 1.0 - penalty)), 2),
            "missedMatches": rec["empty"],
        })
    return sorted(out, key=lambda r: -r["reliability"])


def _coverage(matches, entries):
    scouted = {}
    for e in entries:
        scouted.setdefault(e["matchKey"], set()).add(e["team"])
    total = expected = 0
    for m in matches:
        lineup = (m.get("red") or []) + (m.get("blue") or [])
        if not lineup:
            continue
        expected += len(lineup)
        total += len(scouted.get(m["matchKey"], set()) & set(lineup))
    return {"robotsScouted": total, "robotsExpected": expected,
            "pct": round(total / expected * 100.0, 1) if expected else 0.0}
