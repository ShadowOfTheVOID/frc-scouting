"""Outbound API clients.  The server is the only thing that talks to the internet;
devices never hold a key.

Every client returns None on failure rather than raising.  Statbotics was
returning HTTP 500 on every route while this was written, which is exactly the
condition the app must survive without showing an error.
"""
import json
import gzip
import io
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

TBA_BASE = "https://www.thebluealliance.com/api/v3"
STATBOTICS_BASE = "https://api.statbotics.io/v3"
NEXUS_BASE = "https://frc.nexus/api/v1"
FRC_EVENTS_BASE = "https://frc-api.firstinspires.org/v3.0"
LOVAT_BASE = "https://api.lovat.app/v1"

USER_AGENT = "frc-rebuilt-scouting/1.0 (+https://frc.nexus)"
_ctx = ssl.create_default_context()


class _Cache:
    """ETag / Last-Modified cache so polling TBA costs a 304 instead of a payload."""

    def __init__(self):
        self.lock = threading.Lock()
        self.etags = {}
        self.bodies = {}

    def get(self, url):
        with self.lock:
            return self.etags.get(url), self.bodies.get(url)

    def put(self, url, etag, body):
        with self.lock:
            if etag:
                self.etags[url] = etag
            self.bodies[url] = body


CACHE = _Cache()


# ------------------------------------------------------- key verification
#
# `verify()` on each client exists for one screen: the TEST KEYS button on the
# Setup page.  Everything else in this file is a poller that treats a failure as
# "we do not know" and moves on, which is right at a competition and useless to
# a lead who is trying to find out whether the thing they just pasted is the
# thing the vendor issued.  So this is the one place a failure is allowed to be
# specific, and to say whose fault it is.

#: What `verify()` answers with.  `state` is one of:
#:   ok      the vendor accepted the key
#:   bad     the vendor rejected it - the key itself is wrong
#:   warn    the key works but something else does not add up
#:   down    we could not reach the vendor, so we still do not know
#:   unset   no key is saved, which is not a failure
def verdict(state, detail=""):
    return {"state": state, "detail": detail}


def _rejection(status, vendor):
    """One HTTP status, in the words a scouting lead can act on."""
    if status in (401, 403):
        return verdict("bad", f"{vendor} rejected the key - it is not one they issued, "
                              "or it has been revoked")
    if status == 429:
        return verdict("warn", f"{vendor} is rate-limiting us; the key itself looks fine. "
                               "Wait a minute and test again")
    if status == 0:
        return verdict("down", "could not reach them at all - check this laptop is online. "
                               "Venue wifi often blocks this")
    return verdict("down", f"{vendor} answered with HTTP {status}, which is their end, "
                           "not the key")


def _request(url, headers=None, timeout=12, use_etag=False, method="GET", data=None, raw=False):
    """`raw` returns the decoded body as text instead of parsed JSON.

    Lovat exports CSV, and it deserves the same gzip / ETag / timeout handling
    as every other source rather than a second copy of this function.
    """
    headers = dict(headers or {})
    headers.setdefault("User-Agent", USER_AGENT)
    headers.setdefault("Accept-Encoding", "gzip")
    etag = None
    if use_etag:
        etag, cached = CACHE.get(url)
        if etag:
            headers["If-None-Match"] = etag
    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx) as res:
            data_bytes = res.read()
            if res.headers.get("Content-Encoding") == "gzip":
                data_bytes = gzip.GzipFile(fileobj=io.BytesIO(data_bytes)).read()
            if raw:
                # utf-8-sig, not utf-8: Lovat's CSV export leads with a BOM,
                # and left in place it becomes part of the first column's name.
                body = data_bytes.decode("utf-8-sig", "replace") if data_bytes else None
            else:
                body = json.loads(data_bytes.decode("utf-8")) if data_bytes else None
            if use_etag:
                CACHE.put(url, res.headers.get("ETag"), body)
            return body, res.status
    except urllib.error.HTTPError as e:
        if e.code == 304 and use_etag:
            _, cached = CACHE.get(url)
            return cached, 304
        return None, e.code
    except Exception:
        return None, 0


# ------------------------------------------------------------------- TBA

class TBA:
    def __init__(self, key):
        self.key = key

    @property
    def ok(self):
        return bool(self.key)

    def _get(self, path, use_etag=True):
        if not self.key:
            return None
        body, _ = _request(TBA_BASE + path, {"X-TBA-Auth-Key": self.key}, use_etag=use_etag)
        return body

    def event(self, key):
        return self._get(f"/event/{key}")

    def event_teams(self, key):
        return self._get(f"/event/{key}/teams/simple")

    def events_for_team(self, team, year=2026):
        """Every event one team is registered for.  The authoritative list.

        Second choice behind Statbotics only because that one needs no key, and
        this runs during setup, before there is necessarily a key to use.
        """
        return self._get(f"/team/frc{int(team)}/events/{year}/simple", use_etag=False)

    def event_matches(self, key):
        return self._get(f"/event/{key}/matches")

    def event_rankings(self, key):
        return self._get(f"/event/{key}/rankings")

    def event_oprs(self, key):
        return self._get(f"/event/{key}/oprs")

    def verify(self):
        """Ask TBA whether this key works.  See `sources.verify` for the shape.

        `/status` is the cheapest route TBA has and it is authenticated, which
        is the whole requirement: a key that gets a 200 here is a key that will
        get one everywhere else.
        """
        if not self.ok:
            return verdict("unset")
        body, status = _request(TBA_BASE + "/status", {"X-TBA-Auth-Key": self.key}, timeout=10)
        if body is not None:
            return verdict("ok", "key accepted")
        return _rejection(status, "The Blue Alliance")


