"""One small adapter over four chat APIs, and the rules every prompt carries.

Raw HTTP on purpose.  The hub runs on a competition laptop with a stock Python
and no packages - CI installs nothing, deliberately - so a vendor SDK is not
available to us, and one HTTP path is also the only way to serve Anthropic,
OpenAI, Gemini and OpenRouter from a single adapter.

Like every client in sources.py this returns None on failure rather than
raising.  A model that is unreachable at a venue is the normal case, not an
error worth a dialog.

The governing rule is in GROUND_RULES below and it is not decoration: these
features exist to surface context that is already in the data - which match a
claim rests on, which scout said it, which block a number came from - and to
add nothing.  Nothing generated here is ever written back as data.
"""
import json
import time

import sources

#: Every model the Setup page offers, in the order it offers them: Claude,
#: then Gemini, then OpenAI, then the same three through OpenRouter.  One list,
#: because it has to drive three things -
#: the dropdown, which provider a model belongs to, and the shape of the
#: request that model accepts - and three copies of that would drift.
#:
#: `effort` is whether the model takes a reasoning knob at all.  It is a
#: property of the model, not the provider: `output_config.effort` is right for
#: Opus 5 and a 400 on Haiku 4.5.  Unknown models get no knob, which is the one
#: shape that cannot be rejected.
#:
#: `fallbacks` asks Anthropic to retry a refusal on another model inside the
#: same call, which Anthropic recommends by default for these two.
MODELS = [
    # id                  provider     label                price          effort fallbacks
    ("claude-opus-5",     "anthropic", "Claude Opus 5",     "$5 / $25",     True,  True),
    ("claude-fable-5-1",  "anthropic", "Claude Fable 5.1",  "$10 / $50",    True,  True),
    ("claude-sonnet-5",   "anthropic", "Claude Sonnet 5",   "$2 / $10",     True,  False),
    ("claude-haiku-4-5",  "anthropic", "Claude Haiku 4.5",  "$1 / $5",      False, False),
    ("gemini-3.7-flash",  "gemini",    "Gemini 3.7 Flash",  "$0.75 / $3.75", True, False),
    ("gemini-3.6-flash",  "gemini",    "Gemini 3.6 Flash",  "$0.75 / $3.75", True, False),
    ("gemini-3.1-pro",    "gemini",    "Gemini 3.1 Pro",    "$2 / $12",     True,  False),
    ("gpt-5.6-sol",       "openai",    "GPT-5.6 Sol",       "$4 / $20",     True,  False),
    ("gpt-5.6-terra",     "openai",    "GPT-5.6 Terra",     "$2 / $12",     True,  False),
    ("gpt-5.6-luna",      "openai",    "GPT-5.6 Luna",      "$0.20 / $1.20", True, False),
    # OpenRouter is not a model-maker: it is one key and one bill in front of
    # everybody else's models, which for a team already juggling six services
    # is the whole point of it.  Its ids are always `vendor/model`, and any of
    # the hundreds it carries can be typed into the MODEL ID box - these three
    # are here so the list shows what the ids look like.  The prices are the
    # makers' own; OpenRouter takes its cut when the credit is bought, not per
    # token.
    ("anthropic/claude-opus-5",  "openrouter", "Claude Opus 5 (OpenRouter)",
     "$5 / $25", True, False),
    ("google/gemini-3.7-flash",  "openrouter", "Gemini 3.7 Flash (OpenRouter)",
     "$0.75 / $3.75", True, False),
    ("openai/gpt-5.6-terra",     "openrouter", "GPT-5.6 Terra (OpenRouter)",
     "$2 / $12", True, False),
]

BY_ID = {m[0]: m for m in MODELS}

PROVIDERS = {"anthropic": "Claude (Anthropic)", "gemini": "Gemini (Google)",
             "openai": "OpenAI", "openrouter": "OpenRouter"}

#: The default, in both senses: what the Setup page starts on, and what a hub
#: that has a key but has never picked a model uses. Turning the features off
#: is a deliberate choice - the `none` option, which stores `aiProvider` as
#: "none" - and not the same thing as never having chosen.
DEFAULT_MODEL = "claude-opus-5"

#: What `aiProvider` holds when a lead has picked "none — no AI features".
OFF = "none"

