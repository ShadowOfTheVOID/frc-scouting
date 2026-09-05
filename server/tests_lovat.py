#!/usr/bin/env python3
"""Regression gate for the Lovat import.  Run: python3 server/tests_lovat.py

Lovat's export is somebody else's file format and it will drift.  These cover
the three ways a drift would be silent rather than loud: a match label that
does not map onto our schedule, a blank column read as a zero, and free-text
notes losing the scout who wrote them.  No network - the fixture is on disk.
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
               test_climb_timing, test_survives_a_bad_file):
        print("\n" + fn.__name__.replace("test_", "").replace("_", " "))
        ok &= fn()
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)