def parse_breakdown_2026(match):
    """Pull the per-window fuel counts and per-robot tower levels out of a TBA match.

    Field names verified against TBA's live OpenAPI schema
    (Match_Score_Breakdown_2026_Alliance / HubScore_2026 / TowerRobot_2026).
    """
    # Somebody else's JSON, over the network, mid-event. A shape we did not
    # expect - a schema change, a half-written match, an error object - used to
    # raise out of here into the poller, which then skipped every source queued
    # behind TBA and retried this same match every two seconds for the rest of
    # the day. Unknown reads as unknown, the same as every other source here.
    if not isinstance(match, dict):
        return {}
    bd = match.get("score_breakdown")
    if not isinstance(bd, dict):
        return {}
    out = {}
    for alliance in ("red", "blue"):
        a = bd.get(alliance)
        if not isinstance(a, dict) or not a:
            continue
        hub = a.get("hubScore")
        hub = hub if isinstance(hub, dict) else {}
        windows = {
            "auto": hub.get("autoCount"),
            "transition": hub.get("transitionCount"),
            "shift1": hub.get("shift1Count"),
            "shift2": hub.get("shift2Count"),
            "shift3": hub.get("shift3Count"),
            "shift4": hub.get("shift4Count"),
            "endgame": hub.get("endgameCount"),
        }
        out[alliance] = {
            "windows": {k: v for k, v in windows.items() if v is not None},
            "autoTower": [a.get(f"autoTowerRobot{i}") for i in (1, 2, 3)],
            "endgameTower": [a.get(f"endGameTowerRobot{i}") for i in (1, 2, 3)],
            "totalPoints": a.get("totalPoints"),
            "totalTowerPoints": a.get("totalTowerPoints"),
            "rp": a.get("rp"),
            "energized": a.get("energizedAchieved"),
            "supercharged": a.get("superchargedAchieved"),
            "traversal": a.get("traversalAchieved"),
            "fouls": {"minor": a.get("minorFoulCount"), "major": a.get("majorFoulCount")},
        }
    # Which alliance won auto decides who sits out shift 1.
    if "red" in out and "blue" in out:
        r = out["red"]["windows"].get("auto")
        b = out["blue"]["windows"].get("auto")
        if r is not None and b is not None:
            out["autoWinner"] = "red" if r > b else ("blue" if b > r else None)
    return out


# ---------------------------------------------------------------- Nexus

class Nexus:
    def __init__(self, key):
        self.key = key

    @property
    def ok(self):
        return bool(self.key)

    def _get(self, path):
        if not self.key:
            return None
        body, _ = _request(NEXUS_BASE + path, {"Nexus-Api-Key": self.key})
        return body

    def event(self, key):
        return self._get(f"/event/{key}")

    def pits(self, key):
        return self._get(f"/event/{key}/pits")

    def pit_map(self, key):
        return self._get(f"/event/{key}/map")

    def inspection(self, key):
        return self._get(f"/event/{key}/inspection")

    def alliances(self, key):
        return self._get(f"/event/{key}/alliances")

    def events(self):
        return self._get("/events")

    def verify(self, event_key=None):
        """Ask Nexus whether this key works.

        The configured event if there is one, because that answers the second
        question in the same request - a key can be perfectly good and still
        return nothing for an event Nexus is not covering.
        """
        if not self.ok:
            return verdict("unset")
        path = f"/event/{event_key}" if event_key else "/events"
        body, status = _request(NEXUS_BASE + path, {"Nexus-Api-Key": self.key}, timeout=10)
        if body is not None:
            return verdict("ok", "key accepted")
        if status == 404 and event_key:
            return verdict("warn", f"the key works, but Nexus has nothing for {event_key} - "
                                   "check the event key, or wait until the event opens")
        return _rejection(status, "Nexus")


# ----------------------------------------------------------- Statbotics

class Statbotics:
    """Optional enrichment.  Never blocks, never surfaces an error."""

    def __init__(self):
        self.down_until = 0.0

    def _get(self, path):
        if time.time() < self.down_until:
            return None
        body, status = _request(STATBOTICS_BASE + path, timeout=8)
        if body is None:
            # back off for a while rather than hammering a service that is 500ing
            self.down_until = time.time() + 300
        return body

    def team_year(self, team, year=2026):
        return self._get(f"/team_year/{team}/{year}")

    def team_events(self, event, year=2026):
        q = urllib.parse.urlencode({"event": event, "year": year, "limit": 200})
        return self._get(f"/team_events?{q}")

    def events_for_team(self, team, year=2026):
        """Every event one team is registered for this season.

        The only lookup in this file that needs no key at all, which is what
        makes it worth having: it runs on a hub that has been started for the
        first time and configured with nothing, and it is what turns "what is my
        event key?" - the one question in setup nobody can answer from memory -
        into a list to pick from.
        """
        q = urllib.parse.urlencode({"team": int(team), "year": year, "limit": 50})
        return self._get(f"/team_events?{q}")


