"""Read a broadcast harvest's scouting API as one more source from outside.

The harvest is a separate tool (`TBACroppedOutVid`) that pulls match video TBA
links to, crops the burned-in graphics off it, and reads each alliance's fuel
counter off the score banner before discarding it.  What it leaves behind is a
SQLite database and a read-only JSON API over it, which is what this file
talks to:

    python3 serve.py            # in the harvest checkout: 127.0.0.1:8781

Set `visionUrl` in the admin panel to that address and the hub polls it like
any other source.  There is no key: the API is read-only, GET-only and local.

## What it can and cannot tell us

The one thing here that exists nowhere else is **when** fuel went in.  TBA
publishes per-window totals; the harvest read the counter every fifth of a
second, so it has the scoring curve inside a match, for any match anyone has
filmed - including matches at events we were never at, played by robots we are
about to.

What it cannot tell us is **which robot**.  The scoreboard says an alliance
scored and never says which of its three did, so every number in here is
alliance-level.  The harvest's own notes are blunt about this and so is this
file: bumper OCR does not work on this footage (a bumper number is about five
pixels tall at broadcast resolution), and until its identity layer runs behind
a trained detector and a tracker, a per-robot number from this source would be
an alliance total with a robot's name written on it.

So `vision` is its own block beside `lovat`, for the same reason and with the
same rule: **it is shown for comparison and feeds nothing.**  Neither
`solve.py` nor the picklist reads it.  Three separate reasons, any one of which
would be enough:

  * it is alliance-level, and the solver's whole job is dividing an alliance
    total between three robots - handing it a third of a broadcast reading as
    if it were a measurement of one robot is the exact mistake that
    `solve.provisional_match` marks and that `poll_frc_events` refuses to make;
  * it is OCR off somebody's video overlay, and the harvest reports the read
    failing outright on roughly 40% of events;
  * the solver reconciles against TBA's official totals, which are the same
    quantity measured by the field itself.  A second, worse reading of a number
    we already have exactly is not an input.  It is a cross-check, which is
    worth having and is not the same job.

Every row carries `scoreboardOk` so a reading the harvest itself does not
trust is visible as one rather than averaged in silently.

Like every other source client here, nothing in this file raises: a harvest
that is not running, or is mid-rebuild, returns None, which is the same "we do
not know" the rest of `sources.py` returns.
"""
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from sources import USER_AGENT, verdict

#: The harvest is normally on the same laptop or the same bench switch, so this
#: is short on purpose: a source that is not there must not hold up the poll
#: loop behind it.  `sources._request` is not reused because that one is built
#: for the internet - ETag caching against vendors, gzip, a 12-second budget -
#: and this is a localhost call to a stdlib HTTP server that sends no ETags.
TIMEOUT_S = 6

#: Don't walk an unbounded number of teams.  A district event is 30-40 teams
#: and each one is a separate request against the harvest's `/teams/<n>`; the
#: cap is what stops a misconfigured `visionUrl` pointing at something large
#: from turning one poll into a thousand requests.
MAX_TEAMS = 60

#: And a wall clock over the whole walk, because the cap alone is not enough.
#: `hub.run_poller` runs every source in sequence in one thread: a harvest that
#: stops answering halfway through would hold Nexus and TBA behind forty
#: timeouts, and Nexus is what arms the scouting screen on six phones.  The
#: harvest is a dataset that changes between events, so an incomplete pass is
#: nearly free - the next one is five minutes away and will find the rest.
WALK_BUDGET_S = 20

#: `2026casj_qm42` -> `2026casj`.  The harvest keys matches the way TBA does.
MATCH_KEY = re.compile(r"^([0-9]{4}[a-z0-9]+)_")

#: A whole match key, for the one route that takes one from a caller.
#: `urllib.parse.quote` leaves `/` alone by default, so a key is not a safe
#: thing to interpolate into a path on its own - and `/api/vision?match=` is
#: reachable by anything on the venue wifi.  Checked here rather than only at
#: the endpoint, so the next caller of `Vision.match` inherits the check.
WHOLE_MATCH_KEY = re.compile(r"^[0-9]{4}[a-z0-9]+_[a-z]{1,2}[0-9]+(m[0-9]+)?$")


def _num(v):
    """A real number out of somebody else's JSON, or None.

    SQLite hands back NULL for a match whose scoreboard never read, and that
    has to stay unknown rather than becoming a zero that drags an average
    down - the same rule every other source in this server follows.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return v if math.isfinite(v) else None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


def normalise_url(raw):
    """A harvest base URL, or "" for anything that is not one.

    Scheme required, path and query dropped: what is stored is an origin, and
    every route this file asks for is appended to it.  A pasted
    `http://127.0.0.1:8781/health` is the address somebody copied out of their
    browser and is meant to work.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urllib.parse.urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


