#!/usr/bin/env python3
"""FRC 2026 REBUILT scouting server.

Python 3 standard library only - no pip install, nothing to build.  Runs the
same on macOS (testing) and Windows (competition).

    python3 server/hub.py [--port 6059] [--db data/scouting.db]
"""
import argparse
import base64
import csv
import gzip as _gzip
import hashlib
import hmac
import secrets
import io
import json
import math
import mimetypes
import os
import posixpath
import platform
import queue
import re
import socket
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai
import analytics
import discover
import envfile
import keys as keyhygiene
import lovat as lovat_report
import offsite
import rules
import solve
import sources
from store import Store

WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
WEB_ROOT = os.path.abspath(WEB_ROOT)

# Our team number. It is above 1024, so no admin rights are needed to bind it,
# and it is not a port some other tool on a borrowed laptop is likely to have
# taken already. --port still overrides it, and web/js/net.js has to agree.
PORT = 6059

# One nonce per run of the process, mixed into every ETag. The store's write
# counters live in memory and start again from zero, so without this a phone
# that slept through a hub restart would hold a tag that happens to match a
# quite different state and never ask for the real one again.
BOOT = uuid.uuid4().hex[:8]

NEXUS_POLL_SECONDS = 20
# The pit map, the pit list and inspection status change a handful of times a
# day between them - a pit map is fixed once the venue is set up - so they do
# not need the fast tick. The live status does, because `On field` is what arms
# the phones. So does the alliance table: during selection the picklist crosses
# teams off by itself as they are picked, and a board two minutes behind the
# room is worse than no board at all.
NEXUS_SLOW_POLL_SECONDS = 120
TBA_POLL_SECONDS = 45
# EPA is a season-long fit; it barely moves inside one event, so polling it
# hard buys nothing and Statbotics is the one source we expect to be down.
STATBOTICS_POLL_SECONDS = 600
# Only ever used to fill in matches TBA has not posted yet, so it can be slow.
FRC_EVENTS_POLL_SECONDS = 60
# Lovat rate-limits an API key to one request every three seconds. One request
# every five minutes pulls the whole tournament and leaves that limit alone
# even when a config save fires _poll_all at the same moment.
LOVAT_POLL_SECONDS = 300
# Outbound, to a site the team hosts - see server/offsite.py. Nothing is sent
# when the bundle has not changed, so this is a ceiling on how often the hub
# *could* speak, not on how much it says.
MIRROR_PUSH_SECONDS = offsite.PUSH_SECONDS
# A ceiling on generated answers per event, so a stuck button cannot quietly
# spend a team's API credit all afternoon. Raise it in Setup if you need to.
AI_CALL_CEILING = 250
# These budgets cover the model's reasoning as well as its answer - every model
# on the list thinks before it writes, out of the same allowance - so they are
# several times what the text itself needs. Too small does not truncate
# politely; it returns nothing at all.
AI_PANEL_TOKENS = 4000
AI_ASK_TOKENS = 3000
# What each polled endpoint reads, for its ETag. These have to be kept honest
# against the handlers below: naming a scope too many only costs a needless
# rebuild, naming one too few serves a client stale data behind a 304.
#
# Note what is deliberately absent from STATE_SCOPES and ANALYTICS_SCOPES:
# `kv:devices`, which hub.touch() writes once a minute per phone. Six phones
# would move a single global counter every ten seconds and there would be no
# 304s left to give.
STATE_SCOPES = ("events", "teams", "matches", "flags", "pit_entries",
                "kv:nexusLive", "kv:pits", "kv:pitMap", "kv:inspection",
                "kv:alliances", "kv:seats", "kv:matchClocks", "kv:clockFixes",
                "kv:rankings", "kv:epa", "kv:earlyScores")
ANALYTICS_SCOPES = ("matches", "teams", "scout_entries", "solved",
                    "kv:rankings", "kv:epa", "kv:lovat", "kv:multipliers")
SEATLOG_SCOPES = ("kv:seatLog",)
# `connected` on a crew row is read from the live SSE subscriber set, which no
# store write touches - hence the subscriber generation mixed in at the call.
# `kv:eventKey` because crew() reads it to find the entries to scan. Every tag
# already carries the event key on its own, so this is belt and braces - but a
# scope list that is complete on its own reading is worth more than one scope.
CREW_SCOPES = ("kv:seats", "kv:devices", "kv:eventKey", "scout_entries")

# How often an idle stream writes its comment frame. Every one of these wakes
# six phones' radios for a byte, all day. There is no proxy between a phone and
# a hub on the same LAN - the frame is only there so a stream that has gone
# quiet is noticed - so this is comfortably inside anything that would drop it.
SSE_KEEPALIVE_SECONDS = 45

# The whole event lives on one laptop that gets carried around a venue all day.
SNAPSHOT_SECONDS = 600
SNAPSHOT_KEEP = 12


# ------------------------------------------------------------------- hub

