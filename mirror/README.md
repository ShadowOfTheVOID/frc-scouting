# The off-site mirror

A second website, on a host you already pay for, that holds a copy of the event
and lets you read it from anywhere.

The scouting hub is one laptop on venue wifi. It gets carried around all day,
unplugged, closed, and put in a bag between quals, and it is behind whatever
network the venue runs — nothing on the internet can reach in and ask it for
anything. `data/snapshots/` survives a corrupt database. It does not survive
the laptop.

So this runs somewhere else, and the hub pushes to it. That is the only
direction that works from behind a venue's NAT, and it buys two things:

- **The event survives the laptop.** If the hub is dropped, stolen, or wiped,
  the mirror's JSON export drops straight into a fresh install and the event is
  back.
- **You can read the numbers from anywhere.** A strategy lead in the stands on
  cell data, a mentor at home, a driver in the queue line — none of them need
  to be on venue wifi, and none of them can change anything.

It is deliberately the dumbest thing that could work. The mirror does not
scout, does not solve, holds no API key for anybody, and never opens a
connection of its own. Two kinds of thing arrive from the hub — a bundle, and
the pit photos it does not already have — and everything else is reading those
back out.

---

## Set it up

### 1. Make two secrets

They are **not** the same secret, and the whole design rests on that.

```
python3 -c "import secrets; print('push key:', secrets.token_urlsafe(32))"
```

| | what it is for | who has it |
|---|---|---|
| `MIRROR_PUSH_KEY` | the hub proves it is the hub. **Write.** | one settings field, on one laptop |
| `MIRROR_VIEW_PASSCODE` | a person proves they are on the team. **Read.** | shared around the team all weekend |

The passcode gets typed on phones in a stands and passed to whoever asks. If it
were also the push key, anybody it reached could *overwrite* the event. Pick
something a tired 15-year-old can type; pick the push key with a generator.

### 2. Put it on the host

```
sudo useradd --system --home /opt/frc-scouting frcmirror
sudo git clone https://github.com/ShadowOfTheVOID/frc-scouting /opt/frc-scouting

sudo cp mirror/deploy/frc-mirror.service /etc/systemd/system/
sudo systemctl edit frc-mirror        # add the two Environment= lines
sudo systemctl enable --now frc-mirror
```

`systemctl edit` and not this file, and not the command line: an argument is
visible in `ps` to every user on the host, and this repository is public.

A `.env` beside the checkout works too and is read on startup, which is what
makes this runnable on a laptop for five minutes to see it work — but on a real
host prefer the systemd lines above. A real environment variable always beats
the file, so setting both is not ambiguous.

```
[Service]
Environment=MIRROR_PUSH_KEY=the-long-generated-one
Environment=MIRROR_VIEW_PASSCODE=the-one-people-type
```

### 3. Put TLS in front of it

The mirror listens on `127.0.0.1` and speaks plain HTTP on purpose. Something
that already has a certificate goes in front:

```
sudo cp mirror/deploy/Caddyfile /etc/caddy/Caddyfile     # edit the hostname
sudo systemctl reload caddy
```

Caddy gets and renews the certificate itself. The unit passes `--behind-proxy`,
which is what makes the mirror believe `X-Forwarded-For` — without it every
request looks like it came from `127.0.0.1` and one wrong passcode would
throttle the whole internet.

Point the DNS at the host and open `https://scouting.systemoverload.org`.

### 4. Tell the hub about it

On the hub laptop, in the admin panel at `http://localhost:6059/` — press
**UNLOCK** at the top of that page first, it opens read-only — in
**OFF-SITE MIRROR**:

- **Mirror address** — `https://scouting.systemoverload.org`. A trailing slash
  or a pasted `/api/push` is fine; it gets trimmed.
- **Mirror push key** — `MIRROR_PUSH_KEY`.

**Save**, then **TEST KEYS**. That asks this mirror two questions and reports
them separately, because they have the same symptom and different fixes: is
there a mirror at that address at all (`/api/status`, open to anyone), and would
it accept your push key (`/api/ping`, which needs it). **PUSH NOW** then sends
the event for real and says what happened on the line beside it.

From then on it goes by itself, about once a minute, and the dashboard's
**SERVER** tab carries an `off-site mirror` line that turns to `RETRYING` if it
stops working.

> A mirror deployed before `/api/ping` existed answers 404 to the key half, and
> the hub says so rather than calling the key bad — update the mirror, or press
> **PUSH NOW**, which has always been the honest test.

