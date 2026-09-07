"""What was on the clipboard, turned into the key the vendor actually issued.

Every credential on the Setup page arrives by copy and paste, usually on the
Saturday morning, from somebody who is also doing six other things.  What lands
in the box is very often not the key: it is the key with the header name still
attached, or wrapped in the quotes from a code sample, or split over two lines
by an email client, or it is the Lovat key in the TBA box, or it is the address
of the page the key is on rather than the key.

None of that used to be noticed.  The value went into the settings row verbatim,
the Setup page said `TBA SET`, and the only symptom for the rest of the event was
a service that quietly returned nothing - which this app is built to survive, so
it survived it, all weekend, showing blanks that look exactly like "nobody
scouted that robot".

So: everything is scrubbed on the way in, and a paste that cannot possibly be
the key it is filed under is refused with a sentence saying which box it belongs
in.  The split matters and is deliberate -

  * **error** is only for a paste that is definitely wrong: another vendor's
    key, a web address, the example text, an empty string of punctuation.  It
    refuses the save.
  * **warning** is for a shape that looks off - a TBA key that is not 64
    characters, a Lovat key that does not start `lvt-`.  It saves anyway.  A
    vendor is allowed to change its key format without this file bricking a
    hub at a competition, and `hub.verify_keys()` settles it for real by
    asking the vendor.

`scrub` never rejects, so a key that is merely untidy is fixed silently and the
note is shown, not blocked.
"""
import base64
import re

#: Copying out of a PDF, a chat client or a styled web page brings these along.
#: They are invisible in the box, so a key that carries one looks identical to
#: the key that works.
INVISIBLE = ("\u200b\u200c\u200d"      # zero-width space, non-joiner, joiner
             "\u2060\ufeff"             # word joiner, byte-order mark
             "\u00ad")                   # soft hyphen

#: Straight and curly both: a key pasted out of a code sample or a document that
#: "helpfully" typeset the quotes.
QUOTES = "\"'`‘’“”«»"

#: A header name and its separator, still attached to the value.  Every one of
#: these is what the vendor's own documentation shows, which is exactly why it
#: gets copied whole.
HEADER = re.compile(
    r"^(?:x-tba-auth-key|nexus-api-key|nexus-token|x-nexus-token|authorization"
    r"|x-api-key|x-goog-api-key|x-mirror-key|api[-_ ]?key|auth[-_ ]?key|key|token)"
    r"\s*[:=]\s*", re.I)

#: `Bearer <key>` / `Basic <base64>` - the scheme is part of the header, not of
#: the key.  Kept rather than dropped, because for FRC Events the Basic blob is
#: the username and the token together and both are wanted.
SCHEME = re.compile(r"^(bearer|basic|token)\s+", re.I)

#: Example text from the docs, or from the placeholder in the box above.  A key
#: is never any of these.
PLACEHOLDER = re.compile(
    r"^(?:<.*>|\{.*\}|\[.*\]|x{6,}|\.{3,}|your[-_ ]?|paste|api[-_ ]?key$|key[-_ ]?here"
    r"|enter|none|null|n/a|todo)", re.I)

#: Keys these five vendors issue, in a form that cannot be anything else.  Used
#: only to catch a key filed under the wrong box - never to bless one, because
#: "does not match" has to keep meaning "we do not know".
SIGNATURES = (
    ("lovatKey", re.compile(r"^lvt-"), "a Lovat key"),
    ("aiKey", re.compile(r"^sk-ant-"), "an Anthropic (Claude) key"),
    ("aiKey", re.compile(r"^AIza[\w-]{10,}$"), "a Google (Gemini) key"),
    ("aiKey", re.compile(r"^sk-proj-"), "an OpenAI key"),
)

#: Which company an AI key belongs to, for the one mismatch worth refusing: a
#: Claude key under a Gemini model reads as "the model could not be reached"
#: forever, and nothing on any screen says why.
AI_VENDOR = (
    (re.compile(r"^sk-ant-"), "anthropic", "Claude (Anthropic)"),
    (re.compile(r"^AIza[\w-]{10,}$"), "gemini", "Gemini (Google)"),
    (re.compile(r"^sk-proj-"), "openai", "OpenAI"),
)

#: Nothing any of these vendors issues comes close.  A paste past this is a
#: file, a page of HTML, or the whole JSON response.
MAX_LEN = 400


