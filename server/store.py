"""SQLite store.  WAL mode so ten scouts POSTing at the buzzer don't lock each other out."""
import json
import os
import sqlite3
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(_HERE, "..", "data", "scouting.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS events (
  event_key TEXT PRIMARY KEY, name TEXT, level TEXT DEFAULT 'regional',
  data TEXT, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS teams (
  event_key TEXT NOT NULL, team INTEGER NOT NULL, name TEXT,
  data TEXT, updated_at REAL NOT NULL, PRIMARY KEY (event_key, team));

CREATE TABLE IF NOT EXISTS matches (
  event_key TEXT NOT NULL, match_key TEXT NOT NULL, label TEXT,
  comp_level TEXT, match_number INTEGER, play_order INTEGER,
  red TEXT, blue TEXT, status TEXT, times TEXT, breakdown TEXT,
  updated_at REAL NOT NULL, PRIMARY KEY (event_key, match_key));

-- One row per (match, team, scout).  Last-write-wins on updated_at.
CREATE TABLE IF NOT EXISTS scout_entries (
  event_key TEXT NOT NULL, match_key TEXT NOT NULL, team INTEGER NOT NULL,
  scout_id TEXT NOT NULL, device_id TEXT, alliance TEXT, station INTEGER,
  payload TEXT NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY (match_key, team, scout_id));
CREATE INDEX IF NOT EXISTS idx_scout_event ON scout_entries(event_key, team);

CREATE TABLE IF NOT EXISTS pit_entries (
  event_key TEXT NOT NULL, team INTEGER NOT NULL, scout_id TEXT,
  device_id TEXT, payload TEXT NOT NULL, updated_at REAL NOT NULL,
  PRIMARY KEY (event_key, team));

CREATE TABLE IF NOT EXISTS photos (
  photo_id TEXT PRIMARY KEY, event_key TEXT, team INTEGER,
  mime TEXT, data BLOB, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS solved (
  event_key TEXT NOT NULL, match_key TEXT NOT NULL, team INTEGER NOT NULL,
  fuel INTEGER, band INTEGER, by_phase TEXT, provisional INTEGER DEFAULT 0,
  updated_at REAL NOT NULL, PRIMARY KEY (match_key, team));
CREATE INDEX IF NOT EXISTS idx_solved_team ON solved(event_key, team);

CREATE TABLE IF NOT EXISTS flags (
  event_key TEXT NOT NULL, match_key TEXT NOT NULL, kind TEXT NOT NULL,
  detail TEXT, created_at REAL NOT NULL,
  PRIMARY KEY (match_key, kind));
"""


class Store:
    def __init__(self, path=DEFAULT_DB):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._local = threading.local()
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
            "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, json.dumps(value), time.time()))

    # -------------------------------------------------------------- event
    def put_event(self, key, name=None, level=None, data=None):
        self.conn().execute(
            "INSERT INTO events(event_key,name,level,data,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(event_key) DO UPDATE SET "
            "  name=COALESCE(excluded.name,events.name),"
            "  level=COALESCE(excluded.level,events.level),"
            "  data=COALESCE(excluded.data,events.data), updated_at=excluded.updated_at",
            (key, name, level, json.dumps(data) if data is not None else None, time.time()))

    def event(self, key):
        r = self.conn().execute("SELECT * FROM events WHERE event_key=?", (key,)).fetchone()
        return dict(r) if r else None

    def put_teams(self, event_key, teams):
        now = time.time()
        self.conn().executemany(
            "INSERT INTO teams(event_key,team,name,data,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(event_key,team) DO UPDATE SET name=excluded.name,"
            "  data=excluded.data, updated_at=excluded.updated_at",
            [(event_key, int(t["team"]), t.get("name"), json.dumps(t), now) for t in teams])

    def teams(self, event_key):
        rows = self.conn().execute(
            "SELECT team,name,data FROM teams WHERE event_key=? ORDER BY team", (event_key,)).fetchall()
        return [{"team": r["team"], "name": r["name"], **(json.loads(r["data"]) if r["data"] else {})} for r in rows]

    # ------------------------------------------------------------ matches
    def put_match(self, event_key, match_key, **f):
        now = time.time()
        cols = dict(label=f.get("label"), comp_level=f.get("comp_level"),
                    match_number=f.get("match_number"), play_order=f.get("play_order"),
                    red=_j(f.get("red")), blue=_j(f.get("blue")), status=f.get("status"),
                    times=_j(f.get("times")), breakdown=_j(f.get("breakdown")))
        sets = ",".join(f"{k}=COALESCE(excluded.{k},matches.{k})" for k in cols)
        self.conn().execute(
            f"INSERT INTO matches(event_key,match_key,{','.join(cols)},updated_at) "
            f"VALUES(?,?,{','.join('?' for _ in cols)},?) "
            f"ON CONFLICT(event_key,match_key) DO UPDATE SET {sets}, updated_at=excluded.updated_at",
            (event_key, match_key, *cols.values(), now))

    def matches(self, event_key):
        rows = self.conn().execute(
            "SELECT * FROM matches WHERE event_key=? ORDER BY COALESCE(play_order, match_number)",
            (event_key,)).fetchall()
        return [_match_row(r) for r in rows]

    def match(self, event_key, match_key):
        r = self.conn().execute("SELECT * FROM matches WHERE event_key=? AND match_key=?",
                                (event_key, match_key)).fetchone()
        return _match_row(r) if r else None

    # ------------------------------------------------------- scout entries
    def upsert_scout(self, rec):
        """Last-write-wins on updated_at.  Returns True when the row was applied."""
        now = float(rec.get("updatedAt") or time.time())
        key = (rec["matchKey"], int(rec["team"]), rec["scoutId"])
        cur = self.conn().execute(
            "SELECT updated_at FROM scout_entries WHERE match_key=? AND team=? AND scout_id=?", key).fetchone()
        if cur and float(cur["updated_at"]) >= now:
            return False
        self.conn().execute(
            "INSERT INTO scout_entries(event_key,match_key,team,scout_id,device_id,alliance,station,payload,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(match_key,team,scout_id) DO UPDATE SET"
            "  event_key=excluded.event_key, device_id=excluded.device_id, alliance=excluded.alliance,"
            "  station=excluded.station, payload=excluded.payload, updated_at=excluded.updated_at",
            (rec["eventKey"], rec["matchKey"], int(rec["team"]), rec["scoutId"], rec.get("deviceId"),
             rec.get("alliance"), rec.get("station"), json.dumps(rec.get("payload") or {}), now))
        return True

    def scout_entries(self, event_key, match_key=None, team=None):
        q = "SELECT * FROM scout_entries WHERE event_key=?"
        a = [event_key]
        if match_key:
            q += " AND match_key=?"; a.append(match_key)
        if team is not None:
            q += " AND team=?"; a.append(int(team))
        return [_scout_row(r) for r in self.conn().execute(q, a).fetchall()]

    def upsert_pit(self, rec):
        now = float(rec.get("updatedAt") or time.time())
        cur = self.conn().execute("SELECT updated_at FROM pit_entries WHERE event_key=? AND team=?",
                                  (rec["eventKey"], int(rec["team"]))).fetchone()
        if cur and float(cur["updated_at"]) >= now:
            return False
        self.conn().execute(
            "INSERT INTO pit_entries(event_key,team,scout_id,device_id,payload,updated_at) VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(event_key,team) DO UPDATE SET scout_id=excluded.scout_id,"
            "  device_id=excluded.device_id, payload=excluded.payload, updated_at=excluded.updated_at",
            (rec["eventKey"], int(rec["team"]), rec.get("scoutId"), rec.get("deviceId"),
             json.dumps(rec.get("payload") or {}), now))
        return True

    def pit_entries(self, event_key):
        rows = self.conn().execute("SELECT * FROM pit_entries WHERE event_key=?", (event_key,)).fetchall()
        return [{"team": r["team"], "scoutId": r["scout_id"], "updatedAt": r["updated_at"],
                 "payload": json.loads(r["payload"])} for r in rows]

    # ------------------------------------------------------------- solved
    def put_solved(self, event_key, match_key, rows):
        now = time.time()
        self.conn().executemany(
            "INSERT INTO solved(event_key,match_key,team,fuel,band,by_phase,provisional,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(match_key,team) DO UPDATE SET"
            "  fuel=excluded.fuel, band=excluded.band, by_phase=excluded.by_phase,"
            "  provisional=excluded.provisional, updated_at=excluded.updated_at",
            [(event_key, match_key, int(r["team"]), r["fuel"], r["band"],
              json.dumps(r.get("byPhase") or {}), 1 if r.get("provisional") else 0, now) for r in rows])

    def solved(self, event_key, team=None):
        q = "SELECT * FROM solved WHERE event_key=?"
        a = [event_key]
        if team is not None:
            q += " AND team=?"; a.append(int(team))
        return [{"matchKey": r["match_key"], "team": r["team"], "fuel": r["fuel"], "band": r["band"],
                 "byPhase": json.loads(r["by_phase"] or "{}"), "provisional": bool(r["provisional"])}
                for r in self.conn().execute(q, a).fetchall()]

    def put_photo(self, photo_id, event_key, team, mime, data):
        self.conn().execute(
            "INSERT INTO photos(photo_id,event_key,team,mime,data,updated_at) VALUES(?,?,?,?,?,?)"
            " ON CONFLICT(photo_id) DO UPDATE SET data=excluded.data, mime=excluded.mime,"
            "  updated_at=excluded.updated_at",
            (photo_id, event_key, int(team), mime, sqlite3.Binary(data), time.time()))

    def photo(self, photo_id):
        r = self.conn().execute("SELECT mime,data FROM photos WHERE photo_id=?", (photo_id,)).fetchone()
        return (r["mime"], bytes(r["data"])) if r else (None, None)

    def photo_ids(self, event_key, team=None):
        q = "SELECT photo_id,team FROM photos WHERE event_key=?"
        a = [event_key]
        if team is not None:
            q += " AND team=?"; a.append(int(team))
        return [{"photoId": r["photo_id"], "team": r["team"]}
                for r in self.conn().execute(q + " ORDER BY updated_at DESC", a).fetchall()]

    # ------------------------------------------------------------ backups
    def snapshot(self, keep=12):
        """Copy the whole database to data/snapshots/ and prune old ones.

        sqlite's own backup API, not a file copy: WAL means the .db file on disk
        is not a complete database on its own, and a half-copied event is worse
        than no copy at all. Safe to call while scouts are syncing.
        """
        base = os.path.dirname(self.path)
        stem = os.path.splitext(os.path.basename(self.path))[0]
        out_dir = os.path.join(base, "snapshots")
        os.makedirs(out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(out_dir, f"{stem}-{stamp}.db")
        target = sqlite3.connect(dest)
        try:
            self.conn().backup(target)
        finally:
            target.close()

        existing = sorted(f for f in os.listdir(out_dir)
                          if f.startswith(stem + "-") and f.endswith(".db"))
        for old in existing[:-keep] if keep > 0 else []:
            try:
                os.remove(os.path.join(out_dir, old))
            except OSError:
                pass
        return dest

    def flag(self, event_key, match_key, kind, detail=""):
        self.conn().execute(
            "INSERT INTO flags(event_key,match_key,kind,detail,created_at) VALUES(?,?,?,?,?)"
            " ON CONFLICT(match_key,kind) DO UPDATE SET detail=excluded.detail",
            (event_key, match_key, kind, detail, time.time()))

    def flags(self, event_key):
        return [dict(r) for r in self.conn().execute(
            "SELECT * FROM flags WHERE event_key=? ORDER BY created_at DESC", (event_key,)).fetchall()]


def _j(v):
    return json.dumps(v) if v is not None else None


def _match_row(r):
    return {"matchKey": r["match_key"], "label": r["label"], "compLevel": r["comp_level"],
            "matchNumber": r["match_number"], "playOrder": r["play_order"],
            "red": json.loads(r["red"] or "null"), "blue": json.loads(r["blue"] or "null"),
            "status": r["status"], "times": json.loads(r["times"] or "null"),
            "breakdown": json.loads(r["breakdown"] or "null"), "updatedAt": r["updated_at"]}


def _scout_row(r):
    return {"eventKey": r["event_key"], "matchKey": r["match_key"], "team": r["team"],
            "scoutId": r["scout_id"], "deviceId": r["device_id"], "alliance": r["alliance"],
            "station": r["station"], "payload": json.loads(r["payload"]), "updatedAt": r["updated_at"]}
