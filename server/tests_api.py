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
                code == 200 and c["keys"] == {"tba": False, "nexus": False, "frcEvents": False},
                f"({c['keys']})")
    L.req("/api/config", {"frcEventsUser": "someone", "frcEventsToken": "secret"})
    code, c = L.req("/api/config")
    ok &= check("frc events credentials register once both are set",
                c["keys"]["frcEvents"] is True)
    ok &= check("no key value is ever served back to a client",
                "secret" not in json.dumps(c) and "frcEventsToken" not in c)

    # Anything not on the allowlist must not become config.
    L.req("/api/config", {"somethingElse": "nope"})
    ok &= check("an unknown config field is ignored", L.store.get("somethingElse") is None)
    return ok


def main():
    L = Live()
    try:
        seed_event(L)
        passed = True
        for fn in (test_sync_and_last_write_wins, test_solving_ran, test_analytics_null_safe,
                   test_picklist_lock, test_export_import_idempotent, test_csv_export,
                   test_seats, test_match_clock, test_reconcile, test_config_scope):
            print(f"\n{fn.__name__.replace('test_', '').replace('_', ' ')}")
            passed &= fn(L)
        print()
        print("ALL PASS" if passed else "FAILURES ABOVE")
        return 0 if passed else 1
    finally:
        L.close()


if __name__ == "__main__":
    sys.exit(main())
