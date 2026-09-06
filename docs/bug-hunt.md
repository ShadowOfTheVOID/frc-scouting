# Bug hunt — handoff notes

Working branch: `claude/seat-bugs-cl68f2`, 26 commits on top of `e669eaa`.
Everything below is committed and pushed. Four test suites pass, and both CI
gates hold (accuracy 9.4% of TBA over 253 fitted windows; the Nexus/TBA merge
gate at 40 canonical rows in schedule order).

This is a working log, not a spec. It exists so the hunt can be picked up
later without re-deriving what was already looked at.

## How anything here was accepted as a bug

Reproduce it first, fix it, reproduce the fix, and where possible prove the
"before" by reverting the change and running the same drive again. Several
findings that looked real did not survive that and were dropped — they are in
[Checked and clean](#checked-and-clean) so nobody spends a second afternoon on
them.

Two "before" runs in this log were initially worthless and had to be redone:
one used `git stash` on code that was already committed (a no-op, so it
measured the fixed code twice), and one moved the hub by *port* with the IP
fixed, which is backwards from a real DHCP move and skipped the subnet sweep
entirely. Both are corrected below. If a before/after table looks too tidy,
check that the "before" leg really was the old code.

## Fixed

### The seat / station claim

| commit | what was wrong |
| --- | --- |
| `9148ace` | eight bugs in one: the claim was losable, expirable and unfreeable; a claim made offline was dropped; a restart walked past the bump screen; practice seats persisted |
| `37fe04a` | the deliberate take-back was blocked by my own guard; station labels disagreed across seven call sites; a bump did not bank the work in hand |
| `fc2186c` | IT'S STILL MY CHAIR now only offered between matches; a bumped phone was dragged back by `matchStart`; rejoining landed on a finished match |

### Numbers the strategy team acts on

| commit | what was wrong |
| --- | --- |
| `0db516f` | **a hold across a window edge.** The scout holds the pad; the robot does not stop shooting because a shift ended. The interval carried one phase tag — where it started. Three robots that shot identically (5s TRANSITION + 7s SHIFT 1, official 30 + 60) resolved to 16 / 37 / 37 fuel. Now 30 / 30 / 30. Split on read, so the phone still shows one run and UNDO still takes it back in one tap. Median per-robot error 13.6% → 11.8% in a continuous-time Monte Carlo |
| `0cdbc68` | calibration fitted from alliances where only some robots were watched — 0.1% vs 44.6% multiplier error |
| `07842fd` | the key migration ran but reconcile skipped re-solving, so an even three-way split survived its own repair |
| `4c91031` | a three-match Nexus push reordered the whole schedule |
| `861700a` | six spellings of "qualification 42" did not merge onto `qm42`; slug keys could be URL-unsafe |
| `dc44269` | `remap_match_key` was not atomic — a crash mid-fold orphaned scouting |
| `e36147a` | Lovat's BOM nulled every `matchKey`; three enum columns were read as booleans, leaving three permanently blank rows; climb rate was biased |
| `2265f8c` | a legal clock correction could fabricate a 40/40/40 split |

### The hub as a server

| commit | what was wrong |
| --- | --- |
| `d60da5a` | one bare `NaN`/`Infinity` made three endpoints unparseable to every browser — the dashboard fell back to cache and quietly stopped updating |
| `2265f8c` | seven ways a request thread died outright; junk payloads blanked `/api/analytics` |
| `9d6e16f` | accept backlog 5 → 128 (60 of 200 simultaneous connects were being reset); the writes buffer was uncapped |
| `2ce9fe8` | one odd match from TBA killed three other pollers for the day, and span at 2s |
| `7728427` | stored XSS via a pit photo's `Content-Type`; header injection through the export filename; CSV formula injection |
| `a4cd7d7` | a non-string AI setting stopped `/api/diag` answering |
| `8860047` | a second dashboard wiped picklist DNP/order during alliance selection; the SSE subscription names were dead |
| `732504a` | the phone's queue drained one row per flush instead of one match |

### Finding the hub at all

Four separate faults, all on the path a phone takes when the laptop's address
changes. Driven in Chromium against a hub bound to one IP at a time, port 6059
throughout, nothing about the phone's state touched by hand:

```
before:  never found it. still on http://192.0.2.3:6059   (chip says offline)
after:   the phone's own sweep found it: http://192.0.2.2:6059 (t+15s)
         streams opened: .3:6059/api/stream, .2:6059/api/stream
         match starts heard after the move: 1
```

| commit | what was wrong |
| --- | --- |
| `a52e9e8` | **no CORS headers anywhere.** Every hub address other than the one a phone loaded the page from is a different origin, so the browser refused every response: the remembered last-good address, both fallback candidates and the 254-host sweep all failed identically. A JSON POST is preflighted, and `BaseHTTPRequestHandler` answers OPTIONS with 501, so `/api/sync` was refused before it was sent. Not granted to localhost — `_is_local()` is what guards the API keys, and a website open on the hub laptop is exactly the caller it exists to stop |
| `f2a9767` | `connectStream()` bailed on `es` alone, so a stream was never re-pointed. EventSource parks a dead connection in CONNECTING, not CLOSED, so the one line that cleared it never ran. The phone synced happily to the new address while streaming from the old one — deaf to seat claims, match starts and the shared clock |
| `9b9091c` | the sweep pulled the /24 *and* the port from one IP-shaped regex, so `http://scout.local:7000` swept port 6059. `scout.local` is the first candidate the client tries, and a lead running `--port` is the only person the remembered port exists for |

### Documentation

`8a4ab6d` Nexus marked required · `0fcfe23` a manual door so Plan B is real ·
`fd35d85` cache figures measured against a 75-match schedule, not the demo's 40 ·
`a1375a4` why there is no Lovat API-keys page

## Checked and clean

Do not re-litigate these without new evidence.

- **`chart.js` coordinate math** — swept with generated data, no off-by-ones.
- **XSS through six untrusted paths** — `esc()` holds. An earlier "finding"
  here was my detector matching `esc()`-decoded attribute values.
- **`csv.writer` quoting**, and `rules` / `solve` / `analytics` under
  degenerate input.
- **Mixed time units.** Nexus sends milliseconds, TBA seconds, both into the
  same `times` dict (documented at `store.py:153`). Two consumers, both
  correct.
- **`/api/ai/` cache keys.**
- **`lineup.indexOf(t)` in `allianceCard()` (`web/js/desk.js`).** Looks like a
  duplicate-team bug and is not: `teams` is derived elementwise from `lineup`
  by a pure lookup, so a duplicate resolves to the same object either way.
- **The RP outlook math.** The Energized/Supercharged/Traversal thresholds are
  arithmetically consistent with `rules2026.json`, and the event-level chain
  (regional → champs) was verified end to end on a live hub. The RP *names* are
  this repo's invention for a fictional game and have no documented semantics,
  so there is nothing to check them against.

## Still open

Nothing is blocked. These are the threads that were live when the hunt paused.

1. **The hunt itself.** It stopped because it was asked to, not because the app
   is clean. No claim is made that nothing is left.
2. **`server/fixtures/lovat_report_example.csv` is unrepresentative** in exactly
   the two ways that let `e36147a` live: it has no BOM, and it uses TRUE/FALSE
   where the real export sends enum strings. Regenerating it from Lovat's real
   `CondensedReport` field order would make `tests_lovat.py` mean something.
3. **`tests_api.py` carries test cases added before the no-test-files
   instruction.** They are committed and passing. If the instruction was meant
   to apply retroactively, they should come out.
4. **`seed_demo.py` defaults to 40 matches / 31 teams.** Real quals run 60–75.
   The seed also generates intervals strictly inside each window, which is why
   the phase-straddle bug in `0db516f` could not be caught by the existing
   Monte Carlo.

## The harness is gone

Everything used to find these — fuzzers, concurrency and contention drivers,
parser fuzzers, and the Playwright drives — lived in the session scratchpad and
was never committed, by instruction. It does not survive the container. Rebuild
what you need; the drives that mattered most were:

- a hub bound to one IP at a time, so an address can genuinely go dark
- a browser pinned to direct connections (`--no-proxy-server`), or a sandboxed
  Chromium routes 254 LAN probes through a proxy that cannot reach them
- unique match keys per run — `start_match` is first-tap-wins, so a reused key
  broadcasts nothing and a control leg silently reads zero
