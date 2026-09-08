"""The API keys are encrypted in the database.  Run: python3 server/tests_vault.py

The claim this file exists to hold to is one sentence, and it is the one the
README now makes: **a copy of `data/scouting.db` is not a copy of your keys.**
Snapshots, an emailed database, a shared drive between events, the occasional
accidental commit - all of those used to hand over seven live credentials, and
`strings` was the whole attack.  So the headline test here is exactly that: the
bytes on disk are searched for every key that was saved, and finding one is a
failure.

The rest is the machinery around it, and each test is a way somebody actually
loses their keys: a `.env` that was replaced, a sealed value moved between rows
by hand, a database upgraded from a build that stored keys in the clear.

All stdlib, no network, no vendor keys - the values below are made up.
"""
import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import envfile  # noqa: E402
import hub  # noqa: E402
import vault  # noqa: E402
from store import Store  # noqa: E402

#: Distinctive on purpose: every one of these is grepped for in the raw
#: database file, so they must not look like anything else that could be in it.
FAKE = {
    "tbaKey": "T" * 64,
    "nexusKey": "nexus-BEEFCAFE-0001",
    "nexusToken": "webhook-BEEFCAFE-0002",
    "frcEventsToken": "frcevents-BEEFCAFE-0003",
    "lovatKey": "lvt-BEEFCAFE-0004",
    "aiKey": "sk-ant-BEEFCAFE-0005",
    "mirrorKey": "mirror-BEEFCAFE-0006",
}


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    return ok


class Sandbox:
    """A throwaway database and a throwaway `.env`.

    The `.env` matters as much as the database: the sealing key is written into
    it the first time a credential is saved, and a test that used the real one
    would both depend on the developer's own hub and quietly change it.
    """

    def __init__(self, key=True):
        self.dir = tempfile.mkdtemp(prefix="frc-vault-test-")
        self.db = os.path.join(self.dir, "test.db")
        self.env = os.path.join(self.dir, ".env")
        self._env_path = envfile.PATH
        envfile.PATH = self.env
        os.environ.pop(vault.KEY_VAR, None)
        vault.forget()
        if key:
            os.environ[vault.KEY_VAR] = base64.b64encode(os.urandom(32)).decode()

    def store(self):
        return Store(self.db)

    def raw(self):
        """Every byte the database occupies, write-ahead log included.

        The `-wal` file is half the point: a row overwritten in place leaves its
        old value there, and a test that only read the `.db` would pass on a
        database that still had the keys in it.
        """
        blob = b""
        for suffix in ("", "-wal", "-journal"):
            try:
                with open(self.db + suffix, "rb") as fh:
                    blob += fh.read()
            except OSError:
                pass
        return blob

    def close(self):
        envfile.PATH = self._env_path
        os.environ.pop(vault.KEY_VAR, None)
        vault.forget()
        shutil.rmtree(self.dir, ignore_errors=True)


# ------------------------------------------------------------------- tests

def test_nothing_readable_on_disk():
    """The headline: save every key, then grep the database for them."""
    box = Sandbox()
    ok = True
    try:
        st = box.store()
        for field, value in FAKE.items():
            st.set(field, value)
        st.set("eventKey", "2026test")          # not a secret, and must stay readable
        st.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        blob = box.raw()
        leaked = [f for f, v in FAKE.items() if v.encode() in blob]
        ok &= check("no saved key appears in the database file", not leaked, str(leaked))
        ok &= check("and the settings that are not secrets still do",
                    b"2026test" in blob)
        ok &= check("every key reads back exactly as it was typed",
                    all(st.get(f) == v for f, v in FAKE.items()))
        ok &= check("with nothing left unsealed",
                    all(vault.is_sealed(json.loads(row[0])) for row in st.conn().execute(
                        "SELECT value FROM kv WHERE key IN (%s)"
                        % ",".join("?" * len(FAKE)), tuple(FAKE))))
    finally:
        box.close()
    return ok


