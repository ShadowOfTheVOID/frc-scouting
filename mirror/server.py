"""The off-site mirror.  Run this on a real host; the hub pushes to it.⁠​‌​‌​‌​​​‌‌​​‌​‌​‌‌​​‌​​​‌‌​​‌​​​​‌​​​​​​‌​‌​‌​​​‌‌​‌‌‌‌​‌‌​‌‌‌​​‌‌‌‌​​‌​​‌​​​​​​​‌​‌‌​‌​​‌​​​​​​‌‌​​‌‌‌​‌‌​‌​​‌​‌‌‌​‌​​​‌‌​‌​​​​‌‌‌​‌​‌​‌‌​​​‌​​​‌​‌‌‌​​‌‌​​​‌‌​‌‌​‌‌‌‌​‌‌​‌‌​‌​​‌​‌‌‌‌​‌​‌​​‌‌​‌‌​‌​​​​‌‌​​​​‌​‌‌​​‌​​​‌‌​‌‌‌‌​‌‌‌​‌‌‌​‌​​‌‌‌‌​‌‌​​‌‌​​‌​‌​‌​​​‌‌​‌​​​​‌‌​​‌​‌​‌​‌​‌‌​​‌​​‌‌‌‌​‌​​‌​​‌​‌​​​‌​​​​‌​​​​​​​‌​‌‌​‌​​‌​​​​​​​‌‌​​‌​​​‌‌​​​​​​‌‌​​‌​​​‌‌​‌‌​⁠

    MIRROR_PUSH_KEY=... MIRROR_VIEW_PASSCODE=... python3 mirror/server.py

It is deliberately the dumbest thing that could work. It does not scout, does
not solve, does not hold an API key for anybody, and never opens a connection
of its own. Two things arrive from the hub - a bundle, and the pit photos it
does not already have - and everything else is reading those back out.

Two secrets, and they are not the same secret:

  MIRROR_PUSH_KEY       the hub proves it is the hub.  Write.
  MIRROR_VIEW_PASSCODE  a person proves they are on the team.  Read.

Splitting them is the point. The passcode gets shared around a team over a
weekend and typed on phones in a stands; the push key lives in one settings
field on one laptop. If the passcode gets out, somebody reads last weekend's
scouting - annoying. If they were the same string, they could overwrite it.

There is no TLS here on purpose: put it behind a reverse proxy that already
has a certificate. `deploy/` has a Caddyfile and a systemd unit that do exactly
that for scouting.systemoverload.org.
"""
import argparse
import base64
import binascii
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import posixpath
import re
import secrets
import socketserver
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from mirrordb import Store  # noqa: E402

# The two secrets may come from a `.env` beside the checkout as well as from the
# environment, which is what makes a mirror runnable on a laptop for five
# minutes to see it work. Guarded, because this directory is meant to survive
# being copied somewhere on its own: without the hub's half of the repository
# there is no .env reader, and systemd's own Environment= lines - which is what
# a real host should use anyway - do not need one.
try:
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "server"))
    import envfile                                        # noqa: E402
except ImportError:                                       # pragma: no cover
    envfile = None
# The rules for a pit question, from the same checkout as the hub, so the two
# can never disagree about what one is. Without them the editor is off and
# everything else on the mirror still works.
try:
    import pitquestions                                   # noqa: E402
except ImportError:                                       # pragma: no cover
    pitquestions = None

WEB_ROOT = os.path.join(_HERE, "web")
#: The fonts, the colours of both themes and the light/dark switch are the
#: hub's, byte for byte, so the mirror reads as the same application rather
#: than a lookalike. Nothing else is served from there - the dashboard's CSS
#: assumes a 1240px laptop, and this page has to work on a phone in a stands.
SHARED_ROOT = os.path.abspath(os.path.join(_HERE, "..", "web"))
SHARED_OK = ("/fonts/", "/css/fonts.css", "/css/theme.css", "/js/theme.js")

PROTOCOL = 1
PORT = 8060

#: How many pushes to keep per event. At a push a minute with the skip-if-
#: unchanged rule, this is most of a competition day of distinct states.
KEEP_REVISIONS = 60

#: A bundle is JSON with every scouting record in it; gzipped it is usually
#: under a megabyte, but a photo-heavy import can be much larger.
MAX_BODY = 96 * 1024 * 1024

