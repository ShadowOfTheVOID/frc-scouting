"""Regression gate for the HTTP surface.  Run: python3 server/tests_api.py

tests_solver.py covers the maths.  This covers everything the maths sits on: the
last-write-wins rule that lets six phones flush at the buzzer in any order, the
passcode that gates the picklist, the export/import round trip the docs promise
is a no-op, and the rule that a missing API key reads as unknown rather than as
zero.  All stdlib, no fixtures on disk beyond a temp database.
"""
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import analytics  # noqa: E402
import envfile  # noqa: E402
import hub  # noqa: E402
import offsite  # noqa: E402
from store import Store  # noqa: E402

EK = "2026test"


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    return ok


# ------------------------------------------------------------------ harness

class Live:
    """A real server on a real socket, against a throwaway database."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="frc-api-test-")
        self.store = Store(os.path.join(self.dir, "test.db"))
        self.hub = hub.Hub(self.store)
        hub.Handler.hub = self.hub
        hub.Handler.allow_remote_config = True   # the test client is not localhost-ish enough
        self.srv = hub.Server(("127.0.0.1", 0), hub.Handler)
        self.port = self.srv.server_address[1]
        self.hub.port = self.port
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def req(self, path, body=None, method=None, headers=None, raw=False, body_bytes=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        # body_bytes goes on the wire verbatim, so a test can send something
        # that is not an object - or not JSON at all.
        data = body_bytes if body_bytes is not None else (
            json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=10) as res:
                payload = res.read()
                return res.status, (payload.decode("utf-8-sig") if raw
                                    else json.loads(payload or b"null"))
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload or b"null")
            except ValueError:
                return e.code, payload.decode("utf-8", "replace")

    def cond(self, path, etag=None):
        """A conditional GET. Returns (status, etag, body-bytes).

        Separate from req() because the whole point here is the headers and the
        empty body, and req() throws both away.
        """
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        if etag:
            r.add_header("If-None-Match", etag)
        try:
            with urllib.request.urlopen(r, timeout=10) as res:
                return res.status, res.headers.get("ETag"), res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("ETag"), e.read()


def entry(match, team, scout, updated_at, note="", intervals=None):
    return {
        "eventKey": EK, "matchKey": match, "team": team, "scoutId": scout,
        "deviceId": "test", "alliance": "red", "station": 1, "updatedAt": updated_at,
        "payload": {"intervals": intervals if intervals is not None
                    else [{"start": 35.0, "end": 45.0, "phase": "shift1", "intensity": "steady"}],
                    "feedIntervals": [], "defenseIntervals": [], "preload": 3,
                    "autoTower": "None", "endgameTower": "Level2",
                    "driverRating": 4, "defenseRating": 1,
                    "died": False, "tipped": False, "noShow": False, "note": note},
    }


def seed_event(L):
    L.store.set("eventKey", EK)
    L.store.put_event(EK, name="Test Event", level="regional")
    L.store.put_teams(EK, [{"team": t, "name": f"Team {t}"} for t in (101, 102, 103)])
    L.store.put_match(EK, f"{EK}_qm1", label="Qualification 1", comp_level="qm", match_number=1,
                      red=[101, 102, 103], blue=[201, 202, 203],
                      breakdown={
                          "red": {"windows": {"auto": 10, "shift1": 60},
                                  "autoTower": [None, None, None],
                                  "endgameTower": ["Level2", "None", "Level1"],
                                  "totalPoints": 120, "rp": 3},
                          "blue": {"windows": {"auto": 4, "shift2": 40},
                                   "autoTower": [None, None, None],
                                   "endgameTower": ["None", "None", "None"],
                                   "totalPoints": 80, "rp": 0},
                          "autoWinner": "red",
                      })


# -------------------------------------------------------------------- tests

def test_sync_and_last_write_wins(L):
    ok = True
    now = time.time()
    code, r = L.req("/api/sync", {"scout": [entry(f"{EK}_qm1", 101, "AK", now, note="first")]})
    ok &= check("sync accepts a fresh row", code == 200 and r["applied"] == 1, f"({r})")

    # An older write must lose, however it arrives - phones flush out of order.
    code, r = L.req("/api/sync", {"scout": [entry(f"{EK}_qm1", 101, "AK", now - 60, note="stale")]})
    ok &= check("an older write is rejected, not applied",
                code == 200 and r["applied"] == 0 and r["rejected"] == 1, f"({r})")
    stored = L.store.scout_entries(EK, match_key=f"{EK}_qm1")[0]
    ok &= check("the newer payload survived the stale write",
                stored["payload"]["note"] == "first")

    # A newer one wins, so the phone can correct a match after the buzzer.
    code, r = L.req("/api/sync", {"scout": [entry(f"{EK}_qm1", 101, "AK", now + 60, note="fixed")]})
    stored = L.store.scout_entries(EK, match_key=f"{EK}_qm1")[0]
    ok &= check("a newer write replaces the old one",
                r["applied"] == 1 and stored["payload"]["note"] == "fixed")

    # Same match, same team, different scout is a different row on purpose.
    L.req("/api/sync", {"scout": [entry(f"{EK}_qm1", 101, "BR", now)]})
    ok &= check("two scouts on one robot are two rows",
                len(L.store.scout_entries(EK, match_key=f"{EK}_qm1")) == 2)
    return ok


def test_solving_ran(L):
    ok = True
    solved = {s["team"]: s for s in L.store.solved(EK)}
    ok &= check("syncing a match solved it", bool(solved), f"({len(solved)} rows)")
    red = [solved[t]["fuel"] for t in (101, 102, 103) if t in solved]
    ok &= check("solved fuel sums to the official red total",
                sum(red) == 70, f"(got {sum(red)} of 70)")
    ok &= check("every solved row carries a band",
                all("band" in s for s in solved.values()))
    return ok


def test_analytics_null_safe(L):
    ok = True
    code, a = L.req("/api/analytics")
    t = a["teams"].get("101") or a["teams"].get(101)
    ok &= check("analytics answers with no api keys configured", code == 200 and t is not None)
    ok &= check("epa is unknown, not zero, with no statbotics data",
                t["epa"]["epa"] is None, f"({t['epa']})")
    ok &= check("rank is unknown, not zero, with no rankings polled",
                t["exact"]["rank"] is None and t["exact"]["opr"] is None)
    ok &= check("W-L-T is derived from the matches we scouted",
                (t["exact"]["record"] or {}).get("wins") == 1
                and t["exact"]["record"]["official"] is False, f"({t['exact']['record']})")
    ok &= check("the scout note reached the summary",
                any(n["note"] == "fixed" for n in t["notes"]), f"({t['notes']})")
    ok &= check("estimated carries both a mean error and a single-match spread",
                t["estimated"]["band"] is not None and t["estimated"]["matchBand"] is not None
                and t["estimated"]["matchBand"] >= t["estimated"]["band"],
                f"(band {t['estimated']['band']}, matchBand {t['estimated']['matchBand']})")

    # With rankings present the official record wins over ours.
    L.store.set(f"rankings:{EK}", {"101": {"rank": 4, "wins": 9, "losses": 2, "ties": 0,
                                           "rankingPoints": 2.7, "opr": 31.5}})
    L.store.set(f"epa:{EK}", {"101": {"epa": 42.0, "auto": 8.0, "teleop": 25.0,
                                      "endgame": 9.0, "rank": 120}})
    _, a = L.req("/api/analytics")
    t = a["teams"].get("101") or a["teams"].get(101)
    ok &= check("official rankings override the derived record",
                t["exact"]["rank"] == 4 and t["exact"]["record"]["wins"] == 9
                and t["exact"]["record"]["official"] is True)
    ok &= check("epa appears once statbotics has answered", t["epa"]["epa"] == 42.0)
    return ok


def test_trend_series(L):
    """The per-match series the charts draw, and the gaps in it.

    The one thing a chart can get catastrophically wrong is drawing a zero
    where nobody looked, so this checks the shape of the row as much as the
    numbers in it: absent must survive the whole way out as null.
    """
    ok = True
    _, a = L.req("/api/analytics")
    t = a["teams"].get("101") or a["teams"].get(101)
    trend = t.get("trend") or []
    ok &= check("a scouted, played match becomes one row", len(trend) == 1, f"({len(trend)})")
    row = trend[0]
    ok &= check("the row carries the solver's fuel and its band",
                row["fuel"] is not None and row["band"] is not None, f"({row})")
    ok &= check("and TBA's own numbers beside it, not mixed into it",
                row["officialFuel"] == 70 and row["climb"] == "Level2", f"({row})")
    ok &= check("our scouts' defence seconds are a real zero, because a scout watched",
                row["defenseSecs"] == 0.0, f"({row['defenseSecs']})")
    ok &= check("nobody defended this robot, so the seconds faced are unknown, not zero",
                row["defenseFacedSecs"] is None, f"({row['defenseFacedSecs']})")
    ok &= check("with no Lovat key every lovat field on the row is unknown",
                row["lovatFuel"] is None and row["lovatDefenseSecs"] is None, f"({row})")

    # A match still to be played is not a data point, and a run of empty points
    # on the right of a chart squashes the part with data in it.
    L.store.put_match(EK, f"{EK}_qm2", label="Qualification 2", comp_level="qm", match_number=2,
                      red=[101, 102, 103], blue=[201, 202, 203])
    _, a = L.req("/api/analytics")
    t = a["teams"].get("101") or a["teams"].get(101)
    ok &= check("a scheduled match nobody has played yet adds no point",
                len(t["trend"]) == 1, f"({len(t['trend'])})")
    return ok


def test_defence_counts_both_ways(L):
    """Defence is logged against the robot doing it; the robot taking it wants
    to know too, in seconds as well as in matches."""
    ok = True
    # Newer than whatever the earlier tests left behind - last-write-wins is the
    # whole ingest rule, and a hard-coded offset would quietly lose to the
    # import test's future-dated row.
    latest = max([e["updatedAt"] for e in L.store.scout_entries(EK)] or [time.time()])
    e = entry(f"{EK}_qm1", 101, "AK", latest + 60)
    e["payload"]["defenseIntervals"] = [{"start": 40.0, "end": 55.0}]
    e["payload"]["defenseTarget"] = 201
    L.req("/api/sync", {"scout": [e]})
    _, a = L.req("/api/analytics")
    by = a["teams"].get("101") or a["teams"].get(101)
    on = a["teams"].get("201") or a["teams"].get(201)
    ok &= check("the defender's own seconds land in observed",
                by["observed"]["defenseSecs"] == 15.0, f"({by['observed']['defenseSecs']})")
    ok &= check("and the robot on the receiving end is told, in seconds",
                on["observed"]["defenseFacedSecs"] == 15.0
                and on["observed"]["defenseFacedMatches"] == 1,
                f"({on['observed']})")
    ok &= check("the same seconds land on that match's row, for the chart",
                any(r["defenseFacedSecs"] == 15.0 for r in (on.get("trend") or [])),
                f"({on.get('trend')})")
    return ok


def test_picklist_lock(L):
    ok = True
    code, pl = L.req("/api/picklist")
    ok &= check("the board is readable with no passcode set",
                code == 200 and pl["canEdit"] is True and pl["locked"] is False)

    code, _ = L.req("/api/config", {"strategyPin": "4821"})
    ok &= check("a passcode can be set", code == 200)

    code, pl = L.req("/api/picklist")
    ok &= check("the board stays readable once locked",
                code == 200 and pl["locked"] is True and pl["canEdit"] is False)

    code, _ = L.req("/api/picklist", {"order": [103, 101]})
    ok &= check("editing without the passcode is refused", code == 403)

    code, r = L.req("/api/unlock", {"pin": "0000"})
    ok &= check("a wrong passcode does not unlock", code == 403 and r["ok"] is False)

    code, r = L.req("/api/unlock", {"pin": "4821"})
    token = r.get("token")
    ok &= check("the right passcode issues a token", code == 200 and bool(token))

    code, r = L.req("/api/picklist", {"order": [103, 101], "order2": [101],
                                      "weights2": {"defense": 40}},
                    headers={"X-Strategy-Token": token})
    ok &= check("editing with the token is accepted",
                code == 200 and r["picklist"]["order"] == [103, 101])
    ok &= check("both boards persist independently",
                r["picklist"]["order2"] == [101] and r["picklist"]["weights2"]["defense"] == 40)

    # Rotating the code before alliance selection must sign everyone out.
    L.req("/api/config", {"strategyPin": "9999"})
    code, _ = L.req("/api/picklist", {"order": [101]}, headers={"X-Strategy-Token": token})
    ok &= check("changing the passcode invalidates old tokens", code == 403)
    L.req("/api/config", {"strategyPin": ""})
    return ok


def test_export_import_idempotent(L):
    ok = True
    code, dump = L.req("/api/export")
    ok &= check("export names itself", code == 200
                and dump["kind"] == "frc-rebuilt-scouting-export")
    before = len(L.store.scout_entries(EK))

    code, r = L.req("/api/import", dump)
    ok &= check("re-importing the same file changes nothing",
                code == 200 and r["applied"] == 0, f"({r})")
    ok &= check("and adds no rows", len(L.store.scout_entries(EK)) == before)

    code, r = L.req("/api/import", {"kind": "something-else"})
    ok &= check("a file that is not an export is refused", code == 400)

    # A genuinely newer row in the file must still be applied.
    dump["scout"][0]["updatedAt"] = time.time() + 300
    dump["scout"][0]["payload"]["note"] = "from the backup file"
    code, r = L.req("/api/import", dump)
    ok &= check("a newer row in an import is applied", r["applied"] == 1, f"({r})")

    # The export used to carry pit rows with no eventKey on them, and
    # upsert_pit needs one to know what it is merging - so every pit record in
    # a backup file was silently rejected on the way back in. Nothing said so:
    # `applied` counted the match scouting and the pit scouting just was not
    # there. It is the half nobody can re-collect, because the robots have gone
    # home.
    L.req("/api/sync", {"pit": [{
        "eventKey": EK, "team": 9982, "scoutId": "PT", "deviceId": "test",
        "updatedAt": time.time(),
        "payload": {"drivetrain": "swerve", "shooter": "drum", "notes": "tall intake"}}]})
    code, dump = L.req("/api/export")
    ok &= check("the export carries the pit record", len(dump.get("pit") or []) == 1,
                f"({len(dump.get('pit') or [])})")

    fresh = Store(os.path.join(L.dir, "restored.db"))
    landed = sum(1 for rec in dump.get("pit") or [] if fresh.upsert_pit(rec))
    ok &= check("and it restores into an empty database", landed == 1
                and len(fresh.pit_entries(EK)) == 1, f"({landed} applied)")
    return ok


def test_snapshot_and_restore(L):
    """The recovery procedure the README hands to a non-programmer.

    "Stop the server. Copy the newest file out of data/snapshots/ over
    data/scouting.db, delete the -wal and -shm files, start it again."  Nothing
    covered it, and it is the step that runs on the worst day of the event -
    against a database nobody can re-collect, by someone who is not a
    programmer, with matches still queuing.
    """
    ok = True
    entries, matches = len(L.store.scout_entries(EK)), len(L.store.matches(EK))

    dest = L.store.snapshot(keep=12)
    ok &= check("the snapshot lands in snapshots/ beside the database",
                os.path.isfile(dest) and os.path.basename(os.path.dirname(dest)) == "snapshots",
                f"({os.path.basename(dest)})")

    # The claim store.snapshot exists to make: WAL means the .db file on disk is
    # not a whole database, so this is sqlite's backup API rather than a copy.
    # Carrying the one file off and opening it with no -wal or -shm beside it is
    # exactly what a lead does, and is the assertion that would catch a
    # regression to shutil.copy.
    alone = os.path.join(L.dir, "carried-off", "scouting.db")
    os.makedirs(os.path.dirname(alone), exist_ok=True)
    shutil.copy(dest, alone)
    lifted = Store(alone)
    ok &= check("and is a whole database on its own, with no -wal or -shm beside it",
                len(lifted.scout_entries(EK)) == entries and len(lifted.matches(EK)) == matches,
                f"({len(lifted.scout_entries(EK))} entries, {len(lifted.matches(EK))} matches)")

    # The procedure end to end, on a database of its own so the rest of the
    # suite keeps its event.
    room = tempfile.mkdtemp(prefix="frc-restore-test-")
    try:
        live = os.path.join(room, "scouting.db")
        st = Store(live)
        st.set("eventKey", EK)
        st.put_event(EK, name="Test Event", level="regional")
        st.put_teams(EK, [{"team": t, "name": f"Team {t}"} for t in (101, 102, 103)])
        good = st.snapshot(keep=12)

        # Whatever lands after the snapshot is what a bad shutdown costs you -
        # at most the ten minutes between snapshots, which is the trade.
        st.put_teams(EK, [{"team": 999, "name": "Written after the snapshot"}])
        ok &= check("the live database has the later write",
                    any(t["team"] == 999 for t in st.teams(EK)))

        st.conn().close()                      # "stop the server"
        shutil.copy(good, live)                # "copy the newest file over"
        for suffix in ("-wal", "-shm"):        # "delete the -wal and -shm"
            if os.path.exists(live + suffix):
                os.remove(live + suffix)

        back = Store(live)                     # "start it again"
        ok &= check("restoring brings the event back",
                    back.get("eventKey") == EK and len(back.teams(EK)) == 3,
                    f"({len(back.teams(EK))} teams)")
        ok &= check("losing only what was written after the snapshot",
                    not any(t["team"] == 999 for t in back.teams(EK)))
        ok &= check("and the restored database answers analytics rather than raising",
                    isinstance(analytics.event_summary(back, EK).get("teams"), dict))

        # Retention, written by hand: the stamp is per-second, so snapshots
        # taken inside one second would collide rather than accumulate.
        out = os.path.join(room, "snapshots")
        for stamp in ("20260101-000001", "20260101-000002", "20260101-000003"):
            open(os.path.join(out, f"scouting-{stamp}.db"), "w").close()
        back.snapshot(keep=2)
        kept = sorted(f for f in os.listdir(out) if f.endswith(".db"))
        ok &= check("keeping the last N prunes the oldest", len(kept) == 2, f"({kept})")
    finally:
        shutil.rmtree(room, ignore_errors=True)
    return ok


def test_csv_export(L):
    ok = True
    code, body = L.req("/api/export.csv?table=teams", raw=True)
    lines = body.strip().splitlines()
    ok &= check("teams csv has a header and a row per team",
                code == 200 and lines[0].startswith("team,name,rank") and len(lines) >= 2,
                f"({len(lines)} lines)")
    code, body = L.req("/api/export.csv?table=scout", raw=True)
    ok &= check("scout csv exports one row per entry",
                code == 200 and len(body.strip().splitlines()) == len(L.store.scout_entries(EK)) + 1)
    code, body = L.req("/api/export.csv?table=pit", raw=True)
    ok &= check("pit csv answers even with no pit data", code == 200)
    code, r = L.req("/api/export.csv?table=nonsense")
    ok &= check("an unknown table is a 400, not a stack trace", code == 400)
    return ok


def test_hostile_input(L):
    """Anything on the venue wifi can POST here. None of it may take a thread down.

    Every handler reads `body.get(...)`; JSON's top level can be a list, a
    string, a number or null, and each of those used to raise inside the request
    thread, so the phone got a dropped connection instead of an answer.
    """
    ok = True
    for name, raw in (("a bare array", b"[1,2,3]"), ("a bare string", b'"hi"'),
                      ("null", b"null"), ("a number", b"7"), ("not json", b"<<<")):
        code, _ = L.req("/api/sync", body_bytes=raw)
        ok &= check(f"a body that is {name} still gets an answer", code == 200, f"({code})")

    for name, body in (("scout is a dict", {"scout": {"a": 1}}),
                       ("pit is a number", {"pit": 3}),
                       ("rows are nulls", {"scout": [None, 1, "x"]}),
                       ("who is a string", {"scout": [], "who": "me"})):
        code, _ = L.req("/api/sync", body)
        ok &= check(f"sync survives when {name}", code == 200, f"({code})")

    for name, body in (("a list", {"matchKey": ["a"]}), ("a dict", {"matchKey": {"a": 1}}),
                       ("empty", {}), ("50k long", {"matchKey": "m" * 50000})):
        code, _ = L.req("/api/matchstart", body)
        ok &= check(f"a matchKey that is {name} is a 400, not a dropped thread", code == 400,
                    f"({code})")

    # And the hub is still answering afterwards.
    code, _ = L.req("/api/state")
    ok &= check("the hub still answers after all of that", code == 200)
    return ok


def test_junk_payload_cannot_blank_the_dashboard(L):
    """One malformed record must not take the whole event down with it.

    Every consumer - solver, analytics, exports, dashboard - reads the payload
    with `.get()`. A row whose payload was a string stopped `/api/analytics`
    answering at all, so one bad record from one phone blanked the strategy
    dashboard for the rest of the event, and the match it was in never solved.
    """
    ok = True
    ek = "2026junk"
    was = L.store.get("eventKey")
    L.store.set("eventKey", ek)
    L.store.put_event(ek)
    L.store.put_match(ek, f"{ek}_qm1", label="Qualification 1", red=[101, 102, 103],
                      blue=[201, 202, 203],
                      breakdown={"red": {"windows": {"auto": 30, "shift1": 30}},
                                 "blue": {"windows": {"auto": 10}}})
    good = entry(f"{ek}_qm1", 101, "AK", time.time())
    good["eventKey"] = ek
    L.req("/api/sync", {"scout": [good]})

    for junk in ("corrupted", [1, 2, 3], 7, None):
        rec = entry(f"{ek}_qm1", 102, "BAD", time.time() + 50)
        rec["eventKey"] = ek
        rec["payload"] = junk
        code, _ = L.req("/api/sync", {"scout": [rec]})
        ok &= check(f"a {type(junk).__name__} payload is accepted without a crash", code == 200)

    rows = L.store.scout_entries(ek)
    ok &= check("no non-dict payload survives into the store",
                all(isinstance(r["payload"], dict) for r in rows),
                f"({[type(r['payload']).__name__ for r in rows]})")
    code, a = L.req("/api/analytics?event=" + ek)
    ok &= check("analytics still answers", code == 200, f"({code})")
    ok &= check("and the match still solved despite the bad row",
                bool([s for s in L.store.solved(ek) if s["matchKey"] == f"{ek}_qm1"]))
    L.store.set("eventKey", was)          # this fixture is not the event under test
    return ok


def test_clock_correction_never_invents_numbers(L):
    """A correction that empties a robot out is not a correction.

    CLOCK_FIX_LIMIT was 180s against a 160s match, so an offset large enough to
    shift every observation past the final buzzer was still trusted. Every
    interval lost its window, the solver fell back to an even split, and a
    fabricated 40/40/40 was reported with nothing flagged on it.
    """
    ok = True
    ek = "2026clock"
    mk = f"{ek}_qm1"
    was = L.store.get("eventKey")
    L.store.set("eventKey", ek)
    L.store.put_event(ek)
    actual = time.time() - 500
    L.store.put_match(ek, mk, label="Qualification 1", red=[101, 102, 103], blue=[201, 202, 203],
                      times={"actual": actual},
                      breakdown={"red": {"windows": {"auto": 30, "shift1": 30,
                                                     "shift2": 30, "endgame": 30}},
                                 "blue": {"windows": {"auto": 10}}})

    def iv(s, e, i="steady"):
        ph = hub.rules.phase_at(s)
        return {"start": s, "end": e, "phase": ph["id"] if ph else None, "intensity": i}

    plan = {101: [iv(2, 18, "dumping"), iv(32, 52, "dumping"),
                  iv(58, 78, "dumping"), iv(132, 158, "dumping")],
            102: [iv(6, 10), iv(36, 40), iv(60, 64), iv(136, 140)],
            103: [iv(8, 9, "trickle")]}
    for t, ivs in plan.items():
        L.store.upsert_scout({"eventKey": ek, "matchKey": mk, "team": t, "scoutId": f"S{t}",
                              "deviceId": f"d{t}", "alliance": "red", "station": 1,
                              "updatedAt": time.time(),
                              "payload": {"intervals": ivs, "clockShared": True}})

    ok &= check("a correction can never be longer than the match itself",
                hub.Hub.CLOCK_FIX_LIMIT <= hub.rules.MATCH_SECONDS,
                f"({hub.Hub.CLOCK_FIX_LIMIT}s vs {hub.rules.MATCH_SECONDS}s)")

    def solved_for(late):
        L.store.mutate("matchClocks",
                       lambda c: ({mk: {"matchKey": mk, "startedAt": actual + late, "by": "S"}},
                                  None), {})
        L.store.conn().execute("DELETE FROM solved WHERE match_key=?", (mk,))
        L.store.conn().execute("DELETE FROM flags WHERE match_key=?", (mk,))
        L.hub.solve_match(mk)
        return {r["team"]: r["fuel"] for r in L.store.solved(ek)
                if r["matchKey"] == mk and r["team"] in (101, 102, 103)}

    honest = solved_for(0)
    ok &= check("with a good clock the dominant robot is credited",
                honest[101] > honest[102] > honest[103], f"({honest})")

    for late in (170, 300):
        got = solved_for(late)
        ok &= check(f"a {late}s-late tap does not become an even split",
                    len(set(got.values())) > 1, f"({got})")
        ok &= check(f"and the {late}s offset is flagged rather than silently applied",
                    any(f["kind"] == "clock-offset" for f in L.store.flags(ek)))
        ok &= check(f"the real allocation survives a {late}s-late tap", got == honest, f"({got})")
    L.store.set("eventKey", was)          # this fixture is not the event under test
    return ok


def test_burst_of_connections(L):
    """Everything connects at once at the buzzer, and the kernel decides first.

    socketserver's default accept backlog is 5. Six phones flushing on the
    buzzer, two dashboards on their own timers and the pit tablet genuinely do
    arrive together: measured, 60 of 200 simultaneous connects were reset
    before a line of handler code ran. A queued scouting record survives that
    and retries; a seat claim and a match clock are one-shot, and the clock is
    what the solver's accuracy rests on.
    """
    ok = True
    ok &= check("the accept backlog is not socketserver's default 5",
                hub.Server.request_queue_size >= 64, f"({hub.Server.request_queue_size})")

    errs = []
    def one(i):
        code, _ = L.req("/api/seat", {"alliance": "red", "station": (i % 3) + 1,
                                      "scoutId": "BX", "deviceId": f"burst{i}"})
        if code != 200:
            errs.append(code)
    ts = [threading.Thread(target=one, args=(i,)) for i in range(60)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ok &= check("60 phones connecting at the same instant all get served",
                not errs, f"({len(errs)} refused)")
    for k in list(L.hub.seats()):          # leave the chairs as we found them
        L.req("/api/unseat", {"seat": k})

    # the writes/min list is trimmed as it grows, not only when someone opens
    # the dashboard on it
    L.hub.record_writes(50000)
    ok &= check("the writes/min buffer has a ceiling", len(L.hub.writes) <= 1000,
                f"({len(L.hub.writes)})")
    return ok


def test_seats(L):
    ok = True
    L.req("/api/seat", {"alliance": "red", "station": 2, "scoutId": "AK", "deviceId": "phone-a"})
    code, seats = L.req("/api/seats")
    ok &= check("claiming a station records it", "red2" in seats)

    # A scout who really is in that chair must always win.
    _, r = L.req("/api/seat", {"alliance": "red", "station": 2,
                               "scoutId": "CJ", "deviceId": "phone-b"})
    ok &= check("a second phone takes the chair from the first",
                r["seats"]["red2"]["deviceId"] == "phone-b")

    # One device sits in exactly one seat, or two robots go unwatched.
    L.req("/api/seat", {"alliance": "blue", "station": 1, "scoutId": "CJ", "deviceId": "phone-b"})
    _, seats = L.req("/api/seats")
    ok &= check("moving a phone vacates its old chair",
                "blue1" in seats and "red2" not in seats, f"({sorted(seats)})")

    L.req("/api/unseat", {"seat": "blue1"})
    _, seats = L.req("/api/seats")
    ok &= check("the lead can free a chair", "blue1" not in seats)

    # A claim key used to be f"{alliance}{station}" with nothing checked, so a
    # phone posting a station it does not have left a chair in the map that no
    # dashboard row could show and no FREE button could clear.
    L.req("/api/seat", {"alliance": "blue", "station": 3, "scoutId": "AK", "deviceId": "p"})
    for bad in ({"alliance": "red", "station": 0, "scoutId": "AK", "deviceId": "p"},
                {"alliance": "green", "station": 1, "scoutId": "AK", "deviceId": "p"},
                {"alliance": "red", "station": 1, "scoutId": "", "deviceId": "p"},
                {"alliance": "red", "station": 1, "scoutId": "AK"}):
        code, _ = L.req("/api/seat", bad)
        ok &= check(f"a claim of {bad.get('alliance')}{bad.get('station')} "
                    f"by {bad.get('scoutId')!r} is a 400", code == 400)
    _, seats = L.req("/api/seats")
    ok &= check("and nothing junk reached the seat map",
                sorted(seats) == ["blue3"], f"({sorted(seats)})")
    L.req("/api/unseat", {"seat": "blue3"})

    code, _ = L.req("/api/unseat", {"seat": "red9"})
    ok &= check("freeing a station that does not exist is a 400", code == 400)

    # The lead clicks FREE on the row in front of them. If someone else has sat
    # down in that chair since the board was drawn, the click must miss.
    L.req("/api/seat", {"alliance": "red", "station": 3, "scoutId": "AK", "deviceId": "phone-a"})
    L.req("/api/seat", {"alliance": "red", "station": 3, "scoutId": "CJ", "deviceId": "phone-c"})
    L.req("/api/unseat", {"seat": "red3", "deviceId": "phone-a"})
    _, seats = L.req("/api/seats")
    ok &= check("FREE aimed at the scout who left does not throw out their replacement",
                (seats.get("red3") or {}).get("deviceId") == "phone-c")
    L.req("/api/unseat", {"seat": "red3", "deviceId": "phone-c"})
    _, seats = L.req("/api/seats")
    ok &= check("and it does free the phone it names", "red3" not in seats)

    # Freeing a chair was silent, so that phone kept scouting while the lead saw
    # an empty station and sat someone else on the same robot.
    log = L.hub.seat_history()
    ok &= check("a FREE shows up on the lead's swap list",
                bool(log) and log[0]["seat"] == "red3" and log[0].get("freed"))
    return ok


def test_seat_lifetime(L):
    """A chair belongs to whoever is sitting in it, for as long as they are."""
    ok = True
    L.req("/api/seat", {"alliance": "blue", "station": 2, "scoutId": "DM", "deviceId": "phone-d"})

    # `at` used to be written only at the moment of the claim, so a scout who sat
    # down at nine dropped off the crew board at noon and the lead was told the
    # robot was unwatched while someone was watching it.
    def age(seats):
        seats = dict(seats)
        seats["blue2"] = {**seats["blue2"], "at": time.time() - hub.Hub.SEAT_TTL + 30}
        return seats, None
    L.store.mutate("seats", age, {})
    L.hub.touch({"deviceId": "phone-d", "scoutId": "DM", "seat": "blue2"}, "sync")
    fresh = L.hub.seats()["blue2"]["at"]
    ok &= check("hearing from the phone keeps its chair alive",
                time.time() - fresh < 5, f"({int(time.time() - fresh)}s old)")

    # Re-asserting the same chair is what a phone does every time it finds the
    # hub again. It must not read as a swap, or the lead's list fills with noise.
    before = len(L.hub.seat_history(limit=60))
    L.req("/api/seat", {"alliance": "blue", "station": 2, "scoutId": "DM", "deviceId": "phone-d"})
    L.req("/api/seat", {"alliance": "blue", "station": 2, "scoutId": "DM", "deviceId": "phone-d"})
    ok &= check("re-claiming the chair you are already in is not a swap",
                len(L.hub.seat_history(limit=60)) == before)

    # A claim that ages out with nobody reporting still goes away.
    def expire(seats):
        seats = dict(seats)
        seats["blue2"] = {**seats["blue2"], "at": time.time() - hub.Hub.SEAT_TTL - 1}
        return seats, None
    L.store.mutate("seats", expire, {})
    ok &= check("a chair nobody has reported from all afternoon is released",
                "blue2" not in L.hub.seats())
    return ok


def test_match_clock(L):
    ok = True
    code, r = L.req("/api/matchstart", {"matchKey": f"{EK}_qm1", "scoutId": "AK"})
    first = r["clock"]["startedAt"]
    ok &= check("the first tap starts the clock", code == 200 and r["clock"]["by"] == "AK")

    time.sleep(0.05)
    _, r = L.req("/api/matchstart", {"matchKey": f"{EK}_qm1", "scoutId": "BR"})
    ok &= check("a later tap never restarts it under anyone",
                r["clock"]["startedAt"] == first and r["clock"]["by"] == "AK")

    code, r = L.req("/api/matchstart", {})
    ok &= check("a matchstart with no match is a 400", code == 400)
    return ok


def test_reconcile(L):
    """A hub restarted onto an existing database must not show zeros."""
    ok = True
    L.store.conn().execute("DELETE FROM solved")
    ok &= check("solved rows can be cleared", not L.store.solved(EK))
    n = L.hub.reconcile()
    ok &= check("reconcile re-solves the backlog on startup",
                n == 1 and len(L.store.solved(EK)) > 0, f"({n} match(es))")
    return ok


def test_config_scope(L):
    ok = True
    code, c = L.req("/api/config")
    ok &= check("config reports which keys are set",
                code == 200 and c["keys"] == {"tba": False, "nexus": False, "frcEvents": False,
                                              "lovat": False, "ai": False, "mirror": False},
                f"({c['keys']})")
    L.req("/api/config", {"frcEventsUser": "someone", "frcEventsToken": "secret"})
    code, c = L.req("/api/config")
    ok &= check("frc events credentials register once both are set",
                c["keys"]["frcEvents"] is True)
    ok &= check("no key value is ever served back to a client",
                "secret" not in json.dumps(c) and "frcEventsToken" not in c)

    L.req("/api/config", {"lovatKey": "lvt-hunter2", "aiKey": "sk-hunter2",
                          "aiModel": "anthropic:claude-opus-5"})
    code, c = L.req("/api/config")
    ok &= check("lovat and ai register as set", c["keys"]["lovat"] is True and c["keys"]["ai"] is True)
    ok &= check("one field sets both the model and its provider",
                c["ai"]["provider"] == "anthropic" and c["ai"]["model"] == "claude-opus-5"
                and c["ai"]["label"] == "Claude Opus 5", f"({c['ai']})")
    ok &= check("no key value is ever served back, ai included",
                "hunter2" not in json.dumps(c))

    # A model typed by hand after this list was written still has to route.
    L.req("/api/config", {"aiModel": "gemini-4.0-imaginary"})
    code, c = L.req("/api/config")
    ok &= check("an unlisted model is routed by name, not rejected",
                c["ai"]["provider"] == "gemini" and c["ai"]["model"] == "gemini-4.0-imaginary",
                f"({c['ai']})")

    # The picker is built from this list, so the order it arrives in is the
    # order a scouting lead reads: Claude, Gemini, OpenAI, then the same models
    # through OpenRouter.
    seen = []
    for m in c["ai"]["models"]:
        if m["provider"] not in seen:
            seen.append(m["provider"])
    ok &= check("the model list is served for the picker, grouped by who answers",
                seen == ["anthropic", "gemini", "openai", "openrouter"]
                and all(m["id"] and m["label"] and m["price"] for m in c["ai"]["models"]),
                f"({len(c['ai']['models'])} models, {seen})")
    ok &= check("and every provider in it has a name the picker can label a group with",
                all(p in c["ai"]["providers"] for p in seen), f"({c['ai']['providers']})")

    # An OpenRouter id carries a slash and its own key. Saved as one field, the
    # two halves cannot end up disagreeing - which on this pair is a whole
    # weekend of "the model could not be reached".
    L.req("/api/config", {"aiModel": "openrouter:anthropic/claude-opus-5"})
    code, c = L.req("/api/config")
    ok &= check("a model routed through openrouter keeps the maker in its id",
                c["ai"]["provider"] == "openrouter"
                and c["ai"]["model"] == "anthropic/claude-opus-5", f"({c['ai']})")
    L.req("/api/config", {"aiModel": "deepseek/deepseek-v3-imaginary"})
    code, c = L.req("/api/config")
    ok &= check("and one typed by hand routes there on the slash alone",
                c["ai"]["provider"] == "openrouter", f"({c['ai']})")

    L.req("/api/config", {"aiModel": "none"})
    code, c = L.req("/api/config")
    ok &= check("choosing none turns it off rather than storing a fake model",
                c["keys"]["ai"] is False and c["ai"]["provider"] == "none"
                and c["ai"]["model"] is None, f"({c['ai']})")

    # Anything not on the allowlist must not become config.
    L.req("/api/config", {"somethingElse": "nope"})
    ok &= check("an unknown config field is ignored", L.store.get("somethingElse") is None)
    return ok


def test_key_hygiene(L):
    """What gets pasted into the API key boxes, and what the hub makes of it.

    Every one of these is a real paste: the header name still attached, the
    quotes off a code sample, a key split over two lines by an email client,
    the Lovat key in the TBA box.  All of them used to be stored verbatim, and
    the only symptom for the rest of the event was a service that quietly
    returned nothing - which reads on every screen exactly like "nobody
    scouted that robot".
    """
    ok = True
    was = L.store.get("eventKey")
    for f in ("tbaKey", "nexusKey", "nexusToken", "frcEventsUser", "frcEventsToken",
              "lovatKey", "aiKey", "mirrorKey"):
        L.req("/api/config", {f: ""})

    good = "a" * 64
    code, r = L.req("/api/config", {"tbaKey": '  "X-TBA-Auth-Key: %s"  ' % good})
    ok &= check("a key pasted with its header name and quotes is saved as the key",
                code == 200 and L.store.get("tbaKey") == good, f"({L.store.get('tbaKey')!r})")
    ok &= check("and the page is told what was taken off it",
                bool(r.get("notes", {}).get("tbaKey")), f"({r.get('notes')})")

    code, _ = L.req("/api/config", {"nexusKey": "nx-abc\n  def"})
    ok &= check("a key split across two lines is joined back up",
                L.store.get("nexusKey") == "nx-abcdef", f"({L.store.get('nexusKey')!r})")

    code, r = L.req("/api/config", {"lovatKey": "Bearer lvt-realkey"})
    ok &= check("a `Bearer` scheme is not part of the key",
                L.store.get("lovatKey") == "lvt-realkey", f"({L.store.get('lovatKey')!r})")

    # The one worth refusing: eight boxes on one page, and a key in the wrong
    # one breaks two services while both still say SET.
    code, r = L.req("/api/config", {"tbaKey": "lvt-thisisalovatkey"})
    ok &= check("a Lovat key in the TBA box is refused, and named",
                code == 400 and "LOVAT" in r["problems"]["tbaKey"], f"({code} {r})")
    ok &= check("and nothing at all was saved by that request",
                L.store.get("tbaKey") == good, f"({L.store.get('tbaKey')!r})")

    for name, body, field in (
            ("a web address", {"tbaKey": "https://www.thebluealliance.com/account"}, "tbaKey"),
            ("the example text", {"nexusKey": "<your-api-key>"}, "nexusKey"),
            ("an email address", {"lovatKey": "lead@team6059.org"}, "lovatKey"),
            ("nothing but punctuation", {"aiKey": '"  "'}, "aiKey"),
            ("a whole file", {"mirrorKey": "x" * 500}, "mirrorKey")):
        code, r = L.req("/api/config", body)
        ok &= check(f"{name} in a key box is refused",
                    code == 400 and field in r.get("problems", {}), f"({code} {r})")

    # A Claude key under a Gemini model has no symptom anywhere but "the model
    # could not be reached", forever.
    code, r = L.req("/api/config", {"aiModel": "gemini:gemini-3.7-flash",
                                    "aiKey": "sk-ant-api03-notreal"})
    ok &= check("an AI key from the wrong company is refused for the model beside it",
                code == 400 and "aiKey" in r.get("problems", {}), f"({code} {r})")
    L.req("/api/config", {"aiModel": "anthropic:claude-opus-5", "aiKey": "sk-ant-api03-notreal"})
    ok &= check("and accepted for the right one", L.store.get("aiKey") == "sk-ant-api03-notreal")

    # The same mistake in the direction OpenRouter makes easy: its key pasted
    # under the maker's own model, because the model name says Claude either way.
    code, r = L.req("/api/config", {"aiModel": "anthropic:claude-opus-5",
                                    "aiKey": "sk-or-v1-notreal"})
    ok &= check("an openrouter key under a model bought direct is refused too",
                code == 400 and "aiKey" in r.get("problems", {}), f"({code} {r})")
    L.req("/api/config", {"aiModel": "openrouter:anthropic/claude-opus-5",
                          "aiKey": "sk-or-v1-notreal"})
    ok &= check("and accepted once the model beside it goes through OpenRouter",
                L.store.get("aiKey") == "sk-or-v1-notreal")

    # FRC Events' own documentation hands you the two halves joined together.
    L.req("/api/config", {"frcEventsToken": "someone:tok-12345678"})
    ok &= check("a `username:token` paste fills in both boxes",
                L.store.get("frcEventsUser") == "someone"
                and L.store.get("frcEventsToken") == "tok-12345678",
                f"({L.store.get('frcEventsUser')!r})")
    L.req("/api/config", {"frcEventsUser": "", "frcEventsToken": ""})
    L.req("/api/config", {"frcEventsToken": "Basic bGVhZDp0b2stODc2NTQzMjE="})
    ok &= check("so does the base64 Basic credential from their docs",
                L.store.get("frcEventsUser") == "lead"
                and L.store.get("frcEventsToken") == "tok-87654321",
                f"({L.store.get('frcEventsUser')!r})")

    # Shape is advisory: a vendor may change its key format, and a hub that
    # refuses to be configured at a competition is worse than a warning.
    code, r = L.req("/api/config", {"tbaKey": "short"})
    ok &= check("an odd-looking key is saved anyway, with a warning",
                code == 200 and L.store.get("tbaKey") == "short"
                and "tbaKey" in r.get("warnings", {}), f"({code} {r})")

    # confirmEventSwitch because the harness is seeded: pointing a hub that
    # holds scouting at another event is asked about once, and test_admin_panel
    # is where that is checked.
    code, r = L.req("/api/config", {"eventKey": " https://www.thebluealliance.com/event/2026CASF/ ",
                                    "confirmEventSwitch": True})
    ok &= check("an event page address is saved as the event key",
                L.store.get("eventKey") == "2026casf", f"({L.store.get('eventKey')!r})")
    code, r = L.req("/api/config", {"eventKey": "casf", "confirmEventSwitch": True})
    ok &= check("an event key with no year on it is flagged, not refused",
                code == 200 and "eventKey" in r.get("warnings", {}), f"({r})")

    # A blank box means "leave that key alone" - nobody retypes eight keys to
    # change the event - so there has to be another way to take one off.
    L.req("/api/config", {"tbaKey": ""})
    ok &= check("an emptied box is how a key is forgotten", not L.store.get("tbaKey"))

    code, c = L.req("/api/config")
    ok &= check("the page is told which boxes hold something, box by box",
                c["saved"]["tbaKey"] is False and c["saved"]["lovatKey"] is True,
                f"({c['saved']})")
    ok &= check("and still never the value of one", "lvt-realkey" not in json.dumps(c))

    # The same rules, run as the boxes are filled in, storing nothing.
    code, r = L.req("/api/keycheck", {"tbaKey": "X-TBA-Auth-Key: %s" % good,
                                      "lovatKey": "https://lovat.app"})
    ok &= check("keycheck answers with the cleaned value and the problems",
                code == 200 and r["cleaned"]["tbaKey"] == good
                and "lovatKey" in r["problems"], f"({r})")
    ok &= check("and saves none of it", not L.store.get("tbaKey"))

    # With nothing configured this touches no network at all, which is the
    # state CI runs in.
    for f in ("nexusKey", "lovatKey", "aiKey", "frcEventsUser", "frcEventsToken", "mirrorKey"):
        L.req("/api/config", {f: ""})
    L.req("/api/config", {"aiModel": "none", "mirrorUrl": ""})
    code, r = L.req("/api/keytest", {})
    ok &= check("testing the keys reports every unset one as unset, not as broken",
                code == 200 and all(v["state"] == "unset" for v in r["checked"].values()),
                f"({r.get('checked')})")

    L.store.set("eventKey", was)      # hand the shared harness back its event
    return ok


def test_env_file(L):
    """The `.env` file, which is where the admin password lives.

    Forty lines of parser, and every one of these is somebody's Saturday: an
    `export` copied out of a shell, quotes around a password with a space in
    it, a `#` inside a password that is not a comment, and a machine whose real
    environment must beat a file sitting in the checkout.
    """
    ok = True
    tmp = tempfile.mkdtemp(prefix="frc-env-test-")
    try:
        path = os.path.join(tmp, ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# a comment\n\n"
                     "export ADMIN_PASSWORD_B64=%s\n"
                     "SPACED = \"two words\"\n"
                     "HASHED='pass#word'\n"
                     "TRAILING=value # and a note\n"
                     "not a line at all\n" % envfile.encode("hub-6059"))
        got = envfile.parse(open(path, encoding="utf-8").read())
        ok &= check("`export`, quotes, comments and junk lines all parse",
                    got.get("SPACED") == "two words" and got.get("HASHED") == "pass#word"
                    and got.get("TRAILING") == "value" and "not" not in got, f"({got})")

        os.environ.pop(envfile.ADMIN, None)
        envfile.load(path)
        ok &= check("the password comes back out of the file",
                    envfile.admin_password() == ("hub-6059", None),
                    f"({envfile.admin_password()})")

        # A host that sets this through systemd must not be overridden by a
        # file that happens to be sitting in the checkout.
        os.environ[envfile.ADMIN] = envfile.encode("from-the-machine")
        envfile.load(path)
        ok &= check("a real environment variable beats the file",
                    envfile.admin_password()[0] == "from-the-machine")

        # Written, not appended: a second line would be a password that depends
        # on which one the reader wins with.
        os.environ.pop(envfile.ADMIN, None)
        envfile.write(envfile.ADMIN, envfile.encode("second"), path)
        envfile.write(envfile.ADMIN, envfile.encode("third"), path)
        text = open(path, encoding="utf-8").read()
        ok &= check("writing the password twice leaves one line, not three",
                    text.count(envfile.ADMIN + "=") == 1
                    and envfile.parse(text)[envfile.ADMIN] == envfile.encode("third"),
                    f"({text.count(envfile.ADMIN + '=')} lines)")
        ok &= check("and every other line survives being rewritten",
                    envfile.parse(text).get("HASHED") == "pass#word")
        ok &= check("the file it writes is readable by nobody else",
                    (os.stat(path).st_mode & 0o077) == 0,
                    oct(os.stat(path).st_mode & 0o777))

        # It is base64, and base64 is not encryption. The test says so too, so
        # nobody reads this as a claim it never made.
        ok &= check("the stored form is decodable by anyone with the file - it is not encrypted",
                    base64.b64decode(envfile.parse(text)[envfile.ADMIN]).decode() == "third")
    finally:
        os.environ.pop(envfile.ADMIN, None)
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def test_admin_panel(L):
    """The settings are an admin panel, not a page anyone at the laptop can retype.

    Two separate things, and they are separate on purpose. The **lock** is
    against an accident - a hub is set up once and then left alone for two
    days, on a laptop that sits on the scoring table with people around it. The
    **password** is against somebody else, it lives in `.env` rather than in any
    box on the page, and it is optional: a team that never sets one must not
    find its own hub locked, which is the same call the picklist already makes
    about its passcode.
    """
    ok = True
    was = L.store.get("eventKey")
    os.environ.pop(envfile.ADMIN, None)
    os.environ.pop(envfile.ADMIN_PLAIN, None)

    code, c = L.req("/api/config")
    ok &= check("with no admin password the settings are open, and say so",
                c["admin"]["set"] is False and c["admin"]["unlocked"] is True
                and c["admin"]["source"] == "none", f"({c['admin']})")
    code, _ = L.req("/api/config", {"ourTeam": "6059"})
    ok &= check("and a save goes through", code == 200 and L.store.get("ourTeam") == "6059")

    os.environ[envfile.ADMIN] = envfile.encode("hub-6059")
    code, c = L.req("/api/config")
    ok &= check("a password in the environment locks the settings behind it",
                c["admin"]["set"] is True and c["admin"]["unlocked"] is False
                and c["admin"]["source"] == "env", f"({c['admin']})")
    ok &= check("and the password itself never comes back out",
                "hub-6059" not in json.dumps(c) and envfile.encode("hub-6059") not in json.dumps(c))

    code, r = L.req("/api/config", {"ourTeam": "254"})
    ok &= check("a save without it is refused, and says which button to press",
                code == 403 and r.get("locked") is True and "UNLOCK" in r["error"],
                f"({code} {r})")
    ok &= check("and changed nothing", L.store.get("ourTeam") == "6059")
    for path in ("/api/keycheck", "/api/keytest"):
        code, _ = L.req(path, {})
        ok &= check(f"{path} is behind the same lock", code == 403, f"({code})")

    code, r = L.req("/api/admin/unlock", {"code": "hub-6058"})
    ok &= check("a wrong password is refused", code == 403 and not r.get("token"), f"({r})")
    code, r = L.req("/api/admin/unlock", {"code": "hub-6059"})
    tok = r.get("token")
    ok &= check("the right one hands back a token", code == 200 and bool(tok))

    hdr = {"X-Admin-Token": tok or ""}
    code, _ = L.req("/api/config", {"ourTeam": "254"}, headers=hdr)
    ok &= check("which is what a save rides on",
                code == 200 and L.store.get("ourTeam") == "254", f"({code})")
    code, c = L.req("/api/config", headers=hdr)
    ok &= check("and the page is told it is unlocked", c["admin"]["unlocked"] is True)

    code, _ = L.req("/api/admin/lock", {}, headers=hdr)
    code, _ = L.req("/api/config", {"ourTeam": "6059"}, headers=hdr)
    ok &= check("locking again spends the token immediately",
                code == 403 and L.store.get("ourTeam") == "254", f"({code})")

    # A value that is there but unusable must lock the panel, never open it: a
    # typo in .env that read as "no password" would be the worst of both.
    _, r = L.req("/api/admin/unlock", {"code": "hub-6059"})
    hdr = {"X-Admin-Token": r.get("token") or ""}
    os.environ[envfile.ADMIN] = "not base64 at all!!"
    code, c = L.req("/api/config")
    ok &= check("a password that cannot be read still locks the panel",
                c["admin"]["set"] is True and c["admin"]["unlocked"] is False
                and "base64" in (c["admin"]["problem"] or ""), f"({c['admin']})")
    code, r = L.req("/api/admin/unlock", {"code": "hub-6059"})
    ok &= check("and nothing unlocks it - the answer says what to fix",
                code == 403 and "base64" in r.get("error", ""), f"({code} {r})")

    # Trimmed on both sides or neither. It used to be stripped off what was
    # typed in and not off what was stored, so a password chosen with a space
    # on the end could never be entered again by anybody.
    os.environ[envfile.ADMIN] = envfile.encode("  hub 6059  ")
    code, r = L.req("/api/admin/unlock", {"code": "hub 6059"})
    ok &= check("a password stored with spaces around it still unlocks",
                code == 200 and bool(r.get("token")), f"({code} {r})")
    code, r = L.req("/api/admin/unlock", {"code": "hub"})
    ok &= check("and a wrong one still does not", code == 403, f"({code})")

    # The name people type out of habit is not read as a password, and is not
    # silently ignored either.
    os.environ.pop(envfile.ADMIN, None)
    os.environ.pop(envfile.ADMIN, None)
    os.environ[envfile.ADMIN_PLAIN] = "hub-6059"
    code, c = L.req("/api/config")
    ok &= check("ADMIN_PASSWORD without the _B64 is named, not ignored",
                c["admin"]["set"] is True and envfile.ADMIN in (c["admin"]["problem"] or ""),
                f"({c['admin']})")

    os.environ.pop(envfile.ADMIN_PLAIN, None)
    code, c = L.req("/api/config")
    ok &= check("and with the line gone the settings open back up",
                c["admin"]["set"] is False and c["admin"]["unlocked"] is True, f"({c['admin']})")

    # ---- the event key, which is the switch that changes every screen at once
    ek = L.store.get("eventKey")
    code, r = L.req("/api/config", {"eventKey": "2026other"})
    ok &= check("switching a hub that holds scouting is asked about, not done",
                code == 409 and r["switch"]["from"] == ek and r["switch"]["records"] > 0,
                f"({code} {r})")
    ok &= check("and nothing was saved by the asking", L.store.get("eventKey") == ek)
    code, _ = L.req("/api/config", {"eventKey": ek})
    ok &= check("re-saving the same event key is not a switch", code == 200)
    code, _ = L.req("/api/config", {"eventKey": "2026other", "confirmEventSwitch": True})
    ok &= check("confirmed, it goes through",
                code == 200 and L.store.get("eventKey") == "2026other")
    code, _ = L.req("/api/config", {"eventKey": "2026third"})
    ok &= check("and an event with nothing in it switches away freely",
                code == 200 and L.store.get("eventKey") == "2026third", f"({code})")
    ok &= check("the old event's scouting is still in the database, not deleted",
                len(L.store.scout_entries(ek)) > 0, f"({len(L.store.scout_entries(ek))} rows)")

    L.store.set("eventKey", was)      # hand the shared harness back its event
    return ok


def test_ai_is_gated_and_grounded(L):
    """The AI routes spend real money and must never be reachable by accident.

    Nothing here calls a model: with no provider configured every route reports
    that plainly, which is also the state most hubs run in.
    """
    ok = True
    real_is_local = hub.Handler._is_local
    try:
        L.req("/api/config", {"aiModel": "none", "aiKey": "", "strategyPin": ""})
        hub.Handler._is_local = lambda self: False
        code, r = L.req("/api/ai/ask", {"question": "who feeds?"})
        ok &= check("with no passcode set, a phone in the stands cannot spend the key",
                    code == 403, f"(HTTP {code})")

        hub.Handler._is_local = lambda self: True
        code, r = L.req("/api/ai/ask", {"question": "who feeds?"})
        ok &= check("the hub machine gets a plain 'no model chosen' answer, not an error",
                    code == 200 and r.get("configured") is False, f"({r})")

        L.req("/api/config", {"aiModel": "anthropic:claude-opus-5", "aiKey": "sk-test"})
        code, r = L.req("/api/ai/ask", {})
        ok &= check("an empty question is refused before any model is called",
                    code == 200 and r.get("text") is None and "question" in (r.get("reason") or ""),
                    f"({r})")
        before = L.store.get("aiCalls") or 0
        code, r = L.req("/api/ai/notes/6059", {"peek": True})
        ok &= check("a peek never spends a call", (L.store.get("aiCalls") or 0) == before,
                    f"({r})")
        code, r = L.req("/api/ai/picklist", {"order": []})
        ok &= check("an empty picklist is refused before any model is called",
                    code == 200 and r.get("text") is None, f"({r})")
        code, r = L.req(f"/api/ai/match/{EK}_qm404", {})
        ok &= check("a match that is not on the schedule is refused, not invented",
                    code == 200 and r.get("text") is None
                    and "no such match" in (r.get("reason") or ""), f"({r})")
        before = L.store.get("aiCalls") or 0
        code, r = L.req(f"/api/ai/match/{EK}_qm1", {"peek": True})
        ok &= check("peeking at a match read never spends a call",
                    code == 200 and (L.store.get("aiCalls") or 0) == before, f"({r})")
    finally:
        hub.Handler._is_local = real_is_local
        L.req("/api/config", {"aiModel": "none", "aiKey": ""})
    return ok


def test_scout_data_is_lead_only(L):
    """Per-scout quality scores must not reach a dashboard in the stands.

    They name individuals and grade them, /api/analytics is open to anything on
    the venue wifi, and nothing downweights a low score anyway - so it was a
    personal scoreboard with no analytical payoff. The lead still gets it, from
    the hub machine or with the strategy passcode.
    """
    ok = True
    real_is_local = hub.Handler._is_local
    try:
        # Pretend every request comes from a phone in the stands.
        hub.Handler._is_local = lambda self: False

        L.req("/api/config", {"strategyPin": ""})
        code, a = L.req("/api/analytics")
        ok &= check("with no passcode set, scout data stays on the hub machine",
                    code == 200 and "scouts" not in a)

        L.req("/api/config", {"strategyPin": "4821"})
        code, a = L.req("/api/analytics")
        ok &= check("a remote dashboard never sees per-scout scores",
                    code == 200 and "scouts" not in a)
        ok &= check("but it still gets everything about the robots",
                    "teams" in a and "coverage" in a and "scoreReport" in a)

        _, r = L.req("/api/unlock", {"pin": "4821"})
        code, a = L.req("/api/analytics", headers={"X-Strategy-Token": r["token"]})
        ok &= check("the passcode unlocks it for the lead",
                    code == 200 and isinstance(a.get("scouts"), list))

        hub.Handler._is_local = lambda self: True
        code, a = L.req("/api/analytics")
        ok &= check("and the hub machine itself always has it",
                    code == 200 and isinstance(a.get("scouts"), list))
    finally:
        hub.Handler._is_local = real_is_local
        L.req("/api/config", {"strategyPin": ""})
    return ok


def test_score_report(L):
    """The report must not be the solver marking its own homework.

    solve_match distributes TBA's official window totals, so summing solved fuel
    per alliance reproduces TBA exactly however wrong the scouts were. The
    report has to use the raw interval estimate instead, and this pins that:
    it is asserted to disagree with TBA on data that was deliberately inflated.
    """
    ok = True
    ek = "2026report"
    L.store.set("eventKey", ek)
    L.store.put_event(ek)
    L.store.put_match(ek, f"{ek}_qm1", label="Qualification 1", comp_level="qm", match_number=1,
                      red=[501, 502, 503], blue=[601, 602, 603],
                      breakdown={"autoWinner": "blue",
                                 "red": {"windows": {"shift1": 100}, "totalPoints": 100,
                                         "endgameTower": ["None"] * 3, "autoTower": ["None"] * 3},
                                 "blue": {"windows": {"auto": 100}, "totalPoints": 100,
                                          "endgameTower": ["None"] * 3, "autoTower": ["None"] * 3}})
    # Red's three scouts claim 30s of DUMPING each in shift1. At the shipped
    # prior of 11 fuel/sec that is ~990 fuel against an official 100.
    for i, team in enumerate((501, 502, 503)):
        L.store.upsert_scout({
            "eventKey": ek, "matchKey": f"{ek}_qm1", "team": team, "scoutId": f"S{i}",
            "alliance": "red", "station": i + 1, "updatedAt": time.time(),
            "payload": {"intervals": [{"start": 31, "end": 61, "phase": "shift1",
                                       "intensity": "dumping"}]}})
    L.hub.solve_match(f"{ek}_qm1")

    solved = sum(r["fuel"] for r in L.store.solved(ek) if r["team"] in (501, 502, 503))
    ok &= check("solved fuel always reproduces TBA exactly (so it cannot grade anything)",
                solved == 100, f"({solved} vs official 100)")

    rep = analytics.score_report(L.store, ek)
    red = [r for r in rep["rows"] if r["alliance"] == "red"][0]
    ok &= check("the report uses the raw estimate and sees the overclaim",
                red["deltaPct"] > 200, f"(off by {red['deltaPct']}%)")
    ok &= check("the official side is reported untouched", red["officialFuel"] == 100)
    ok &= check("an unwatched alliance is not counted in the rollup",
                rep["compared"] == 1, f"({rep['compared']} compared)")

    L.store.set("eventKey", EK)
    return ok


def test_concurrent_writes(L):
    """Six phones do everything at once, because they do.

    seats/matchClocks/devices all live in single kv rows, and a plain
    get-then-set across a thread-per-request server drops writes: before
    Store.mutate this recorded four of six chairs every single run, and handed
    the six phones different "shared" clock origins about half the time.
    """
    ok = True
    ek = "2026race"
    L.store.set("eventKey", ek)
    L.store.put_event(ek)
    for k in list(L.hub.seats()):
        L.req("/api/unseat", {"seat": k})

    seats = [("red", 1), ("red", 2), ("red", 3), ("blue", 1), ("blue", 2), ("blue", 3)]
    bar = threading.Barrier(len(seats))
    results = []

    def claim(i, al, n):
        bar.wait()
        results.append(L.req("/api/seat", {"alliance": al, "station": n,
                                           "scoutId": f"S{i}", "deviceId": f"d{i}"}))
    ts = [threading.Thread(target=claim, args=(i, al, n)) for i, (al, n) in enumerate(seats)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ok &= check("six simultaneous claims record six chairs",
                len(L.hub.seats()) == 6, f"({len(L.hub.seats())} of 6)")

    clocks = []
    bar2 = threading.Barrier(6)

    def tap(i):
        bar2.wait()
        _, r = L.req("/api/matchstart", {"matchKey": f"{ek}_qm1", "scoutId": f"S{i}"})
        clocks.append(r["clock"]["startedAt"])
    ts = [threading.Thread(target=tap, args=(i,)) for i in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    ok &= check("six simultaneous taps share one clock origin",
                len(set(clocks)) == 1, f"({len(set(clocks))} distinct)")

    L.store.set("eventKey", EK)
    return ok


def test_nexus_tba_one_row(L):
    """The failure that made the whole app produce fiction at a real event.

    Nexus labels a qual "Qualification N"; TBA keys it "..._qmN". Keyed
    separately, the phone logged against one row and the solver read the other,
    so every alliance total was split evenly across three robots and none of the
    scouting reached the numbers.
    """
    ok = True
    ek = "2026nexus"
    L.store.set("eventKey", ek)
    L.store.put_event(ek)
    L.hub.apply_nexus_event({"eventKey": ek, "dataAsOfTime": time.time(), "matches": [
        {"label": "Qualification 1", "status": "On field",
         "redTeams": ["101", "102", "103"], "blueTeams": ["201", "202", "203"],
         "times": {"estimatedOnFieldTime": 1e12}}]})

    rows = L.store.matches(ek)
    ok &= check("a nexus label resolves to TBA's key",
                len(rows) == 1 and rows[0]["matchKey"] == f"{ek}_qm1",
                f"({[r['matchKey'] for r in rows]})")

    # The phone reads its matchKey out of /api/state, so log against whatever
    # the hub just handed back - that is the whole point of the bug.
    phone_key = rows[0]["matchKey"]
    L.req("/api/sync", {"scout": [{
        "eventKey": ek, "matchKey": phone_key, "team": 101, "scoutId": "AK",
        "alliance": "red", "station": 1, "updatedAt": time.time(),
        "payload": {"intervals": [{"start": 31, "end": 45, "phase": "shift1",
                                   "intensity": "dumping"}]}}]})

    L.store.put_match(ek, f"{ek}_qm1", comp_level="qm", match_number=1,
                      times={"actual": time.time()},
                      red=[101, 102, 103], blue=[201, 202, 203],
                      breakdown={"autoWinner": "blue",
                                 "red": {"windows": {"shift1": 120}, "totalPoints": 200,
                                         "endgameTower": ["None"] * 3,
                                         "autoTower": ["None"] * 3},
                                 "blue": {"windows": {"auto": 40}, "totalPoints": 150,
                                          "endgameTower": ["None"] * 3,
                                          "autoTower": ["None"] * 3}})
    ok &= check("tba does not add a second row for the same match",
                len(L.store.matches(ek)) == 1)

    m = L.store.match(ek, f"{ek}_qm1")
    ok &= check("nexus and tba timings coexist on one row",
                bool(m["times"].get("estimatedOnFieldTime") and m["times"].get("actual")),
                f"({sorted(m['times'])})")
    ok &= check("nexus status survives a tba write", m["status"] == "On field")

    L.hub.solve_match(f"{ek}_qm1")
    fuel = {r["team"]: r["fuel"] for r in L.store.solved(ek)}
    # The give-away symptom was 40/40/40 - an even split of 120 across three
    # robots, which is what the solver falls back to when it sees no intervals.
    ok &= check("fuel follows the scout, not an even three-way split",
                fuel.get(101) == 120 and fuel.get(102) == 0 and fuel.get(103) == 0,
                f"({[fuel.get(t) for t in (101, 102, 103)]})")

    an = analytics.event_summary(L.store, ek)
    ok &= check("the exact block sees the official result",
                an["teams"][101]["exact"]["matchesWithOfficial"] == 1)
    return ok


def test_legacy_keys_migrate(L):
    """A database written before the fix still has two rows. Merge them."""
    ok = True
    ek = "2026legacy"
    L.store.set("eventKey", ek)
    L.store.put_event(ek)
    L.store.put_match(ek, f"{ek}_qualification1", label="Qualification 1", play_order=0,
                      red=[301, 302, 303], blue=[401, 402, 403], status="On field")
    L.store.put_match(ek, f"{ek}_qm1", label="Qualification 1", comp_level="qm",
                      match_number=1, red=[301, 302, 303], blue=[401, 402, 403],
                      breakdown={"autoWinner": "blue",
                                 "red": {"windows": {"shift1": 90}, "totalPoints": 100,
                                         "endgameTower": ["None"] * 3,
                                         "autoTower": ["None"] * 3}})
    L.store.upsert_scout({"eventKey": ek, "matchKey": f"{ek}_qualification1", "team": 301,
                          "scoutId": "AK", "alliance": "red", "station": 1,
                          "updatedAt": time.time(),
                          "payload": {"intervals": [{"start": 31, "end": 50, "phase": "shift1",
                                                     "intensity": "steady"}]}})
    L.store.set("matchClocks", {f"{ek}_qualification1": {
        "matchKey": f"{ek}_qualification1", "startedAt": time.time(), "by": "AK"}})

    L.hub.reconcile()
    rows = L.store.matches(ek)
    ok &= check("the duplicate row is folded away",
                len(rows) == 1 and rows[0]["matchKey"] == f"{ek}_qm1")
    ok &= check("the scout entry comes with it",
                [e["matchKey"] for e in L.store.scout_entries(ek)] == [f"{ek}_qm1"])
    ok &= check("the match clock is re-pointed",
                list(L.store.get("matchClocks")) == [f"{ek}_qm1"])
    fuel = {r["team"]: r["fuel"] for r in L.store.solved(ek)}
    ok &= check("and the numbers are rebuilt from real scouting",
                fuel.get(301) == 90 and fuel.get(302) == 0,
                f"({[fuel.get(t) for t in (301, 302, 303)]})")

    before = [m["matchKey"] for m in L.store.matches(ek)]
    L.hub.migrate_match_keys(ek)
    ok &= check("migrating twice changes nothing",
                [m["matchKey"] for m in L.store.matches(ek)] == before)

    L.store.set("eventKey", EK)     # hand the shared harness back its event
    return ok


def test_collection_path_is_intact(L):
    """What a scout logs must arrive unchanged, and be readable everywhere.

    The battery work touched the paths around this - when a save is flushed,
    how often the phone polls, when the hub bothers to rebuild - so this pins
    the thing none of that may alter: the observation itself. Every field a
    scout can produce goes in, and has to come back identical through the store,
    the raw endpoint, the export, and the analytics the picklist reads.
    """
    ok = True
    now = time.time()
    mk = f"{EK}_qm1"
    # Deliberately awkward: sub-second interval edges, a hold that crosses a
    # window boundary, a zero preload (which is not the same as unanswered),
    # and every yes/no.
    payload = {
        "intervals": [{"start": 12.34, "end": 19.87, "phase": "auto", "intensity": "dumping"},
                      {"start": 41.02, "end": 58.44, "phase": "shift1", "intensity": "trickle"}],
        "feedIntervals": [{"start": 61.5, "end": 66.25}],
        "defenseIntervals": [{"start": 70.0, "end": 88.75}],
        "preload": 0, "autoTower": "Level1", "endgameTower": "Level3",
        "driverRating": 5, "defenseRating": 3,
        "died": False, "tipped": True, "noShow": False, "fouls": True,
        "note": "crossed a window edge mid-hold",
        "startPosition": "left", "autoFailed": False, "defenseTarget": 201,
        "clockShared": True, "clockBy": "AK",
    }
    rec = {"eventKey": EK, "matchKey": mk, "team": 101, "scoutId": "PATH",
           "deviceId": "path-test", "alliance": "red", "station": 1,
           "updatedAt": now, "payload": payload}
    code, r = L.req("/api/sync", {"scout": [rec]})
    ok &= check("a record posts", code == 200 and r.get("applied"))

    stored = [e for e in L.store.scout_entries(EK, mk) if e["scoutId"] == "PATH"]
    ok &= check("it is stored exactly once", len(stored) == 1)
    if stored:
        got = stored[0]["payload"]
        for k, v in payload.items():
            ok &= check(f"payload.{k} survives the round trip", got.get(k) == v,
                        "" if got.get(k) == v else f"sent {v!r}, got {got.get(k)!r}")
        ok &= check("the scout's own updatedAt is kept, not the server's",
                    abs(stored[0]["updatedAt"] - now) < 1e-6)

    # The same row through the endpoint the dashboard and the export read.
    _, raw = L.req(f"/api/scout?event={EK}&match={mk}")
    mine = [e for e in raw if e["scoutId"] == "PATH"]
    ok &= check("/api/scout returns it with its intervals intact",
                len(mine) == 1 and mine[0]["payload"]["intervals"] == payload["intervals"])
    _, dump = L.req(f"/api/export?event={EK}")
    ok &= check("the export carries it too",
                any(e.get("scoutId") == "PATH" for e in (dump.get("scout") or [])))

    # And a newer edit still wins, which is the rule the whole queue rests on.
    L.req("/api/sync", {"scout": [{**rec, "updatedAt": now + 5,
                                   "payload": {**payload, "note": "edited"}}]})
    after = [e for e in L.store.scout_entries(EK, mk) if e["scoutId"] == "PATH"]
    ok &= check("a later edit replaces it rather than duplicating",
                len(after) == 1 and after[0]["payload"]["note"] == "edited")
    L.req("/api/sync", {"scout": [{**rec, "updatedAt": now - 5,
                                   "payload": {**payload, "note": "stale"}}]})
    after = [e for e in L.store.scout_entries(EK, mk) if e["scoutId"] == "PATH"]
    ok &= check("and an older one is refused", after[0]["payload"]["note"] == "edited")

    # The memoized analytics must see it - this is the path the ETag work
    # rewrote, so it is the one worth proving reaches the picklist.
    summary = analytics.event_summary(L.store, EK)
    ok &= check("the memoized analytics include the new observation",
                (summary["teams"].get(101) or {}).get("matchesScouted", 0) > 0)
    return ok


def test_scope_lists_are_complete(L):
    """Every kv row a handler reads must be named in its scope list.

    This is a check and not a comment because the failure is silent: a scope
    left out means the endpoint answers 304 over data that really did change,
    and the strategy team reads a stale number with nothing on screen to say so.
    Anyone adding a `store.get` to one of these handlers should see this fail.
    """
    ok = True
    reads = []
    real_get, real_mutate = Store.get, Store.mutate

    def spy_get(self, key, default=None):
        reads.append(Store.kv_scope(key)); return real_get(self, key, default)

    def spy_mutate(self, key, fn, default=None):
        reads.append(Store.kv_scope(key)); return real_mutate(self, key, fn, default)

    h, st = L.hub, L.store
    cases = [
        ("STATE_SCOPES", hub.STATE_SCOPES,
         {"events", "teams", "matches", "flags", "pit_entries"}, lambda: (
             st.event(EK), st.teams(EK), st.matches(EK), st.get("nexusLive"),
             h.nexus_data("pits", EK, {}), h.nexus_data("pitMap", EK),
             h.nexus_data("inspection", EK, {}), h.nexus_data("alliances", EK, []),
             st.flags(EK), h.seats(), st.get("matchClocks"), st.get("clockFixes"),
             st.pit_entries(EK), h.event_data("rankings", EK, {}),
             h.event_data("epa", EK, {}), h.event_data("earlyScores", EK, {}))),
        ("ANALYTICS_SCOPES", hub.ANALYTICS_SCOPES,
         {"matches", "teams", "scout_entries", "solved"},
         lambda: analytics._event_summary(st, EK, include_scouts=True)),
        ("CREW_SCOPES", hub.CREW_SCOPES, {"scout_entries"}, lambda: h.crew()),
        ("SEATLOG_SCOPES", hub.SEATLOG_SCOPES, set(), lambda: h.seat_history()),
        ("BUNDLE_SCOPES", offsite.BUNDLE_SCOPES,
         {"events", "teams", "matches", "flags", "scout_entries", "pit_entries",
          "photos", "solved"}, lambda: offsite.build_bundle(h, EK)),
    ]
    try:
        Store.get, Store.mutate = spy_get, spy_mutate
        for name, declared, tables, fn in cases:
            reads.clear()
            fn()
            missing = ({r for r in reads if r.startswith("kv:")} | tables) - set(declared)
            ok &= check(f"{name} names everything its handler reads",
                        not missing, "" if not missing else f"missing {sorted(missing)}")
    finally:
        Store.get, Store.mutate = real_get, real_mutate
    return ok


def test_cheap_polling(L):
    """An unchanged poll must cost a bare 304, and a changed one must not.

    Six phones, two dashboards and a pit tablet poll this hub all day. Before
    this, every one of those requests rebuilt the whole event and re-sent it -
    ~165KB of identical JSON per dashboard per cycle, and the analytics were
    recomputed from every scouting row at the event to produce it. The risk in
    fixing that is the opposite failure: a 304 that hides a real change, which
    would show the strategy team stale numbers with no way to tell. So the
    invalidation is what is actually tested here.
    """
    ok = True

    # Plant a chair nobody has reported from since this morning. seats() used to
    # expire that by writing the filtered map back - during the payload build,
    # after the tag had already been taken - so the tag handed to the client was
    # stale the instant it was issued and the next poll got a 200 it did not
    # need. Nothing served was ever wrong, but an unreliable 304 is no 304 at
    # all. CI caught this only because an earlier test happened to leave such a
    # chair behind; planted here, it is caught every run.
    L.store.set("seats", {**(L.store.get("seats") or {}),
                          "red3": {"scoutId": "GN", "deviceId": "gone",
                                   "at": time.time() - hub.Hub.SEAT_TTL - 60}})
    # Nothing may call hub.seats() between planting it and the request below -
    # a single read used to be enough to sweep it, which is what made this
    # intermittent rather than reliable in the first place.
    seats_v = L.store.version_for("kv:seats")
    _, state_tag, _ = L.cond("/api/state")
    ok &= check("serving /api/state does not write to seats",
                L.store.version_for("kv:seats") == seats_v)
    ok &= check("and the expired chair is filtered out of what it served",
                "red3" not in (L.req("/api/seats")[1] or {}))

    for path in ("/api/state", "/api/analytics", "/api/crew", "/api/seatlog"):
        code, etag, body = L.cond(path)
        ok &= check(f"{path} answers with an ETag", code == 200 and bool(etag))
        # Serving an endpoint must not move its own tag. This is the check that
        # would have failed before the fix above.
        _, again, _ = L.cond(path)
        ok &= check(f"{path} does not invalidate its own tag by being served",
                    again == etag)
        code, _, body = L.cond(path, etag)
        ok &= check(f"{path} repeated is a bare 304", code == 304 and body == b"",
                    f"({len(body)} bytes)")

    # A write has to move the tags of the endpoints that read it, and only those.
    _, state_before, _ = L.cond("/api/state")
    _, an_before, _ = L.cond("/api/analytics")
    L.req("/api/sync", {"scout": [entry(f"{EK}_qm1", 103, "ET", time.time(), "etag")]})
    ok &= check("a scout sync moves the analytics tag",
                L.cond("/api/analytics")[1] != an_before)
    ok &= check("and /api/state re-serves after it",
                L.cond("/api/state", state_before)[0] in (200, 304))
    ok &= check("a stale analytics tag gets the new data, not a 304",
                L.cond("/api/analytics", an_before)[0] == 200)

    # /api/crew reads the live SSE subscriber set, which no store write touches.
    _, crew_before, _ = L.cond("/api/crew")
    q = L.hub.subscribe({"deviceId": "etag-probe", "scoutId": "ZZ", "seat": "red1"})
    ok &= check("a phone connecting moves the crew tag",
                L.cond("/api/crew")[1] != crew_before)
    L.hub.unsubscribe(q)

    # A seat claim is a kv write and shows on both.
    _, state_before, _ = L.cond("/api/state")
    _, crew_before, _ = L.cond("/api/crew")
    L.req("/api/seat", {"alliance": "blue", "station": 3, "scoutId": "QQ", "deviceId": "etag-dev"})
    ok &= check("a seat claim moves the state tag", L.cond("/api/state")[1] != state_before)
    ok &= check("a seat claim moves the crew tag", L.cond("/api/crew")[1] != crew_before)

    # The one endpoint that must never be cached: net.js corrects every phone's
    # clock skew against this, and the shared clock is what the solver rests on.
    code, etag, _ = L.cond("/api/config")
    ok &= check("/api/config carries no ETag", code == 200 and not etag)
    t1 = L.req("/api/config")[1]["serverTime"]
    time.sleep(0.05)
    t2 = L.req("/api/config")[1]["serverTime"]
    ok &= check("/api/config serverTime still advances", t2 > t1)

    # The crew board works out ages itself now, so two polls a second apart are
    # the same bytes. If the hub went back to sending ages, nothing above would
    # fail - the tag would still match - but the board would freeze.
    rows = L.req("/api/crew")[1]
    ok &= check("crew rows carry instants, not server-computed ages",
                all("lastSeenSec" not in r and "lastMatchAgoSec" not in r for r in rows)
                and any("lastSeenAt" in r for r in rows))
    return ok


def test_write_counters(L):
    """The counters the ETags and the analytics cache are built on."""
    ok = True
    v = lambda *s: L.store.version_for(*s)

    before = v("matches")
    L.store.put_match(EK, f"{EK}_qm1", label="Qualification 1", comp_level="qm",
                      match_number=1, red=[101, 102, 103], blue=[201, 202, 203])
    ok &= check("re-putting an identical match writes nothing", v("matches") == before)
    L.store.put_match(EK, f"{EK}_qm1", status="On field")
    ok &= check("a real change does move it", v("matches") != before)

    before = v("teams")
    L.store.put_teams(EK, [{"team": t, "name": f"Team {t}"} for t in (101, 102, 103)])
    ok &= check("re-putting identical teams writes nothing", v("teams") == before)

    before = v("kv:seats")
    L.store.mutate("seats", lambda cur: (None, cur), {})
    ok &= check("a mutate that declines to write does not count", v("kv:seats") == before)

    # Scoped, not global: hub.touch() writes `devices` once a minute per phone,
    # and with one counter six phones would leave no 304s to give.
    before = v("matches", "teams", "scout_entries", "solved")
    L.store.set("devices", {"d": {"at": time.time()}})
    ok &= check("a device heartbeat does not disturb the analytics scopes",
                v("matches", "teams", "scout_entries", "solved") == before)

    before = L.store.version_for("photos")
    L.store.snapshot(keep=2)
    ok &= check("a snapshot changes no data and no counter",
                L.store.version_for("photos") == before)

    # The memoized summary must be the same object until something it reads moves.
    a = analytics.event_summary(L.store, EK)
    b = analytics.event_summary(L.store, EK)
    ok &= check("event_summary is memoized between writes", a is b)
    L.req("/api/sync", {"scout": [entry(f"{EK}_qm1", 102, "MM", time.time(), "cache bust")]})
    c = analytics.event_summary(L.store, EK)
    ok &= check("and recomputed after one", c is not a)
    return ok


def test_static_revalidates(L):
    """Code and markup are no-cache, which is only cheap if there is an ETag."""
    ok = True
    code, etag, body = L.cond("/js/net.js")
    ok &= check("a script is served with an ETag", code == 200 and bool(etag) and len(body) > 0)
    code, _, body = L.cond("/js/net.js", etag)
    ok &= check("and revalidates to a bare 304", code == 304 and body == b"")
    return ok


def test_nexus_broadcasts_only_on_change(L):
    """The broadcast that used to wake every device in the building every 20s.

    `dataAsOfTime` is Nexus's own clock and advances on every poll, so it orders
    updates but says nothing about whether they carry news. Each broadcast costs
    a full refresh on every dashboard, a whole /api/state and a ~60KB IndexedDB
    rewrite on every phone, and a complete SVG rebuild on the pit tablet.
    """
    ok = True
    sent = []
    real = L.hub.broadcast
    L.hub.broadcast = lambda kind, payload: sent.append(kind)
    try:
        payload = {"eventKey": EK, "dataAsOfTime": time.time() * 1000,
                   "nowQueuing": "Qualification 5", "matches": [], "announcements": []}
        L.hub.apply_nexus_event(dict(payload))
        first = sent.count("nexus")
        # Same news, later timestamp - which is exactly what a quiet poll looks like.
        payload["dataAsOfTime"] += 20000
        L.hub.apply_nexus_event(dict(payload))
        ok &= check("an unchanged Nexus poll broadcasts nothing",
                    sent.count("nexus") == first, f"({sent.count('nexus')} sent)")
        payload["dataAsOfTime"] += 20000
        payload["nowQueuing"] = "Qualification 6"
        L.hub.apply_nexus_event(dict(payload))
        ok &= check("a real change still broadcasts", sent.count("nexus") == first + 1)
    finally:
        L.hub.broadcast = real
    return ok


def test_vendor_backoff_is_remembered(L):
    """A vendor that says stop has to still be saying it on the next call.

    `Lovat.down_until` and `ai.Client.down_until` are the whole of this hub's
    politeness to two rate-limited services - Lovat allows one request every
    three seconds, and an AI key that was just rejected will be rejected
    again. Both clients used to be rebuilt from the settings row on every
    single call, so the field was written onto an object thrown away on the
    next line: nothing ever backed off, and the diagnostics panel could never
    show that anything had.
    """
    ok = True
    L.store.set("lovatKey", "lvt-" + "a" * 20)
    first = L.hub.lovat()
    ok &= check("the same lovat client answers twice", L.hub.lovat() is first)
    first.down_until = time.time() + 300
    ok &= check("so a rate-limit backoff is still there on the next call",
                L.hub.lovat().down_until > time.time())
    ok &= check("and the diagnostics panel can see it",
                any(s["name"] == "lovat" and "backing off" in s["detail"]
                    for s in L.hub.diag()["services"]))

    L.store.set("aiProvider", "anthropic")
    L.store.set("aiKey", "sk-ant-" + "b" * 20)
    L.store.set("aiModel", "claude-opus-5")
    client = L.hub.ai()
    ok &= check("the same ai client answers twice", L.hub.ai() is client and client.ok)
    client.down_until = time.time() + 60
    ok &= check("so a refused key sits its minute out", L.hub.ai().down_until > time.time())

    # ...and editing the key is what clears it, because that is what somebody
    # fixing a wrong key means by fixing it.
    L.store.set("lovatKey", "lvt-" + "c" * 20)
    L.store.set("aiKey", "sk-ant-" + "d" * 20)
    ok &= check("a corrected key is a new client, with no backoff on it",
                L.hub.lovat().down_until == 0.0 and L.hub.ai().down_until == 0.0)
    L.store.set("lovatKey", None)
    L.store.set("aiKey", None)
    L.store.set("aiProvider", None)
    L.store.set("aiModel", None)
    return ok


def main():
    L = Live()
    try:
        seed_event(L)
        passed = True
        for fn in (test_sync_and_last_write_wins, test_solving_ran, test_analytics_null_safe,
                   test_picklist_lock, test_export_import_idempotent,
                   test_snapshot_and_restore, test_csv_export,
                   test_hostile_input, test_burst_of_connections,
                   test_junk_payload_cannot_blank_the_dashboard,
                   test_clock_correction_never_invents_numbers,
                   test_seats, test_seat_lifetime, test_match_clock, test_reconcile,
                   test_config_scope, test_key_hygiene, test_env_file, test_admin_panel,
                   test_trend_series, test_defence_counts_both_ways,
                   test_ai_is_gated_and_grounded,
                   test_nexus_tba_one_row, test_legacy_keys_migrate,
                   test_concurrent_writes, test_score_report,
                   test_scout_data_is_lead_only,
                   test_cheap_polling, test_write_counters, test_static_revalidates,
                   test_nexus_broadcasts_only_on_change, test_scope_lists_are_complete,
                   test_vendor_backoff_is_remembered,
                   test_collection_path_is_intact):
            print(f"\n{fn.__name__.replace('test_', '').replace('_', ' ')}")
            passed &= fn(L)
        print()
        print("ALL PASS" if passed else "FAILURES ABOVE")
        return 0 if passed else 1
    finally:
        L.close()


if __name__ == "__main__":
    sys.exit(main())
