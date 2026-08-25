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


def _request(url, headers=None, timeout=12, use_etag=False, method="GET", data=None):
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
            raw = res.read()
            if res.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            body = json.loads(raw.decode("utf-8")) if raw else None
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

    def event_matches(self, key):
        return self._get(f"/event/{key}/matches")

    def event_rankings(self, key):
        return self._get(f"/event/{key}/rankings")

    def event_oprs(self, key):
        return self._get(f"/event/{key}/oprs")


def parse_breakdown_2026(match):
    """Pull the per-window fuel counts and per-robot tower levels out of a TBA match.

    Field names verified against TBA's live OpenAPI schema
    (Match_Score_Breakdown_2026_Alliance / HubScore_2026 / TowerRobot_2026).
    """
    bd = (match or {}).get("score_breakdown") or {}
    out = {}
    for alliance in ("red", "blue"):
        a = bd.get(alliance)
        if not a:
            continue
        hub = a.get("hubScore") or {}
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
