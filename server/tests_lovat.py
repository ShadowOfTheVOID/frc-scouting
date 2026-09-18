#!/usr/bin/env python3
"""Regression gate for the Lovat import.  Run: python3 server/tests_lovat.py

Lovat's export is somebody else's file format and it will drift.  These cover
the ways a drift would be silent rather than loud: a match label that does not
map onto our schedule, a blank column read as a zero, free-text notes losing
the scout who wrote them, the byte-order mark that once nulled every match key,
and the three columns Lovat has written both as booleans and as enums.  No
network - the fixture is on disk.

The fixture is deliberately awkward, and every part of that is load-bearing: it
leads with a BOM the way the real export does, it spells one climb `L2` and
another `Level3`, it carries a label we cannot read at all (`Snorkel`), a
playoff row that must not join onto a qual match, and columns nobody filled.
"""
import io
import os
import shutil
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import lovat  # noqa: E402
import lovat_key  # noqa: E402
import envfile  # noqa: E402

EK = "2026test"
FIXTURE = os.path.join(_HERE, "fixtures", "lovat_report_example.csv")


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    return ok


def test_match_keys():
    ok = True
    ok &= check("a qual label maps onto our match key",
                lovat.match_key("Q42", EK) == f"{EK}_qm42")
    ok &= check("leading zeros and spacing survive",
                lovat.match_key(" Q07 ", EK) == f"{EK}_qm7")
    ok &= check("a playoff label maps to nothing rather than onto a qual match",
                lovat.match_key("SF2-1", EK) is None)
    ok &= check("so does anything unreadable",
                lovat.match_key("", EK) is None and lovat.match_key("Q", EK) is None
                and lovat.match_key("Q12", "") is None)
    return ok


def test_parse_fixture():
    ok = True
    with open(FIXTURE, encoding="utf-8") as fh:
        teams = lovat.parse_report_csv(fh.read(), EK)
    ok &= check("the fixture parses into two teams", sorted(teams) == [254, 6059],
                f"({sorted(teams)})")

    t = teams[6059]
    ok &= check("every row is counted, playoff rows included", t["matches"] == 4,
                f"({t['matches']})")
    ok &= check("the playoff row is reported, not dropped and not mis-joined",
                t["unmatched"] == ["SF2-1"], f"({t['unmatched']})")
    ok &= check("means skip the blank cells rather than averaging in a zero",
                t["avgFuel"] == 79.7, f"({t['avgFuel']})")
    ok &= check("a column nobody filled reads as unknown, never as zero",
                teams[254]["outpostIntakes"] is None, f"({teams[254]['outpostIntakes']})")
    ok &= check("a column filled with a real zero still reads as zero",
                teams[254]["feedSecs"] == 0.0 and t["outpostIntakes"] == 2.0,
                f"({teams[254]['feedSecs']}, {t['outpostIntakes']})")

    ok &= check("L2 and Level3 land in the same vocabulary we use",
                t["climbs"] == {"Level3": 3, "Level2": 1}, f"({t['climbs']})")
    ok &= check("best climb comes off that", t["bestClimb"] == "Level3")
    ok &= check("a climb label we cannot read is unknown, not a failed climb",
                teams[254]["climbs"] == {"Level3": 1} and teams[254]["climbsRead"] == 1,
                f"({teams[254]['climbs']})")

    ok &= check("booleans become a rate over the rows that answered",
                t["autoClimbRate"] == 25.0 and teams[254]["autoClimbRate"] == 100.0,
                f"({t['autoClimbRate']})")
    ok &= check("pipe-joined roles are tallied",
                t["roles"] == {"Scorer": 4, "Feeder": 1, "Defender": 1}, f"({t['roles']})")
    ok &= check("distinct scouters are counted", t["scouters"] == 3, f"({t['scouters']})")

    notes = t["notes"]
    ok &= check("only rows with a note become notes", len(notes) == 2, f"({len(notes)})")
    ok &= check("a note keeps the scout and the match it came from",
                notes[0]["scouter"] == "ada" and notes[0]["matchKey"] == f"{EK}_qm12",
                f"({notes[0]})")
    return ok


