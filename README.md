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
    http://192.168.1.120:8080  <- try this first

  Scouts:    open  http://192.168.1.120:8080/scout
  Dashboard: open  http://192.168.1.120:8080/dashboard
  Join QR:   open  http://localhost:8080/join   on this screen and let scouts scan it
  Settings:  open  http://localhost:8080/       on this screen (API keys live here)
```

**Leave that black window open.** Closing it stops the server. Minimise it instead.

> **Windows will pop up a firewall warning the first time.** Tick **Private networks** and click
> **Allow access**. If you click Cancel, phones will not be able to connect and there is no
> other symptom — it just silently does not work. This is the single most common problem.

### 4. Add your API keys

On the **laptop itself**, open a browser and go to **http://localhost:8080/**

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
| **AI provider** | plain-English summaries of your scout notes | Claude, OpenAI or Gemini — see below |
| Statbotics | EPA next to your own numbers | nothing to do — no key needed |

The fuel numbers always come from The Blue Alliance, whichever other keys you set. FRC Events
only gets you the result sooner.

**Lovat** is another team's scouting app that a lot of teams use. If your team is registered and
verified on Lovat, your scouting lead can make a key: open the Lovat Dashboard, go to
**Settings → API keys**, add one, and copy it — it starts with `lvt-`. Paste that in and the hub
pulls what Lovat has for your event every five minutes. It is shown in its own column and its
own panel, clearly marked as other teams' scouting, and it never changes any of your own
numbers. It is a second opinion, not a correction.

**AI** is optional and off until you pick a provider and paste a key. It adds three things: a
summary of what your scout notes add up to on each team, a plain-English explanation of the
picklist you already built, and a question box on the CREW tab. It only ever reads the numbers
already on this hub — it cannot look anything up, it is told to cite the match and the scout
behind every claim, and it never changes a number or the picklist order. If you leave the
provider on *none*, none of it appears. Answers cost a fraction of a cent each and only ever
happen when somebody presses a button.

Also set the **event key** (like `2026casf` — the code on frc.events or The Blue Alliance) and
**our team** (6059). Click **SAVE & REFRESH**.

### 5. Try it before you rely on it

Practice with a fake event before you are standing in a venue. See
[Practice without a competition](#practice-without-a-competition) at the bottom.

---

## At the competition

### Setting up (15 minutes, once per event)

1. **Turn on the laptop's hotspot.** This is the most reliable option by a mile — venue wifi is
   usually blocked, overloaded, or set up so devices cannot see each other.
   - Windows: Settings → Network & internet → **Mobile hotspot** → on
   - Mac: System Settings → General → Sharing → **Internet Sharing** → on
2. **Connect all six scout phones to that hotspot.**
3. **Start the server** (double-click the launcher as before).
4. **Check the event key is right** at http://localhost:8080/ — it changes every competition.
5. **Open http://localhost:8080/join on the laptop screen.** It shows a big QR code.
6. **Each scout points their normal camera at the QR** and taps the link that pops up. Not a
   scanner app — the camera app they already have.
7. **Each scout picks the station matching the sign above their chair.** RED 2 means tap RED 2.
   There is no field map on purpose, so there is nothing to mirror or get backwards.
8. **Each scout adds it to their home screen** so it opens full-screen like an app:
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

The one thing to tell scouts: **do not force-reload the page while out of range.** Their data is
safe either way, but the page will not load again until they are back in range.

---

## For the scout lead

Open the dashboard on your laptop — `http://<the address the server printed>:8080/dashboard` —
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
  phone is told immediately and stops, so you never get two people logging one robot.
- **FREE** on the crew board releases a chair when someone walks off.

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

**Print it before alliance selection.** The SERVER tab has PRINTABLE PICKLIST (and one for the
second-pick board). One laptop is one laptop, and this is the ten minutes where it cannot fail.

---

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
| An AI panel says "the model could not be reached" | no internet, or the key is wrong | it is safe to ignore — nothing else depends on it. Re-check the key at http://localhost:8080/ |
| The LOVAT column is empty | no Lovat key, or nobody uploaded that robot | not a fault: blank means nobody scouted it there, which is not a zero |
| Scouts see an old event's teams | event key not changed | http://localhost:8080/ on the laptop, set the new event key |
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
- **CSV** — team summary, every scout entry, or pit scouting. For a spreadsheet, or for handing
  numbers to an alliance partner. The team summary carries the same scout-vs-official check the
  HEALTH tab shows, plus defence in both directions and each robot's usual start zone, so the
  spreadsheet and the dashboard cannot disagree.
- **Printable picklist** — see above.

---

## Practice without a competition

Generate a full fake event and poke at everything:

```
python3 server/seed_demo.py --db data/demo.db --event 2026demo
python3 server/hub.py --db data/demo.db
```

(On Windows, type `python` instead of `python3`.)

That builds 31 teams, 40 matches with 26 already played, scout data already logged, and a real
pit map. The teams are FIRST's actual **Off-Season Demo Teams (9970–9999)** plus **6059**, so
nothing here can be confused with a real team's record.

Everything works with no keys and no internet: the numbers crunch, the picklist ranks, the pit
map draws.

Adding `--via-nexus` builds the same event but feeds the schedule in the way a real competition
does, through Nexus rather than as finished results. It is worth using if you are changing how
matches are ingested — that path is the one that broke once.

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
```

Both are plain Python with nothing to install, and both run on every push
(`.github/workflows/ci.yml`).

## License

MIT — see [LICENSE](LICENSE). Third-party code, fonts, and data are credited in
[NOTICE.md](NOTICE.md).
