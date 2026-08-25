"""2026 REBUILT rules, loaded from the same web/rules2026.json the client uses.

Sharing one file is deliberate: a client and server that disagree about where
SHIFT 2 ends would silently mis-attribute fuel.
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
RULES_PATH = os.path.join(_HERE, "..", "web", "rules2026.json")

with open(RULES_PATH, "r", encoding="utf-8") as fh:
    RULES = json.load(fh)

PHASES = RULES["phases"]
PHASE_IDS = [p["id"] for p in PHASES]
MATCH_SECONDS = RULES["matchSeconds"]
BUCKETS = [b["id"] for b in RULES["intensityBuckets"]]
BUCKET_PRIORS = {b["id"]: b["prior"] for b in RULES["intensityBuckets"]}
TBA_WINDOW_FIELD = RULES["tbaBreakdownWindows"]


def phase_at(elapsed):
    if elapsed < 0:
        return None
    for p in PHASES:
        if p["start"] <= elapsed < p["end"]:
            return p
    return None


def phase_by_id(pid):
    for p in PHASES:
        if p["id"] == pid:
            return p
    return None


def hub_active(phase_id, alliance, auto_winner):
    """Is `alliance`'s hub active in `phase_id`?  None when undetermined.

    The alliance that scores more fuel in AUTO is inactive for SHIFT 1, then
    alternates.  Both hubs are active in auto, transition, and endgame.
    """
    p = phase_by_id(phase_id)
    if p is None:
        return None
    if p.get("bothHubsActive"):
        return True
    if auto_winner not in ("red", "blue"):
        return None
    s = p["shiftIndex"]
    return (s % 2 == 0) if auto_winner == alliance else (s % 2 == 1)


def tower_points(level, period):
    return RULES["tower"].get(period, {}).get(level, 0)


def rp_thresholds(event_level="regional"):
    rp = RULES["rankingPoints"]
    return {
        "energized": rp["energized"].get(event_level, rp["energized"]["regional"]),
        "supercharged": rp["supercharged"].get(event_level, rp["supercharged"]["regional"]),
        "traversal": rp["traversal"].get(event_level, rp["traversal"]["regional"]),
    }
