# How it works

The reasoning behind the parts that are not obvious. You do not need any of this to run
an event — see the [README](../README.md) for that.

## The match clock

The one thing that has to be right, but not in the way you'd expect.

**What matters is that the three scouts on an alliance agree with each other — not that
they agree with the buzzer.** The solver splits each official window total between three
robots, so a shared offset barely moves the answer while scouts on independent clocks
tear the windows apart. Measured:

| clock | median per-robot error |
|---|---|
| perfect | 19.6% |
| **shared** offset, sd 5s | 19.1% |
| independent offsets, sd 2s | 23.6% |
| independent offsets, sd 5s | 32.4% |

So the first scout to tap the pad starts the match **for everyone on it**. The hub records
that instant and pushes it over SSE; the other phones adopt it, jump into the HUD, and show
`CLOCK FROM AK` so nobody wonders whose clock they are on. Phones also correct for their own
clock skew against the hub, so "the same instant" really is the same instant. A phone that
is offline falls back to its own clock — unavoidable, and the reason the header says so.

### Can't an API just start the timer?

No API can start it **live**, but one can **fix it afterwards** — which is better, because it
means nobody has to be precise.

| source | what it gives | use for the clock |
|---|---|---|
| Nexus | `estimatedStartTime`, and `On field` when a volunteer clicks | arms the timer; cannot start it |
| TBA | `predicted_time` before, **`actual_time` after** | re-anchors the clock once results post |
| FRC Events | results after the match | same, slower |

Nexus is a queueing tool driven by volunteers — its own docs say no continuous FMS feed is
required — so `On field` happens while robots are still being placed. TBA's `actual_time` is
the real FMS start, but it does not exist until the match is over.

So the hub uses it retroactively: when TBA posts the match, it compares `actual_time` to when
the scouts' shared clock started, and re-attributes every interval to the window it truly
fell in. Raw observations are never modified — the correction is applied at solve time, so it
can be redone if TBA revises the match.

**What that buys, measured:**

| | raw | after the fix |
|---|---|---|
| shared tap, 5s late | 18.9% | 19.9% |
| shared tap, 15s late | 24.9% | **19.9%** |
| shared tap, 30s late | 30.8% | **19.9%** |

19.9% is the perfect-clock number. **A tap 30 seconds late costs nothing.** Tell scouts to
tap when they notice the match started, not to race the buzzer.

One limit worth knowing: the fix only applies to phones that were on the shared clock. A
phone that was offline ran its own timeline, and shifting it by someone else's offset makes
it *worse* (23.6% → 25.3% in simulation), so those are left alone and the match is flagged
`clock-partial` instead.

## How fuel counting works

A drum shooter empties faster than anyone can tap, so nobody counts balls.

**The scout answers "who was shooting, when, and roughly how hard."** Left thumb picks a rate off
the ladder (a trickle / steady / pouring). Right thumb holds the pad while the robot shoots. That
is the whole interaction.

**The server turns that into numbers.** TBA publishes the official fuel count per alliance *per
window* (auto, transition, shift 1–4, endgame). `server/solve.py` distributes each official total
across the three robots in proportion to `duration × rate`, largest-remainder rounded so the three
integers sum to the official count exactly.

Accuracy, measured by `server/tests_solver.py` (20k simulated matches, realistic scout error):

| | median | p90 |
|---|---|---|
| per-match, per-robot | 12.5% | ~50% |
| season average, 12 quals | ~8% | — |
| **ranking 40 teams** | **Spearman 0.966**, 7 of the true top 8 | |

So a single-match number is never shown as a bare integer — it always carries a band. Ranking
teams over an event, which is what a picklist needs, is strong.

The solver **calibrates itself**: a least-squares fit of the rate multipliers against official
window totals, converging in about 30 matches. The shipped values are only a cold start. Watch it
on the dashboard's HEALTH tab.

### Why the picklist leads with exact fields

Estimated fuel cannot be made exact by any amount of human observation — we tested both obvious
levers and neither works (a global OPR-style solve is a wash; a second scout on the same robot
only moves 12.8% → 10.5%). So the picklist is weighted toward what *is* exact:

- **Exact, from TBA:** per-robot climb level per match, auto climb, RP, fouls, W-L-T.
- **Exact-enough, from scouts:** yes/no observations — stockpiles through an inactive shift,
  feeds, real defense, broke down, no-showed.
- **Estimated:** fuel volume, banded, used to break ties.

---

## Getting data out (and back in)

There is no QR data handoff, deliberately. Receiving a QR needs a camera, a camera needs a secure
context, and the hub serves plain HTTP — so the dashboard on a LAN address cannot open one. The
join QR works because the *phone's own camera app* scans it, which is a different path entirely.

Instead:

- **Phone, offline** → `SAVE A BACKUP FILE` writes the queued matches as JSON.
- **Hub** → `GET /api/export` dumps the whole event; `POST /api/import` merges a file back in
  under the same last-write-wins rule, so re-importing the same file is a no-op.

