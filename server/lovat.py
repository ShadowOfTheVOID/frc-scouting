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

Every column in the export is kept, and every one of them is null-safe: a
column Lovat did not fill reads as unknown, never as zero.  Most are averaged
into the per-team record, and the ones a chart needs are kept per row as well,
in `perMatch` - an average cannot show that a robot's fuel collapsed after
Q30, and that is the shape worth walking to the pits about.

This module never raises - a malformed export returns None, the same "we do
not know" every source in sources.py returns.
"""
import csv
import io
import math
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

#: Rates over the matches that answered.  Only two of these are booleans in
#: Lovat's schema; the other three are enums, and reading them with _bool gave
#: None for every row, so autoClimbRate, beachedRate and traversalRate were
#: permanently blank on the dashboard and nothing said why.
FLAGS = {"autoClimb": "autoClimbRate", "beached": "beachedRate",
         "scoresWhileMoving": "scoresWhileMovingRate", "disrupts": "disruptRate",
         "fieldTraversal": "traversalRate"}

#: column -> (values that mean yes, values that mean no).  Anything outside both
#: sets stays unknown rather than being guessed at - the same rule the rest of
#: this file follows.  TRUE/FALSE is still accepted first, because Lovat has
#: written these columns both ways and an older export must keep working.
ENUM_FLAGS = {
    "autoClimb": ({"SUCCEEDED"}, {"NOT_ATTEMPTED", "FAILED"}),
    "beached": ({"ON_FUEL", "ON_BUMP", "BOTH"}, {"NEITHER"}),
    "fieldTraversal": ({"TRENCH", "BUMP", "BOTH"}, {"NONE", "N/A"}),
}

#: Lovat records a climb by the second it STARTED, in one column per level, and
#: fills only the level the robot actually used.  That is the one thing in this
#: export our own scouting cannot produce - a scout with two thumbs cannot time
#: a climb - and it answers the question every alliance captain asks about a
#: robot that says it climbs: how long before the buzzer does it have to leave?
CLIMB_START = {"l1StartTime": "Level1", "l2StartTime": "Level2", "l3StartTime": "Level3"}

#: Pipe-joined list columns, tallied.  The last three are single-valued enums
#: rather than lists, and are tallied for the same reason: "beached on the fuel"
#: and "beached on the bump" are different problems, and the rate above cannot
#: tell them apart.
#:
#: For those three, a cell Lovat wrote as TRUE/FALSE is skipped rather than
#: tallied.  Those spellings carry no kind - the rate in FLAGS has already read
#: them - and counting them put "false" into a row whose entire job is to say
#: WHICH kind, immediately beside the percentage that already said whether.  The
#: dashboard reads "gets beached 0% - false" off exactly that.
LISTS = {"robotRoles": "roles", "feederTypes": "feederTypes", "intakeType": "intakeTypes",
         "beached": "beachedKinds", "fieldTraversal": "traversalKinds",
         "autoClimb": "autoClimbResults"}

#: The columns kept per row rather than only averaged, so the dashboard can
#: draw Lovat's fuel and defence match by match beside our own instead of one
#: number beside one number.  An average hides the shape: a robot that put up
#: 40 fuel then 120 averages the same as one that put up 80 twice, and only the
#: first of those is worth asking about in the pits.
PER_MATCH = {
    "totalFuelOutputted": "fuel",
    "totalBallThroughput": "throughput",
    "fuelPerSecond": "fuelPerSec",
    "accuracy": "accuracy",
    "totalBallsFed": "ballsFed",
    "totalDefenseTime": "defenseSecs",
    "contactDefenseTime": "contactDefenseSecs",
    "campingDefenseTime": "campingDefenseSecs",
    "defenseEffectiveness": "defenseEffectiveness",
    "timeFeeding": "feedSecs",
    "totalPoints": "totalPoints",
    "driverAbility": "driver",
}


def _num(v):
    """One number out of a CSV cell, or None for anything that is not one.

    Infinity is unknown for the same reason NaN is, and for one more: a cell
    reading `1e999` is a float here, and `int()` of it raises - which took the
    whole import down through `int(teamNumber)`, against the promise at the top
    of this file that the module never raises. The poller catches it and tries
    again in five minutes, forever, so one junk cell in somebody else's export
    means no Lovat data at all for the event.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _bool(v):
    s = (v or "").strip().upper()
    if s in ("TRUE", "T", "YES", "1"):
        return True
    if s in ("FALSE", "F", "NO", "0"):
        return False
    return None


