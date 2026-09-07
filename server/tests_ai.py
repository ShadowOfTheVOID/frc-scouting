#!/usr/bin/env python3
"""Regression gate for the AI adapter.  Run: python3 server/tests_ai.py

No network and no key: `sources._request` is replaced with a stub that records
what would have been sent and hands back a canned reply.

These cover the failures a model change makes silently rather than loudly.
Every model on the list reasons before it answers, out of the same token
budget as the answer, and each vendor turns that down with a different
parameter that the others reject.  A wrong knob is a 400; a missing one is
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


class Routed:
    """Answers by URL, for the calls that make more than one request."""

    def __init__(self, routes):
        self.routes, self.urls, self.sent = routes, [], []

    def __call__(self, url, headers=None, timeout=None, method=None, data=None, **kw):
        self.urls.append(url)
        self.sent.append(dict(headers or {}))
        for fragment, answer in self.routes.items():
            if fragment in url:
                return answer
        return None, 404


def verify(model, routes, key="k", provider=None):
    real = sources._request
    stub = Routed(routes)
    sources._request = stub
    try:
        return stub, ai.Client(provider, key, model).verify()
    finally:
        sources._request = real


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
    # The slash is the whole rule. `claude-opus-5` is bought from Anthropic
    # with an Anthropic key; `anthropic/claude-opus-5` is the same model
    # through OpenRouter, and sending one to the other's endpoint is a 401.
    ok &= check("an id with a maker in front of it goes to OpenRouter",
                [ai.provider_for(m) for m in ("anthropic/claude-opus-5",
                                              "google/gemini-3.7-flash",
                                              "meta-llama/llama-4-scout")]
                == ["openrouter"] * 3)
    ok &= check("and the bare name of the same model still goes to its maker",
                ai.provider_for("claude-opus-5") == "anthropic")
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
    ok &= check("the picker list is grouped Claude, Gemini, OpenAI, then OpenRouter",
                [m["provider"] for m in ai.catalogue()]
                == ["anthropic"] * 4 + ["gemini"] * 3 + ["openai"] * 3 + ["openrouter"] * 3)
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

    stub, _, _ = run("anthropic/claude-opus-5", {"choices": [{"message": {"content": "ok"}}]})
    ok &= check("openrouter: its own endpoint, not the maker's",
                stub.url == "https://openrouter.ai/api/v1/chat/completions"
                and stub.headers["Authorization"] == "Bearer k", f"({stub.url})")
    ok &= check("openrouter: the model keeps the maker in front of it",
                stub.payload["model"] == "anthropic/claude-opus-5")
    ok &= check("openrouter: one reasoning knob for whichever maker answers",
                stub.payload["reasoning"] == {"effort": "low"}
                and "output_config" not in stub.payload
                and "reasoning_effort" not in stub.payload, f"({sorted(stub.payload)})")
    ok &= check("openrouter: max_tokens, which is the name it documents",
                stub.payload.get("max_tokens") == 4000
                and "max_completion_tokens" not in stub.payload)
    ok &= check("openrouter: the system prompt is the first message",
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

    _, text, reason = run("google/gemini-3.7-flash",
                          {"choices": [{"message": {"content": "6059 climbs [exact]."}}]})
    ok &= check("openrouter: the answer is read out of OpenAI's shape, whoever replied",
                text == "6059 climbs [exact]." and reason is None, f"({text})")
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
        ("anthropic/claude-opus-5", {"choices": [{"finish_reason": "length",
                                                  "message": {"content": ""}}]}),
    ):
        _, text, reason = run(model, body)
        ok &= check(f"{model}: an answer cut short says so", text is None
                    and reason == ai.OUT_OF_ROOM, f"({reason})")

    for model, body in (
        ("claude-opus-5", {"stop_reason": "refusal", "content": []}),
        ("gemini-3.7-flash", {"candidates": [{"finishReason": "SAFETY"}]}),
        ("openai/gpt-5.6-terra", {"choices": [{"finish_reason": "content_filter",
                                               "message": {"content": ""}}]}),
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


def test_test_keys_button():
    """What TEST KEYS asks, and the one provider where the obvious call lies.

    OpenRouter serves its catalogue to anybody, with or without a key. Testing
    a key against it would answer "ok" for a key revoked last week, which is
    the exact failure the button exists to catch.
    """
    ok = True
    KEY = "https://openrouter.ai/api/v1/key"
    CATALOGUE = "https://openrouter.ai/api/v1/models"
    listing = {"data": [{"id": "anthropic/claude-opus-5"}, {"id": "openai/gpt-5.6-terra"}]}

    stub, v = verify("anthropic/claude-opus-5",
                     {KEY: ({"data": {"label": "hub"}}, 200), CATALOGUE: (listing, 200)})
    ok &= check("openrouter: the key is proved against the route that needs one",
                stub.urls[0] == KEY and v["state"] == "ok", f"({stub.urls} {v})")
    ok &= check("openrouter: and the bearer header carries it, on that call",
                stub.sent[0].get("Authorization") == "Bearer k"
                # The catalogue is fetched without one, which is the point of it
                # being a second call rather than the first.
                and not stub.sent[1], f"({stub.sent})")

    stub, v = verify("anthropic/claude-opus-5", {KEY: (None, 401)})
    ok &= check("openrouter: a revoked key is refused, not blessed by the catalogue",
                v["state"] == "bad" and CATALOGUE not in stub.urls, f"({v})")

    stub, v = verify("anthropic/claude-opus-6-typo",
                     {KEY: ({"data": {"label": "hub"}}, 200), CATALOGUE: (listing, 200)})
    ok &= check("openrouter: a good key with a mistyped id says which of the two is wrong",
                v["state"] == "warn" and "check the model name" in v["detail"], f"({v})")

    # The catalogue answers `vendor/model`; cutting at the slash the way Gemini
    # needs would compare "claude-opus-5" against "anthropic/claude-opus-5" and
    # warn about a model that is right there on the list.
    stub, v = verify("google/gemini-3.7-flash",
                     {KEY: ({"data": {"label": "hub"}}, 200),
                      CATALOGUE: ({"data": [{"id": "google/gemini-3.7-flash"}]}, 200)})
    ok &= check("openrouter: an id is matched whole, slash and all", v["state"] == "ok",
                f"({v})")

    stub, v = verify("claude-opus-5", {"api.anthropic.com/v1/models":
                                       ({"data": [{"id": "claude-opus-5"}]}, 200)})
    ok &= check("anthropic: still its own model list, and one call",
                v["state"] == "ok" and len(stub.urls) == 1, f"({stub.urls} {v})")

    stub, v = verify("gemini-3.7-flash", {"generativelanguage.googleapis.com":
                                          ({"models": [{"name": "models/gemini-3.7-flash"}]}, 200)})
    ok &= check("gemini: the `models/` prefix is still cut off before comparing",
                v["state"] == "ok", f"({v})")

    # This one reads as "no model chosen" if the off switch and an unroutable
    # id share a branch - and the fix for the two is not remotely the same.
    stub, v = verify("llama-4", {})
    ok &= check("a typed id that routes nowhere says so, and calls nothing",
                v["state"] == "bad" and "OpenRouter" in v["detail"] and not stub.urls,
                f"({v})")
    stub, v = verify("", {}, provider=ai.OFF)
    ok &= check("and the off switch is still the quiet unset, not an error",
                v["state"] == "unset" and not stub.urls, f"({v})")
    return ok


if __name__ == "__main__":
    ok = True
    for fn in (test_routing, test_request_shape, test_reading_the_answer,
               test_a_failure_says_which_failure, test_test_keys_button):
        print("\n" + fn.__name__.replace("test_", "").replace("_", " "))
        ok &= fn()
    print("\n" + ("ALL PASS" if ok else "FAILURES ABOVE"))
    sys.exit(0 if ok else 1)