# --------------------------------------------------------- FRC Events

class FRCEvents:
    def __init__(self, username, token):
        self.username, self.token = username, token

    @property
    def ok(self):
        return bool(self.username and self.token)

    def _get(self, path):
        if not self.ok:
            return None
        import base64
        cred = base64.b64encode(f"{self.username}:{self.token}".encode()).decode()
        body, _ = _request(FRC_EVENTS_BASE + path,
                           {"Authorization": f"Basic {cred}", "Accept": "application/json"})
        return body

    def scores(self, season, event, level="qual"):
        return self._get(f"/{season}/scores/{event}/{level}")

    def verify(self, season=2026):
        """Ask FRC Events whether this username and token work together.

        Either half being wrong fails the same way, which is why the message
        says both: people paste the token into the username box constantly.
        """
        if not self.ok:
            return verdict("unset", "username and token are both needed")
        body, status = self._get_with_status(f"/{season}")
        if body is not None:
            return verdict("ok", "username and token accepted")
        if status in (401, 403):
            return verdict("bad", "FRC Events rejected them - check the username as well as "
                                  "the token; they are two different strings")
        return _rejection(status, "FRC Events")

    def _get_with_status(self, path):
        import base64
        cred = base64.b64encode(f"{self.username}:{self.token}".encode()).decode()
        return _request(FRC_EVENTS_BASE + path,
                        {"Authorization": f"Basic {cred}", "Accept": "application/json"},
                        timeout=10)


# --------------------------------------------------------------- Lovat

class Lovat:
    """Other teams' scouting, from https://lovat.app.

    Auth is an API key the scouting lead mints in the Lovat Dashboard; it starts
    with `lvt-` and rides on `Authorization: Bearer`.  Lovat rate-limits a key to
    one request every three seconds and 403s a team that has not verified its
    email, so a failure here backs off rather than hammering the key - the same
    shape as Statbotics.
    """

    #: Lovat allows one request per three seconds per key; a rejection means we
    #: sit out five minutes rather than burn the team's quota re-asking.
    BACKOFF_SECONDS = 300

    def __init__(self, key):
        self.key = (key or "").strip()
        self.down_until = 0.0

    @property
    def ok(self):
        return bool(self.key)

    def report_csv(self, tournament_key):
        """Every scout report at one tournament, one row per team per match.

        Returns CSV text, or None for "we do not know" - no key, rate limited,
        an unverified team, or an event Lovat has nothing for.
        """
        if not (self.ok and tournament_key):
            return None
        if time.time() < self.down_until:
            return None
        q = urllib.parse.urlencode({"tournamentKey": tournament_key})
        body, status = _request(
            f"{LOVAT_BASE}/analysis/reportcsv?{q}",
            {"Authorization": f"Bearer {self.key}", "Accept": "text/csv"},
            use_etag=True, raw=True, timeout=20,
        )
        if body is None:
            # 429 (rate limited), 403 (team not verified) and 401 (bad key) all
            # mean the next few requests would fail the same way.
            if status in (401, 403, 429):
                self.down_until = time.time() + self.BACKOFF_SECONDS
            return None
        return body

    def verify(self, tournament_key=None):
        """Ask Lovat whether this key works.

        403 is the one that needs saying out loud, because it is not the key: it
        is a team that has not been verified on Lovat's side, which is a person
        there approving you and can take days.  A lead who reads "bad key" for
        that goes and makes a second key, which fails identically.

        The backoff is deliberately ignored - this is a button somebody pressed,
        not the poller - but a rejection still arms it, because the answer to
        "is this key good" does not change in the next five minutes.
        """
        if not self.ok:
            return verdict("unset")
        if not tournament_key:
            return verdict("warn", "set the event key first - Lovat is asked per tournament, "
                                   "so there is nothing to test against yet")
        q = urllib.parse.urlencode({"tournamentKey": tournament_key})
        body, status = _request(
            f"{LOVAT_BASE}/analysis/reportcsv?{q}",
            {"Authorization": f"Bearer {self.key}", "Accept": "text/csv"},
            raw=True, timeout=20)
        if body is not None:
            rows = max(0, len([r for r in body.splitlines() if r.strip()]) - 1)
            if rows:
                return verdict("ok", f"key accepted, {rows} row(s) for {tournament_key}")
            return verdict("warn", "the key works, but Lovat has nothing for "
                                   f"{tournament_key} - nobody has uploaded this event yet")
        if status in (401, 403, 429):
            self.down_until = time.time() + self.BACKOFF_SECONDS
        if status == 403:
            return verdict("bad", "Lovat says your team is not verified yet. That is their "
                                  "check on your team, not a problem with the key - a new "
                                  "key will fail the same way")
        return _rejection(status, "Lovat")
