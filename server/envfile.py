"""The `.env` file: the admin password and every API key live in it.

Stdlib only, like everything else here: the hub runs on a competition laptop
with a stock Python and no packages, so `python-dotenv` is not available to us
and would not be worth a dependency if it were.  This is a hundred lines of
parser, reader and writer, and nothing else.

Two rules that are worth stating because both are somebody's Saturday:

  * **A real environment variable always wins.**  A mirror host sets these
    through systemd, and a file sitting in the checkout must never quietly
    override what the machine was told.
  * **A malformed value is never read as "no password".**  Anything else and a
    typo in the file silently opens the admin panel, which is the exact
    opposite of what the person who typed it was doing.

The admin password is held base64-encoded, which is **not encryption** - anyone
who can read the file can decode it in one command, and this file says so out
loud so nobody has to find out the hard way.  What it does buy is that the
password is not sitting in plain sight in a file that gets opened on a
projector, or read over a shoulder at a scoring table, and that it survives
being copied around without being retyped.  Protection against somebody who has
the file is what the file permissions are for.
"""
import base64
import binascii
import os
import re

#: Where the hub looks: the repository root, one level up from `server/`.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, ".env")

#: The admin password, base64 of the UTF-8 password.  One name, so there is no
#: second place to look when it does not work.
ADMIN = "ADMIN_PASSWORD_B64"

#: What people type instead, out of habit, roughly half the time.  Never read
#: as a password - only used to say what to do about it.
ADMIN_PLAIN = "ADMIN_PASSWORD"

#: Every credential the hub holds, and the `.env` line it lives on.  Field name
#: on the admin panel -> variable name in the file.
#:
#: These used to be settings rows in the sqlite database, which meant a key was
#: in every snapshot under `data/snapshots/`, in any copy of the `.db` somebody
#: sent a mentor to look at, and gone the moment a laptop was replaced.  A file
#: is the better home: one thing to back up, one thing to copy to a spare
#: laptop, nothing to retype, and a key that never travels with the event data.
#:
#: `MIRROR_PUSH_KEY` is deliberately the same name the mirror itself reads (see
#: mirror/server.py).  Both sides of that push want the same string, and on a
#: laptop trying the mirror out, the two halves now agree by construction.
KEYS = {
    "tbaKey": "TBA_API_KEY",
    "nexusKey": "NEXUS_API_KEY",
    "nexusToken": "NEXUS_WEBHOOK_TOKEN",
    "frcEventsUser": "FRC_EVENTS_USER",
    "frcEventsToken": "FRC_EVENTS_TOKEN",
    "lovatKey": "LOVAT_API_KEY",
    "aiKey": "AI_API_KEY",
    "mirrorKey": "MIRROR_PUSH_KEY",
}


#: What the hub writes: `NEXUS_API_KEY_B64`, holding base64 of the key, the same
#: shape and the same suffix as the admin password above.  The plain name is
#: still read - see `key()` - because a hand-typed line and a systemd
#: `Environment=` are both real ways a key arrives.
B64 = "_B64"


def encoded_name(field):
    return KEYS[field] + B64


def key(field):
    """One credential, out of the environment.  `""` when it is not set.

    Two lines can hold it and both are read, encoded first: `NEXUS_API_KEY_B64`
    is what the admin panel writes, and plain `NEXUS_API_KEY` is what somebody
    typed into the file by hand or what a systemd unit sets on a mirror host.
    A plain one is not second-class - it works, and the hub re-writes it in the
    encoded form the next time it starts.

    A `_B64` line that will not decode returns nothing rather than garbage: a
    key made of mangled bytes reads to every vendor as a wrong key, and to
    everybody here as a wrong key that was typed correctly.  `problems()` is
    what says so out loud.

    Trimmed, because a key in a hand-edited file picks up a trailing space about
    as often as one pasted into a box, and the symptom is identical: a vendor
    that answers 401 all weekend for no visible reason.
    """
    raw = (os.environ.get(encoded_name(field)) or "").strip()
    if raw:
        return decode(raw) or ""
    return (os.environ.get(KEYS[field]) or "").strip()