TOKEN_HOURS = 16

#: Failed passcode attempts allowed from one address before it is made to wait.
#: This is on the public internet, where the hub's flat 0.6s pause is not
#: enough on its own.
UNLOCK_FAILS = 8
UNLOCK_WINDOW = 600

#: Pit scouting done on this site. How long an open pit form holds its robot
#: with nobody renewing it (the page renews once a minute), and the limits on
#: what one save may carry - a pit record is a handful of answers and a few
#: phone photos, already shrunk on the phone.
PIT_CLAIM_SECONDS = 180
PIT_PHOTOS_MAX = 12
PIT_PHOTO_BYTES = 4 * 1024 * 1024
PIT_PAYLOAD_BYTES = 64 * 1024
PHOTO_ID = re.compile(r"^[0-9a-f]{8,40}$")
#: The built-in pit questions. A robot with four of these answered is done.
PIT_FIELDS = ("drivetrain", "shooter", "maxClimb", "stockpile", "groundPickup")


class Mirror:
    """Config, secrets and the token table.  One instance, shared by threads."""

    def __init__(self, store, push_key, view_passcode, open_read=False):
        self.store = store
        self.push_key = push_key or ""
        self.open_read = bool(open_read)
        self.started_at = time.time()
        self.pushes = 0
        self.last_push_from = None
        self._fails = {}
        self._fails_lock = threading.Lock()

        # The passcode is never stored, only a salted hash of it, and the salt
        # is per-install rather than per-process so restarting the site does
        # not sign every phone in the stands out.
        self.view_salt = store.get("viewSalt")
        if not self.view_salt:
            self.view_salt = secrets.token_hex(8)
            store.set("viewSalt", self.view_salt)
        self.view_hash = self._hash(view_passcode) if view_passcode else None
        # A changed passcode has to invalidate the tokens issued under the old
        # one, and the only way to notice it changed is to have kept its
        # fingerprint. Same reasoning as the hub's set_pin.
        if store.get("viewFingerprint") != self.view_hash:
            store.set("viewFingerprint", self.view_hash)
            store.set("viewTokens", {})

    def _hash(self, pin):
        return hashlib.sha256((self.view_salt + (pin or "")).encode()).hexdigest()

    # ---------------------------------------------------------------- write
    def push_ok(self, given):
        """Constant-time, and false for an unconfigured key rather than true.

        The hub's own rule is that an unset passcode means open, because a
        read-only picklist on a LAN should not be gated by accident. This is
        the opposite situation in every respect, so the default flips: no key,
        no writes, and the server refuses to start without one anyway.
        """
        if not self.push_key:
            return False
        return hmac.compare_digest(self.push_key, given or "")

    # ----------------------------------------------------------------- read
    def locked(self):
        return bool(self.view_hash) and not self.open_read

    def check_pin(self, pin):
        if not self.locked():
            return True
        return hmac.compare_digest(self.view_hash, self._hash(pin))

    def issue_token(self):
        tok = secrets.token_urlsafe(24)

        def keep(toks):
            toks = dict(toks or {})
            toks[tok] = time.time() + TOKEN_HOURS * 3600
            return {k: v for k, v in toks.items() if v > time.time()}, None
        self.store.mutate("viewTokens", keep)
        return tok

    def token_ok(self, tok):
        if not self.locked():
            return True
        toks = self.store.get("viewTokens") or {}
        exp = toks.get(tok or "")
        return bool(exp and exp > time.time())

    # ------------------------------------------------------------ throttle
    def note_fail(self, who):
        with self._fails_lock:
            now = time.time()
            hits = [t for t in self._fails.get(who, []) if now - t < UNLOCK_WINDOW]
            hits.append(now)
            self._fails[who] = hits
            if len(self._fails) > 4000:          # nothing here is worth unbounded memory
                self._fails = {k: v for k, v in self._fails.items()
                               if v and now - v[-1] < UNLOCK_WINDOW}
            return len(hits)

    def throttled(self, who):
        with self._fails_lock:
            now = time.time()
            hits = [t for t in self._fails.get(who, []) if now - t < UNLOCK_WINDOW]
            return len(hits) >= UNLOCK_FAILS

    def clear_fails(self, who):
        with self._fails_lock:
            self._fails.pop(who, None)


