"""Turn Lovat's scout-report export into one record per team.

Lovat (https://lovat.app, FRC 8033) hands back the whole tournament as CSV, one
row per team per match.  Three things about that file are worth knowing before
reading the code, all of them decided by Lovat's exporter rather than by us:

  * booleans are the strings "TRUE" / "FALSE";
  * list columns are pipe-joined ("Scorer|Feeder");
  * commas inside free text were replaced with semicolons before export, which
    is not reversible - a note reads with semicolons where the scout typed
    commas, and that is the best that can be done.

The `match` column is `matchType[0] + matchNumber` ("Q42"), NOT a TBA match key,
so it has to be mapped onto ours.  Qualification matches map cleanly; anything
else keeps its raw label and is reported in `unmatched` rather than being
dropped or, worse, mis-joined onto a qual match of the same number.

Every field is null-safe: a column Lovat did not fill reads as unknown, never as
zero.  This module never raises - a malformed export returns None, the same
"we do not know" every source in sources.py returns.
"""
import csv
import io
import statistics as st

#: Column -> the name it takes in the per-team record.  Lovat's own names are
#: kept rather than bent to match our `observed` block: these are somebody
#: else's measurements and reading them as ours is exactly the mistake the
#: four-block trust model exists to prevent.
NUMERIC = {
    "totalPoints": "totalPoints",
    "autoPoints": "autoPoints",
    "teleopPoints": "teleopPoints",
    "driverAbility": "driver",
    "fuelPerSecond": "fuelPerSec",
    "accuracy": "accuracy",
    "volleysPerMatch": "volleys",
    "totalFuelOutputted": "avgFuel",
    "totalBallThroughput": "throughput",
    "totalBallsFed": "ballsFed",
    "timeFeeding": "feedSecs",
    "feedingRate": "feedingRate",
    "feedsPerMatch": "feedsPerMatch",
    "totalDefenseTime": "defenseSecs",
    "contactDefenseTime": "contactDefenseSecs",
    "campingDefenseTime": "campingDefenseSecs",
    "defenseEffectiveness": "defenseEffectiveness",
    "outpostIntakes": "outpostIntakes",
}

#: Booleans we count as a rate over the matches that answered.
FLAGS = {"autoClimb": "autoClimbRate", "beached": "beachedRate",
         "scoresWhileMoving": "scoresWhileMovingRate", "disrupts": "disruptRate"}

#: Pipe-joined list columns, tallied.
LISTS = {"robotRoles": "roles", "feederTypes": "feederTypes", "intakeType": "intakeTypes"}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN reads as unknown


def _bool(v):
    s = (v or "").strip().upper()
    if s in ("TRUE", "T", "YES", "1"):
        return True
    if s in ("FALSE", "F", "NO", "0"):
        return False
    return None


def _mean(xs, places=1):
    return round(st.mean(xs), places) if xs else None


def _climb_level(v):
    """Normalise Lovat's climb label onto the Level1/2/3 vocabulary we use.

    Lovat has written this as "L2", "Level 2" and "2" at different times, so
    match on the digit rather than the spelling.  A label we cannot read is
    unknown, not "None" - claiming a robot did not climb because we could not
    parse a word is the kind of quiet wrong answer that loses an alliance.
    """
    s = (v or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in ("none", "no", "nothing", "n/a"):
        return "None"
    for d in ("3", "2", "1"):
        if d in s:
            return "Level" + d
    return None


def match_key(label, event_key):
    """"Q42" -> "<event>_qm42".  Returns None for anything else."""
    s = (label or "").strip()
    if not (s and event_key):
        return None
    kind, digits = s[0].upper(), s[1:].strip()
    if kind == "Q" and digits.isdigit():
        return "%s_qm%d" % (event_key, int(digits))
    return None


def parse_report_csv(text, event_key):
    """CSV text -> {team: record}.  None if the export is unreadable."""
    if not text or not text.strip():
        return None
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        return None
    if not rows:
        return {}

    by_team = {}
    for row in rows:
        team = _num(row.get("teamNumber"))
        if team is None:
            continue
        by_team.setdefault(int(team), []).append(row)
    return {team: _team_record(team, rs, event_key) for team, rs in by_team.items()}


def _team_record(team, rows, event_key):
    labels = []
    unmatched = []
    for r in rows:
        label = (r.get("match") or "").strip()
        mk = match_key(label, event_key)
        labels.append((label, mk))
        if label and not mk:
            unmatched.append(label)

    rec = {"team": team, "matches": len(rows)}

    for col, name in NUMERIC.items():
        rec[name] = _mean([v for v in (_num(r.get(col)) for r in rows) if v is not None])

    for col, name in FLAGS.items():
        seen = [b for b in (_bool(r.get(col)) for r in rows) if b is not None]
        rec[name] = round(sum(seen) / len(seen) * 100.0, 1) if seen else None

    for col, name in LISTS.items():
        tally = {}
        for r in rows:
            for item in (r.get(col) or "").split("|"):
                item = item.strip()
                if item:
                    tally[item] = tally.get(item, 0) + 1
        rec[name] = tally

    climbs = {}
    read = 0
    for r in rows:
        lvl = _climb_level(r.get("endgameClimb"))
        if lvl is None:
            continue
        read += 1
        climbs[lvl] = climbs.get(lvl, 0) + 1
    rec["climbs"] = climbs
    rec["climbsRead"] = read
    rec["bestClimb"] = next((l for l in ("Level3", "Level2", "Level1") if climbs.get(l)),
                            "None" if read else None)
    rec["climbRate"] = ({k: round(v / read * 100.0, 1) for k, v in climbs.items()}
                        if read else {})

    notes = []
    scouters = set()
    for r, (label, mk) in zip(rows, labels):
        who = (r.get("scouter") or "").strip()
        if who:
            scouters.add(who)
        note = (r.get("notes") or "").strip()
        if note:
            notes.append({"match": label or None, "matchKey": mk,
                          "scouter": who or None, "note": note})
    rec["notes"] = notes
    rec["scouters"] = len(scouters)
    # Kept so the dashboard can say "42 rows, 3 of them playoff labels we could
    # not place" instead of silently showing a smaller number than Lovat has.
    rec["unmatched"] = sorted(set(unmatched))
    return rec
