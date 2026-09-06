"""2026 REBUILT rules, loaded from the same web/rules2026.json the client uses.

Sharing one file is deliberate: a client and server that disagree about where
SHIFT 2 ends would silently mis-attribute fuel.
"""
import json
import math
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


def interval_secs(iv):
    """How long one recorded interval lasted, or 0.0 if it does not say.

    Every consumer - the solver, analytics, the exports - used to read this as
    `float(iv.get("end", iv["start"])) - float(iv["start"])`, which assumes both
    ends are numbers. A row where either is null or a string took the whole
    request down with a TypeError, and `/api/analytics` dying is the strategy
    dashboard going blank for the rest of the event. An interval that cannot say
    how long it was contributes nothing, which is what it knows.
    """
    if not isinstance(iv, dict):
        return 0.0
    try:
        start = float(iv["start"])
        end = iv.get("end")
        end = start if end is None else float(end)
    except (KeyError, TypeError, ValueError):
        return 0.0
    if not (math.isfinite(start) and math.isfinite(end)):
        return 0.0
    return max(0.0, end - start)


def split_by_phase(intervals):
    """One interval per window a hold actually touched, clipped to each.

    A scout holds the pad while their robot shoots; the robot does not stop
    because a shift ended.  The recorded hold carries a single `phase` tag -
    the window it started in - so a 12s hold from t=25 spent 5s in TRANSITION
    and 7s in SHIFT 1 but told the solver it spent all 12s in TRANSITION.  That
    inflates the robot's share of one window's official fuel and hands it none
    of the next, and a hold that began while its hub was still dark was thrown
    away whole.  Splitting on the window edges is the same observation, told
    accurately: ~13.6% -> ~11.8% median per-robot error in simulation.

    Done on read, never on the stored row: the scout pressed the pad once, so
    the phone still shows one run and can still undo it in one tap.
    """
    out = []
    for iv in intervals or []:
        if not isinstance(iv, dict):
            continue
        try:
            start = float(iv["start"])
            end = iv.get("end")
            end = start if end is None else float(end)
        except (KeyError, TypeError, ValueError):
            out.append(iv)      # cannot place it - leave whatever it claims
            continue
        if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
            out.append(iv)
            continue
        cut = [dict(iv, start=max(start, p["start"]), end=min(end, p["end"]), phase=p["id"])
               for p in PHASES
               if min(end, p["end"]) > max(start, p["start"])]
        # Off the end of the match, or before it began: in no window at all.
        out.extend(cut or [dict(iv, phase=None)])
    return out


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
