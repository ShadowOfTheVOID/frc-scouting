# What this app does

Every screen and every field, in order. This is the reference — hand it to a new strategy
student, or read it to see what the app covers without running it.

For **how to get through a competition day**, read the [README](../README.md) instead. For
**why the tricky parts work the way they do** — the fuel solver, the match clock, how accurate
any of it is — read [how-it-works.md](how-it-works.md). This file deliberately does not repeat
either.

---

## The things you open

One Python process serves all of them. Nothing is installed on any phone.

| Address | Who opens it | What it is |
|---|---|---|
| `/scout` | six scouts, on phones | The match HUD. The only screen a scout ever needs. |
| `/dashboard` | scout lead, strategy | Seven tabs of everything the hub knows. |
| `/pit` | pit scouts, on phones | The pit map, and a form per robot. |
| `/` | the hub laptop only | Event key, API keys, passcode. Refuses to open from a phone. |
| `/join` | the hub laptop screen | A QR code per network address. Scouts point a camera at it. |
| `/picklist/print` | the hub laptop | Paper fallback for alliance selection. |

---

## The scout phone

Landscape, two thumbs, six screens. The app moves between them on its own — a scout never
navigates.

### TAKE A SEAT

Where a scout starts, once, at the beginning of the day.

- **Initials**, typed once and remembered on that phone.
- **Six station buttons** — RED 1/2/3, BLUE 1/2/3. The scout taps the one matching the sign
  above their chair. There is no field map on purpose: nothing to mirror, nothing to get
  backwards.
- Stations already claimed show **the other scout's initials**; free ones show **OPEN** in
  green, so a double-booked chair is visible before it costs a match.
- The footer shows which hub was found and which event is loaded.

Claiming a station takes it from whoever held it. Their phone is told immediately and stops —
see [BUMPED](#bumped).

### STANDBY

Between matches. Shows a countdown to the next match, the robot this scout will watch next,
and everything logged so far today.

**Before the buzzer** — two rows of one-tap chips in the right-hand column:

- **WHERE DO THEY START** — LEFT / CENTRE / RIGHT.
- **FUEL LOADED BEFORE THE BUZZER** — `0` to the game's `preloadMax` (currently 8, from
  `web/rules2026.json`).

Both are facts the scout can read off the field while the robot lines up, which is exactly why
they live here. During a match the left thumb owns the rate ladder and the right thumb owns the
hold pad, so there is no thumb free for anything new — every field added since has gone either
before the match or after the buzzer.

Leaving either blank is fine, and is not the same as answering zero. An unanswered preload is
reported as unknown rather than dragging a team's average down.

Also on this screen:

- **WHAT YOU LOGGED TODAY** — the last few matches, each marked `SENT` or `QUEUED`, and
  `reconciled` once the hub has matched that entry against the official result.
- **FIX A PAST MATCH** — reopens the after-the-buzzer screen for the most recent entry.
- **HAND OVER** — a new person takes this chair on this phone. Anything half-entered is banked
  under the outgoing scout's name first.

The screen keeps itself awake and jumps into the HUD by itself when the match takes the field.

### LIVE MATCH

The only screen that matters, and it has two controls.

- **Left thumb — the rate ladder.** `DUMPING` / `STEADY` / `A TRICKLE`. How hard the robot is
  shooting right now.
- **Right thumb — the hold pad.** Held down for as long as the robot is actually shooting.
  That is the whole job.

Everything else on the screen is read-only feedback: a running ball estimate, the shift strip
showing whether this alliance's hub is live or dead right now, and a bar of every run logged so
far.

The first tap on the pad also **starts the match clock for everyone watching that match** — the
other five phones adopt the same timeline and show `CLOCK FROM AK` so nobody wonders whose
clock they are on. Tapping late costs nothing; the hub re-anchors afterwards from the official
start time.

Two smaller pads:

- **FEEDING** and **DEFENDING** — held the same way as the main pad, recording when the robot
  was doing something other than scoring.
- **UNDO** takes back the most recently finished run of any kind — shooting, feeding or
  defending.
- **CLIMB** cycles `None → L1 → L2 → L3`. During auto it is a single toggle, because the auto
  tower is Level 1 only.

### AFTER THE BUZZER

Reached automatically when the match clock runs out. Everything here is a tap.