def test_same_key_twice_looks_different():
    box = Sandbox()
    ok = True
    try:
        first = vault.seal("tbaKey", FAKE["tbaKey"])
        second = vault.seal("tbaKey", FAKE["tbaKey"])
        ok &= check("a fresh nonce every save, so two identical keys do not match",
                    first != second)
        ok &= check("and both still open", vault.unseal("tbaKey", first)
                    == vault.unseal("tbaKey", second) == FAKE["tbaKey"])
    finally:
        box.close()
    return ok


def test_a_sealed_key_only_opens_in_its_own_box():
    """The row name is inside the tag, and this is why.

    Somebody who can edit the database could otherwise copy the sealed Lovat
    key into the `aiKey` row and have the hub send a team's Lovat credential to
    Anthropic on the next summary.
    """
    box = Sandbox()
    ok = True
    try:
        st = box.store()
        st.set("lovatKey", FAKE["lovatKey"])
        c = sqlite3.connect(box.db)
        sealed = c.execute("SELECT value FROM kv WHERE key='lovatKey'").fetchone()[0]
        c.execute("INSERT INTO kv(key,value,updated_at) VALUES('aiKey',?,0)", (sealed,))
        c.commit()
        c.close()
        moved = box.store()
        ok &= check("a sealed value moved to another row does not open",
                    moved.get("aiKey") is None)
        ok &= check("and the panel is told why", "aiKey" in moved.secret_problems)
        ok &= check("the row it belongs to is unaffected",
                    moved.get("lovatKey") == FAKE["lovatKey"])
    finally:
        box.close()
    return ok


def test_tampering_is_refused():
    box = Sandbox()
    ok = True
    try:
        sealed = vault.seal("tbaKey", FAKE["tbaKey"])
        nonce, ct, tag = sealed[len(vault.PREFIX):].split(":")
        flipped = bytearray(base64.b64decode(ct))
        flipped[0] ^= 0x01
        bad = (vault.PREFIX + nonce + ":"
               + base64.b64encode(bytes(flipped)).decode() + ":" + tag)
        try:
            vault.unseal("tbaKey", bad)
            ok &= check("one flipped byte is refused", False, "it opened")
        except vault.Unreadable:
            ok &= check("one flipped byte is refused", True)
        for junk in ("sealed:v1:not-base64:x:y", "sealed:v1:AAAA", vault.PREFIX):
            try:
                vault.unseal("tbaKey", junk)
                ok &= check("malformed sealed values are refused", False, junk)
                break
            except vault.Unreadable:
                pass
        else:
            ok &= check("malformed sealed values are refused", True)
    finally:
        box.close()
    return ok


def test_a_different_env_cannot_open_them():
    """Losing `.env` means re-pasting the keys, and the hub has to say so."""
    box = Sandbox()
    ok = True
    try:
        box.store().set("tbaKey", FAKE["tbaKey"])
        os.environ[vault.KEY_VAR] = base64.b64encode(os.urandom(32)).decode()
        vault.forget()
        st = box.store()
        ok &= check("a key sealed with another .env does not open", st.get("tbaKey") is None)
        ok &= check("and it reads as an empty box, not as an exception",
                    st.get("tbaKey", "") == "")
        ok &= check("with a reason a lead can act on",
                    "paste" in st.secret_problems.get("tbaKey", "").lower())

        os.environ.pop(vault.KEY_VAR, None)
        vault.forget()
        gone = box.store()
        ok &= check("no key at all is the same answer", gone.get("tbaKey") is None
                    and vault.KEY_VAR in gone.secret_problems.get("tbaKey", ""))
    finally:
        box.close()
    return ok


def test_an_old_database_is_sealed_on_startup():
    """Every hub built before this existed has seven rows of plain text in it."""
    box = Sandbox()
    ok = True
    try:
        legacy = box.store()
        # Written the way the old build wrote them: straight into the row.
        for field, value in FAKE.items():
            legacy.conn().execute(
                "INSERT INTO kv(key,value,updated_at) VALUES(?,?,0) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value", (field, json.dumps(value)))
        legacy.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = box.raw()
        ok &= check("the fixture really is in the clear to begin with",
                    all(v.encode() in before for v in FAKE.values()))

        st = box.store()
        hub.Hub(st)                              # what starting the hub does
        after = box.raw()
        left = [f for f, v in FAKE.items() if v.encode() in after]
        ok &= check("starting the hub seals them", not left, str(left))
        ok &= check("and they still read back", all(st.get(f) == v for f, v in FAKE.items()))
        ok &= check("a second start has nothing left to do", st.seal_secrets() == [])
    finally:
        box.close()
    return ok


