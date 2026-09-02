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

PROVIDERS = {
    "anthropic": {"label": "Claude (Anthropic)", "model": "claude-opus-5"},
    "openai": {"label": "OpenAI", "model": "gpt-4o-mini"},
    "gemini": {"label": "Gemini (Google)", "model": "gemini-2.0-flash"},
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


class Client:
    def __init__(self, provider, key, model=None):
        self.provider = (provider or "none").strip().lower()
        self.key = (key or "").strip()
        self.model = (model or "").strip() or (
            PROVIDERS.get(self.provider, {}).get("model"))
        self.down_until = 0.0

    @property
    def ok(self):
        return bool(self.provider in PROVIDERS and self.key and self.model)

    def ask(self, system, user, max_tokens=1200):
        """System + user prompt in, text out.  None means "we could not"."""
        if not self.ok or time.time() < self.down_until:
            return None
        url, headers, payload = self._build(system, user, max_tokens)
        body, status = sources._request(
            url, headers, timeout=60, method="POST",
            data=json.dumps(payload).encode("utf-8"))
        if body is None:
            # A rejected key or an exhausted quota would fail identically on the
            # next press of the button; sit out a minute instead.
            if status in (401, 403, 429):
                self.down_until = time.time() + 60
            return None
        return self._text(body)

    # ------------------------------------------------------------ per provider
    def _build(self, system, user, max_tokens):
        msg = [{"role": "user", "content": user}]
        if self.provider == "anthropic":
            return ("https://api.anthropic.com/v1/messages",
                    {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
                    {"model": self.model, "max_tokens": max_tokens,
                     "system": system, "messages": msg})
        if self.provider == "openai":
            return ("https://api.openai.com/v1/chat/completions",
                    {"Authorization": "Bearer " + self.key,
                     "content-type": "application/json"},
                    {"model": self.model, "max_completion_tokens": max_tokens,
                     "messages": [{"role": "system", "content": system}] + msg})
        return ("https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent",
                {"x-goog-api-key": self.key, "content-type": "application/json"},
                {"systemInstruction": {"parts": [{"text": system}]},
                 "contents": [{"role": "user", "parts": [{"text": user}]}],
                 "generationConfig": {"maxOutputTokens": max_tokens}})

    def _text(self, body):
        try:
            if self.provider == "anthropic":
                parts = [b.get("text", "") for b in body.get("content") or []
                         if b.get("type") == "text"]
                return "\n".join(p for p in parts if p).strip() or None
            if self.provider == "openai":
                choices = body.get("choices") or []
                txt = ((choices[0].get("message") or {}).get("content")
                       if choices else None)
                return (txt or "").strip() or None
            cands = body.get("candidates") or []
            parts = ((cands[0].get("content") or {}).get("parts") or []) if cands else []
            return "\n".join(p.get("text", "") for p in parts).strip() or None
        except (AttributeError, IndexError, TypeError):
            return None


def client(cfg):
    """Build a client from the hub's stored settings."""
    return Client(cfg("aiProvider"), cfg("aiKey"), cfg("aiModel"))
