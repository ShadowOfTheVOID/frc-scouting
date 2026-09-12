"""The mirror's own database.  Nothing here knows anything about FRC.

A hub pushes a bundle; this keeps it, gzipped, alongside the ones before it,
and hands back the newest on request. That is the whole model: the mirror is
not a second hub and never computes anything - it cannot solve a match, rank a
team or talk to The Blue Alliance. It stores what it was told and serves it
back, which is exactly what makes it useful when the hub is gone.

Revisions rather than one current row, because "the database looks wrong" is
one of the failures this exists for: being able to hand back the copy from
forty minutes ago is worth far more than the few megabytes it costs.
"""
import gzip
import json
import os
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_HERE, "data", "mirror.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);

-- One row per push that changed something.  `body` is the gzipped bundle
-- exactly as it arrived; nothing is unpacked into columns, so a hub that
-- starts sending a new field needs no migration here.
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_key TEXT NOT NULL, hub_id TEXT, digest TEXT,
  received_at REAL NOT NULL, bytes INTEGER NOT NULL,
  summary TEXT, body BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS idx_rev_event ON revisions(event_key, id DESC);

CREATE TABLE IF NOT EXISTS photos (
  photo_id TEXT PRIMARY KEY, event_key TEXT, team INTEGER,
  mime TEXT, data BLOB, updated_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_photo_event ON photos(event_key);
"""


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._local = threading.local()
        self._write = threading.Lock()
        with self.conn() as c:
            c.executescript(SCHEMA)

    def conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=8000")
            self._local.c = c
        return c

    # ------------------------------------------------------------ settings
    def get(self, key, default=None):
        r = self.conn().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set(self, key, value):
        self.conn().execute(
            "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE "
            "SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), time.time()))

    def mutate(self, key, fn):
        """Read-modify-write one kv row under the write lock.

        Two phones unlocking in the same second is not hypothetical at a
        competition, and a plain get-then-set loses one of the two tokens.
        `fn` takes the current value and returns `(new_value, result)`;
        returning None for the new value skips the write.
        """
        with self._write:
            new, result = fn(self.get(key))
            if new is not None:
                self.set(key, new)
            return result

    # ----------------------------------------------------------- revisions
    def put_revision(self, bundle, keep=60):
        """Store one bundle. Returns (revision id, stored bytes).

        The summary is the handful of counts the events list and the history
        table show. Computed once, on the way in, so listing forty revisions
        does not mean decompressing forty bundles.
        """
        ek = bundle.get("eventKey") or "?"
        raw = gzip.compress(json.dumps(bundle, separators=(",", ":"),
                                       default=str).encode(), 6)
        summary = json.dumps(summarize(bundle))
        with self._write:
            c = self.conn()
            cur = c.execute(
                "INSERT INTO revisions(event_key,hub_id,digest,received_at,bytes,summary,body)"
                " VALUES(?,?,?,?,?,?,?)",
                (ek, bundle.get("hubId"), bundle.get("digest"), time.time(),
                 len(raw), summary, sqlite3.Binary(raw)))
            rev = cur.lastrowid
            if keep > 0:
                c.execute(
                    "DELETE FROM revisions WHERE event_key=? AND id NOT IN "
                    "(SELECT id FROM revisions WHERE event_key=? ORDER BY id DESC LIMIT ?)",
                    (ek, ek, keep))
        return rev, len(raw)

    def revision(self, event_key, rev=None):
        """One stored bundle, newest by default. None when there is none.

        `rev` arrives off a query string, so it is whatever somebody typed.
        `int()` on that used to raise straight out of the request handler -
        no response at all, just a dropped connection - and on an --open
        mirror that is anybody who can reach the address. A revision that is
        not a number is a revision this mirror does not have.
        """
        if rev is not None and str(rev).strip() != "":
            try:
                rev = int(str(rev).strip())
            except (TypeError, ValueError):
                return None
            r = self.conn().execute(
                "SELECT body FROM revisions WHERE event_key=? AND id=?",
                (event_key, rev)).fetchone()
        else:
            r = self.conn().execute(
                "SELECT body FROM revisions WHERE event_key=? ORDER BY id DESC LIMIT 1",
                (event_key,)).fetchone()
        if not r:
            return None
        return json.loads(gzip.decompress(bytes(r["body"])).decode("utf-8"))

    def history(self, event_key, limit=60):
        rows = self.conn().execute(
            "SELECT id,received_at,bytes,digest,hub_id,summary FROM revisions "
            "WHERE event_key=? ORDER BY id DESC LIMIT ?", (event_key, int(limit))).fetchall()
        return [{"revision": r["id"], "receivedAt": r["received_at"], "bytes": r["bytes"],
                 "digest": r["digest"], "hubId": r["hub_id"],
                 **json.loads(r["summary"] or "{}")} for r in rows]

    def events(self):
        """One row per mirrored event, newest push first."""
        rows = self.conn().execute(
            "SELECT event_key, COUNT(*) AS revisions, MAX(received_at) AS received_at,"
            "       SUM(bytes) AS bytes FROM revisions GROUP BY event_key "
            "ORDER BY received_at DESC").fetchall()
        out = []
        for r in rows:
            last = self.conn().execute(
                "SELECT summary,hub_id FROM revisions WHERE event_key=? ORDER BY id DESC LIMIT 1",
                (r["event_key"],)).fetchone()
            out.append({"eventKey": r["event_key"], "revisions": r["revisions"],
                        "receivedAt": r["received_at"], "bytes": r["bytes"],
                        "hubId": last["hub_id"] if last else None,
                        **json.loads((last["summary"] if last else None) or "{}")})
        return out

    def last_received(self):
        r = self.conn().execute("SELECT MAX(received_at) AS t FROM revisions").fetchone()
        return r["t"] if r and r["t"] else None

    # -------------------------------------------------------------- photos
    def put_photo(self, photo_id, event_key, team, mime, data):
        with self._write:
            self.conn().execute(
                "INSERT INTO photos(photo_id,event_key,team,mime,data,updated_at)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(photo_id) DO UPDATE SET"
                "  data=excluded.data, mime=excluded.mime, updated_at=excluded.updated_at",
                (photo_id, event_key, int(team) if team is not None else None,
                 mime, sqlite3.Binary(data), time.time()))

    def photo(self, photo_id):
        r = self.conn().execute("SELECT mime,data FROM photos WHERE photo_id=?",
                                (photo_id,)).fetchone()
        return (r["mime"], bytes(r["data"])) if r else (None, None)

    def have_photos(self, ids):
        """Which of these the mirror already holds. Chunked - sqlite caps
        variables per statement, and a pit-scouted event carries hundreds."""
        ids = [i for i in ids if isinstance(i, str)]
        have = set()
        for i in range(0, len(ids), 400):
            chunk = ids[i:i + 400]
            q = "SELECT photo_id FROM photos WHERE photo_id IN (%s)" % ",".join("?" * len(chunk))
            have.update(r["photo_id"] for r in self.conn().execute(q, chunk).fetchall())
        return have

    def photo_count(self, event_key):
        r = self.conn().execute("SELECT COUNT(*) AS n FROM photos WHERE event_key=?",
                                (event_key,)).fetchone()
        return r["n"] if r else 0


def summarize(bundle):
    """The counts that make a revision list readable without opening anything."""
    matches = bundle.get("matches") or []
    played = [m for m in matches if isinstance(m, dict) and m.get("breakdown")]
    cov = ((bundle.get("analytics") or {}).get("coverage") or {})
    return {
        "eventName": ((bundle.get("event") or {}) or {}).get("name"),
        "teams": len(bundle.get("teams") or []),
        "matches": len(matches),
        "matchesPlayed": len(played),
        "scoutEntries": len(bundle.get("scout") or []),
        "pitEntries": len(bundle.get("pit") or []),
        "photos": len(bundle.get("photoIds") or []),
        "coveragePct": cov.get("pct"),
        "capturedAt": bundle.get("capturedAt"),
        "protocol": bundle.get("protocol"),
    }