class Hub:
    def __init__(self, store):
        self.store = store
        self.subs = []
        self.subs_lock = threading.Lock()
        # Bumped whenever a stream opens or closes. /api/crew's `connected`
        # column is read from the live subscriber set, which no store write
        # touches, so this is the only thing that can invalidate its tag when a
        # phone drops off the wifi.
        self.subs_gen = 0
        self.statbotics = sources.Statbotics()
        self.last_nexus_at = 0.0
        self.last_nexus_live = None
        self.stop_flag = threading.Event()
        self.port = PORT
        self.status = {"nexus": None, "tba": None, "statbotics": None,
                       "frcEvents": None, "lovat": None, "mirror": None,
                       "lastUpdate": None}
        self.last_snapshot = None
        self.started_at = time.time()
        self._recal_lock = threading.Lock()
        self._recal_pending = False
        self.writes = []          # timestamps, for writes/min
        self.log = []             # ring buffer for the event log panel
        # Both of those are written from every request thread at once - six
        # phones flush at the buzzer - and read from another while the
        # diagnostics panel is open. Rebuilding a list and assigning it back is
        # not one step, so entries went missing and, on the log, a trim racing
        # an append could drop a line somebody was about to need.
        self._note_lock = threading.Lock()
        # The source clients that carry state - see _client() below.
        self._clients = {}
        self._clients_lock = threading.Lock()
        self._last_poll_all = 0.0
        # One push to the mirror at a time. The poller and the PUSH NOW button
        # are two threads, and a push is a whole event over a venue uplink.
        self._push_lock = threading.Lock()
        # apply_nexus_event is reached from the poller and from the webhook
        # handler, and it decides what is news by comparing against the last
        # payload it saw. Two threads inside that comparison at once is two
        # copies of the same broadcast, or a dropped update.
        self._nexus_lock = threading.Lock()

    WRITES_KEPT = 1000        # five minutes of writes is all writes/min needs

    def record_writes(self, n):
        """One timestamp per applied row, for the diagnostics panel's writes/min.

        Trimmed on the way in rather than only in diag(): a hub nobody has the
        dashboard open on still takes writes all day, and this was the one list
        here with no ceiling on it.
        """
        now = time.time()
        with self._note_lock:
            keep = [t for t in self.writes if now - t < 300]
            keep.extend([now] * max(0, min(int(n), self.WRITES_KEPT)))
            self.writes = keep[-self.WRITES_KEPT:]

    def note(self, level, msg):
        with self._note_lock:
            self.log.append({"at": time.time(), "level": level, "msg": msg})
            if len(self.log) > 300:
                del self.log[:100]

    def diag(self):
        now = time.time()
        with self._note_lock:
            self.writes = [t for t in self.writes if now - t < 300]
            writes = len(self.writes)
            log = list(reversed(self.log[-40:]))
        mem = None
        try:
            import resource
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux reports KB, macOS reports bytes
            mem = rss / (1024 * 1024) if rss > 10 ** 7 else rss / 1024
        except Exception:
            pass

        def svc(name, ok, detail, retrying=False):
            return {"name": name, "status": "RETRYING" if retrying else ("RUNNING" if ok else "IDLE"),
                    "detail": detail}

        nx, tba, fe = self.nexus(), self.tba(), self.frc_events()
        lv, ai_c = self.lovat(), self.ai()
        mrr, mst = self.mirror(), self.store.get("mirrorState") or {}
        age = lambda t: f"{int(now - t)}s ago" if t else "never"
        services = [
            svc("http + sse", True, f"{len(self.subs)} client(s) streaming"),
            svc("sqlite (wal)", True, os.path.basename(self.store.path)),
            svc("nexus poll", bool(nx.ok), age(self.status["nexus"]) if nx.ok else "no api key",
                bool(nx.ok) and not self.status["nexus"]),
            svc("tba poll", bool(tba.ok), age(self.status["tba"]) if tba.ok else "no api key",
                bool(tba.ok) and not self.status["tba"]),
            svc("frc events", bool(fe.ok), age(self.status["frcEvents"]) if fe.ok else "no api key",
                bool(fe.ok) and not self.status["frcEvents"]),
            # Statbotics needs no key, so "IDLE" would be a lie. It is allowed to
            # be down (sources.Statbotics backs off on its own) and the honest
            # report is which of those two states we are in.
            svc("statbotics", True,
                "unreachable, backing off" if self.statbotics.down_until > now
                else age(self.status["statbotics"]),
                not self.status["statbotics"]),
            svc("lovat", bool(lv.ok),
                ("rate limited, backing off" if lv.down_until > now
                 else age(self.status["lovat"])) if lv.ok else "no api key",
                bool(lv.ok) and not self.status["lovat"]),
            svc("ai", ai_c.ok, f"{ai_c.provider} · {ai_c.model} · {self.ai_calls()}"
                f"/{self.ai_ceiling()} answers this event" if ai_c.ok else "no provider set"),
            svc("solver", True, f"multipliers fitted from {self.store.get('multipliersFittedFrom') or 0} windows"),
            # The one service that is not a source: this is the hub talking
            # outwards. A mirror that is refusing pushes has to be visible
            # here, because nothing else on the hub gets worse when it breaks.
            svc("off-site mirror", mrr.ok,
                (mst.get("error") or
                 f"{age(mst.get('pushedAt'))}, rev {mst.get('revision')}"
                 + (f", {mst['photosPending']} photo(s) queued"
                    if mst.get("photosPending") else ""))
                if mrr.ok else "not configured",
                bool(mrr.ok) and bool(mst.get("error"))),
            svc("snapshots", True,
                f"last {age(self.last_snapshot)}, keeping {SNAPSHOT_KEEP}"
                if self.last_snapshot else f"every {SNAPSHOT_SECONDS // 60}m, none yet"),
        ]
        return {
            "host": socket.gethostname(),
            "platform": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "uptimeSec": int(now - self.started_at),
            "memoryMB": round(mem, 1) if mem else None,
            "writesPerMin": round(writes / 5.0, 1),
            "sseClients": len(self.subs),
            "services": services,
            # A short age rather than the default: this is the panel a lead
            # opens precisely when the laptop has moved to another network, so
            # it must not be showing them where it used to be. Still enough to
            # collapse a tab left open and polling.
            "addresses": discover.urls(self.port, max_age=30.0),
            "seats": self.seats(),
            "log": log,
        }

    # --------------------------------------------------------- settings
    def cfg(self, k, d=None):
        return self.store.get(k, d)

    def event_key(self):
        return self.cfg("eventKey")

    def tba(self):
        return sources.TBA(self.cfg("tbaKey"))

    def nexus(self):
        return sources.Nexus(self.cfg("nexusKey"))

    def frc_events(self):
        return sources.FRCEvents(self.cfg("frcEventsUser"), self.cfg("frcEventsToken"))

    # The two clients that remember something between calls, and so are the two
    # that cannot be rebuilt per call.
    #
    # `Lovat.down_until` and `Client.down_until` are how this hub honours a
    # vendor saying stop: Lovat allows one request every three seconds and 403s
    # a team that is not verified, and an AI key that was just rejected will be
    # rejected again. Both set that field on themselves - and both were handed
    # out fresh from here on every single call, so the field was written onto an
    # object that was thrown away on the next line. Nothing ever backed off:
    # every press of TEST KEYS and every save (which fires _poll_all) went
    # straight back at a vendor that had just refused us, and the diagnostics
    # panel's "rate limited, backing off" line could never appear, because it
    # reads `down_until` off a client that was one millisecond old.
    #
    # Cached by the credentials they were built from, so a key edited on the
    # Setup page still takes effect on the next call - and clears the backoff
    # with it, which is what somebody fixing a wrong key means by fixing it.
    def _client(self, name, sig, build):
        with self._clients_lock:
            have = self._clients.get(name)
            if have is None or have[0] != sig:
                have = (sig, build())
                self._clients[name] = have
            return have[1]

    def lovat(self):
        key = self.cfg("lovatKey")
        return self._client("lovat", key, lambda: sources.Lovat(key))

    def ai(self):
        sig = (self.cfg("aiProvider"), self.cfg("aiKey"), self.cfg("aiModel"))
        return self._client("ai", sig, lambda: ai.client(self.cfg))

    def mirror(self):
        return offsite.Mirror(self.cfg("mirrorUrl"), self.cfg("mirrorKey"))

    def verify_keys(self):
        """Ask every vendor whether the key saved here actually works.

        This is what the TEST KEYS button on the Setup page is for, and it is
        the only thing in the app that can tell a lead the difference between
        the two states that look identical everywhere else: a key that is
        wrong, and a service with nothing to say yet.  `SET` on that page has
        only ever meant "a string is stored".

        Five vendors at up to twenty seconds each is over a minute in a row, so
        they go at once - a lead standing at the laptop pressing a button is
        the one caller in this file allowed to be in a hurry.
        """
        ek = self.event_key()
        season, _ = _split_event_key(ek)
        checks = {
            "tba": lambda: self.tba().verify(),
            "nexus": lambda: self.nexus().verify(ek),
            "frcEvents": lambda: self.frc_events().verify(int(season) if season else 2026),
            "lovat": lambda: self.lovat().verify(ek),
            "ai": lambda: self.ai().verify(),
            # The mirror is the only one of these that sends anything out
            # rather than fetching, so "does the key work" is worth knowing
            # before an event rather than after one - a push that is failing
            # has no symptom at the venue at all, because everything at the
            # venue keeps working.
            "mirror": lambda: self.mirror().verify(),
        }
        out = {}

        def run(name, fn):
            try:
                out[name] = fn()
            except Exception as e:
                # A vendor is allowed to answer with anything at all, including
                # something that breaks the client parsing it. That is still an
                # answer about them, not a reason to 500 the button.
                out[name] = sources.verdict("down", f"the check itself failed: {e}")

        threads = [threading.Thread(target=run, args=(n, f), daemon=True)
                   for n, f in checks.items()]
        for t in threads:
            t.start()
        # One deadline for all five, not thirty seconds each in turn - which is
        # two and a half minutes of a button that looks stuck.
        deadline = time.time() + 30
        for t in threads:
            t.join(timeout=max(0.1, deadline - time.time()))
        for name in checks:
            out.setdefault(name, sources.verdict("down", "timed out waiting for an answer"))
        self.note("info", "api keys tested: "
                          + ", ".join(f"{n} {v['state']}" for n, v in sorted(out.items())))
        return {"eventKey": ek, "at": time.time(), "checked": out}

    def ai_ceiling(self):
        try:
            return max(0, int(self.cfg("aiCallLimit") or AI_CALL_CEILING))
        except (TypeError, ValueError):
            return AI_CALL_CEILING

    def ai_calls(self):
        return int(self.store.get("aiCalls") or 0)

    def ai_charge(self):
        """Count one generated answer. False once the event ceiling is reached."""
        limit = self.ai_ceiling()

        def apply(n):
            n = int(n or 0)
            if n >= limit:
                return None, False
            return n + 1, True
        return self.store.mutate("aiCalls", apply, 0)

    def csv_text(self, ek, table):
        """One of the dashboard's CSV exports, as text.

        The mirror carries these verbatim rather than building its own, so a
        column can never mean one thing on the hub and another off-site.
        """
        header, rows = _csv_table(self, ek, table)
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(_csv_safe(r) for r in rows)
        return buf.getvalue()

    def poll_mirror(self):
        """Push the event off-site. Never the reason anything else fails.

        Sits in the poller with the five inbound sources, and is the only one
        that goes the other way. push_once() swallows its own failures and
        records them; this only has to keep the status line honest.
        """
        if not self.mirror().ok:
            return
        res = offsite.push_once(self)
        if res.get("skipped") == "already pushing":
            return
        if res.get("ok"):
            self.status["mirror"] = time.time()
        elif res.get("reason"):
            self.note("warn", f"mirror push failed: {res['reason']}")

    # ------------------------------------------------------------- SSE
    def subscribe(self, who=None):
        q = queue.Queue(maxsize=64)
        q.who = who or {}
        q.since = time.time()
        with self.subs_lock:
            self.subs.append(q)
            self.subs_gen += 1
        if who and who.get("scoutId"):
            self.touch(who, "connected")
        return q

    def unsubscribe(self, q):
        with self.subs_lock:
            if q in self.subs:
                self.subs.remove(q)
                self.subs_gen += 1

    def touch(self, who, what):
        """Record that a device is alive. This is what tells the lead who to
        go and talk to, so it is kept per-device rather than per-scout."""
        if not who or not who.get("deviceId"):
            return
        def apply(devs):
            devs = dict(devs or {})
            d = dict(devs.get(who["deviceId"], {}))
            d.update({"deviceId": who["deviceId"], "at": time.time(), "what": what})
            for k in ("scoutId", "seat"):
                if who.get(k):
                    d[k] = who[k]
            devs[who["deviceId"]] = d
            # forget phones that have been gone for a whole day
            cutoff = time.time() - 12 * 3600
            return {k: v for k, v in devs.items() if v.get("at", 0) > cutoff}, None
        self.store.mutate("devices", apply, {})
        # Hearing from the phone is also the chair reporting in - see keep_seat_warm.
        self.keep_seat_warm(who["deviceId"])

    def crew(self):
        """One row per station: who is on it, are they live, are they behind.

        Instants, not ages. The dashboard subtracts them itself, so the board
        keeps counting up with no network at all and two identical polls a
        second apart really are identical - which is what lets this endpoint
        answer 304 instead of re-scanning every scouting row at the event.
        """
        devs = self.store.get("devices") or {}
        seats = self.seats()
        with self.subs_lock:
            live = {q.who.get("deviceId") for q in self.subs
                    if getattr(q, "who", None) and q.who.get("deviceId")}

        ek = self.event_key()
        entries = self.store.scout_entries(ek) if ek else []
        last_by_scout = {}
        for e in entries:
            sid = e.get("scoutId")
            if sid and e["updatedAt"] > last_by_scout.get(sid, (0, None))[0]:
                last_by_scout[sid] = (e["updatedAt"], e["matchKey"])

        rows = []
        for key in ("red1", "red2", "red3", "blue1", "blue2", "blue3"):
            claim = seats.get(key) or {}
            dev = devs.get(claim.get("deviceId")) or {}
            sid = claim.get("scoutId")
            last = last_by_scout.get(sid)
            rows.append({
                "seat": key,
                "scoutId": sid,
                "deviceId": claim.get("deviceId"),
                "connected": bool(claim.get("deviceId")) and claim["deviceId"] in live,
                "lastSeenAt": dev.get("at"),
                "lastMatch": last[1] if last else None,
                "lastMatchAt": last[0] if last else None,
            })
        return rows

    def broadcast(self, kind, payload):
        msg = json.dumps({"type": kind, "data": payload, "at": time.time()})
        with self.subs_lock:
            targets = list(self.subs)
        for q in targets:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass  # a wedged client must not stall the others

    # --------------------------------------------------------- ingest
    def apply_nexus_event(self, payload):
        """Nexus live event status, from push or poll.  Ordering guarded by dataAsOfTime.

        One thread at a time. Two reach this - the poller every twenty seconds,
        and the webhook handler whenever Nexus pushes - and everything that
        decides whether this payload is news (`last_nexus_at`, and the compare
        against `last_nexus_live` at the end) is read and then written. Two
        threads inside that is two copies of the same broadcast to every phone
        in the building, or an update dropped because the other thread had
        already moved the clock past it.
        """
        if not payload:
            return False
        with self._nexus_lock:
            return self._apply_nexus_event(payload)

    def _apply_nexus_event(self, payload):
        try:
            as_of = float(payload.get("dataAsOfTime") or 0)
        except (TypeError, ValueError):
            as_of = 0.0
        if as_of and as_of <= self.last_nexus_at:
            return False  # Nexus warns updates can arrive out of order
        self.last_nexus_at = as_of or time.time()

        ek = payload.get("eventKey") or self.event_key()
        if not ek:
            return False
        self.store.put_event(ek)
        for i, m in enumerate(payload.get("matches") or []):
            label = m.get("label")
            if not label:
                continue
            red = [_int(t) for t in (m.get("redTeams") or [])]
            blue = [_int(t) for t in (m.get("blueTeams") or [])]
            # A qualification's number IS its place in the schedule. This used
            # to take the index within the payload, which is only the same
            # thing when the payload is the whole schedule - a live feed
            # carrying just the next few matches renumbered them from zero and
            # sent them to the front of everyone's schedule. Measured: pushing
            # Q14-Q16 reordered a 20-match event to Q14 Q1 Q15 Q16 Q2 Q3, which
            # is what pickCurrentMatch reads to tell a scout which robot to
            # watch. Playoffs have no number of their own, so they keep the
            # index, offset to sort after every qual.
            qual = _QUAL_LABEL.match(str(label))
            self.store.put_match(
                ek, resolve_match_key(self.store, ek, label, red, blue),
                label=label, play_order=int(qual.group(1)) if qual else 10000 + i,
                red=red, blue=blue,
                status=m.get("status"), times=m.get("times"))
        self.store.set("nexusLive", {
            "nowQueuing": payload.get("nowQueuing"),
            "announcements": payload.get("announcements") or [],
            "partsRequests": payload.get("partsRequests") or [],
            "dataAsOfTime": as_of,
        })
        self.status["nexus"] = time.time()
        # Only when something actually moved. `dataAsOfTime` above is Nexus's
        # own clock and advances on every poll, so it orders updates but says
        # nothing about whether they carry news - and this broadcast used to
        # fire every twenty seconds regardless. Each one costs six fetches and a
        # full DOM rebuild on every dashboard, a whole /api/state plus a ~60KB
        # IndexedDB rewrite on every phone, and a complete SVG rebuild on the
        # pit tablet. The four sibling payloads in poll_nexus_slow below have
        # always been guarded this way; this one was the omission.
        live = {
            "nowQueuing": payload.get("nowQueuing"),
            "matches": payload.get("matches") or [],
            "announcements": payload.get("announcements") or [],
            "partsRequests": payload.get("partsRequests") or [],
        }
        if live == self.last_nexus_live:
            return True
        self.last_nexus_live = live
        self.broadcast("nexus", live)
        return True

    def poll_nexus(self):
        """Live event status and the alliance table - the two that must be fast.

        `On field` is what opens the match screen on six phones, so a slower
        tick here is directly a later arm. Alliances are here for the other
        end of the event: during selection the picklist crosses teams off as
        they are picked, and that is a twenty-minute window where being two
        minutes stale is being wrong. The three in poll_nexus_slow below are
        genuinely not like that.
        """
        ek = self.event_key()
        nx = self.nexus()
        if not (ek and nx.ok):
            return
        self.apply_nexus_event(nx.event(ek))
        self._nexus_side("alliances", nx.alliances, ek)

    def poll_nexus_slow(self):
        """Pit map, pit list, inspection status.

        A pit map is fixed once the venue is set up and the pit list barely
        moves after Thursday. Asking for these alongside the status call every
        twenty seconds was three wasted round trips in five, on a laptop running
        off its battery next to the field.
        """
        ek = self.event_key()
        nx = self.nexus()
        if not (ek and nx.ok):
            return
        for name, fn in (("pits", nx.pits), ("pitMap", nx.pit_map),
                         ("inspection", nx.inspection)):
            self._nexus_side(name, fn, ek)

    def _nexus_side(self, name, fn, ek):
        """One of Nexus's per-event side payloads, stored and pushed on change."""
        data = fn(ek)
        if data is None:
            # 404 (no pit map at this event) or a transient failure: keep what
            # we have for THIS event rather than blanking the screen.
            return
        key = f"{name}:{ek}"
        if self.store.get(key) != data:
            self.store.set(key, data)
            self.broadcast(name, data)

    def event_data(self, name, ek, default=None):
        """Per-event cached payload. Never falls back to another event's data."""
        v = self.store.get(f"{name}:{ek}")
        return default if v is None else v

    # kept as the name the Nexus call sites read better with
    nexus_data = event_data

    def poll_tba(self):
        ek = self.event_key()
        tba = self.tba()
        if not (ek and tba.ok):
            return
        ms = tba.event_matches(ek)
        if ms is None:
            return
        self.status["tba"] = time.time()
        changed = []
        for m in ms:
            mk = m.get("key")
            if not mk:
                continue
            bd = sources.parse_breakdown_2026(m) if m.get("score_breakdown") else None
            self.store.put_match(
                ek, mk, label=_tba_label(m), comp_level=m.get("comp_level"),
                match_number=m.get("match_number"),
                times={"actual": m.get("actual_time"), "scheduled": m.get("time"),
                       "predicted": m.get("predicted_time")},
                red=[_int(t) for t in (m.get("alliances", {}).get("red", {}).get("team_keys") or [])],
                blue=[_int(t) for t in (m.get("alliances", {}).get("blue", {}).get("team_keys") or [])],
                breakdown=bd)
            if bd:
                changed.append(mk)
        for mk in changed:
            self.solve_match(mk)
        if changed:
            self.request_recalibrate()
            self.broadcast("results", {"matches": changed})

        teams = tba.event_teams(ek)
        if teams:
            self.store.put_teams(ek, [{"team": _int(t.get("key")), "name": t.get("nickname")}
                                      for t in teams if t.get("key")])

        self.poll_rankings(ek, tba)

    def poll_rankings(self, ek, tba):
        """Official standings and OPR.

        Both are exact - they come straight off TBA - so they belong next to the
        climb numbers on the picklist, not next to the estimated fuel. Folded
        into the TBA poll because it shares the same ETag cache.
        """
        merged = {}
        rankings = tba.event_rankings(ek)
        for r in ((rankings or {}).get("rankings") or []):
            team = _int(r.get("team_key"))
            if team is None:
                continue
            rec = r.get("record") or {}
            merged[team] = {
                "rank": r.get("rank"),
                "rankingPoints": _round(r.get("sort_orders", [None])[0]
                                        if r.get("sort_orders") else None),
                "wins": rec.get("wins"), "losses": rec.get("losses"), "ties": rec.get("ties"),
                "played": r.get("matches_played"),
            }
        oprs = (tba.event_oprs(ek) or {}).get("oprs") or {}
        for key, v in oprs.items():
            team = _int(key)
            if team is not None:
                merged.setdefault(team, {})["opr"] = _round(v)
        if not merged:
            return
        cache = f"rankings:{ek}"
        if self.store.get(cache) != merged:
            self.store.set(cache, merged)
            self.broadcast("rankings", {"teams": len(merged)})

    def poll_statbotics(self):
        """EPA, the one outside number that corroborates our fuel estimate.

        Statbotics needs no key and is allowed to be down - sources.Statbotics
        backs off on its own - so a failure here must never surface as an error.
        """
        ek = self.event_key()
        if not ek:
            return
        rows = self.statbotics.team_events(ek)
        if rows is None:
            return
        self.status["statbotics"] = time.time()
        out = {}
        for r in rows:
            team = _int(r.get("team"))
            if team is None:
                continue
            epa = r.get("epa") or {}
            bd = (epa.get("breakdown") or {}) if isinstance(epa, dict) else {}
            out[team] = {
                "epa": _round(_nested(epa, "total_points", "mean")),
                "auto": _round(_first(bd.get("auto_points"))),
                "teleop": _round(_first(bd.get("teleop_points"))),
                "endgame": _round(_first(bd.get("endgame_points"))),
                "rank": (r.get("record") or {}).get("season_rank") if isinstance(r.get("record"), dict) else None,
            }
        cache = f"epa:{ek}"
        if self.store.get(cache) != out:
            self.store.set(cache, out)
            self.broadcast("epa", {"teams": len(out)})

    def poll_lovat(self):
        """Other teams' scouting, pulled whole in one request.

        Lovat's export is scoped to what our account's team-source rule allows,
        so a short list is a setting on their side, not a failure on ours.  A
        parse that comes back empty is still written: "Lovat has nothing for
        this event" is an answer, and it must not leave yesterday's rows on
        screen.
        """
        ek = self.event_key()
        lv = self.lovat()
        if not (ek and lv.ok):
            return
        text = lv.report_csv(ek)
        if text is None:
            return
        teams = lovat_report.parse_report_csv(text, ek)
        if teams is None:
            self.note("error", "lovat export could not be parsed")
            return
        self.status["lovat"] = time.time()
        out = {str(t): rec for t, rec in teams.items()}
        cache = f"lovat:{ek}"
        if self.store.get(cache) != out:
            self.store.set(cache, out)
            self.broadcast("lovat", {"teams": len(out)})

    def poll_frc_events(self):
        """Post the official result before TBA has caught up.

        Deliberately limited: this records the alliance totals and marks the
        match played, and nothing else. It does NOT synthesise a score
        breakdown. `sources.parse_breakdown_2026` is written against TBA's
        published schema, the per-window fuel counts are what the solver
        divides between three robots, and guessing at the equivalent FRC Events
        field names would put invented numbers into the fuel pipeline. TBA stays
        the only source the solver trusts - clock_offset() needs its actual_time
        anyway - so this just closes the few minutes between the buzzer and TBA.
        """
        ek = self.event_key()
        fe = self.frc_events()
        if not (ek and fe.ok):
            return
        season, code = _split_event_key(ek)
        if not season:
            return
        payload = fe.scores(season, code, "qual")
        if payload is None:
            return
        self.status["frcEvents"] = time.time()
        have = {m["matchKey"] for m in self.store.matches(ek) if not m.get("breakdown")}
        early = dict(self.event_data("earlyScores", ek, None) or {})
        added = []
        for row in (payload.get("MatchScores") or []):
            num = row.get("matchNumber")
            if num is None:
                continue
            mk = f"{ek}_qm{num}"
            if mk not in have or mk in early:
                continue          # unknown, or TBA already answered it
            totals = {}
            for a in (row.get("alliances") or []):
                side = str(a.get("alliance") or "").lower()
                if side in ("red", "blue") and a.get("totalPoints") is not None:
                    totals[side] = a["totalPoints"]
            if len(totals) != 2:
                continue
            early[mk] = {**totals, "at": time.time()}
            added.append(mk)
        if added:
            self.store.set(f"earlyScores:{ek}", early)
            self.note("info", f"frc events posted {len(added)} result(s) ahead of tba")
            self.broadcast("earlyScores", {"matches": added})

    # -------------------------------------------------------- shared codes
    #
    # Shared passcodes, not per-person accounts and not IP allow-listing.
    # Nothing about who someone is gets stored - only whether they know the
    # code - and the lead can rotate one at any point.
    #
    # There are two, and they answer different questions.  The **strategy**
    # passcode is about the picklist and who may see per-scout numbers; it is
    # shared with the strategy table and lasts the competition day.  The
    # **admin** code is about this hub's settings - the event it is pointed at,
    # the API keys, the passcode itself - and it is the one that stops a hub
    # being changed by somebody leaning on the laptop between matches.
    #
    # Both work the same way underneath, so it is written once.

    #: The strategy passcode lasts the competition day; a lead types it at
    #: breakfast and is not asked again.  Admin is minutes, renewed on use:
    #: settings are edited in a burst and then left alone, and a Setup page
    #: left open on the scoring table is exactly the accident being prevented.
    STRATEGY_HOURS = 16
    ADMIN_MINUTES = 20

    def _set_code(self, row, tokens_row, code):
        if not code:
            self.store.set(row, None)
            self.store.set(tokens_row, {})
            return
        salt = secrets.token_hex(8)
        digest = hashlib.sha256((salt + code).encode()).hexdigest()
        self.store.set(row, {"salt": salt, "hash": digest})
        self.store.set(tokens_row, {})          # a new code invalidates old ones

    def _check_code(self, row, code):
        rec = self.store.get(row)
        if not rec:
            return True                          # no code configured: open
        want = rec["hash"]
        got = hashlib.sha256((rec["salt"] + (code or "")).encode()).hexdigest()
        return hmac.compare_digest(want, got)

    def _issue(self, tokens_row, seconds):
        tok = secrets.token_urlsafe(18)

        def apply(toks):
            toks = dict(toks or {})
            toks[tok] = time.time() + seconds
            return {k: v for k, v in toks.items() if v > time.time()}, None
        self.store.mutate(tokens_row, apply, {})
        return tok

    def _token_ok(self, tokens_row, tok, renew=0):
        toks = self.store.get(tokens_row) or {}
        exp = toks.get(tok or "")
        if not (exp and exp > time.time()):
            return False
        if renew:
            # Renewed on use, so a lead part-way through typing eight keys is
            # never locked out mid-save, and a panel nobody has touched still
            # closes itself.
            def apply(cur):
                cur = dict(cur or {})
                if tok in cur:
                    cur[tok] = time.time() + renew
                return {k: v for k, v in cur.items() if v > time.time()}, None
            self.store.mutate(tokens_row, apply, {})
        return True

    def _drop_token(self, tokens_row, tok):
        self.store.mutate(tokens_row,
                          lambda cur: ({k: v for k, v in (cur or {}).items()
                                        if k != tok and v > time.time()}, None), {})

    # ----------------------------------------------------------- picklist

    def set_pin(self, pin):
        self._set_code("strategyPin", "unlockTokens", pin)

    def pin_set(self):
        return bool(self.store.get("strategyPin"))

    def check_pin(self, pin):
        return self._check_code("strategyPin", pin)

    def issue_token(self):
        return self._issue("unlockTokens", self.STRATEGY_HOURS * 3600)

    def token_ok(self, tok):
        if not self.pin_set():
            return True
        return self._token_ok("unlockTokens", tok)

    # -------------------------------------------------------------- admin
    #
    # The admin password is not stored here at all: it lives in `.env`, read at
    # startup, and this hub only ever compares against it.  One place to set
    # it, one place to look when it does not work, and a password that never
    # goes into the database that gets copied to a mirror and exported to JSON.

    def admin_password(self):
        return envfile.admin_password()

    def admin_set(self):
        """Whether unlocking needs a password - including when it needs one
        nobody can supply, because the value in `.env` is unusable."""
        password, problem = self.admin_password()
        return bool(password or problem)

    def check_admin(self, code):
        password, problem = self.admin_password()
        if problem:
            return False           # a broken password locks, it does not open
        if not password:
            return True            # none set: the panel's own lock is the guard
        # Both sides trimmed, not just the one typed in. A password set with a
        # space on the end - which `--set-admin-password` accepts, and which a
        # password manager or a paste puts there - was stored with the space
        # and compared against a value that had just had it taken off, so it
        # could never be entered again by anybody, including the person who
        # chose it.
        return hmac.compare_digest(password.strip(), (code or "").strip())

    def issue_admin_token(self):
        return self._issue("adminTokens", self.ADMIN_MINUTES * 60)

    def admin_token_ok(self, tok):
        """Whether this request may change settings.

        Open when no password is set, the same call the picklist makes: a team
        that has not set one up must not find its own hub locked. The panel
        still opens locked either way - the lock is what stops an accident, and
        the password is what stops somebody else.
        """
        if not self.admin_set():
            return True
        return self._token_ok("adminTokens", tok, renew=self.ADMIN_MINUTES * 60)

    def end_admin(self, tok):
        self._drop_token("adminTokens", tok)

    #: Every field of the board, so a hub that has never been edited answers
    #: the same shape as one that has.
    PICKLIST_BASE = {"weights": {}, "weights2": {}, "dnp": [], "order": [],
                     "order2": [], "rev": 0}

    def picklist(self):
        # Two lists, because alliance selection asks two different questions:
        # the best robot left, and the best complement to the one we have.
        return {**self.PICKLIST_BASE, **(self.store.get("picklist") or {})}

    # ------------------------------------------------------- match clock
    def start_match(self, match_key, scout_id, client_now=None):
        """One shared match clock for everyone watching this match.

        Measured: an offset shared by all three scouts on an alliance costs
        almost nothing (19.6% -> 19.1% at 5s), because the solver only needs
        them to agree with each other. Scouts each starting their own clock is
        what hurts (23.6% at 2s, 32.4% at 5s).

        So the first tap wins and everyone else adopts it. Nexus cannot supply
        this - it is a queueing tool driven by volunteers, not an FMS feed, so
        'On field' precedes the real start by an unknown amount.
        """
        def apply(clocks):
            clocks = dict(clocks or {})
            existing = clocks.get(match_key)
            if existing:
                return None, (existing, False)   # first tap wins; never restart under anyone
            rec = {"matchKey": match_key, "startedAt": time.time(), "by": scout_id}
            clocks[match_key] = rec
            # keep this small; only the last handful of matches can still be live
            if len(clocks) > 12:
                for k in sorted(clocks, key=lambda k: clocks[k]["startedAt"])[:-12]:
                    del clocks[k]
            return clocks, (rec, True)
        rec, started = self.store.mutate("matchClocks", apply, {})
        if started:
            self.note("info", f"match clock started for {match_key} by {scout_id}")
            self.broadcast("matchStart", rec)
        return rec

    def match_clock(self, match_key):
        return (self.store.get("matchClocks") or {}).get(match_key)

    # ------------------------------------------------------------- seats
    STATIONS = ("red1", "red2", "red3", "blue1", "blue2", "blue3")
    SEAT_TTL = 3 * 3600            # seconds a chair survives with nobody reporting
    # How stale a claim has to look before a phone reporting in rewrites its
    # timestamp. It used to be 60s, which against a three-hour TTL is about a
    # hundred and eighty times more often than the job needs - and every one of
    # those writes moved the `seats` counter, which is what /api/state hangs its
    # ETag on, so six phones alone kept a dashboard rebuilding the whole event
    # six times a minute. Ten minutes still leaves a wide margin.
    SEAT_WARM_SECONDS = 600

    @staticmethod
    def normalize_seat(seat):
        """`red2` from whatever was sent, or None if it is not a real station."""
        s = str(seat or "").strip().lower()
        return s if s in Hub.STATIONS else None

    @classmethod
    def seat_key(cls, alliance, station):
        """One canonical key from an alliance and a station number.

        Unvalidated input used to be pasted straight into the key, so a phone
        that posted `station: 0` - or none at all - left a `red0` chair sitting
        in the map. Nothing on the crew board can show a chair that is not one
        of the six, so nothing could free it either, and the SEATS tab counted
        it: "7 of 6 stations claimed".
        """
        try:
            n = int(str(station).strip())
        except (TypeError, ValueError):
            return None
        return cls.normalize_seat(f"{str(alliance or '').strip().lower()}{n}")

    def _live_seats(self, seats):
        cutoff = time.time() - self.SEAT_TTL
        return {k: v for k, v in (seats or {}).items()
                if k in self.STATIONS and (v or {}).get("at", 0) > cutoff}

    def seat_history(self, limit=12):
        return (self.store.get("seatLog") or [])[-limit:][::-1]

    def _log_seat(self, entry):
        self.store.mutate("seatLog", lambda log: ((list(log or []) + [entry])[-60:], None), [])

    def claim_seat(self, alliance, station, scout_id, device_id):
        """Record who is sitting where. Returns the full seat map, or None if
        the claim was not one a scout could have made.

        Not enforced - a scout who really is in that chair must always win.
        The point is that the phone can SHOW the clash before it costs a match.
        """
        key = self.seat_key(alliance, station)
        scout_id = str(scout_id or "").strip().upper()[:4]
        device_id = str(device_id or "").strip()
        if not key or not scout_id or not device_id:
            return None

        def apply(seats):
            seats = self._live_seats(seats)
            prev = seats.get(key)
            seats[key] = {"scoutId": scout_id, "deviceId": device_id, "at": time.time()}
            # one device sits in exactly one seat
            vacated = []
            for k, v in list(seats.items()):
                if k != key and v.get("deviceId") == device_id:
                    vacated.append(k)
                    del seats[k]
            return seats, (seats, prev, vacated)
        seats, prev, vacated = self.store.mutate("seats", apply, {})

        self.touch({"deviceId": device_id, "scoutId": scout_id, "seat": key}, "seated")

        # Phones re-assert the chair they are already sitting in whenever they
        # find the hub again, so only a real change is news. Logging every
        # re-assert filled the lead's swap list with "AK -> AK".
        if (prev and prev.get("scoutId") == scout_id
                and prev.get("deviceId") == device_id and not vacated):
            return seats

        # A phone that has just been displaced must be told, or two scouts keep
        # logging the same robot and neither knows.
        displaced = None
        if prev and prev.get("deviceId") and prev["deviceId"] != device_id:
            displaced = prev["deviceId"]

        self._log_seat({"at": time.time(), "seat": key, "scoutId": scout_id,
                        "from": (prev or {}).get("scoutId"), "vacated": vacated})
        self.note("info", f"{scout_id} took {key}"
                          + (f" from {prev['scoutId']}" if prev and prev.get("scoutId") else ""))

        self.broadcast("seats", {"seats": seats, "displaced": displaced, "seat": key,
                                 "scoutId": scout_id})
        return seats

    def free_seat(self, key, device_id=None):
        """Release a chair from the crew board. Returns the full seat map.

        `device_id` is the phone the lead was looking at when they clicked. The
        click frees that phone or nobody, so a FREE aimed at the scout who
        walked off cannot land on the one who has just sat down in their place.
        """
        def apply(seats):
            seats = self._live_seats(seats)
            prev = seats.get(key)
            if not prev or (device_id and prev.get("deviceId") != device_id):
                return seats, (seats, None)
            del seats[key]
            return seats, (seats, prev)
        seats, prev = self.store.mutate("seats", apply, {})
        if not prev:
            return seats

        self._log_seat({"at": time.time(), "seat": key, "scoutId": None,
                        "from": prev.get("scoutId"), "freed": True, "vacated": []})
        self.note("info", f"{prev.get('scoutId') or 'someone'} was freed from {key}")
        # Same envelope as a claim, and it names the phone that has to stop.
        # Freeing a chair used to be silent: that phone kept scouting, the lead
        # saw an empty station and sat someone else on the same robot, which is
        # exactly the double-scouting the bump screen exists to prevent.
        self.broadcast("seats", {"seats": seats, "displaced": prev.get("deviceId"),
                                 "seat": key, "scoutId": None, "freed": True})
        return seats

    def keep_seat_warm(self, device_id):
        """A chair belongs to whoever is sitting in it, for as long as they are.

        `seats()` ages claims out, but `at` was only ever written at the moment
        of the claim. A scout who sat down at nine and never opened the seat
        screen again dropped off the crew board three hours later, and the lead
        was told six robots were unwatched while six people watched them.

        Note this is the chair's clock, not the phone's. How recently the phone
        itself was heard from is `devices`, which touch() writes, and that is
        what the crew board's LAST HEARD column reads.
        """
        now = time.time()

        def apply(seats):
            seats = self._live_seats(seats)
            touched = False
            for k, v in list(seats.items()):
                if (v.get("deviceId") == device_id
                        and now - v.get("at", 0) > self.SEAT_WARM_SECONDS):
                    seats[k] = {**v, "at": now}
                    touched = True
            return (seats if touched else None), None
        self.store.mutate("seats", apply, {})

    def seats(self):
        """The chairs that are still held.  A read, and only a read.

        Two reasons it must not write. store.mutate takes the SQLite write lock
        before it can read (BEGIN IMMEDIATE, and it must - see the note there),
        and this is on the read path of /api/seats, /api/state and /api/diag, so
        a dashboard refresh was taking the write lock three times a cycle and
        queueing behind six phones flushing at the buzzer.

        The second reason is subtler and was a real bug: /api/state takes its
        ETag before building the payload, precisely so an unchanged poll can
        skip the work. Expiring a chair here wrote to `seats` *during* that
        build - moving the counter the tag had just been derived from - so the
        tag handed to the client was stale the instant it was issued and the
        next conditional request got a 200 it did not need. Nothing served was
        ever wrong, but the 304 was unreliable, which is the whole feature.

        Dropping the write costs nothing: an expired chair is filtered out of
        every read, and the filtered map is persisted by the next thing that
        genuinely writes - claim_seat, free_seat and keep_seat_warm all run
        _live_seats inside their own mutate.
        """
        return self._live_seats(self.store.get("seats") or {})

    # ---------------------------------------------------------- solving
    # Beyond this it is not a late tap. It used to be a flat 180s, which is
    # longer than a match: an offset that large shifts every observation past
    # the final buzzer, every interval loses its window, and the solver falls
    # back to an even split - the fabricated 40/40/40 the solver tests exist to
    # catch, reported with no flag on it. A correction cannot be longer than
    # the thing it is correcting.
    CLOCK_FIX_LIMIT = rules.MATCH_SECONDS

    def clock_offset(self, m):
        """How far the scouts' shared clock was from the real match start.

        TBA publishes `actual_time` from FMS once results post. It cannot drive
        a live timer - it does not exist until the match is over - but it lets
        us re-anchor afterwards, which is better: the scouts never have to be
        precise, and the data corrects itself.
        """
        actual = ((m.get("times") or {}) or {}).get("actual")
        rec = self.match_clock(m["matchKey"])
        if not actual or not rec or not rec.get("startedAt"):
            return None
        off = rec["startedAt"] - float(actual)
        if abs(off) > self.CLOCK_FIX_LIMIT:
            self.store.flag(self.event_key(), m["matchKey"], "clock-offset",
                            f"scout clock was {off:+.0f}s from the official start — not applied")
            return None
        return off

    def _rephase(self, intervals, offset):
        """Re-attribute intervals to windows using the corrected timeline.

        The scouts' raw observations are never mutated; correction happens at
        solve time so it can be redone if TBA revises the match.
        """
        out, lost = [], 0
        for iv in intervals or []:
            try:
                start = float(iv["start"]) + offset
                end = float(iv.get("end", iv["start"])) + offset
            except (TypeError, ValueError, KeyError):
                continue
            ph = rules.phase_at(start)
            if ph is None:
                # Shifted off the end of the match: this observation no longer
                # belongs to any window and stops counting for anyone.
                lost += 1
            j = dict(iv)
            j["start"] = start
            j["end"] = end
            j["phase"] = ph["id"] if ph else None
            out.append(j)
        return out, lost

    def solve_match(self, match_key):
        """Allocate official per-window fuel across the three robots that scouts watched."""
        ek = self.event_key()
        m = self.store.match(ek, match_key)
        if not m or not m.get("breakdown"):
            return
        bd = m["breakdown"]
        mult = self.store.get("multipliers") or dict(rules.BUCKET_PRIORS)
        offset = self.clock_offset(m)
        entries = self.store.scout_entries(ek, match_key=match_key)
        # A mid-match HAND OVER leaves two rows for one (match, team). Take the
        # newest rather than whichever the query happened to return first - the
        # outgoing scout's row is a partial match by definition.
        by_team = {}
        for e in entries:
            cur = by_team.get(e["team"])
            if cur is None or (e.get("updatedAt") or 0) > (cur.get("updatedAt") or 0):
                by_team[e["team"]] = e

        out_rows = []
        for alliance in ("red", "blue"):
            info = bd.get(alliance)
            if not info:
                continue
            lineup = m.get(alliance) or []
            robots = []
            for t in lineup:
                e = by_team.get(t)
                payload = (e or {}).get("payload")
                payload = payload if isinstance(payload, dict) else {}
                ivs = payload.get("intervals")
                ivs = ivs if isinstance(ivs, list) else []
                # Only re-anchor phones that were on the shared clock. A phone
                # that fell back to its own timeline has a different origin, and
                # shifting it by someone else's offset makes it worse, not better.
                if offset and payload.get("clockShared"):
                    shifted, lost = self._rephase(ivs, offset)
                    # A correction that empties a robot out is not a correction.
                    # Silently it hands that robot's fuel to the other two and
                    # reports a number nobody observed, so keep what the scout
                    # actually saw and say so instead.
                    if ivs and lost == len(ivs):
                        self.store.flag(ek, match_key, "clock-offset",
                                        f"a {offset:+.0f}s correction would have thrown away every "
                                        f"observation of {t} — left uncorrected")
                    else:
                        ivs = shifted
                robots.append({"team": t, "intervals": rules.split_by_phase(ivs)})
            if not robots:
                continue
            rows = solve.solve_match(info.get("windows") or {}, robots, mult=mult, bootstrap=120)
            out_rows.extend(rows)

            observed = sum(len(r["intervals"]) for r in robots)
            official = sum((info.get("windows") or {}).values())
            if official > 20 and observed == 0:
                self.store.flag(ek, match_key, f"unscouted-{alliance}",
                                f"{official} fuel officially, no scout intervals")
        if out_rows:
            self.store.put_solved(ek, match_key, out_rows)
            # The scouts who logged this match can now be told their numbers
            # reconciled against the official totals. "Touched by a sync" is not
            # the same thing - solving only happens once TBA has posted.
            self.broadcast("solved", {"matchKey": match_key,
                                      "teams": sorted(r["team"] for r in out_rows)})
        if offset:
            shared = sum(1 for e in entries
                         if isinstance(e.get("payload"), dict) and e["payload"].get("clockShared"))
            fix = {"offset": round(offset, 2), "corrected": shared, "of": len(entries)}
            self.store.mutate("clockFixes",
                              lambda f: ({**(f or {}), match_key: fix}, None), {})
            if shared < len(entries):
                self.store.flag(ek, match_key, "clock-partial",
                                f"{len(entries) - shared} scout(s) were on their own clock "
                                f"and could not be re-anchored")

    def request_recalibrate(self, delay=1.0):
        """Schedule one recalibration off the request thread.

        The fit walks every match and every entry, so doing it per-solved-match
        made a six-phone flush pay for it six times. Multipliers also converge
        after ~30 matches and barely move afterwards, so there is nothing to
        gain from running it more often than this.
        """
        with self._recal_lock:
            if self._recal_pending:
                return
            self._recal_pending = True

        def run():
            time.sleep(delay)
            with self._recal_lock:
                self._recal_pending = False
            try:
                self.recalibrate()
            except Exception as e:
                self.note("error", f"recalibrate failed: {e}")

        threading.Thread(target=run, daemon=True, name="recalibrate").start()

    def recalibrate(self):
        """Refit bucket multipliers from official totals - the largest controllable error source."""
        ek = self.event_key()
        rows = []
        for m in self.store.matches(ek):
            bd = m.get("breakdown")
            if not bd:
                continue
            entries = {e["team"]: e for e in self.store.scout_entries(ek, match_key=m["matchKey"])}
            for alliance in ("red", "blue"):
                info = bd.get(alliance)
                if not info:
                    continue
                # Every robot on the alliance, or none of them. The official
                # total for a window covers all three; the seconds we can see
                # cover only the robots somebody was watching. Fitting one
                # against the other taught the model that a scouted robot
                # produces the whole alliance's fuel. Measured against a known
                # truth: fully-scouted windows recover it to 0.1%, and letting
                # in windows where a third had only one robot watched put the
                # multipliers 44.6% out - and these multipliers are behind every
                # fuel number the solver produces for the rest of the event.
                # It is the same rule the score report and solve_match already
                # apply; calibration was the one place it was missing.
                lineup = [t for t in (m.get(alliance) or []) if t]
                if not lineup or any(t not in entries for t in lineup):
                    continue
                for pid, total in (info.get("windows") or {}).items():
                    if not total:
                        continue
                    secs = {b: 0.0 for b in rules.BUCKETS}
                    for t in lineup:
                        payload = entries[t].get("payload") or {}
                        for iv in rules.split_by_phase(payload.get("intervals")):
                            if iv.get("phase") != pid:
                                continue
                            b = iv.get("intensity", "steady")
                            if b in secs:          # an intensity we do not model
                                secs[b] += rules.interval_secs(iv)
                    if any(secs.values()):
                        rows.append((secs, total))
        fit = solve.calibrate_multipliers(rows)
        if fit:
            prev = self.store.get("multipliers")
            if prev != fit:
                self.store.set("multipliers", fit)
                self.store.set("multipliersFittedFrom", len(rows))
                self.broadcast("calibration", {"multipliers": fit, "rows": len(rows)})

    def reconcile(self):
        """Solve any match that has official results but no solved rows yet.

        Normally solving is triggered by the sync that brought the data in, or
        by TBA posting the breakdown. Neither fires when the hub is restarted
        onto an existing database - or when a database is built offline, which
        is exactly what seed_demo.py does - so the fuel numbers would read zero
        for matches that were already played.
        """
        ek = self.event_key()
        if not ek:
            return
        self.migrate_match_keys(ek)
        done = {r["matchKey"] for r in self.store.solved(ek)}
        todo = [m["matchKey"] for m in self.store.matches(ek)
                if m.get("breakdown") and m["matchKey"] not in done]
        for mk in todo:
            try:
                self.solve_match(mk)
            except Exception as e:
                sys.stderr.write(f"[reconcile] {mk}: {e}\n")
        if todo:
            self.note("info", f"solved {len(todo)} match(es) on startup")
            self.recalibrate()
        return len(todo)

    def migrate_match_keys(self, ek):
        """Fold legacy Nexus-keyed rows onto the canonical TBA key.

        A database written before resolve_match_key existed has two rows per
        match and its scouting attached to the wrong one. Runs from reconcile(),
        before the server starts serving, so the first dashboard load is already
        correct. Idempotent.
        """
        moved = []
        for m in self.store.matches(ek):
            want = resolve_match_key(self.store, ek, m.get("label"), m.get("red"), m.get("blue"))
            if want != m["matchKey"]:
                if self.store.remap_match_key(ek, m["matchKey"], want):
                    moved.append((m["matchKey"], want))
        if moved:
            # anything derived from the old key is now stale
            self.store.set("clockFixes", {})
            # Solved rows most of all. The canonical key was very likely solved
            # already, on nothing - that is the even three-way split this whole
            # migration exists to undo - and reconcile() treats a match with any
            # solved row as already done. Leaving them meant the scouting moved
            # across correctly and the fabricated numbers stayed on screen
            # anyway, which is the bug surviving its own repair.
            for _, new_key in moved:
                self.store.drop_solved(ek, new_key)
            clocks = self.store.get("matchClocks") or {}
            for old, new in moved:
                if old in clocks:
                    clocks[new] = {**clocks.pop(old), "matchKey": new}
            self.store.set("matchClocks", clocks)
            self.note("info", f"merged {len(moved)} duplicate match row(s) onto their official keys")
        return len(moved)

    def run_snapshots(self):
        """Copy the database aside every few minutes.

        data/ is one file on one laptop that gets carried around a venue all
        day, and /api/export only helps if somebody remembered to click it. A
        snapshot is a whole working database: to recover, stop the hub, copy one
        out of data/snapshots/ over data/scouting.db, and start it again.
        """
        while not self.stop_flag.is_set():
            self.stop_flag.wait(SNAPSHOT_SECONDS)
            if self.stop_flag.is_set():
                return
            try:
                dest = self.store.snapshot(keep=SNAPSHOT_KEEP)
                self.last_snapshot = time.time()
                self.note("info", f"snapshot written to {os.path.basename(dest)}")
            except Exception as e:
                self.note("error", f"snapshot failed: {e}")
                sys.stderr.write(f"[snapshot] {e}\n")

    # ---------------------------------------------------------- poller
    def run_poller(self):
        """Every source on its own schedule, and its own failure.

        This used to be one try around all five in sequence. One source raising
        - TBA returning a match shaped differently than its schema says, say -
        skipped every source queued behind it AND never advanced its own next-due
        time, so the hub spent the rest of the event retrying that one call every
        two seconds and never polling Lovat, FRC Events or Statbotics again.
        Nothing said so; the poll simply stopped being a poll.
        """
        every = ((self.poll_nexus, NEXUS_POLL_SECONDS),
                 (self.poll_nexus_slow, NEXUS_SLOW_POLL_SECONDS),
                 (self.poll_tba, TBA_POLL_SECONDS),
                 (self.poll_frc_events, FRC_EVENTS_POLL_SECONDS),
                 (self.poll_statbotics, STATBOTICS_POLL_SECONDS),
                 (self.poll_lovat, LOVAT_POLL_SECONDS),
                 (self.poll_mirror, MIRROR_PUSH_SECONDS))
        due = [0.0] * len(every)
        while not self.stop_flag.is_set():
            now = time.time()
            for i, (fn, secs) in enumerate(every):
                if now < due[i]:
                    continue
                # Advance before the call, not after: a source that fails every
                # time still waits its own interval instead of spinning.
                due[i] = now + secs
                try:
                    fn()
                except Exception as e:
                    name = getattr(fn, "__name__", "poll")
                    self.note("error", f"{name} failed: {e}")
                    sys.stderr.write(f"[poll] {name}: {e}\n")
            self.status["lastUpdate"] = time.time()
            self.stop_flag.wait(2.0)