- **HOW WAS THEIR DRIVING** — rough / okay / solid / great / best.
- **DID THEY GET IN THE WAY** — not at all / a little / some / a lot.
- **ANYTHING GO WRONG** — `STOPPED MOVING`, `TIPPED`, `NO-SHOW`, `LOTS OF FOULS`,
  `AUTO DID NOTHING`. The last one is deliberately distinct from the first two: the robot
  turned up and moved, but auto did nothing.
- **WHO WERE THEY BLOCKING** — three chips, one per opposing robot. **Only appears if this
  scout actually logged defence**, so a scout who logged none never sees the question.
- **WHERE DID THEY START** — the same picker as standby, mirrored here for a scout who was
  thrown straight into the HUD by a match already on the field.
- **A note** — one line of free text.
- **SEND IT IN** — saves, sends if the hub is reachable, and loads the next match.

### OFFLINE

Shown when the hub cannot be reached. It exists to say *nothing is wrong* — the phone keeps
working and everything is saved locally.

- A list of matches waiting to send.
- **SAVE A BACKUP FILE** writes the queue out as JSON, for a phone that is truly stuck.
- **TRY AGAIN NOW** re-runs discovery.

### BUMPED

Shown when another phone claims this scout's chair. The clock stops so two people never log the
same robot. Offers **IT'S STILL MY CHAIR** or **PICK A DIFFERENT STATION**, and lists what this
phone had already saved.

### Practice mode

`/scout?practice` runs the real HUD against a fake match. Nothing is saved and no hub state is
touched. Two fake matches is enough to train someone.

---

## The pit app

`/pit`, on a phone. Two views of the same thing, toggled at the top.

- **MAP** — the actual venue floor plan from Nexus, drawn to scale, with walls, walkway arrows
  and area labels. Each pit is coloured by whether it has been scouted. Tap one to open the
  form. This is the point of the app: a pit scout should see where to walk next rather than
  read team numbers off a list.
- **LIST** — the same robots, sorted unscouted-first and then by pit address, so the list reads
  as a walking order. Works at events with no pit map.

The form per robot:

| Field | Options |
|---|---|
| Drivetrain | swerve / tank / mecanum / other |
| Shooter | drum / flywheel / dump / none |
| Max climb | L3 / L2 / L1 / none |
| Can stockpile | yes / some / no |
| Ground pickup | yes / no |
| Autos | free text |
| Weight | number |
| Notes | free text |
| Photos | any number, from the camera |

Photos are resized on the phone before sending — a 12MP pit photo is not worth 4MB on a venue
hotspot. Inspection status and pit address come from Nexus when a key is set. A progress bar
tracks how much of the field is done.

---

## The dashboard

`/dashboard`, on any laptop or tablet on the network. Seven tabs, plus two reached by clicking
through.

### CREW

**The tab to leave open during quals.** It answers the only live question: is data coming in,
and if not, who do I go talk to?

It says so in plain words — `RED 2 — nobody seated, those robots are unwatched`, `AK on RED 1 —
app is not open on their phone`, `CJ on BLUE 3 — gone quiet 6m ago, check their wifi` — and one
green line when everything is fine. **THIS MATCH** lists the six robots about to play and who
is watching each, so an unwatched robot is obvious before the match rather than after. **FREE**
releases a chair when someone walks off.

### LIVE

On-field and queuing matches, a top-twelve ranking, the picklist's top eight, and our own
ranking-point outlook — rank, record, RP per match, matches left, and the best case if we win
out with every bonus. That last one is a ceiling, not a forecast, and says so.

### TEAMS

Every scouted team, sortable on any column: rank, fuel per match with its band, climb, EPA,
tower points, stockpile rate, wasted fuel, died rate, driver rating, **Lovat** fuel with the
number of matches behind it, matches scouted, and a
**CONFIDENCE** bar. Confidence is a property of the data on that robot — how many matches, and
how tight the band is relative to the mean — not a judgement of anybody. Click a row for team
detail.

### PICKLIST

Two boards, because alliance selection asks two questions. **FIRST PICK** ranks the best robot
left. **SECOND PICK** ranks the best *complement* to the two you already have, where defence,
feeding and not breaking down count for far more. Each has its own weights and its own hand
ordering.

Weights are sliders. Drag a row to move a team by hand — the first drag freezes the board as
you see it so new match data stops reordering it under you, and **RESET TO COMPUTED** hands it
back. Teams are crossed off automatically as they are picked. **DNP** flags a team as
do-not-pick.

Anyone may look. Changing it needs the strategy passcode.