def test_per_match_rows():
    """The chart needs one row per match, not one average per team.

    An average hides the shape, and the shape is the reason to walk to a pit:
    a robot that put up 40 then 120 averages the same as one that put up 80
    twice.
    """
    ok = True
    with open(FIXTURE, encoding="utf-8") as fh:
        teams = lovat.parse_report_csv(fh.read(), EK)
    t = teams[6059]
    rows = t["perMatch"]
    ok &= check("one row per match, playoff rows included", len(rows) == t["matches"] == 4,
                f"({len(rows)})")
    ok &= check("a qual row carries the match key we join on",
                rows[0]["matchKey"] == f"{EK}_qm12" and rows[0]["match"] == "Q12",
                f"({rows[0]['matchKey']})")
    ok &= check("a playoff row keeps its label and no key, rather than being dropped",
                rows[3]["match"] == "SF2-1" and rows[3]["matchKey"] is None,
                f"({rows[3]['match']}, {rows[3]['matchKey']})")
    ok &= check("fuel and defence come through per match",
                [r["fuel"] for r in rows[:2]] == [88.0, 80.0]
                and rows[2]["defenseSecs"] == 4.5,
                f"({[r['fuel'] for r in rows]})")
    ok &= check("a blank cell is unknown per match too, never a zero",
                rows[2]["fuel"] is None, f"({rows[2]['fuel']})")
    return ok


def test_climb_timing():
    """The one thing Lovat has that our own scouting cannot produce.

    A scout with two thumbs cannot time a climb. Lovat records the second it
    started, in one column per level, and that answers the question every
    alliance captain asks: how long before the buzzer does this robot leave?
    """
    ok = True
    with open(FIXTURE, encoding="utf-8") as fh:
        teams = lovat.parse_report_csv(fh.read(), EK)
    t = teams[6059]
    ok &= check("the filled level column names the level as well as the time",
                t["climbStart"] == {"Level2": 119.0, "Level3": 128.5}, f"({t['climbStart']})")
    ok &= check("and pools into one number for the team",
                t["climbStartSecs"] == 126.2, f"({t['climbStartSecs']})")
    ok &= check("a team nobody timed in auto reads unknown, not zero",
                t["autoClimbStartSecs"] is None
                and teams[254]["autoClimbStartSecs"] == 55.0,
                f"({t['autoClimbStartSecs']}, {teams[254]['autoClimbStartSecs']})")
    untimed = teams[254]["perMatch"][1]
    ok &= check("a row with no climb time reads unknown and is left out of the mean",
                untimed["climbStartSecs"] is None and untimed["climbLevelTimed"] is None
                and teams[254]["climbStartSecs"] == 120.0,
                f"({untimed['climbStartSecs']}, {teams[254]['climbStartSecs']})")
    ok &= check("field traversal joins the other booleans as a rate",
                t["traversalRate"] == 100.0, f"({t['traversalRate']})")
    return ok


def test_the_bom_is_stripped():
    """Lovat's exporter leads with a byte-order mark, and it is load-bearing.

    Decoded as plain utf-8 it becomes part of the FIRST column's name, so every
    row's label reads as empty and every matchKey comes back None: none of
    Lovat's per-match data can be joined to our schedule at all.  That shipped
    once (`e36147a`) and survived because the fixture had no BOM - which is why
    the first assertion here is about the fixture rather than about the parser.
    """
    ok = True
    with open(FIXTURE, "rb") as fh:
        head = fh.read(3)
    ok &= check("the fixture still leads with a BOM, the way the real export does",
                head == b"\xef\xbb\xbf", f"({head!r})")
    # utf-8, not utf-8-sig: this is the naive read, and the parser has to cope
    # with it, because a caller that did the decoding itself is the normal case.
    with open(FIXTURE, encoding="utf-8") as fh:
        teams = lovat.parse_report_csv(fh.read(), EK)
    keys = [r["matchKey"] for r in teams[6059]["perMatch"]]
    ok &= check("a BOM does not blank the first column, so matches still join",
                keys[:2] == [f"{EK}_qm12", f"{EK}_qm19"], f"({keys})")
    with open(FIXTURE, "rb") as fh:
        ok &= check("and bytes straight off the wire decode the same way",
                    lovat.parse_report_csv(fh.read(), EK).keys() == {254, 6059})
    return ok