def decode(raw):
    """base64 back to text, or None if it is not base64 of text."""
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8").strip() or None
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def problems():
    """`{field: what is wrong}` for every credential line that cannot be read.

    A key that is present and unreadable is the worst of the three states: the
    panel would say SET, every vendor would say no, and nothing anywhere would
    connect the two.  So it is named, on the panel and in the hub's window.
    """
    out = {}
    for field in KEYS:
        raw = (os.environ.get(encoded_name(field)) or "").strip()
        if raw and decode(raw) is None:
            out[field] = (f"{encoded_name(field)} in .env is not valid base64, so this key "
                          "cannot be read at all. Paste the key into the box again, or write it "
                          f"as a plain {KEYS[field]}= line and the hub will encode it.")
    return out


_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse(text):
    """`KEY=value` lines out of a `.env`, in order.

    Handles what people actually write: `export` in front, quotes around the
    value, comments, blank lines, and a `#` inside a quoted value (which is not
    a comment, and stripping it would corrupt exactly one password in fifty).
    """
    out = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        out[key] = value
    return out


def load(path=PATH):
    """Read `.env` into the environment and return what it set.

    Never overwrites a variable the machine already has: a mirror host sets
    these through systemd, and a checked-out file must not win over that.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            values = parse(fh.read())
    except OSError:
        return {}
    applied = {}
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def encode(password):
    """A password as the line to put in `.env`."""
    return base64.b64encode(password.encode("utf-8")).decode("ascii")


def admin_password():
    """`(password, problem)` - exactly one of them is set, and both can be None.

    `(None, None)` is the ordinary state of a hub that has not set one: the
    admin panel still opens locked, and unlocking it is a button.  A `problem`
    is a value that is there but unusable, and it locks the panel rather than
    opening it - see the module docstring.
    """
    raw = (os.environ.get(ADMIN) or "").strip()
    if not raw:
        if (os.environ.get(ADMIN_PLAIN) or "").strip():
            return None, (f"{ADMIN_PLAIN} is set, but this hub reads {ADMIN} - the password "
                          "base64-encoded. Run `python3 server/hub.py --set-admin-password` "
                          "to write the right line.")
        return None, None
    try:
        password = base64.b64decode(raw, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None, (f"{ADMIN} in .env is not valid base64, so nobody can unlock the admin "
                      "panel. Run `python3 server/hub.py --set-admin-password` to write it "
                      "properly.")
    if not password.strip():
        return None, f"{ADMIN} in .env decodes to nothing. Set a password or remove the line."
    return password, None


def write(name, value, path=PATH):
    """Set one variable in `.env`, keeping every other line exactly as it was.

    `None` removes the line altogether, which is what forgetting a key means -
    an empty `NEXUS_API_KEY=` would be an honest enough "no key", but it reads
    as a half-finished setup to the next person to open the file.
    """
    return write_many({name: value}, path)


def write_many(values, path=PATH):
    """Set several variables in one pass, keeping every other line as it was.

    Rewritten rather than appended to, because a second `ADMIN_PASSWORD_B64=`
    further down the file is a password that changes depending on which line
    the reader happens to win with.  One pass rather than one call per key
    because a save from the admin panel can carry eight of them, and eight
    rewrites of the same file is eight chances to be interrupted halfway.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    out, done = [], set()
    for line in lines:
        m = _LINE.match(line)
        name = m.group(1) if m else None
        if name in values:
            if name in done:
                continue                     # a duplicate of the one we just wrote
            done.add(name)
            if values[name] is not None:
                out.append(f"{name}={values[name]}")
            continue
        out.append(line)
    added = False
    for name, value in values.items():
        if name in done or value is None:
            continue
        if not added and out and out[-1].strip():
            out.append("")                   # one blank line before the new block, not eight
        added = True
        out.append(f"{name}={value}")
    # Leading blanks are what a removed line leaves behind, and they accumulate:
    # a file that starts with two empty lines reads as a file somebody gave up on.
    while out and not out[0].strip():
        out.pop(0)
    text = "\n".join(out).rstrip("\n") + "\n"
    # 0600 from the moment it exists: this file is the password.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.chmod(path, 0o600)                # an existing file keeps its old mode otherwise
    except OSError:
        pass                                 # Windows, where this means little anyway
    return path
