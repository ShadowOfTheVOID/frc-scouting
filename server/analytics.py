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
import threading
import weakref

import rules
import solve

# What event_summary reads.  Kept here, beside the reads themselves, and mirrored
# by ANALYTICS_SCOPES in hub.py for the matching ETag.
SCOPES = ("matches", "teams", "scout_entries", "solved",
          "kv:rankings", "kv:epa", "kv:lovat", "kv:multipliers")

# Per store, not global: a snapshot restore opens a second Store over the
# restored file, and a fresh one starts every counter at zero - so a global
# cache could hand it an answer computed from a different database that happens
# to share a version string. Weak, so closing a store drops its cache with it.
_caches = weakref.WeakKeyDictionary()
_cache_lock = threading.Lock()


def _mean(xs):
    return st.mean(xs) if xs else 0.0


def _stdev(xs):
    return st.pstdev(xs) if len(xs) > 1 else 0.0


def event_summary(store, event_key, include_scouts=False):
    """Per-team aggregates, memoized until something it reads is written.

    `include_scouts` gates the per-scout quality scores. They name individuals
    and grade them, and /api/analytics is readable by anything on the venue
    wifi, so the hub only fills that block in for the strategy lead - see
    Handler.do_GET. Everything else on this payload is about robots.

    This walks every scouting row at the event and every match x alliance x
    robot, re-running the phase split and the interval weighting, and it used to
    do all of that again for every caller: two dashboards polling, both CSV
    exports, the mirror push every sixty seconds, and - the one that hurt - the
    AI panel, which the picklist fires on every keystroke in its search box.
    The key is the store's write counters for exactly the scopes read below, so
    a stale answer is not possible: any write to any of them changes the key.
    """
    key = (event_key, include_scouts, store.version_for(*SCOPES))
    with _cache_lock:
        hit = _caches.setdefault(store, {}).get(key)
    if hit is not None:
        return hit
    out = _event_summary(store, event_key, include_scouts)
    with _cache_lock:
        cache = _caches.setdefault(store, {})
        # Four covers the lead and the room, at the current version and the one
        # before it. Past that, every remaining entry is a version nobody can
        # ask for again, so the whole thing goes.
        if len(cache) > 4:
            cache.clear()
        cache[key] = out
    return out


