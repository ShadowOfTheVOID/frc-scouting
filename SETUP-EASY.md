# Scouting setup, step by step

The easy version. One laptop runs everything; scouts use a web page on their phones, so there
is no app to install. Do **Part 1** at home, once, before your first event. It takes about 20
minutes. Tick the boxes as you go.

| Part | | |
|---|---|---|
| [1 · Get it running](#part-1--get-it-running) | **Required** | Python, the hub, Nexus, phones |
| [2 · Extra data sources](#part-2--extra-data-sources) | Optional | TBA, FRC Events, Lovat, AI |
| [3 · Off-site mirror](#part-3--off-site-mirror) | Optional | Needs a web host and a domain |

Want the details behind a step? [setup.md](setup.md) and the [README](README.md) explain
everything.

---

## Part 1 · Get it running

This is the whole app. Once you finish this part, you can scout a real event.

### ☐ 1. Install Python 3 *(hub laptop)*

- **Windows:** go to [python.org/downloads](https://www.python.org/downloads/), click the big
  yellow button and run the installer.
- **Mac:** open Terminal and type `python3 --version`. If it prints a number like 3.11, you
  already have it. If it offers developer tools, say yes.

> ⚠️ **Windows:** on the first installer screen, tick **"Add python.exe to PATH"**. If you miss
> it, nothing else works. Re-run the installer to fix it.

That's the only thing you install. No pip, no packages.

### ☐ 2. Download the files

On the GitHub page for **frc-scouting**, click the green **Code** button, then **Download ZIP**.
Unzip it somewhere easy to find, like the Desktop. Keep the folder together.

### ☐ 3. Start the hub

- **Windows:** double-click `start-server.bat`
- **Mac:** double-click `start-server.command`. The first time, right-click it and choose
  **Open** instead.

A black window opens. **Leave it open.** Closing it stops the server.

> **Mac says "cannot be opened"?** In Terminal, type `chmod +x ` (with a space), drag
> `start-server.command` into the window and press Enter. You only do this once.

### ☐ 4. Allow phones through the firewall *(Windows only)*

The black window asks whether to allow phones through the firewall. **Type yes**, then confirm
when Windows asks. If Windows shows its own popup, tick **Private networks** and click
**Allow**.

> ⚠️ **This is the #1 setup mistake.** If you skip it, the laptop looks fine but every phone
> times out.

### ☐ 5. Open the admin panel

The first time, it opens in your browser by itself. If it doesn't, go to
`http://localhost:6059/` on the hub laptop. It only works on that laptop, on purpose.

You'll see a **START HERE** checklist. Each line ticks itself off as you finish it. If the
panel is locked, press **UNLOCK**.

### ☐ 6. Pick your event

Type **6059** into **OUR TEAM** and press **FIND MY EVENTS**. Pick the event, and the hub fills
in the event key and level for you. You can also type a key like `2026casf` yourself.

### ☐ 7. Add the Nexus key

1. Sign in at [frc.nexus](https://frc.nexus) with your team's account.
2. Go to [frc.nexus/api](https://frc.nexus/api) and generate an API key. It's free.
3. Paste it into the **NEXUS** box.

Nexus tells the hub when a match is on the field, which starts scouting on all six phones.
Without it, every scout has to tap **THEY'RE ON THE FIELD** by hand every match.

### ☐ 8. Save and test

Press **SAVE & REFRESH**. The first time, it also checks that each key really works and shows
what each service said. Press **TEST KEYS** any time to check again.

### ☐ 9. Try it on a phone

Put a phone on the **same wifi** as the laptop. On the laptop, open
`http://localhost:6059/join` and scan the QR code with the phone. The scouting screen should
load.

The black window also lists the addresses: `/scout` for phones, `/dashboard` for the strategy
screen on any other laptop or tablet.

### ☐ 10. Train your scouts *(before the event)*

With the hub running, have scouts open `/tutorial` on their phones. It's linked from the join
screen. About 4 minutes, ending in a practice match. Nothing they do there is saved.

### ☐ 11. Back up your keys

All your keys live in one file called `.env` in the folder. Copy it to a USB stick. With that
file, any laptop with Python and the folder becomes a spare hub.

> ⚠️ **Keep it private.** Don't upload it, commit it to GitHub, or put it on a shared drive.
> Anyone with the file can read your keys.

Optional: lock the admin panel with a password. Run this in the folder (on Windows, type
`python` instead of `python3`):

```
python3 server/hub.py --set-admin-password
```

---

## At every event

Keys never expire, so you set them up once. At each competition you only:

1. Start the hub (double-click **start-server**).
2. Change the **event key** in the admin panel. Old events are kept.
3. Check that one phone connects.

---

## Part 2 · Extra data sources

All optional, and a first event is fine without any of them. In the admin panel they're under
**THE OPTIONAL ONES**. Paste each key, then **SAVE & REFRESH** and **TEST KEYS**.
**Statbotics** needs no key at all.

### The Blue Alliance — official results and climbs. Free.

1. Sign in at [thebluealliance.com/account](https://www.thebluealliance.com/account) with Google.
2. Under **Read API Keys**, type a description like *6059 scouting hub* and add the key.
3. Paste the 64-character key into **THE BLUE ALLIANCE**.

### FRC Events — official results a few minutes sooner. Free.

1. Request a key at
   [frc-events.firstinspires.org/services/API](https://frc-events.firstinspires.org/services/API).
2. FIRST sends a **username** and a **token**. The token looks like
   `1f0c8b3a-77d2-4a51-9d6e-3b0a5c9e4477`.
3. Username into **FRC EVENTS USERNAME**, token into **FRC EVENTS TOKEN**.

### Lovat — other teams' scouting notes. Free, takes 10 minutes.

1. Sign in at [dashboard.lovat.app](https://dashboard.lovat.app). Go to **Settings → Team email
   → Change** and click the link in the email **within 20 minutes**.
2. In Chrome or Edge on the dashboard, press **F12**, open **Network**, tick **Preserve log**,
   and type `api.lovat.app` in the filter.
3. Reload and wait for the app to finish loading.
4. Find the **profile** row whose type is **fetch**. Right-click → **Copy** → **Copy as cURL**.
5. Double-click `get-lovat-key.bat` (Windows) or `get-lovat-key.command` (Mac). It reads your
   clipboard and saves the key for you.
6. Restart the hub, then press **TEST KEYS**.

> ⚠️ **Never share what you copied.** It works like your Lovat password for 72 hours. Don't
> paste it into chats or screenshots.

### AI answers — costs a few cents per question.

Pick a model from the **AI MODEL** dropdown, then paste that company's key below it. Each answer
costs a fraction of a cent to a few cents, and only when someone presses a button.

- **Claude:** [console.anthropic.com](https://console.anthropic.com) → API keys. Starts `sk-ant-`
- **Gemini:** [aistudio.google.com/apikey](https://aistudio.google.com/apikey). Starts `AIza`
- **OpenAI:** [platform.openai.com/api-keys](https://platform.openai.com/api-keys). Starts `sk-proj-`
- **OpenRouter:** [openrouter.ai/keys](https://openrouter.ai/keys). Starts `sk-or-`

The account needs a little credit. Leave the model on *none* to turn AI off.

### Strategy passcode — locks the picklist. You make it up.

Type any passcode into the strategy passcode box. It protects the picklist and the AI answers.
Leave it blank for no lock.

---

## Part 3 · Off-site mirror

A read-only copy of your event on the internet, so the data survives if the laptop dies and the
team can check it from anywhere. **Skip this if you don't have a web host and a domain name.**
It's the most technical part; a mentor who's used a Linux server before should do it.

### ☐ 1. Make two secrets

**Push key**: how the hub proves it's the hub. Generate it:

```
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**View passcode**: what the team types to read the site. Make it short and easy to type on a
phone.

### ☐ 2. Get a server

Any Ubuntu server works. Oracle Cloud's
[Always Free tier](https://www.oracle.com/cloud/free/) is enough: an Ubuntu 24.04 instance with
shape `VM.Standard.E2.1.Micro`, a public IP, and ports 80 and 443 open.

> ⚠️ **Oracle needs ports opened twice:** once in the subnet's Security List, and once on the
> machine itself. [setup.md](setup.md#no-host-yet-oracle-clouds-free-tier) has the exact
> commands.

On the server, install the tools:

```
sudo apt update && sudo apt install -y python3 git caddy
```

### ☐ 3. Install the mirror

```
sudo useradd --system --home /opt/frc-scouting frcmirror
sudo git clone https://github.com/$(echo U2hhZG93T2ZUaGVWT0lE | base64 -d)/frc-scouting /opt/frc-scouting
sudo cp /opt/frc-scouting/mirror/deploy/frc-mirror.service /etc/systemd/system/
sudo systemctl edit frc-mirror
```

In the editor that opens, paste this with your two secrets, then save:

```
[Service]
Environment=MIRROR_PUSH_KEY=your-generated-push-key
Environment=MIRROR_VIEW_PASSCODE=your-view-passcode
```

Start it:

```
sudo systemctl enable --now frc-mirror
```

### ☐ 4. Point a domain at it and turn on HTTPS

1. At your domain provider (Wix: **Domains → ⋯ → Manage DNS Records**), add an **A record**:
   host `scouting`, value = the server's public IP. Leave the other records alone.
2. Copy the Caddy config, change the hostname inside it to yours, and reload:

```
sudo cp /opt/frc-scouting/mirror/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

After DNS updates (minutes to an hour), `https://scouting.yourdomain` should show a passcode
prompt. Caddy handles the certificate.

### ☐ 5. Connect the hub to it *(hub laptop)*

In the admin panel, open **OFF-SITE MIRROR**. Enter the mirror address
(`https://scouting.yourdomain`) and the push key. Press **SAVE & REFRESH**, **TEST KEYS**, then
**PUSH NOW**. After that it updates by itself about once a minute.

### ☐ 6. Give the team the view passcode

Share only the **view passcode**. Never share the push key; anyone with it can overwrite the
event.

---

## If something goes wrong

| What you see | What to do |
|---|---|
| Phones can't reach the hub | Check the phone is on the same wifi. On Windows, the firewall prompt was probably dismissed: restart the hub and answer yes. |
| Start file says Python isn't installed | Re-run the Python installer and tick "Add python.exe to PATH". |
| Admin panel won't open on a phone | That's intentional. Keys are set on the hub laptop only. |
| FIND MY EVENTS shows nothing | The laptop has no internet, or the schedule isn't out yet. Type the event key by hand. |
| Panel stays locked and mentions `.env` | Run `--set-admin-password` again. |
| Mirror shows 404 or the team website | Use the `scouting.` subdomain, not the main domain. |
| Mirror rejected the push key | The key in the panel doesn't match the one on the server. Check them on both sides. |
