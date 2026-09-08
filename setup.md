# Setup — the short version

Every step, in order, with the explanations stripped out — from the ZIP, through every API
key, to the off-site mirror. Start at the top and stop when you have what you need: **Part A**
is the whole app; **Part B** is the mirror and is optional.

For *why* any of this is the way it is, read the [README](README.md) and
[mirror/README.md](mirror/README.md) instead — this page deliberately repeats neither.

---

## Part A — the hub laptop

The one laptop that runs everything. About 20 minutes, once, at home.

### 1. Install Python 3

- **Windows** — [python.org/downloads](https://www.python.org/downloads/), run the installer,
  and **tick "Add python.exe to PATH"** on the first screen. Miss it and nothing else works.
- **Mac** — `python3 --version` in Terminal. A number means you are done.

There is nothing else to install. No `pip`, no dependencies.

### 2. Download the ZIP

Green **Code** button → **Download ZIP**. Unzip it somewhere you will find again — the Desktop
is fine. Keep the folder together; the server, the web pages and the mirror all live in it.

### 3. Start it

- **Windows** — double-click `start-server.bat`
- **Mac** — double-click `start-server.command`

> **Mac, first run:** if it "cannot be opened", the ZIP stripped the execute bit. In Terminal
> type `chmod +x ` (with the space), drag `start-server.command` into the window, press Enter.
> Once, forever. Or skip it and run `python3 server/hub.py` from the folder.

Leave the black window open — closing it stops the server. It prints the three addresses:

```
http://<laptop-ip>:6059/scout        scouts, on phones
http://<laptop-ip>:6059/dashboard    strategy, on any device on the same wifi
http://localhost:6059/               admin panel, this laptop only
```

### 4. Let it through the firewall

**Windows pops up a warning on the first run. Tick "Private networks" and click Allow.**
Click Cancel and phones silently fail to connect, with no other symptom.

### 5. Set the event and the keys

On the laptop itself open **http://localhost:6059/**. It opens read-only — press **UNLOCK**
at the top. It re-locks after ten minutes and on every reload.

Fill in the top three first: **EVENT KEY** (like `2026casf` — the code on frc.events or The
Blue Alliance), **EVENT LEVEL**, and **OUR TEAM** (`6059`).

| Box | Needed? | Costs | Get it at |
|---|---|---|---|
| **NEXUS** | **required** | free | [frc.nexus/api](https://frc.nexus/api) |
| NEXUS WEBHOOK TOKEN | only with a webhook | free | same place, only if you register one |
| THE BLUE ALLIANCE | optional | free | [thebluealliance.com/account](https://www.thebluealliance.com/account) |
| FRC EVENTS USERNAME + TOKEN | optional | free | [frc-events.firstinspires.org](https://frc-events.firstinspires.org/services/API) |
| LOVAT API KEY | optional | free | no page for it — a `curl`, see below |
| AI MODEL + AI KEY | optional | cents per answer | Anthropic, Google, OpenAI or OpenRouter |
| Statbotics | — | free | nothing to do, no key exists |

Do all of this **at home**. A key that turns out to be wrong is not a thing you can replace on
a Saturday morning.

#### Getting each key

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
2. They send back a **username** and an **authorization token**.
3. Username into **FRC EVENTS USERNAME**, token into **FRC EVENTS TOKEN**. Do not paste the
   combined `Basic ...` blob into one box.

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

#### Then save and check

Paste into the box, press **SAVE & REFRESH**, then press **TEST KEYS**.

- The page takes the wrapping off a paste for you — a header name, quotes from a code sample, a
  line break from an email — and shows you what will actually be saved. A key that belongs in a
  different box is refused and told where to go. Nothing saves at all when one box is refused,
  so fix that box and save again.
- **TEST KEYS asks each vendor whether the key works**, and reports each on its own line. `SET`
  beside a box only ever meant that something is stored there. This is the only thing that tells
  a wrong key apart from a service with nothing to say yet — at a competition those look
  identical everywhere else, on purpose.
- **STRATEGY PASSCODE** is optional and gates the picklist and every AI answer. Blank means no
  lock; a space on the end is a lockout nobody ever finds, so it is trimmed for you.
- Changing the **event key** once the hub holds scouting is asked about twice. Nothing is ever
  deleted — the old event comes back if you set its key again.

### 6. Optionally, a password on the panel

```
python3 server/hub.py --set-admin-password
```

Asks twice, writes `ADMIN_PASSWORD_B64` into `.env` beside the hub, and carries on serving.
That file is the only place it lives, the same command changes or removes it, and `.env` is
in `.gitignore` — never commit it. Base64 is not encryption; keep the file off shared drives.

No password set means **UNLOCK** is one button, which is a fine way to run an event.

### 7. Check it from a phone

Same wifi, open `http://<laptop-ip>:6059/scout`. Or open `http://localhost:6059/join` on the
laptop and let scouts scan the QR code.

**That is the whole app.** Everything below is the mirror.

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

## Getting the event back

Mirror → **BACKUP** tab → **DOWNLOAD JSON**. On a fresh install: start the hub, set the same
event key, then dashboard → **SERVER** tab → import the file. Re-importing is a no-op for
anything already there, so it is safe to run twice. The mirror keeps the last 60 copies.

## When something is wrong

| What you see | What to do |
|---|---|
| Phones cannot reach the hub | the Windows firewall prompt was dismissed — allow it on Private networks |
| Admin panel refuses to open on a phone | on purpose; keys are set on the hub laptop only |
| Panel stays locked and complains about `.env` | a typo in `ADMIN_PASSWORD_B64` — re-run `--set-admin-password` |
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
