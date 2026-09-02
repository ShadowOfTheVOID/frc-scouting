"""One small adapter over three chat APIs, and the rules every prompt carries.

Raw HTTP on purpose.  The hub runs on a competition laptop with a stock Python
and no packages - CI installs nothing, deliberately - so a vendor SDK is not
available to us, and one HTTP path is also the only way to serve Anthropic,
OpenAI and Gemini from a single adapter.

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
#: then Gemini, then OpenAI.  One list, because it has to drive three things -
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
]

BY_ID = {m[0]: m for m in MODELS}

PROVIDERS = {"anthropic": "Claude (Anthropic)", "gemini": "Gemini (Google)",
             "openai": "OpenAI"}

#: What the Setup page starts on. Not a fallback: a hub with no model chosen
#: has the AI features off, and must not quietly behave as though it picked one.
DEFAULT_MODEL = "claude-opus-5"

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


class Client:
    def __init__(self, provider, key, model=None):
        self.model = (model or "").strip()
        # A stored provider that disagrees with the model loses: the model is
        # what the request is actually built for.
        self.provider = ((provider_for(self.model) if self.model else None)
                         or (provider or "").strip().lower() or "none")
        self.key = (key or "").strip()
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

        payload = {"systemInstruction": {"parts": [{"text": system}]},
                   "contents": [{"role": "user", "parts": [{"text": user}]}],
                   "generationConfig": {"maxOutputTokens": max_tokens}}
        if effort:
            payload["generationConfig"][effort[0]] = effort[1]
        return ("https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent",
                {"x-goog-api-key": self.key, "content-type": "application/json"},
                payload)

    def _text(self, body):
        try:
            if self.provider == "anthropic":
                stop = body.get("stop_reason")
                parts = [b.get("text", "") for b in body.get("content") or []
                         if b.get("type") == "text"]
                return self._finish("\n".join(p for p in parts if p),
                                    out_of_room=stop == "max_tokens",
                                    declined=stop == "refusal")
            if self.provider == "openai":
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