class Handler(BaseHTTPRequestHandler):
    mirror = None
    behind_proxy = False
    protocol_version = "HTTP/1.1"
    server_version = "frc-mirror/1.0"

    def log_message(self, fmt, *args):
        # A read token rides in the query string on image and download URLs,
        # because an <img> tag cannot send a header. Logging request lines
        # would write those tokens to disk on a public host.
        pass

    # ------------------------------------------------------------- plumbing
    def _send(self, body, ctype, code=200, extra=None, immutable=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        enc = None
        if len(body) > 1024 and "gzip" in (self.headers.get("Accept-Encoding") or ""):
            body, enc = gzip.compress(body, 6), "gzip"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if enc:
            self.send_header("Content-Encoding", enc)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable"
                         if immutable else "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        # No third-party anything: every byte this page loads it serves itself.
        # It is scouting data on the open internet behind one shared code, and
        # the smallest surface is the right one.
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'none'")
        if self.behind_proxy:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(json.dumps(obj, default=str), "application/json; charset=utf-8", code, extra)

    def _who(self):
        """The address to hold a failed passcode against.

        Behind a proxy every request comes from 127.0.0.1, so one wrong guess
        would throttle the whole internet. The forwarded header is only
        believed when we were told there is a proxy in front - trusting it
        unconditionally lets anybody pick their own identity by sending one.
        """
        if self.behind_proxy:
            fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if fwd:
                return fwd[:64]
        return (self.client_address or ("?",))[0]

    def _body(self):
        """The request body as an object, gunzipped if it arrived that way."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if n <= 0 or n > MAX_BODY:
            return {}
        raw = self.rfile.read(n)
        if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except Exception:
                return {}
        try:
            v = json.loads(raw.decode("utf-8"))
        except Exception:
            return {}
        return v if isinstance(v, dict) else {}

    def _token(self, q):
        return (self.headers.get("X-Mirror-Token")
                or (q.get("t") or [None])[0])

    def _read_ok(self, q):
        return Handler.mirror.token_ok(self._token(q))

    def _file(self, relpath):
        rel = posixpath.normpath("/" + relpath.lstrip("/"))
        path = os.path.normpath(os.path.join(WEB_ROOT, rel.lstrip("/")))
        if not (path.startswith(WEB_ROOT + os.sep) or path == WEB_ROOT) or not os.path.isfile(path):
            if rel.startswith(SHARED_OK):
                path = os.path.normpath(os.path.join(SHARED_ROOT, rel.lstrip("/")))
                if not path.startswith(SHARED_ROOT + os.sep) or not os.path.isfile(path):
                    return self._json({"error": "not found"}, 404)
            else:
                return self._json({"error": "not found"}, 404)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith(".js"):
            ctype = "text/javascript; charset=utf-8"
        elif path.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        with open(path, "rb") as fh:
            data = fh.read()
        self._send(data, ctype, immutable="/fonts/" in path.replace(os.sep, "/"))

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        u = urlparse(self.path)
        p = posixpath.normpath(u.path)
        q = parse_qs(u.query)
        m = Handler.mirror
        st = m.store

        if p in ("/", "/index.html"):
            return self._file("index.html")

        # Open, and deliberately thin: enough for a phone that cannot get in to
        # tell "I typed the code wrong" from "the hub stopped pushing an hour
        # ago", which are different problems with different fixes. It names no
        # event and counts no team.
        if p == "/api/status":
            last = st.last_received()
            return self._json({"ok": True, "protocol": PROTOCOL, "locked": m.locked(),
                               "events": len(st.events()),
                               "lastReceivedAt": last, "serverTime": time.time(),
                               "uptimeSec": int(time.time() - m.started_at)})

        if p.startswith("/api/"):
            if not self._read_ok(q):
                return self._json({"error": "locked"}, 401)

        if p == "/api/events":
            return self._json({"events": st.events(), "lastReceivedAt": st.last_received(),
                               "serverTime": time.time()})

        if p == "/api/pit/live":
            # What the pit scouting page polls: every robot's status, merged
            # from what the hub last pushed and what was saved here since, and
            # who has which robot open right now. Small on purpose - it is
            # fetched every fifteen seconds by every phone in the pits.
            ek = (q.get("event") or [None])[0] or _default_event(st)
            pits = _pit_records(st, ek) if ek else {}
            return self._json({
                "eventKey": ek,
                "status": {str(t): {"done": _pit_done(r), "scoutId": r.get("scoutId"),
                                    "updatedAt": r.get("updatedAt")}
                           for t, r in pits.items()},
                "claims": _fresh_claims(st.get("pitClaims:%s" % ek) or {}) if ek else {},
                "serverTime": time.time()})

        if p == "/api/pit/entry":
            # One robot's pit record, to open its form with.
            ek = (q.get("event") or [None])[0] or _default_event(st)
            try:
                team = int((q.get("team") or [""])[0])
            except ValueError:
                return self._json({"error": "which team?"}, 400)
            rec = (_pit_records(st, ek) if ek else {}).get(team)
            return self._json({"eventKey": ek, "team": team, "record": rec})

        if p == "/api/pitquestions":
            # The editor's copy. Before anything has been saved here, it starts
            # from the questions the hub last pushed, so moving the editor to
            # the mirror never begins with an empty list.
            doc = _questions_doc(st)
            return self._json({**doc, "editable": bool(pitquestions) and m.locked(),
                               "why": None if pitquestions else "this mirror cannot find "
                                      "server/pitquestions.py beside it",
                               "serverTime": time.time()})

        if p == "/api/snapshot":
            ek = (q.get("event") or [None])[0] or _default_event(st)
            b = st.revision(ek, (q.get("rev") or [None])[0]) if ek else None
            if not b:
                return self._json({"error": "nothing mirrored for that event"}, 404)
            b["mirror"] = {"receivedAt": st.last_received(), "serverTime": time.time(),
                           "photos": st.photo_count(ek)}
            return self._json(b)

        if p == "/api/history":
            ek = (q.get("event") or [None])[0] or _default_event(st)
            return self._json({"eventKey": ek, "revisions": st.history(ek) if ek else []})

        if p == "/api/export":
            ek = (q.get("event") or [None])[0] or _default_event(st)
            b = st.revision(ek, (q.get("rev") or [None])[0]) if ek else None
            if not b:
                return self._json({"error": "nothing mirrored for that event"}, 404)
            # Exactly the shape /api/import reads, so this file restores a hub.
            out = {"kind": "frc-rebuilt-scouting-export", "version": 1,
                   "eventKey": b.get("eventKey"), "exportedAt": time.time(),
                   "mirroredAt": b.get("capturedAt"),
                   "scout": b.get("scout") or [], "pit": b.get("pit") or []}
            return self._json(out, extra={
                "Content-Disposition": 'attachment; filename="%s"'
                                       % _safe_name("%s-mirror.json" % ek)})

        if p == "/api/export.csv":
            ek = (q.get("event") or [None])[0] or _default_event(st)
            table = (q.get("table") or ["teams"])[0]
            b = st.revision(ek) if ek else None
            text = ((b or {}).get("csv") or {}).get(table)
            if text is None:
                return self._json({"error": "no %s csv in the mirrored bundle" % table}, 404)
            # The BOM is what makes Excel open it as UTF-8, same as the hub's.
            return self._send("\ufeff" + text, "text/csv; charset=utf-8", extra={
                "Content-Disposition": 'attachment; filename="%s"'
                                       % _safe_name("%s-%s.csv" % (ek, table))})

        if p.startswith("/api/photo/"):
            mime, data = st.photo(p.rsplit("/", 1)[-1])
            if not data:
                return self._json({"error": "no such photo"}, 404)
            return self._send(data, _image_mime(mime))

        if p.startswith("/api/"):
            return self._json({"error": "not found"}, 404)
        return self._file(p)

    def do_HEAD(self):
        self.do_GET()

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        u = urlparse(self.path)
        p = posixpath.normpath(u.path)
        m = Handler.mirror

        if p == "/api/unlock":
            who = self._who()
            if m.throttled(who):
                return self._json({"ok": False, "error": "too many attempts - wait ten minutes"},
                                  429)
            if not m.check_pin((self._body() or {}).get("pin")):
                m.note_fail(who)
                time.sleep(0.6)
                return self._json({"ok": False}, 403)
            m.clear_fails(who)
            return self._json({"ok": True, "token": m.issue_token(),
                               "expiresIn": TOKEN_HOURS * 3600})

        if p == "/api/ping":
            # The hub's TEST KEYS button, and nothing else. A push proves the
            # key works but costs a whole event over the venue uplink, so there
            # has to be something that proves it and costs nothing - otherwise
            # the only way to find out a push key is wrong is to watch a push
            # fail, which is exactly the thing nobody is watching.
            if not m.push_ok(self.headers.get("X-Mirror-Key")):
                time.sleep(0.5)
                return self._json({"error": "bad push key"}, 401)
            return self._json({"ok": True, "protocol": PROTOCOL,
                               "events": len(m.store.events()),
                               "lastReceivedAt": m.store.last_received(),
                               "locked": m.locked()})

        if p == "/api/pitquestions":
            # Saving the team's pit questions. A team member with the view
            # passcode may do it - but only on a mirror that has one: on an
            # open mirror "a team member" is anybody on the internet.
            if not pitquestions:
                return self._json({"error": "this mirror cannot edit pit questions"}, 501)
            if not m.locked():
                return self._json({"error": "Set a view passcode on this mirror before "
                                            "questions can be edited here."}, 403)
            if not m.token_ok(self._token(parse_qs(u.query))):
                return self._json({"error": "locked"}, 401)
            body = self._body()
            sent_rev = body.get("rev")

            def save(cur):
                cur = cur or _questions_doc(m.store)
                # Two people editing at once: the second save is refused rather
                # than quietly undoing the first.
                if sent_rev != cur.get("rev"):
                    return None, ("stale", cur)
                qs, err = pitquestions.clean(body.get("questions"), cur.get("questions"))
                if err:
                    return None, ("bad", err)
                new = {"id": cur.get("id") or secrets.token_hex(8),
                       "rev": int(cur.get("rev") or 0) + 1,
                       "questions": qs, "savedAt": time.time()}
                return new, ("ok", new)
            kind, out = m.store.mutate("pitQuestions", save)
            if kind == "stale":
                return self._json({"error": "Somebody else saved the questions while you were "
                                            "editing. Reload to see theirs, then redo yours.",
                                   "stale": True, "current": out}, 409)
            if kind == "bad":
                return self._json({"error": out}, 400)
            return self._json({"ok": True, **out})

        if p in ("/api/pit/save", "/api/pitclaim"):
            # Pit scouting from a phone: the one thing besides the pit
            # questions that this read-only copy accepts. Same rule: only a
            # mirror with a passcode, only with a token from it.
            if not m.locked():
                return self._json({"error": "Set a view passcode on this mirror before "
                                            "pit scouting can be done here."}, 403)
            if not m.token_ok(self._token(parse_qs(u.query))):
                return self._json({"error": "locked"}, 401)
            body = self._body()
            ek = body.get("eventKey")
            try:
                team = int(body.get("team"))
            except (TypeError, ValueError):
                team = 0
            if not isinstance(ek, str) or ek not in {e.get("eventKey") for e in m.store.events()} \
                    or team <= 0:
                return self._json({"error": "needs an event this mirror has, and a team"}, 400)
            if p == "/api/pitclaim":
                device = str(body.get("deviceId") or "")[:64]
                if not device:
                    return self._json({"error": "needs a deviceId"}, 400)
                claims = _claim_pit(m.store, ek, team, device,
                                    str(body.get("scoutId") or "")[:16] or "pit",
                                    release=bool(body.get("release")))
                return self._json({"ok": True, "claims": claims, "serverTime": time.time()})
            rec, err = _pit_record(m.store, ek, team, body)
            if err:
                return self._json({"error": err}, 400)
            seq = m.store.put_pit(rec)
            return self._json({"ok": True, "seq": seq, "updatedAt": rec["updatedAt"]})

        if p == "/api/pit/pull":
            # The hub, collecting pit scouting done here: everything saved
            # after the last seq it took, photos inlined as data: URIs so the
            # hub files them exactly as it files one from a phone on its wifi.
            if not m.push_ok(self.headers.get("X-Mirror-Key")):
                time.sleep(0.5)
                return self._json({"error": "bad push key"}, 401)
            body = self._body()
            inbox = m.store.get("pitInboxId")
            if not inbox:
                inbox = secrets.token_hex(8)
                m.store.set("pitInboxId", inbox)
            # A hub that last read a different inbox (this database was
            # rebuilt) starts again from the beginning.
            since = body.get("since") if body.get("inboxId") == inbox else 0
            rows = m.store.pits_since(since if isinstance(since, int) else 0, limit=10)
            out = []
            for seq, rec in rows:
                rec = dict(rec)
                payload = dict(rec.get("payload") or {})
                # Only a photo taken on this site travels as bytes. Every other
                # id came from the hub, which already holds it under the same
                # content hash - so it goes back as the id, and is never dropped
                # for want of bytes this mirror has not been sent yet.
                fresh = set(rec.get("newPhotos") or [])
                photos = []
                for pid in payload.get("photos") or []:
                    mime, data = m.store.photo(pid) if pid in fresh else (None, None)
                    photos.append("data:%s;base64,%s" % (mime, base64.b64encode(data).decode())
                                  if data else pid)
                payload["photos"] = photos
                rec.pop("newPhotos", None)
                rec["payload"] = payload
                out.append(rec)
            return self._json({"inboxId": inbox, "records": out,
                               "seq": rows[-1][0] if rows else since or 0,
                               "more": len(rows) == 10})

        if p == "/api/pitquestions/pull":
            # The hub, collecting what was saved here. Same key as a push: the
            # hub is the only thing that has it.
            if not m.push_ok(self.headers.get("X-Mirror-Key")):
                time.sleep(0.5)
                return self._json({"error": "bad push key"}, 401)
            doc = m.store.get("pitQuestions") or {}
            return self._json({"id": doc.get("id"), "rev": int(doc.get("rev") or 0),
                               "questions": doc.get("questions") or []})

        if p == "/api/push":
            if not m.push_ok(self.headers.get("X-Mirror-Key")):
                time.sleep(0.5)
                return self._json({"error": "bad push key"}, 401)
            b = self._body()
            if b.get("kind") != "frc-rebuilt-scouting-mirror":
                return self._json({"error": "not a mirror bundle"}, 400)
            if not b.get("eventKey"):
                return self._json({"error": "bundle names no event"}, 400)
            rev, size = m.store.put_revision(b, keep=KEEP_REVISIONS)
            m.pushes += 1
            m.last_push_from = self._who()
            # Photos are the only part of a bundle worth not re-sending, so the
            # answer to a push is the list of ids we are missing. The hub sends
            # a few per push; over a pit-scouting morning they all arrive, and
            # nothing crosses the venue uplink twice.
            want = [pid for pid in
                    (r.get("photoId") for r in (b.get("photoIds") or [])
                     if isinstance(r, dict))
                    if isinstance(pid, str)]
            have = m.store.have_photos(want)
            missing = [pid for pid in want if pid not in have]
            return self._json({"ok": True, "revision": rev, "bytes": size,
                               "protocol": PROTOCOL,
                               "photosMissing": len(missing),
                               "wantPhotos": missing[:16]})

        if p == "/api/photos":
            if not m.push_ok(self.headers.get("X-Mirror-Key")):
                time.sleep(0.5)
                return self._json({"error": "bad push key"}, 401)
            b = self._body()
            ek = b.get("eventKey")
            stored = 0
            for rec in (b.get("photos") or []):
                if not isinstance(rec, dict) or not isinstance(rec.get("photoId"), str):
                    continue
                try:
                    raw = base64.b64decode(rec.get("data") or "", validate=True)
                except (binascii.Error, ValueError):
                    continue
                # Whatever a phone once claimed, it is served back as an image
                # type this file actually starts with, or as a download.
                if not raw or len(raw) > 24 * 1024 * 1024:
                    continue
                try:
                    m.store.put_photo(rec["photoId"], ek, rec.get("team"),
                                      _image_mime(rec.get("mime"), raw), raw)
                except (TypeError, ValueError):
                    continue          # a team that is not a number: skip the row, keep the batch
                stored += 1
            return self._json({"ok": True, "stored": stored})

        return self._json({"error": "not found"}, 404)


def _fresh_claims(claims, now=None):
    """{team: {deviceId: {scoutId, at}}} with stale holders and empty teams gone."""
    cut = (now or time.time()) - PIT_CLAIM_SECONDS
    out = {}
    for team, holders in (claims or {}).items():
        if isinstance(holders, dict):
            live = {d: c for d, c in holders.items()
                    if isinstance(c, dict) and (c.get("at") or 0) > cut}
            if live:
                out[team] = live
    return out


def _claim_pit(store, ek, team, device, scout, release=False):
    """Open, renew or let go of one robot's pit form for one phone.

    A marker, not a lock - a second scout is told who has it and may carry on -
    and each phone only ever adds or removes its own claim, the same rules as
    the hub's pit tablets.
    """
    key, now = str(team), time.time()

    def apply(cur):
        cur = _fresh_claims(cur or {}, now)
        holders = cur.setdefault(key, {})
        if release:
            holders.pop(device, None)
        else:
            holders[device] = {"scoutId": scout, "at": now}
        if not holders:
            del cur[key]
        return cur, cur
    return store.mutate("pitClaims:%s" % ek, apply)


def _pit_records(store, ek):
    """{team: record} - what the hub last pushed, overlaid by saves made here.

    Whichever is newer wins, so a robot scouted here shows straight away, and
    once the hub has collected it and pushed it back the hub's copy (with its
    photos filed) takes over.
    """
    out = {}
    pushed = store.latest_part(ek, "pit", [])
    for r in pushed:
        if isinstance(r, dict) and r.get("team") is not None:
            try:
                out[int(r["team"])] = r
            except (TypeError, ValueError):
                continue
    for team, r in store.pits_for(ek).items():
        have = out.get(team)
        if not have or float(r.get("updatedAt") or 0) >= float(have.get("updatedAt") or 0):
            out[team] = r
    return out


def _pit_done(rec):
    p = (rec or {}).get("payload") or {}
    n = sum(1 for f in PIT_FIELDS if p.get(f))
    return "done" if n >= 4 else "partial" if n else ""


def _pit_record(store, ek, team, body):
    """A pit save from a phone, made safe to keep. Returns (record, error).

    Photos arrive as data: URIs and are filed in the photo table here, under
    the same content-hash id the hub would give them, and the record keeps
    only the ids. A photo id already on the record (one the hub pushed) is
    kept as it is. Everything else in the payload is bounded in size, and
    the hub cleans every field again when it collects the record.
    """
    payload = body.get("payload")
    if not isinstance(payload, dict):
        return None, "no answers in that save"
    payload = dict(payload)
    # Size is checked before any photo is filed, so a refused save leaves
    # nothing behind in the photo table.
    if len(json.dumps({k: v for k, v in payload.items() if k != "photos"})) > PIT_PAYLOAD_BYTES:
        return None, "that save is too large"
    # Photos taken on this site and not yet collected by the hub, carried from
    # the last save of this robot so an edit that keeps them keeps them owed.
    prev = (store.pits_for(ek).get(team) or {})
    owed = set(prev.get("newPhotos") or [])
    fresh = []
    photos = []
    for src in (payload.get("photos") if isinstance(payload.get("photos"), list) else [])[:PIT_PHOTOS_MAX]:
        if isinstance(src, str) and src.startswith("data:"):
            try:
                raw = base64.b64decode(src.split(",", 1)[1], validate=True)
            except (binascii.Error, ValueError, IndexError):
                continue
            if not raw or len(raw) > PIT_PHOTO_BYTES:
                continue
            mime = _image_mime(None, raw)
            if not mime.startswith("image/"):
                continue
            pid = hashlib.sha1(raw).hexdigest()[:16]
            store.put_photo(pid, ek, team, mime, raw)
            photos.append(pid)
            fresh.append(pid)
        elif isinstance(src, str) and PHOTO_ID.fullmatch(src):
            photos.append(src)
            if src in owed:
                fresh.append(src)
    payload["photos"] = photos
    return {"eventKey": ek, "team": team, "newPhotos": fresh,
            "scoutId": str(body.get("scoutId") or "")[:16] or "mirror",
            "deviceId": "mirror:" + str(body.get("deviceId") or "")[:48],
            "updatedAt": time.time(), "payload": payload}, None


def _questions_doc(store):
    """The pit questions as saved here, or - before any save - as last pushed.

    rev 0 means nothing has been saved on the mirror yet; the hub ignores a
    document at rev 0, so its own copy stands until somebody saves here.
    """
    doc = store.get("pitQuestions")
    if isinstance(doc, dict) and doc.get("rev"):
        return {"id": doc.get("id"), "rev": int(doc["rev"]),
                "questions": doc.get("questions") or [], "savedAt": doc.get("savedAt")}
    ek = _default_event(store)
    pushed = store.latest_part(ek, "pitQuestions", []) if ek else []
    return {"id": None, "rev": 0, "questions": pushed or [], "savedAt": None}


def _default_event(store):
    """The event a bare URL means: the one pushed most recently."""
    evs = store.events()
    return evs[0]["eventKey"] if evs else None


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(name))[:120] or "export"


#: The only content types a mirrored photo will ever be served as. It arrived
#: from a phone, through a hub, through the internet, and a stored label is not
#: evidence of anything - so the bytes are re-checked and anything unrecognised
#: goes out as a download rather than as something a browser will run.
_MAGIC = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"),
          (b"RIFF", "image/webp"), (b"GIF8", "image/gif"))


def _image_mime(claimed, raw=None):
    if raw:
        for magic, mime in _MAGIC:
            if raw.startswith(magic):
                return mime
        return "application/octet-stream"
    return claimed if claimed in (m for _, m in _MAGIC) else "application/octet-stream"


class Server(socketserver.ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _secret(name):
    """`NAME`, or `NAME_B64` decoded.  Either is a real way to set one.

    A host sets these plainly through systemd; the hub's admin panel writes the
    encoded form, and the two sides of a push read the same variable name on
    purpose - so a mirror being tried out on the same laptop as the hub finds the
    key the panel already wrote. Neither form is more secret than the other:
    base64 is not encryption, and the reason it is here is legibility over a
    shoulder, not secrecy.
    """
    # Through the hub's own reader where it is importable, so the two sides of
    # a push cannot disagree about which line wins. They did: this read the
    # plain name first and the hub read the encoded one first, so a host with
    # both lines set would have the mirror checking a different string from the
    # one the hub sends - reported as "bad push key", from two halves each
    # certain they were right.
    if envfile:
        return envfile.read(name) or None
    return (os.environ.get(name) or "").strip() or None


def main():
    # First, so every default below sees it. A real environment variable still
    # wins over the file - see server/envfile.py.
    if envfile:
        envfile.load()
    ap = argparse.ArgumentParser(
        description="Off-site mirror for the REBUILT scouting hub")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MIRROR_PORT") or PORT))
    ap.add_argument("--bind", default=os.environ.get("MIRROR_BIND") or "127.0.0.1",
                    help="default 127.0.0.1: this is meant to sit behind a TLS proxy. "
                         "Use 0.0.0.0 only if something else is terminating TLS.")
    ap.add_argument("--db", default=os.environ.get("MIRROR_DB") or None)
    ap.add_argument("--push-key", default=_secret("MIRROR_PUSH_KEY"),
                    help="what the hub must send. Prefer the env var - an argument "
                         "is visible in `ps` to everyone on the host.")
    ap.add_argument("--view-passcode", default=_secret("MIRROR_VIEW_PASSCODE"),
                    help="what a person types to read the site")
    ap.add_argument("--open", action="store_true",
                    help="serve the data to anyone who finds the address, with no passcode")
    ap.add_argument("--behind-proxy", action="store_true",
                    default=bool(os.environ.get("MIRROR_BEHIND_PROXY")),
                    help="trust X-Forwarded-For, and send HSTS")
    args = ap.parse_args()

    if not args.push_key:
        sys.exit("MIRROR_PUSH_KEY is not set. Nothing may write to a mirror without one.\n"
                 "  Make one:  python3 -c \"import secrets; print(secrets.token_urlsafe(32))\"")
    if not args.view_passcode and not args.open:
        sys.exit("MIRROR_VIEW_PASSCODE is not set, and --open was not given.\n"
                 "This site is on the public internet. Set a passcode, or say --open and mean it.")

    store = Store(args.db) if args.db else Store()
    Handler.mirror = Mirror(store, args.push_key, args.view_passcode, open_read=args.open)
    Handler.behind_proxy = args.behind_proxy

    srv = Server((args.bind, args.port), Handler)
    evs = store.events()
    print("  REBUILT scouting mirror")
    print("  ----------------------------------------------------")
    print("    listening on   http://%s:%d" % (args.bind, args.port))
    print("    database       %s" % store.path)
    print("    reading        %s" % ("PASSCODE REQUIRED" if Handler.mirror.locked()
                                     else "OPEN - anyone with the address"))
    print("    events held    %s" % (", ".join(e["eventKey"] for e in evs) or "none yet"))
    print()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  shutting down")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    main()
