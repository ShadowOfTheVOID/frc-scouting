"""The `.env` file, and the admin password that lives in it.

Stdlib only, like everything else here: the hub runs on a competition laptop
with a stock Python and no packages, so `python-dotenv` is not available to us
and would not be worth a dependency if it were.  This is forty lines.

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


def write(key, value, path=PATH):
    """Set one key in `.env`, keeping every other line exactly as it was.

    Rewritten rather than appended to, because a second `ADMIN_PASSWORD_B64=`
    further down the file is a password that changes depending on which line
    the reader happens to win with.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        lines = []
    out, done = [], False
    for line in lines:
        m = _LINE.match(line)
        if m and m.group(1) == key:
            if done:
                continue                     # a duplicate of the one we just wrote
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(line)
    if not done:
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
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
