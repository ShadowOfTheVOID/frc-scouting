# Bug hunt — handoff notes

First pass: `claude/seat-bugs-cl68f2`, 26 commits on top of `e669eaa`.
Second pass: `claude/bug-hunt-logic-review-7oavun`, over everything that landed
after it — the off-site mirror, the admin panel and key checking, the battery
and ETag work, and OpenRouter.

Everything below is committed and pushed. Five test suites pass, and both CI
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

## Fixed — first pass

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

## Fixed — second pass

Everything in this section is on `claude/bug-hunt-logic-review-7oavun`. The
phone ones were driven in Chromium against a real hub, with the browser's radio
taken away under a loaded page rather than by killing the server, and each was
proved by stashing `web/js/hud.js` and running the same drive against the old
file.

### The phone stopped opening for a match

| what was wrong |
| --- |
| **`On field` only armed the HUD when it arrived by webhook.** The status a phone acts on has two paths into it: Nexus's push, which broadcasts `matchStatus`, and `poll_nexus`, which lands in `/api/state` and broadcasts `nexus`. Only the first one armed anything; `applyState` took the new schedule, redrew standby and stopped. A webhook has to reach the hub *from the internet*, and the hub sits behind the venue's NAT — which is the whole reason `server/offsite.py` exists and pushes outward — so at an actual event the polled path is the only path there is. Driven: seated on RED 1, `On field` applied exactly as `poll_nexus` applies it. Before: `s-standby`, and it stays there while the match is played. After: `s-live`, on 9001. Arming is one-way per match, so pressing DONE and landing back on standby while the field still reads `On field` cannot hand the scout a second empty entry for a match they have just sent |
| **Arming threw away the pre-match answers.** `loadMatch` builds a fresh entry, and standby is where the preload and the start zone are typed — against the very match that is about to arm. Both paths reloaded unconditionally, so at the buzzer the scout had a blank entry and no spare thumb to retype them. Arming keeps the entry when it already belongs to that match and that robot, and adopts the shared clock itself. Driven: CENTRE and 3 balls entered on standby, then `On field`, then one logged run. Before: `start None, pre None`. After: `start centre, pre 3` |
| **Nothing ever took a phone off the offline screen.** It is only ever entered — DONE, HAND OVER or SIT while out of range — and the way back was a reload. So a phone that lost the hub once stayed there for the rest of the day, still queueing and still syncing when the hub came back, but off the one screen the auto-arm fires from. Driven: standby → radio off → HAND OVER → `s-offline`; radio back on → before, `s-offline` for as long as the drive ran; after, `s-standby` inside one tick |
| **The standby list said SENT about everything.** The QUEUED/SENT tag read a `_queued` flag on the row that nothing in the codebase ever wrote, so a phone that had never once reached the hub told the scout their morning was sent. Driven: one match logged out of range, chip `1 WAITING` — before, no QUEUED tag on it; after, `QUEUED`. The tag is now kept beside the rows rather than stamped on them (`history[0]` is handed straight to FIX THE LAST MATCH as the live entry, and a display flag welded into a record gets saved and synced with it), and the list is re-read when the queue drains |

### The hub

| what was wrong |
| --- |
| **Two vendors' back-offs were written to objects that were thrown away.** `hub.lovat()` and `hub.ai()` built a fresh client from the settings row on every call, and `Lovat.down_until` / `ai.Client.down_until` are instance fields those clients set on themselves. Lovat allows one request every three seconds and 403s a team that is not verified; an AI key that was just rejected will be rejected again. Neither ever backed off — every TEST KEYS press and every settings save (which fires `_poll_all`) went straight back at a vendor that had just refused us — and the diagnostics panel's `rate limited, backing off` line could not appear, because it reads `down_until` off a client one millisecond old. Both clients are now cached by the credentials they were built from, so a key corrected on the Setup page still takes effect at once, and clears the backoff with it |
| **A password with a space on the end locked its own owner out.** `check_admin` stripped what was typed in and not what was stored, and `--set-admin-password` stores what it is given. Both sides are trimmed now |
| **The CORS preflight did not allow `X-Admin-Token`.** Every other header the API is sent was listed. This is only reachable with `--allow-remote-config`, or from a phone that found the hub again at a second address, but it fails as a browser refusal with nothing in the response to read |
| **Broadcasts nobody was listening to.** `_nexus_side` sends `alliances`, `pits`, `pitMap` and `inspection` under their own names. The dashboard's listener list had dropped `alliances` on the stated grounds that alliance selection arrives inside `nexus` — it does not; the `nexus` message carries the queueing status, the schedule and the announcements — so during selection the board learned a team had been picked only when the ten-second poll came round, on the one screen where hub.py's own comment says that is the thing not to do. The pit tablet had the same note about `pits`/`pitMap`/`inspection` and the same thirty-second wait behind it. Both now listen for what the hub actually sends, coalesced; the dashboard deliberately still ignores the three pit names, because nothing on it draws them |

### The mirror

| what was wrong |
| --- |
| **`?rev=abc` killed the request thread.** `int(rev)` on a query-string value, straight out of `do_GET`: no response, the socket dropped, and on a mirror run with `--open` that is anybody who can reach the address. Proved by restoring the old function body under a live mirror — `RemoteDisconnected: Remote end closed connection without response`. A revision that is not a number is now a revision the mirror does not have, and `mirror/tests_mirror.py` covers four spellings of it. A photo row whose team is not a number no longer takes the rest of its batch with it either |

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
5. **A HAND OVER mid-match is read three different ways.** `hub.solve_match`
   takes the newest of the two rows for one (match, team) on the grounds that
   the outgoing scout's row is a partial match; `analytics._team_trend` and
   `analytics.score_report` take the *first* on the grounds that it covers the
   start of the match. Both comments are right about their own half and neither
   row is the whole match — the incoming scout gets a fresh entry, so the two
   are complementary halves of one observation. Nothing here is wrong enough to
   have shown up in the accuracy gate, and merging them is a solver change
   rather than a fix, so it is written down rather than done.
6. **`Handler._body()` does not drain a body it refuses.** A bogus or oversized
   `Content-Length` gets a JSON answer, and then the unread bytes are parsed as
   the next request on a keep-alive connection. Only reachable by a malformed
   request, and it costs that one connection.
7. **`db.saveScout` stamps `updatedAt` from `Date.now()`, not `net.serverNow()`.**
   Last-write-wins on the hub is decided by that number, and the skew-corrected
   clock exists three modules away. It only bites when two phones write the same
   (match, team, scout) — a HAND OVER onto a second phone — and `db.js` cannot
   import `net.js` without a cycle, so it is a small refactor rather than a line.

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

The second pass rebuilt three of those drives and they went the same way. Two
notes for whoever rebuilds them next:

- `pip install playwright` pulls a version whose browser build does not match
  the one on the image. Launch with
  `executable_path="/opt/pw-browsers/chromium"` rather than downloading another.
- Take the radio away with the browser's own offline switch
  (`context.set_offline(True)`), not by stopping the hub. There is no service
  worker — plain HTTP is not a secure context — so a phone whose hub is gone
  cannot reload the page at all, and killing the server tests a state a scout
  never reaches. Leaving the page loaded and cutting its network is exactly the
  scout who has walked into the stands.