class Vision:
    """The harvest's read-only API.  Every method returns None on failure."""

    def __init__(self, base):
        self.base = normalise_url(base)

    @property
    def ok(self):
        return bool(self.base)

    def _get(self, path, timeout=TIMEOUT_S):
        if not self.base:
            return None, 0
        req = urllib.request.Request(self.base + path,
                                     headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                body = res.read()
                return (json.loads(body.decode("utf-8")) if body else None), res.status
        except urllib.error.HTTPError as e:
            return None, e.code
        except Exception:
            return None, 0

    def health(self):
        """Row counts, which is also the "is it there" check."""
        body, _ = self._get("/health")
        return body if isinstance(body, dict) else None

    def teams(self):
        """`team_summary` for every team in the harvest, across all events."""
        body, _ = self._get("/teams")
        return (body or {}).get("teams") if isinstance(body, dict) else None

    def team(self, number):
        """One team's summary plus its per-match alliance fuel rows."""
        body, _ = self._get(f"/teams/{int(number)}")
        return body if isinstance(body, dict) else None

    def match(self, match_key):
        """A match, its roster, and the full scoring timeline off the banner.

        Refuses anything that is not a match key rather than asking the harvest
        about it: this is the one route whose path comes from a caller, and a
        key with a slash in it would be a request for some other route.
        """
        if not WHOLE_MATCH_KEY.match(match_key or ""):
            return None
        body, _ = self._get(f"/matches/{urllib.parse.quote(match_key, safe='')}")
        return body if isinstance(body, dict) else None

    def verify(self):
        """Ask the harvest whether it is there and has anything.  See `sources.verdict`.

        Three answers rather than two, because "running but empty" is a real
        state with a different fix: the harvest is up and the API works, and
        nobody has pulled any video yet.  Telling a lead that as "ok" sends
        them looking for a bug in the hub.
        """
        if not self.ok:
            return verdict("unset")
        body, status = self._get("/health")
        if body is None:
            if status == 0:
                return verdict("down",
                               "nothing answered at that address - is the harvest's "
                               "`python3 serve.py` running, and is this the port it "
                               "printed?")
            return verdict("down", f"the harvest answered with HTTP {status}, which is "
                                   "its end, not the address")
        counts = body.get("counts") or {}
        matches = _int(counts.get("matches")) or 0
        if not matches:
            return verdict("warn", "the harvest is running but has no matches in it "
                                   "yet - pull some video on that machine first")
        frames = _int(counts.get("frames")) or 0
        return verdict("ok", f"{matches} match(es), {frames} frame(s) harvested")


def _event_of(match_key):
    m = MATCH_KEY.match(match_key or "")
    return m.group(1) if m else ""


def team_record(detail, our_event=""):
    """One team's `vision` block, out of the harvest's `/teams/<n>` payload.

    `matches` counts only the matches whose scoreboard read cleanly, because
    that is what the harvest's own average is over, and a count that disagreed
    with the average it sits beside would be read as the average being wrong.
    `matchesSeen` is every match it has footage of, so the gap between the two
    says how much of this robot's footage was unreadable rather than hiding it.

    Rows from THIS event and rows from anywhere else are counted together and
    then separated, because the two answer different questions: at a pick
    meeting, footage from a robot's earlier event is the whole reason to care,
    and footage from this one is a cross-check against numbers we already have.
    """
    if not isinstance(detail, dict):
        return None
    summary = detail.get("summary") or {}
    rows = [r for r in (detail.get("matches") or []) if isinstance(r, dict)]

    per_match, here, elsewhere = [], 0, 0
    for row in rows:
        mk = row.get("match_key") or ""
        fuel = _int(row.get("alliance_fuel"))
        ok = bool(_int(row.get("scoreboard_ok")))
        event = _event_of(mk)
        if event and our_event and event == our_event:
            here += 1
        elif event:
            elsewhere += 1
        per_match.append({
            "matchKey": mk,
            "event": event,
            "alliance": row.get("alliance"),
            # Alliance-level, and named so at every level of this payload.  A
            # key called `fuel` here would be read as this robot's fuel by the
            # next person to touch the dashboard, which is the whole thing this
            # block is trying not to imply.
            "allianceFuel": fuel,
            "scoreboardOk": ok,
        })
    per_match.sort(key=lambda r: r["matchKey"])

    clean = [r["allianceFuel"] for r in per_match
             if r["scoreboardOk"] and r["allianceFuel"] is not None]
    avg = _num(summary.get("avg_alliance_fuel"))
    if avg is None and clean:
        avg = sum(clean) / len(clean)

    return {
        # `matches`, which is what the harvest's `team_summary` view calls it.
        # This read `matches_played` first -- a name that view has never had --
        # so it was always None and always fell through to the count below.
        # That fallback agrees most of the time, which is why it went unnoticed:
        # the view counts rows with scoreboard_ok=1 and this counts rows that
        # also parsed to a number, so a clean read with a null total is counted
        # by one and not the other. The test fixture had invented the same
        # wrong name, so nothing caught it.
        "matches": _int(summary.get("matches")) or len(clean),
        "matchesSeen": len(per_match),
        "avgAllianceFuel": round(avg, 1) if avg is not None else None,
        "totalAllianceFuel": _int(summary.get("total_alliance_fuel")),
        "matchesHere": here,
        "matchesElsewhere": elsewhere,
        "perMatch": per_match,
    }


def timeline(match):
    """The scoring curve for one match, off `/matches/<key>`.

    The one thing in this source that exists nowhere else, so it is kept whole
    rather than reduced to a total we already have from TBA.  `tSource` is
    seconds into the source video, not into the match: the harvest counts from
    the start of the broadcast, which begins before the buzzer by however long
    the director felt like.  Nothing here pretends otherwise, and no caller may
    treat it as match time without an offset somebody has actually measured.
    """
    if not isinstance(match, dict):
        return None
    row = match.get("match") or {}
    events = []
    for e in (match.get("score_events") or []):
        if not isinstance(e, dict):
            continue
        balls = _int(e.get("balls"))
        t = _num(e.get("t_source"))
        if balls is None or t is None:
            continue
        events.append({"tSource": round(t, 2), "alliance": e.get("alliance"),
                       "balls": balls, "total": _int(e.get("total"))})
    events.sort(key=lambda e: e["tSource"])
    return {
        "matchKey": row.get("match_key"),
        "event": row.get("event_key") or _event_of(row.get("match_key") or ""),
        "scoreboardOk": bool(_int(row.get("scoreboard_ok"))),
        "blueFuel": _int(row.get("blue_fuel")),
        "redFuel": _int(row.get("red_fuel")),
        "teams": [t for t in (match.get("teams") or []) if isinstance(t, dict)],
        "events": events,
    }


def collect(client, event_key, our_teams, max_teams=MAX_TEAMS):
    """Everything the harvest has about the teams at one event.

    `our_teams` is the roster to ask about, and it is the roster rather than
    everything the harvest holds for a reason: the harvest is a season-wide
    dataset - a thousand events, most of them nothing to do with this
    competition - and the hub only has a use for the robots in this building.

    Returns `{"teams": {str(team): record}, "counts", "asked", "wanted",
    "known", "complete"}`, or None if the harvest could not be reached at all.
    An empty `teams` is an answer and is written as one: "the harvest has no
    footage of anybody here" must not leave yesterday's rows on screen.

    `complete` is false when the walk ran out of budget or the harvest stopped
    answering partway, so a short answer is readable as a short answer rather
    than as a harvest with less footage than it has.
    """
    health = client.health()
    if health is None:
        return None

    # One request to find out who is worth asking about individually, instead
    # of one per team to find out most of them are not in the harvest at all.
    known = set()
    for row in (client.teams() or []):
        if isinstance(row, dict):
            n = _int(row.get("team"))
            if n is not None:
                known.add(n)

    wanted = [t for t in sorted(set(our_teams)) if t in known][:max_teams]
    out = {}
    asked = 0
    deadline = time.monotonic() + WALK_BUDGET_S
    for team in wanted:
        if time.monotonic() > deadline:
            break
        detail = client.team(team)
        asked += 1
        if detail is None:
            # It answered `/health` and `/teams` and has now stopped. Whatever
            # is wrong will not be fixed by asking it thirty-nine more times,
            # and what we have is still an answer about the teams we got to.
            break
        rec = team_record(detail, our_event=event_key)
        if rec and rec["matchesSeen"]:
            out[str(team)] = rec
    return {"teams": out, "counts": health.get("counts") or {},
            "asked": asked, "wanted": len(wanted), "known": len(known),
            # So a partial pass is visible as one rather than looking like a
            # harvest that has footage of fewer robots than it does.
            "complete": asked == len(wanted)}
