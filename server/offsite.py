"""Pushing the event to an off-site mirror.

The hub is one laptop on venue wifi. It gets carried, unplugged, knocked off a
table, and put to sleep in a bag between quals - and it is behind whatever NAT
the venue runs, so nothing on the internet can reach in and ask it for
anything. Snapshots in `data/snapshots/` survive a corrupt database; they do
not survive the laptop.

So the direction is out, never in: every minute the hub packs the event into
one bundle and POSTs it to a mirror site the team already hosts. That is the
only shape that works from behind NAT, and it has a second use - the mirror is
a normal website, so a strategy lead in the stands with a phone on cell data
can read the numbers without being on venue wifi at all.

Nothing here is on the critical path. Every function returns a status dict and
raises nothing: a mirror that is down, misconfigured or slow must cost the hub
nothing but a line in the diagnostics panel.
"""
import base64
import gzip
import hashlib
import json
import secrets
import ssl
import time
import urllib.error
import urllib.request

import analytics
import sources

#: Bumped when the bundle's shape changes in a way a mirror has to know about.
#: The mirror stores what it is sent either way - a bundle it cannot read is
#: still a bundle worth keeping - but it reports the mismatch.
PROTOCOL = 1

USER_AGENT = "frc-rebuilt-scouting-hub/1.0"

#: A venue uplink is shared with several thousand people and their phones. The
#: bundle is gzipped and skipped entirely when nothing changed, so this is the
#: ceiling on how often it *could* go, not how often it does.
PUSH_SECONDS = 60

#: Even with nothing changed, push this often anyway. The mirror's front page
#: says how long ago it last heard from the hub, and that line is only worth
#: anything if silence means silence.
HEARTBEAT_SECONDS = 900

#: Photos are the only part of a bundle measured in megabytes, so they go
#: separately and only once: the mirror answers a push with the ids it does not
#: have, and the hub sends those. This caps one batch so a pit-scouting day of
#: photos crosses the uplink over several minutes rather than one 40MB POST.
PHOTO_BATCH = 6

TIMEOUT = 45


def hub_id(store):
    """A stable id for this hub, so a mirror can hold two events at once.

    Not a secret and not a key - it identifies the laptop, not the team. It is
    generated once and kept in the database so a mirror can tell "the same hub
    pushed again" from "a second laptop is pushing the same event", which is
    the case where the two would otherwise overwrite each other all afternoon.
    """
    def apply(cur):
        if cur:
            return None, cur
        new = secrets.token_hex(6)
        return new, new
    return store.mutate("hubId", apply, None)


def _digest(bundle):
    """A hash of everything in the bundle except when it was taken.

    This is what makes "nothing changed, do not send it" possible - and the
    capture time changes every minute whether or not a single scout has tapped
    anything, so it has to come out first.
    """
    body = {k: v for k, v in bundle.items() if k not in ("capturedAt", "digest")}
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


# Every scope build_bundle below reads. Checked before the bundle is built at
# all: the digest was doing that job, but only after serialising the entire
# event and hashing it, once a minute, forever, to discover nothing had moved.
BUNDLE_SCOPES = ("events", "teams", "matches", "flags", "scout_entries",
                 "pit_entries", "photos", "solved",
                 "kv:rankings", "kv:epa", "kv:lovat", "kv:alliances", "kv:pits",
                 "kv:pitMap", "kv:inspection", "kv:matchClocks", "kv:clockFixes",
                 "kv:multipliers", "kv:multipliersFittedFrom", "kv:picklist",
                 "kv:ourTeam", "kv:hubId")


