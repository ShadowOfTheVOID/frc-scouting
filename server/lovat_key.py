#!/usr/bin/env python3
"""Mint a Lovat API key without typing a curl command.

Run:  python3 server/lovat_key.py

Lovat's key endpoints exist on their server and nothing in their own apps ever
calls them, so getting a key has meant reading a credential out of the browser's
Network tab and then hand-editing a copied `curl` into a different one.  Every
step of that has a trap, and the traps are the reason this file exists:

  * `curl` in Windows PowerShell is an alias for `Invoke-WebRequest`, which
    does not understand `-X` or `-H` and fails with a parameter error that
    looks like a problem with the command;
  * the copied command needs exactly two edits - the path and the method - and
    changing one of the two gets you your profile back, or a 404;
  * line continuations are `\\` on a Mac and `^` in Command Prompt;
  * the value wanted is what comes after `Bearer `, and copying the whole
    header line instead is the usual cause of a 401 on a token that works;
  * the response is the one and only place the key will ever exist, because
    only a hash of it is stored, so losing the terminal loses the key.

None of that is about scouting.  Paste what the browser gave you, and this
does the rest: finds the token inside whatever you pasted, says how long it has
left before asking Lovat anything, mints the key, writes it into `.env` the way
the admin panel would, and translates each way it can fail into the thing to go
and do about it.

The one step no script can take for you is getting the token in the first
place; that needs a signed-in browser.  `--help` prints where to click.

  python3 server/lovat_key.py            mint a key and save it
  python3 server/lovat_key.py --list     what keys this team already has
  python3 server/lovat_key.py --revoke   remove one by its uuid
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import envfile  # noqa: E402

BASE = "https://api.lovat.app/v1/manager"
UA = "FRCScoutingHub/1.0 (+lovat_key.py)"

#: A JSON Web Token, which is what Lovat's browser credential is.  Always three
#: base64url segments, and the first always starts `eyJ` because that is base64
#: of `{"`.  Matching on the token itself rather than on the shape of the
#: command around it is what lets one expression read every way a browser
#: offers to copy a request - `Copy as cURL` on a Mac, the `^`-continued
#: version Command Prompt gets, `Copy as fetch`, or the header line on its own.
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]+")

#: A minted key.  Here only so that pasting one in place of the browser token -
#: an easy mistake, they arrive minutes apart - is answered with the reason
#: rather than with Lovat's 403.
LVT = re.compile(r"\blvt-[A-Za-z0-9_\-.]+")


#: How to read the clipboard using only what the operating system already has.
#: PowerShell ships with every supported Windows; `pbpaste` is part of macOS;
#: on Linux it is whichever of the three the desktop happens to have. Nothing
#: to install, which is the rule for everything in this repository.
#:
#: This matters most on Windows, where the alternative is pasting several
#: kilobytes into a console window that has its own opinions about how long a
#: line may be. Copying in the browser and running this is shorter anyway.
CLIPBOARD = {
    "win32": [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]],
    "darwin": [["pbpaste"]],
}
CLIPBOARD_LINUX = [["wl-paste", "--no-newline"],
                   ["xclip", "-selection", "clipboard", "-o"],
                   ["xsel", "--clipboard", "--output"]]


def clipboard():
    """Whatever is on the clipboard, or `""` if we cannot read it.

    Never raises and never explains itself: this is an opportunistic first
    look, and every way it can fail - no such command, no desktop session, a
    five-second timeout - ends in the same place, which is asking for a paste
    instead.
    """
    tries = CLIPBOARD.get(sys.platform) or CLIPBOARD_LINUX
    # Without this a console window flashes up on Windows for each attempt.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    for cmd in tries:
        try:
            done = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  timeout=5, creationflags=flags)
        except Exception:
            continue
        if done.returncode == 0 and done.stdout:
            return done.stdout.decode("utf-8", "replace")
    return ""


def extract_token(text):
    """The browser credential out of whatever was pasted.

    Returns `(token, problem)`; exactly one of the two is set.  Anything
    recognisable enough to name is named, because "nothing usable in that" is
    the least helpful thing this could say to somebody who has just spent ten
    minutes in a Network tab.
    """
    if not isinstance(text, str) or not text.strip():
        return None, "there was nothing in that."

    # A blob can carry more than one token - a cookie header alongside the
    # authorization one. The one after the word `authorization` is the one
    # asked for; without that word, the longest is the best guess available.
    lowered = text.lower()
    at = lowered.find("authorization")
    found = None
    if at != -1:
        found = JWT.search(text, at)
    if found is None:
        hits = JWT.findall(text)
        found = max(hits, key=len) if hits else None
    else:
        found = found.group(0)

    if found:
        return found, None

    if LVT.search(text):
        return None, ("that is an `lvt-` key, not the browser token. A key cannot "
                      "mint another key - that is the whole reason this needs a "
                      "signed-in browser. Go back to the Network tab and copy the "
                      "`profile` request.")
    if "http" in lowered and "lovat" in lowered:
        return None, ("that looks like the request but with no credential in it. "
                      "The `preflight` row carries no token - you want the one "
                      "below it whose Type is `fetch`.")
    return None, ("no sign-in token in that. It is a long string starting `eyJ`, "
                  "and it is in the `authorization` header of the `profile` request.")


def _b64url(seg):
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def token_expiry(token):
    """The `exp` claim in seconds, or None if it cannot be read.

    Read, not verified - there is no signature check here and there is no point
    in one, because only Lovat's server can do that. It is read for one reason:
    the token lasts 72 hours and a stale one comes back 401, which is
    indistinguishable from a wrong one. Knowing before the request is the
    difference between "go and copy a fresh one" and half an hour of doubt.
    """
    try:
        payload = json.loads(_b64url(token.split(".")[1]).decode("utf-8"))
        exp = payload.get("exp")
        return float(exp) if isinstance(exp, (int, float)) else None
    except Exception:
        return None


def _left(secs):
    """How long a token has, in the units somebody would say it in.

    Hours all the way up rather than days: these last 72 of them, and "3 days
    left" on a token with 73 hours in it is the wrong side of a competition
    weekend.
    """
    if secs < 0:
        return "expired %s ago" % _left(-secs).replace(" left", "")
    if secs < 3600:
        return "%d minutes left" % (secs // 60)
    return "%d hours left" % (secs // 3600)


def request(method, path, token, params=None, timeout=20):
    """One call to Lovat.  `(status, parsed-or-text)`; never raises.

    The error body is kept, unlike `sources._request`, because Lovat says which
    of the two 403s it is in that body and that is the whole diagnosis.
    """
    q = ("?" + urllib.parse.urlencode(params)) if params else ""
    req = urllib.request.Request(
        BASE + path + q,
        data=b"" if method == "POST" else None,
        method=method,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", "replace")
            status = res.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace") if e.fp else ""
        status = e.code
    except Exception as e:
        return 0, "could not reach Lovat at all (%s). Check this laptop is online - " \
                  "venue wifi often blocks it." % type(e).__name__
    try:
        return status, json.loads(raw) if raw.strip() else None
    except ValueError:
        return status, raw.strip()


def explain(status, body, what="mint a key"):
    """One HTTP status, in the words of the thing to go and do about it.

    Every line here is a failure somebody has actually hit. The two 403s are
    the pair worth separating: one is a person at Lovat who has not approved
    your team yet and no amount of retrying changes it, and the other is having
    pasted the key where the token goes.
    """
    said = ""
    if isinstance(body, dict):
        said = str(body.get("message") or body.get("error") or "")
    elif isinstance(body, str):
        said = body
    low = said.lower()

    if status == 0:
        return said
    if status == 401 and "no team" in low:
        return ("Lovat says this account is not on a team. Join your team on "
                "dashboard.lovat.app first - the key belongs to the team, not to you.")
    if status == 401:
        return ("Lovat rejected the sign-in token. It lasts 72 hours, so the usual "
                "cause is a stale copy - open the Network tab again and take a fresh "
                "one. If you copied the whole `authorization: Bearer eyJ...` line, that "
                "is fine here; this script takes the line apart for you.")
    if status == 403 and "api key" in low:
        return ("That token is an API key, not a browser credential - a key cannot "
                "mint another key. Copy the `profile` request out of a signed-in "
                "browser instead.")
    if status == 403:
        return ("Lovat has not verified your team yet, which is their check on you "
                "and not a problem with the token. Sign in at dashboard.lovat.app, "
                "go to Settings -> Team email -> Change, and click the link in the "
                "mail within twenty minutes. That link going quietly stale is the "
                "usual reason this step never finishes." +
                (" Lovat said: " + said if said else ""))
    if status == 404:
        return "Lovat has no such endpoint. This script may be out of date with their API."
    if status == 429:
        return "Lovat is rate-limiting us. Wait a few seconds and run this again."
    if 500 <= status < 600:
        return "Lovat's server answered %d, which is their end. Try again shortly." % status
    return "Lovat answered HTTP %d and we could not %s.%s" % (
        status, what, (" It said: " + said) if said else "")


def read_paste(stream=None, out=None):
    """Everything pasted, up to a blank line.

    Off stdin rather than off the command line on purpose: a token handed over
    as an argument is in the shell's history file and in the process list, and
    this one is a password for 72 hours.
    """
    stream = stream or sys.stdin
    out = out or sys.stdout
    lines = []
    while True:
        try:
            line = stream.readline()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            break
        if not line.strip():
            if lines:
                break
            continue          # a stray Enter before the paste is not the end of it
        lines.append(line)
    return "".join(lines)


HOW = """
  Getting the token - the one step a script cannot do for you.

    1. Sign in at dashboard.lovat.app, in Chrome or Edge.
    2. Press F12 (Mac: option-command-I) and open the Network tab.
    3. Tick "Preserve log", click "Fetch/XHR", type  api.lovat.app  in the filter.
    4. Reload, and WAIT for the page to finish drawing - it is a Flutter app and
       the API calls come seconds after the first files.
    5. Click the row named `profile` whose Type is `fetch` - not the `preflight`
       one below it, which carries no credential.
    6. Right-click it -> Copy -> Copy as cURL.

  Then run this again - copying is all it needs, because it reads the
  clipboard itself. If that did not work, paste here instead: the whole
  command is fine, and so is just the `authorization:` line or the token
  alone. Press Enter twice when you are done.

  What you are pasting is a password for your Lovat account, good for 72 hours.
  It is not written anywhere by this script and is never printed back.
