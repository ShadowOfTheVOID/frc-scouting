"""Per-team aggregates for the dashboard.

Deliberately separates EXACT fields (straight from TBA, no estimation) from
ESTIMATED ones (solver output, always carrying a band).  The picklist leans on
the exact side; fuel volume only breaks ties.

Five blocks per team: `exact` (TBA), `estimated` (our solver), `observed`
(scout yes/no answers), `epa` (Statbotics) and `lovat` (other teams' scouts,
pulled from lovat.app).  The last two are the ones from outside, which is
exactly why they earn a place next to a number we produced ourselves - and
exactly why they stay in their own blocks.  `lovat` in particular is somebody
else's scouting, unverified and collected to somebody else's standard: it is
shown for comparison and feeds nothing.  Neither the solver nor the picklist
reads it.  Every block is null-safe: a missing source reads as unknown, never
as zero.
"""
import math
import statistics as st

import rules
import solve


def _mean(xs):
    return st.mean(xs) if xs else 0.0


def _stdev(xs):
    return st.pstdev(xs) if len(xs) > 1 else 0.0


def event_summary(store, event_key, include_scouts=False):
    """Per-team aggregates.

    `include_scouts` gates the per-scout quality scores. They name individuals
    and grade them, and /api/analytics is readable by anything on the venue
    wifi, so the hub only fills that block in for the strategy lead - see
    Handler.do_GET. Everything else on this payload is about robots.
    """
    matches = store.matches(event_key)
    # Exact side-tables, both straight from an API. Absent is the normal case
    # (no key, or Statbotics down) and must read as "unknown", never as zero.
    rankings = store.get(f"rankings:{event_key}") or {}
    epa = store.get(f"epa:{event_key}") or {}
    lovat_rows = store.get(f"lovat:{event_key}") or {}
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

    # Defence is logged against the robot doing it; the robot on the receiving
    # end wants to know too, and only a pass over every entry can say.
    defended_by = {}
    for e in entries:
        target = (e.get("payload") or {}).get("defenseTarget")
        if target is None:
            continue
        try:
            target = int(target)
        except (TypeError, ValueError):
            continue
        defended_by.setdefault(target, {})
        defended_by[target][e["team"]] = defended_by[target].get(e["team"], 0) + 1

    # A team Lovat has and we do not is still a team at this event worth a row -
    # it is the case where somebody else's scouting is most use to us.
    lovat_teams = [t for t in (_int(k) for k in lovat_rows) if t is not None]

    out = {}
    for team in sorted(set(list(teams) + list(entries_by_team) + list(solved_by)
                           + lovat_teams)):
        out[team] = _team_summary(team, teams.get(team, {}), entries_by_team.get(team, []),
                                  solved_by.get(team, []), by_match,
                                  _lookup(rankings, team), _lookup(epa, team),
                                  defended_by.get(team) or {},
                                  _lookup(lovat_rows, team))

    return {
        "eventKey": event_key,
        "teams": out,
        "coverage": _coverage(matches, entries),
        "scoreReport": score_report(store, event_key, matches, entries),
        **({"scouts": _scout_reliability(entries, by_match, solved)} if include_scouts else {}),
    }