def build_bundle(hub, event_key):
    """Everything about one event, in one object.

    Three different jobs, deliberately in one file rather than three endpoints:

    - `scout` and `pit` are the raw rows, in exactly the shape `/api/import`
      reads. That is the restore path - a mirror export dropped into a fresh
      hub rebuilds the event.
    - `analytics`, `matches`, `teams` and `picklist` are what the mirror's own
      pages draw, so the mirror never has to run the solver or know the game.
    - `csv` is the spreadsheet the dashboard's SERVER tab produces, carried
      verbatim so the two can never disagree about what a column means.

    Per-scout quality scores are the one thing deliberately left out. The hub
    only shows them to the lead, on the reasoning that a scoreboard of who is
    worst at their job costs morale and buys nothing; a copy on the public
    internet, behind one shared passcode, is not the place to relax that.
    """
    store = hub.store
    # A copy, minus the per-scout block. event_summary memoizes and hands back
    # the object it cached, so popping a key off it here would quietly strip
    # that block from the copy the lead's dashboard is about to be served.
    summary = {k: v for k, v in analytics.event_summary(store, event_key).items()
               if k != "scouts"}

    csv = {}
    for table in ("teams", "lovat"):
        try:
            csv[table] = hub.csv_text(event_key, table)
        except Exception:
            pass

    bundle = {
        "kind": "frc-rebuilt-scouting-mirror",
        "protocol": PROTOCOL,
        "hubId": hub_id(store),
        "eventKey": event_key,
        "capturedAt": time.time(),
        "ourTeam": hub.cfg("ourTeam"),
        "event": store.event(event_key),
        "teams": store.teams(event_key),
        "matches": store.matches(event_key),
        "flags": store.flags(event_key),
        "rankings": hub.event_data("rankings", event_key, {}),
        "epa": hub.event_data("epa", event_key, {}),
        "alliances": hub.nexus_data("alliances", event_key, []),
        "pits": hub.nexus_data("pits", event_key, {}),
        "pitMap": hub.nexus_data("pitMap", event_key),
        "inspection": hub.nexus_data("inspection", event_key, {}),
        "matchClocks": store.get("matchClocks") or {},
        "clockFixes": store.get("clockFixes") or {},
        "multipliers": store.get("multipliers") or {},
        "multipliersFittedFrom": store.get("multipliersFittedFrom") or 0,
        "picklist": hub.picklist(),
        "analytics": summary,
        "scout": store.scout_entries(event_key),
        "pit": store.pit_entries(event_key),
        "photoIds": store.photo_ids(event_key),
        "csv": csv,
    }
    bundle["digest"] = _digest(bundle)
    return bundle


class Mirror:
    """The off-site site, as the hub sees it.

    `ok` is false whenever it is not configured, which is the normal state for
    a team that has not set one up - and every caller treats that as "nothing
    to do", never as an error.
    """

    def __init__(self, url, key):
        self.url = (url or "").strip().rstrip("/")
        self.key = (key or "").strip()

    @property
    def ok(self):
        return bool(self.url and self.key)

    def _post(self, path, obj, gzipped=True):
        """POST JSON, gzipped, and return (parsed body, http status).

        Returns (None, code) on anything that went wrong, including a socket
        that never opened - which reads as code 0. Never raises: the caller is
        a poller thread whose job is to try again in a minute.
        """
        body = json.dumps(obj, separators=(",", ":"), default=str).encode()
        headers = {"Content-Type": "application/json",
                   "X-Mirror-Key": self.key,
                   "User-Agent": USER_AGENT}
        if gzipped:
            body = gzip.compress(body, 6)
            headers["Content-Encoding"] = "gzip"
        req = urllib.request.Request(self.url + path, data=body,
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=ssl.create_default_context()) as res:
                raw = res.read()
                return (json.loads(raw.decode("utf-8")) if raw else None), res.status
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8")), e.code
            except Exception:
                return None, e.code
        except Exception:
            return None, 0

    def verify(self):
        """Ask the mirror whether this address and this push key work.

        Two questions, and they fail differently, so they are asked
        separately: `/api/status` is open and answers "is there a mirror at
        this address at all", `/api/ping` needs the key and answers "would a
        push be accepted". Told apart, a typo in the address and a wrong push
        key stop looking like the same silence.

        Returns the same `{state, detail}` shape as every client in
        sources.py - see `sources.verdict`.
        """
        if not self.url:
            return sources.verdict("unset", "no mirror address")
        status, code = self._get("/api/status")
        if not isinstance(status, dict):
            # A socket that never opened is not the same answer as a 404. One
            # is "this laptop has no internet", which is half of every Saturday
            # at a venue; the other is "there is no mirror at that address",
            # which is a typo somebody has to go and fix.
            return sources.verdict("down" if code == 0 else "bad", _explain(code, status))
        if not self.key:
            return sources.verdict("warn", "there is a mirror at that address, but no push "
                                           "key is saved here, so nothing is being sent")
        body, code = self._post("/api/ping", {})
        if code == 404:
            # A mirror deployed before /api/ping existed. The address is
            # right, and the key is the one thing still unproven.
            return sources.verdict("warn", "that mirror is older than this hub and cannot "
                                           "answer a key check - press PUSH NOW to test the "
                                           "push key for real")
        if not isinstance(body, dict) or not body.get("ok"):
            return sources.verdict("bad", _explain(code, body))
        last = body.get("lastReceivedAt")
        when = ("nothing pushed to it yet" if not last
                else "last received %d minute(s) ago" % max(0, int((time.time() - last) / 60)))
        return sources.verdict("ok", "address and push key both accepted; " + when
                               + ("" if body.get("locked") else
                                  " - WARNING: that mirror is serving with no view passcode"))

    def _get(self, path):
        req = urllib.request.Request(self.url + path,
                                     headers={"User-Agent": USER_AGENT}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT,
                                        context=ssl.create_default_context()) as res:
                raw = res.read()
                return (json.loads(raw.decode("utf-8")) if raw else None), res.status
        except urllib.error.HTTPError as e:
            return None, e.code
        except Exception:
            return None, 0

    def push(self, bundle):
        return self._post("/api/push", bundle)

    def push_photos(self, event_key, photos):
        return self._post("/api/photos", {"eventKey": event_key, "photos": photos},
                          gzipped=False)      # already-compressed image bytes


