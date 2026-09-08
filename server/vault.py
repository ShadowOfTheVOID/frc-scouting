"""The API keys, encrypted in the database rather than sitting in it.

Every credential a team pastes into the Setup page - The Blue Alliance, Nexus
and its webhook token, FRC Events, Lovat, the AI vendor, the mirror push key -
used to be written into the `kv` table verbatim.  `strings data/scouting.db`
printed all seven.  That database is the one thing in this project that
*travels*: `data/snapshots/` fills up with copies of it, a lead emails it to
themselves so the numbers survive the laptop, it gets dropped on a shared drive
between events, and every so often somebody unpicks `.gitignore` and commits
it.  Any one of those hands over keys that bill a real credit card and, for
Lovat and Nexus, an account belonging to somebody else's team.

So the keys are sealed on the way into that table and opened on the way out.
What that buys, exactly:

  * A copy of the database on its own is no longer a copy of the keys.  A
    snapshot, an emailed `.db`, a stolen backup, an accidental commit: all of
    them are now ciphertext.
  * It buys **nothing** against somebody sitting at the unlocked hub laptop,
    and it is not meant to.  The hub has to reach TBA at 8am with nobody
    standing over it, so the hub must be able to open these by itself, so the
    key to open them is on the same machine.  Anyone with the whole machine has
    both halves.  That is the honest boundary and it is the same one every
    unattended server has.

The sealing key lives in `.env` as `HUB_SECRET_KEY`, beside the admin password
and under the same 0600, generated the first time a credential is saved.  That
is the one file this project already treats as secret, already keeps out of
git, and already tells people not to copy around - so it is the one file that
has to stay behind when the database goes somewhere.  A real environment
variable wins over the file, exactly as it does for everything else in
`envfile`, so a host that manages secrets through systemd can hand the key over
that way instead.

**Losing `.env` means re-pasting the keys.**  There is no recovery and there is
not meant to be one - a database that could be opened without the key file
would be a database that still carries its keys.  A value that cannot be opened
reads as an empty box on the Setup page, which is the state the page already
knows how to explain, rather than as an error nobody can act on.

What is *not* sealed, and why: the FRC Events username (a login name, not a
secret, and it is shown on the page), the strategy passcode and admin password
(never stored as typed at all - a salted hash and a `.env` line respectively),
and the unlock tokens (minted by this hub, expiring in hours, worth nothing
away from it).  Sealing those would cost the same and buy the appearance of
more than it gives.

The construction, since it is not a library call
================================================

Stdlib only, like the rest of the server: the hub runs on a competition laptop
with a stock Python, CI installs nothing, and `cryptography` is not there to be
imported.  Python's standard library ships no cipher, so the keystream is built
out of the one primitive it does ship - HMAC-SHA256, in counter mode, which is
the KDF construction in NIST SP 800-108 used as a stream cipher.  Every
primitive here comes from `hashlib`/`hmac`, so nothing security-critical is
hand-rolled arithmetic; the only code below is XOR and framing.

Per value:

    nonce  16 random bytes, fresh for every save
    Ke     HMAC-SHA256(master, "…/enc" + nonce)
    Km     HMAC-SHA256(master, "…/mac" + nonce)
    stream HMAC-SHA256(Ke, counter||nonce) for counter = 0, 1, 2, …
    ct     plaintext XOR stream
    tag    HMAC-SHA256(Km, name + nonce + ct)

Encrypt-then-MAC, so a value is authenticated before a byte of it is used, and
the *name of the settings row is inside the tag*.  That last part matters more
than it looks: without it, anybody who could edit the database could copy the
sealed Lovat key into the `aiKey` row and the hub would happily send a team's
Lovat credential to Anthropic.  With it, a sealed value only opens in the box
it was sealed for.

A fresh nonce per save means two boxes holding the same key do not look alike,
and re-saving a key does not reveal that it did not change.
"""
import base64
import binascii
import hashlib
import hmac
import os
import secrets
import threading

import envfile
import keys as keyhygiene

#: Where the sealing key lives.  One name, in the file people already know
#: holds the secrets, so there is no second place to look.
KEY_VAR = "HUB_SECRET_KEY"

#: 32 bytes, which is what SHA-256 gives back anyway.
KEY_BYTES = 32

NONCE_BYTES = 16

#: Version-tagged so a later format can be told apart from this one without
#: guessing, and distinctive enough that no vendor's key could be mistaken for
#: a sealed value.  `keys.check()` would refuse a paste shaped like this long
#: before it reached the database.
PREFIX = "sealed:v1:"

_ENC = b"frc-rebuilt-hub/secret/v1/enc"
_MAC = b"frc-rebuilt-hub/secret/v1/mac"

#: Which settings rows get sealed: every box on the Setup page that holds a
#: credential a vendor issued.  Derived from `keys.FIELDS` rather than listed
#: again, so a ninth service added there is sealed without anybody remembering
#: to come back here - `secret` is already how that file says "this is a key
#: and not a name", and `code` marks the passcodes, which are hashed instead
#: and never stored as typed.
SECRETS = frozenset(name for name, f in keyhygiene.FIELDS.items()
                    if f.secret and not f.code)

_lock = threading.Lock()
_cached = None


class VaultError(Exception):
    """Something is wrong with the sealing key itself.

    Raised on the way *in* - there is nowhere to write the key, or what is in
    `.env` is not a key at all. It has to stop the save rather than be swallowed:
    a credential that goes into the database sealed with a key that was never
    written down is a credential nobody will ever get back, and it would look
    like a successful save right up until the next restart.
    """