def score_report(store, event_key, matches=None, entries=None):
    """What the scouts said a match was worth, against what TBA says it was.

    The one honest way to grade the scouting, and it took some care to get
    right: the SOLVED fuel cannot be compared to TBA at all, because the solver
    *distributes* TBA's official window totals - add the three robots back up
    and you have reproduced TBA exactly, by construction, however wrong the
    scouts were.

    So this uses the raw estimate instead: duration x intensity over the
    intervals alone (`solve.interval_weight`), restricted to windows where that
    alliance's hub was actually live. That number never sees TBA, which is what
    makes the comparison mean something.

    Per alliance-match plus event rollups. Names no scout - it grades the data.
    """
    matches = store.matches(event_key) if matches is None else matches
    entries = store.scout_entries(event_key) if entries is None else entries
    mult = store.get("multipliers") or dict(rules.BUCKET_PRIORS)

    by_match = {}
    for e in entries:
        by_match.setdefault(e["matchKey"], {}).setdefault(e["team"], e)

    rows, errs, called, decided = [], [], 0, 0
    for m in matches:
        bd = m.get("breakdown")
        if not bd:
            continue
        auto_winner = bd.get("autoWinner")
        seen = by_match.get(m["matchKey"], {})
        sides = {}
        for alliance in ("red", "blue"):
            info = bd.get(alliance)
            if not info:
                continue
            official = sum(v for v in (info.get("windows") or {}).values() if v)
            scout_fuel, scouted = 0.0, 0
            tower = 0.0
            for idx, team in enumerate(m.get(alliance) or []):
                e = seen.get(team)
                if not e:
                    continue
                scouted += 1
                ivs = [iv for iv in ((e.get("payload") or {}).get("intervals") or [])
                       if rules.hub_active(iv.get("phase"), alliance, auto_winner) is True]
                scout_fuel += solve.interval_weight(ivs, mult)
                p = e.get("payload") or {}
                tower += rules.tower_points(p.get("endgameTower") or "None", "teleop")
                tower += rules.tower_points(p.get("autoTower") or "None", "auto")
            if not scouted:
                continue
            fuel_pts = rules.RULES.get("fuelPoints", 1)
            row = {
                "matchKey": m["matchKey"], "label": m.get("label"), "alliance": alliance,
                "robotsScouted": scouted,
                "officialFuel": official,
                "scoutFuel": round(scout_fuel, 1),
                "officialPoints": info.get("totalPoints"),
                "scoutPoints": round(scout_fuel * fuel_pts + tower, 1),
                "deltaPct": (round((scout_fuel - official) / official * 100.0, 1)
                             if official else None),
            }
            rows.append(row)
            sides[alliance] = row
            # Only a fully-watched alliance says anything about our accuracy.
            if official > 20 and scouted == 3 and row["deltaPct"] is not None:
                errs.append(abs(row["deltaPct"]))

        # Would our numbers have picked the winner?
        if len(sides) == 2 and all(s["robotsScouted"] == 3 for s in sides.values()):
            ours = sides["red"]["scoutPoints"] - sides["blue"]["scoutPoints"]
            rp = (bd.get("red") or {}).get("totalPoints")
            bp = (bd.get("blue") or {}).get("totalPoints")
            if rp is not None and bp is not None and rp != bp and ours != 0:
                decided += 1
                if (ours > 0) == (rp > bp):
                    called += 1

    errs.sort()
    return {
        "rows": rows[::-1],                     # newest match first, like the log
        "compared": len(errs),
        "medianPct": round(st.median(errs), 1) if errs else None,
        "p90Pct": round(errs[min(len(errs) - 1, int(0.9 * len(errs)))], 1) if errs else None,
        "biasPct": round(_mean([r["deltaPct"] for r in rows
                                if r["deltaPct"] is not None and r["robotsScouted"] == 3]), 1)
                   if errs else None,
        "calledIt": called,
        "decided": decided,
        "calledPct": round(called / decided * 100.0, 1) if decided else None,
    }


