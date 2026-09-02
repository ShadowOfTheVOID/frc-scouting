#!/usr/bin/env python3
"""Regression gate for the AI adapter.  Run: python3 server/tests_ai.py

No network and no key: `sources._request` is replaced with a stub that records
what would have been sent and hands back a canned reply.

These cover the failures a model change makes silently rather than loudly.
Every model on the list reasons before it answers, out of the same token
budget as the answer, and each vendor turns that down with a different
parameter that the other two reject.  A wrong knob is a 400; a missing one is
an empty panel at a competition on a perfectly good key.
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import ai  # noqa: E402
import sources  # noqa: E402


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    return ok


class Stub:
    """Captures the request, returns whatever reply the test asks for."""

    def __init__(self, reply=None, status=200):
        self.reply, self.status = reply, status
        self.url = self.headers = self.payload = None

    def __call__(self, url, headers=None, timeout=None, method=None, data=None, **kw):
        self.url, self.headers = url, dict(headers or {})
        self.payload = json.loads(data.decode("utf-8")) if data else None
        return self.reply, self.status


def run(model, reply, status=200, key="k"):
    real = sources._request
    stub = Stub(reply, status)
    sources._request = stub
    try:
        text, reason = ai.Client(None, key, model).ask("system", "user", 4000)
    finally:
        sources._request = real
    return stub, text, reason


def test_routing():
    ok = True
    ok &= check("a listed model knows its own provider",
                [ai.provider_for(m) for m in ("claude-opus-5", "gemini-3.7-flash", "gpt-5.6-luna")]
                == ["anthropic", "gemini", "openai"])
    ok &= check("so does a model released after this list was written",
                ai.provider_for("claude-opus-9") == "anthropic"
                and ai.provider_for("gemini-9-ultra") == "gemini"
                and ai.provider_for("gpt-9") == "openai")
    ok &= check("a name belonging to nobody routes nowhere, rather than guessing",
                ai.provider_for("llama-4") is None and ai.provider_for("") is None)
    # "Never chosen" and "chosen none" are different states: the first should
    # just work on the default, the second is an off switch that a key sitting
    # in the box must not override.
    never = ai.Client(None, "a-real-key", "")
    ok &= check("a key with no model yet gets the default, Claude Opus 5",
                never.ok and never.model == "claude-opus-5" == ai.DEFAULT_MODEL,
                f"({never.model})")
    ok &= check("choosing none stays off, key or no key",
                ai.Client("none", "a-real-key", "").ok is False)
    ok &= check("and no key is still off, whatever the model",
                ai.Client(None, "", "claude-opus-5").ok is False)
    ok &= check("a stored provider that disagrees with the model loses",
                ai.Client("openai", "k", "claude-opus-5").provider == "anthropic")
    ok &= check("the picker list is grouped Claude, then Gemini, then OpenAI",
                [m["provider"] for m in ai.catalogue()]
                == ["anthropic"] * 4 + ["gemini"] * 3 + ["openai"] * 3)
    return ok


def test_request_shape():
    ok = True
    reply = {"content": [{"type": "text", "text": "ok"}]}
    stub, _, _ = run("claude-opus-5", reply)
    ok &= check("anthropic: right endpoint, key header and version",
                stub.url == "https://api.anthropic.com/v1/messages"
                and stub.headers["x-api-key"] == "k"
                and stub.headers["anthropic-version"] == "2023-06-01")
    ok &= check("anthropic: reasoning turned down, and the budget covers it",
                stub.payload["output_config"] == {"effort": "low"}
                and stub.payload["max_tokens"] == 4000)
    ok &= check("anthropic: a refusal retries on a fallback inside the same call",
                stub.payload.get("fallbacks") == "default"
                and "server-side-fallback" in stub.headers.get("anthropic-beta", ""))

    # The knob is a property of the MODEL, not the provider: output_config is
    # right for Opus 5 and a 400 on Haiku 4.5.
    stub, _, _ = run("claude-haiku-4-5", reply)
    ok &= check("anthropic: the model that rejects the effort knob is not sent one",
                "output_config" not in stub.payload and "fallbacks" not in stub.payload,
                f"({sorted(stub.payload)})")

    stub, _, _ = run("claude-opus-9-unreleased", reply)
    ok &= check("an unlisted model is sent the plainest request, which cannot be rejected",
                "output_config" not in stub.payload and "fallbacks" not in stub.payload)

    stub, _, _ = run("gpt-5.6-terra", {"choices": [{"message": {"content": "ok"}}]})
    ok &= check("openai: chat completions, bearer key, reasoning_effort",
                stub.url.endswith("/v1/chat/completions")
                and stub.headers["Authorization"] == "Bearer k"
                and stub.payload["reasoning_effort"] == "low")
    ok &= check("openai: the token cap uses the name reasoning models require",
                stub.payload.get("max_completion_tokens") == 4000
                and "max_tokens" not in stub.payload)
    ok &= check("openai: the system prompt is the first message",
                stub.payload["messages"][0]["role"] == "system")

    stub, _, _ = run("gemini-3.7-flash",
                     {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
    cfg = stub.payload["generationConfig"]
    ok &= check("gemini: the model name is in the path and the key is a header",
                "gemini-3.7-flash:generateContent" in stub.url
                and stub.headers["x-goog-api-key"] == "k")
    ok &= check("gemini: thinkingLevel is set, and thinkingBudget never beside it (a 400)",
                cfg["thinkingConfig"] == {"thinkingLevel": "LOW"}
                and "thinkingBudget" not in json.dumps(cfg), f"({cfg})")
    return ok


def test_reading_the_answer():
    ok = True
    _, text, reason = run("claude-opus-5", {"content": [
        {"type": "thinking", "thinking": "let me weigh this"},
        {"type": "text", "text": "6059 climbs [exact]."}]})
    ok &= check("anthropic: the answer is read, the thinking is not",
                text == "6059 climbs [exact]." and reason is None, f"({text})")

    # Gemini returns its reasoning as parts flagged `thought`. Printing those
    # as the digest would be worse than printing nothing.
    _, text, _ = run("gemini-3.7-flash", {"candidates": [{"content": {"parts": [
        {"text": "weighing the notes", "thought": True},
        {"text": "6059 climbs [exact]."}]}}]})
    ok &= check("gemini: a thought part never reaches the panel",
                text == "6059 climbs [exact].", f"({text})")

    _, text, _ = run("gpt-5.6-luna", {"choices": [{"message": {"content": " spaced "}}]})
    ok &= check("openai: the answer is read and trimmed", text == "spaced")
    return ok


def test_a_failure_says_which_failure():
    ok = True
    # Reasoning ate the whole budget. This is THE failure the low-effort knobs
    # exist to prevent, and "could not be reached" would send a scouting lead
    # hunting for a network fault that is not there.
    for model, body in (
        ("claude-opus-5", {"stop_reason": "max_tokens", "content": []}),
        ("gpt-5.6-luna", {"choices": [{"finish_reason": "length",
                                       "message": {"content": ""}}]}),
        ("gemini-3.7-flash", {"candidates": [{"finishReason": "MAX_TOKENS"}]}),
    ):
        _, text, reason = run(model, body)
        ok &= check(f"{model}: an answer cut short says so", text is None
                    and reason == ai.OUT_OF_ROOM, f"({reason})")

    for model, body in (
        ("claude-opus-5", {"stop_reason": "refusal", "content": []}),
        ("gemini-3.7-flash", {"candidates": [{"finishReason": "SAFETY"}]}),
    ):
        _, text, reason = run(model, body)
        ok &= check(f"{model}: a decline is not reported as a network fault",
                    reason == ai.DECLINED, f"({reason})")

    _, text, reason = run("claude-opus-5", None, status=0)
    ok &= check("a dead network is the one that reads as unreachable",
                text is None and reason == ai.UNREACHABLE)

    c = ai.Client(None, "k", "claude-opus-5")
    real = sources._request
    sources._request = Stub(None, 429)
    try:
        c.ask("s", "u")
        first = c.down_until
        c.ask("s", "u")
    finally:
        sources._request = real
    ok &= check("a rate-limited key is not hammered on the next button press",
                first > 0 and c.down_until == first)

    _, text, reason = run("claude-opus-5", {"content": [{"type": "text", "text": "hi"}]}, key="")
    ok &= check("no key answers without calling anything", text is None)
    return ok


if __name__ == "__main__":
    ok = True
    for fn in (test_routing, test_request_shape, test_reading_the_answer,
               test_a_failure_says_which_failure):
        print("\n" + fn.__name__.replace("test_", "").replace("_", " "))
        ok &= fn()
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)
