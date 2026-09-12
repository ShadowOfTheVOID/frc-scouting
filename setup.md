# Setup — the short version

Every step, in order, with the explanations stripped out — from the ZIP, through every API
key, to the off-site mirror. Start at the top and stop when you have what you need: **Part A**
is the whole app; **Part B** is the mirror and is optional.

For *why* any of this is the way it is, read the [README](README.md) and
[mirror/README.md](mirror/README.md) instead — this page deliberately repeats neither.

---

## Part A — the hub laptop

The one laptop that runs everything. Three things to do here; the hub tells you the rest as you
go. About 20 minutes, once, at home.

### 1. Install Python 3

- **Windows** — [python.org/downloads](https://www.python.org/downloads/), run the installer,
  and **tick "Add python.exe to PATH"** on the first screen. Miss it and nothing else works.
- **Mac** — `python3 --version` in Terminal. A number means you are done.

There is nothing else to install. No `pip`, no dependencies.

### 2. Download the ZIP and start it

Green **Code** button → **Download ZIP**. Unzip it somewhere you will find again — the Desktop
is fine. Keep the folder together; the server, the web pages and the mirror all live in it.

- **Windows** — double-click `start-server.bat`
- **Mac** — double-click `start-server.command`

> **Mac, first run:** macOS may ask you to confirm an app from the internet — right-click the
> file and choose **Open** the first time. If it says "cannot be opened" instead, the execute
> bit did not survive the download: in Terminal type `chmod +x ` (with the space), drag
> `start-server.command` into the window, press Enter. Once, forever. Or skip it and run
> `python3 server/hub.py` from the folder.

Leave the black window open — closing it stops the server. It prints the three addresses:

```
http://<laptop-ip>:6059/scout        scouts, on phones
http://<laptop-ip>:6059/dashboard    strategy, on any device on the same wifi
http://localhost:6059/               admin panel, this laptop only
```

**On a hub that has never been set up, it opens the admin panel in your browser for you** and
prints what is left to do in that black window. Once an event key is set it stops doing that —
by then the panel is somewhere you go on purpose. `--no-browser` turns it off entirely, and so
does starting the hub from a script rather than by double-clicking it.

**On Windows the hub asks, in that same black window, whether to allow phones through the
firewall. Answer yes** — it adds one rule (this port, inbound, private networks only) and
Windows asks you to confirm it. Windows may also show its own popup: tick **Private networks**
and click Allow. Dismissing both is the single most common setup failure there is, and it is
silent — the hub runs, this laptop's browser works, and every phone times out. The checklist
below notices it for you: nothing ever connects.

### 3. Work down the checklist on that page

The panel — **http://localhost:6059/** on this laptop, and nowhere else — opens with **START
HERE** and five lines. Each one ticks itself off when the hub actually has the thing, so the
same list answers "what now?" tonight and "did that work?" on the Saturday morning:

| | | |
|---|---|---|
| 1 | **Unlock this panel** | Already done on a hub with nothing set up and no admin password — there is nothing to protect yet, so it opens itself. After that it opens read-only every time: press **UNLOCK**, and it re-locks after ten minutes and on every reload. |
| 2 | **Name the event** | Type your team number into **OUR TEAM** and press **FIND MY EVENTS**. The hub looks up what you are registered for and fills in the key and the level. No API key needed for that — the list comes from Statbotics. Typing `2026casf` yourself still works. |
| 3 | **Paste the Nexus key** | Free, from [frc.nexus/api](https://frc.nexus/api). The one key worth stopping for — without it every scout taps **THEY'RE ON THE FIELD** by hand, six times an hour. |
| 4 | **Press SAVE & REFRESH** | On a hub that has never had a passing key test, saving also tests: it asks each vendor whether the key really works and prints what each one said. **TEST KEYS** does it again any time. |
| 5 | **Open it on a phone** | Same wifi, `http://<laptop-ip>:6059/scout` — or open `http://localhost:6059/join` here and let scouts scan the QR code. |

Everything else — The Blue Alliance, FRC Events, Lovat, the AI model, the strategy passcode —
is behind **THE OPTIONAL ONES** on the same page, folded away until you want it. A first event
is fine without any of it. [Where each key comes from](#where-each-key-comes-from) is below.

Three things about that page worth knowing before the Saturday:

- The page takes the wrapping off a paste for you — a header name, quotes from a code sample, a
  line break from an email — and shows you what will actually be saved. A key that belongs in a
  different box is refused and told where to go. Nothing saves at all when one box is refused,
  so fix that box and save again.
- **Keys are saved in `.env` beside the hub**, base64-encoded and never in the event database.
  That one file is the whole backup: copy it to a spare laptop and that laptop is a hub.
  Encoding is not encryption — anyone holding the file can decode it in one command — it keeps a
  key off the screen at a glance. You can still type a plain `NEXUS_API_KEY=` line in yourself
  and the hub will encode it on its next start; [`.env.example`](.env.example) has the name each
  key sits under, and a hand-edited file needs the hub restarted.
- Changing the **event key** once the hub holds scouting is asked about twice. Nothing is ever
  deleted — the old event comes back if you set its key again.

Do all of this **at home**. A key that turns out to be wrong is not a thing you can replace on
a Saturday morning.

### 4. Optionally, a password on the panel

```
python3 server/hub.py --set-admin-password
```

Asks twice, writes `ADMIN_PASSWORD_B64` into `.env` beside the hub, and carries on serving.
That file is the only place it lives, the same command changes or removes it, and `.env` is
in `.gitignore` — never commit it. Base64 is not encryption; keep the file off shared drives.

No password set means **UNLOCK** is one button, which is a fine way to run an event.

**That is the whole app.** Part B below is the mirror.

---

## Set once, or set every event?

Short answer: **the keys are a once-ever job, the event key is the per-event one.**

| | Changes when | |
|---|---|---|
| Every API key on this page | never — until you revoke it | No key here is per-event or per-season. Nexus, TBA, FRC Events, Lovat and the AI keys all keep working at the next competition and the next season. |
| **EVENT KEY**, **EVENT LEVEL** | every competition | The one thing to change on the Friday. Two boxes, ten seconds. Old events stay in the database behind their own key. |
| **OUR TEAM**, strategy passcode, admin password, mirror settings | when you decide to | Not touched by moving to a new event. |
| The Lovat *browser token* | every 72 hours | Not a key and not stored anywhere — it is only used to mint the `lvt-` key once, and that key does not expire. |

So: set the keys at home before your first event, back up `.env`, and at every event after that
the setup is the event key and a phone check. A replacement laptop needs Python, the folder,
`.env`, and the event key.

## Where each key comes from

**Nexus — the one you cannot skip.** It is what tells the hub a match has taken the field, and
that is what arms the scouting screen on all six phones. Without it every scout taps
**THEY'RE ON THE FIELD** by hand, six times an hour.

1. Sign in at [frc.nexus](https://frc.nexus) with the account your team already uses.
2. Go to [frc.nexus/api](https://frc.nexus/api) and request/generate an API key.
3. Copy it into the **NEXUS** box.

The **NEXUS WEBHOOK TOKEN** box is only for teams who registered a push webhook with Nexus —
the hub polls perfectly well without one. If you did register one, the token you chose there
goes in that box; the hub rejects webhook posts that do not carry it.

**The Blue Alliance — official results and per-robot climb.**

1. Sign in at [thebluealliance.com/account](https://www.thebluealliance.com/account) with a
   Google account.
2. Find the **Read API Keys** section, type a description (`6059 scouting hub`), and add the key.
3. It is 64 characters of letters and digits. Copy it into **THE BLUE ALLIANCE** box.

The fuel numbers always come from TBA, whichever other keys you set.

**FRC Events — the official result a few minutes sooner.** This is the only one that is two
boxes, because FIRST authenticates with a username *and* a token.

1. Go to [frc-events.firstinspires.org/services/API](https://frc-events.firstinspires.org/services/API)
   and request an API key with your FIRST account.
2. They send back a **username** and an **authorization token**. The username is the one you
   chose; the token is a **UUID** — 8-4-4-4 hex digits and then 12, like
   `1f0c8b3a-77d2-4a51-9d6e-3b0a5c9e4477`. Nothing else about it is meaningful, and it does not
   expire. If yours does not look like that, you are probably holding the wrong half.
3. Username into **FRC EVENTS USERNAME**, token into **FRC EVENTS TOKEN**. Do not paste the
   combined `Basic ...` blob into one box.

   The `Basic` blob in their documentation is just those two joined and base64-encoded —
   `base64("username:token")` — and it is the one thing the API actually sends. Paste that whole
   blob, or a plain `username:token`, into the token box and the hub splits it and fills both.
   The box warns you if the token is not UUID-shaped, but saves it anyway: shapes are advisory,
   and only **TEST KEYS** knows whether FIRST accepts it.

Skip this one and you still get every result — just when TBA posts it rather than a few minutes
before.

**Lovat — what other teams' scouts wrote about the same robots.** Free, and the only key on this
page with no page to get it from: Lovat's key endpoints exist on their server, but nothing in
their dashboard or website calls them. So it is a terminal command. Do it at home. FRC 8033 run
it.

**First, the team email.** Sign in at [dashboard.lovat.app](https://dashboard.lovat.app), then
**Settings → Team email → Change**, and click the link in the mail. **It expires in twenty
minutes** — a stale link is why this silently never completes, and without it every step below
returns 403.

**Then check you have curl.**

- **Windows** — `curl --version` in **Command Prompt**. Windows 10 and 11 ship it. If it is
  missing: `winget install cURL.cURL`, or install
  [Git for Windows](https://git-scm.com/download/win), which includes it.
- **Mac** — `curl --version` in Terminal. Part of macOS, nothing to install.

> **Windows: not PowerShell.** There, `curl` is an alias for `Invoke-WebRequest` and does not
> understand `-X` or `-H`. Use Command Prompt, or write `curl.exe`.

**Then mint the key.** Chrome or Edge — Safari's inspector cannot *Copy as cURL*.

1. Signed in to the dashboard, **F12** → **Network**, tick **Preserve log**, filter
   `api.lovat.app`.
2. Reload and **wait for the app to finish drawing** — it is Flutter, so the API calls come
   seconds after the page, not with it.
3. Click the **`profile`** row whose Type is **fetch**, not the `preflight` one under it.
   Right-click → **Copy** → **Copy as cURL**. Paste in your terminal, do not run it.
4. Change two things: path `profile` → `apikey?name=6059%20scouting%20hub`, and add `-X POST`.
   Keep the token as copied; delete every header except `authorization`.

   ```bash
   curl -X POST --url "https://api.lovat.app/v1/manager/apikey?name=6059%20scouting%20hub" \
     -H "authorization: Bearer eyJhbGciOi…"
   ```

   On Windows Command Prompt use one line, or `^` to continue instead of `\`.
5. **Copy the `lvt-…` straight away.** Only its hash is kept, so that response is the only place
   it ever exists — listing your keys later shows a name and a date, never the key.
6. Paste into **LOVAT API KEY**.

> That browser token is a full account password lasting **72 hours**, and nothing invalidates it
> early. Never paste it into a chat, an issue or a screenshot.
>
> `403 Your team has not been verified yet` is the email step — and `/profile` succeeding does
> not rule it out, since only `/apikey` checks the team. A `401` after it just worked is usually
> the whole header line copied instead of the value after `Bearer `. Changing the path but not
> the method (or the reverse) gives you your profile back or a 404 — the usual reason for "there
> is no API key in the response". `403 Cannot create API key using an API key` means an `lvt-`
> key went where the browser token goes. (The other Lovat approval, where a person checks your
> team, gates the team join code rather than API keys, so it does not block this.)

Everything Lovat sends stays in its own column and its own colour. It never changes your own
numbers — the solver and the picklist do not read it.

**AI — optional, off until you pick a model.** Pick from the one **AI MODEL** dropdown (Claude,
then Gemini, then OpenAI, then OpenRouter, price beside each name) and paste that company's key
underneath. Picking the model picks who it goes to, so there is nothing to match up — and a key
from the wrong one is refused rather than failing silently all weekend. It starts on
**Claude Opus 5**.

- **Claude** — [console.anthropic.com](https://console.anthropic.com) → **API keys** →
  **Create key**. Starts `sk-ant-`. Needs a little credit on the account.
- **Gemini** — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) →
  **Create API key**. Starts `AIza`.
- **OpenAI** — [platform.openai.com/api-keys](https://platform.openai.com/api-keys) →
  **Create new secret key**. Starts `sk-proj-`. Needs credit on the account.
- **OpenRouter** — [openrouter.ai/keys](https://openrouter.ai/keys) → **Create key**. Starts
  `sk-or-`. One key and one bill for all of the above and a few hundred more; buy credit on the
  account first. Its model ids name the maker in front — `anthropic/claude-opus-5` — and that
  slash is what sends it to OpenRouter rather than to Anthropic. Anything not in the dropdown
  goes in **other — type a model id**.

An answer costs between a fraction of a cent and a few cents, and only ever happens when
somebody presses a button. Leave the model on *none* and none of it appears.

**Statbotics** needs no key and no account — EPA shows up beside your own numbers on its own.

**The strategy passcode** is not a key and no vendor issues it: you make it up, it gates the
picklist and every AI answer, and blank means no lock. A space on the end is a lockout nobody
ever finds, so it is trimmed for you. It is in **THE OPTIONAL ONES** with the rest.

**Where they are kept.** Every key above is written to `.env` beside the hub, base64-encoded on
a `_B64` line — one file, mode `0600`, in `.gitignore`, and never part of the event database or
of anything the mirror is sent. Encoding is not encryption and nothing here pretends it is:
anyone holding the file decodes it in one command. It keeps a key from being legible over a
shoulder, on a projector, or in a screenshot, which is the same reason the admin password has
always been kept that way.

A hub set up by an older build had its keys in the database, or on plain lines; it tidies both
into the encoded form the next time it starts and says so in the black window. A plain line you
type in yourself keeps working until then, and a `_B64` line that will not decode is called out
by name on the panel rather than being sent to a vendor as mangled bytes.

---

## Part B — the off-site mirror

A copy of the event on a host you already pay for, so the event survives the laptop and the
team can read it from anywhere. The hub pushes to it once a minute; nothing reaches in.
Skip this if you do not have a host with a domain name.

### 1. Make two secrets

They are **not** the same secret — one writes, one reads.

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"    # MIRROR_PUSH_KEY
```

- `MIRROR_PUSH_KEY` — how the hub proves it is the hub. Generated, long, typed once.
- `MIRROR_VIEW_PASSCODE` — what a person types to read the site. Pick something a tired
  15-year-old can type on a phone in the stands.

### 2. Put it on the host

```
sudo useradd --system --home /opt/frc-scouting frcmirror
sudo git clone https://github.com/ShadowOfTheVOID/frc-scouting /opt/frc-scouting

sudo cp /opt/frc-scouting/mirror/deploy/frc-mirror.service /etc/systemd/system/
sudo systemctl edit frc-mirror
```

In the override that opens, and nowhere else — not the unit file, not the command line, where
`ps` shows it to every user on the host:

```
[Service]
Environment=MIRROR_PUSH_KEY=the-long-generated-one
Environment=MIRROR_VIEW_PASSCODE=the-one-people-type
```

Then:

```
sudo systemctl enable --now frc-mirror
```

### 3. Put TLS in front of it

The mirror listens on `127.0.0.1` and speaks plain HTTP on purpose.

```
sudo cp /opt/frc-scouting/mirror/deploy/Caddyfile /etc/caddy/Caddyfile   # edit the hostname
sudo systemctl reload caddy
```

Point the domain's A record at the host. Caddy gets and renews the certificate itself. Open
`https://your-hostname` and you should get the passcode prompt.

### 4. Tell the hub about it

Back on the laptop, `http://localhost:6059/` → **UNLOCK** → **OFF-SITE MIRROR**:

- **Mirror address** — `https://your-hostname` (a trailing slash or a pasted `/api/push` is
  trimmed for you)
- **Mirror push key** — `MIRROR_PUSH_KEY`

Then **SAVE & REFRESH**, **TEST KEYS** (the button up in the keys pane — it checks the mirror
too, answering two questions separately: is there a mirror at that address at all, and would it
accept your push key), and finally **PUSH NOW**, which sends the event for real and reports on
the line beside it.

From then on it goes by itself, about once a minute. The dashboard's **SERVER** tab carries an
`off-site mirror` line that reads `RETRYING` if it stops working.

### 5. Hand out the view passcode

`MIRROR_VIEW_PASSCODE`, to the team, for the weekend. Not the push key — anyone with that can
overwrite the event.

---

## Trying the mirror on a laptop first

No host, no systemd, five minutes, two terminal windows:

```
MIRROR_PUSH_KEY=test-key MIRROR_VIEW_PASSCODE=1234 python3 mirror/server.py
```

Then point the hub at `http://localhost:8060` with push key `test-key`. Flags: `--port`,
`--bind`, `--db`, `--behind-proxy`, and `--open` for no read passcode at all.

Both sides read `MIRROR_PUSH_KEY`, so one line in `.env` is both halves — put
`MIRROR_PUSH_KEY=test-key` in the file, start the mirror with only
`MIRROR_VIEW_PASSCODE=1234`, and the hub already has the push key. Only the address still needs
typing into the panel.

## Getting the event back

Mirror → **BACKUP** tab → **DOWNLOAD JSON**. On a fresh install: start the hub, set the same
event key, then dashboard → **SERVER** tab → import the file. Re-importing is a no-op for
anything already there, so it is safe to run twice. The mirror keeps the last 60 copies.

## When something is wrong

| What you see | What to do |
|---|---|
| Phones cannot reach the hub | a firewall prompt was dismissed. Restart the hub and answer yes when it offers to add the rule, or run: `netsh advfirewall firewall add rule name=FRC-Scouting-Hub dir=in action=allow protocol=TCP localport=6059 profile=private` in an Administrator Command Prompt |
| Admin panel refuses to open on a phone | on purpose; keys are set on the hub laptop only |
| Panel stays locked and complains about `.env` | a typo in `ADMIN_PASSWORD_B64` — re-run `--set-admin-password` |
| A key box says SAVED but the panel says the line cannot be read | a `_B64` line was hand-edited into something that is not base64. Paste the key into the box again, or write it as a plain `NEXUS_API_KEY=` line and restart |
| **FIND MY EVENTS** comes back with nothing | no internet on the laptop, or the schedule is not published yet. Type the event key in by hand |
| `could not be reached` on the mirror line | laptop has no internet, or the address is wrong |
| `rejected the push key` | `systemctl show frc-mirror -p Environment` on the host |
| Mirror header amber / red | nothing has arrived for 5 minutes / an hour — everything on screen is that old |
| `too many attempts` on the mirror | eight wrong passcodes from one address; it clears itself |

## Checking a change

```
python3 server/tests_api.py
python3 server/tests_solver.py
python3 server/tests_ai.py
python3 server/tests_lovat.py
python3 mirror/tests_mirror.py
```

Plain Python, nothing to install, and all five run on every push.
