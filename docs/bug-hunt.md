# Bug hunt — handoff notes

First pass: `claude/seat-bugs-cl68f2`, 26 commits on top of `e669eaa`.
Second pass: `claude/bug-hunt-logic-review-7oavun`, over everything that landed
after it — the off-site mirror, the admin panel and key checking, the battery
and ETag work, and OpenRouter.
Third pass: the same branch with `main` merged back in, over the setup
checklist, the keys moving into `.env`, the Windows firewall helper and the
after screen's second page.
Fourth pass: stress harnesses rather than a code read - concurrent clients
against a live hub, every fix proved by removing it and watching the failure
come back.

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

### The strategy dashboard

Driven in Chromium against a hub serving the seeded demo event, reading the
rendered DOM rather than the source.

| what was wrong |
| --- |
| **Stored XSS through a pit photo id.** `/api/sync` is deliberately open - it is what every phone hits at the buzzer - and `_extract_photos` kept any string in `photos` that was not a `data:` URI as "already an id". Both the dashboard's team detail and the pit tablet then pasted that string straight into an `<img src="...">` with no escaping. A pit record synced with `photos: ['x" onerror="…"']` ran script on the hub's own origin, which is where the strategy token lives. Demonstrated end to end: `document.title` came back `XSS`. The hub now keeps only ids shaped like the ids it issues (the first 16 hex of the image's sha1), and both render sites escape and URI-encode what they are given. The mirror already did this correctly, which is how the shape of the fix was already decided |
| **The TEAMS table was a column short.** Thirteen headers and thirteen grid tracks, twelve cells per row: the DRIVER cell was never rendered. Everything from DRIVER rightwards sat under the wrong heading - Lovat's fuel appeared under DRIVER, the match count under LOVAT, and MATCHES was blank - while clicking DRIVER sorted correctly by a number that was not on screen. Counted in the rendered DOM: `header cells: 13  row cells: 12` |
| **Two panels named a match by mangling its key.** `shortCode()` takes a label (`Qualification 4` → `Q4`); the crew board's LAST MATCH column and the flag list on HEALTH hand it a match *key*, and its regex turned `2026demo_qm4` into `24` - the first character of the event key, then the last run of digits. Both are read to decide where to walk. Driven: before `24 · 1s ago` and `24 · clock-offset`, after `Q4 · 1s ago` and `Q4 · clock-offset`. The helper now recognises a key, and both call sites resolve it to its label first |
| **COVERAGE counted matches nobody had played yet.** The whole schedule was in the denominator, so a crew that had missed nothing read 65% on the seeded demo (26 of 40 played) and would read about 11% on the Saturday morning of a 70-match regional. It is the tile beside MEDIAN ERROR and CALIBRATED under "how much to trust the numbers", and the mirror carries it in its header line. Now 100% of 156 on that same demo. Played means TBA has posted it or somebody scouted it - the second half because TBA lags the buzzer by minutes, and a match nobody watched is exactly the one that must not be quietly dropped |
| **The "gone unscouted" heads-up could not see a station go quiet.** It asked whether a team had EVER been scouted, which is almost always yes, so a scout who stopped after Q4 named nobody - the failure the panel exists for. Analytics now says per match whether one of our scouts was on that robot (`scouted` on each trend row, which the charts already carry), and the panel asks that over the last three played matches. A seated scout who has never logged a single row is flagged too: the old rule read an age, and an age nobody has is null, so the scout who had not understood the app was the only one it could not see |
| **The picklist scaled defence wrong.** The phone collects 1..4 ('not at all' .. 'a lot'); the score divided by 5, so a robot a scout had explicitly marked as *not* defending scored 0.2 - which on the second-pick board, where defence is weighted 35, put it seven points ahead of a robot nobody had rated at all. Now `(rating - 1) / 3` |
| **A projection over an unscouted alliance read as a weak one.** A robot nobody has scouted contributes nothing to the sum, so PROJECTED FUEL showed a real number with a warn colour and no hint that two of the three robots were unknown. The row table underneath said `not scouted`; the tile - the thing that gets read out loud - now does too |

### Two people clicking at once

The database was never the problem: `store.mutate` takes the write lock before
it reads, and every kv write already goes through it. What collided was above
that - whole documents where a patch was meant, and shared objects in the hub
process with nothing round them.