class Field:
    """One box on the Setup page.

    `shape` is what the vendor is known to issue and is advisory only - it can
    only ever raise a warning.  `secret` is false for the two boxes that hold a
    name rather than a key, which changes what counts as obviously wrong (an
    address is a fine username; it is never a key).
    """

    def __init__(self, label, where="", shape=None, shape_says="", secret=True):
        self.label, self.where = label, where
        self.shape, self.shape_says, self.secret = shape, shape_says, secret


FIELDS = {
    "tbaKey": Field(
        "The Blue Alliance", "the THE BLUE ALLIANCE box",
        re.compile(r"^[A-Za-z0-9]{64}$"),
        "The Blue Alliance issues a 64-character key of letters and digits"),
    "nexusKey": Field("Nexus", "the NEXUS box"),
    "nexusToken": Field("Nexus webhook token", "the NEXUS WEBHOOK TOKEN box"),
    "frcEventsUser": Field("FRC Events username", "the FRC EVENTS USERNAME box", secret=False),
    "frcEventsToken": Field("FRC Events token", "the FRC EVENTS TOKEN box"),
    "lovatKey": Field(
        "Lovat", "the LOVAT API KEY box",
        re.compile(r"^lvt-\S+$"), "Lovat keys start with `lvt-`"),
    "aiKey": Field("AI", "the AI KEY box"),
    "mirrorKey": Field("mirror push key", "the MIRROR PUSH KEY box"),
    #: Not a key, and not checked like one - but a passcode with a space on the
    #: end locks a lead out of their own picklist and looks identical to one
    #: without.  Trimmed, never rejected.
    "strategyPin": Field("strategy passcode", "the STRATEGY PASSCODE box"),
}


def scrub(raw):
    """`(value, notes)` - the key as issued, and what had to be taken off it.

    Never rejects and never guesses: everything removed here is something that
    cannot be part of a key issued by any of these services.

    Peeled in a loop rather than in one pass, because a real paste nests these
    - `"X-TBA-Auth-Key: abc"` off a code sample is a quote around a header name
    around the key, and one pass in a fixed order gets whichever layer it
    checked for first and leaves the rest welded on.
    """
    notes = []
    s = raw
    if any(ch in s for ch in INVISIBLE):
        for ch in INVISIBLE:
            s = s.replace(ch, "")
        notes.append("removed an invisible character that came with the paste")
    s = s.replace(" ", " ").strip()

    for _ in range(4):
        before = s
        header = HEADER.match(s)
        if header:
            s = s[header.end():].strip()
            notes.append("dropped the `%s` header name" % header.group(0).rstrip(" :=").strip())
        scheme = SCHEME.match(s)
        if scheme:
            s = s[scheme.end():].strip()
            notes.append("dropped the `%s` scheme" % scheme.group(1))
        stripped = s.strip(QUOTES).strip()
        if stripped != s:
            s = stripped
            notes.append("removed the quotes around it")
        # What a copied line of code leaves on the end.
        if s and s[-1] in ",;":
            notes.append("removed the trailing `%s`" % s[-1])
            s = s[:-1].strip()
        if s == before:
            break

    # A key has no whitespace anywhere in it, so any that survived the peeling
    # came from a line wrap - which is invisible once it is in a one-line box.
    if re.search(r"\s", s):
        s = re.sub(r"\s+", "", s)
        notes.append("joined it back up - it was split across lines")

    # The same layer twice over is one thing to say, not two.
    seen = []
    for n in notes:
        if n not in seen:
            seen.append(n)
    return s, seen


def _basic(value):
    """`user:token` out of a `Basic` blob, or None.

    FRC Events' own documentation tells you to base64 your username and token
    together, so that blob is what a lot of people have on the clipboard when
    they reach the token box.  Both halves are wanted and both are here.
    """
    try:
        text = base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("utf-8")
    except Exception:
        return None
    if text.count(":") != 1:
        return None
    user, token = text.split(":")
    return (user.strip(), token.strip()) if user.strip() and token.strip() else None