#: `2026casf`: the season, then the event's short code.  Advisory only - a new
#: kind of key one season is not worth a hub that refuses to be configured.
EVENT_KEY = re.compile(r"^\d{4}[a-z0-9]+$")


def _event_key(raw):
    """What somebody typed into the event key box, as the key every source uses.

    The Blue Alliance and frc.events both put the key in the address bar, so
    the address is what is on the clipboard about half the time, and both sites
    display the code capitalised while every API wants it lower case.
    """
    s = raw.strip() if isinstance(raw, str) else ""
    if "://" in s or s.lower().startswith("www."):
        s = s.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\s+", "", s).lower()


def _event_switch(h, body):
    """What a save would do to the event this hub is already on, or None.

    Only a real switch counts, and only away from an event that has something
    in it: setting the key for the first time, re-saving the same key, or
    leaving the box alone are all ordinary and get asked nothing. Nothing is
    deleted by a switch - every row is stored under its own event key and comes
    back when the old key does - but every screen in the building changes at
    once, and that is not something to discover by accident.
    """
    if "eventKey" not in body:
        return None
    now = h.event_key()
    want, _ = _event_key_check(body["eventKey"])
    if not now or not want or now == want:
        return None
    records = len(h.store.scout_entries(now)) + len(h.store.pit_entries(now))
    if not records:
        return None
    return {"from": now, "to": want, "records": records}