def _event_summary(store, event_key, include_scouts=False):
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
    # end wants to know too, and only a pass over every entry can say. Seconds
    # as well as matches: "defended three times" and "defended for ninety
    # seconds" are different facts, and the second is the one that explains a
    # fuel number that fell off a cliff.
    defended_by = {}
    faced_secs = {}
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
        secs = _interval_secs((e.get("payload") or {}).get("defenseIntervals"))
        faced = faced_secs.setdefault(target, {})
        faced[e["matchKey"]] = faced.get(e["matchKey"], 0.0) + secs

    # Which matches each robot actually played, off the schedule rather than off
    # our scouting. The `exact` block is TBA's, and TBA knows what every robot on
    # the field did whether or not one of our scouts was sitting on it - reading
    # it through our entries meant an opponent nobody watched had no climb, no
    # tower points and no record, on the tab where an opponent is the whole
    # question. One row per match by construction, so a HAND OVER mid-match
    # cannot double anything either.
    matches_by_team = {}
    for m in matches:
        if not m.get("breakdown"):
            continue
        for side in ("red", "blue"):
            for t in (m.get(side) or []):
                matches_by_team.setdefault(t, []).append((m, side))

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
                                  _lookup(lovat_rows, team),
                                  faced_secs.get(team) or {},
                                  matches_by_team.get(team) or [])
        # Kept beside the averages rather than folded into them: an average
        # says how good a robot is, a series says whether it is getting better,
        # and a picklist meeting the night before eliminations wants both.
        out[team]["trend"] = _team_trend(team, matches, entries_by_team.get(team, []),
                                         solved_by.get(team, []),
                                         _lookup(lovat_rows, team),
                                         faced_secs.get(team) or {})

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
                ivs = [iv for iv in rules.split_by_phase((e.get("payload") or {}).get("intervals"))
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


def _start(iv):
    """When an interval began, or None if it does not say a number."""
    try:
        v = float(iv["start"])
    except (KeyError, TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _interval_secs(intervals):
    return sum(rules.interval_secs(iv)
               for iv in (intervals or []))


def _team_trend(team, matches, entries, solved, lovat, faced_secs):
    """One row per match this robot played, in schedule order.

    Everything the dashboard draws a line through, joined on the match key and
    kept in its own source's terms: `fuel` is the solver, `officialFuel` is
    TBA's total for the whole alliance, `defenseSecs` is our scouts, and the
    `lovat*` fields are somebody else's scouts.  Four sources on one x axis is
    the point - the disagreements are where the interesting robots are - and
    mixing them into one averaged number would hide exactly that.

    A field nobody recorded is null and the chart leaves a gap. It is never
    zero: "no scout was watching" and "did nothing" look identical on a line
    and mean opposite things.
    """
    solved_by_key = {s["matchKey"]: s for s in solved}
    # A HAND OVER mid-match leaves two entries for one (match, team); the
    # first is the one that covers the start of the match.
    entry_by_key = {}
    for e in entries:
        entry_by_key.setdefault(e["matchKey"], e)
    lovat_by_key = {}
    for row in (lovat or {}).get("perMatch") or []:
        if row.get("matchKey"):
            lovat_by_key.setdefault(row["matchKey"], row)

    out = []
    for m in matches:
        mk = m["matchKey"]
        alliance = ("red" if team in (m.get("red") or [])
                    else "blue" if team in (m.get("blue") or []) else None)
        sv, e, lv = solved_by_key.get(mk), entry_by_key.get(mk), lovat_by_key.get(mk)
        known = sv or e or lv
        if not (alliance or known):
            continue                    # not this robot's match at all
        # A match still to be played carries no measurement from anybody, and a
        # run of empty points on the right of a chart is dead space that
        # squashes the part with data in it.
        if not (m.get("breakdown") or known):
            continue
        info = ((m.get("breakdown") or {}).get(alliance) or {}) if alliance else {}
        lineup = (m.get(alliance) or []) if alliance else []
        idx = lineup.index(team) if team in lineup else None
        climb = ((info.get("endgameTower") or [None, None, None])[idx]
                 if idx is not None and info else None)
        auto_climb = ((info.get("autoTower") or [None, None, None])[idx]
                      if idx is not None and info else None)
        p = (e.get("payload") or {}) if e else {}
        row = {
            "matchKey": mk,
            "label": m.get("label"),
            "alliance": alliance,
            "played": bool(m.get("breakdown")),
            # Whether one of our scouts was on this robot in this match. The
            # dashboard's "gone unscouted" alert had no way to ask that and
            # asked whether the team had EVER been scouted instead, so a
            # station going quiet mid-event was invisible to it.
            "scouted": bool(e),
            # estimated. A provisional row is not a measurement of this robot
            # (see solve.provisional_match), so it reads as no measurement here
            # too - the line breaks, which is what a gap already means on every
            # chart in this app: nobody was watching, not nothing happened.
            "fuel": sv["fuel"] if sv and not sv.get("provisional") else None,
            "band": sv["band"] if sv and not sv.get("provisional") else None,
            "provisional": bool(sv.get("provisional")) if sv else None,
            # exact - the whole alliance, which is what TBA publishes
            "officialFuel": (sum(v for v in (info.get("windows") or {}).values() if v)
                             if info.get("windows") else None),
            "climb": climb,
            "autoClimb": auto_climb,
            "towerPoints": (rules.tower_points(climb or "None", "teleop")
                            + rules.tower_points(auto_climb or "None", "auto")
                            if info else None),
            # observed
            "defenseSecs": round(_interval_secs(p.get("defenseIntervals")), 1) if e else None,
            "feedSecs": round(_interval_secs(p.get("feedIntervals")), 1) if e else None,
            "defenseFacedSecs": round(faced_secs[mk], 1) if mk in faced_secs else None,
            "died": bool(p.get("died")) if e else None,
            # The second our scout said this robot left to climb, beside the
            # second Lovat's scout timed it two rows down. Null where nobody
            # said - a climb with no time is not a climb at 0s.
            "climbStartSecs": p.get("climbStartSecs") if e else None,
            # lovat
            "lovatFuel": lv.get("fuel") if lv else None,
            "lovatDefenseSecs": lv.get("defenseSecs") if lv else None,
            "lovatClimbStartSecs": lv.get("climbStartSecs") if lv else None,
        }
        out.append(row)
    return out


def match_projection(teams, match, alliance):
    """What the numbers already on the hub add up to for one alliance.

    Sums, not a forecast: `projectedFuel` is each robot's solver average added
    together, `band` is how well we know those averages, `matchSpread` is how
    much a single match swings (independent robots, so the variances add), and
    the tower points are TBA's own per-robot averages.

    It exists so a generated match read can cite a projection rather than do
    arithmetic - the ground rules forbid the model computing new numbers, and a
    strategy call needs the alliance total, not three separate averages. The
    dashboard's own `project()` in desk.js is the same sum for the same reason;
    this one travels to the model.
    """
    lineup = (match.get(alliance) or []) if match else []
    fuel = band_sq = spread_sq = tower = 0.0
    scouted, unscouted = 0, []
    for t in lineup:
        rec = teams.get(t) or teams.get(str(t))
        if not rec or not rec.get("estimated", {}).get("matches"):
            unscouted.append(t)
            continue
        scouted += 1
        es, ex = rec["estimated"], rec.get("exact") or {}
        fuel += es.get("avgFuel") or 0
        band_sq += (es.get("band") or 0) ** 2
        spread_sq += (es.get("matchBand") or 0) ** 2
        tower += ex.get("avgTowerPoints") or 0
    fuel_pts = rules.RULES.get("fuelPoints", 1)
    return {
        "alliance": alliance,
        "lineup": lineup,
        "robotsScouted": scouted,
        "notScouted": unscouted,
        "projectedFuel": round(fuel, 1),
        "band": round(math.sqrt(band_sq), 1),
        "matchSpread": round(math.sqrt(spread_sq), 1),
        "projectedTowerPoints": round(tower, 1),
        "projectedPoints": round(fuel * fuel_pts + tower, 1),
    }


def defense_history(teams, match):
    """Who on each alliance has made a habit of defending whom on the other.

    Straight out of `observed.defenseAgainst`, restricted to pairs that are
    actually on the field together in this match. Our scouts logged every one
    of these against a named robot, so it is the one part of a match read that
    is a record rather than an inference.
    """
    out = []
    for side, other in (("red", "blue"), ("blue", "red")):
        for t in (match.get(side) or []):
            rec = teams.get(t) or teams.get(str(t))
            if not rec:
                continue
            for target, n in ((rec.get("observed") or {}).get("defenseAgainst") or {}).items():
                tgt = _int(target)
                if tgt in (match.get(other) or []):
                    out.append({"by": t, "byAlliance": side, "against": tgt, "matches": n,
                                "secsPerMatch": (rec.get("observed") or {}).get("defenseSecs")})
    return out


def _lookup(table, team):
    """kv-store tables round-trip through JSON, so integer keys come back as strings."""
    return table.get(team) or table.get(str(team)) or {}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _team_summary(team, meta, entries, solved, by_match, ranking=None, epa=None,
                  defended_by=None, lovat=None, faced_secs=None, official=None):
    ranking, epa = ranking or {}, epa or {}
    defended_by = defended_by or {}
    faced_secs = faced_secs or {}
    lovat = lovat or {}
    # (match, alliance) for every match this robot played that has an official
    # breakdown.  Off the schedule, not off our entries - see event_summary.
    official = official or []
    # ---------------------------------------------- EXACT (from TBA)
    climbs = {"Level1": 0, "Level2": 0, "Level3": 0, "None": 0}
    auto_climbs = 0
    tower_pts = []
    wins = losses = ties = 0
    rps = []
    official_matches = 0

    # One row per match this robot played, taken off the schedule.  The alliance
    # is whichever lineup the team is in, not whichever one a scout typed: the
    # schedule is the authority on that, and asking it removes a whole class of
    # scout-versus-schedule disagreement from a block that claims to be exact.
    for m, alliance in official:
        info = (m.get("breakdown") or {}).get(alliance)
        lineup = m.get(alliance) or []
        if not info or team not in lineup:
            continue
        official_matches += 1
        opp = "blue" if alliance == "red" else "red"
        ours = info.get("totalPoints")
        theirs = ((m.get("breakdown") or {}).get(opp) or {}).get("totalPoints")
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
    # The after-screen's second page.  Every one of these is a question other
    # teams' scouts have always answered and ours never asked, which is why the
    # LOVAT panel could say a robot crosses on the bump while ours had nothing
    # to say at all.  Tallies rather than rates where the KIND matters: "stuck
    # on the bump" is a route you can send a robot around, "stuck 40%" is not.
    accuracy = []
    start_lanes = {}
    traversals = {}
    beached = {}
    climb_spots = {}
    scores_moving = disrupts = climb_failed = auto_climb_failed = 0
    climb_start = []
    auto_climb_start = []

    for e in entries:
        p = e.get("payload") or {}
        m = by_match.get(e["matchKey"])
        auto_winner = ((m or {}).get("breakdown") or {}).get("autoWinner")
        alliance = e.get("alliance")
        ivs = rules.split_by_phase(p.get("intervals"))

        waste_s = act_s = 0.0
        for iv in ivs:
            dur = rules.interval_secs(iv)
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
            feed_secs.append(sum(rules.interval_secs(iv) for iv in fi))
        di = p.get("defenseIntervals") or []
        if di:
            defense_secs.append(sum(rules.interval_secs(iv) for iv in di))
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
        if p.get("startLane"):
            ln = str(p["startLane"])
            start_lanes[ln] = start_lanes.get(ln, 0) + 1
        for key, tally in (("traversal", traversals), ("beached", beached),
                           ("climbSpot", climb_spots)):
            if p.get(key):
                v = str(p[key])
                tally[v] = tally.get(v, 0) + 1
        if isinstance(p.get("accuracyRating"), (int, float)) and p["accuracyRating"]:
            accuracy.append(float(p["accuracyRating"]))
        scores_moving += 1 if p.get("scoresWhileMoving") else 0
        disrupts += 1 if p.get("disrupts") else 0
        # A robot cannot both climb and fall. The two are collected by a chip
        # and a button that hide each other on the phone, but a row logged
        # before that guard - or by any other writer - can hold both, and then
        # one team reads "best climb L2, 100% of matches" and "tried a climb
        # and fell, 100%" side by side. The recorded level is the stronger
        # signal: it says which level, and the chip only says that something
        # went wrong.
        if p.get("climbFailed") and (p.get("endgameTower") or "None") == "None":
            climb_failed += 1
        if p.get("autoClimbFailed") and (p.get("autoTower") or "None") == "None":
            auto_climb_failed += 1
        # Seconds into the match, and only where the scout's phone was actually
        # running a clock - the HUD leaves it null otherwise rather than
        # writing a zero that would read as "left at the buzzer".
        for key, into in (("climbStartSecs", climb_start),
                          ("autoClimbStartSecs", auto_climb_start)):
            v = p.get(key)
            if isinstance(v, (int, float)):
                into.append(float(v))
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
        # Nothing official read means UNKNOWN, and it has to read as unknown all
        # the way out. A rate of 0.0 and a bestClimb of "None" are measurements:
        # they say this robot was watched and did not climb. For a robot no
        # match has posted a result for yet, that is a claim made from nothing -
        # and the MATCH tab drew it as a flat `NONE` beside `0 ±0` on the one
        # screen that gets read out loud before a match.
        "exact": {
            "matchesWithOfficial": official_matches,
            "climbs": climbs if official_matches else {},
            "climbRate": ({k: (v / official_matches * 100.0) for k, v in climbs.items()}
                          if official_matches else {}),
            "autoClimbs": auto_climbs,
            "autoClimbRate": (round(auto_climbs / official_matches * 100.0, 1)
                              if official_matches else None),
            "avgTowerPoints": round(_mean(tower_pts), 1) if official_matches else None,
            "bestClimb": _best_climb(climbs) if official_matches else None,
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
            # The lane inside that side, asked after the buzzer. A side with no
            # lane is a robot nobody watched closely enough to say, not a robot
            # that started in neither.
            "startLanes": start_lanes,
            "startLane": (max(start_lanes, key=start_lanes.get) if start_lanes else None),
            # How well the shots went in, 1-5, as the scout saw it. Deliberately
            # not folded into the fuel estimate: the solver reconciles against
            # TBA's own totals, and letting an opinion move a reconciled number
            # is how an estimate stops being one.
            "accuracy": round(_mean(accuracy), 1) if accuracy else None,
            "accuracyMatches": len(accuracy),
            # Rates over the matches that ANSWERED, with the kinds kept beside
            # them - the same shape the lovat block uses for the same questions,
            # so the two can be read side by side without converting anything.
            "traversalKinds": traversals,
            "traversalRate": (round(sum(v for k, v in traversals.items() if k != "none")
                                    / sum(traversals.values()) * 100.0, 1)
                              if traversals else None),
            "beachedKinds": beached,
            "beachedRate": (round(sum(v for k, v in beached.items() if k != "neither")
                                  / sum(beached.values()) * 100.0, 1)
                            if beached else None),
            # These two are chips, not questions: not ticked reads as no, the
            # same as died and tipped beside them, so the rate is over every
            # match scouted rather than over the ones that answered.
            "scoresWhileMovingRate": round(scores_moving / n * 100.0, 1),
            "disruptRate": round(disrupts / n * 100.0, 1),
            # Tried and fell is not the same robot as never left the floor, and
            # until the after screen asked, both read as "no climb".
            "climbFailRate": round(climb_failed / n * 100.0, 1),
            "autoClimbFailRate": round(auto_climb_failed / n * 100.0, 1),
            "climbSpots": climb_spots,
            "climbSpot": (max(climb_spots, key=climb_spots.get) if climb_spots else None),
            # The second the scout said the climb began. Ours is a tap and
            # Lovat's is a timer, so they sit in different blocks - but they
            # answer the same question, and until now only Lovat could.
            "climbStartSecs": round(_mean(climb_start), 1) if climb_start else None,
            "climbsTimed": len(climb_start),
            "autoClimbStartSecs": (round(_mean(auto_climb_start), 1)
                                   if auto_climb_start else None),
            # {team: matches} in both directions.
            "defenseAgainst": defense_against,
            "defendedBy": defended_by,
            # Seconds, averaged over the matches somebody was on us. Absent
            # means nobody has bothered defending this robot yet, which is
            # itself worth knowing before you pick it.
            "defenseFacedSecs": (round(_mean(list(faced_secs.values())), 1)
                                 if faced_secs else None),
            "defenseFacedMatches": len(faced_secs),
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
            "autoClimbRate", "beachedRate", "scoresWhileMovingRate",
            "disruptRate", "traversalRate", "outpostIntakes",
            "climbStart", "climbStartSecs", "autoClimbStartSecs",
            "roles", "intakeTypes", "feederTypes",
            # Which kind, not just how often: "beached on the bump" is a route
            # you can send a robot around, "beached 40%" is not.
            "beachedKinds", "traversalKinds", "autoClimbResults",
            "climbsRead",
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
    """The highest level TBA recorded, or "None" for a robot that did not climb.

    Only ever called where at least one official result was read, because
    "None" here is the fact that it stayed on the floor - which is a different
    answer from having nothing to go on, and the caller is the one that knows
    which of the two it is holding.
    """
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
                    and _start(iv) is not None
                    and _start(iv) - nxt["start"] <= 6.0
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
        ivs = rules.split_by_phase((e.get("payload") or {}).get("intervals"))
        if not info:
            continue
        official = sum((info.get("windows") or {}).values())
        if official > 20 and not ivs:
            rec["empty"] += 1
            continue
        for pid, total in (info.get("windows") or {}).items():
            secs = sum(rules.interval_secs(iv)
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
    """How many of the robots that have taken the field somebody watched.

    Only matches that have actually happened. The whole schedule used to be in
    the denominator, so a crew that had not missed a single robot read 65% on
    the seeded demo (26 of 40 played) and would read about 11% on the Saturday
    morning of a 70-match regional. This tile sits beside MEDIAN ERROR and
    CALIBRATED under the heading "how much to trust the numbers", and the
    mirror puts it in its header line; a number that cannot reach 100% until
    the last match of the event is not that.

    Played means TBA has posted it, or somebody scouted it. The second half
    matters at a venue: TBA lags the buzzer by minutes, and a match nobody
    watched at all is exactly the one this must not quietly drop.
    """
    scouted = {}
    for e in entries:
        scouted.setdefault(e["matchKey"], set()).add(e["team"])
    total = expected = 0
    for m in matches:
        lineup = (m.get("red") or []) + (m.get("blue") or [])
        if not lineup:
            continue
        seen = scouted.get(m["matchKey"], set()) & set(lineup)
        if not (m.get("breakdown") or seen):
            continue                    # not played yet: nobody has missed anything
        expected += len(lineup)
        total += len(seen)
    return {"robotsScouted": total, "robotsExpected": expected,
            "pct": round(total / expected * 100.0, 1) if expected else 0.0}
