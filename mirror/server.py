"""The off-site mirror.  Run this on a real host; the hub pushes to it.

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
that for systemoverload.org.
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

WEB_ROOT = os.path.join(_HERE, "web")
#: The fonts and the font declarations are the hub's, byte for byte, so the
#: mirror reads as the same application rather than a lookalike. Nothing else
#: is served from there - the dashboard's CSS assumes a 1240px laptop, and this
#: page has to work on a phone in a stands.
SHARED_ROOT = os.path.abspath(os.path.join(_HERE, "..", "web"))
SHARED_OK = ("/fonts/", "/css/fonts.css")

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
                m.store.put_photo(rec["photoId"], ek, rec.get("team"),
                                  _image_mime(rec.get("mime"), raw), raw)
                stored += 1
            return self._json({"ok": True, "stored": stored})

        return self._json({"error": "not found"}, 404)


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


def main():
    ap = argparse.ArgumentParser(
        description="Off-site mirror for the REBUILT scouting hub")
    ap.add_argument("--port", type=int, default=int(os.environ.get("MIRROR_PORT") or PORT))
    ap.add_argument("--bind", default=os.environ.get("MIRROR_BIND") or "127.0.0.1",
                    help="default 127.0.0.1: this is meant to sit behind a TLS proxy. "
                         "Use 0.0.0.0 only if something else is terminating TLS.")
    ap.add_argument("--db", default=os.environ.get("MIRROR_DB") or None)
    ap.add_argument("--push-key", default=os.environ.get("MIRROR_PUSH_KEY"),
                    help="what the hub must send. Prefer the env var - an argument "
                         "is visible in `ps` to everyone on the host.")
    ap.add_argument("--view-passcode", default=os.environ.get("MIRROR_VIEW_PASSCODE"),
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
