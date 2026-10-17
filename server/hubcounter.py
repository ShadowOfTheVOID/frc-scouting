"""The camera hub FUEL counter, read into the scouting hub.

The counter (YOLOv26-FRC-Model, `run.py hubgui` or the Hub Counter / Watchtower
apps) already sends its counts to the field: bioarena by UDP, Watchtower by
HTTP. It will not send to a third place - its FanOut refuses a second UDP
target, because one bioarena is the official score - so this side asks instead.
The counter serves `/api/board` with no PIN on purpose (it is the scoreboard
page's feed), and that is all this reads: nothing here can change a count or a
setting on the counter.

What the board says, and what it does not:

  * linked to bioarena (`source` "bioarena"), `score` is bioarena's credited
    count for THIS match, with its match state and clock. That is a match
    score, and the only thing recorded per match below.
  * otherwise (`source` "counter"), `score` is the counter's own total since it
    started. Nothing on the counter knows when a match starts, so it is shown
    live and labelled, never stored against a match.

Standard library only, like the rest of the server.
"""
import json
import threading
import time
import urllib.error
import urllib.request

HUBS = ("red", "blue")
POLL_SECONDS = 1.0
# Past this with no good reply the panel says OFFLINE. Five polls: the counter
# laptop's wifi drops a request now and then, and a panel that flickers between
# LIVE and OFFLINE gets ignored by the time it means it.
STALE_SECONDS = 5.0
TIMEOUT_SECONDS = 2.0
KEEP_MATCHES = 200
# bioarena states with no match being played. Anything else - AUTO, the
# transition, TELEOP, shifts, endgame - is a match in progress.
IDLE_STATES = {"PRE_MATCH", "POST_MATCH", "TIMEOUT_ACTIVE", "POST_TIMEOUT"}


def normalise_url(raw):
    """What was typed into the setup box, as the counter's base address.

    'host', 'host:8090', 'http://host:8090/' and the board page's own address
    ('.../board') are all pasted in practice; all become 'http://host:8090'.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if "://" not in s:
        s = "http://" + s
    s = s.rstrip("/")
    for tail in ("/api/board", "/board"):
        if s.endswith(tail):
            s = s[: -len(tail)]
    return s


def _int(v):
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def parse_board(raw):
    """The counter's board reply, cut down to what the panel draws.

    Unknown or malformed fields are dropped rather than passed through: the
    reply comes from another machine and goes out to every screen."""
    if not isinstance(raw, dict):
        raise ValueError("board reply is not an object")
    score = raw.get("score") if isinstance(raw.get("score"), dict) else {}
    rawc = raw.get("raw") if isinstance(raw.get("raw"), dict) else score
    out = {
        "source": raw.get("source") if raw.get("source") in ("bioarena", "practice", "counter")
        else "counter",
        "score": {h: _int(score.get(h)) for h in HUBS},
        "raw": {h: _int(rawc.get(h)) for h in HUBS},
        "running": bool(raw.get("running")),
        "linked": bool(raw.get("linked")),
        "alert": str(raw["alert"])[:160] if raw.get("alert") else None,
    }
    for k in ("match_state", "shift"):
        if isinstance(raw.get(k), str):
            out[k] = raw[k][:32]
    if isinstance(raw.get("match_time_s"), (int, float)):
        out["match_time_s"] = float(raw["match_time_s"])
    if isinstance(raw.get("hub_active"), dict):
        out["hub_active"] = {h: bool(raw["hub_active"].get(h)) for h in HUBS}
    if isinstance(raw.get("lag_ms"), (int, float)):
        out["lag_ms"] = round(float(raw["lag_ms"]), 1)
    return out


class MatchRecorder:
    """Per-match totals out of the board's stream of states.

    A match is recorded once, when bioarena's state leaves play for an idle
    one, with the last credited score seen while it was playing. Only a board
    linked to bioarena has a match state; counter-only totals are never
    recorded (see the module docstring)."""

    def __init__(self):
        self.playing = False
        self.last = None
        self.label = None

    def feed(self, board, label=None):
        """Returns a finished match's record, or None."""
        state = board.get("match_state")
        if board.get("source") not in ("bioarena", "practice") or not state:
            return None
        if state not in IDLE_STATES:
            self.playing = True
            self.last = dict(board["score"])
            self.label = label
            return None
        if not self.playing:
            return None
        self.playing = False
        rec = {"match": self.label or label, "score": self.last or dict(board["score"]),
               "source": board["source"], "at": time.time()}
        self.last = None
        return rec


def fetch(base, timeout=TIMEOUT_SECONDS):
    req = urllib.request.Request(base + "/api/board",
                                 headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise ValueError(f"HTTP {r.status}")
        return parse_board(json.loads(r.read(65536).decode("utf-8")))


class HubCounterLink:
    """Polls one counter on its own thread; `state()` is what /api/hubcount serves.

    `get_url` is read every poll so a new address from the setup page takes
    effect without a restart. `on_change(state)` fires when what the panel
    shows changes; `on_match(record)` when a match finishes."""

    def __init__(self, get_url, on_change=None, on_match=None, label_now=None,
                 fetch_fn=fetch):
        self.get_url = get_url
        self.on_change = on_change or (lambda s: None)
        self.on_match = on_match or (lambda r: None)
        self.label_now = label_now or (lambda: None)
        self.fetch = fetch_fn
        self.recorder = MatchRecorder()
        self.board = None
        self.ok_at = None
        self.error = None
        self.url = ""
        self.lock = threading.Lock()
        self._shown = None

    def state(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if not self.url:
                status = "off"
            elif self.ok_at and now - self.ok_at <= STALE_SECONDS:
                status = "live"
            else:
                status = "offline"
            return {"status": status, "url": self.url, "board": self.board,
                    "error": self.error if status != "live" else None,
                    "ageSec": round(now - self.ok_at, 1) if self.ok_at else None}

    def poll_once(self, now=None):
        now = time.time() if now is None else now
        url = normalise_url(self.get_url())
        board = err = None
        if url:
            try:
                board = self.fetch(url)
            except (OSError, ValueError, urllib.error.URLError) as e:
                err = str(getattr(e, "reason", None) or e)[:160]
        with self.lock:
            if url != self.url:
                self.url, self.board, self.ok_at = url, None, None
                self.recorder = MatchRecorder()
            if board is not None:
                self.board, self.ok_at, self.error = board, now, None
            else:
                self.error = err
        if board is not None:
            rec = self.recorder.feed(board, self.label_now())
            if rec:
                self.on_match(rec)
        st = self.state(now)
        shown = json.dumps([st["status"], st["board"]], sort_keys=True)
        if shown != self._shown:
            self._shown = shown
            self.on_change(st)
        return st

    def run(self, stop_flag):
        while not stop_flag.is_set():
            try:
                self.poll_once()
            except Exception as e:  # never let the panel's poller die
                self.error = str(e)[:160]
            stop_flag.wait(POLL_SECONDS)