def test_enum_flags():
    """Three columns Lovat has written both ways, and a word from neither set.

    `autoClimb`, `beached` and `fieldTraversal` are enums in their schema and
    were TRUE/FALSE in an older export.  Read only as booleans they came back
    None for every row, so three rows on the dashboard were permanently blank
    and nothing said why.
    """
    ok = True
    with open(FIXTURE, encoding="utf-8") as fh:
        teams = lovat.parse_report_csv(fh.read(), EK)
    t, s = teams[6059], teams[254]
    ok &= check("SUCCEEDED is a yes; NOT_ATTEMPTED and FAILED are noes",
                t["autoClimbRate"] == 25.0 and s["autoClimbRate"] == 100.0,
                f"({t['autoClimbRate']}, {s['autoClimbRate']})")
    ok &= check("so is being beached on the bump, against NEITHER",
                t["beachedRate"] == 25.0 and s["beachedRate"] == 50.0,
                f"({t['beachedRate']}, {s['beachedRate']})")
    ok &= check("and crossing by the trench, against NONE",
                t["traversalRate"] == 100.0 and s["traversalRate"] == 50.0,
                f"({t['traversalRate']}, {s['traversalRate']})")
    # The shape of the next drift: Lovat adds a word, and the one thing we must
    # not do is read it as "it did not happen". 100% here rather than 50% is
    # the whole assertion - the unknown row left the denominator.
    drift = lovat.parse_report_csv(
        "match,teamNumber,beached\nQ1,6059,ON_THE_RAMP\nQ2,6059,ON_BUMP\n", EK)[6059]
    ok &= check("a word from neither set is unknown, never counted as a no",
                drift["beachedRate"] == 100.0, f"({drift['beachedRate']})")
    ok &= check("but it is still tallied, which is how anyone notices the drift",
                drift["beachedKinds"] == {"ON_THE_RAMP": 1, "ON_BUMP": 1},
                f"({drift['beachedKinds']})")
    return ok


def test_kind_tallies():
    """The tally says WHICH kind; the rate beside it already said whether.

    "beached 40%" tells a strategist nothing they can act on, and "beached 40%
    - on the bump" tells them to send the robot the long way round.  A cell
    written TRUE/FALSE carries no kind at all, and tallying it anyway put
    "false" into that row: the dashboard read `gets beached 0% - false`.
    """
    ok = True
    with open(FIXTURE, encoding="utf-8") as fh:
        t = lovat.parse_report_csv(fh.read(), EK)[6059]
    ok &= check("an enum export tallies the kind",
                t["beachedKinds"] == {"NEITHER": 3, "ON_BUMP": 1}
                and t["traversalKinds"] == {"TRENCH": 2, "BUMP": 1, "BOTH": 1},
                f"({t['beachedKinds']}, {t['traversalKinds']})")
    ok &= check("auto climb keeps its three outcomes apart - never tried is not fell",
                t["autoClimbResults"] == {"NOT_ATTEMPTED": 2, "FAILED": 1, "SUCCEEDED": 1},
                f"({t['autoClimbResults']})")
    old = lovat.parse_report_csv(
        "match,teamNumber,beached,fieldTraversal,autoClimb,robotRoles\n"
        "Q1,6059,FALSE,TRUE,FALSE,Scorer|Defender\n", EK)[6059]
    ok &= check("an older TRUE/FALSE export carries no kind, rather than a false one",
                old["beachedKinds"] == {} and old["traversalKinds"] == {}
                and old["autoClimbResults"] == {},
                f"({old['beachedKinds']}, {old['traversalKinds']}, {old['autoClimbResults']})")
    ok &= check("the rate it does carry is untouched",
                old["beachedRate"] == 0.0 and old["traversalRate"] == 100.0,
                f"({old['beachedRate']}, {old['traversalRate']})")
    ok &= check("and a role list is still tallied exactly as written",
                old["roles"] == {"Scorer": 1, "Defender": 1}, f"({old['roles']})")
    return ok