"""


def _check(text, out, where):
    """One candidate, wherever it came from.  `(token, None)` or `(None, why not)`.

    The expiry is read here rather than at each call site, because the answer
    to a stale token is the same whether it was pasted, read off the clipboard
    or taken out of a file: go and copy a fresh one.
    """
    token, problem = extract_token(text)
    if problem:
        return None, problem
    out.write("\n  " + where)
    exp = token_expiry(token)
    if exp is not None:
        left = exp - time.time()
        if left <= 0:
            out.write(".\n")
            return None, ("that token %s. Nothing else is wrong with it - they last "
                          "72 hours. Reload the dashboard and copy the `profile` "
                          "request again." % _left(left))
        out.write(", %s" % _left(left))
    out.write(".\n")
    return token, None


def prompt_for_token(out=None, stream=None, from_file=None, use_clipboard=True):
    """The sign-in token, from the easiest place it can be found.

    The clipboard first, because the browser step ends with a copy and asking
    somebody to paste several kilobytes into a console is the worst part of
    this on Windows. Silent when there is nothing usable there - an empty
    clipboard, no desktop session, a machine with none of the three Linux
    tools - and it falls through to asking, which always works.
    """
    out = out or sys.stdout
    if from_file:
        try:
            with open(from_file, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as e:
            return None, "could not read %s (%s)." % (from_file, e.strerror or e)
        return _check(text, out, "token read from %s" % from_file)

    if use_clipboard:
        text = clipboard()
        if text and extract_token(text)[0]:
            return _check(text, out, "found a sign-in token on your clipboard")

    out.write(HOW + "\n  paste here: ")
    out.flush()
    return _check(read_paste(stream, out), out, "token read")


def mint(token, name, req=request):
    """`(key, None)` or `(None, why not)`.

    `/profile` first, and not only to be friendly: it checks that you are
    signed in and nothing else, while `/apikey` also checks that your team has
    been verified. Asking both is what lets a 403 be reported as the team check
    rather than as a bad token, which is the single most confusing failure here.
    """
    status, body = req("GET", "/profile", token)
    if status != 200:
        return None, explain(status, body, "read your profile")
    who = ""
    if isinstance(body, dict):
        team = body.get("teamNumber") or body.get("team") or ""
        who = str(body.get("teamName") or body.get("name") or "").strip()
        who = ("signed in as %s %s" % (team, who)).strip() if (team or who) else ""

    status, body = req("POST", "/apikey", token, {"name": name})
    if status not in (200, 201):
        return None, explain(status, body)
    key = body.get("apiKey") if isinstance(body, dict) else None
    if not key:
        return None, ("Lovat accepted the request but sent no key back, which this "
                      "script does not know how to read: %r" % (body,))
    return key, who


def save(key, path=None):
    """Into `.env`, base64-encoded, the same shape and place the panel writes."""
    name = envfile.encoded_name("lovatKey")
    plain = envfile.KEYS["lovatKey"]
    written = envfile.write_many(
        {name: envfile.encode(key), plain: None}, path or envfile.PATH)
    # Both halves, the way `Hub.save_keys` does it: one key, one home. A hub
    # serving an event reads `os.environ`, so a key that only takes effect
    # after a restart is a key that did not work when it was pasted.
    os.environ[name] = envfile.encode(key)
    os.environ.pop(plain, None)
    return written


def _mint_command(args, out):
    token, problem = prompt_for_token(
        out, from_file=args.from_file, use_clipboard=not args.paste)
    if problem:
        out.write("\n  %s\n\n" % problem)
        return 1
    out.write("  asking Lovat...\n")
    key, why = mint(token, args.name)
    if not key:
        out.write("\n  %s\n\n" % why)
        return 1
    if why:
        out.write("  %s\n" % why)
    if args.no_save:
        out.write("\n  not saved, as asked. The key is:\n\n    %s\n\n"
                  "  This is the only time it exists - Lovat keeps a hash and "
                  "nothing else.\n\n" % key)
        return 0
    path = save(key)
    out.write("\n  minted, and written to %s\n" % path)
    out.write("  base64-encoded, which is not encryption: anyone who can read that\n"
              "  file can decode it. Keep it off shared drives.\n")
    if args.show:
        out.write("\n    %s\n" % key)
    out.write("\n  Next: restart the hub so it picks the key up, set the event key at\n"
              "  http://localhost:6059/ , and press TEST KEYS.\n\n")
    return 0


def _list_command(args, out):
    token, problem = prompt_for_token(
        out, from_file=args.from_file, use_clipboard=not args.paste)
    if problem:
        out.write("\n  %s\n\n" % problem)
        return 1
    status, body = request("GET", "/apikey", token)
    if status != 200:
        out.write("\n  %s\n\n" % explain(status, body, "list your keys"))
        return 1
    rows = body if isinstance(body, list) else (body or {}).get("apiKeys") or []
    if not rows:
        out.write("\n  no keys on this team yet.\n\n")
        return 0
    out.write("\n  %d key(s). The keys themselves are not here and never will be -\n"
              "  Lovat stores a hash, so this is a name and a date by design.\n\n" % len(rows))
    for r in rows:
        r = r if isinstance(r, dict) else {}
        out.write("    %-38s %s  %s\n" % (r.get("uuid") or r.get("id") or "?",
                                          r.get("createdAt") or r.get("created") or "",
                                          r.get("name") or ""))
    out.write("\n  Remove one:  python3 server/lovat_key.py --revoke <uuid>\n\n")
    return 0


def _revoke_command(args, out):
    token, problem = prompt_for_token(
        out, from_file=args.from_file, use_clipboard=not args.paste)
    if problem:
        out.write("\n  %s\n\n" % problem)
        return 1
    status, body = request("DELETE", "/apikey", token, {"uuid": args.revoke})
    if status not in (200, 204):
        out.write("\n  %s\n\n" % explain(status, body, "revoke that key"))
        return 1
    out.write("\n  revoked. Any hub still holding it will start getting 401s.\n\n")
    return 0


def main(argv=None, out=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(
        prog="lovat_key.py",
        description="Mint a Lovat API key from a signed-in browser token.",
        epilog="The token comes out of the browser's Network tab; run with no "
               "arguments and the steps are printed.")
    ap.add_argument("--name", default="scouting hub",
                    help="what the key is called in Lovat's own list (default: %(default)s)")
    ap.add_argument("--list", action="store_true", help="list this team's keys")
    ap.add_argument("--revoke", metavar="UUID", help="revoke one key by uuid")
    ap.add_argument("--show", action="store_true",
                    help="also print the key after saving it (default: saved, not shown)")
    ap.add_argument("--no-save", action="store_true",
                    help="print the key instead of writing it into .env")
    ap.add_argument("--paste", action="store_true",
                    help="ask for a paste instead of reading the clipboard")
    ap.add_argument("--from-file", metavar="PATH",
                    help="read the browser token out of a file, for when neither the "
                         "clipboard nor a paste will do")
    args = ap.parse_args(argv)
    try:
        if args.revoke:
            return _revoke_command(args, out)
        if args.list:
            return _list_command(args, out)
        return _mint_command(args, out)
    except KeyboardInterrupt:
        out.write("\n  stopped. Nothing was changed.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
