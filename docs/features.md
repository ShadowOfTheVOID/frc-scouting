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

And one that is not this process at all: the **off-site mirror**, a separate website on a host
you already own, which the hub pushes a copy of the event to about once a minute. It runs
somewhere else, holds nothing but what it is sent, and is read-only. See below.

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
  green, so a double-booked chair is visible before it costs a match. The chair this phone
  itself is holding reads **THIS PHONE**.
- The footer shows which hub was found and which event is loaded.

Claiming a station takes it from whoever held it. Their phone is told immediately and stops —
see [BUMPED](#bumped).

### STANDBY

Between matches. Shows a countdown to the next match, the robot this scout will watch next,
and everything logged so far today.

**The screen arms itself.** When Nexus reports the match `On field`, the hub pushes that to
every phone and each one whose seat is in that match jumps straight to the live screen — the
scout never picks a match. That is what the Nexus key buys, and why it is the one to set.

**THEY'RE ON THE FIELD** is the manual way through, for when nothing arms it: no Nexus key, a
volunteer who has not clicked yet, or no route to the hub at all. It opens the match screen on
the robot this seat watches next — read from the schedule cached on the phone, so it works with
the network down. It does not start the clock; the pad still does that. A phone that has never
reached the hub has no schedule, and the button asks for the number off the robot instead: that
entry keeps everything the scout saw, but the hub cannot line it up with an official match, so
it does not feed the fuel solver.

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
- **THEY'RE ON THE FIELD** opens the match screen with no hub at all — the same control as on
  standby, put here because this is where a phone with no network actually lands. It is what
  makes [Plan B](../README.md#plan-b--no-usable-network) something a scout can actually do.
- **SAVE A BACKUP FILE** writes the queue out as JSON, for a phone that is truly stuck.
- **TRY AGAIN NOW** re-runs discovery.

### BUMPED

Shown when this scout's chair goes away — another phone claimed it, or the lead freed it on the
crew board. The clock stops so two people never log the same robot. Offers **IT'S STILL MY
CHAIR** or **PICK A DIFFERENT STATION**, and lists what this phone had already saved. Reopening
the app does not get past it: the phone asks the hub who is in the chair before it scouts, and
another scout starting a match will not pull it back into the HUD.

**IT'S STILL MY CHAIR only appears between matches.** While a match is being played the button
is replaced by the time left until the buzzer. Taking a chair back mid-match stops whoever is
sitting in it, and then two people have half a match each and neither half is worth having —
so the argument about who is in that chair waits for the buzzer, which is where it belongs.
**PICK A DIFFERENT STATION** stays available throughout: giving a chair up is never the
dangerous direction.

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

Photos are resized on the phone before sending — a 12MP pit photo is not worth 4MB on venue
wifi. Inspection status and pit address come from Nexus when a key is set. A progress bar
tracks how much of the field is done.

---

## The dashboard

`/dashboard`, on any laptop or tablet on the network. Nine tabs, plus TEAM DETAIL, reached by
clicking a team.

### CREW

**The tab to leave open during quals.** It answers the only live question: is data coming in,
and if not, who do I go talk to?

It says so in plain words — `RED 2 — nobody seated, those robots are unwatched`, `AK on RED 1 —
app is not open on their phone`, `CJ on BLUE 3 — gone quiet 6m ago, check their wifi` — and one
green line when everything is fine. **THIS MATCH** lists the six robots about to play and who
is watching each, so an unwatched robot is obvious before the match rather than after. **FREE**
releases a chair when someone walks off; that phone is told at once and stops, so a chair you
have freed is never still logging.

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

### GRAPHS

Everything the other tabs average, drawn over time. Three charts, all scoped by one row of team
chips at the top — up to six teams, and a team keeps its colour for as long as it is selected
so the legend never re-shuffles under the reader.

- **FUEL BY MATCH** — one line per selected team across the whole schedule, from the solver.
  The question a table cannot answer: is this robot getting better, and did something change
  after lunch on Saturday.
- **DEFENCE — PLAYED AND FACED** — seconds of contact per match in both directions, plus
  Lovat's own defence seconds where they have them. *Faced* counts only defence a scout
  attributed to a named robot, so it is a floor rather than a total, and the caption says so.
- **WHERE THE SOURCES DISAGREE** — two scatter plots, one dot per team. Our fuel against
  Lovat's fuel, with a dashed y=x line for agreement; and our fuel against Statbotics EPA,
  with no such line, because they are different units and drawing one would invent a
  relationship. A team far off the agreement line is one the two sets of scouts read
  differently — usually a robot one of them has seen fewer times.

The side pane counts how many teams each source has anything for, lists the busiest defenders
and the most-defended robots, and — from Lovat only — how many seconds into a match each robot
leaves to go and climb. Our own scouting cannot produce that number: a scout with two thumbs
cannot time a climb.

Every chart carries a hover readout and a **table view** underneath it, so no value is
reachable only by hovering. A gap in a line is a match with no measurement; it is never drawn
as a zero.

### PICKLIST

Two boards, because alliance selection asks two questions. **FIRST PICK** ranks the best robot
left. **SECOND PICK** ranks the best *complement* to the two you already have, where defence,
feeding and not breaking down count for far more. Each has its own weights and its own hand
ordering.

Weights are sliders. Drag a row to move a team by hand — the first drag freezes the board as
you see it so new match data stops reordering it under you, and **RESET TO COMPUTED** hands it
back. Teams are crossed off automatically as they are picked. **DNP** flags a team as
do-not-pick.

A row of filters sits over the board: free text against team number and name, a minimum climb,
a minimum **fuel per second**, a minimum number of matches our scouts watched, the start zone a
robot habitually uses, and chips for HIDE TAKEN, HIDE DNP, RELIABLE (died or no-showed in under
a tenth of its matches), AUTO WORKS (auto did nothing in under a fifth of them), NO AUTO CLASH,
DEFENDS (rated 3+ or has actually spent seconds on it) and STOCKPILES (in at least half its
matches).

Fuel per second is `estimated.cycleRate` — fuel over active seconds, the rate behind the volume.
It is estimated, because it divides a solved number, so it sits beside the fuel it came from on
each row and never above an exact field. A robot nobody could time never clears a floor: no
measured rate is not a fast rate.

NO AUTO CLASH hides robots whose habitual start zone is our own team's, using the same
`observed.startZone` the match preview's auto-clash callout reads, so the two cannot disagree.
It needs OUR TEAM set and scouted; without that the chip is disabled and says why, and the
filter is inert rather than guessing.

They are a view and nothing more.
Rank, score, hand order, the rationale below and the printed sheet are all computed over the
whole board, so a filtered list is the same list with rows hidden — the number beside a team
stays the rank it holds among everyone. Filters are per-browser (`localStorage`), deliberately
not hub state: the order has to be shared, but one reader's narrowing must never reach the
laptop running alliance selection.

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

The service list carries one line that is not a data source: `off-site mirror` is the hub
talking *outwards*. `IDLE` means no mirror is configured, `RUNNING` names the last copy it
pushed, and `RETRYING` means it has stopped working — with the reason on the setup page.
Nothing at the venue degrades when it is red.

### MATCH

Both alliances side by side: projected fuel and points, win probability with the margin it came
from, and each robot's fuel and climb. It follows the field — on-field, then queuing, then the
next unplayed match — until you pick a specific match from the dropdown, after which it holds
still so a refresh does not move it while you are reading.

Two warnings fire here:

- **AUTO** — two robots on the same alliance that habitually start in the same zone. Worth
  asking about before the match rather than watching it happen.
- **EXPECT DEFENCE** — an opponent with a logged history of defending someone in this lineup.

**HOW TO PLAY IT** is generated (see [AI](#ai)) and only appears with a model configured. Four
labelled lines: how the alliances compare, the one opposing robot that decides the match, who
to defend, and the risk that would make the read wrong. The projection is computed by
`analytics.match_projection` and handed to the model already summed — the ground rules forbid
it doing arithmetic, and an alliance total it worked out itself is a number nobody can check.

### TEAM DETAIL

Reached by clicking a team. Fuel — with its band and, where we could time it, fuel per second —
climb, tower points, reliability, EPA and OPR as tiles; then
what scouts saw — stockpiling, wasted fuel, feeding, defence in **both** directions (who this
robot defends, and who defends it), usual start zone, auto failures, fouls, driver rating,
average preload — then two charts, then **FROM LOVAT** if other teams scouted them, then **WHAT
THE NOTES ADD UP TO** (generated, see [AI](#ai)), then every note anyone typed about them, and
their pit scouting with photos.

The two charts are this robot alone, match by match:

- **FUEL BY MATCH** — the solver's number with its single-match uncertainty shaded around it,
  and Lovat's count of the same robot drawn beside it where they have one. The x axis is this
  robot's own matches, so a gap really is a missing measurement and the line breaks rather than
  bridging it.
- **DEFENCE BY MATCH** — seconds played, seconds taken, and Lovat's seconds, whichever of the
  three anyone recorded. Zero is a scout watching and seeing no defence; a gap is no scout
  entry at all.

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

### What Lovat actually gives us

Their export is one row per team per match, and every column of it is kept. Most are averaged
into the team's `lovat` block; the ones a chart needs are also kept per row, because an average
cannot show that a robot's fuel collapsed after Q30 and that is the shape worth walking to the
pits about.

| What | Notes |
|---|---|
| fuel, throughput, fuel per second, accuracy, volleys | their scouts' count of the same robot, next to ours |
| feeding — seconds, rate, feeds per match, balls fed | |
| defence — total, contact, camping, effectiveness | |
| **climb start time**, per level, and auto climb start | the second the robot left to go and climb. Our scouting cannot produce this: a scout with two thumbs cannot time a climb |
| climbs and climb rate per level, best climb | `L2`, `Level 2` and `2` all normalise onto our vocabulary; a label we cannot read is unknown, never a failed climb |
| points — total, auto, teleop — and driver ability | |
| beached, scores-while-moving, disrupts, field traversal | booleans, counted as a rate over the rows that answered |
| outpost intakes, robot roles, feeder types, intake type | |
| scouter names and free-text notes | notes are shown beside ours, tagged `· lovat` |

Two things their exporter does that cannot be undone on our side: commas inside free text were
replaced with semicolons before export, and playoff rows carry a label (`SF2-1`) that does not
map onto a qualification match key. Playoff rows are counted and listed as `unmatched` rather
than dropped or, worse, joined onto the qual match of the same number.

The whole file is also downloadable as its own CSV from the SERVER tab — separate from the team
summary on purpose, because a spreadsheet that mixes it into our columns is how it ends up
quoted back as ours.

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

Optional, off unless you set a provider and a key in Setup. Four panels, all of them text
beside the numbers and never a number of their own:

| Panel | Where | What it does |
|---|---|---|
| **WHAT THE NOTES ADD UP TO** | TEAM DETAIL | Reads that team's notes — ours and Lovat's — and names the recurring themes, citing the matches and scouts behind each, and flagging where two scouts disagree. |
| **HOW TO PLAY IT** | MATCH | Four lines on one match: how the alliances compare, the opposing robot that decides it, who to defend, and the risk that would make the read wrong. |
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
  names attached, never the raw entry table. Where a panel needs a total — the alliance
  projection on a match read — the hub computes it and sends it, rather than leaving the model
  to add three numbers up in prose where nobody can check the working.
- **Nothing is written back.** Answers are cached under an `ai:` key as generated text. No AI
  output reaches the solver, the bands, the picklist order or a team record. Read it against the
  panel beside it — that is what the citations are for.
- **It is always labelled** with the model that wrote it and when.

### Why an empty panel is never just "unavailable"

Every model on the list reasons before it answers, and those reasoning tokens come out of the
same budget as the answer. Left at its default a model can spend the entire allowance thinking
and return nothing — which, reported as "unreachable", would send you hunting for a network
fault that is not there. So the hub turns reasoning down to its low setting on every provider
(`output_config.effort`, `reasoning_effort`, `thinkingConfig.thinkingLevel` — one per vendor,
and each rejects the other two), gives the budget room, and reports the real cause: **the
answer ran out of room** (press again), **the model declined to answer that**, or **the model
could not be reached**, which is the only one that means the network.

### What it costs, and who can spend it

Generating always takes a button press. Opening a page only ever reads the cache, so walking
the dashboard costs nothing. A cached answer regenerates by itself only when the data behind it
changes; **SUMMARISE** / **EXPLAIN** forces a fresh one.

An answer is a few thousand tokens. Across a whole regional that lands somewhere between a few
cents and a couple of dollars depending on which model you picked — the list spans a 50×
price range, and the cheapest end handles a note digest perfectly well.

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
are set, never a key value, and the passcode is stored only as a salted hash. The two routes
that check and test keys are on the same boundary as the settings they serve: the hub machine
only.

### And on the mirror

The off-site copy has its own two levels, and they are separate strings on purpose. The **push
key** is the write key: the hub proves it is the hub, and it lives in one settings field on one
laptop. The **view passcode** is what a person types to read the site, and it gets shared around
a team over a weekend. If one string did both jobs, anyone it reached could overwrite the event.

The mirror is read-only in the strong sense: there is no route on it that changes anything on
the hub, and no API key of any kind is ever sent to it. Per-scout quality scores are not
mirrored at all — the hub's rule above is not relaxed for a public host behind one shared
code.

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

Entered at `/` on the hub laptop. All keys are free. **Nexus is required** in practice — without
it nothing arms the match screen and every scout opens each match by hand. The rest are optional.

| Setting | What it does |
|---|---|
| **Event key** | e.g. `2026casf`. The same code on frc.events, The Blue Alliance and Nexus. Paste the whole address of the event page and the key is taken out of it; capitals and stray spaces are fixed. |
| **Event level** | regional / dcmp / champs — sets the ranking-point thresholds. |
| **Our team** | Highlights us in every table and drives the RP outlook. |
| **Strategy passcode** | Gates picklist editing and the per-scout panel. Blank means open. |
| **The Blue Alliance** | Official results, per-robot climb, rankings, OPR. The fuel solver's only source. |
| **Nexus** — required | Live queueing and match status, pit map, pit addresses, inspection, alliance selection. Its `On field` is what arms the match screen on the phones; without it every scout has to tap **THEY'RE ON THE FIELD** by hand, six times an hour. |
| **Nexus webhook token** | Only if you registered a push webhook. |
| **FRC Events** | The official result a few minutes before TBA posts it. Does not feed the solver. A username and a token, and pasting the joined `username:token` — or the base64 `Basic` blob out of their documentation — into the token box fills in both. |
| **Lovat API key** | Other teams' scouting for this event. Your scouting lead makes one in the Lovat Dashboard under Settings → API keys; it starts `lvt-`, and your team has to be verified on Lovat first. Polled once every five minutes — Lovat allows one request every three seconds per key, so the hub stays well inside it. The export is scoped to what your Lovat account is allowed to see, so a short list is a setting on their side, not a failure on ours. |
| **AI model** | One list, grouped Claude / Gemini / OpenAI, each option priced per million tokens. Picking a model picks the company that makes it, so there is no provider field to get wrong. Starts on **Claude Opus 5**, so pasting a key is enough — you never have to touch the list. *none* turns the three panels below off entirely, and stays off even with a key in the box. **other** takes a typed model id for anything released after this list was written; the name decides where it is sent. |
| **AI key** | The key for whoever makes the model you picked. A key from one of the other two is refused here rather than saved: that mismatch has no symptom anywhere except every AI answer reading *the model could not be reached*. |
| **Mirror address** | Optional. The root of an off-site mirror, e.g. `https://systemoverload.org`. A trailing slash or a pasted `/api/push` is trimmed, and a bare hostname gets `https://`. Blank sends nothing anywhere. |
| **Mirror push key** | Whatever `MIRROR_PUSH_KEY` is on that host. The write key, and not the passcode people type to read the site. |
| Statbotics | EPA. No key needed. |

### What happens to a key on the way in

Every one of these arrives by copy and paste, and what lands in the box is very often not the
key. So the hub takes the wrapping off before it stores anything: the header name (`X-TBA-Auth-Key:`,
`Authorization: Bearer`), the quotes off a code sample, a line break from an email client, a
trailing comma. The box then shows what will actually be saved, with a line underneath saying
what came off — because a key that needed fixing and got fixed silently is a key nobody knows
was wrong.

Four pastes are **refused** rather than stored, each naming what to do instead: a key that
belongs in one of the other boxes (a `lvt-` key in the TBA box), a web address, the example
text, and an AI key from a different company than the model picked above it. Nothing is saved
at all when a box is refused, so a save is a re-press rather than a re-type of eight keys.

Everything else is saved with a **warning** and no argument — a TBA key that is not 64
characters, a Lovat key not starting `lvt-`. A vendor is allowed to change its key format
without a hub refusing to be configured on a Saturday morning.

Two more things on that page:

- **TEST KEYS** asks every vendor whether the key stored here actually works, and is the only
  thing in the app that can tell you. `SET` beside a box has never meant more than "a string is
  stored". Each answer is one line: accepted, rejected, rate-limited, or "we could not reach
  them" — which are four different problems that look identical everywhere else, because at a
  competition a source that is down has to read as *we do not know* rather than as an error.
  The AI key is checked against the vendor's free model list, so it costs nothing. Lovat's 403
  is called what it is: your team is not verified on their side, and a second key will fail the
  same way.
- **FORGET**, beside a box that has something in it, is how a key comes off a hub. A blank box
  means "leave that one alone" — it has to, or changing the event key would mean retyping every
  key on the page.

### Command line

```
python3 server/hub.py [--port 6059] [--db data/scouting.db] [--no-mdns] [--no-poll]
                      [--allow-remote-config]
```

`--no-mdns` skips answering to `scout.local`. `--allow-remote-config` lets any device on the
network change hub settings — off by default, and rarely what you want.

`--no-poll` stops the hub reaching out to Nexus, TBA, FRC Events, Statbotics or Lovat; it still
serves everything already in the database. Use it to look at a saved event without touching the
network. Statbotics is the reason the flag earns its place: it needs no key, so a hub that is
online will ask about whatever event key it holds, and for an event that does not exist — a
demo, a restored snapshot under a placeholder key — it gets a truthful "nothing" back and stores
that over what was there. The hub notes in its log when polling is off, because a source that is
silent and a source that is down must not look the same.

```
python3 server/seed_demo.py [--db data/demo.db] [--event 2026demo] [--teams 31] [--matches 40] [--via-nexus]
```

`--via-nexus` feeds the schedule in through the Nexus ingest path rather than writing
TBA-shaped rows directly. That is what a real event does, and it is the path that a bug once
hid in, so it is worth exercising.

```
MIRROR_PUSH_KEY=... MIRROR_VIEW_PASSCODE=... python3 mirror/server.py
                      [--port 8060] [--bind 127.0.0.1] [--db mirror/data/mirror.db]
                      [--behind-proxy] [--open]
```

The mirror, on the other host. It refuses to start without a push key, and refuses to start
without a read passcode unless you say `--open`. `--bind` defaults to loopback because it is
meant to sit behind something that already has a TLS certificate; `--behind-proxy` is what makes
it believe `X-Forwarded-For`, without which every request looks like it came from `127.0.0.1`
and one wrong passcode would throttle everybody. Full setup in
[mirror/README.md](../mirror/README.md).

---

## The off-site mirror

A second website, on a host the team already has, holding a copy of the event.

The hub is one laptop on venue wifi, behind whatever network the venue runs — nothing on the
internet can reach in and ask it for anything, so the hub pushes and the mirror never asks. Two
things come of that: the event survives the laptop, and anybody with the view passcode can read
the numbers from a phone on cell data, off venue wifi entirely.

**What it carries.** Once a minute: the event, teams, matches, flags, rankings, EPA, Nexus's
alliance and pit-map data, the picklist, the whole analytics payload the dashboard draws from,
every scout and pit record, and the team-summary and Lovat CSVs — the last carried verbatim
rather than rebuilt, so a column cannot mean one thing on the hub and another off-site.

**What it does not.** Nothing is sent when nothing changed: the bundle is hashed without its
timestamp and an unchanged event is skipped, because a venue uplink is shared with a few
thousand people and their phones. A photo crosses once — the mirror answers each push with the
ids it is missing and the hub sends a few per push. Per-scout quality scores are never sent at
all. No API key ever leaves the hub; the mirror has no use for one, since it never calls TBA,
Nexus, Statbotics, Lovat or any model.

**What it shows.** Five tabs, laid out for a phone: TEAMS (the same table as the dashboard, tap
for the detail sheet with pit scouting, photos and scout notes), MATCHES, PICKLIST, HEALTH and
BACKUP. A banner at the top says how old the copy is and turns amber after five minutes and red
after an hour, because a mirror that quietly shows yesterday's numbers as if they were live is
worse than no mirror. Ages are measured against the mirror's clock, not the phone's.

**Getting the event back.** BACKUP → DOWNLOAD JSON is the same file `/api/export` produces, so
it imports straight into a fresh hub under the same last-write-wins rule — re-importing is a
no-op, so it is always safe to try twice. The mirror keeps the last sixty distinct copies and
every one is a download link on that tab, because *"the database looks wrong"* is one of the
failures this exists for.

### Its own HTTP surface

| Route | Method | Notes |
|---|---|---|
| `/api/status` | GET | Open. Whether it is locked, and how long ago it last heard from a hub. Names no event. |
| `/api/unlock` | POST | Passcode for a token, 16 hours. Eight wrong tries from one address locks it out for ten minutes. |
| `/api/events` | GET | What events are mirrored, and when each last arrived. |
| `/api/snapshot?event=&rev=` | GET | The whole bundle, newest by default. |
| `/api/history?event=` | GET | Every copy held, with the counts that make one choosable. |
| `/api/export?event=&rev=` | GET | A hub import file. |
| `/api/export.csv?event=&table=` | GET | `teams` or `lovat`, as pushed. |
| `/api/photo/<id>` | GET | A mirrored pit photo, served as the type its bytes actually are. |
| `/api/push`, `/api/photos` | POST | The hub only. `X-Mirror-Key`. |

Reads take the token as `X-Mirror-Token`, or as `?t=` for the URLs a browser fetches by itself —
an `<img>` tag and a download link cannot send a header. Request logging is off for that reason.

---

## HTTP API

Everything is JSON over plain HTTP. Useful if you want to drive another display off the hub.

| Route | Method | Notes |
|---|---|---|
| `/api/config` | GET | Event, which keys are set (booleans only), calibration, server time. |
| `/api/config` | POST | Hub machine only. |
| `/api/state` | GET | Schedule, teams, matches, pit data, seats, flags, rankings, EPA. |
| `/api/analytics` | GET | Per-team aggregates, coverage, score report, and `trend[]` — one row per match per team, the series the charts draw. `scouts[]` only for the lead. |
| `/api/scout`, `/api/pit` | GET | Raw entries. |
| `/api/crew`, `/api/seats`, `/api/seatlog` | GET | Who is where. |
| `/api/diag` | GET | Server diagnostics and event log. |
| `/api/picklist` | GET / POST | Reading is open; writing needs the passcode. |
| `/api/unlock` | POST | Exchange the passcode for a token. |
| `/api/sync` | POST | What phones send. Last-write-wins on `updatedAt`. |
| `/api/import` / `/api/export` | POST / GET | Whole-event JSON, idempotent. |
| `/api/export.csv?table=` | GET | `teams`, `lovat`, `scout` or `pit`. |
| `/api/seat`, `/api/unseat`, `/api/matchstart` | POST | Station claims and the shared clock. |
| `/api/photo/<id>`, `/api/photos` | GET | Pit photos. |
| `/api/refresh`, `/api/resolve` | POST | Force a poll, or re-solve one match. |
| `/api/mirror/push` | POST | Push to the off-site mirror now. Hub machine or the strategy passcode. Answers 200 either way, with the reason in the body. |
| `/api/ai/notes/<team>` | POST | Note digest. `{"peek":true}` reads the cache without generating; `{"force":true}` regenerates. |
| `/api/ai/match/<matchKey>` | POST | Strategy read of one match. Same `peek` / `force`. |
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
