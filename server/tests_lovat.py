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
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import lovat  # noqa: E402

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


if __name__ == "__main__":
    ok = True
    for fn in (test_match_keys, test_parse_fixture, test_per_match_rows,
               test_climb_timing, test_the_bom_is_stripped, test_enum_flags,
               test_kind_tallies, test_survives_a_bad_file):
        print("\n" + fn.__name__.replace("test_", "").replace("_", " "))
        ok &= fn()
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)
