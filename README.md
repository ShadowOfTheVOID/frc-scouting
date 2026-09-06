# REBUILT Scouting — FRC 2026

Scouting for **REBUILT presented by Haas**, built for FRC Team 6059.

One laptop runs the whole thing. Scouts open a web page on their phones — nothing to install,
no app store. A strategy dashboard runs on any other laptop or tablet on the same network.

**You do not need to be a programmer to run this.** If you can open a terminal and type one
line, you are done. Everything below assumes you have never done that before.

---

## Start here — first time only

You do this **once**, at home, before your first competition. Give yourself 20 minutes.

### 1. Install Python

Python is the only thing this needs. It is free.

- **Windows** — go to [python.org/downloads](https://www.python.org/downloads/), click the big
  yellow button, run the installer. **Tick the box that says "Add python.exe to PATH"** on the
  first screen. This matters; if you miss it, nothing else will work.
- **Mac** — open the Terminal app and type `python3 --version`. If it prints a number like
  `3.11.6`, you already have it and can skip ahead. If it offers to install developer tools,
  say yes.

### 2. Get the files onto the laptop

Download this repository as a ZIP (green **Code** button → **Download ZIP**) and unzip it
somewhere you will find again — the Desktop is fine.

### 3. Start it once, to check it works

- **Windows** — open the unzipped folder and double-click **`start-server.bat`**
- **Mac** — open the unzipped folder and double-click **`start-server.command`**

> **Mac, first time: "cannot be opened" or nothing happens?** Downloading a ZIP strips the
> permission that lets a file be double-clicked. Fix it once and it works forever after:
> open the **Terminal** app, type `chmod +x ` (with the space), then **drag the
> `start-server.command` file into the Terminal window** and press Enter. Now double-click it.
>
> If you would rather skip that entirely, you can always start it by dragging the *folder* into
> a Terminal window and typing `python3 server/hub.py`.

A black window appears and prints something like this:

```
  FRC 2026 REBUILT scouting server
  ----------------------------------------------------
    http://192.168.1.120:6059  <- try this first

  Scouts:    open  http://192.168.1.120:6059/scout
  Dashboard: open  http://192.168.1.120:6059/dashboard
  Join QR:   open  http://localhost:6059/join   on this screen and let scouts scan it
  Settings:  open  http://localhost:6059/       on this screen (API keys live here)
```

**Leave that black window open.** Closing it stops the server. Minimise it instead.

> **Windows will pop up a firewall warning the first time.** Tick **Private networks** and click
> **Allow access**. If you click Cancel, phones will not be able to connect and there is no
> other symptom — it just silently does not work. This is the single most common problem.

### 4. Add your API keys

On the **laptop itself**, open a browser and go to **http://localhost:6059/**

> Keys can only be entered here, on the hub laptop. Open that address from a phone and it will
> politely tell you to go to the laptop. That is on purpose — nobody on the venue wifi can
> change your settings.

Paste in whichever keys you have. All of them are free and all of them are optional; the app
runs without any, just with less live data.

| Key | What it gets you | Where to get it |
|---|---|---|
| **The Blue Alliance** | official match results, per-robot climb | [thebluealliance.com/account](https://www.thebluealliance.com/account) |
| **Nexus** | live queueing, match timing, pit map, alliance selection | [frc.nexus/api](https://frc.nexus/api) |
| **FRC Events** | the official result a few minutes before TBA posts it | [frc-events.firstinspires.org](https://frc-events.firstinspires.org/services/API) |
| **Lovat** | what other teams' scouts recorded about the same robots | [lovat.app](https://lovat.app) — see below |
| **AI model** | summaries of your scout notes, and a read of the next match | Claude, Gemini or OpenAI — see below |
| Statbotics | EPA next to your own numbers | nothing to do — no key needed |

The fuel numbers always come from The Blue Alliance, whichever other keys you set. FRC Events
only gets you the result sooner.

#### Getting a Lovat key — the long version

**Lovat** ([lovat.app](https://lovat.app)) is another team's scouting app — FRC 8033 build it —
that a lot of teams upload to. A key gets you what *their* scouts wrote about the robots at your
event: fuel per match, defence, feeding, driver ratings, notes, and the one thing our own
scouting cannot produce — the second on the clock each robot left to go and climb.

It is free, and it takes about ten minutes the first time. Do it at home, not at the venue.

1. **Make an account** at [lovat.app](https://lovat.app) and **verify the email** they send.
   Skipping this is the single most common reason a key comes back empty: an unverified account
   gets a polite `403` and no data.
2. **Join or create your team** on Lovat, and get the team **verified**. Verification is a
   person at Lovat checking that you are who you say you are; it is not instant, so do not
   leave it until the Thursday before a competition.
3. Open the **Lovat Dashboard** → **Settings** → **API keys** → **Add key**. Name it something
   you will recognise later, like `6059 scouting hub`.
4. **Copy the key immediately.** It starts with `lvt-` and Lovat will not show it to you again.
5. Paste it into the **Lovat** box at http://localhost:6059/ on the hub laptop, set the event
   key beside it, and click **SAVE & REFRESH**.
6. Check it worked on the dashboard's **SERVER** tab: the `lovat` service goes green, and the
   **GRAPHS** tab starts counting teams under `teams lovat has`.

**Some things worth knowing before you rely on it.**

- Lovat only hands back what your account is allowed to see. If you have set a *team source*
  rule on their side that narrows it to your own team, that is what you will get. A short list
  is a setting on their side, not a fault on ours.
- The hub asks once every five minutes. Lovat rate-limits a key to one request every three
  seconds, so this leaves that limit completely alone.
- A blank Lovat column means **nobody at this event uploaded that robot**. It is not a zero, it
  is not a bad robot, and nothing here will treat it as one.
- Playoff rows cannot be joined onto our qualification schedule, so they are counted in the
  totals and listed separately rather than being quietly attached to the wrong match.

Everything Lovat sends is kept in its own column, its own panel, its own CSV and its own colour
on every chart, clearly marked as other teams' scouting. **It never changes any of your own
numbers** — neither the solver nor the picklist reads it. It is a second opinion, not a
correction, and the places where it disagrees with your own scouts are the interesting ones:
the **GRAPHS** tab plots the two against each other so you can see them.

**AI** is optional and off until you pick a model and paste a key. It adds four things: a
summary of what your scout notes add up to on each team, a plain-English explanation of the
picklist you already built, a **read of the next match** on the MATCH tab — how it compares,
which robot decides it, who to defend, and what would make that read wrong — and a question box
on the CREW tab. It only ever reads the numbers already on this hub — it cannot look anything
up, it is told to cite the match and the scout behind every claim, and it never changes a
number or the picklist order. If you leave it on *none*, none of it appears.

Pick from one dropdown, grouped Claude, then Gemini, then OpenAI, with the price beside each
name. Picking the model picks the company, so the key you paste underneath is just that
company's key — there is nothing to match up. It starts on **Claude Opus 5**, so if that suits
you, pasting a key is the whole job. Any of them will do this job; the list spans a
50× price range and the cheap end is genuinely fine for summarising scout notes. An answer
costs somewhere between a fraction of a cent and a few cents depending on which you pick, and
only ever happens when somebody presses a button.

Also set the **event key** (like `2026casf` — the code on frc.events or The Blue Alliance) and
**our team** (6059). Click **SAVE & REFRESH**.

### 5. Try it before you rely on it

Practice with a fake event before you are standing in a venue. See
[Practice without a competition](#practice-without-a-competition) at the bottom.

---

## At the competition

### Setting up (15 minutes, once per event)

> **Do not turn on the laptop's hotspot at a competition.** FIRST's event rules prohibit teams
> from running their own wireless access point in the venue — a laptop Mobile Hotspot, a phone
> Personal Hotspot and an ad-hoc network all count. It exists because team radios interfere with
> the field, so it is enforced on the spot rather than after the fact. Look up the current wording
> and rule number in this year's game manual; it moves between seasons. The hotspot is for
> [practising at home](#practice-without-a-competition), and nothing else.
>
> That is a rule about the *network*, not about this app. Nothing here transmits anything by
> itself — it is an ordinary web server that uses whatever network is already there.

1. **Put the laptop on the venue wifi**, whatever network the venue gives teams.
2. **Start the server** (double-click the launcher as before). Note the address it prints.
3. **Test one phone before you seat six.** Put one phone on the same wifi and open that printed
   address in its browser. This is the most valuable thirty seconds of your setup: if it loads,
   the network works for the whole event. If it does not, see
   [If phones cannot reach the hub](#if-phones-cannot-reach-the-hub) — do that now, not at
   match 1.
4. **Check the event key is right** at http://localhost:6059/ — it changes every competition.
5. **Put the rest of the phones on the same wifi.**
6. **Open http://localhost:6059/join on the laptop screen.** It shows a big QR code.
7. **Each scout points their normal camera at the QR** and taps the link that pops up. Not a
   scanner app — the camera app they already have.
8. **Each scout picks the station matching the sign above their chair.** RED 2 means tap RED 2.
   There is no field map on purpose, so there is nothing to mirror or get backwards.
9. **Each scout adds it to their home screen** so it opens full-screen like an app:
   - iPhone: Share button → **Add to Home Screen**
   - Android: Chrome menu (⋮) → **Add to home screen**

That is it. From here on they tap the icon.

### During matches

Scouts do not have to do anything except watch their robot:

- **Left thumb** picks how fast the robot is shooting — *a trickle*, *steady*, *dumping*.
- **Right thumb** holds the big pad while the robot is actually shooting. That is the whole job.
- The first scout to tap the pad starts the clock **for everyone on that match**. The others'
  phones jump straight into the match on the same clock.
- **Tapping late is fine.** Really. Tell them to tap when they notice the match started, not to
  race the buzzer — the hub corrects the timing afterwards from official results.
- **Before the buzzer**, while the robot is lining up, the waiting screen asks two things in one
  tap each: where it starts, and how much fuel it is carrying. Both can be left blank.
- After the buzzer: a few taps for driving, defence, anything that went wrong, then **SEND IT IN**.
  If they logged any defence, one extra tap asks which robot they were blocking — scouts who
  logged none never see the question.

### If the wifi drops

Nothing is lost and nobody needs to do anything. The phone keeps working, saves everything on
itself, and sends it the moment the hub is reachable again. The header changes to say so.

The one thing to tell scouts: **do not force-reload the page while out of range.** Everything
they have logged is stored on the phone and is safe either way — but the *page* is not, so it
will not come back until they are in range again. A browser will only keep a page for offline
use over HTTPS, and this hub serves plain HTTP so that nobody has to install a certificate on
six phones the morning of a competition. That trade is deliberate; the cost is this one rule.

### If phones cannot reach the hub

Bandwidth is never the problem. A whole competition day of scouting for six people is about
80 KB total, and a connected phone that is doing nothing sends roughly one byte per second. If a
phone cannot reach the hub, something is blocking it — the network is not merely slow. In the
order these actually happen:

1. **The network isolates clients from each other.** Venue and guest wifi very often do this:
   phones reach the internet perfectly well and cannot see the laptop at all. Nothing on this end
   can defeat it — go to **Plan B** below.
2. **A captive portal.** The phone has joined the wifi but has not been through the splash page.
   Open any ordinary website on the phone first, accept whatever it asks, then try the hub again.
3. **Windows Firewall.** It prompts the first time the server runs and silently blocks every
   phone if that prompt was dismissed. Allow Python on **Private** networks.
4. **The address moved.** The venue's DHCP can hand the laptop a new one. Phones re-find it
   themselves within a minute; re-opening `/join` shows you the current address immediately.

### Plan B — no usable network

This costs you the live dashboard, not your data. The app is built to run offline.

- **Scouts keep scouting, exactly as normal.** Everything queues on the phone. They will see the
  offline header all day; that is fine and expected.
- **At the end of the day, each phone taps `SAVE A BACKUP FILE`** on the offline screen.
- **Collect those files onto the laptop** and import each one — `POST /api/import`, or the import
  control on the dashboard. Re-importing the same file is a no-op, so you cannot double-count by
  being careless about which files you have already done.
- The solver, picklist and every dashboard tab work normally once the data is in.

The one thing that genuinely does not survive Plan B is the shared match clock across phones that
never saw each other. Those matches are flagged `clock-partial` and the hub leaves their timing
alone rather than corrupting it — see [how it works](docs/how-it-works.md#the-match-clock).

---

## For the scout lead

Open the dashboard on your laptop — `http://<the address the server printed>:6059/dashboard` —
and stay on the **CREW** tab. It answers the only question you have during quals: *is data
coming in, and if not, who do I go talk to?*

It tells you in plain words:

- `RED 2 — nobody seated, those robots are unwatched`
- `AK on RED 1 — app is not open on their phone` → go tell them to reopen it
- `CJ on BLUE 3 — gone quiet 6m ago, check their wifi`

When everything is fine it says so in one green line and you can go back to watching robots.

**THIS MATCH** lists the six robots on the field and who is watching each, so an unwatched robot
is obvious *before* the match instead of after.

### People swapping in and out

Six scouts and no spare, so this happens all day.

- **Same phone, new person** — on the standby screen, tap **HAND OVER**, type the new initials.
  The seat and the match stay put; the previous scout's work is saved under their name.
- **Different phone** — the new scout just claims the station from their own phone. The old
  phone is told immediately and stops, so you never get two people logging one robot. If that
  happened by mistake, the bumped phone offers **IT'S STILL MY CHAIR** — but only between
  matches. Mid-match it shows the time to the buzzer instead, because taking the chair back
  would stop whoever is sitting in it now.
- **FREE** on the crew board releases a chair when someone walks off. That phone is told
  immediately and stops, so a chair you have freed is never still logging.

### Training someone new

Send them to **`/scout?practice=1`**. It is the real screen against a fake match — everything
behaves exactly as it will in a real one — and nothing they do is saved. Two fake matches and
they have got it.

---

## The picklist

Everyone on the network can **look** at the picklist. Only people with the strategy passcode can
**change** it, so a bored student cannot flag a team as do-not-pick an hour before alliance
selection.

Set the passcode on the settings page. Leave it blank and anyone can edit. Changing it signs
everyone out, which is handy right before alliance selection.

The passcode also gates the **per-scout** panel on the HEALTH tab — see below.

During alliance selection the board crosses teams off by itself as they are picked, so the next
available name is always at the top.

There are two boards. **FIRST PICK** ranks the best robot left; **SECOND PICK** ranks the best
*complement* to the two you already have, which is a different question — defence, feeding and
not breaking down count for more down there. Each has its own weights and its own order.

Drag a row to move a team by hand. The first time you do, the board freezes as you see it, so
new match data stops reordering it under you; **RESET TO COMPUTED** hands it back to the score.

**Filters narrow what you are looking at, never the board.** Above the rows: search by team
number or name, a minimum climb, a minimum **fuel per second**, a minimum number of matches
scouted, the zone a robot usually starts in, and chips for HIDE TAKEN, HIDE DNP, RELIABLE,
AUTO WORKS, NO AUTO CLASH, DEFENDS and STOCKPILES.

Fuel per second is the rate behind the volume — how fast a robot cycles rather than how much it
finishes with — and it now reads on every row and on the team page. **NO AUTO CLASH** hides
robots that habitually start where ours does, which is the auto argument the match preview has
always had, moved to the day you can still pick somebody else; it needs OUR TEAM set and
scouted, and says so when it is not.

Hiding rows does not renumber the ones left — the
number beside a team is always its rank on the whole board — and it changes nothing else: not
the score, not the saved order, not the printed sheet, not what a drag does. Filters stay in
the browser you set them in rather than on the hub, so the laptop running alliance selection
can never inherit one somebody upstairs forgot to clear. **CLEAR** puts them all back.

**Print it before alliance selection.** The SERVER tab has PRINTABLE PICKLIST (and one for the
second-pick board). One laptop is one laptop, and this is the ten minutes where it cannot fail.

---

## Graphs

The **GRAPHS** tab draws what the tables only average.

- **FUEL BY MATCH** — up to six robots, a line each, over the whole schedule. This is the one
  that answers *is it getting better*, which an average cannot. Pick teams from the row of
  chips; a team keeps its colour while it is on, so the legend does not move under you.
- **DEFENCE — PLAYED AND FACED** — seconds of contact per match, in both directions. A robot
  with a good fuel number and a lot of seconds *faced* is a robot that scored that anyway,
  which is worth knowing an hour before alliance selection.
- **WHERE THE SOURCES DISAGREE** — every team as one dot: our fuel against Lovat's on the left,
  our fuel against Statbotics EPA on the right. On the left, the dashed line is agreement, and
  a robot well off it is one the two sets of scouts read differently. Those are the robots to
  go and watch yourself.

Every chart has a **table view** under it, so nothing is only reachable by hovering.

A gap in a line is a match with no measurement — a match a robot did not play, or one nobody
scouted. It is never drawn as a zero, because "nobody was watching" and "did nothing" are
opposite facts.

Each team's own page has the same two charts for that robot alone, with the solver's
uncertainty shaded around the fuel line and Lovat's count of the same robot drawn beside it.

## Reading a match before it happens

The **MATCH** tab puts both alliances side by side: projected fuel and points, win probability
with the margin it came from, and each robot's fuel and climb. It follows the field by default;
pick any match from the dropdown to look ahead or back. Two warnings fire on their own — two
partners who both start in the same auto zone, and an opponent with a logged history of
defending someone in this lineup.

**HOW TO PLAY IT** is the generated part, and only if you have set an AI key. Press the button
and it writes four lines: how the two alliances compare, the single opposing robot that decides
it, who to put defence on (or that the data does not support putting anyone on defence), and
the one thing most likely to make that read wrong. It is told the projection rather than asked
to work it out, it cites the block behind every number, and it says so plainly when an
alliance has robots nobody has scouted.

## Judging the data, not the scouts

A dashboard left open in the stands is readable by anyone walking past, so it is careful about
what it puts on screen.

**Who needs help is public.** Whose phone has gone dark, which station has logged nothing in
twenty minutes — that is equipment, you have to act on it immediately, and it is on the CREW
tab in plain words.

**Who is any good is not.** Per-scout reconciliation rates are on the HEALTH tab behind the
strategy passcode, or on the hub laptop itself. It is coaching material — someone to go and
stand next to for a match — not a leaderboard for the room.

What the room sees instead is **SCOUTS vs TBA** on the HEALTH tab: what our scouting said each
match was worth against the official result, and whether we are running hot or cold today. That
is a statement about the data, and it is the number that actually tells you how far to trust
the fuel column.

---

## When something goes wrong

| What you see | What it is | What to do |
|---|---|---|
| Phones cannot connect at all | Windows Firewall blocked it | Restart the server, click **Allow access** on **Private networks** |
| A phone says it cannot reach the hub | out of range, or the laptop moved networks | Walk back toward the laptop. Data is safe; it sends itself |
| An AI panel says "the model could not be reached" | no internet, or the key is wrong | it is safe to ignore — nothing else depends on it. Re-check the key at http://localhost:6059/ |
| The LOVAT column is empty | no Lovat key, or nobody uploaded that robot | not a fault: blank means nobody scouted it there, which is not a zero |
| Scouts see an old event's teams | event key not changed | http://localhost:6059/ on the laptop, set the new event key |
| Fuel numbers look wrong for one team | a scout was on their own clock, or missed matches | Check the **HEALTH** tab — flagged matches are listed with the reason |
| Every team's fuel looks too high, or too low | scouts are calling shooting harder or softer than it scores | **HEALTH** tab, ACCURACY panel — it says `running N% hot` or `cold`. Worth a word about the rate ladder; the solver corrects for it either way |
| Nothing at all loads | the black window got closed | Double-click the launcher again |
| The database is damaged, or a whole day looks wrong | anything from a bad shutdown to a full disk | Stop the server. Copy the newest file out of `data/snapshots/` over `data/scouting.db`, delete `data/scouting.db-wal` and `-shm` if they are there, and start it again. The hub writes a snapshot every ten minutes and keeps the last twelve |
| The QR code will not scan | screen too dim, or too far | Turn brightness up; hold the phone about a foot away |

If a phone is truly stuck, the scout can keep scouting anyway — everything saves locally — and
you can collect it later with **SAVE A BACKUP FILE** on their offline screen.

## Getting the numbers out

The **SERVER** tab on the dashboard has all of it:

- **JSON export** — the whole event. This is the one that imports back in, and the one to send
  another laptop.
- **CSV** — team summary, every scout entry, pit scouting, or everything Lovat has. For a
  spreadsheet, or for handing numbers to an alliance partner. The team summary carries the same
  scout-vs-official check the HEALTH tab shows, plus defence in both directions — seconds
  played and seconds taken — and each robot's usual start zone, so the spreadsheet and the
  dashboard cannot disagree. The Lovat file is separate on purpose: it is other teams' scouting
  and mixing it into our columns is how it ends up quoted back as ours.
- **Printable picklist** — see above.

---

## Practice without a competition

Generate a full fake event and poke at everything:

```
python3 server/seed_demo.py --db data/demo.db --event 2026demo
python3 server/hub.py --db data/demo.db
```

(On Windows, type `python` instead of `python3`.)

That builds 31 teams, 40 matches with 26 already played, scout data already logged, a real pit
map, and a fake Lovat export covering about three quarters of the field. The teams are FIRST's
actual **Off-Season Demo Teams (9970–9999)** plus **6059**, so nothing here can be confused
with a real team's record.

Everything works with no keys and no internet: the numbers crunch, the picklist ranks, the
graphs draw with all three sources on them, and the pit map draws. The fake Lovat data is
written as a CSV and read back through the same importer a real key feeds, so the demo
exercises that path rather than faking around it — and because those scouts disagree with ours
by a fifth or so, the "where the sources disagree" chart has something real to show.

Adding `--via-nexus` builds the same event but feeds the schedule in the way a real competition
does, through Nexus rather than as finished results. It is worth using if you are changing how
matches are ingested — that path is the one that broke once.

### Practising with real phones, at home

At home there is no field to interfere with, so the laptop's hotspot is the easy way to get six
phones onto one network — and it is the one place you should use it:

- Windows: Settings → Network & internet → **Mobile hotspot** → on
- Mac: System Settings → General → Sharing → **Internet Sharing** → on

Connect the phones to it, then set up as normal. The hub advertises the hotspot's gateway address
(`192.168.137.1` on Windows) first, so it is usually the top QR code on the `/join` screen.

**Then turn it off before you leave for the competition**, and run through
[Setting up](#setting-up-15-minutes-once-per-event) on venue wifi when you get there. Practising
on a hotspot and arriving expecting one is how teams get told to shut it down at 8am on a
Friday.

---

## What is in here

```
start-server.bat / .command   double-click these
server/                       the hub — Python, no dependencies to install
web/                          what phones and laptops actually open
design/                       the UI specification the screens were built to
data/                         your event database — never share this, it holds your keys
data/snapshots/               automatic backups, newest is the one to restore from
docs/features.md              what every screen and field does
docs/how-it-works.md          why the tricky parts work the way they do
```

`data/` is excluded from git on purpose: it holds your API keys and strategy passcode.

## Two more documents

[docs/features.md](docs/features.md) is the reference: every screen, every tab, every field a
scout can fill in, and who is allowed to see what. Hand it to a new strategy student.

[docs/how-it-works.md](docs/how-it-works.md) covers the parts with real reasoning behind them —
how per-robot fuel counts are worked out when nobody can count that fast, why the match clock
matters less than you would think, and how accurate any of it actually is.

Run the tests with:

```
python3 server/tests_solver.py    # the accuracy claims above, on 20k simulated matches
python3 server/tests_api.py       # the server: syncing, the passcode, export and import
python3 server/tests_lovat.py     # reading Lovat's export, which is somebody else's file format
python3 server/tests_ai.py        # the model adapter, stubbed — no key and no network
```

All four are plain Python with nothing to install, and all four run on every push
(`.github/workflows/ci.yml`).

## License

MIT — see [LICENSE](LICENSE). Third-party code, fonts, and data are credited in
[NOTICE.md](NOTICE.md).
