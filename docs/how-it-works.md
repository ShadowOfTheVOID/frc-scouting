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
| Nexus — required | `estimatedStartTime`, and `On field` when a volunteer clicks | arms the timer; cannot start it |
| TBA | `predicted_time` before, **`actual_time` after** | re-anchors the clock once results post |
| FRC Events | results after the match | same, slower |

Arming is not a nicety: `On field` is what opens the match screen on the six phones, and no
other source carries it, which is why Nexus is the one key to set — without it each scout has
to open every match by hand. Nexus is a queueing
tool driven by volunteers — its own docs say no continuous FMS feed is required — so `On field`
happens while robots are still being placed. TBA's `actual_time` is the real FMS start, but it
does not exist until the match is over.

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
the ladder (a trickle / steady / dumping). Right thumb holds the pad while the robot shoots. That
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

---

## The network, and why we do not bring our own

**The hub emits nothing.** There is no radio code in this repository. `hub.py` binds a socket,
`discover.py` sends mDNS multicast and *reads* the interface list the OS already has. Whatever
network exists, we use.

That is a deliberate constraint, not an omission. FIRST's event rules prohibit a team from
operating its own wireless access point in the venue — laptop hotspot, phone hotspot, ad-hoc
network alike — because team radios interfere with the field. So the setup instructions lead
with venue wifi, and the hotspot is documented only for practising at home. Rule numbers move
between seasons; the game manual is the authority.

### Bandwidth was never the constraint

Measured against the seeded demo event (31 teams, 40 matches, 156 scout entries):

| | over the wire |
|---|---|
| one scout's match record | 1.1 KB median, 1.4 KB p90 |
| one match, all six phones | ~7 KB |
| **a 12-match qual day, whole crew** | **~80 KB** |
| connected phone, idle | a `: keepalive` every 45s, and nothing else |
| dashboard refresh | four conditional GETs per 10s; ~0 bytes unless something changed |
| first page load, per phone | ~256 KB (66 KB gzipped code + 186 KB fonts) |
| every load after the first | ~0, if nothing was edited — code and markup revalidate |

Responses over 1 KB are gzipped, and phones only POST when the queue is non-empty. The whole
crew's steady-state demand is under 50 kbps. Even badly congested venue wifi has orders of
magnitude more than this.

The polled endpoints answer `304 Not Modified` when nothing has moved, which is most of the
time: the hub keeps a write counter per table and per kv key, and an endpoint's ETag is the
counters for exactly the things it reads. A dashboard sitting on a quiet event costs four
empty responses every ten seconds instead of ~165 KB of identical JSON, and — because the tag
is checked before the payload is built — the hub does not assemble the event to answer. See
[Battery](#battery), below.

So when a phone cannot reach the hub it is **never** because the network is slow. It is client
isolation, a captive portal, or a firewall — three things that are all binary, and all
discoverable in thirty seconds by loading the hub's address on one phone before seating six.
That preflight is the single highest-value step in the setup list.

### Why there is no service worker

The app would be much better with one: a scout who reloads out of range would get the app back
instead of a dead page. It cannot be done here. Browsers only register a service worker in a
secure context, and `http://` on a LAN address is not one — the same wall that stops the
dashboard from opening a camera, above.

The alternatives are worse than the problem. HTTPS on a LAN address means a self-signed
certificate and a scary warning to click through on six phones on a Friday morning, or a real
certificate for a hostname that resolves to a DHCP address that moves. So: the *data* lives in
IndexedDB and is safe across anything, the *page* does not survive a reload out of range, and
both the README and the phone's own offline screen say so in as many words.

---

## Battery

A phone runs this for a ten-hour competition day on one charge, and the hub laptop runs the
whole event off its own battery next to the field. Nothing about that is free, and the app
used to spend heavily on things nobody had asked it to do.

### The screen is the battery

Everything else on a phone put together is a rounding error next to the panel being lit. So
the screen is the one thing deliberately **not** given back: the standby screen holds the phone
awake all day, because a scout has to see the HUD open by itself when the match takes the
field, and a sleeping phone cannot show that. Once the screen sleeps the browser suspends the
page — it cannot even buzz.

What it does instead is stop being *bright*. After a minute with nobody touching it, the
standby screen goes flat black: the two full-viewport red gradients, the scanline overlay, the
panel glow and every soft-shadow halo come off, leaving the countdown, the robot and the seat
legible in grey. On the OLED panels these phones have, a black pixel is close to an off pixel.
Any touch brings it back, so does the match arming, and so does the countdown coming inside a
minute — so the screen is already bright before the robot is on the field.

This is worth much less on an LCD phone, where the backlight costs the same whatever it is
showing. It is not worth nothing there — the work below is saved either way.

### Work nobody asked for

The rest was straightforward waste, and it is worth naming because the shapes recur:

- **A render loop instead of rendering on change.** The phone repainted five times a second
  whatever screen it was on. On standby — where it sits for the whole day between matches —
  that meant tearing down and rebuilding a dozen DOM nodes and their click handlers, five times
  a second, for hours. Measured on the seeded demo event: **2472 DOM mutations in twenty
  seconds, now 20.** The live screen kept its 200ms tick; it is the match clock, it runs two
  and a half minutes at a time, and precision there is worth more than the saving.
- **A broadcast that fired whether or not there was news.** The hub told every device in the
  building that Nexus had spoken every twenty seconds, because it keyed on Nexus's own
  timestamp, which advances on every poll. Each one cost six fetches and a full DOM rebuild on
  every dashboard, a whole `/api/state` and a ~60 KB IndexedDB rewrite on every phone, and a
  complete SVG rebuild on the pit tablet. It is now guarded on the payload actually differing,
  the way its four sibling payloads always were.
- **Polling with nothing to poll for.** No endpoint could say "nothing has changed", so every
  poll re-sent — and re-*built* — the whole event. See the ETags above.
- **Recomputing the same answer.** `analytics.event_summary` walks every scouting row and every
  match × alliance × robot. It ran per request, per export, per mirror push, and once per
  keystroke in the picklist search box, which reaches it through the AI panel. It is memoized
  on the write counters now, so it runs once per change instead of once per reader.
- **Timers that ran when nobody was looking.** There was not one `clearInterval` in the client
  and a single `document.hidden` check in the whole codebase. A backgrounded dashboard polled
  and rebuilt ten panes it was not showing. `web/js/timers.js` is the shared answer: intervals
  that stop while the page is hidden and catch up once, jittered, on the way back.
- **Drawing panes nobody could see.** Tabs are switched with a class, so all ten were being
  rebuilt on every refresh — scatter plots, team tables, the log listing — while nine were
  `display:none`. Only the visible tab is drawn; the others are marked dirty and drawn on the
  way in, from data already in hand.
- **A retry that never backed off.** A phone out of range retried on a flat eight seconds
  forever, and once it had been away thirty seconds it swept 254 hosts × two subnets on *every*
  one of those ticks. An hour out of range cost roughly a quarter of a million probe requests.
  It now backs off 8 → 15 → 30 → 60 seconds and sweeps once per offline episode.

One thing deliberately left alone: the phone buzzes twice per shooting run, on the hold and on
the release. The second one is what tells a scout the run was actually recorded — the interval
is discarded below 0.15s — and a day of them adds up to about nine seconds of motor time.
