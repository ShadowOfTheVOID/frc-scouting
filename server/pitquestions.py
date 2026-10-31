"""The team's own pit scouting questions: what one may be, and how to clean one.

Shared by the hub and the off-site mirror. The questions are edited on the
mirror - they are set up from home before an event, not on the laptop that is
busy running matches - and the hub collects them from it once a minute, so
both ends have to agree exactly on what a question is. Plain Python with no
imports from the rest of the hub, because the mirror loads this file from the
same checkout and nothing else.
"""
import re
import secrets

#: The kinds of question a team can add to pit scouting. A choice is a row of
#: chips like the built-in questions; yes/no is a choice with its two answers
#: fixed, so a spreadsheet never has to reconcile "Yes", "y" and "true".
TYPES = ("choice", "yesno", "number", "text")
#: Limits, so a pit form stays something a person can fill in walking past.
MAX_QUESTIONS, MAX_LABEL, MAX_HINT = 30, 120, 160
MAX_OPTIONS, MAX_OPTION = 8, 32
#: What an id looks like. Ids are minted by `clean` and only ever copied after.
ID_RE = re.compile(r"^q_[a-z0-9_]{1,40}$")


def _words(v, cap):
    return " ".join(str(v or "").split())[:cap]


def _shape(q):
    """One question's label, type, options and hint, tidied. (dict, error)."""
    label = _words(q.get("label"), MAX_LABEL)
    if not label:
        return None, None
    kind = q.get("type") if q.get("type") in TYPES else "text"
    out = {"label": label, "type": kind}
    if kind == "choice":
        opts = []
        for o in (q.get("options") if isinstance(q.get("options"), list) else []):
            o = _words(o, MAX_OPTION)
            if o and o.lower() not in (x.lower() for x in opts):
                opts.append(o)
        if len(opts) < 2:
            return None, '"%s" needs at least two answers to choose from' % label
        if len(opts) > MAX_OPTIONS:
            return None, '"%s" has more than %d answers' % (label, MAX_OPTIONS)
        out["options"] = opts
    hint = _words(q.get("hint"), MAX_HINT)
    if hint:
        out["hint"] = hint
    return out, None


def clean(raw, existing):
    """Clean a list of questions somebody edited. Returns (questions, error).

    Each question keeps its id for as long as it exists - the answers already
    given are filed under it, so renaming a question must not orphan them. A
    new question gets a fresh id with a random tail rather than one derived
    from its position or label alone: deleting a question and adding a
    different one with the same words must never pick up the old one's answers.
    Blank questions are dropped; anything else wrong is refused whole, so a
    half-applied save is never what the tablets get.
    """
    if not isinstance(raw, list):
        return None, "questions must be a list"
    if len(raw) > MAX_QUESTIONS:
        return None, "at most %d custom questions" % MAX_QUESTIONS
    known = {q.get("id") for q in (existing or []) if isinstance(q, dict)}
    used, out = set(), []
    for q in raw:
        if not isinstance(q, dict):
            continue
        shaped, err = _shape(q)
        if err:
            return None, err
        if not shaped:
            continue
        qid = q.get("id")
        if not (isinstance(qid, str) and qid in known and qid not in used):
            slug = re.sub(r"[^a-z0-9]+", "_", shaped["label"].lower()).strip("_")[:24] or "question"
            qid = "q_%s_%s" % (slug, secrets.token_hex(2))
            while qid in used or qid in known:
                qid = "q_%s_%s" % (slug, secrets.token_hex(2))
        used.add(qid)
        shaped["id"] = qid
        out.append(shaped)
    return out, None


def trusted(raw):
    """Questions as they arrive from the mirror, already cleaned once there.

    Checked again rather than believed: the hub writes these to every pit
    tablet. Ids are kept as given - they were minted by `clean` - and any
    question that does not look like one is dropped rather than repaired.
    """
    out, seen = [], set()
    for q in (raw if isinstance(raw, list) else [])[:MAX_QUESTIONS]:
        if not isinstance(q, dict):
            continue
        qid = q.get("id")
        if not (isinstance(qid, str) and ID_RE.fullmatch(qid)) or qid in seen:
            continue
        shaped, err = _shape(q)
        if err or not shaped:
            continue
        shaped["id"] = qid
        seen.add(qid)
        out.append(shaped)
    return out


#: The questions every pit form starts with, as (field, label). A team can
#: remove any of them from its own forms; the field names are what the answers
#: are filed under, so they never change.
BUILTIN = (("drivetrain", "Drivetrain"), ("shooter", "How do they shoot?"),
           ("maxClimb", "Highest tower level they claim"),
           ("stockpile", "Can they hold fuel through an inactive shift?"),
           ("groundPickup", "Ground pickup?"), ("autos", "Auto routines"),
           ("weight", "Weight (lb)"), ("notes", "Notes"), ("photos", "Photos"))
BUILTIN_FIELDS = tuple(f for f, _ in BUILTIN)


def clean_hidden(raw):
    """The built-in questions a team removed, as a list of known field names.

    Anything that is not one of BUILTIN_FIELDS is dropped; order follows the
    form, so the same choice always saves as the same list.
    """
    picked = set(x for x in (raw if isinstance(raw, list) else []) if isinstance(x, str))
    return [f for f in BUILTIN_FIELDS if f in picked]