def test_survives_a_bad_file():
    ok = True
    ok &= check("an empty export is unknown, not an empty event",
                lovat.parse_report_csv("", EK) is None
                and lovat.parse_report_csv("   ", EK) is None)
    ok &= check("a header with no rows is a real, empty answer",
                lovat.parse_report_csv("match,teamNumber\n", EK) == {})
    ok &= check("a row with no team number is skipped, not crashed on",
                lovat.parse_report_csv("match,teamNumber\nQ1,\nQ1,6059\n", EK).keys() == {6059})
    ok &= check("junk parses to nothing rather than raising",
                lovat.parse_report_csv("not a csv at all", EK) == {})
    return ok


def _jwt(seconds_left):
    """A browser token shaped like Auth0's, with an expiry we choose."""
    import base64 as _b64, json as _json, time as _time
    enc = lambda raw: _b64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return "%s.%s.signature" % (
        enc(b'{"alg":"RS256","typ":"JWT"}'),
        enc(_json.dumps({"exp": int(_time.time() + seconds_left)}).encode()))


def test_the_key_script_reads_any_paste():
    """Whatever the browser put on the clipboard has the token in it somewhere.

    Chrome on a Mac copies a `\\`-continued command in single quotes, Command
    Prompt gets `^` and double quotes, `Copy as fetch` is JavaScript, and
    plenty of people copy the one header line. Matching the token itself rather
    than the shape of the command around it reads all of them.
    """
    ok = True
    tok = _jwt(3600 * 40)
    for label, blob in (
            ("a Copy as cURL from Chrome",
             "curl 'https://api.lovat.app/v1/manager/profile' \\\n"
             "  -H 'accept: application/json' \\\n"
             "  -H 'authorization: Bearer %s' \\\n  --compressed" % tok),
            ("the Command Prompt version, with ^ and double quotes",
             'curl "https://api.lovat.app/v1/manager/profile" ^\n'
             '  -H "authorization: Bearer %s"' % tok),
            ("Copy as fetch",
             'fetch("https://api.lovat.app/v1/manager/profile", {"headers":'
             '{"authorization":"Bearer %s"}});' % tok),
            ("just the header line", "authorization: Bearer %s" % tok),
            ("just the token", tok),
            ("a cookie token first, then the real one",
             "-H 'cookie: s=%s' -H 'authorization: Bearer %s'" % (_jwt(999), tok))):
        got, why = lovat_key.extract_token(blob)
        ok &= check("the token comes out of %s" % label, got == tok, "(%s)" % (why or got))

    # The two wrong pastes worth naming, because both look like success.
    got, why = lovat_key.extract_token("lvt-abcdefghijklmnop")
    ok &= check("pasting the key where the token goes says so, rather than 403ing later",
                got is None and "cannot mint another key" in (why or ""), "(%s)" % why)
    got, why = lovat_key.extract_token(
        "curl 'https://api.lovat.app/v1/manager/profile' -X OPTIONS")
    ok &= check("and the preflight row is named as the one with no credential in it",
                got is None and "preflight" in (why or ""), "(%s)" % why)
    return ok


