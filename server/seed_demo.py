"""Generate a realistic fake event so the UI can be exercised without a live comp.

    python3 server/seed_demo.py [--db data/test.db] [--event 2026demo]
"""
import argparse
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/test.db")
    ap.add_argument("--event", default="2026demo")
    ap.add_argument("--teams", type=int, default=31)   # 30 demo teams + ours
    ap.add_argument("--matches", type=int, default=40)
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
    st.put_teams(ek, [{
        "team": n,
        "name": f"Off-Season Demo Team {n}" if DEMO_FIRST <= n <= DEMO_LAST else f"Team {n}",
    } for n in numbers])

    def bucket(r):
        return "trickle" if r < 2.2 else ("steady" if r < 6.5 else "dumping")

    played = 0
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
                    towers.append(p["climb"] if rng.random() < 0.8 else "None")
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

        st.put_match(ek, mk, label=label, comp_level="qm", match_number=mi, play_order=mi,
                     red=red, blue=blue,
                     status="On field" if mi == played + 1 else ("Now queuing" if mi == played + 2 else None),
                     breakdown=breakdown)

        if not is_played:
            continue
        for alliance, lineup in (("red", red), ("blue", blue)):
            for idx, t in enumerate(lineup):
                p = profiles[t]
                ivs = intervals[t]
                if p["stock"]:
                    ivs = [iv for iv in ivs
                           if rules.hub_active(iv["phase"], alliance, auto_winner) is not False]
                st.upsert_scout({
                    "eventKey": ek, "matchKey": mk, "team": t,
                    "scoutId": SCOUTS[(idx + (0 if alliance == "red" else 3)) % len(SCOUTS)],
                    "deviceId": "seed", "alliance": alliance, "station": idx + 1,
                    "updatedAt": time.time(),
                    "payload": {
                        "intervals": ivs,
                        "feedIntervals": [],
                        "preload": rng.randint(0, 8),
                        "autoTower": "None",
                        "endgameTower": (breakdown[alliance]["endgameTower"][idx] if breakdown else "None"),
                        "driverRating": rng.randint(2, 5),
                        "defenseRating": rng.randint(0, 4),
                        "died": rng.random() > p["reliab"],
                        "tipped": rng.random() < 0.03,
                        "noShow": False,
                        "note": rng.choice(NOTES) if rng.random() < 0.18 else "",
                    },
                })

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
    sz = pit_map.get("size", {})
    print(f"  pit map {sz.get('x')}x{sz.get('y')} from the real Nexus example "
          f"({len(pit_map.get('pits', {}))} pits, {len(addrs)} assigned, "
          f"{len(pit_map.get('walls', {}))} walls, {len(pit_map.get('arrows', {}))} arrows)")
    print("now run the solver over it via the server, or restart the server to pick it up")


if __name__ == "__main__":
    main()
