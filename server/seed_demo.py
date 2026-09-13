"""Generate a realistic fake event so the UI can be exercised without a live comp.

    python3 server/seed_demo.py [--db data/test.db] [--event 2026demo]
"""
import argparse
import csv
import io
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import lovat as lovat_report
import rules
from store import Store

OUR_TEAM = 6059
DEMO_FIRST, DEMO_LAST = 9970, 9999      # FIRST Off-Season Demo Teams
ARCH = {"drum": (9.0, 14.0), "steady": (2.5, 4.5), "trickle": (0.6, 1.6)}
SCOUTS = ["AK", "BR", "CJ", "DM", "EL", "FT"]

# The one free-text channel a scout has. Kept short and specific, the way a
# real one is typed with a thumb between matches.
NOTES = [
    "shot from the far side all match, never crossed",
    "intake jammed twice, driver cleared it both times",
    "held fuel through the dead shift then dumped it",
    "played defence on 9982 for most of shift 3",
    "climbed late, nearly missed it",
    "very fast cycles, best driver we have seen today",
    "fed their partner instead of shooting",
    "brownout after the climb",
]


#: Lovat's export columns, in the order its exporter writes them. The demo
#: builds this file and hands it to the same parser a real key feeds, so a
#: column that gets renamed upstream breaks here as loudly as it would there.
LOVAT_COLUMNS = [
    "match", "teamNumber", "totalPoints", "teleopPoints", "autoPoints", "driverAbility",
    "fuelPerSecond", "accuracy", "volleysPerMatch", "l1StartTime", "l2StartTime",
    "l3StartTime", "autoClimbStartTime", "contactDefenseTime", "defenseEffectiveness",
    "campingDefenseTime", "totalDefenseTime", "timeFeeding", "feedingRate", "feedsPerMatch",
    "totalFuelOutputted", "totalBallThroughput", "totalBallsFed", "outpostIntakes",
    "robotRoles", "fieldTraversal", "endgameClimb", "beached", "scoresWhileMoving",
    "disrupts", "autoClimb", "feederTypes", "intakeType", "scouter", "notes",
]

LOVAT_SCOUTERS = ["8033-ana", "8033-ben", "1114-cy", "254-dee"]


def _extras(rng, endgame_tower):
    """The optional after-the-buzzer answers, filled in the way a crew does it.

    Not every question every match: a lead who sees 100% coverage in the demo
    builds a dashboard that has never been shown a blank, and a blank is the
    normal case for a page a scout can skip.
    """
    out = {}
    if rng.random() < 0.55:
        out["startLane"] = rng.choice(["trench", "bump"])
    if rng.random() < 0.5:
        out["traversal"] = rng.choices(["trench", "bump", "both", "none"],
                                       weights=[4, 3, 1, 2])[0]
    if rng.random() < 0.5:
        out["beached"] = rng.choices(["neither", "fuel", "bump", "both"],
                                     weights=[8, 1, 2, 1])[0]
    if rng.random() < 0.45:
        out["accuracyRating"] = rng.randint(2, 5)
    out["scoresWhileMoving"] = rng.random() < 0.35
    out["disrupts"] = rng.random() < 0.12
    if endgame_tower and endgame_tower != "None":
        if rng.random() < 0.5:
            out["climbSpot"] = rng.choice(["frontSide", "frontMiddle", "backSide", "backMiddle"])
        if rng.random() < 0.7:
            # Seconds into a 160s match. Higher levels take longer to set up,
            # so a robot going for L3 leaves earlier.
            base = {"Level1": 143.0, "Level2": 134.0, "Level3": 126.0}[endgame_tower]
            out["climbStartSecs"] = round(rng.uniform(base - 6, base + 6), 1)
    else:
        out["climbFailed"] = rng.random() < 0.12
    return out