def _event_key_check(raw):
    """`(key, warning)` - the same answer whether it is being saved or typed."""
    ek = _event_key(raw)
    if ek and not EVENT_KEY.match(ek):
        return ek, ("An event key looks like `2026casf` - the year, then the event's short "
                    "code. This is saved as typed, but nothing will load if it is wrong.")
    return ek, None


def _ai_provider(h, body):
    """Which company's AI key this save expects, once its model is applied.

    The model and the key are two boxes filled in together, and the mismatch
    between them - a Claude key under a Gemini model - is the one AI setup
    mistake with no symptom at all beyond "the model could not be reached".
    Checking for it means knowing the provider the same save is about to set,
    not the one already stored.
    """
    if isinstance(body.get("aiModel"), str):
        provider, _, model = body["aiModel"].strip().rpartition(":")
        model = model.strip()
        if model.lower() in ("", "none"):
            return ai.OFF
        return provider.strip() or ai.provider_for(model) or ai.OFF
    return h.ai().provider


def _mirror_url(raw):
    """What somebody typed into the mirror box, as an address that works.

    "systemoverload.org", with a trailing slash, or with the /api/push path
    already on the end are all the same intention, and all three used to be
    saved verbatim and then fail with a 404 that pointed at nothing. https is
    assumed rather than http: this crosses the open internet carrying a whole
    event and a push key.
    """
    url = (raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    url = url.rstrip("/")
    for tail in ("/api/push", "/api/photos", "/api"):
        if url.endswith(tail):
            url = url[: -len(tail)]
            break
    return url.rstrip("/")


def _finite(v):
    """The same value with every NaN and Infinity replaced by null.

    "We do not know" is what a non-finite number means here anyway, and null is
    how every other unknown in this API is spelled.
    """
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v


def _gz(data):
    buf = io.BytesIO()
    with _gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as fh:
        fh.write(data)
    return buf.getvalue()


#: The only content types /api/photo will ever claim. A pit record arrives from
#: a phone - or from anything else on the venue wifi - and its data: URI used to
#: name its own Content-Type, which meant a "photo" could be served back as
#: text/html with attacker-chosen bytes in it: stored XSS on the hub's own
#: origin, where the strategy token lives.
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/gif", "image/heic")


def _image_mime(raw):
    m = str(raw or "").strip().lower()
    if m == "image/jpg":
        m = "image/jpeg"
    return m if m in IMAGE_MIMES else "application/octet-stream"


#: What this hub calls a photo: the first 16 hex characters of the image's own
#: sha1, and never anything else. The id is pasted into an `<img src>` on the
#: dashboard and on the pit tablet, so a "photo" that is not an id is a string
#: of somebody's choosing inside an HTML attribute on the hub's own origin -
#: which is where the strategy token lives. Demonstrated: a pit record synced
#: with `photos: ['x" onerror="..."']` ran script on the dashboard.
#: Nothing legitimate is lost by this: every id in the table is written by
#: put_photo below, from that same hash.
_PHOTO_ID = re.compile(r"^[0-9a-f]{8,40}$")


def _extract_photos(store, rec):
    """Pull data: URIs off a pit record into the photo table.

    Photos travel over the network only - they would blow up any other transport
    and they do not belong in the row we merge on every sync.
    """
    payload = rec.get("payload") or {}
    photos = payload.get("photos") or []
    kept = []
    for src in photos:
        if isinstance(src, str) and src.startswith("data:"):
            try:
                head, b64 = src.split(",", 1)
                mime = _image_mime(head[5:].split(";")[0])
                raw = base64.b64decode(b64)
                pid = hashlib.sha1(raw).hexdigest()[:16]
                store.put_photo(pid, rec["eventKey"], rec["team"], mime, raw)
                kept.append(pid)
            except Exception:
                continue
        elif isinstance(src, str) and _PHOTO_ID.match(src):
            kept.append(src)          # already an id, and shaped like one
    payload["photos"] = kept
    rec["payload"] = payload


def _csv_safe(row):
    """One row, with nothing in it a spreadsheet will treat as a formula.

    Scout notes are free text typed on a phone, and this export exists to be
    opened in Excel and handed to an alliance partner. A cell beginning = + - @
    is a formula to every spreadsheet there is, so it gets a leading quote -
    the standard defusing, and it still reads as the text the scout typed.
    """
    out = []
    for v in row:
        if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
            v = "'" + v
        out.append(v)
    return out


def _csv_table(h, ek, table):
    """Flatten one table for a spreadsheet.

    A strategy lead lives in a spreadsheet, and at an event the practical way to
    hand numbers to an alliance partner is a file they can open. The JSON export
    stays the one that round-trips through /api/import; this one is for humans,
    so it flattens the nested blocks into columns and keeps the estimated fuel
    band in its own column rather than baking a plus-minus into a string.
    """
    if table == "teams":
        summary = analytics.event_summary(h.store, ek)
        header = ["team", "name", "rank", "record", "rankingPoints", "opr", "epa",
                  "matchesScouted", "matchesWithOfficial", "scoutVsOfficialPct",
                  "avgFuel", "fuelBand",
                  "fuelConsistency", "bestClimb", "climbL3Pct", "climbL2Pct", "climbL1Pct",
                  "autoClimbPct", "avgTowerPoints", "avgRP", "stockpilePct", "wastedFuelPct",
                  "feedPct", "feedSecs", "defenseSecs", "defenseFacedSecs",
                  "defenseFacedMatches", "defenseAgainst", "defendedBy",
                  "startZone", "startZonePct", "autoFailPct", "foulPct", "avgPreload",
                  "driver", "defense", "diedPct", "tippedPct", "noShowPct"]
        # How far the raw scout estimate ran from the official total on the
        # matches this team played - the same check the HEALTH tab shows, so it
        # survives into a spreadsheet.
        off_by = {}
        for r in (summary.get("scoreReport") or {}).get("rows") or []:
            if r.get("deltaPct") is None or r.get("robotsScouted") != 3:
                continue
            m = h.store.match(ek, r["matchKey"]) or {}
            for team in (m.get(r["alliance"]) or []):
                off_by.setdefault(team, []).append(r["deltaPct"])

        rows = []
        for t in sorted(summary["teams"].values(), key=lambda x: x["team"]):
            e, es, o, ep = t["exact"], t["estimated"], t["observed"], t["epa"]
            rec = e.get("record") or {}
            deltas = off_by.get(t["team"]) or []
            rows.append([
                t["team"], t.get("name"), e.get("rank"),
                f"{rec['wins']}-{rec['losses']}-{rec['ties']}" if rec else None,
                e.get("rankingPoints"), e.get("opr"), ep.get("epa"),
                t["matchesScouted"], e["matchesWithOfficial"],
                round(sum(deltas) / len(deltas), 1) if deltas else None,
                es["avgFuel"], es["band"], es["consistency"], e["bestClimb"],
                round(e["climbRate"].get("Level3", 0), 1), round(e["climbRate"].get("Level2", 0), 1),
                round(e["climbRate"].get("Level1", 0), 1), e["autoClimbRate"],
                e["avgTowerPoints"], e["avgRP"], o["stockpileRate"], o["wastedFuelPct"],
                o["feedRate"], o["feedSecs"], o["defenseSecs"],
                o.get("defenseFacedSecs"), o.get("defenseFacedMatches"),
                _counts(o.get("defenseAgainst")), _counts(o.get("defendedBy")),
                o.get("startZone"), o.get("startZonePct"),
                o.get("autoFailRate"), o.get("foulRate"), o.get("avgPreload"),
                o["driver"], o["defense"],
                o["diedRate"], o["tippedRate"], o["noShowRate"],
            ])
        return header, rows

    if table == "scout":
        header = ["matchKey", "team", "alliance", "station", "scoutId", "runs", "activeSecs",
                  "feedSecs", "defenseSecs", "defenseTarget", "preload", "startPosition",
                  "autoTower", "endgameTower", "driverRating", "defenseRating",
                  "died", "tipped", "noShow", "autoFailed", "fouls", "note"]
        rows = []
        for e in sorted(h.store.scout_entries(ek), key=lambda x: (x["matchKey"], x["team"])):
            p = e.get("payload") or {}
            rows.append([
                e["matchKey"], e["team"], e.get("alliance"), e.get("station"), e.get("scoutId"),
                len(p.get("intervals") or []), _secs(p.get("intervals")),
                _secs(p.get("feedIntervals")), _secs(p.get("defenseIntervals")),
                p.get("defenseTarget"), p.get("preload"), p.get("startPosition"),
                p.get("autoTower"), p.get("endgameTower"),
                p.get("driverRating"), p.get("defenseRating"),
                bool(p.get("died")), bool(p.get("tipped")), bool(p.get("noShow")),
                bool(p.get("autoFailed")), bool(p.get("fouls")),
                (p.get("note") or "").strip(),
            ])
        return header, rows

    if table == "lovat":
        # Kept as its own file rather than as extra columns on the team summary,
        # for the same reason it is its own block on the dashboard: it is
        # somebody else's scouting, collected to somebody else's standard, and a
        # spreadsheet that mixes it into our columns is how it ends up quoted
        # back as ours. Every column Lovat's export carries is here.
        summary = analytics.event_summary(h.store, ek)
        header = ["team", "name", "matches", "scouters", "avgFuel", "fuelPerSec", "throughput",
                  "accuracy", "volleys", "ballsFed", "feedSecs", "feedingRate", "feedsPerMatch",
                  "defenseSecs", "contactDefenseSecs", "campingDefenseSecs",
                  "defenseEffectiveness", "totalPoints", "autoPoints", "teleopPoints",
                  "driver", "bestClimb", "climbL3Pct", "climbL2Pct", "climbL1Pct",
                  "autoClimbPct", "climbStartSecs", "autoClimbStartSecs", "beachedPct",
                  "scoresWhileMovingPct", "disruptPct", "traversalPct", "outpostIntakes",
                  "roles", "intakeTypes", "unmatchedRows"]
        rows = []
        for t in sorted(summary["teams"].values(), key=lambda x: x["team"]):
            lv = t.get("lovat") or {}
            if not lv.get("matches"):
                continue
            cr = lv.get("climbRate") or {}
            rows.append([
                t["team"], t.get("name"), lv.get("matches"), lv.get("scouters"),
                lv.get("avgFuel"), lv.get("fuelPerSec"), lv.get("throughput"),
                lv.get("accuracy"), lv.get("volleys"), lv.get("ballsFed"),
                lv.get("feedSecs"), lv.get("feedingRate"), lv.get("feedsPerMatch"),
                lv.get("defenseSecs"), lv.get("contactDefenseSecs"),
                lv.get("campingDefenseSecs"), lv.get("defenseEffectiveness"),
                lv.get("totalPoints"), lv.get("autoPoints"), lv.get("teleopPoints"),
                lv.get("driver"), lv.get("bestClimb"),
                cr.get("Level3"), cr.get("Level2"), cr.get("Level1"),
                lv.get("autoClimbRate"), lv.get("climbStartSecs"), lv.get("autoClimbStartSecs"),
                lv.get("beachedRate"), lv.get("scoresWhileMovingRate"), lv.get("disruptRate"),
                lv.get("traversalRate"), lv.get("outpostIntakes"),
                _counts(lv.get("roles")), _counts(lv.get("intakeTypes")),
                " · ".join(lv.get("unmatched") or []) or None,
            ])
        return header, rows

    if table == "pit":
        header = ["team", "scoutId", "drivetrain", "shooter", "maxClimb", "stockpile",
                  "groundPickup", "weight", "autos", "notes", "photos"]
        rows = []
        for e in sorted(h.store.pit_entries(ek), key=lambda x: x["team"]):
            p = e.get("payload") or {}
            rows.append([e["team"], e.get("scoutId"), p.get("drivetrain"), p.get("shooter"),
                         p.get("maxClimb"), p.get("stockpile"), p.get("groundPickup"),
                         p.get("weight"), p.get("autos"), p.get("notes"),
                         len(p.get("photos") or [])])
        return header, rows

    raise KeyError(table)


def _counts(m):
    """{9982: 3, 9975: 1} -> "9982 x3 · 9975" for a spreadsheet cell."""
    rows = sorted((m or {}).items(), key=lambda kv: -kv[1])
    return " · ".join(f"{t} x{n}" if n > 1 else str(t) for t, n in rows) or None


def _secs(intervals):
    return round(sum(rules.interval_secs(iv)
                     for iv in (intervals or [])), 1)


AI_NOTES_TASK = """Task: read one team's scout notes and say what they add up to.

Write at most four short bullets naming a recurring theme, each citing the
matches and scouts behind it, then - only if there is one - a line beginning
"Scouts disagree:" stating both sides and who said each. A theme one scout
mentioned once is a single observation, not a theme; say so. End there."""

AI_PICKLIST_TASK = """Task: explain a picklist that has already been ordered.

The order is fixed and was produced by this app's solver and the strategy
lead. You are explaining it, not revising it: never suggest a different
position for a team, and never say a team is ranked too high or too low.

For each team in order, write one sentence saying what the data shows about
that robot, citing the block each number comes from. Then two final lines,
"First pick:" and "Second pick:", each naming a team already in the list and
the recorded strength that argues for it."""

AI_MATCH_TASK = """Task: read one upcoming match for the strategy lead.

Write it as four labelled lines and nothing else:

"Reads:" - one sentence on how the two alliances compare, citing the projection
block and the team numbers behind it.
"Watch:" - the single robot on the opposing alliance that decides this match,
and the recorded number that says so.
"Defence:" - who to put defence on, or that the data does not support putting a
robot on defence, citing the `defenseHistory` block and the `defenseSecs` behind
it. If nobody on either alliance has logged defence, say that.
"Risk:" - the one thing most likely to make this read wrong, from the data
itself: robots with few matches scouted, a wide band, a died or no-show rate,
or a lineup nobody has scouted.

The projection block is already summed for you - never add, subtract or
rescale numbers to make a new one. Where an alliance has unscouted robots, say
which and treat the projection as incomplete rather than quietly trusting it."""

AI_ASK_TASK = """Task: answer one question from the strategy lead about this event.

Answer in at most four sentences, from the team records supplied and nothing
else. Cite the team numbers and the block behind every claim. If the data
does not contain the answer - it is about something nobody recorded, another
event, or another season - say exactly that and stop. Do not guess, and do
not offer to look it up."""


def _ai_team_payload(rec, rank=None):
    """The slice of a team record the model is allowed to see.

    Deliberately small and deliberately labelled: the block names travel with
    the numbers so a claim can be cited, and nothing here is a raw entry table.
    """
    if not rec:
        return None
    e, es, o, lv = (rec.get("exact") or {}), (rec.get("estimated") or {}), \
                   (rec.get("observed") or {}), (rec.get("lovat") or {})
    out = {
        "team": rec.get("team"),
        "name": rec.get("name"),
        "matchesScouted": rec.get("matchesScouted"),
        "exact": {k: e.get(k) for k in
                  ("bestClimb", "climbRate", "autoClimbRate", "avgTowerPoints",
                   "rank", "opr", "avgRP", "record", "matchesWithOfficial")},
        "estimated": {k: es.get(k) for k in
                      ("avgFuel", "band", "matches", "consistency", "cycleRate")},
        "observed": {k: o.get(k) for k in
                     ("stockpileRate", "wastedFuelPct", "feedRate", "feedSecs",
                      "defenseSecs", "driver", "defense", "diedRate", "tippedRate",
                      "noShowRate", "foulRate", "autoFailRate", "startZone")},
        "epa": rec.get("epa") or {},
        "noteCount": len(rec.get("notes") or []),
    }
    if lv.get("matches"):
        out["lovat"] = {k: lv.get(k) for k in
                        ("matches", "avgFuel", "fuelPerSec", "accuracy", "driver",
                         "bestClimb", "climbRate", "autoClimbRate", "feedSecs",
                         "defenseSecs", "defenseEffectiveness", "roles", "scouters")}
    if rank is not None:
        out["rank"] = rank
    return out


def _ai_match_payload(summary, match):
    """The six robots on one match, both projections, and the defence history.

    The projections are computed in analytics.py and travel with the payload so
    a match read can cite an alliance total instead of adding three numbers up
    itself, which the ground rules forbid and which a model gets wrong quietly.
    """
    teams = summary.get("teams") or {}
    sides = {}
    for side in ("red", "blue"):
        rows = [r for r in (_ai_team_payload(teams.get(t) or teams.get(str(t)))
                            for t in (match.get(side) or [])) if r]
        sides[side] = {
            "projection": analytics.match_projection(teams, match, side),
            "teams": rows,
        }
    return {
        "match": {"key": match.get("matchKey"), "label": match.get("label"),
                  "status": match.get("status")},
        "red": sides["red"],
        "blue": sides["blue"],
        "defenseHistory": analytics.defense_history(teams, match),
        "startZones": {side: {t: ((teams.get(t) or teams.get(str(t)) or {})
                                  .get("observed") or {}).get("startZone")
                              for t in (match.get(side) or [])}
                       for side in ("red", "blue")},
    }


def _ai_notes_payload(rec):
    """Our notes and Lovat's, kept apart so the model can say which is which."""
    lv = rec.get("lovat") or {}
    return {
        "team": rec.get("team"),
        "name": rec.get("name"),
        "matchesScouted": rec.get("matchesScouted"),
        "numbers": _ai_team_payload(rec),
        "ourNotes": [{"match": n.get("matchKey"), "scout": n.get("scoutId"),
                      "note": n.get("note")} for n in (rec.get("notes") or [])],
        "lovatNotes": [{"match": n.get("matchKey") or n.get("match"),
                        "scout": n.get("scouter"), "note": n.get("note")}
                       for n in (lv.get("notes") or [])],
    }


#: How often a button may kick the whole set of sources. A save has just
#: changed a key and must go through; REFRESH is a button two people can lean
#: on together, and every press is six outbound calls on somebody's quota.
POLL_ALL_SECONDS = 10


def _poll_all(h, force=False):
    """Kick every source off the request thread. A poll must never block a save.

    Rate limited unless a save asked for it. Two leads pressing REFRESH at the
    same moment - or one lead pressing it repeatedly because nothing seems to
    be happening, which is exactly when they will - used to be six fresh
    threads per press against five vendors, with nothing between them and the
    key's quota.
    """
    now = time.time()
    if not force:
        with h._clients_lock:
            if now - getattr(h, "_last_poll_all", 0.0) < POLL_ALL_SECONDS:
                return False
            h._last_poll_all = now
    else:
        with h._clients_lock:
            h._last_poll_all = now
    for fn in (h.poll_nexus, h.poll_nexus_slow, h.poll_tba, h.poll_frc_events,
               h.poll_statbotics, h.poll_lovat):
        threading.Thread(target=fn, daemon=True).start()
    return True


def _round(v, places=1):
    try:
        return round(float(v), places)
    except (TypeError, ValueError):
        return None


def _first(v):
    """Statbotics returns some breakdown values as {'mean': x}, some as a number."""
    if isinstance(v, dict):
        return v.get("mean")
    return v


def _nested(d, *path):
    for k in path:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def _split_event_key(ek):
    """'2026casf' -> ('2026', 'casf').  FRC Events takes the two separately."""
    ek = str(ek or "")
    if len(ek) > 4 and ek[:4].isdigit():
        return ek[:4], ek[4:]
    return None, None


def _int(v):
    if isinstance(v, int):
        return v
    s = str(v or "")
    if s.startswith("frc"):
        s = s[3:]
    try:
        return int(s)
    except ValueError:
        return None


def _tba_label(m):
    cl = (m.get("comp_level") or "").upper()
    return f"{ {'QM':'Qualification','QF':'Quarterfinal','SF':'Semifinal','F':'Final','EF':'Eighthfinal'}.get(cl, cl) } {m.get('match_number')}"


# Nexus labels a qual match "Qualification 12"; TBA keys the same match
# "2026casf_qm12". Deriving a key from the label alone therefore produced a
# SECOND row for every match, and the two halves of the app each saw only one of
# them: the phone takes its matchKey from the row with a `status` (Nexus), while
# the solver only walks rows with a `breakdown` (TBA). Scout intervals never
# reached the solver, so every alliance total got split evenly across three
# robots and the dashboard showed confident numbers containing no scouting.
# The spellings that unambiguously mean "qualification match N", so all of them
# land on TBA's qmN instead of each earning a row of its own. A row of its own is
# the documented failure this whole path exists to prevent: scouts log against
# one key, the solver reads the other, and every fuel number becomes an even
# three-way split of the official total with no scouting in it.
# A qual word is required - "Match 42" alone could as easily be a playoff, and
# guessing wrong is worse than keeping it separate.
_QUAL_LABEL = re.compile(
    r"^\s*(?:qualification|quals|qual|q)\s*(?:match\s*)?#?\s*(\d+)\s*(?:\([^)]*\))?\s*$",
    re.I)

#: Anything that is not a letter or a digit. A match key is used as a URL path
#: segment (/api/ai/match/<key>) and in query strings, unencoded, so a label
#: with a # in it truncated the request at the fragment and one with a / routed
#: somewhere else entirely.
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def _slug_match_key(event_key, label):
    slug = _SLUG_UNSAFE.sub("", str(label or "").lower())
    return f"{event_key}_{slug or 'match'}"


def resolve_match_key(store, event_key, label, red=None, blue=None):
    """The one key both TBA and Nexus should agree on for this match.

    Quals - which is everything scouts log - map straight onto TBA's `qmN`.
    Playoff labels carry no TBA equivalent ("Match 3" tells us nothing about
    `sf1m1`), so those fall back to matching an already-known row by its exact
    lineup, and finally to the old slug.
    """
    m = _QUAL_LABEL.match(str(label or ""))
    if m:
        return f"{event_key}_qm{int(m.group(1))}"

    if red and blue:
        want = (sorted(_int(t) for t in red), sorted(_int(t) for t in blue))
        for row in store.matches(event_key):
            if row.get("compLevel") == "qm" or not (row.get("red") and row.get("blue")):
                continue
            if (sorted(row["red"]), sorted(row["blue"])) == want:
                return row["matchKey"]
    return _slug_match_key(event_key, label)


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FRCScout/1.0"
    hub = None
    allow_remote_config = False

    def log_message(self, fmt, *args):
        if os.environ.get("SCOUT_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---------------------------------------------------------- helpers
    def _etag(self, *scopes, **kw):
        """The ETag for an endpoint that reads `scopes`.

        BOOT is in the tag because the counters live in memory and restart at
        zero: without it a phone that slept through a hub restart holds a tag
        that matches a completely different state. The event key is in it
        because these endpoints all take ?event=, and switching events must not
        read as "unchanged".
        """
        ek = (parse_qs(urlparse(self.path).query).get("event")
              or [Handler.hub.event_key()])[0] or "-"
        extra = kw.get("extra")
        return 'W/"%s-%s-%s%s"' % (BOOT, Handler.hub.store.version_for(*scopes), ek,
                                   "" if extra is None else "-" + str(extra))

    def _not_modified(self, etag):
        """True (and the 304 is already sent) when the client's copy is current.

        Called before the payload is built, not after - the point is to skip the
        work, not just the bytes.
        """
        if not etag or self.headers.get("If-None-Match") != etag:
            return False
        self.send_response(304)
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def _json(self, obj, code=200, etag=None):
        # allow_nan=False so this can never emit bare NaN or Infinity. Python
        # writes those happily and reads them back; the browser's JSON.parse
        # rejects them outright, so one non-finite number anywhere in a response
        # made the whole endpoint unreadable to every client - the dashboard
        # fell back to its cached copy and quietly stopped updating for the rest
        # of the event. Costs nothing on the normal path: the scrub only runs
        # when there is actually something to scrub.
        try:
            body = json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except ValueError:
            body = json.dumps(_finite(obj), separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
        enc = None
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body, enc = _gz(body), "gzip"
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(body)))
        if etag:
            # no-cache, not no-store: revalidate every time, but let the
            # revalidation come back empty. no-store would forbid holding the
            # copy at all, which is the whole saving.
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _csv(self, filename, header, rows):
        # The filename is built from ?event=, so it is caller-controlled. A bare
        # newline in it used to land in the response headers verbatim - a real
        # Set-Cookie could be injected by anything that could get a lead to click
        # a crafted export link - and a quote broke the quoted filename outright.
        filename = re.sub(r"[^A-Za-z0-9._-]", "_", str(filename))[:120] or "export.csv"
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(_csv_safe(r) for r in rows)
        body = buf.getvalue().encode("utf-8-sig")   # BOM: Excel opens it as UTF-8
        enc = None
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body, enc = _gz(body), "gzip"
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------- CORS
    # Every hub address other than the one a phone loaded the page from is a
    # cross-origin address, and a browser will not let script read a response
    # from one without this header. Without it `discover()` could only ever
    # reach `location.origin`, which made the whole find-the-hub-again story
    # inert: the remembered last-good address, both fallback candidates and the
    # 254-host subnet sweep that exists so a phone can follow the laptop when
    # DHCP moves it all failed identically, and a phone whose origin went dark
    # sat offline holding its queue until somebody reloaded it by hand.
    #
    # `*` rather than an echo of Origin: the hub cannot know which of its
    # addresses the phones reached it on. It is a LAN box serving numbers that
    # are on TBA anyway - but not to a page open on the hub machine itself,
    # where `_is_local()` is what stands between a request and the API keys.
    # A website the lead happens to visit on that laptop is exactly the caller
    # that boundary is for, so localhost keeps answering nobody but itself.
    def end_headers(self):
        if getattr(self, "path", "").startswith("/api/") and not self._is_local():
            self.send_header("Access-Control-Allow-Origin", "*")
        BaseHTTPRequestHandler.end_headers(self)

    def do_OPTIONS(self):
        """The preflight a cross-origin JSON POST asks before it sends anything.

        BaseHTTPRequestHandler answers an unknown method with 501, which the
        browser reads as a refusal - so /api/sync was never even attempted from
        another origin, and a phone that had just found the hub again still
        could not hand over a single queued record.
        """
        if not self.path.startswith("/api/"):
            self.send_error(404, "Not found")
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        # Every header this API is ever sent. X-Admin-Token was missing, so an
        # admin request from any origin but the hub's own was refused at the
        # preflight - which is every admin request on a hub started with
        # --allow-remote-config, and every one from a phone that found the hub
        # again at a second address.
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, X-Strategy-Token, X-Admin-Token")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _is_local(self):
        """True only for a request from the machine running the hub.

        API keys are entered on the hub itself and nowhere else. That is the
        honest boundary: whoever is sitting at the laptop is the person who
        should be configuring it, and it needs no passcode to enforce.
        """
        host = (self.client_address or ("",))[0]
        return host in ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")

    def _unlocked(self):
        return Handler.hub.token_ok(self.headers.get("X-Strategy-Token"))

    def _admin_ok(self):
        """Whether this request may change hub settings.

        Two separate gates, and both have to hold. `_is_local` is the boundary
        that has always been here: settings are entered on the hub machine and
        nowhere else. This is the second one - the admin code, when a team has
        set one - and it is what makes the Setup page an admin panel rather
        than a page anybody at the laptop can retype the event key into.
        """
        return Handler.hub.admin_token_ok(self.headers.get("X-Admin-Token"))

    def _locked_out(self):
        return self._json({
            "error": "The hub settings are locked. Press UNLOCK on the admin page and "
                     "enter the admin password.",
            "locked": True,
        }, 403)

    def _unlocked_strict(self):
        """Like _unlocked, but an unset passcode does not mean "everyone".

        _unlocked is deliberately open when no code is configured - a read-only
        picklist should never be gated by accident. Scout quality data is the
        opposite: absent a passcode it stays on the hub machine only.
        """
        return Handler.hub.pin_set() and self._unlocked()

    # Well above any real sync - a pit record carries base64 photos - but low
    # enough that a bogus Content-Length cannot make the hub eat the laptop's
    # memory in the middle of quals.
    MAX_BODY = 64 * 1024 * 1024

    def _body(self):
        """The POST body as an object. Never anything else.

        Every handler below reads `body.get(...)`. JSON's top level can just as
        well be a list, a string, a number or null, and each of those used to
        take the request thread down with an AttributeError - no response at
        all, the phone seeing a dropped connection rather than an answer. A
        malformed Content-Length did the same before the read even started.
        """
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if n <= 0 or n > self.MAX_BODY:
            return {}
        try:
            v = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}
        return v if isinstance(v, dict) else {}

    @staticmethod
    def _rows(body, key):
        """`scout`/`pit` as a list of records, whatever actually arrived.

        `{"pit": 3}` used to reach `for rec in 3` and kill the request thread.
        """
        v = body.get(key)
        return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []

    def _file(self, relpath):
        path = os.path.normpath(os.path.join(WEB_ROOT, relpath.lstrip("/")))
        if not path.startswith(WEB_ROOT) or not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".js"):
            ctype = "text/javascript; charset=utf-8"
        elif path.endswith(".webmanifest"):
            ctype = "application/manifest+json"

        # Fonts and icons are content-stable and were being revalidated on every
        # load; markup and code stay no-cache so a fix reaches phones instantly.
        immutable = "/fonts/" in path.replace(os.sep, "/") or "/icons/" in path.replace(os.sep, "/")

        # no-cache means "ask me every time", not "never store it" - so give the
        # browser something to ask WITH. Without an ETag every reload pulled all
        # ~66KB of code and markup down again, and a scout who reloads is a
        # scout whose phone just lost the page. Cheap and exact: size and mtime
        # off the directory entry, no read and no hash. There is no service
        # worker here (plain HTTP is not a secure context - see docs), so this
        # revalidation is the whole of the phone's caching story.
        try:
            st = os.stat(path)
            etag = 'W/"%x-%x"' % (int(st.st_mtime), st.st_size)
        except OSError:
            etag = None
        if etag and not immutable and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        with open(path, "rb") as fh:
            data = fh.read()

        enc = None
        compressible = path.endswith((".js", ".css", ".html", ".json", ".webmanifest", ".svg"))
        if compressible and len(data) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            data, enc = _gz(data), "gzip"

        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(data)))
        if etag and not immutable:
            self.send_header("ETag", etag)
        self.send_header("Cache-Control",
                         "public, max-age=31536000, immutable" if immutable else "no-cache")
        self.end_headers()
        self.wfile.write(data)

    # -------------------------------------------------------------- GET
    def do_GET(self):
        u = urlparse(self.path)
        p = posixpath.normpath(u.path)
        q = parse_qs(u.query)
        h = Handler.hub

        if p in ("/", "/index.html"):
            if not (self._is_local() or Handler.allow_remote_config):
                return self._file("setup_elsewhere.html")
            return self._file("index.html")
        if p == "/scout":
            return self._file("scout.html")
        if p == "/dashboard":
            return self._file("dashboard.html")
        if p == "/join":
            return self._file("join.html")
        if p == "/pit":
            return self._file("pit.html")
        if p == "/api/stream":
            return self._stream()
        if p == "/api/config":
            # Built once each. h.ai() constructs a fresh client every call and
            # this response asked for five of them, plus two mirrors and an
            # frc_events - eight objects per request, on an endpoint every page
            # in the building fetches. No ETag on this one: `serverTime` below
            # is what net.js corrects the phones' clock skew against, and the
            # shared match clock is the last thing to serve from a cache.
            ek = h.event_key()
            aic, mir, frc = h.ai(), h.mirror(), h.frc_events()
            return self._json({
                "eventKey": ek,
                "eventLevel": (h.store.event(ek) or {}).get("level", "regional") if ek else "regional",
                "keys": {"tba": bool(h.cfg("tbaKey")), "nexus": bool(h.cfg("nexusKey")),
                         "frcEvents": frc.ok, "lovat": bool(h.cfg("lovatKey")),
                         "ai": aic.ok, "mirror": mir.ok},
                # Which boxes have something in them, box by box, so the Setup
                # page can say SAVED beside each one and offer to forget it.
                # Whether, never what: no key value leaves the hub, and this
                # route is open to everything on the venue wifi.
                "saved": {k: bool(h.cfg(k)) for k in keyhygiene.FIELDS},
                # The address is a setting and the setup page has to show what
                # is saved; the push key is a secret and never comes back out.
                "mirror": {"url": h.cfg("mirrorUrl") or None, "ok": mir.ok,
                           **{k: v for k, v in (h.store.get("mirrorState") or {}).items()
                              if k != "digest"}},
                # The provider and model are settings, not secrets - the panel
                # that shows generated text has to be able to name what wrote it.
                # The effective model, not the stored one: a hub that has
                # never picked gets the default, and the page has to show what
                # would actually be used.
                "ai": {"provider": aic.provider, "model": aic.model or None,
                       "label": aic.label,
                       "calls": h.ai_calls(), "limit": h.ai_ceiling(),
                       "providers": dict(ai.PROVIDERS), "models": ai.catalogue(),
                       "default": ai.DEFAULT_MODEL},
                "picklistLocked": h.pin_set(),
                # Whether the settings are behind an admin code, and whether
                # the asking page is currently past it. The panel opens locked
                # either way: the code stops somebody else, the lock stops an
                # accident, and a team with no code still gets the second one.
                "admin": {"set": h.admin_set(), "unlocked": self._admin_ok(),
                          # Where it is configured, never what it is, and the
                          # reason when it is there but unusable - a panel that
                          # nobody can unlock has to say why on the panel.
                          "source": "env" if h.admin_set() else "none",
                          "problem": h.admin_password()[1]},
                "ourTeam": h.cfg("ourTeam"),
                "status": h.status,
                "multipliers": h.store.get("multipliers") or rules.BUCKET_PRIORS,
                "multipliersFittedFrom": h.store.get("multipliersFittedFrom") or 0,
                "serverTime": time.time(),
            })
        if p == "/api/state":
            ek = (q.get("event") or [h.event_key()])[0]
            if not ek:
                return self._json({"error": "no event selected"}, 400)
            etag = self._etag(*STATE_SCOPES)
            if self._not_modified(etag):
                return None
            return self._json({
                "eventKey": ek,
                "event": h.store.event(ek),
                "teams": h.store.teams(ek),
                "matches": h.store.matches(ek),
                "live": h.store.get("nexusLive") or {},
                "pits": h.nexus_data("pits", ek, {}),
                "pitMap": h.nexus_data("pitMap", ek),
                "inspection": h.nexus_data("inspection", ek, {}),
                "alliances": h.nexus_data("alliances", ek, []),
                "flags": h.store.flags(ek),
                "seats": h.seats(),
                "matchClocks": h.store.get("matchClocks") or {},
                "clockFixes": h.store.get("clockFixes") or {},
                "pitEntries": h.store.pit_entries(ek),
                "rankings": h.event_data("rankings", ek, {}),
                "epa": h.event_data("epa", ek, {}),
                "earlyScores": h.event_data("earlyScores", ek, {}),
            }, etag=etag)
        if p == "/api/scout":
            ek = (q.get("event") or [h.event_key()])[0]
            return self._json(h.store.scout_entries(ek, (q.get("match") or [None])[0]))
        if p == "/api/pit":
            ek = (q.get("event") or [h.event_key()])[0]
            return self._json(h.store.pit_entries(ek))
        if p == "/api/analytics":
            ek = (q.get("event") or [h.event_key()])[0]
            if not ek:
                return self._json({"error": "no event selected"}, 400)
            # Per-scout quality scores name people and grade them, and this
            # endpoint is open to everything on the venue wifi - a scoreboard of
            # who is worst at their job on the big screen costs morale and buys
            # nothing, because nothing downweights a low score anyway. The lead
            # gets it (strategy passcode, or sitting at the hub); the room does
            # not.
            scouts = self._is_local() or self._unlocked_strict()
            # `scouts` is in the tag: the same event served with and without the
            # per-scout block is two different bodies, and a lead who locks the
            # board must not keep being told their copy is current.
            etag = self._etag(*ANALYTICS_SCOPES, extra=int(scouts))
            if self._not_modified(etag):
                return None
            return self._json(analytics.event_summary(h.store, ek, include_scouts=scouts),
                              etag=etag)
        if p.startswith("/api/photo/"):
            pid = p.rsplit("/", 1)[-1]
            mime, data = h.store.photo(pid)
            if not data:
                return self.send_error(404, "no such photo")
            self.send_response(200)
            # Re-checked on the way out as well as in: a database written by an
            # older build can still hold whatever a phone once claimed.
            self.send_header("Content-Type", _image_mime(mime))
            # And if it is mislabelled, no sniffing it into something runnable.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            return self.wfile.write(data)
        if p == "/api/photos":
            ek = (q.get("event") or [h.event_key()])[0]
            t = (q.get("team") or [None])[0]
            return self._json(h.store.photo_ids(ek, t))
        if p == "/api/seats":
            return self._json(h.seats())
        if p == "/api/crew":
            etag = self._etag(*CREW_SCOPES, extra=h.subs_gen)
            if self._not_modified(etag):
                return None
            return self._json(h.crew(), etag=etag)
        if p == "/api/picklist":
            # anyone may read the board - scouts want to know who we are picking.
            # Only changing it needs the passcode (see the POST handler).
            return self._json({**h.picklist(), "canEdit": self._unlocked(),
                               "locked": h.pin_set()})
        if p == "/api/seatlog":
            etag = self._etag(*SEATLOG_SCOPES)
            if self._not_modified(etag):
                return None
            return self._json(h.seat_history(), etag=etag)
        if p == "/api/diag":
            return self._json(h.diag())
        if p == "/api/export":
            ek = (q.get("event") or [h.event_key()])[0]
            return self._json({
                "kind": "frc-rebuilt-scouting-export", "version": 1,
                "eventKey": ek, "exportedAt": time.time(),
                "scout": h.store.scout_entries(ek),
                "pit": h.store.pit_entries(ek),
            })
        if p == "/api/export.csv":
            ek = (q.get("event") or [h.event_key()])[0]
            table = (q.get("table") or ["teams"])[0]
            if not ek:
                return self._json({"error": "no event selected"}, 400)
            try:
                header, rows = _csv_table(h, ek, table)
            except KeyError:
                return self._json(
                    {"error": "table must be teams, lovat, scout or pit"}, 400)
            return self._csv(f"{ek}-{table}.csv", header, rows)
        if p == "/picklist/print":
            return self._file("picklist_print.html")
        if p == "/api/discover":
            return self._json({"urls": discover.urls(self.server.server_address[1])})
        return self._file(p)

    # ------------------------------------------------------------- POST
    def do_POST(self):
        u = urlparse(self.path)
        p = posixpath.normpath(u.path)
        h = Handler.hub
        body = self._body()

        if p == "/api/config":
            if not (self._is_local() or Handler.allow_remote_config):
                return self._json({
                    "error": "Hub settings can only be changed on the hub machine. "
                             "Open http://localhost:%d/ there." % self.server.server_address[1],
                }, 403)
            if not self._admin_ok():
                return self._locked_out()
            # Every credential arrives by copy and paste and a good half of them
            # arrive with something else attached - a header name, the quotes
            # from a code sample, a line break, or the Lovat key in the TBA box.
            # None of that used to be noticed: it went into the settings row
            # verbatim, this page said SET, and the service quietly returned
            # nothing for the rest of the event.
            cleaned, problems, warnings, notes = keyhygiene.check_all(
                {k: body[k] for k in keyhygiene.FIELDS if k in body}, _ai_provider(h, body))
            if problems:
                # Nothing is saved when any box is wrong. The boxes still hold
                # what was typed, so this costs a re-press and not a re-type,
                # and a half-applied save is the one state nobody can debug.
                return self._json({"error": "; ".join(problems.values()),
                                   "problems": problems}, 400)
            # Pointing the hub at a different event changes every screen in
            # the building at once, and the tab that did it is the one place it
            # does not show. Asked for a second time, once, and only when there
            # is something to lose - a hub being set up for the first time is
            # not made to confirm anything.
            switch = _event_switch(h, body)
            if switch and not body.get("confirmEventSwitch"):
                return self._json({
                    "error": "This hub is on %s and holds %d scouting record(s) for it. "
                             "Switching to %s hides all of it behind the new event."
                             % (switch["from"], switch["records"], switch["to"]),
                    "switch": switch,
                }, 409)
            for k in ("eventLevel", "ourTeam", "aiProvider", "aiCallLimit"):
                if k in body:
                    h.store.set(k, body[k])
            for k, v in cleaned.items():
                if k not in keyhygiene.CODES:   # hashed, never stored as typed
                    h.store.set(k, v)
            # An event key is pasted the same way and gets the same treatment:
            # a whole TBA address, a capitalised code and a stray space are all
            # what somebody means by "2026casf", and none of them work as typed.
            if "eventKey" in body:
                ek, warn = _event_key_check(body["eventKey"])
                h.store.set("eventKey", ek)
                if switch:
                    h.note("warn", "event switched from %s to %s; %d record(s) for %s are "
                                   "still in this database, behind the old key"
                                   % (switch["from"], ek, switch["records"], switch["from"]))
                if warn:
                    warnings["eventKey"] = warn
            # A trailing slash, a bare hostname or a copied "/api/push" are all
            # what somebody means by "the mirror address", and none of them
            # work as typed. Normalising here means the setup page can be a
            # plain text box.
            if "mirrorUrl" in body:
                h.store.set("mirrorUrl", _mirror_url(body["mirrorUrl"]))
            # The Setup page sends one value for both, as "provider:model", so
            # the two can never be saved disagreeing with each other. A bare id
            # typed by hand still resolves.
            if "aiModel" in body:
                raw = (body["aiModel"] or "").strip()
                provider, _, model = raw.rpartition(":")
                model = model.strip()
                if model.lower() in ("", "none"):
                    h.store.set("aiModel", "")
                    h.store.set("aiProvider", "none")
                else:
                    h.store.set("aiModel", model)
                    h.store.set("aiProvider",
                                provider.strip() or ai.provider_for(model) or "none")
            if "strategyPin" in cleaned:
                h.set_pin(cleaned["strategyPin"])
            if h.cfg("eventKey") and "eventKey" in body:
                h.store.put_event(h.cfg("eventKey"), level=body.get("eventLevel"))
            _poll_all(h, force=True)      # a save has just changed a key
            # Saved, and everything that was quietly fixed or looks off on the
            # way in, so the page can say so rather than leaving it to be found
            # out on the Saturday.
            return self._json({"ok": True, "warnings": warnings, "notes": notes})

        if p in ("/api/keycheck", "/api/keytest"):
            # Both are the Setup page's, and the Setup page is the hub laptop.
            if not (self._is_local() or Handler.allow_remote_config):
                return self._json({
                    "error": "Hub settings can only be changed on the hub machine. "
                             "Open http://localhost:%d/ there." % self.server.server_address[1],
                }, 403)
            if not self._admin_ok():
                return self._locked_out()
            if p == "/api/keytest":
                return self._json(h.verify_keys())
            # The same rules as the save, run as the boxes are filled in and
            # storing nothing. One copy of the rules, on the server, so what
            # the page says while you type cannot drift from what the save
            # does. The values it echoes are the ones just sent to it.
            cleaned, problems, warnings, notes = keyhygiene.check_all(
                {k: body[k] for k in keyhygiene.FIELDS if k in body}, _ai_provider(h, body))
            if "eventKey" in body:
                cleaned["eventKey"], warn = _event_key_check(body["eventKey"])
                if warn:
                    warnings["eventKey"] = warn
            return self._json({"cleaned": cleaned, "problems": problems,
                               "warnings": warnings, "notes": notes})

        if p == "/api/matchstart":
            # A list here used to reach dict.get() as an unhashable key and take
            # the thread down; a 50k-character one would have gone into the kv
            # row verbatim and stayed there for the event.
            mk = body.get("matchKey")
            if not isinstance(mk, str) or not mk.strip() or len(mk) > 120:
                return self._json({"error": "matchKey required"}, 400)
            sid = body.get("scoutId")
            rec = h.start_match(mk.strip(), (str(sid).strip()[:4].upper() if sid else "") or "?")
            return self._json({"ok": True, "clock": rec, "serverTime": time.time()})

        if p in ("/api/admin/unlock", "/api/admin/lock"):
            # The settings boundary, first: an admin code is not a way to
            # configure a hub from the stands.
            if not (self._is_local() or Handler.allow_remote_config):
                return self._json({
                    "error": "Hub settings can only be changed on the hub machine. "
                             "Open http://localhost:%d/ there." % self.server.server_address[1],
                }, 403)
            if p == "/api/admin/lock":
                h.end_admin(self.headers.get("X-Admin-Token"))
                return self._json({"ok": True})
            if not h.check_admin(body.get("code")):
                time.sleep(0.6)                  # blunt the guessing rate
                h.note("warn", "an admin password was refused at the hub")
                problem = h.admin_password()[1]
                return self._json({"ok": False,
                                   "error": problem or "That is not the admin password."}, 403)
            return self._json({"ok": True, "token": h.issue_admin_token(),
                               "minutes": h.ADMIN_MINUTES})

        if p == "/api/unlock":
            if not h.check_pin(body.get("pin")):
                time.sleep(0.6)                  # blunt the guessing rate
                return self._json({"ok": False}, 403)
            return self._json({"ok": True, "token": h.issue_token()})

        if p == "/api/picklist":
            if not self._unlocked():
                return self._json({"error": "picklist is read-only without the passcode"}, 403)
            # A patch, not a document. Two leads work this board at once during
            # alliance selection - that is what the second dashboard is FOR -
            # and both of them used to send the whole picklist on every edit.
            # Measured, three runs out of three: one lead marks 254 do-not-pick
            # while the other drags 1678 to the top, and the board ends up with
            # one of the two edits and no sign the other ever happened. The
            # write itself was always atomic; what collided was the payload.
            #
            # So only the fields actually sent are touched, and the flags come
            # as add/remove rather than as a list - two leads flagging two
            # different robots in the same second is not a conflict at all and
            # must not be resolved as one.
            def apply(cur):
                cur = {**Hub.PICKLIST_BASE, **(cur or {})}
                for k in ("weights", "weights2", "dnp", "order", "order2"):
                    if k in body:
                        cur[k] = body[k]
                dnp = [t for t in (_int(x) for x in (cur.get("dnp") or [])) if t is not None]
                for x in (body.get("dnpAdd") or []):
                    t = _int(x)
                    if t is not None and t not in dnp:
                        dnp.append(t)
                drop = {t for t in (_int(x) for x in (body.get("dnpRemove") or []))
                        if t is not None}
                cur["dnp"] = [t for t in dnp if t not in drop]
                # Which version of the board this is. The broadcast carries it
                # so a second dashboard can tell the echo of its own edit from
                # somebody else's, and a reader can tell it has fallen behind.
                cur["rev"] = int(cur.get("rev") or 0) + 1
                return cur, cur
            cur = h.store.mutate("picklist", apply, {})
            h.broadcast("picklist", {"updatedAt": time.time(), "rev": cur["rev"]})
            return self._json({"ok": True, "picklist": cur})

        if p == "/api/seat":
            seats = h.claim_seat(body.get("alliance"), body.get("station"),
                                 body.get("scoutId"), body.get("deviceId"))
            if seats is None:
                return self._json({"error": "a claim needs alliance red or blue, "
                                            "station 1-3, initials and a deviceId"}, 400)
            return self._json({"ok": True, "seats": seats})

        if p == "/api/unseat":
            # the lead can free a station from the dashboard when someone walks off
            key = h.normalize_seat(body.get("seat"))
            if not key:
                return self._json({"error": "unseat needs a station like red2"}, 400)
            seats = h.free_seat(key, body.get("deviceId"))
            return self._json({"ok": True, "seats": seats})

        if p == "/api/sync":
            # No token gate here on purpose: it is the endpoint every phone hits
            # at the buzzer, and a scout locked out on Saturday morning is a far
            # worse outcome than an open one. (There WAS a `hubToken` check, but
            # no client ever sent the header and no page could set the value, so
            # turning it on simply bricked every phone.)
            #
            # This used to be justified by "a hotspot you control is a closed
            # network". That reasoning is dead - team access points are not
            # allowed in the venue, so the hub now runs on venue wifi and anything
            # on that subnet can POST here. Accepted, not overlooked: the worst
            # case is junk scout rows, which last-write-wins and the solver's
            # outlier handling already absorb, and no key or config is reachable
            # from here (those are localhost-only). Revisit before this endpoint
            # is ever given something destructive to do.
            applied, rejected = 0, 0
            touched = set()
            for rec in self._rows(body, "scout"):
                try:
                    if h.store.upsert_scout(rec):
                        applied += 1
                        touched.add(rec["matchKey"])
                    else:
                        rejected += 1
                except Exception:
                    rejected += 1
            for rec in self._rows(body, "pit"):
                try:
                    _extract_photos(h.store, rec)
                    if h.store.upsert_pit(rec):
                        applied += 1
                    else:
                        rejected += 1
                except Exception:
                    rejected += 1
            for mk in touched:
                try:
                    h.solve_match(mk)
                except Exception as e:
                    sys.stderr.write(f"[solve] {mk}: {e}\n")
            if touched:
                h.request_recalibrate()
            if isinstance(body.get("who"), dict):
                h.touch(body["who"], "sync")
            if applied:
                h.record_writes(applied)
                h.note("info", f"sync accepted {applied} row(s)"
                               + (f", rejected {rejected} stale" if rejected else ""))
                h.broadcast("scout", {"applied": applied, "matches": sorted(touched)})
            return self._json({"ok": True, "applied": applied, "rejected": rejected,
                               "serverTime": time.time()})

        if p == "/api/nexus/webhook":
            # Answer immediately: Nexus does not retry non-200 and disables
            # webhooks that keep failing.
            expected = h.cfg("nexusToken")
            token = self.headers.get("Nexus-Token")
            self._json({"ok": True})
            if expected and token != expected:
                sys.stderr.write("[nexus] webhook rejected: bad Nexus-Token\n")
                return
            try:
                if "match" in body and "matches" not in body:
                    m = body.get("match") or {}
                    ek = body.get("eventKey") or h.event_key()
                    if ek and m.get("label"):
                        red = [_int(t) for t in (m.get("redTeams") or [])]
                        blue = [_int(t) for t in (m.get("blueTeams") or [])]
                        h.store.put_match(ek, resolve_match_key(h.store, ek, m["label"], red, blue),
                                          label=m["label"], red=red, blue=blue,
                                          status=m.get("status"), times=m.get("times"))
                        h.broadcast("matchStatus", {"match": m, "eventKey": ek})
                else:
                    h.apply_nexus_event(body)
            except Exception as e:
                sys.stderr.write(f"[nexus] webhook: {e}\n")
            return

        if p == "/api/import":
            if body.get("kind") != "frc-rebuilt-scouting-export":
                return self._json({"error": "not a scouting export file"}, 400)
            applied = rejected = 0
            touched = set()
            for rec in self._rows(body, "scout"):
                try:
                    if h.store.upsert_scout(rec):
                        applied += 1
                        touched.add(rec["matchKey"])
                    else:
                        rejected += 1
                except Exception:
                    rejected += 1
            for rec in self._rows(body, "pit"):
                try:
                    _extract_photos(h.store, rec)
                    applied += 1 if h.store.upsert_pit(rec) else 0
                except Exception:
                    rejected += 1
            for mk in touched:
                try:
                    h.solve_match(mk)
                except Exception:
                    pass
            if applied:
                h.broadcast("scout", {"applied": applied, "matches": sorted(touched)})
            return self._json({"ok": True, "applied": applied, "rejected": rejected})

        if p == "/api/mirror/push":
            # Same boundary as the settings that configure it: the hub machine,
            # or the strategy passcode. Pushing is not destructive, but it does
            # send the whole event somewhere, and that is not a button for
            # anyone who found the wifi.
            if not (self._is_local() or self._unlocked_strict()):
                return self._json({"error": "hub machine or strategy passcode only"}, 403)
            # 200 either way, with the reason in the body. A failed push is
            # not a fault in this hub - the mirror is down, or the key is
            # wrong - and an HTTP error code here only got the setup page a
            # thrown exception where it wanted something to put on screen.
            return self._json(offsite.push_once(h, force=True))

        if p == "/api/refresh":
            # 200 either way: "already asked a moment ago" is not a failure, and
            # the button has nothing useful to do with an error code.
            return self._json({"ok": True, "polled": _poll_all(h)})

        if p == "/api/resolve":
            mk = body.get("matchKey")
            if mk:
                h.solve_match(mk)
                h.request_recalibrate()
            return self._json({"ok": True})

        if p.startswith("/api/ai/"):
            return self._ai(p[len("/api/ai/"):], body)

        self.send_error(404, "Not found")

    # --------------------------------------------------------------- AI
    def _ai(self, kind, body):
        """Generated text, and the three things that gate it.

        Money, first: a generated answer costs the team real credit, so this is
        the one read path held to the strict lock - a configured passcode and a
        valid token, or you are sitting at the hub. With no passcode set that
        means the hub machine only, which is the same call `_unlocked_strict`
        already makes about scout quality scores and for the same reason.

        Nothing here writes data. Answers are cached under an `ai:` key as
        generated text and never merged into a team record, the solver, or the
        picklist.
        """
        h = Handler.hub
        if not (self._unlocked_strict() or self._is_local()):
            return self._json({"error": "Strategy passcode required."}, 403)
        client = h.ai()
        if not client.ok:
            return self._json({"configured": False})
        ek = h.event_key()
        if not ek:
            return self._json({"configured": True, "text": None, "reason": "no event set"})

        summary = analytics.event_summary(h.store, ek)
        teams = summary.get("teams") or {}
        if kind.startswith("notes/"):
            team = _int(kind.split("/", 1)[1])
            rec = teams.get(team) or teams.get(str(team))
            if not rec:
                return self._json({"configured": True, "text": None,
                                   "reason": "nothing scouted for that team"})
            payload = _ai_notes_payload(rec)
            if not (payload["ourNotes"] or payload["lovatNotes"]):
                return self._json({"configured": True, "text": None,
                                   "reason": "no notes typed yet"})
            cache, system, user = (f"ai:notes:{ek}:{team}", AI_NOTES_TASK,
                                   json.dumps(payload, sort_keys=True))
        elif kind.startswith("match/"):
            mk = kind.split("/", 1)[1]
            match = h.store.match(ek, mk)
            if not match:
                return self._json({"configured": True, "text": None,
                                   "reason": "no such match on this schedule"})
            lineup = (match.get("red") or []) + (match.get("blue") or [])
            if not lineup:
                return self._json({"configured": True, "text": None,
                                   "reason": "no lineup for that match yet"})
            payload = _ai_match_payload(summary, match)
            if not (payload["red"]["teams"] or payload["blue"]["teams"]):
                return self._json({"configured": True, "text": None,
                                   "reason": "nothing scouted on either alliance yet"})
            cache, system, user = (f"ai:match:{ek}:{mk}", AI_MATCH_TASK,
                                   json.dumps(payload, sort_keys=True))
        elif kind == "picklist":
            order = [t for t in (_int(x) for x in (body.get("order") or [])) if t]
            rows = [_ai_team_payload(teams.get(t) or teams.get(str(t)), i + 1)
                    for i, t in enumerate(order[:10])]
            rows = [r for r in rows if r]
            if not rows:
                return self._json({"configured": True, "text": None,
                                   "reason": "nothing ranked yet"})
            cache, system, user = (f"ai:picklist:{ek}", AI_PICKLIST_TASK,
                                   json.dumps({"ranking": rows}, sort_keys=True))
        elif kind == "ask":
            question = (body.get("question") or "").strip()[:400]
            if not question:
                return self._json({"configured": True, "text": None,
                                   "reason": "ask a question first"})
            # Capped: a big event is 75 teams and the whole point is a small,
            # readable payload. Teams nobody has data on add nothing to answer with.
            known = sorted((t for t in teams.values()
                            if t.get("matchesScouted") or (t.get("lovat") or {}).get("matches")),
                           key=lambda t: -(t.get("matchesScouted") or 0))[:60]
            rows = [r for r in (_ai_team_payload(t) for t in known) if r]
            return self._ai_send(None, AI_ASK_TASK,
                                 json.dumps({"question": question, "teams": rows},
                                            sort_keys=True), AI_ASK_TOKENS)
        else:
            return self.send_error(404, "Not found")

        # Regenerate only when the data behind the answer changed - or when the
        # button was pressed on purpose.
        stamp = hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]
        cached = h.store.get(cache) or {}
        if cached.get("stamp") == stamp and not body.get("force"):
            return self._json({**cached, "configured": True, "cached": True})
        # A peek reads the cache and stops. Opening a team page must never spend
        # the team's credit on its own - generating is always a button press.
        if body.get("peek"):
            return self._json({"configured": True, "text": None, "peek": True,
                               "stale": bool(cached.get("text"))})
        return self._ai_send(cache, system, user, AI_PANEL_TOKENS, stamp)

    def _ai_send(self, cache, task, user, max_tokens, stamp=None):
        h = Handler.hub
        client = h.ai()
        if not h.ai_charge():
            return self._json({"configured": True, "text": None,
                               "reason": f"answer ceiling reached ({h.ai_ceiling()}) - "
                                         "raise the limit in Setup"})
        text, reason = client.ask(ai.GROUND_RULES + "\n\n" + task, user, max_tokens)
        if not text:
            # Offline at a venue is the normal case, not an error worth a dialog.
            # The reason separates that from an answer that was cut off or
            # declined, which need different things from the person reading it.
            return self._json({"configured": True, "text": None, "reason": reason})
        out = {"text": text, "provider": client.provider, "model": client.label,
               "at": time.time(), "stamp": stamp}
        if cache:
            h.store.set(cache, out)
        return self._json({**out, "configured": True, "cached": False})

    # -------------------------------------------------------------- SSE
    def _stream(self):
        who = {k: (parse_qs(urlparse(self.path).query).get(k) or [None])[0]
               for k in ("deviceId", "scoutId", "seat")}
        q = Handler.hub.subscribe(who)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            last_touch = time.time()
            while True:
                try:
                    msg = q.get(timeout=SSE_KEEPALIVE_SECONDS)
                    self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")  # keeps proxies and phones from dropping it
                self.wfile.flush()
                # A stream we can still write to IS the phone reporting in.
                # "Last heard" used to count from the moment the phone
                # connected and never move, so a phone that stayed up all
                # morning read "gone quiet 4h ago" on the crew board and the
                # lead was sent to check wifi that was working perfectly.
                if who.get("deviceId") and time.time() - last_touch > 60:
                    last_touch = time.time()
                    Handler.hub.touch(who, "connected")
        except Exception:
            pass
        finally:
            Handler.hub.unsubscribe(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # socketserver defaults this to 5. Six phones flush the moment the buzzer
    # goes, two dashboards poll on their own timers and the pit tablet syncs
    # whenever it likes, so connections genuinely do arrive in bursts - and a
    # burst past the backlog is refused by the kernel before any of this code
    # runs. Measured: 60 of 200 simultaneous connects were reset at 5. A queued
    # scouting record survives that and retries, but a seat claim or a match
    # clock is a one-shot, and the clock is what the solver's accuracy rests on.
    request_queue_size = 128


def _set_admin_password():
    """Ask for a password twice and write it into `.env`.  True if it was.

    This exists because the alternative is telling a scouting lead to base64
    something by hand and create a dotfile on a Windows laptop, where Explorer
    hides the extension and will happily make `.env.txt`. Nothing about a
    password should require that.
    """
    import getpass
    print("\n  A blank password removes it: the panel still opens locked, and\n"
          "  unlocking it is then just the button.\n")
    try:
        first = getpass.getpass("  new admin password: ")
        again = getpass.getpass("  the same one again: ")
    except (EOFError, KeyboardInterrupt):
        print("\n  nothing changed")
        return False
    if first != again:
        print("  those two do not match - nothing changed")
        return False
    if not first.strip():
        path = envfile.write(envfile.ADMIN, "")
        print(f"  admin password removed from {path}")
        return True
    path = envfile.write(envfile.ADMIN, envfile.encode(first))
    os.environ[envfile.ADMIN] = envfile.encode(first)
    print(f"  written to {path}, base64-encoded - which is not encryption:\n"
          "  anyone who can read that file can read the password. Keep the file\n"
          "  off shared drives and out of the repository (.gitignore has it).")
    return True


def main():
    # Before anything reads an environment variable. A real one always wins
    # over the file - a mirror host sets these through systemd, and a checkout
    # must never quietly override the machine.
    from_file = envfile.load()

    ap = argparse.ArgumentParser(description="FRC 2026 REBUILT scouting server")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-mdns", action="store_true")
    ap.add_argument("--no-poll", action="store_true",
                    help="do not reach out to Nexus, TBA, FRC Events, Statbotics or Lovat "
                         "(the hub still serves everything already in the database)")
    ap.add_argument("--allow-remote-config", action="store_true",
                    help="let any device on the network change hub settings and API keys "
                         "(default: the hub machine only)")
    ap.add_argument("--set-admin-password", action="store_true",
                    help="type a new admin password and write it into .env, then carry on "
                         "serving. This is the only way it is set, and the way back in when "
                         "nobody can remember it")
    args = ap.parse_args()

    if args.set_admin_password and not _set_admin_password():
        return 1

    store = Store(args.db) if args.db else Store()
    hub = Hub(store)
    # Every unlock dies with the process. The panel opens locked on every
    # reload anyway, and a token surviving a restart would outlive a password
    # changed in .env while the hub was stopped - which is when it is changed.
    store.set("adminTokens", {})
    Handler.hub = hub
    Handler.allow_remote_config = args.allow_remote_config

    hub.port = args.port
    hub.note("info", f"hub started on port {args.port}")
    # Catch up on anything solved-but-not-stored before we start serving, so the
    # first dashboard load shows real numbers rather than zeros.
    try:
        hub.reconcile()
    except Exception as e:
        sys.stderr.write(f"[reconcile] {e}\n")
    srv = Server(("0.0.0.0", args.port), Handler)
    # Serving a database somebody built offline is a real mode, not just a test
    # one: Statbotics needs no key, so a hub with no event on the internet still
    # asks about one, and gets a truthful "nothing" back that overwrites what is
    # already there. A hub told not to poll says so in its log, because silence
    # from a source is otherwise indistinguishable from a source being down.
    if args.no_poll:
        hub.note("info", "polling disabled (--no-poll): serving what is already stored")
    else:
        threading.Thread(target=hub.run_poller, daemon=True, name="poller").start()
    threading.Thread(target=hub.run_snapshots, daemon=True, name="snapshots").start()

    responder = None
    if not args.no_mdns:
        ips = discover.local_ipv4s()
        if ips:
            responder = discover.MDNSResponder(ips[0])
            responder.start()

    print(discover.banner(args.port))
    # Where the admin password came from, and never what it is. A hub whose
    # .env has a typo in it is a hub nobody can unlock, and the person who can
    # fix that is standing in front of this window.
    password, problem = envfile.admin_password()
    if problem:
        print(f"  !! {problem}\n")
    elif password:
        print("  Admin password: loaded from %s\n"
              % (".env" if envfile.ADMIN in from_file else "the environment"))
    else:
        print("  Admin password: none set - UNLOCK on the admin page is one button.\n"
              "                  `python3 server/hub.py --set-admin-password` sets one.\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  shutting down")
    finally:
        hub.stop_flag.set()
        if responder:
            responder.stop()
        srv.shutdown()


if __name__ == "__main__":
    main()