def _photo_payload(store, photo_id, team):
    mime, data = store.photo(photo_id)
    if not data:
        return None
    return {"photoId": photo_id, "team": team, "mime": mime,
            "data": base64.b64encode(data).decode("ascii")}


def push_once(hub, force=False):
    """One round trip. Returns the status dict the diagnostics panel shows.

    `force` sends the bundle even when nothing has changed - what the SAVE
    button on the setup page uses, so a lead who just typed a URL finds out
    immediately whether it works rather than within the minute.
    """
    m = hub.mirror()
    state = dict(hub.store.get("mirrorState") or {})
    if not m.ok:
        return {"ok": False, "reason": "not configured"}
    ek = hub.event_key()
    if not ek:
        return {"ok": False, "reason": "no event selected"}

    stale = time.time() - float(state.get("pushedAt") or 0) > HEARTBEAT_SECONDS
    version = hub.store.version_for(*BUNDLE_SCOPES)
    if (state.get("version") == version and state.get("event") == ek
            and not stale and not force):
        return {"ok": True, "skipped": "unchanged", "pushedAt": state.get("pushedAt"),
                "revision": state.get("revision")}

    bundle = build_bundle(hub, ek)
    unchanged = (state.get("digest") == bundle["digest"]
                 and state.get("event") == ek)
    if unchanged and not stale and not force:
        # The write counters moved but the bundle did not - a poll rewriting a
        # value with the same contents, typically. Record the version so the
        # cheap check above catches it next time.
        state["version"] = version
        hub.store.set("mirrorState", state)
        return {"ok": True, "skipped": "unchanged", "pushedAt": state.get("pushedAt"),
                "revision": state.get("revision")}

    body, code = m.push(bundle)
    if code != 200 or not isinstance(body, dict):
        state.update({"error": _explain(code, body), "erroredAt": time.time()})
        hub.store.set("mirrorState", state)
        return {"ok": False, "reason": state["error"], "status": code}

    sent = 0
    # The mirror names the photos it is missing; nothing else is worth the
    # uplink. A batch at a time, so the next push is never queued behind a
    # pit-scouting day's worth of camera images.
    want = [p for p in (body.get("wantPhotos") or []) if isinstance(p, str)][:PHOTO_BATCH]
    if want:
        by_id = {p["photoId"]: p.get("team") for p in bundle["photoIds"]}
        batch = [q for q in (_photo_payload(hub.store, pid, by_id.get(pid)) for pid in want) if q]
        if batch:
            pbody, pcode = m.push_photos(ek, batch)
            sent = (pbody or {}).get("stored", 0) if pcode == 200 else 0

    state.update({
        "event": ek, "digest": bundle["digest"], "version": version,
        "pushedAt": time.time(),
        "revision": body.get("revision"), "bytes": body.get("bytes"),
        "photosPending": max(0, int(body.get("photosMissing") or 0) - sent),
        "error": None, "erroredAt": None,
    })
    hub.store.set("mirrorState", state)
    return {"ok": True, "revision": state["revision"], "bytes": state["bytes"],
            "photosSent": sent, "photosPending": state["photosPending"],
            "pushedAt": state["pushedAt"], "url": m.url}


def _explain(code, body):
    """An HTTP code as something a scout lead can act on."""
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])[:200]
    return {
        0: "could not be reached - check the address, and that the laptop has internet",
        401: "the mirror rejected the push key",
        403: "the mirror rejected the push key",
        404: "no mirror at that address - check for a typo, and that it ends in the site root",
        413: "the mirror refused the bundle as too large",
    }.get(code, f"mirror answered HTTP {code}")