class Unreadable(VaultError):
    """A sealed value that this hub cannot open.

    Almost always one thing: `.env` was replaced, or the database was carried
    to a machine that has a different one - which is the whole design working,
    seen from the wrong side.  Callers turn this into an empty box rather than
    an error, because "paste the key again" is the only thing anybody can do
    about it and an empty box says exactly that.
    """


def _key_from_env():
    raw = (os.environ.get(KEY_VAR) or "").strip()
    if not raw:
        # main() loads `.env` before anything else, but a Store built by a
        # test, by seed_demo.py or by a script has not been through that.
        # `envfile.load` never overwrites a variable the machine already set,
        # so calling it twice is a no-op rather than a surprise. The path is
        # passed rather than left to the default so that both of these follow
        # `envfile.PATH` if something moves it.
        envfile.load(envfile.PATH)
        raw = (os.environ.get(KEY_VAR) or "").strip()
    if not raw:
        return None
    try:
        material = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise Unreadable(
            "%s in .env is not valid base64, so the saved API keys cannot be opened. "
            "Restore the .env this database was written with, or clear that line and "
            "paste the keys again." % KEY_VAR)
    if len(material) < 16:
        raise Unreadable(
            "%s in .env is too short to be the key this database was written with. "
            "Restore that file, or clear the line and paste the keys again." % KEY_VAR)
    return material


def key(create=False):
    """The master key, or None when this hub has never sealed anything.

    `create=True` generates one and writes it to `.env` - which is what the
    first credential a team saves does.  A hub that is never given a key never
    grows a `.env`, so seeding a demo event and running it offline still needs
    no files it did not ask for.
    """
    global _cached
    with _lock:
        if _cached is None:
            _cached = _key_from_env()
        if _cached is None and create:
            material = secrets.token_bytes(KEY_BYTES)
            encoded = base64.b64encode(material).decode("ascii")
            # 0600, and the same file the admin password is in: see
            # envfile.write. Written before it is used, and the save is
            # abandoned if it cannot be - a read-only checkout would otherwise
            # seal every key with a key that exists only until the process
            # ends, which is a database of credentials nobody can ever open.
            try:
                envfile.write(KEY_VAR, encoded, envfile.PATH)
            except OSError as e:
                raise VaultError(
                    "the API keys are encrypted with %s in %s, and that file could not be "
                    "written (%s). Nothing was saved. Make the folder writable, or set %s "
                    "in the environment yourself." % (KEY_VAR, envfile.PATH, e, KEY_VAR))
            os.environ[KEY_VAR] = encoded
            _cached = material
        return _cached


def forget():
    """Drop the cached key.  For tests, and for `--set-secret-key`."""
    global _cached
    with _lock:
        _cached = None


def _stream(subkey, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(subkey, counter.to_bytes(4, "big") + nonce, hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _subkeys(master, nonce):
    return (hmac.new(master, _ENC + nonce, hashlib.sha256).digest(),
            hmac.new(master, _MAC + nonce, hashlib.sha256).digest())


def _tag(mac_key, name, nonce, ct):
    # The row name is length-prefixed rather than merely concatenated, so
    # `aiKey` + some ciphertext can never hash the same as `aiKe` + a
    # ciphertext that happens to start with a `y`.
    label = name.encode("utf-8")
    body = len(label).to_bytes(2, "big") + label + nonce + ct
    return hmac.new(mac_key, body, hashlib.sha256).digest()


def is_sealed(value):
    """Whether this stored value came out of `seal`.

    Only ever asked of a value already read back from the database.  A
    plaintext key from a hub written before this file existed answers False and
    is migrated; see `store.Store.seal_secrets`.
    """
    return isinstance(value, str) and value.startswith(PREFIX)


def seal(name, value, master=None):
    """One credential, sealed for the settings row called `name`."""
    if not isinstance(value, str):
        raise TypeError("only text is sealed")
    master = master or key(create=True)
    nonce = secrets.token_bytes(NONCE_BYTES)
    enc_key, mac_key = _subkeys(master, nonce)
    pt = value.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(pt, _stream(enc_key, nonce, len(pt))))
    tag = _tag(mac_key, name, nonce, ct)
    return PREFIX + ":".join(base64.b64encode(part).decode("ascii")
                             for part in (nonce, ct, tag))


def unseal(name, blob, master=None):
    """The credential back, or `Unreadable`.

    Authenticated before it is decrypted, and authenticated against the name of
    the row it was found in - a sealed value moved between boxes by hand does
    not open.
    """
    if not is_sealed(blob):
        raise Unreadable("that is not a sealed value")
    master = master or key()
    if master is None:
        raise Unreadable(
            "this database holds sealed API keys but %s is not set, so they cannot be "
            "opened. Restore the .env this database was written with, or paste the keys "
            "again." % KEY_VAR)
    parts = blob[len(PREFIX):].split(":")
    if len(parts) != 3:
        raise Unreadable("a sealed value in the database is malformed")
    try:
        nonce, ct, tag = (base64.b64decode(p, validate=True) for p in parts)
    except (binascii.Error, ValueError):
        raise Unreadable("a sealed value in the database is malformed")
    if len(nonce) != NONCE_BYTES:
        raise Unreadable("a sealed value in the database is malformed")
    enc_key, mac_key = _subkeys(master, nonce)
    if not hmac.compare_digest(tag, _tag(mac_key, name, nonce, ct)):
        raise Unreadable(
            "the saved %s does not match this hub's %s - it was sealed with a different "
            "key, or it has been altered. Paste that key again." % (name, KEY_VAR))
    return bytes(a ^ b for a, b in zip(ct, _stream(enc_key, nonce, len(ct)))).decode("utf-8")
