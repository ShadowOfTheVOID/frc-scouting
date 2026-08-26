#!/usr/bin/env python3
"""FRC 2026 REBUILT scouting server.

Python 3 standard library only - no pip install, nothing to build.  Runs the
same on macOS (testing) and Windows (competition).

    python3 server/hub.py [--port 8080] [--db data/scouting.db]
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import analytics
import discover
import rules
import solve
import sources
from store import Store

WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")
WEB_ROOT = os.path.abspath(WEB_ROOT)

NEXUS_POLL_SECONDS = 20
TBA_POLL_SECONDS = 45
# EPA is a season-long fit; it barely moves inside one event, so polling it
# hard buys nothing and Statbotics is the one source we expect to be down.
STATBOTICS_POLL_SECONDS = 600
# Only ever used to fill in matches TBA has not posted yet, so it can be slow.
FRC_EVENTS_POLL_SECONDS = 60
# The whole event lives on one laptop that gets carried around a venue all day.
SNAPSHOT_SECONDS = 600
SNAPSHOT_KEEP = 12


# ------------------------------------------------------------------- hub

class Hub:
    def __init__(self, store):
        self.store = store
        self.subs = []
        self.subs_lock = threading.Lock()
        self.statbotics = sources.Statbotics()
        self.last_nexus_at = 0.0
        self.stop_flag = threading.Event()
        self.port = 8080
        self.status = {"nexus": None, "tba": None, "statbotics": None,
                       "frcEvents": None, "lastUpdate": None}
        self.last_snapshot = None
        self.started_at = time.time()
        self._recal_lock = threading.Lock()
        self._recal_pending = False
        self.writes = []          # timestamps, for writes/min
        self.log = []             # ring buffer for the event log panel

    def note(self, level, msg):
        self.log.append({"at": time.time(), "level": level, "msg": msg})
        if len(self.log) > 300:
            del self.log[:100]

    def diag(self):
        now = time.time()
        self.writes = [t for t in self.writes if now - t < 300]
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
            svc("solver", True, f"multipliers fitted from {self.store.get('multipliersFittedFrom') or 0} windows"),
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
            "writesPerMin": round(len(self.writes) / 5.0, 1),
            "sseClients": len(self.subs),
            "services": services,
            "addresses": discover.urls(self.port),
            "seats": self.seats(),
            "log": list(reversed(self.log[-40:])),
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

    # ------------------------------------------------------------- SSE
    def subscribe(self, who=None):
        q = queue.Queue(maxsize=64)
        q.who = who or {}
        q.since = time.time()
        with self.subs_lock:
            self.subs.append(q)
        if who and who.get("scoutId"):
            self.touch(who, "connected")
        return q

    def unsubscribe(self, q):
        with self.subs_lock:
            if q in self.subs:
                self.subs.remove(q)

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

    def crew(self):
        """One row per station: who is on it, are they live, are they behind."""
        now = time.time()
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
                "lastSeenSec": int(now - dev["at"]) if dev.get("at") else None,
                "lastMatch": last[1] if last else None,
                "lastMatchAgoSec": int(now - last[0]) if last else None,
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
        """Nexus live event status, from push or poll.  Ordering guarded by dataAsOfTime."""
        if not payload:
            return False
        as_of = float(payload.get("dataAsOfTime") or 0)
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
            self.store.put_match(
                ek, resolve_match_key(self.store, ek, label, red, blue),
                label=label, play_order=i, red=red, blue=blue,
                status=m.get("status"), times=m.get("times"))
        self.store.set("nexusLive", {
            "nowQueuing": payload.get("nowQueuing"),
            "announcements": payload.get("announcements") or [],
            "partsRequests": payload.get("partsRequests") or [],
            "dataAsOfTime": as_of,
        })
        self.status["nexus"] = time.time()
        self.broadcast("nexus", {
            "nowQueuing": payload.get("nowQueuing"),
            "matches": payload.get("matches") or [],
            "announcements": payload.get("announcements") or [],
            "partsRequests": payload.get("partsRequests") or [],
        })
        return True

    def poll_nexus(self):
        ek = self.event_key()
        nx = self.nexus()
        if not (ek and nx.ok):
            return
        self.apply_nexus_event(nx.event(ek))
        for name, fn in (("pits", nx.pits), ("pitMap", nx.pit_map),
                         ("inspection", nx.inspection), ("alliances", nx.alliances)):
            data = fn(ek)
            if data is None:
                # 404 (no pit map at this event) or a transient failure: keep what
                # we have for THIS event rather than blanking the screen.
                continue
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

    # ----------------------------------------------------------- picklist
    #
    # A shared passcode, not per-person accounts and not IP allow-listing.
    # Nothing about who someone is gets stored - only whether they know the
    # code - and the lead can rotate it before alliance selection.

    def set_pin(self, pin):
        if not pin:
            self.store.set("strategyPin", None)
            self.store.set("unlockTokens", {})
            return
        salt = secrets.token_hex(8)
        digest = hashlib.sha256((salt + pin).encode()).hexdigest()
        self.store.set("strategyPin", {"salt": salt, "hash": digest})
        self.store.set("unlockTokens", {})      # a new code invalidates old ones

    def pin_set(self):
        return bool(self.store.get("strategyPin"))

    def check_pin(self, pin):
        rec = self.store.get("strategyPin")
        if not rec:
            return True                          # no code configured: open
        want = rec["hash"]
        got = hashlib.sha256((rec["salt"] + (pin or "")).encode()).hexdigest()
        return hmac.compare_digest(want, got)

    def issue_token(self):
        tok = secrets.token_urlsafe(18)

        def apply(toks):
            toks = dict(toks or {})
            toks[tok] = time.time() + 16 * 3600    # lasts the competition day
            return {k: v for k, v in toks.items() if v > time.time()}, None
        self.store.mutate("unlockTokens", apply, {})
        return tok

    def token_ok(self, tok):
        if not self.pin_set():
            return True
        toks = self.store.get("unlockTokens") or {}
        exp = toks.get(tok or "")
        return bool(exp and exp > time.time())

    def picklist(self):
        # Two lists, because alliance selection asks two different questions:
        # the best robot left, and the best complement to the one we have.
        base = {"weights": {}, "weights2": {}, "dnp": [], "order": [], "order2": []}
        return {**base, **(self.store.get("picklist") or {})}

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
    def seat_history(self, limit=12):
        return (self.store.get("seatLog") or [])[-limit:][::-1]

    def claim_seat(self, alliance, station, scout_id, device_id):
        """Record who is sitting where. Returns the full seat map.

        Not enforced - a scout who really is in that chair must always win.
        The point is that the phone can SHOW the clash before it costs a match.
        """
        key = f"{alliance}{station}"

        def apply(seats):
            seats = dict(seats or {})
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

        # A phone that has just been displaced must be told, or two scouts keep
        # logging the same robot and neither knows.
        displaced = None
        if prev and prev.get("deviceId") and prev["deviceId"] != device_id:
            displaced = prev["deviceId"]

        entry = {"at": time.time(), "seat": key, "scoutId": scout_id,
                 "from": (prev or {}).get("scoutId"), "vacated": vacated}
        self.store.mutate("seatLog", lambda log: ((list(log or []) + [entry])[-60:], None), [])
        self.note("info", f"{scout_id} took {key}"
                          + (f" from {prev['scoutId']}" if prev and prev.get("scoutId") else ""))

        self.broadcast("seats", {"seats": seats, "displaced": displaced, "seat": key,
                                 "scoutId": scout_id})
        return seats

    def seats(self):
        cutoff = time.time() - 3 * 3600

        def apply(seats):
            seats = seats or {}
            live = {k: v for k, v in seats.items() if v.get("at", 0) > cutoff}
            return (live if live != seats else None), live
        return self.store.mutate("seats", apply, {})

    # ---------------------------------------------------------- solving
    CLOCK_FIX_LIMIT = 180          # seconds; beyond this it is not a late tap

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
        out = []
        for iv in intervals or []:
            start = float(iv["start"]) + offset
            ph = rules.phase_at(start)
            j = dict(iv)
            j["start"] = start
            j["end"] = float(iv.get("end", iv["start"])) + offset
            j["phase"] = ph["id"] if ph else None
            out.append(j)
        return out

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
                payload = (e or {}).get("payload", {})
                ivs = payload.get("intervals") or []
                # Only re-anchor phones that were on the shared clock. A phone
                # that fell back to its own timeline has a different origin, and
                # shifting it by someone else's offset makes it worse, not better.
                if offset and payload.get("clockShared"):
                    ivs = self._rephase(ivs, offset)
                robots.append({"team": t, "intervals": ivs})
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
            shared = sum(1 for e in entries if (e.get("payload") or {}).get("clockShared"))
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
                for pid, total in (info.get("windows") or {}).items():
                    if not total:
                        continue
                    secs = {b: 0.0 for b in rules.BUCKETS}
                    seen = False
                    for t in (m.get(alliance) or []):
                        e = entries.get(t)
                        for iv in (e or {}).get("payload", {}).get("intervals") or []:
                            if iv.get("phase") != pid:
                                continue
                            seen = True
                            secs[iv.get("intensity", "steady")] = secs.get(iv.get("intensity", "steady"), 0.0) + \
                                max(0.0, float(iv.get("end", iv["start"])) - float(iv["start"]))
                    if seen:
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
        next_nexus = next_tba = next_stat = next_frc = 0.0
        while not self.stop_flag.is_set():
            now = time.time()
            try:
                if now >= next_nexus:
                    self.poll_nexus()
                    next_nexus = now + NEXUS_POLL_SECONDS
                if now >= next_tba:
                    self.poll_tba()
                    next_tba = now + TBA_POLL_SECONDS
                if now >= next_frc:
                    self.poll_frc_events()
                    next_frc = now + FRC_EVENTS_POLL_SECONDS
                if now >= next_stat:
                    self.poll_statbotics()
                    next_stat = now + STATBOTICS_POLL_SECONDS
                self.status["lastUpdate"] = time.time()
            except Exception as e:  # a poller crash must never take the server down
                self.note("error", f"poll failed: {e}")
                sys.stderr.write(f"[poll] {e}\n")
            self.stop_flag.wait(2.0)


def _gz(data):
    buf = io.BytesIO()
    with _gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6, mtime=0) as fh:
        fh.write(data)
    return buf.getvalue()


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
                mime = head[5:].split(";")[0] or "image/jpeg"
                raw = base64.b64decode(b64)
                pid = hashlib.sha1(raw).hexdigest()[:16]
                store.put_photo(pid, rec["eventKey"], rec["team"], mime, raw)
                kept.append(pid)
            except Exception:
                continue
        elif isinstance(src, str):
            kept.append(src)          # already an id
    payload["photos"] = kept
    rec["payload"] = payload


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
                  "feedPct", "feedSecs", "defenseSecs", "defenseAgainst", "defendedBy",
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
    return round(sum(max(0.0, float(iv.get("end", iv["start"])) - float(iv["start"]))
                     for iv in (intervals or [])), 1)


def _poll_all(h):
    """Kick every source off the request thread. A poll must never block a save."""
    for fn in (h.poll_nexus, h.poll_tba, h.poll_frc_events, h.poll_statbotics):
        threading.Thread(target=fn, daemon=True).start()


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
_QUAL_LABEL = re.compile(r"^\s*(?:qualification|qual|q)\s*(\d+)\s*$", re.I)


def _slug_match_key(event_key, label):
    return f"{event_key}_{label.lower().replace(' ', '')}"


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
    def _json(self, obj, code=200):
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        enc = None
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body, enc = _gz(body), "gzip"
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _csv(self, filename, header, rows):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
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

    def _unlocked_strict(self):
        """Like _unlocked, but an unset passcode does not mean "everyone".

        _unlocked is deliberately open when no code is configured - a read-only
        picklist should never be gated by accident. Scout quality data is the
        opposite: absent a passcode it stays on the hub machine only.
        """
        return Handler.hub.pin_set() and self._unlocked()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

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
        with open(path, "rb") as fh:
            data = fh.read()

        enc = None
        compressible = path.endswith((".js", ".css", ".html", ".json", ".webmanifest", ".svg"))
        if compressible and len(data) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            data, enc = _gz(data), "gzip"

        # Fonts and icons are content-stable and were being revalidated on every
        # load; markup and code stay no-cache so a fix reaches phones instantly.
        immutable = "/fonts/" in path.replace(os.sep, "/") or "/icons/" in path.replace(os.sep, "/")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(data)))
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
            return self._json({
                "eventKey": h.event_key(),
                "eventLevel": (h.store.event(h.event_key()) or {}).get("level", "regional") if h.event_key() else "regional",
                "keys": {"tba": bool(h.cfg("tbaKey")), "nexus": bool(h.cfg("nexusKey")),
                         "frcEvents": h.frc_events().ok},
                "picklistLocked": h.pin_set(),
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
            })
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
            return self._json(analytics.event_summary(
                h.store, ek, include_scouts=self._is_local() or self._unlocked_strict()))
        if p.startswith("/api/photo/"):
            pid = p.rsplit("/", 1)[-1]
            mime, data = h.store.photo(pid)
            if not data:
                return self.send_error(404, "no such photo")
            self.send_response(200)
            self.send_header("Content-Type", mime or "image/jpeg")
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
            return self._json(h.crew())
        if p == "/api/picklist":
            # anyone may read the board - scouts want to know who we are picking.
            # Only changing it needs the passcode (see the POST handler).
            return self._json({**h.picklist(), "canEdit": self._unlocked(),
                               "locked": h.pin_set()})
        if p == "/api/seatlog":
            return self._json(h.seat_history())
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
                return self._json({"error": "table must be teams, scout or pit"}, 400)
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
            for k in ("eventKey", "tbaKey", "nexusKey", "nexusToken", "eventLevel", "ourTeam",
                      "frcEventsUser", "frcEventsToken"):
                if k in body:
                    h.store.set(k, body[k])
            if "strategyPin" in body:
                h.set_pin(body["strategyPin"])
            if body.get("eventKey"):
                h.store.put_event(body["eventKey"], level=body.get("eventLevel"))
            _poll_all(h)
            return self._json({"ok": True})

        if p == "/api/matchstart":
            mk = body.get("matchKey")
            if not mk:
                return self._json({"error": "matchKey required"}, 400)
            rec = h.start_match(mk, body.get("scoutId") or "?")
            return self._json({"ok": True, "clock": rec, "serverTime": time.time()})

        if p == "/api/unlock":
            if not h.check_pin(body.get("pin")):
                time.sleep(0.6)                  # blunt the guessing rate
                return self._json({"ok": False}, 403)
            return self._json({"ok": True, "token": h.issue_token()})

        if p == "/api/picklist":
            if not self._unlocked():
                return self._json({"error": "picklist is read-only without the passcode"}, 403)
            base = {"weights": {}, "weights2": {}, "dnp": [], "order": [], "order2": []}

            def apply(cur):
                cur = {**base, **(cur or {})}
                for k in ("weights", "weights2", "dnp", "order", "order2"):
                    if k in body:
                        cur[k] = body[k]
                return cur, cur
            cur = h.store.mutate("picklist", apply, {})
            h.broadcast("picklist", {"updatedAt": time.time()})
            return self._json({"ok": True, "picklist": cur})

        if p == "/api/seat":
            seats = h.claim_seat(body.get("alliance"), body.get("station"),
                                 body.get("scoutId"), body.get("deviceId"))
            h.touch({"deviceId": body.get("deviceId"), "scoutId": body.get("scoutId"),
                     "seat": f"{body.get('alliance')}{body.get('station')}"}, "seated")
            return self._json({"ok": True, "seats": seats})

        if p == "/api/unseat":
            # the lead can free a station from the dashboard when someone walks off
            def apply(seats):
                seats = dict(seats or {})
                seats.pop(body.get("seat"), None)
                return seats, seats
            seats = h.store.mutate("seats", apply, {})
            h.broadcast("seats", seats)
            return self._json({"ok": True, "seats": seats})

        if p == "/api/sync":
            # No token gate here on purpose. It is the endpoint every phone hits
            # at the buzzer; a hotspot you control is already a closed network,
            # and a scout locked out on Saturday morning is a far worse outcome
            # than an open one. (There WAS a `hubToken` check, but no client ever
            # sent the header and no page could set the value, so turning it on
            # simply bricked every phone.)
            applied, rejected = 0, 0
            touched = set()
            for rec in body.get("scout") or []:
                try:
                    if h.store.upsert_scout(rec):
                        applied += 1
                        touched.add(rec["matchKey"])
                    else:
                        rejected += 1
                except Exception:
                    rejected += 1
            for rec in body.get("pit") or []:
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
            if body.get("who"):
                h.touch(body["who"], "sync")
            if applied:
                h.writes.extend([time.time()] * applied)
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
            for rec in body.get("scout") or []:
                try:
                    if h.store.upsert_scout(rec):
                        applied += 1
                        touched.add(rec["matchKey"])
                    else:
                        rejected += 1
                except Exception:
                    rejected += 1
            for rec in body.get("pit") or []:
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

        if p == "/api/refresh":
            _poll_all(h)
            return self._json({"ok": True})

        if p == "/api/resolve":
            mk = body.get("matchKey")
            if mk:
                h.solve_match(mk)
                h.request_recalibrate()
            return self._json({"ok": True})

        self.send_error(404, "Not found")

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
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")  # keeps proxies and phones from dropping it
                self.wfile.flush()
        except Exception:
            pass
        finally:
            Handler.hub.unsubscribe(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser(description="FRC 2026 REBUILT scouting server")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-mdns", action="store_true")
    ap.add_argument("--allow-remote-config", action="store_true",
                    help="let any device on the network change hub settings and API keys "
                         "(default: the hub machine only)")
    args = ap.parse_args()

    store = Store(args.db) if args.db else Store()
    hub = Hub(store)
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
    threading.Thread(target=hub.run_poller, daemon=True, name="poller").start()
    threading.Thread(target=hub.run_snapshots, daemon=True, name="snapshots").start()

    responder = None
    if not args.no_mdns:
        ips = discover.local_ipv4s()
        if ips:
            responder = discover.MDNSResponder(ips[0])
            responder.start()

    print(discover.banner(args.port))
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