def _lookup(table, team):
    """kv-store tables round-trip through JSON, so integer keys come back as strings."""
    return table.get(team) or table.get(str(team)) or {}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _team_summary(team, meta, entries, solved, by_match, ranking=None, epa=None,
                  defended_by=None, lovat=None):
    ranking, epa = ranking or {}, epa or {}
    defended_by = defended_by or {}
    lovat = lovat or {}
    # ---------------------------------------------- EXACT (from TBA)
    climbs = {"Level1": 0, "Level2": 0, "Level3": 0, "None": 0}
    auto_climbs = 0
    tower_pts = []
    wins = losses = ties = 0
    rps = []
    official_matches = 0

    # One match counts once, however many scouts watched the robot. These numbers
    # come from TBA, not from the scout - and a HAND OVER mid-match leaves two
    # entries for the same (match, team), which would otherwise double every
    # climb, tower point and RP for that match.
    seen_matches = set()
    for e in entries:
        m = by_match.get(e["matchKey"])
        if not m or not m.get("breakdown"):
            continue
        if e["matchKey"] in seen_matches:
            continue
        alliance = e.get("alliance")
        info = (m["breakdown"] or {}).get(alliance)
        lineup = m.get(alliance) or []
        if not info or team not in lineup:
            continue
        seen_matches.add(e["matchKey"])
        official_matches += 1
        opp = "blue" if alliance == "red" else "red"
        ours = info.get("totalPoints")
        theirs = ((m["breakdown"] or {}).get(opp) or {}).get("totalPoints")
        if ours is not None and theirs is not None:
            if ours > theirs:
                wins += 1
            elif ours < theirs:
                losses += 1
            else:
                ties += 1
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
    # Two different spreads, and using the wrong one gives a confidently wrong
    # answer.  `spread` is how much a single match varies - solver band plus
    # match-to-match variation - and is what a head-to-head projection needs.
    # `band` divides that by sqrt(n): how well we know the team's AVERAGE, which
    # is what "averages 84 +/- 7 fuel" means.
    spread = math.sqrt(_mean([b * b for b in bands]) + _stdev(fuels) ** 2)
    band = spread / math.sqrt(max(1, len(fuels)))

    # ---------------------------------- OBSERVED (categorical, scout-reliable)
    wasted = []
    stockpiles = 0
    active_secs = []
    defense = []
    driver = []
    died = no_show = tipped = fouls = auto_failed = 0
    start_zones = {}
    defense_against = {}
    feeds = 0
    feed_secs = []
    defense_secs = []
    preloads = []
    notes = []

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
        if (p.get("note") or "").strip():
            notes.append({"matchKey": e["matchKey"], "scoutId": e.get("scoutId"),
                          "at": e.get("updatedAt"), "note": p["note"].strip()})
        # Deliberately not `is not None` on a defaulted field: the HUD used to
        # ship preload as a hard 0 nobody could change, so every team on the
        # dashboard read "average preload 0". Only a real answer counts.
        if isinstance(p.get("preload"), (int, float)):
            preloads.append(int(p["preload"]))
        if p.get("defenseRating"):
            defense.append(p["defenseRating"])
        if p.get("driverRating"):
            driver.append(p["driverRating"])
        died += 1 if p.get("died") else 0
        tipped += 1 if p.get("tipped") else 0
        no_show += 1 if p.get("noShow") else 0
        fouls += 1 if p.get("fouls") else 0
        auto_failed += 1 if p.get("autoFailed") else 0
        if p.get("startPosition"):
            z = str(p["startPosition"])
            start_zones[z] = start_zones.get(z, 0) + 1
        tgt = p.get("defenseTarget")
        if tgt is not None:
            try:
                tgt = int(tgt)
                defense_against[tgt] = defense_against.get(tgt, 0) + 1
            except (TypeError, ValueError):
                pass

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
            # Official standings win over anything we can derive: they count
            # every match, not just the ones a scout was sitting for.
            "rank": ranking.get("rank"),
            "rankingPoints": ranking.get("rankingPoints"),
            "opr": ranking.get("opr"),
            "record": _record(ranking, wins, losses, ties),
        },
        # A fourth kind of number, and the only one from outside: an
        # independent read on the same robot, which is what makes it worth
        # showing next to a banded estimate we produced ourselves.
        "epa": {
            "epa": epa.get("epa"),
            "auto": epa.get("auto"),
            "teleop": epa.get("teleop"),
            "endgame": epa.get("endgame"),
            "rank": epa.get("rank"),
        },
        "estimated": {
            "avgFuel": round(avg_fuel, 1),
            "band": round(band, 1),
            "matchBand": round(spread, 1),
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
            "foulRate": round(fouls / n * 100.0, 1),
            "autoFailRate": round(auto_failed / n * 100.0, 1),
            # {zone: matches}. Absent means no scout has said, which is not the
            # same as "started nowhere".
            "startPositions": start_zones,
            "startZone": (max(start_zones, key=start_zones.get) if start_zones else None),
            "startZonePct": (round(max(start_zones.values()) / sum(start_zones.values()) * 100.0, 1)
                             if start_zones else None),
            # {team: matches} in both directions.
            "defenseAgainst": defense_against,
            "defendedBy": defended_by,
        },
        # A fifth kind of number, and the second one from outside: other teams'
        # scouts, via lovat.app. Unverified, collected to somebody else's
        # standard, and read here only as a second opinion - nothing in
        # solve.py or the picklist sees it. Absent means nobody at this event
        # uploaded that robot to Lovat, which is not the same as a zero.
        "lovat": {k: lovat.get(k) for k in (
            "matches", "avgFuel", "fuelPerSec", "accuracy", "throughput",
            "volleys", "driver", "totalPoints", "autoPoints", "teleopPoints",
            "feedSecs", "feedingRate", "feedsPerMatch", "ballsFed",
            "defenseSecs", "contactDefenseSecs", "campingDefenseSecs",
            "defenseEffectiveness", "climbs", "climbRate", "bestClimb",
            "autoClimbRate", "beachedRate", "roles", "intakeTypes",
            "scouters", "notes", "unmatched")},
        "notes": sorted(notes, key=lambda x: -(x.get("at") or 0)),
    }


def _record(ranking, wins, losses, ties):
    """W-L-T, official when we have it, otherwise from the matches we scouted."""
    if ranking.get("wins") is not None:
        return {"wins": ranking.get("wins"), "losses": ranking.get("losses"),
                "ties": ranking.get("ties"), "official": True}
    if wins or losses or ties:
        return {"wins": wins, "losses": losses, "ties": ties, "official": False}
    return None


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

    Coaching material for the scouting lead, and nothing else: it does not
    downweight anyone's contribution to the solver, and it is never served to
    the room. A scout claiming heavy shooting in a window that officially scored
    4 fuel is someone to go and stand next to for a match, not someone to put on
    a leaderboard.
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