def _climb_attempt(rng, best):
    """What a robot managed in ONE match, which is not what it is capable of.

    A robot with an L3 in it does not get an L3 every time - it runs out of
    endgame, or settles for the level it can still reach. A demo where every
    climb is either the robot's best or nothing gives every team a single
    level, so `climbRate` per level and the per-level climb timing Lovat
    supplies have nothing to draw and nobody notices when they break.
    """
    if best == "None" or rng.random() < 0.2:
        return "None"
    level = int(best[-1])
    if level > 1 and rng.random() < 0.25:
        level -= 1
    return f"Level{level}"


def _lovat_row(rng, team, match_no, profile, fuel, defends, breakdown, alliance, idx):
    """One row of somebody else's scouting for one robot in one match.

    Their number for the same robot, not ours: a scout counting by eye lands
    within about a fifth of the truth, which is exactly the spread that makes
    the two sources worth showing side by side rather than averaging.
    """
    seen = round(fuel * rng.uniform(0.8, 1.2), 1) if fuel else None
    climb = breakdown[alliance]["endgameTower"][idx] if breakdown else "None"
    # Lovat times the climb, in one column per level and blank in the others.
    starts = {"l1StartTime": "", "l2StartTime": "", "l3StartTime": ""}
    if climb and climb.startswith("Level"):
        starts[f"l{climb[-1]}StartTime"] = round(rng.uniform(108, 142), 1)
    defence = round(rng.uniform(6, 25), 1) if defends else (
        round(rng.uniform(3, 12), 1) if rng.random() < 0.08 else 0)
    # Lovat separates pushing from parking, and our own scouting has no word
    # for the second. A seed that always writes zero camping means the row on
    # the team page that splits them can never appear.
    camping = round(defence * rng.uniform(0.2, 0.6), 1) if defence and rng.random() < 0.3 else 0
    roles = ["Scorer"] + (["Defender"] if defends else []) + (
        ["Feeder"] if rng.random() < 0.2 else [])
    return {
        "match": f"Q{match_no}",
        "teamNumber": team,
        # A column their scout left blank has to survive as blank all the way
        # to the dashboard, so some of these are deliberately empty.
        "totalPoints": round(rng.uniform(40, 180)) if rng.random() < 0.9 else "",
        "teleopPoints": round(rng.uniform(30, 140)),
        "autoPoints": round(rng.uniform(0, 40)),
        "driverAbility": rng.randint(1, 5),
        "fuelPerSecond": round(profile["rate"] * rng.uniform(0.7, 1.3), 2),
        "accuracy": round(rng.uniform(0.5, 0.95), 2) if rng.random() < 0.85 else "",
        "volleysPerMatch": rng.randint(2, 9),
        **starts,
        "autoClimbStartTime": round(rng.uniform(12, 15), 1) if rng.random() < 0.05 else "",
        "contactDefenseTime": round(defence - camping, 1),
        "defenseEffectiveness": rng.randint(0, 5) if defence else 0,
        "campingDefenseTime": camping,
        "totalDefenseTime": defence,
        "timeFeeding": round(rng.uniform(0, 20), 1),
        "feedingRate": round(rng.uniform(0, 1.4), 2),
        "feedsPerMatch": rng.randint(0, 3),
        "totalFuelOutputted": seen if seen is not None else "",
        "totalBallThroughput": round(seen * rng.uniform(1.0, 1.2), 1) if seen else "",
        "totalBallsFed": rng.randint(0, 14),
        "outpostIntakes": rng.randint(0, 4) if rng.random() < 0.7 else "",
        "robotRoles": "|".join(roles),
        # Three of these are enums in Lovat's schema and only two are booleans.
        # A seed that writes TRUE/FALSE for all five is a demo that cannot show
        # the KIND beside the rate - "beached on the bump" is a route you can
        # send a robot around, "beached 5%" is not - and it is also a demo that
        # cannot notice the enum reader breaking, which is how `e36147a` lived.
        "fieldTraversal": (rng.choice(["TRENCH", "BUMP", "BOTH"])
                           if rng.random() < 0.7 else "NONE"),
        "endgameClimb": climb or "None",
        "beached": (rng.choice(["ON_FUEL", "ON_BUMP", "BOTH"])
                    if rng.random() < 0.05 else "NEITHER"),
        "scoresWhileMoving": "TRUE" if rng.random() < 0.4 else "FALSE",
        "disrupts": "TRUE" if rng.random() < 0.12 else "FALSE",
        # A robot that never tries an auto climb and one that tries and falls
        # are different robots, and NOT_ATTEMPTED is how Lovat says the first.
        "autoClimb": ("SUCCEEDED" if rng.random() < 0.04
                      else "FAILED" if rng.random() < 0.08 else "NOT_ATTEMPTED"),
        "feederTypes": "Human" if rng.random() < 0.5 else "",
        "intakeType": rng.choice(["Ground", "Chute", "Both"]),
        "scouter": rng.choice(LOVAT_SCOUTERS),
        # Their notes, in their words. Lovat's exporter replaces commas with
        # semicolons on the way out, and that is not reversible.
        "notes": rng.choice(NOTES).replace(",", ";") if rng.random() < 0.15 else "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/test.db")
    ap.add_argument("--event", default="2026demo")
    ap.add_argument("--teams", type=int, default=31)   # 30 demo teams + ours
    ap.add_argument("--matches", type=int, default=40)
    ap.add_argument("--via-nexus", action="store_true",
                    help="push the schedule through the Nexus ingest path instead of writing "
                         "TBA-shaped rows directly, which is what a real event does")
    args = ap.parse_args()

    rng = random.Random(2026)
    st = Store(args.db)
    ek = args.event
    st.set("eventKey", ek)
    st.set("ourTeam", OUR_TEAM)
    st.put_event(ek, name="Demo Regional", level="regional")

    # FIRST's real Off-Season Demo Teams (9970-9999, Manchester NH), plus our
    # team. Using the official demo numbers means nothing here can be mistaken
    # for a real team's scouting record.
    demo_numbers = list(range(DEMO_FIRST, DEMO_LAST + 1))
    numbers = sorted(set(demo_numbers[:max(0, args.teams - 1)] + [OUR_TEAM]))
    profiles = {}
    for n in numbers:
        a = rng.choices(["drum", "steady", "trickle"], weights=[0.3, 0.45, 0.25])[0]
        lo, hi = ARCH[a]
        profiles[n] = {
            "rate": rng.uniform(lo, hi),
            "duty": rng.uniform(0.25, 0.7),
            "climb": rng.choices(["Level3", "Level2", "Level1", "None"], weights=[.3, .3, .25, .15])[0],
            "reliab": rng.uniform(0.85, 1.0),
            "stock": rng.random() < 0.35,
        }
    # A robot starts where its auto is written for, so give each team a
    # preference rather than rolling uniformly every match.
    STARTS = {n: rng.choice([(6, 2, 1), (1, 6, 2), (1, 2, 6), (3, 3, 3)]) for n in numbers}

    st.put_teams(ek, [{
        "team": n,
        "name": f"Off-Season Demo Team {n}" if DEMO_FIRST <= n <= DEMO_LAST else f"Team {n}",
    } for n in numbers])

    def bucket(r):
        return "trickle" if r < 2.2 else ("steady" if r < 6.5 else "dumping")

    # Lovat only has robots somebody else bothered to scout, so it covers most
    # of the field and not all of it. A team missing from Lovat is a blank
    # column on the dashboard, not a zero, and the demo should show that.
    lovat_teams = set(rng.sample(numbers, int(len(numbers) * 0.75)))
    lovat_rows = []

    played = 0
    nexus_matches = []
    for mi in range(1, args.matches + 1):
        picks = rng.sample(numbers, 6)
        red, blue = picks[:3], picks[3:]
        mk = f"{ek}_qm{mi}"
        label = f"Qualification {mi}"
        is_played = mi <= int(args.matches * 0.65)

        # simulate the match
        auto_fuel = {"red": 0, "blue": 0}
        intervals = {}
        for alliance, lineup in (("red", red), ("blue", blue)):
            for t in lineup:
                intervals[t] = []
        # decide auto winner first so hub_active is well defined
        for alliance, lineup in (("red", red), ("blue", blue)):
            for t in lineup:
                p = profiles[t]
                if rng.random() > p["reliab"]:
                    continue
                auto_fuel[alliance] += int(p["rate"] * rng.uniform(1, 3))
        auto_winner = "red" if auto_fuel["red"] > auto_fuel["blue"] else (
            "blue" if auto_fuel["blue"] > auto_fuel["red"] else None)

        windows = {"red": {}, "blue": {}}
        robot_fuel = {}
        for alliance, lineup in (("red", red), ("blue", blue)):
            for ph in rules.PHASES:
                pid = ph["id"]
                if rules.hub_active(pid, alliance, auto_winner) is not True:
                    continue
                total = 0
                for t in lineup:
                    p = profiles[t]
                    if rng.random() > p["reliab"]:
                        continue
                    budget = (ph["end"] - ph["start"]) * p["duty"]
                    clock = float(ph["start"])
                    for _ in range(rng.randint(0, 3)):
                        if budget <= 0.4:
                            break
                        d = min(budget, rng.uniform(1.0, 6.0))
                        budget -= d
                        r = max(0.2, p["rate"] * rng.uniform(0.8, 1.2))
                        total += d * r
                        robot_fuel[t] = robot_fuel.get(t, 0.0) + d * r
                        od = max(0.3, d + rng.gauss(0, 0.4))
                        b = bucket(r)
                        if rng.random() < 0.15:
                            k = rules.BUCKETS.index(b) + rng.choice((-1, 1))
                            b = rules.BUCKETS[max(0, min(len(rules.BUCKETS) - 1, k))]
                        intervals[t].append(
                            {"start": round(clock, 2), "end": round(clock + od, 2),
                             "phase": pid, "intensity": b})
                        clock += d
                windows[alliance][pid] = int(round(total))

        breakdown = None
        if is_played:
            played += 1
            breakdown = {"autoWinner": auto_winner}
            for alliance, lineup in (("red", red), ("blue", blue)):
                towers = []
                for t in lineup:
                    p = profiles[t]
                    towers.append(_climb_attempt(rng, p["climb"]))
                breakdown[alliance] = {
                    "windows": windows[alliance],
                    "autoTower": ["None"] * 3,
                    "endgameTower": towers,
                    "totalPoints": sum(windows[alliance].values()) + sum(
                        rules.tower_points(x, "teleop") for x in towers),
                    "totalTowerPoints": sum(rules.tower_points(x, "teleop") for x in towers),
                    "rp": rng.randint(0, 6),
                    "energized": sum(windows[alliance].values()) >= 100,
                    "supercharged": sum(windows[alliance].values()) >= 360,
                    "traversal": sum(rules.tower_points(x, "teleop") for x in towers) >= 50,
                    "fouls": {"minor": rng.randint(0, 2), "major": 0},
                }

        status = ("On field" if mi == played + 1
                  else "Now queuing" if mi == played + 2 else None)
        if args.via_nexus:
            # What a real event actually does: Nexus delivers the schedule under
            # its own labels, TBA delivers results under its own keys, and the
            # two have to land on ONE row. Writing only TBA-shaped rows is what
            # hid a bug where they did not, and every fuel number at a live
            # event was an even three-way split with no scouting in it.
            nexus_matches.append({
                "label": label, "status": status,
                "redTeams": [str(t) for t in red], "blueTeams": [str(t) for t in blue],
                "times": {"estimatedOnFieldTime": (time.time() + mi * 420) * 1000},
            })
            if breakdown:
                st.put_match(ek, f"{ek}_qm{mi}", comp_level="qm", match_number=mi,
                             times={"actual": time.time() - (played - mi + 1) * 420},
                             red=red, blue=blue, breakdown=breakdown)
        else:
            st.put_match(ek, mk, label=label, comp_level="qm", match_number=mi, play_order=mi,
                         red=red, blue=blue, status=status, breakdown=breakdown)

        if not is_played:
            continue
        for alliance, lineup in (("red", red), ("blue", blue)):
            for idx, t in enumerate(lineup):
                p = profiles[t]
                ivs = intervals[t]
                if p["stock"]:
                    ivs = [iv for iv in ivs
                           if rules.hub_active(iv["phase"], alliance, auto_winner) is not False]
                # A robot that plays defence does it to somebody; the scout is
                # asked who after the buzzer.
                defends = rng.random() < 0.18
                opponent = blue if alliance == "red" else red
                st.upsert_scout({
                    "eventKey": ek, "matchKey": mk, "team": t,
                    "scoutId": SCOUTS[(idx + (0 if alliance == "red" else 3)) % len(SCOUTS)],
                    "deviceId": "seed", "alliance": alliance, "station": idx + 1,
                    "updatedAt": time.time(),
                    "payload": {
                        "intervals": ivs,
                        "feedIntervals": [],
                        "defenseIntervals": ([{"start": 60.0, "end": 60.0 + rng.uniform(6, 25),
                                               "phase": "shift2", "intensity": "steady"}]
                                             if defends else []),
                        "defenseTarget": rng.choice(opponent) if defends else None,
                        # A real scout leaves some of these blank, and the
                        # dashboard has to read that as "nobody said" rather
                        # than as a zero.
                        "preload": rng.randint(0, 8) if rng.random() < 0.8 else None,
                        "startPosition": (rng.choices(["left", "centre", "right"],
                                                      weights=STARTS[t])[0]
                                          if rng.random() < 0.85 else None),
                        "autoFailed": rng.random() < 0.07,
                        "fouls": rng.random() < 0.1,
                        "autoTower": "None",
                        "endgameTower": (breakdown[alliance]["endgameTower"][idx] if breakdown else "None"),
                        "driverRating": rng.randint(2, 5),
                        "defenseRating": rng.randint(0, 4),
                        "died": rng.random() > p["reliab"],
                        "tipped": rng.random() < 0.03,
                        "noShow": False,
                        "note": rng.choice(NOTES) if rng.random() < 0.18 else "",
                        # The after screen's second page. Answered about half
                        # the time on purpose: it is optional, a real event
                        # leaves plenty of it blank, and every panel that reads
                        # it has to say "not asked" rather than draw a zero.
                        **_extras(rng, breakdown[alliance]["endgameTower"][idx]
                                  if breakdown else "None"),
                    },
                })

                # ---- what another team's scouts, uploading to Lovat, saw.
                # Deliberately NOT a copy of our row: a different pair of eyes
                # on the same robot, missing some matches entirely and some
                # columns within a match. That is what makes the "where the
                # sources disagree" chart show anything, and what keeps the
                # null-safety honest - a blank must read as unknown, never zero.
                if t in lovat_teams and rng.random() < 0.85:
                    lovat_rows.append(_lovat_row(rng, t, mi, p, robot_fuel.get(t, 0.0),
                                                 defends, breakdown, alliance, idx))

    if args.via_nexus:
        import hub as hub_mod
        h = hub_mod.Hub(st)
        h.apply_nexus_event({"eventKey": ek, "dataAsOfTime": time.time(),
                             "matches": nexus_matches})
        print(f"  schedule ingested through the Nexus path ({len(nexus_matches)} matches)")

    # ---- lovat, through the real importer rather than as a hand-built dict, so
    # the demo exercises the CSV parsing that a real event depends on.
    if lovat_rows:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=LOVAT_COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(lovat_rows)
        # With the BOM, because the real export has one: the demo is the only
        # place this whole path runs without a key, and a seed that skips the
        # BOM is a seed that cannot catch it nulling every match key again.
        parsed = lovat_report.parse_report_csv("\ufeff" + buf.getvalue(), ek) or {}
        st.set(f"lovat:{ek}", {str(t): rec for t, rec in parsed.items()})

    # ---- pit map: the real example response from frc.nexus/api/v1/docs, with the
    # fixture's placeholder team numbers remapped onto this event's teams.
    fixture = os.path.join(_HERE, "fixtures", "nexus_pitmap_example.json")
    with open(fixture, "r", encoding="utf-8") as fh:
        pit_map = json.load(fh)
    pit_map.pop("_source", None)

    slots = sorted(pit_map.get("pits", {}).items(),
                   key=lambda kv: (kv[1]["position"]["y"], kv[1]["position"]["x"]))
    addrs, mapping = {}, {}
    used = set()
    for i, (addr, pit) in enumerate(slots):
        if i < len(numbers):
            n = numbers[i]
            pit["team"] = str(n)
            addrs[str(n)] = addr
            used.add(addr)
            mapping[str(n)] = rng.choices(
                ["complete", "queued", "reinspection", "hold", "not-started"],
                weights=[.55, .12, .08, .05, .20])[0]
        else:
            pit["team"] = None          # a real venue has empty pits too
    st.set(f"pitMap:{ek}", pit_map)

    st.set(f"pits:{ek}", addrs)
    st.set(f"inspection:{ek}", mapping)

    # a few pits already scouted, so the map has all three states on screen
    for n in numbers[:int(len(numbers) * 0.35)]:
        st.upsert_pit({"eventKey": ek, "team": n, "scoutId": "PIT", "deviceId": "seed",
                       "updatedAt": time.time(), "payload": {
                           "drivetrain": rng.choice(["swerve", "tank", "mecanum"]),
                           "shooter": rng.choice(["drum", "flywheel", "dump"]),
                           "maxClimb": rng.choice(["L3", "L2", "L1", "none"]),
                           "stockpile": rng.choice(["yes", "some", "no"]),
                           "groundPickup": rng.choice(["yes", "no"]),
                           "autos": "2 — centre 8 fuel, wall 5",
                           "weight": str(rng.randint(95, 125)),
                           "notes": "", "photos": []}})

    # ---- exact side-tables the hub normally polls for.
    # Seeded here so the demo event exercises the rankings and EPA columns with
    # no API keys and no internet, which is the bar the README sets.
    order = sorted(numbers, key=lambda n: -(profiles[n]["rate"] * profiles[n]["duty"]))
    st.set(f"rankings:{ek}", {
        str(n): {
            "rank": i + 1,
            "rankingPoints": round(rng.uniform(1.4, 3.6), 2),
            "wins": w, "losses": max(0, played // 4 - w), "ties": 0,
            "played": played // 4,
            "opr": round(profiles[n]["rate"] * profiles[n]["duty"] * 4.0 + rng.uniform(-3, 3), 1),
        }
        for i, n in enumerate(order)
        for w in [rng.randint(0, max(0, played // 4))]
    })
    st.set(f"epa:{ek}", {
        str(n): {
            # correlated with the true rate, not equal to it - the whole point
            # of EPA on this screen is that it is an independent read
            "epa": round(max(0.8, profiles[n]["rate"] * profiles[n]["duty"] * 5.5 + rng.uniform(-6, 6)), 1),
            "auto": round(rng.uniform(4, 18), 1),
            "teleop": round(max(0.4, profiles[n]["rate"] * profiles[n]["duty"] * 3.4 + rng.uniform(-4, 4)), 1),
            "endgame": round(rng.uniform(3, 16), 1),
            "rank": rng.randint(80, 3400),
        } for n in numbers
    })

    print(f"seeded {ek}: {len(numbers)} teams, {args.matches} matches ({played} played)")
    print(f"  rankings + statbotics epa seeded for {len(numbers)} teams (no keys needed)")
    lv = st.get(f"lovat:{ek}") or {}
    print(f"  lovat: {len(lovat_rows)} rows for {len(lv)} of {len(numbers)} teams "
          f"(other teams' scouting, built as CSV and read back through the importer)")
    sz = pit_map.get("size", {})
    print(f"  pit map {sz.get('x')}x{sz.get('y')} from the real Nexus example "
          f"({len(pit_map.get('pits', {}))} pits, {len(addrs)} assigned, "
          f"{len(pit_map.get('walls', {}))} walls, {len(pit_map.get('arrows', {}))} arrows)")
    print("now run the solver over it via the server, or restart the server to pick it up")


if __name__ == "__main__":
    main()