**WHY THIS ORDER** sits above the board and is generated (see [AI](#ai) below). It explains the
order the weights and your dragging already produced — it never reorders anything, and it is
labelled with the model that wrote it.

### HEALTH

How much to trust the numbers.

- **COVERAGE**, **MEDIAN ERROR**, **FLAGGED**, **CALIBRATED** tiles.
- **SCOUTS vs TBA** — one row per played alliance-match, showing what the scouting said the
  match was worth against the official result, colour-coded by how far off it was.
- **ACCURACY** — median error, worst 10%, whether the scouting is running **hot or cold**
  (systematically over- or under-calling shooting), and how often the projection picked the
  actual winner.
- **NEED RECONCILE** — matches the hub could not line up, with the reason.
- **CALIBRATION** — the fitted rate multipliers and how many official windows they came from.

The comparison uses the **raw** scout estimate, not the solved numbers. That distinction is the
whole reason the panel means anything — see [Where the numbers come
from](#where-the-numbers-come-from).

A **PER-SCOUT** panel sits below it, visible only to the lead. See [Who can see
what](#who-can-see-what).

### SEATS

Which station is claimed for the next few matches, who is on the roster, and a warning for any
empty station.

### SERVER

Diagnostics — uptime, memory, writes per minute, connected devices, per-service status for
every data source, the event log, every network address the hub is reachable on — and all the
export links.

### MATCH PREVIEW

Reached from the tab bar. Both alliances in the next match side by side: projected fuel and
points, win probability with the margin it came from, and each robot's fuel and climb.

Two warnings fire here:

- **AUTO** — two robots on the same alliance that habitually start in the same zone. Worth
  asking about before the match rather than watching it happen.
- **EXPECT DEFENCE** — an opponent with a logged history of defending someone in this lineup.

### TEAM DETAIL

Reached by clicking a team. Fuel, climb, tower points, reliability, EPA and OPR as tiles; then
what scouts saw — stockpiling, wasted fuel, feeding, defence in **both** directions (who this
robot defends, and who defends it), usual start zone, auto failures, fouls, driver rating,
average preload — then **FROM LOVAT** if other teams scouted them, then **WHAT THE NOTES ADD UP
TO** (generated, see [AI](#ai)), then every note anyone typed about them, and their pit scouting
with photos.

Lovat notes appear in the same list as ours, dimmed and tagged `· lovat`, so you can always see
whose scout wrote a line.

---

## Where the numbers come from

Five sources, kept deliberately separate, because mixing them is how a picklist ends up
confidently wrong. `server/analytics.py` enforces the split.

| Block | Source | Trust |
|---|---|---|
| `exact` | The Blue Alliance | Exact. Rank, record, ranking points, OPR, per-robot climb per match, auto climb, tower points. |
| `estimated` | our solver | Estimated, **always** carries a band. Fuel per match, consistency, cycle rate. |
| `observed` | the scouts | Reliable in kind, not in magnitude — yes/no answers, ratings, counts. |
| `epa` | Statbotics | An independent outside read, which is why it earns a place beside a number we produced ourselves. |
| `lovat` | other teams' scouts, via [lovat.app](https://lovat.app) | Somebody else's scouting, unverified, collected to somebody else's standard. Shown for comparison and **fed into nothing** — not the solver, not the picklist, not any other block. |

A missing source reads as **unknown**, never as zero. No API key means a blank column, not a
row of noughts.

Fuel is the only estimated number, and it is never shown as a bare integer. The picklist leads
with exact fields and uses fuel to break ties.

### Why SCOUTS vs TBA uses the raw estimate

This one catches people out. The solver **distributes** TBA's official per-window totals across
three robots — so if you add the solved numbers back up per alliance, you reproduce TBA
exactly, by construction, no matter how wrong the scouts were. Comparing solved fuel against
TBA would be the solver marking its own homework, and would always score 100%.

So the accuracy panel uses the *raw* scout estimate instead — duration × intensity over the
intervals alone, restricted to windows where that alliance's hub was live — which never sees
TBA at all. That is a real comparison, and it is what makes "we are running 10% hot today" a
statement about the scouting rather than about arithmetic.

The maths behind the solver itself is in [how-it-works.md](how-it-works.md).

---

## AI

Optional, off unless you set a provider and a key in Setup. Three panels, all of them text
beside the numbers and never a number of their own:

| Panel | Where | What it does |
|---|---|---|
| **WHAT THE NOTES ADD UP TO** | TEAM DETAIL | Reads that team's notes — ours and Lovat's — and names the recurring themes, citing the matches and scouts behind each, and flagging where two scouts disagree. |
| **WHY THIS ORDER** | PICKLIST | One sentence per team explaining the board you already have, then a first-pick and second-pick argument. |
| **ASK THE DATA** | CREW, in the side column | One question, answered from the team records on this hub. |

### What it is allowed to do

The whole point is to surface context that is **already in the data** — which match a claim
rests on, which scout said it, which block a number came from — and to add nothing. That is
enforced in four places rather than merely hoped for:

- **The prompt is closed.** Every system prompt (`server/ai.py`, `GROUND_RULES`) tells the model
  it has no knowledge of these teams beyond the JSON in front of it, must never state a number
  that is not in that JSON, must cite the block or the match-and-scout behind every claim, must
  say "not enough data" rather than fill a gap, and must report a disagreement between scouts
  rather than resolve it.
- **The payload is small and labelled.** The hub sends a trimmed per-team record with the block
  names attached, never the raw entry table.
- **Nothing is written back.** Answers are cached under an `ai:` key as generated text. No AI
  output reaches the solver, the bands, the picklist order or a team record. Read it against the
  panel beside it — that is what the citations are for.
- **It is always labelled** with the model that wrote it and when.

### What it costs, and who can spend it

Generating always takes a button press. Opening a page only ever reads the cache, so walking
the dashboard costs nothing. A cached answer regenerates by itself only when the data behind it
changes; **SUMMARISE** / **EXPLAIN** forces a fresh one.

The routes are held to a stricter lock than the picklist, because they spend real money: you
need the strategy passcode *and* a valid token, **or** you are sitting at the hub machine. With
no passcode configured that means the hub machine only. There is also a per-event ceiling
(250 answers by default, `aiCallLimit` to change it) and the SERVER tab shows the count.

Cached answers live in the database, so once written they still read with the network gone.

---

## Who can see what

Three levels, and the split is deliberate.

**Open to anyone on the network.** The schedule, every team's numbers, the picklist (to read),
crew status, diagnostics, exports. A dashboard in the stands is useful and nothing on it is
sensitive.

**Needs the strategy passcode.** Changing the picklist — weights, hand ordering, DNP flags — so
a bored student cannot flag a team as do-not-pick an hour before alliance selection. And the
**per-scout quality panel** on HEALTH.

**The hub laptop only.** API keys and event settings. Open `/` from a phone and it politely
sends you to the laptop. This needs no passcode to enforce: whoever is sitting at the machine
is the person who should be configuring it.

### Why per-scout scores are not public

Every scout's reconciliation rate used to be on the dashboard, named, in three places, readable
by anything on the venue wifi. It is coaching material for the lead — someone to go and stand
next to for a match — and it is a bad thing to put on a screen the whole room can see.

It also bought nothing analytically: nothing anywhere downweights a scout's contribution based
on that score. It was a personal scoreboard with no offsetting benefit.

So the split is: **operational status stays public** — whose phone is dark, which station has
gone quiet — because that is about equipment and the lead has to act on it immediately.
**Quality scoring is lead-only**, released by the strategy passcode or by sitting at the hub.
And the public accuracy number, SCOUTS vs TBA, is about the data.

Nothing about a scout's identity leaves the hub. `/api/config` returns booleans for which keys
are set, never a key value, and the passcode is stored only as a salted hash.

---

## When the network goes away

The hub is often at the pit while the scouts are in the stands, so being disconnected is the
normal case, not an error.

**Every entry is written to the phone first** and queued for the hub — nothing is ever typed
straight at the network. The queue collapses to the newest version per match before sending, so
a scout who edits one match ten times costs one row rather than ten. Sending resumes by itself
the moment the hub is reachable; nobody has to do anything.

If a phone cannot find the hub at its last known address, it re-scans the local network. A
scout who is truly stuck can use **SAVE A BACKUP FILE** and hand the file over later.

On the hub: the whole database is snapshotted every ten minutes, keeping the last twelve.
Recovering is copying one file over another. **JSON export** round-trips through **import**
under the same last-write-wins rule, so re-importing the same file is a no-op and merging two
laptops is safe.

**One known limit.** The hub serves plain HTTP on a LAN address, which browsers will not run a
service worker on, so the app shell cannot be cached. A scout who force-reloads the page while
out of range cannot load it again until they are back in range. Their data is safe either way —
it is in the phone's database, not the page. Tell scouts not to reload.

---

## Setup reference

### Keys and settings

Entered at `/` on the hub laptop. All keys are free and all are optional.

| Setting | What it does |
|---|---|
| **Event key** | e.g. `2026casf`. The same code on frc.events, The Blue Alliance and Nexus. |
| **Event level** | regional / dcmp / champs — sets the ranking-point thresholds. |
| **Our team** | Highlights us in every table and drives the RP outlook. |
| **Strategy passcode** | Gates picklist editing and the per-scout panel. Blank means open. |
| **The Blue Alliance** | Official results, per-robot climb, rankings, OPR. The fuel solver's only source. |
| **Nexus** | Live queueing and match status, pit map, pit addresses, inspection, alliance selection. |
| **Nexus webhook token** | Only if you registered a push webhook. |
| **FRC Events** | The official result a few minutes before TBA posts it. Does not feed the solver. |
| **Lovat API key** | Other teams' scouting for this event. Your scouting lead makes one in the Lovat Dashboard under Settings → API keys; it starts `lvt-`, and your team has to be verified on Lovat first. Polled once every five minutes — Lovat allows one request every three seconds per key, so the hub stays well inside it. The export is scoped to what your Lovat account is allowed to see, so a short list is a setting on their side, not a failure on ours. |
| **AI provider / key / model** | Claude, OpenAI or Gemini. Turns on the three generated panels below. Blank model uses the provider's default. |
| Statbotics | EPA. No key needed. |

### Command line

```
python3 server/hub.py [--port 8080] [--db data/scouting.db] [--no-mdns] [--allow-remote-config]
```

`--no-mdns` skips answering to `scout.local`. `--allow-remote-config` lets any device on the
network change hub settings — off by default, and rarely what you want.

```
python3 server/seed_demo.py [--db data/demo.db] [--event 2026demo] [--teams 31] [--matches 40] [--via-nexus]
```

`--via-nexus` feeds the schedule in through the Nexus ingest path rather than writing
TBA-shaped rows directly. That is what a real event does, and it is the path that a bug once
hid in, so it is worth exercising.

---

## HTTP API

Everything is JSON over plain HTTP. Useful if you want to drive another display off the hub.

| Route | Method | Notes |
|---|---|---|
| `/api/config` | GET | Event, which keys are set (booleans only), calibration, server time. |
| `/api/config` | POST | Hub machine only. |
| `/api/state` | GET | Schedule, teams, matches, pit data, seats, flags, rankings, EPA. |
| `/api/analytics` | GET | Per-team aggregates, coverage, score report. `scouts[]` only for the lead. |
| `/api/scout`, `/api/pit` | GET | Raw entries. |
| `/api/crew`, `/api/seats`, `/api/seatlog` | GET | Who is where. |
| `/api/diag` | GET | Server diagnostics and event log. |
| `/api/picklist` | GET / POST | Reading is open; writing needs the passcode. |
| `/api/unlock` | POST | Exchange the passcode for a token. |
| `/api/sync` | POST | What phones send. Last-write-wins on `updatedAt`. |
| `/api/import` / `/api/export` | POST / GET | Whole-event JSON, idempotent. |
| `/api/export.csv?table=` | GET | `teams`, `scout` or `pit`. |
| `/api/seat`, `/api/unseat`, `/api/matchstart` | POST | Station claims and the shared clock. |
| `/api/photo/<id>`, `/api/photos` | GET | Pit photos. |
| `/api/refresh`, `/api/resolve` | POST | Force a poll, or re-solve one match. |
| `/api/ai/notes/<team>` | POST | Note digest. `{"peek":true}` reads the cache without generating; `{"force":true}` regenerates. |
| `/api/ai/picklist` | POST | Rationale for an order you send as `{"order":[team,…]}`. Same `peek` / `force`. |
| `/api/ai/ask` | POST | `{"question":"…"}`. Never cached. |
| `/api/discover` | GET | Every address the hub is reachable on. |
| `/api/nexus/webhook` | POST | Nexus push, verified by `Nexus-Token`. |
| `/api/stream` | GET | Server-sent events. |

The AI routes need the strategy passcode or the hub machine, and answer `{"configured": false}`
rather than an error when no provider is set.

The stream pushes `nexus`, `matchStatus`, `matchStart`, `results`, `earlyScores`, `scout`,
`solved`, `seats`, `picklist`, `rankings`, `epa`, `lovat` and `calibration`, so a client can
react instead of polling.