| what was wrong |
| --- |
| **The picklist was sent as a whole document.** Every edit - a flag, a slider, a drag, a reset - POSTed `weights`, `weights2`, `dnp`, `order` and `order2` together, out of that tab's own memory. Two dashboards during alliance selection is the case the second dashboard exists for, and whichever lead clicked second silently undid the other. Driven, three runs out of three: one lead marks 254 do-not-pick while the other drags 1678 to the top, and the hub keeps one of the two. Now each control sends only what it changed, and the flags go as `dnpAdd` / `dnpRemove` - six leads flagging six different robots in the same instant is not a conflict and is no longer resolved as one. The board carries a `rev` the hub bumps on every write and names in the broadcast, so a tab can tell somebody else's edit from the echo of its own - which also stops a slider being yanked back under the hand still dragging it |
| **A refused picklist write was swallowed.** The passcode gets rotated during an event and a token lasts sixteen hours, so a board could sit there saying EDITING UNLOCKED while the hub threw away every drag. It now drops the dead token, falls back to read-only and says the session expired |
| **Two threads could push to the mirror at once.** The poller runs every sixty seconds and PUSH NOW is a button: two pushes meant the bundle built twice (analytics, both CSVs, a hash of the lot), sent twice over a shared venue uplink, stored as two revisions, and then a race between the two `mirrorState` writes that could leave the panel reporting the older push as the last one. One at a time now - the poller steps aside, the button waits its turn |
| **`writes` and the event log were rebuilt-and-reassigned from every request thread.** Six phones flush at the buzzer and the diagnostics panel reads the same two lists from another thread. Both are under a lock, and `diag()` takes its copy inside it |
| **`apply_nexus_event` had two callers and no lock.** The poller and the webhook handler both read `last_nexus_at`, decide whether the payload is news, and write it back - so two of them inside that is either the same broadcast sent twice to every phone in the building, or an update dropped because the other thread had already moved the clock past it |
| **REFRESH was six outbound calls per press, unlimited.** Two leads pressing it together - or one lead pressing it repeatedly because nothing seems to be happening, which is exactly when they will - went straight at five vendors' quotas. Rate limited to one round every ten seconds; a settings save still forces one through, because it has just changed a key |

### Conflicting signals

Two sources describing one thing, and the app believing the wrong one.

| what was wrong |
| --- |
| **A played match still read `On field`.** Nexus's status comes from a volunteer with a tablet, and `put_match` COALESCEs a missing status - so once a match drops out of the live feed it keeps whatever it was last told, for the rest of the day. TBA's breakdown comes from the field and means the match is over. Driven with Q1 scored and still marked `On field`: before, the phone opened the live HUD on it - a scout logging a match that finished an hour ago, one tap from sending the row - and the dashboard's LIVE panel headlined it as the match on the field. After, the phone sits on standby counting down to Q2 and the board leads with Q2. An official result now beats a queueing status everywhere the two are read together: `pickCurrentMatch`, the arm, the boot jump, the LIVE panel, the match preview and the crew board |
| **A reconnecting phone took its chair back off whoever the lead had just given it to.** The station is freed on the crew board and somebody else sits down; the original phone was out of range and heard none of it; on reconnect it re-asserted the claim it still believed in and bumped the new scout - on the hub's own instruction. Driven with the hub genuinely stopped, so the stream really breaks (Playwright's offline switch leaves an established EventSource alive, which is why the first run of this drive proved nothing): before, `POST /api/seat` and AK has the chair back; after, `GET /api/seats`, AK gets the bump screen, and BK keeps the chair. A reconnection is a re-ask, not a re-assert - and the 15s rate limit on that read now lets a forced one through, or the phone would go on believing the chair was its own |

### Third pass — over the setup, `.env` and after-screen work

Merged `main` in first (three PRs: the Lovat key docs, the after screen's
second page, and setting-up-as-a-checklist with the keys moved into `.env`) and
went over what landed.

| what was wrong |
| --- |
| **`.env` was rewritten in place, and it now holds everything.** Every API key and the admin password live in that one file, and the panel tells people it is the whole backup - so the one thing it must never become is a shorter file. `os.open(..., O_TRUNC)` then write has a window in the middle where it is empty, and a full disk lands in it: the hub comes back with no keys, no password and nothing to say why. It writes beside it, fsyncs and renames now. Proved by making `os.fsync` raise ENOSPC mid-write: before, an empty file; after, every key still there and no temporary left behind |
| **The hub and the mirror read the same variable in opposite orders.** `MIRROR_PUSH_KEY` is deliberately one name on both sides of that push. The hub took `_B64` first and the mirror took the plain one first, so a host with both set had the two halves authenticating against different strings - reported as "bad push key", by two halves each certain they were right. One reader (`envfile.read`) decides it now, and the mirror calls it |
| **A robot could be recorded as having climbed *and* as having fallen off.** CLIMB FELL OFF and the CLIMB button hide each other on the phone, but hiding a chip does not clear it: tap the chip, then record a level, and both stay set with no way back to the chip. The team page then read "best climb L2, 100% of matches" and "tried a climb and fell, 100%" at once. The button clears the chip now, and the aggregate resolves it for rows already logged - the recorded level is the stronger signal, because it says which level and the chip only says something went wrong |
| **`/api/eventsfor?year=abc` killed the request thread.** `int()` on a query-string value, straight out of the handler - no response, a dropped connection - on the one page somebody is looking at while nothing else on the hub works yet. The same shape as the mirror's `?rev=` in the second pass; worth grepping for the next one |
| **The firewall check matched the port as a substring.** `str(port) in out` over `netsh`'s output says yes for `--port 605` against a rule holding 6059 - the false "allowed" that `exists()`'s own docstring says it is there to catch. Matched as a whole number now, still without reading a label, because netsh prints in whatever language Windows was installed in |
| **The panel was opened in a browser before the firewall question was answered.** The socket is bound by then but nothing serves it until `serve_forever()`, so on the one platform this feature exists for, the browser sat on a page that could not load while the question waited in the terminal behind it - which is the same "prompt nobody saw" the module was written to stop. The offer goes first |