#: Reasoning is on by default on every model in the list above, and the tokens
#: it spends come out of the same budget as the answer.  Left alone, Gemini
#: thinks at HIGH and can use the whole allowance before writing a word, which
#: reads at a competition as "the model could not be reached" on a perfectly
#: good key.  Summarising twenty scout notes does not need deep reasoning, so
#: every provider is turned down to its low setting.
EFFORT = {
    "anthropic": ("output_config", {"effort": "low"}),
    "openai": ("reasoning_effort", "low"),
    # thinkingLevel and thinkingBudget together are a 400; only ever send this.
    "gemini": ("thinkingConfig", {"thinkingLevel": "LOW"}),
    # OpenRouter takes one knob for every model it carries and translates it
    # into whatever the maker underneath actually wants.
    "openrouter": ("reasoning", {"effort": "low"}),
}

#: Prepended to every system prompt.  The app's whole doctrine is that its
#: sources are kept separate because mixing them is how a picklist ends up
#: confidently wrong; a model that smooths over a disagreement, or fills a gap
#: from what it happens to know about a team, would be doing exactly that.
GROUND_RULES = """You are reading scouting data for an FRC team at a competition.

Absolute rules:
- Use ONLY the JSON given to you in this message. You have no other knowledge of
  these teams, this event, or this game. Do not use anything you may have seen
  about a team elsewhere.
- Never state a number that is not in the JSON. Do not compute new numbers.
- Name where each claim comes from: the block it sits in (exact, estimated,
  observed, epa, lovat) or, for anything from a note, the match and the scout.
  If you cannot cite a claim, do not write it.
- Where the data is thin or absent, say so plainly - "only 2 matches scouted",
  "no scout has said". Never fill a gap.
- Where scouts disagree, report the disagreement. Do not resolve it.
- The blocks mean different things and must not be mixed: `exact` is official
  results, `estimated` is this app's solver and always carries a band,
  `observed` is our own scouts, `epa` is Statbotics, `lovat` is other teams'
  scouts and is unverified.
- Be brief and plain. No preamble, no encouragement, no advice about scouting."""

#: OpenRouter's catalogue. Public and unauthenticated, so it can say whether a
#: model id exists but never whether a key is good.
OPENROUTER_MODELS = "https://openrouter.ai/api/v1/models"

OUT_OF_ROOM = "the answer ran out of room - try again"
DECLINED = "the model declined to answer that"
UNREACHABLE = "the model could not be reached"


def provider_for(model_id):
    """Which API a model belongs to.

    The catalogue answers for anything on the list.  The prefix rule is for a
    model typed in by hand after this file was written, which is the whole
    reason the Setup page keeps a free-text box.
    """
    m = (model_id or "").strip().lower()
    if m in BY_ID:
        return BY_ID[m][1]
    # A slash is what an OpenRouter id has and no maker's own id does: they
    # name the maker first, `anthropic/claude-opus-5`.  Checked before the
    # prefixes below, so a model routed through OpenRouter is never mistaken
    # for the same model bought direct - different endpoint, different key.
    if "/" in m:
        return "openrouter"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    return None


def catalogue():
    """The list as the Setup page needs it, in order."""
    return [{"id": i, "provider": p, "label": lb, "price": pr}
            for i, p, lb, pr, _, _ in MODELS]


def _text(v):
    """Whatever the settings row holds, as a stripped string.

    These three come out of the kv store, which takes what /api/config was
    given. A value that was not a string used to raise in this constructor -
    and Client is built inside diag(), so the diagnostics endpoint stopped
    answering and the dashboard's SERVER tab went stale for the event.
    """
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v).strip())