def test_the_key_script_reads_the_tokens_own_clock():
    """A stale token and a wrong one are the same 401, so ask before sending.

    Read, never verified - only Lovat can check the signature. The expiry is
    read for one reason: 72 hours is exactly long enough for a scouting lead to
    do step one on Friday and the rest on Sunday.
    """
    ok = True
    ok &= check("a fresh token's time is read off it",
                lovat_key._left(lovat_key.token_expiry(_jwt(3600 * 47)) - time.time())
                == "46 hours left")
    ok &= check("hours all the way up, because these last 72 of them",
                lovat_key._left(3600 * 71) == "71 hours left")
    ok &= check("a stale one says how stale",
                lovat_key._left(-7200).startswith("expired 2 hours"),
                "(%s)" % lovat_key._left(-7200))
    ok &= check("and a token whose payload is not readable is simply unknown",
                lovat_key.token_expiry("not.a.token") is None)
    return ok


def test_the_key_script_explains_lovat():
    """Two different 403s, and only one of them is anything you can act on.

    `/profile` checks that you are signed in; `/apikey` also checks that a
    person at Lovat has verified your team. Asking both is what lets the second
    403 be reported as the team check rather than as a bad token - which is the
    failure that sends people off to mint a second token that fails identically.
    """
    ok = True
    tok = _jwt(3600)

    def stub(profile, apikey=None):
        def req(method, path, token, params=None, timeout=20):
            return profile if path == "/profile" else apikey
        return req

    key, who = lovat_key.mint(tok, "hub", stub(
        (200, {"teamNumber": 6059, "teamName": "Voltage"}), (200, {"apiKey": "lvt-real"})))
    ok &= check("a key comes back, with who it belongs to",
                key == "lvt-real" and "6059" in who, "(%s, %s)" % (key, who))

    key, why = lovat_key.mint(tok, "hub", stub(
        (200, {"teamNumber": 6059}), (403, {"message": "Your team has not been verified yet"})))
    ok &= check("signed in but unverified is reported as the team check, not the token",
                key is None and "verified your team" in why and "Team email" in why,
                "(%s)" % why)

    key, why = lovat_key.mint(tok, "hub", stub(
        (403, {"message": "Cannot create API key using an API key"})))
    ok &= check("and a key used as a token is reported as that instead",
                key is None and "cannot mint another key" in why, "(%s)" % why)

    key, why = lovat_key.mint(tok, "hub", stub((401, {"message": "Unauthorized"})))
    ok &= check("a 401 points at the 72-hour expiry, which is what it usually is",
                key is None and "72 hours" in why, "(%s)" % why)
    key, why = lovat_key.mint(tok, "hub", stub((401, {"message": "No team"})))
    ok &= check("except the one that says no team, which is a different errand",
                key is None and "not on a team" in why, "(%s)" % why)

    key, why = lovat_key.mint(tok, "hub", stub(
        (200, {"teamNumber": 6059}), (200, {"somethingElse": 1})))
    ok &= check("an answer with no key in it is said out loud, not read as success",
                key is None and "no key back" in why, "(%s)" % why)
    return ok


