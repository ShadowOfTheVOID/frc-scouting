"""Regression gate for the HTTP surface.  Run: python3 server/tests_api.py

tests_solver.py covers the maths.  This covers everything the maths sits on: the
last-write-wins rule that lets six phones flush at the buzzer in any order, the
passcode that gates the picklist, the export/import round trip the docs promise
is a no-op, and the rule that a missing API key reads as unknown rather than as
zero.  All stdlib, no fixtures on disk beyond a temp database.
"""
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
import hub  # noqa: E402
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

    def req(self, path, body=None, method=None, headers=None, raw=False):
        url = f"http://127.0.0.1:{self.port}{path}"
        data = json.dumps(body).encode() if body is not None else None
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
                                              "lovat": False, "ai": False},
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
    # order a scouting lead reads: Claude, then Gemini, then OpenAI.
    seen = []
    for m in c["ai"]["models"]:
        if m["provider"] not in seen:
            seen.append(m["provider"])
    ok &= check("the model list is served for the picker, grouped Claude/Gemini/OpenAI",
                seen == ["anthropic", "gemini", "openai"]
                and all(m["id"] and m["label"] and m["price"] for m in c["ai"]["models"]),
                f"({len(c['ai']['models'])} models, {seen})")

    L.req("/api/config", {"aiModel": "none"})
    code, c = L.req("/api/config")
    ok &= check("choosing none turns it off rather than storing a fake model",
                c["keys"]["ai"] is False and c["ai"]["provider"] == "none"
                and c["ai"]["model"] is None, f"({c['ai']})")

    # Anything not on the allowlist must not become config.
    L.req("/api/config", {"somethingElse": "nope"})
    ok &= check("an unknown config field is ignored", L.store.get("somethingElse") is None)
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


def main():
    L = Live()
    try:
        seed_event(L)
        passed = True
        for fn in (test_sync_and_last_write_wins, test_solving_ran, test_analytics_null_safe,
                   test_picklist_lock, test_export_import_idempotent,
                   test_snapshot_and_restore, test_csv_export,
                   test_seats, test_match_clock, test_reconcile, test_config_scope,
                   test_ai_is_gated_and_grounded,
                   test_nexus_tba_one_row, test_legacy_keys_migrate,
                   test_concurrent_writes, test_score_report,
                   test_scout_data_is_lead_only):
            print(f"\n{fn.__name__.replace('test_', '').replace('_', ' ')}")
            passed &= fn(L)
        print()
        print("ALL PASS" if passed else "FAILURES ABOVE")
        return 0 if passed else 1
    finally:
        L.close()


if __name__ == "__main__":
    sys.exit(main())