def check(field, raw, ai_provider=None):
    """One box, checked. Returns a dict, always with the same shape.

        value    what to save - "" means the lead cleared the box on purpose
        error    refuse the save and say this, or None
        warning  save it, but say this, or None
        notes    what `scrub` quietly fixed
        also     other fields this paste turned out to contain

    `ai_provider` is the company that owns the model being saved alongside, and
    is what makes an AI key checkable at all.
    """
    out = {"field": field, "value": "", "error": None, "warning": None,
           "notes": [], "also": {}}
    spec = FIELDS.get(field)
    if spec is None:                      # not ours to police
        out["value"] = raw
        return out
    if not isinstance(raw, str):
        out["error"] = "%s has to be text." % spec.label
        return out
    if not raw.strip():
        return out                        # deliberately cleared

    if field == "strategyPin":
        # Trimmed and nothing else: a passcode is allowed to be any short thing
        # a team can shout across a pit, including one that looks like junk.
        out["value"] = raw.strip()
        if out["value"] != raw:
            out["notes"].append("removed the spaces around it")
        return out

    value, notes = scrub(raw)
    out["notes"] = notes

    if not value:
        out["error"] = ("There was nothing in that but spaces and punctuation. "
                        "Copy the key itself, not the line around it.")
        return out
    if len(value) > MAX_LEN:
        out["error"] = ("That is %d characters - far longer than any key. "
                        "It looks like a whole page or file got pasted." % len(value))
        return out
    if PLACEHOLDER.match(value):
        out["error"] = ("That is the example text, not a key. The real one comes from "
                        "the vendor's site.")
        return out
    if "://" in value or value.lower().startswith("www."):
        out["error"] = ("That is a web address, not a key. Open it, sign in, and copy "
                        "the key off that page.")
        return out
    if spec.secret and "@" in value and "." in value.split("@")[-1]:
        out["error"] = ("That looks like an email address. %s wants the key it issued you, "
                        "not your login." % spec.label)
        return out

    # Filed under the wrong box. This is the one worth being firm about: it is
    # both the easiest mistake to make with eight boxes on one page and the only
    # one that breaks two services at once.
    for owner, sig, describes in SIGNATURES:
        if sig.match(value) and owner != field:
            out["error"] = ("That is %s. It goes in %s."
                            % (describes, FIELDS[owner].where))
            return out

    if field == "frcEventsToken":
        pair = _basic(value)
        if pair:
            out["also"]["frcEventsUser"] = pair[0]
            value = pair[1]
            out["notes"].append("that was the Basic credential - the username came out of "
                                "it too, and is filled in above")
        elif value.count(":") == 1 and all(value.split(":")):
            user, value = value.split(":")
            out["also"]["frcEventsUser"] = user
            out["notes"].append("that was `username:token` - the username came out of it "
                                "too, and is filled in above")

    if field == "aiKey" and ai_provider in ("anthropic", "gemini", "openai"):
        for sig, vendor, vendor_name in AI_VENDOR:
            if sig.match(value) and vendor != ai_provider:
                want = dict(anthropic="Claude (Anthropic)", gemini="Gemini (Google)",
                            openai="OpenAI")[ai_provider]
                out["error"] = ("That key belongs to %s, but the model picked above is %s. "
                                "Pick a model from %s instead, or paste that company's key."
                                % (vendor_name, want, want))
                return out

    out["value"] = value
    if spec.shape and not spec.shape.match(value):
        out["warning"] = ("%s, and this is %d character%s. Saved anyway - press TEST KEYS "
                          "to find out whether it works."
                          % (spec.shape_says, len(value), "" if len(value) == 1 else "s"))
    elif spec.secret and len(value) < 8:
        # A username is allowed to be four characters. A key is not.
        out["warning"] = ("That is only %d characters, which is shorter than anything these "
                          "services issue. Check the whole key got copied." % len(value))
    return out


def check_all(values, ai_provider=None):
    """Every submitted box at once.

    Returns `(cleaned, errors, warnings, notes)`.  A field a paste filled in for
    another one (`also`) is folded into `cleaned` unless that box was typed in
    directly, which wins.
    """
    cleaned, errors, warnings, notes, also = {}, {}, {}, {}, {}
    for field, raw in values.items():
        r = check(field, raw, ai_provider)
        if r["error"]:
            errors[field] = r["error"]
            continue
        cleaned[field] = r["value"]
        if r["warning"]:
            warnings[field] = r["warning"]
        if r["notes"]:
            notes[field] = r["notes"]
        also.update(r["also"])
    for field, value in also.items():
        if not cleaned.get(field):
            cleaned[field] = value
    return cleaned, errors, warnings, notes