def test_the_key_script_takes_the_token_from_wherever_it_is():
    """Clipboard, then a paste, then a file - in that order and never stuck.

    The clipboard is first because the browser step ends with a copy, and
    because the alternative on Windows is pasting several kilobytes into a
    console window with its own opinion of how long a line may be. It is only
    ever an opportunistic look: anything unusable there falls through to
    asking, which works everywhere.
    """
    ok = True
    tok = _jwt(3600 * 47)
    curl = ('curl "https://api.lovat.app/v1/manager/profile" ^\n'
            '  -H "authorization: Bearer %s"\n' % tok)
    was = lovat_key.clipboard
    d = tempfile.mkdtemp(prefix="lovat-tok-")
    try:
        lovat_key.clipboard = lambda: curl
        got, why = lovat_key.prompt_for_token(io.StringIO())
        ok &= check("a copied request is picked up off the clipboard, with nothing typed",
                    got == tok, "(%s)" % (why or got))

        lovat_key.clipboard = lambda: "something else I copied earlier"
        out = io.StringIO()
        got, why = lovat_key.prompt_for_token(out, io.StringIO(curl + "\n"))
        ok &= check("a clipboard with no token in it falls through to asking",
                    got == tok and "Network" in out.getvalue(), "(%s)" % (why or got))

        lovat_key.clipboard = lambda: ""
        got, why = lovat_key.prompt_for_token(io.StringIO(), io.StringIO(curl + "\n"))
        ok &= check("and so does a machine with no way to read one at all",
                    got == tok, "(%s)" % (why or got))

        lovat_key.clipboard = lambda: curl
        got, why = lovat_key.prompt_for_token(
            io.StringIO(), io.StringIO(curl + "\n"), use_clipboard=False)
        ok &= check("--paste asks anyway, for anyone who would rather it did not look",
                    got == tok, "(%s)" % (why or got))

        path = os.path.join(d, "token.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(curl)
        got, why = lovat_key.prompt_for_token(io.StringIO(), from_file=path)
        ok &= check("--from-file reads one off disk", got == tok, "(%s)" % (why or got))
        got, why = lovat_key.prompt_for_token(
            io.StringIO(), from_file=os.path.join(d, "nothing-here.txt"))
        ok &= check("and a file that is not there says which one",
                    got is None and "nothing-here.txt" in (why or ""), "(%s)" % why)
    finally:
        lovat_key.clipboard = was
        shutil.rmtree(d, ignore_errors=True)
    return ok


def test_the_key_script_saves_it_the_way_the_panel_would():
    """Into `.env`, base64, 0600, and the hand-typed line taken out with it.

    One key, one home: a plain `LOVAT_API_KEY=` left behind beside the encoded
    line is a key that changes depending on which one the reader wins with.
    """
    ok = True
    d = tempfile.mkdtemp(prefix="lovat-key-test-")
    try:
        path = os.path.join(d, ".env")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("NEXUS_API_KEY_B64=bngtc29tZXRoaW5n\nLOVAT_API_KEY=lvt-by-hand\n")
        lovat_key.save("lvt-minted", path)
        with open(path, encoding="utf-8") as fh:
            lines = [l.strip() for l in fh if l.strip()]
        coded = [l for l in lines if l.startswith("LOVAT_API_KEY_B64=")]
        ok &= check("the key is written base64-encoded",
                    len(coded) == 1
                    and envfile.decode(coded[0].split("=", 1)[1]) == "lvt-minted",
                    "(%s)" % lines)
        ok &= check("the hand-typed plain line goes with it",
                    not any(l.startswith("LOVAT_API_KEY=") for l in lines))
        ok &= check("every other key in the file is left exactly as it was",
                    "NEXUS_API_KEY_B64=bngtc29tZXRoaW5n" in lines, "(%s)" % lines)
        # Windows has no such mode bits, and `envfile` says so where it writes
        # them. Asserting them there would be asserting something about POSIX.
        if os.name != "nt":
            ok &= check("and the file is not readable by anyone else",
                        oct(os.stat(path).st_mode & 0o777) == "0o600",
                        "(%s)" % oct(os.stat(path).st_mode & 0o777))
    finally:
        shutil.rmtree(d, ignore_errors=True)
        os.environ.pop(envfile.encoded_name("lovatKey"), None)
    return ok


if __name__ == "__main__":
    ok = True
    for fn in (test_match_keys, test_parse_fixture, test_per_match_rows,
               test_climb_timing, test_the_bom_is_stripped, test_enum_flags,
               test_kind_tallies, test_survives_a_bad_file,
               test_the_key_script_reads_any_paste,
               test_the_key_script_reads_the_tokens_own_clock,
               test_the_key_script_explains_lovat,
               test_the_key_script_takes_the_token_from_wherever_it_is,
               test_the_key_script_saves_it_the_way_the_panel_would):
        print("\n" + fn.__name__.replace("test_", "").replace("_", " "))
        ok &= fn()
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)