def _flag(col, v):
    """"Did it happen" for one column, however Lovat spelled it this season."""
    b = _bool(v)
    if b is not None:
        return b
    s = (v or "").strip().upper()
    yes, no = ENUM_FLAGS.get(col, (frozenset(), frozenset()))
    if s in yes:
        return True
    if s in no:
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
    # NOT_ATTEMPTED and FAILED are Lovat's own words for "ended the match on the
    # floor". Reading them as unknown left climbRate counting only the successes,
    # so a robot that tried ten times and fell ten times looked like no data
    # instead of a robot that does not climb.
    if low in ("none", "no", "nothing", "n/a", "not_attempted", "not attempted",
               "failed", "fail", "attempted"):
        return "None"
    for d in ("3", "2", "1"):
        if d in s:
            return "Level" + d
    return None


def _climb_start(row):
    """Seconds into the match this robot began its climb, and at which level.

    Lovat fills one of three columns and leaves the other two blank, so the
    filled one names the level as well as the time.  Two filled at once means
    the robot moved up a level mid-endgame; the later start is the one that
    matters, because it is the one that decided when it had to stop scoring.
    """
    found = [(name, _num(row.get(col))) for col, name in CLIMB_START.items()]
    found = [(name, secs) for name, secs in found if secs is not None]
    if not found:
        return None, None
    level, secs = max(found, key=lambda pair: pair[1])
    return level, secs


def match_key(label, event_key):
    """"Q42" -> "<event>_qm42".  Returns None for anything else."""
    if not isinstance(label, str):
        return None
    s = label.strip()
    if not (s and event_key):
        return None
    kind, digits = s[0].upper(), s[1:].strip()
    if kind == "Q" and digits.isdigit():
        return "%s_qm%d" % (event_key, int(digits))
    return None


def parse_report_csv(text, event_key):
    """CSV text -> {team: record}.  None if the export is unreadable."""
    # "This module never raises" is the contract at the top of the file, and a
    # caller handing us bytes or a parsed object instead of text is exactly the
    # sort of drift it exists for.
    if not isinstance(text, str):
        if isinstance(text, (bytes, bytearray)):
            try:
                text = text.decode("utf-8-sig")
            except Exception:
                return None
        else:
            return None
    # Lovat's exporter passes bom:true to csv-stringify, so the real export
    # starts with a byte-order mark. Decoded as plain utf-8 that becomes part of
    # the FIRST column name - "match" - so every row's label read as empty and
    # every matchKey came back None: none of Lovat's per-match data could be
    # joined to our schedule at all. The bundled fixture has no BOM, which is
    # why nothing here ever noticed.
    text = text.lstrip("\ufeff")
    if not text.strip():
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
        by_team.setdefault(int(team), []).append(row)   # _num has ruled out inf and nan
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
        seen = [b for b in (_flag(col, r.get(col)) for r in rows) if b is not None]
        rec[name] = round(sum(seen) / len(seen) * 100.0, 1) if seen else None

    for col, name in LISTS.items():
        tally = {}
        # Only the three enum columns drop their boolean spellings; a role or an
        # intake type is free to be called whatever Lovat calls it.
        enum_col = col in ENUM_FLAGS
        for r in rows:
            for item in (r.get(col) or "").split("|"):
                item = item.strip()
                if not item or (enum_col and _bool(item) is not None):
                    continue
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
    # One row per match, in the order Lovat exported them, keyed onto our
    # schedule where the label maps.  A playoff row keeps its raw label and a
    # null matchKey: it is real data about the robot, and dropping it here
    # would make the chart disagree with the `matches` count beside it.
    rec["perMatch"] = []
    for r, (label, mk) in zip(rows, labels):
        level, secs = _climb_start(r)
        rec["perMatch"].append({
            "match": label or None, "matchKey": mk,
            **{name: _num(r.get(col)) for col, name in PER_MATCH.items()},
            "climb": _climb_level(r.get("endgameClimb")),
            "climbLevelTimed": level,
            "climbStartSecs": secs,
            "autoClimbStartSecs": _num(r.get("autoClimbStartTime")),
        })

    # Climb timing, per level and overall. Kept per level as well as pooled
    # because "starts its L3 at 128s" and "starts its L1 at 128s" are different
    # robots: the first is quick, the second has given up half the endgame.
    starts = {}
    for row in rec["perMatch"]:
        if row["climbStartSecs"] is not None and row["climbLevelTimed"]:
            starts.setdefault(row["climbLevelTimed"], []).append(row["climbStartSecs"])
    rec["climbStart"] = {lvl: _mean(v) for lvl, v in sorted(starts.items())}
    rec["climbStartSecs"] = _mean([v for vs in starts.values() for v in vs])
    rec["autoClimbStartSecs"] = _mean(
        [v for v in (row["autoClimbStartSecs"] for row in rec["perMatch"]) if v is not None])
    # Kept so the dashboard can say "42 rows, 3 of them playoff labels we could
    # not place" instead of silently showing a smaller number than Lovat has.
    rec["unmatched"] = sorted(set(unmatched))
    return rec