class Client:
    def __init__(self, provider, key, model=None):
        stored = _text(provider).lower()
        self.model = _text(model)
        # Never chosen falls back to the default; chosen "none" stays off, even
        # with a key sitting there. Those are different states and conflating
        # them would either ignore the off switch or leave a keyed hub inert.
        if not self.model and stored != OFF:
            self.model = DEFAULT_MODEL
        # A stored provider that disagrees with the model loses: the model is
        # what the request is actually built for.
        self.provider = ((provider_for(self.model) if self.model else None)
                         or stored or OFF)
        self.key = _text(key)
        self.down_until = 0.0

    @property
    def ok(self):
        return bool(self.provider in PROVIDERS and self.key and self.model)

    @property
    def label(self):
        row = BY_ID.get(self.model)
        return row[2] if row else (self.model or None)

    def _flag(self, i):
        row = BY_ID.get(self.model)
        # An unknown model gets neither knob: the plainest request is the one
        # that cannot be rejected for a parameter the model does not take.
        return bool(row[i]) if row else False

    def ask(self, system, user, max_tokens=4000):
        """System + user prompt in, `(text, reason)` out - exactly one is set.

        The reason matters. Every failure used to read the same way, and "could
        not be reached" is a lie when the truth is that the answer was cut off
        or the model declined.
        """
        if not self.ok:
            return None, UNREACHABLE
        if time.time() < self.down_until:
            return None, UNREACHABLE
        url, headers, payload = self._build(system, user, max_tokens)
        body, status = sources._request(
            url, headers, timeout=60, method="POST",
            data=json.dumps(payload).encode("utf-8"))
        if body is None:
            # A rejected key or an exhausted quota would fail identically on the
            # next press of the button; sit out a minute instead.
            if status in (401, 403, 429):
                self.down_until = time.time() + 60
            return None, UNREACHABLE
        return self._text(body)

    # ------------------------------------------------------------ per provider
    def _build(self, system, user, max_tokens):
        msg = [{"role": "user", "content": user}]
        effort = EFFORT[self.provider] if self._flag(4) else None

        if self.provider == "anthropic":
            headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                       "content-type": "application/json"}
            payload = {"model": self.model, "max_tokens": max_tokens,
                       "system": system, "messages": msg}
            if effort:
                payload[effort[0]] = effort[1]
            if self._flag(5):
                headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
                payload["fallbacks"] = "default"
            return "https://api.anthropic.com/v1/messages", headers, payload

        if self.provider == "openai":
            payload = {"model": self.model, "max_completion_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system}] + msg}
            if effort:
                payload[effort[0]] = effort[1]
            return ("https://api.openai.com/v1/chat/completions",
                    {"Authorization": "Bearer " + self.key,
                     "content-type": "application/json"}, payload)

        if self.provider == "openrouter":
            # OpenAI's shape, deliberately - that is what OpenRouter serves -
            # but `max_tokens` rather than `max_completion_tokens`, which is
            # the name it documents for every model it fronts.
            payload = {"model": self.model, "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system}] + msg}
            if effort:
                payload[effort[0]] = effort[1]
            return ("https://openrouter.ai/api/v1/chat/completions",
                    {"Authorization": "Bearer " + self.key,
                     # What OpenRouter shows beside the spend on their own
                     # dashboard. It names the app, never the team or the event.
                     "X-Title": "FRC Scouting Hub",
                     "content-type": "application/json"}, payload)

        payload = {"systemInstruction": {"parts": [{"text": system}]},
                   "contents": [{"role": "user", "parts": [{"text": user}]}],
                   "generationConfig": {"maxOutputTokens": max_tokens}}
        if effort:
            payload["generationConfig"][effort[0]] = effort[1]
        return ("https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent",
                {"x-goog-api-key": self.key, "content-type": "application/json"},
                payload)

    def verify(self):
        """Ask the vendor whether this key works, without spending anything.

        The three makers list their models on a GET that needs the same key as
        a completion and costs nothing, and OpenRouter has a route that answers
        for the key alone, so the TEST KEYS button can answer for real rather
        than by guessing at the shape of the string.  The model is checked as
        well: a key can be perfectly good while the model beside it is one this
        account cannot reach, or one nobody carries under that id, and all of
        those failures are indistinguishable from the panel that just says
        "unreachable".
        """
        if not self.model:
            return sources.verdict("unset", "no model chosen")
        # A typed id that routes nowhere used to land in the line above and
        # read as "no model chosen", which is the one thing it is not.
        if self.provider not in PROVIDERS:
            return sources.verdict("bad", f"no idea which company makes `{self.model}` - "
                                          "pick a model from the list, or put its maker in "
                                          "front of it (`meta-llama/llama-4-scout`) to go "
                                          "through OpenRouter")
        if not self.key:
            return sources.verdict("unset", "a model is chosen but no key is saved")
        url, headers = self._models_request()
        body, status = sources._request(url, headers, timeout=15)
        if body is None:
            return sources._rejection(status, PROVIDERS[self.provider])
        ids = self._model_ids(body)
        if self.provider == "openrouter" and not ids:
            # /key proved the key and says nothing about models; the catalogue
            # lists the models and needs no key, which is exactly why it cannot
            # be the call the key is tested against. Both are free.
            listing, _ = sources._request(OPENROUTER_MODELS, timeout=15)
            ids = self._model_ids(listing)
        if ids and not any(self.model == i or i.startswith(self.model) for i in ids):
            # OpenRouter's catalogue is the same for everybody, so a miss there
            # is a typo in the id and not an account that cannot reach it.
            router = self.provider == "openrouter"
            miss = "does not carry a model called" if router else "does not list"
            scope = "" if router else " for this account"
            return sources.verdict(
                "warn", f"the key works, but {PROVIDERS[self.provider]} {miss} "
                        f"`{self.model}`{scope} - check the model name")
        return sources.verdict("ok", f"key accepted for {self.label or self.model}")

    def _models_request(self):
        """The free route that proves the key, per provider.

        For the three makers that is their model list; for OpenRouter it is
        /key, because their model list is served to anybody who asks and would
        come back a cheerful 200 on a key that was revoked last week.
        """
        if self.provider == "anthropic":
            return ("https://api.anthropic.com/v1/models",
                    {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
        if self.provider == "openai":
            return "https://api.openai.com/v1/models", {"Authorization": "Bearer " + self.key}
        if self.provider == "openrouter":
            # Not the model list: OpenRouter serves that to anyone, so a
            # revoked key would come back "ok". This route is the one that
            # answers for the key itself, and it is free too.
            return ("https://openrouter.ai/api/v1/key",
                    {"Authorization": "Bearer " + self.key})
        return ("https://generativelanguage.googleapis.com/v1beta/models",
                {"x-goog-api-key": self.key})

    def _model_ids(self, body):
        """Model ids out of any of the shapes above, or [] if it is another.

        An empty list means "we could not tell", which reads downstream as no
        complaint about the model - the key was accepted and that is the thing
        being tested here.  OpenRouter's /key answers with an object rather
        than a list of models, so it lands here as exactly that.
        """
        if not isinstance(body, dict):
            return []
        rows = body.get("data") or body.get("models") or []
        out = []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                name = r.get("id") or r.get("name") or ""
                # Gemini answers "models/gemini-3.7-flash" and wants the tail.
                # An OpenRouter id is `vendor/model` all the way through, so
                # cutting at the slash there would compare half a name.
                out.append(name.rsplit("/", 1)[-1] if self.provider == "gemini" else name)
        return [o for o in out if o]

    def _text(self, body):
        try:
            if self.provider == "anthropic":
                stop = body.get("stop_reason")
                parts = [b.get("text", "") for b in body.get("content") or []
                         if b.get("type") == "text"]
                return self._finish("\n".join(p for p in parts if p),
                                    out_of_room=stop == "max_tokens",
                                    declined=stop == "refusal")
            # OpenRouter answers in OpenAI's shape and normalises the finish
            # reason into OpenAI's words, whichever maker actually replied.
            if self.provider in ("openai", "openrouter"):
                choice = (body.get("choices") or [{}])[0]
                txt = (choice.get("message") or {}).get("content") or ""
                return self._finish(txt, out_of_room=choice.get("finish_reason") == "length",
                                    declined=choice.get("finish_reason") == "content_filter")
            cand = (body.get("candidates") or [{}])[0]
            fin = cand.get("finishReason")
            # A thought part is the model's reasoning, not its answer. Printing
            # it as the digest would be worse than printing nothing.
            parts = [p.get("text", "") for p in (cand.get("content") or {}).get("parts") or []
                     if not p.get("thought")]
            return self._finish("\n".join(p for p in parts if p),
                                out_of_room=fin == "MAX_TOKENS",
                                declined=fin in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"))
        except (AttributeError, IndexError, KeyError, TypeError):
            return None, UNREACHABLE

    @staticmethod
    def _finish(text, out_of_room=False, declined=False):
        text = (text or "").strip()
        if text:
            return text, None
        if declined:
            return None, DECLINED
        if out_of_room:
            # Reasoning spent the whole budget. Naming that is what tells a
            # lead to press again rather than go hunting for a network fault.
            return None, OUT_OF_ROOM
        return None, UNREACHABLE


def client(cfg):
    """Build a client from the hub's stored settings."""
    return Client(cfg("aiProvider"), cfg("aiKey"), cfg("aiModel"))