def test_forget_still_works():
    box = Sandbox()
    ok = True
    try:
        st = box.store()
        st.set("aiKey", FAKE["aiKey"])
        st.set("aiKey", "")                      # the FORGET button
        ok &= check("a forgotten key is gone", st.get("aiKey") == "")
        ok &= check("and reads as absent, without needing the sealing key",
                    not bool(st.get("aiKey")))

        # A box that could not be opened is red until something replaces it,
        # and FORGET is something replacing it - otherwise the line stays under
        # an empty box for the rest of the event.
        st.set("tbaKey", FAKE["tbaKey"])
        os.environ[vault.KEY_VAR] = base64.b64encode(os.urandom(32)).decode()
        vault.forget()
        st = box.store()
        st.get("tbaKey")
        ok &= check("an unopenable key is reported", "tbaKey" in st.secret_problems)
        st.set("tbaKey", "")
        st.get("tbaKey")
        ok &= check("and forgetting it takes the report away",
                    "tbaKey" not in st.secret_problems)
    finally:
        box.close()
    return ok


def test_the_sealing_key_is_made_only_when_it_is_needed():
    box = Sandbox(key=False)
    ok = True
    try:
        st = box.store()
        st.set("eventKey", "2026test")
        st.set("ourTeam", "6059")
        ok &= check("a hub with no credentials never grows a .env",
                    not os.path.exists(box.env))

        st.set("tbaKey", FAKE["tbaKey"])
        ok &= check("the first key saved writes one", os.path.exists(box.env))
        written = envfile.parse(open(box.env).read())
        ok &= check("holding a 32-byte key", vault.KEY_VAR in written
                    and len(base64.b64decode(written[vault.KEY_VAR])) == vault.KEY_BYTES)
        if os.name != "nt":
            ok &= check("readable only by the person running the hub",
                        (os.stat(box.env).st_mode & 0o077) == 0)
        ok &= check("the admin password is untouched by it",
                    envfile.ADMIN not in written)

        # A fresh process: the key comes back off disk rather than out of memory.
        os.environ.pop(vault.KEY_VAR, None)
        vault.forget()
        ok &= check("and a restart reads it back",
                    box.store().get("tbaKey") == FAKE["tbaKey"])
    finally:
        box.close()
    return ok


def test_nowhere_to_write_the_key_refuses_the_save():
    """The one failure that must not look like a success.

    A key sealed with a sealing key that was never written down is a key nobody
    gets back, and it would report `SAVED` right up until the next restart.
    """
    box = Sandbox(key=False)
    ok = True
    try:
        envfile.PATH = os.path.join(box.dir, "no-such-folder", ".env")
        st = box.store()
        try:
            st.set("tbaKey", FAKE["tbaKey"])
            ok &= check("a save that cannot store the sealing key is refused", False,
                        "it saved anyway")
        except vault.VaultError as e:
            ok &= check("a save that cannot store the sealing key is refused",
                        vault.KEY_VAR in str(e))
        ok &= check("and nothing went into the database",
                    st.conn().execute("SELECT COUNT(*) c FROM kv WHERE key='tbaKey'"
                                      ).fetchone()["c"] == 0)
    finally:
        box.close()
    return ok


def main():
    passed = True
    for fn in (test_nothing_readable_on_disk,
               test_same_key_twice_looks_different,
               test_a_sealed_key_only_opens_in_its_own_box,
               test_tampering_is_refused,
               test_a_different_env_cannot_open_them,
               test_an_old_database_is_sealed_on_startup,
               test_forget_still_works,
               test_the_sealing_key_is_made_only_when_it_is_needed,
               test_nowhere_to_write_the_key_refuses_the_save):
        print(f"\n{fn.__name__.replace('test_', '').replace('_', ' ')}")
        passed &= fn()
    print()
    print("ALL PASS" if passed else "FAILURES ABOVE")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
