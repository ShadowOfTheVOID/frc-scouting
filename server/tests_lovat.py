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
    for fn in (test_match_keys, test_parse_fixture, test_survives_a_bad_file):
        print("\n" + fn.__name__.replace("test_", "").replace("_", " "))
        ok &= fn()
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)