### Fourth pass — driven by stress rather than by reading

Load harnesses this time, not a code read: concurrent clients against a live
hub, with an invariant asserted after each round and the fix proved by putting
the lock back and watching the failure return.

| what was wrong |
| --- |
| **Two saves to `.env` at once lost one of them, six runs out of six** - and crashed. It is read, changed and written back, and it holds every key: without one writer at a time, one of two concurrent edits is simply not in the file afterwards. Worse, the atomic write added in the third pass gave both writers the same temporary name, so the second `os.replace` came back `FileNotFoundError` out of the request handler - a dropped connection with the panel still saying "saving…". One writer at a time, and a temporary name of its own. Nine writers × six saves each, with a reader alongside: nothing lost, no gaps seen, no temporary left |
| **The room could be told the loser won a chair.** Two scouts claiming one station: the write was always atomic, but the broadcast that tells everyone is a separate statement, so the two messages could be scheduled in the opposite order to the two writes. Measured, 3 races in 25 (6 in 25 on a second run): the hub holds the winner and every screen in the building shows the loser, until the next seat event or the next full poll - and the crew board's FREE button, its unwatched-robot alert and the phones' own bump check all read that map. Deciding and telling are one step now |
| **A match could be solved from a partial view of it.** Every `/api/sync` solves the match it touched, so three scouts flushing at one buzzer put three threads inside read-entries / divide-the-official-totals / write-the-rows at once, each from a different read - and the last to WRITE won, not the last to read. Measured, 1 match in 8: a robot watched for five seconds came out holding more fuel than one watched for ten. Serialised, so the last writer is also the last reader |
| **A stream client that stopped reading parked a thread forever.** The overflow mark added earlier is read at the top of the loop, and a thread blocked *inside* a write never gets back there - so a phone that walks out of range (TCP retransmits into the silence rather than closing) left the hub holding the subscription, the crew board calling it LIVE, and a thread that was never coming back. A write to a streaming client is bounded now; with the bound, a client that opens the stream and never reads it is let go |
| **Every phone that dropped off the wifi printed a stack trace** into the hub's own window. socketserver reports any exception out of a handler, and a reset connection is one. That window is this app's diagnostic surface - the banner, the key problems and the checklist all print there, and `log_message` was silenced for exactly this reason. Disconnections are quiet now; a real fault in a handler still gets its traceback, which is checked |
| **The printed picklist and the screen ranked robots differently.** The sheet a lead carries into alliance selection had its own copy of the score, and the defence correction in the second pass only reached the dashboard's. Driven on the seeded demo: the paper's top second pick was 9984, the screen's was 9989. The formula is one module now (`web/js/picklist.js`), imported by both - which is what the comment above it already claimed |

Two things the stress said were fine, having looked:

- **The ETags.** A first pass at this reported 110 "stale 304s" in 868 polls;
  every one was the harness racing itself - it fetched the truth *after* the
  304, with writers still running. Redone one-write-at-a-time with nothing
  racing, across nine kinds of write and all four conditional endpoints: every
  tag told the truth.
- **Picklist broadcasts arriving out of order.** Same shape as the seat one and
  it does not matter, because that message carries a revision rather than the
  board: a client that sees a revision it did not write re-reads, in whatever
  order the two arrive. It is the payload in the seat message that made
  ordering load-bearing there.

## Checked and clean

Do not re-litigate these without new evidence.

- **`chart.js` coordinate math** — swept with generated data, no off-by-ones.
- **XSS through six untrusted paths** — `esc()` holds *where it is called*.
  The second pass found two places it was not: the pit photo id, on the
  dashboard's team detail and on the pit tablet (fixed above). An earlier
  "finding" in this section was my detector matching `esc()`-decoded attribute
  values, and that one was genuinely nothing. The lesson from the second pass
  is that a sweep for "is `esc` wrong" will not find "`esc` is absent".
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