---

## Running it by hand

For a laptop, a test, or a host with no systemd:

```
MIRROR_PUSH_KEY=... MIRROR_VIEW_PASSCODE=... python3 mirror/server.py
```

| flag | |
|---|---|
| `--port` | default 8060 (`MIRROR_PORT`) |
| `--bind` | default `127.0.0.1`. `0.0.0.0` only if something else terminates TLS |
| `--db` | default `mirror/data/mirror.db` (`MIRROR_DB`) |
| `--behind-proxy` | trust `X-Forwarded-For`, send HSTS |
| `--open` | no read passcode at all — anyone with the address reads everything |

It refuses to start with no push key, and refuses to start with no read
passcode unless you say `--open` and mean it.

---

## What crosses the wire

Once a minute the hub builds one bundle and gzips it: the event, teams,
matches, flags, rankings, EPA, the alliance and pit-map data from Nexus, the
picklist, the whole analytics payload the dashboard draws from, every scout and
pit record, and the two per-team CSVs the dashboard's SERVER tab produces —
carried verbatim rather than rebuilt, so a column cannot mean one thing on the
hub and another off-site.

Three things do **not**:

- **Nothing is sent when nothing changed.** The bundle is hashed without its
  timestamp; an unchanged event is skipped. A venue uplink is shared with
  several thousand people and their phones.
- **A photo crosses once.** The mirror answers each push with the photo ids it
  is missing and the hub sends a few per push, so a pit-scouting morning
  trickles across over several minutes and nothing is ever re-sent.
- **Per-scout quality scores are never mirrored at all.** They name individuals
  and grade them. The hub already shows them only to the lead, on the reasoning
  that a scoreboard of who is worst at their job costs morale and buys nothing;
  a copy on the public internet behind one shared code is not the place to relax
  that. Everything on the mirror is about robots.

No API key ever leaves the hub. The mirror has no use for one — it never calls
The Blue Alliance, Nexus, Statbotics, Lovat or any model.

One more thing crosses, and it carries nothing: `/api/ping`, a POST that needs
the push key and answers with the protocol version, how many events are stored
and when the last push arrived. It exists so the hub's **TEST KEYS** button can
prove a push key without spending a whole event on a venue uplink to find out.
It is the same key check `/api/push` makes, and the view passcode is no more a
key here than it is there.

---

## Getting the event back

**BACKUP** tab → **DOWNLOAD JSON**. That file is the same shape the hub's own
export is, so on a fresh install:

1. Start the hub, set the same event key at `http://localhost:6059/`.
2. Dashboard → **SERVER** tab → import the file.

Re-importing is a no-op for anything already there, under the same
last-write-wins rule as a phone syncing, so it is always safe to try — and safe
to do twice if you are not sure the first one finished.

The mirror keeps the last 60 distinct copies, not just the newest, because
*"the database looks wrong"* is one of the failures this exists for. Every one
of them is a download link on the same tab.

The team-summary and Lovat CSVs are there too, for a spreadsheet.

---

## What it looks like when it breaks

| what you see | what it is | what to do |
|---|---|---|
| Setup page: `Last push failed: could not be reached` | the laptop has no internet, or the address is wrong | check the address in a browser on the laptop |
| Setup page: `the mirror rejected the push key` | the key here is not `MIRROR_PUSH_KEY` there | `systemctl show frc-mirror -p Environment` on the host |
| Mirror header turns amber | nothing has arrived for over five minutes | usually the laptop moved networks; it catches up on its own |
| Mirror header turns red | nothing for over an hour | the hub is off, or off the internet. **Everything on screen is that old** |
| Mirror says "never received anything" | it is up and the hub has never reached it | finish step 4 on the laptop |
| `too many attempts - wait ten minutes` | eight wrong passcodes from one address | it clears itself |
| Dashboard SERVER tab: `off-site mirror RETRYING` | same as the first two rows, seen from the venue | the reason is on the setup page |

A failing mirror never degrades anything at the venue. Scouting, syncing, the
solver, the picklist and the dashboard do not know it exists.

---

## The tests

```
python3 mirror/tests_mirror.py
```

Both halves on real sockets — a hub with a full demo event on one port, the
mirror on another, and a real push between them. It gates the claim the whole
thing rests on: that the file the mirror hands back rebuilds a lost hub.
