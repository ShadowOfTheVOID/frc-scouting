"""The mirror, end to end.  Run: python3 mirror/tests_mirror.py

Both halves on real sockets: a hub with a seeded event on one port, the mirror
on another, and a push between them over HTTP exactly as it happens at a
competition. Nothing here is stubbed, because the parts that break are the
seams - a bundle that does not round-trip, a photo that crosses the wire twice,
an export that no longer imports.

The claim under test is the one the docs make: if the laptop is destroyed, the
file this mirror hands back rebuilds the event on a new one.
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "server"))

import server as mirror_server  # noqa: E402
from mirrordb import Store as MirrorStore  # noqa: E402

# The two halves each have a module called `store`-shaped thing, so the mirror's
# is `mirrordb` and only one `store` is ever importable. Getting that wrong
# silently gave the hub the mirror's schema, which is exactly the kind of
# failure a test file is the worst place to debug.
import hub as hub_mod  # noqa: E402
import offsite  # noqa: E402
import vault  # noqa: E402
from store import Store as HubStore  # noqa: E402

SEED = os.path.abspath(os.path.join(_HERE, "..", "server", "seed_demo.py"))

PUSH_KEY = "push-key-for-the-tests"
PASSCODE = "orange-tractor"
EK = "2026demo"


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    return bool(ok)


class LiveMirror:
    def __init__(self, tmp, passcode=PASSCODE, open_read=False):
        self.store = MirrorStore(os.path.join(tmp, "mirror.db"))
        mirror_server.Handler.mirror = mirror_server.Mirror(
            self.store, PUSH_KEY, passcode, open_read=open_read)
        mirror_server.Handler.behind_proxy = False
        self.srv = mirror_server.Server(("127.0.0.1", 0), mirror_server.Handler)
        self.port = self.srv.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def req(self, path, body=None, headers=None, method=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(
            self.url + path, data=data, method=method or ("POST" if data else "GET"),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r, timeout=20) as res:
                payload = res.read()
                if raw:
                    return res.status, payload, dict(res.headers)
                return res.status, json.loads(payload or b"null"), dict(res.headers)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload or b"null"), dict(e.headers)
            except ValueError:
                return e.code, payload, dict(e.headers)


class LiveHub:
    """A hub with a full demo event in it, pointed at a mirror."""

    def __init__(self, tmp, mirror_url):
        path = os.path.join(tmp, "hub.db")
        # The same generator the README tells a team to practise on, run the
        # same way: 31 teams, 40 matches, 26 of them played, with scouting
        # already logged against them.
        subprocess.run([sys.executable, SEED, "--db", path, "--event", EK],
                       check=True, stdout=subprocess.DEVNULL)
        # Encrypted at rest, and the key that opens them is written into the
        # checkout's `.env` when the environment has none. Set here so a test
        # run leaves a developer's own hub alone; server/tests_vault.py covers
        # the file being written for real.
        os.environ.setdefault(vault.KEY_VAR, base64.b64encode(os.urandom(32)).decode())
        self.store = HubStore(path)
        self.hub = hub_mod.Hub(self.store)
        self.hub.reconcile()
        self.store.set("eventKey", EK)
        self.store.set("ourTeam", "6059")
        self.store.set("mirrorUrl", mirror_url)
        self.store.set("mirrorKey", PUSH_KEY)


# ------------------------------------------------------------------- tests

def test_locked(M):
    ok = True
    code, body, _ = M.req("/api/events")
    ok &= check("reading needs the passcode", code == 401, f"({code})")

    code, body, _ = M.req("/api/status")
    ok &= check("but the mirror will say it is alive without one",
                code == 200 and body["locked"] is True and "eventKey" not in body)

    code, body, _ = M.req("/api/unlock", {"pin": "wrong"})
    ok &= check("a wrong passcode is refused", code == 403 and not body.get("token"))

    code, body, _ = M.req("/api/unlock", {"pin": PASSCODE})
    ok &= check("the right one issues a token", code == 200 and bool(body.get("token")))
    tok = body.get("token")

    code, _, _ = M.req("/api/events", headers={"X-Mirror-Token": tok})
    ok &= check("which reads", code == 200)
    code, _, _ = M.req(f"/api/events?t={tok}", headers=None)
    ok &= check("as a query parameter too, for <img> and downloads", code == 200)
    return ok, tok


def test_push_auth(M, bundle):
    ok = True
    code, _, _ = M.req("/api/push", bundle)
    ok &= check("a push with no key is refused", code == 401, f"({code})")
    code, _, _ = M.req("/api/push", bundle, headers={"X-Mirror-Key": "not-it"})
    ok &= check("a push with the wrong key is refused", code == 401, f"({code})")
    code, body, _ = M.req("/api/push", {"kind": "something-else"},
                          headers={"X-Mirror-Key": PUSH_KEY})
    ok &= check("and a file that is not a bundle is refused", code == 400, f"({code})")
    # The read passcode must not be a write key, which is the entire reason
    # there are two secrets rather than one.
    code, _, _ = M.req("/api/push", bundle, headers={"X-Mirror-Key": PASSCODE})
    ok &= check("the read passcode cannot write", code == 401, f"({code})")
    return ok


def test_key_check(H, M):
    """TEST KEYS on the Setup page, against a real mirror.

    A push that is failing has no symptom at the venue - everything there keeps
    working - so the only way anybody found out used to be to go looking. This
    is the check that answers before an event rather than after one, and the
    two failures it has to tell apart are a typo in the address and a wrong
    push key, which are otherwise the same silence.
    """
    ok = True
    code, body, _ = M.req("/api/ping", {}, headers={"X-Mirror-Key": PUSH_KEY})
    ok &= check("the mirror answers a keyed ping", code == 200 and body.get("ok") is True,
                f"({code} {body})")
    code, _, _ = M.req("/api/ping", {})
    ok &= check("and refuses an unkeyed one", code == 401, f"({code})")
    code, _, _ = M.req("/api/ping", {}, headers={"X-Mirror-Key": PASSCODE})
    ok &= check("the read passcode is not a push key here either", code == 401, f"({code})")

    v = offsite.Mirror(M.url, PUSH_KEY).verify()
    ok &= check("the hub reports a working mirror as working",
                v["state"] == "ok", f"({v})")
    v = offsite.Mirror(M.url, "not-the-push-key").verify()
    ok &= check("a wrong push key is named as the push key",
                v["state"] == "bad" and "push key" in v["detail"], f"({v})")
    v = offsite.Mirror(M.url + "/nowhere", PUSH_KEY).verify()
    ok &= check("a wrong address is named as the address",
                v["state"] == "bad" and "address" in v["detail"], f"({v})")
    v = offsite.Mirror(M.url, "").verify()
    ok &= check("an address with no key saved is a warning, not a failure",
                v["state"] == "warn", f"({v})")
    v = offsite.Mirror("", "").verify()
    ok &= check("and no mirror at all is not a failure either", v["state"] == "unset", f"({v})")
    return ok


def test_round_trip(H, M, tok):
    ok = True
    res = offsite.push_once(H.hub, force=True)
    ok &= check("the hub pushes the event", res.get("ok") and res.get("revision"),
                f"({res.get('bytes')} bytes, rev {res.get('revision')})")

    code, snap, _ = M.req(f"/api/snapshot?t={tok}")
    ok &= check("the mirror hands the bundle back",
                code == 200 and snap.get("eventKey") == EK)

    hub_teams = len(H.store.teams(EK))
    hub_entries = len(H.store.scout_entries(EK))
    ok &= check("with every team and every scout entry",
                len(snap.get("teams") or []) == hub_teams
                and len(snap.get("scout") or []) == hub_entries,
                f"({len(snap.get('teams') or [])}/{hub_teams} teams, "
                f"{len(snap.get('scout') or [])}/{hub_entries} entries)")

    # The numbers, not just the rows: a mirror that carries the raw scouting
    # but drops the solved fuel would look fine and be useless in the stands.
    teams = (snap.get("analytics") or {}).get("teams") or {}
    fuels = [t["estimated"]["avgFuel"] for t in teams.values() if t["estimated"]["matches"]]
    ok &= check("and the solved fuel numbers the dashboard shows",
                len(fuels) > 10 and any(f > 0 for f in fuels), f"({len(fuels)} teams with fuel)")

    ok &= check("the team CSV comes across whole",
                (snap.get("csv") or {}).get("teams", "").count("\n") > 10)

    # Per-scout quality scores name people. The hub only shows them to the
    # lead; they must not be on a public site at all.
    ok &= check("per-scout quality scores are not mirrored",
                "scouts" not in (snap.get("analytics") or {}))
    return ok, snap


def test_skips_unchanged(H):
    ok = True
    first = offsite.push_once(H.hub, force=True)
    again = offsite.push_once(H.hub)
    ok &= check("an unchanged event is not pushed twice",
                again.get("skipped") == "unchanged", f"({again})")

    H.store.upsert_scout({
        "eventKey": EK, "matchKey": H.store.matches(EK)[0]["matchKey"], "team": 9999,
        "scoutId": "ZZ", "updatedAt": time.time(),
        "payload": {"note": "a new observation", "intervals": []},
    })
    third = offsite.push_once(H.hub)
    ok &= check("one new scout entry is",
                third.get("ok") and third.get("revision") != first.get("revision"),
                f"(rev {first.get('revision')} -> {third.get('revision')})")
    return ok


def test_photos(H, M, tok):
    ok = True
    # A pit record carries its photo as a data: URI; the hub splits it out into
    # the photos table on import, which is the path a real pit scout takes.
    team = H.store.teams(EK)[0]["team"]
    png = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    hub_mod._extract_photos(H.store, {
        "eventKey": EK, "team": team, "updatedAt": time.time(),
        "payload": {"photos": ["data:image/png;base64," + base64.b64encode(png).decode()]},
    })
    ids = H.store.photo_ids(EK)
    ok &= check("the hub holds a pit photo", len(ids) >= 1, f"({len(ids)})")

    res = offsite.push_once(H.hub, force=True)
    ok &= check("the mirror asks for the photos it does not have",
                res.get("photosSent", 0) >= 1, f"({res.get('photosSent')} sent)")

    code, raw, headers = M.req(f"/api/photo/{ids[0]['photoId']}?t={tok}", raw=True)
    ok &= check("and serves the image back", code == 200 and raw == png,
                f"({headers.get('Content-Type')})")
    ok &= check("as an image type read off the bytes, never off a label",
                headers.get("Content-Type") == "image/png")

    again = offsite.push_once(H.hub, force=True)
    ok &= check("a photo already there does not cross the wire twice",
                again.get("photosSent", 0) == 0)
    return ok


def test_restores_a_hub(M, tok, tmp):
    """The claim the whole thing rests on: the export rebuilds a lost hub."""
    ok = True
    code, exported, headers = M.req(f"/api/export?t={tok}")
    ok &= check("the mirror's export is a hub import file",
                code == 200 and exported.get("kind") == "frc-rebuilt-scouting-export")
    ok &= check("offered as a download, not rendered",
                "attachment" in (headers.get("Content-Disposition") or ""))

    fresh = HubStore(os.path.join(tmp, "restored.db"))
    fresh_hub = hub_mod.Hub(fresh)
    applied = 0
    for rec in exported.get("scout") or []:
        applied += 1 if fresh.upsert_scout(rec) else 0
    for rec in exported.get("pit") or []:
        applied += 1 if fresh.upsert_pit(rec) else 0
    ok &= check("every record lands in an empty database", applied > 100, f"({applied} rows)")

    replayed = 0
    for rec in exported.get("scout") or []:
        replayed += 1 if fresh.upsert_scout(rec) else 0
    ok &= check("importing the same file twice changes nothing", replayed == 0,
                f"({replayed} re-applied)")

    # A rebuilt hub has the scouting but not the official results, so it cannot
    # solve fuel. What it must have is every observation, in one piece.
    ok &= check("and the restored event holds the same scouting",
                len(fresh.scout_entries(EK)) == len(exported["scout"]),
                f"({len(fresh.scout_entries(EK))} entries)")
    del fresh_hub
    return ok


def test_history(M, tok):
    ok = True
    code, h, _ = M.req(f"/api/history?t={tok}")
    revs = h.get("revisions") or []
    ok &= check("the mirror keeps older copies, newest first",
                len(revs) >= 2 and revs[0]["revision"] > revs[-1]["revision"],
                f"({len(revs)} kept)")
    ok &= check("each with the counts that make it choosable",
                all("scoutEntries" in r and "receivedAt" in r for r in revs))
    code, old, _ = M.req(f"/api/export?t={tok}&rev={revs[-1]['revision']}")
    ok &= check("and an older one can still be downloaded",
                code == 200 and old.get("kind") == "frc-rebuilt-scouting-export")
    return ok


def test_url_normalising():
    ok = True
    cases = {
        "systemoverload.org": "https://systemoverload.org",
        "https://systemoverload.org/": "https://systemoverload.org",
        "https://systemoverload.org/api/push": "https://systemoverload.org",
        "http://192.168.1.9:8060/api": "http://192.168.1.9:8060",
        "": "",
    }
    for typed, want in cases.items():
        got = hub_mod._mirror_url(typed)
        ok &= check(f"'{typed or '(blank)'}' is saved as '{want or '(blank)'}'", got == want, got)
    return ok


def test_throttle(tmp):
    """Guessing a passcode on the open internet has to get slower."""
    M = LiveMirror(os.path.join(tmp, "throttle"), passcode="short")
    try:
        codes = [M.req("/api/unlock", {"pin": "no"})[0]
                 for _ in range(mirror_server.UNLOCK_FAILS + 2)]
        ok = check("repeated wrong passcodes are locked out, not just slowed",
                   codes[-1] == 429, f"({codes})")
        # The lockout must not brick the real code for the rest of the event
        # for everyone; it is per address, and this is the same address.
        ok &= check("and the right passcode waits it out with them",
                    M.req("/api/unlock", {"pin": "short"})[0] == 429)
        return ok
    finally:
        M.close()


def test_open_mode(tmp):
    M = LiveMirror(os.path.join(tmp, "open"), passcode=None, open_read=True)
    try:
        code, body, _ = M.req("/api/events")
        ok = check("--open serves reads with no passcode", code == 200)
        ok &= check("but writing still needs the push key",
                    M.req("/api/push", {"kind": "frc-rebuilt-scouting-mirror",
                                        "eventKey": EK})[0] == 401)
        return ok
    finally:
        M.close()


def main():
    tmp = tempfile.mkdtemp(prefix="frc-mirror-test-")
    for sub in ("throttle", "open"):
        os.makedirs(os.path.join(tmp, sub), exist_ok=True)
    ok = True
    M = LiveMirror(tmp)
    try:
        print("\nthe passcode")
        good, tok = test_locked(M)
        ok &= good

        print("\nseeding a hub with a full demo event")
        H = LiveHub(tmp, M.url)
        bundle = offsite.build_bundle(H.hub, EK)
        print(f"  {len(H.store.teams(EK))} teams, {len(H.store.matches(EK))} matches, "
              f"{len(H.store.scout_entries(EK))} scout entries")

        print("\nthe push key")
        ok &= test_push_auth(M, bundle)
        ok &= test_key_check(H, M)

        print("\nthe round trip")
        good, snap = test_round_trip(H, M, tok)
        ok &= good

        print("\nwhat gets sent, and what does not")
        ok &= test_skips_unchanged(H)
        ok &= test_photos(H, M, tok)

        print("\nrestoring from the mirror")
        ok &= test_restores_a_hub(M, tok, tmp)
        ok &= test_history(M, tok)

        print("\nthe address box on the setup page")
        ok &= test_url_normalising()

        print("\nthe public internet")
        ok &= test_throttle(tmp)
        ok &= test_open_mode(tmp)
    finally:
        M.close()
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
