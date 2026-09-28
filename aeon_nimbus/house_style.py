"""House style, enforced rather than intended.

Every word a user reads on this platform has to sound like an analyst wrote it.
The rules below come from Wikipedia's "Signs of AI writing", which is a useful
list precisely because it is concrete: specific words, specific constructions,
specific punctuation habits.

This is a checker rather than a style guide because a style guide in a document
is a wish. A test that fails on the next piece of copy is a rule.

    from aeon_nimbus import house_style
    house_style.check("Our robust platform delves into the landscape...")
    -> [Violation(word='robust', ...), Violation(word='delve', ...)]

Not everything on the list can be caught mechanically, and the ones that cannot
are left out rather than approximated: a rule that fires on good writing gets
switched off, and then none of them work.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Words that mark a sentence as machine-written. Each is a whole-word match, so
# "keystone" does not trip on "key" and "understated" does not trip on "state".
# --------------------------------------------------------------------------
TELLS = {
    # abstract nouns that say nothing
    "tapestry": "name the thing instead",
    "landscape": "say what it actually is (a market, a sector, a set of rules)",
    "realm": "say where",
    "myriad": "say how many, or 'many'",
    "plethora": "say how many, or 'many'",
    "testament": "say what it shows",
    "interplay": "say how the two things affect each other",
    "synergy": "say what combining them does",
    "paradigm": "say what changed",
    # verbs doing promotional work
    "delve": "'look at', or just do it",
    "delves": "'looks at'",
    "underscore": "'shows' or 'means'",
    "underscores": "'shows' or 'means'",
    "underscoring": "'which shows'",
    "showcase": "'show'",
    "showcases": "'shows'",
    "boasts": "'has'",
    "garner": "'get' or 'win'",
    "garnered": "'got' or 'won'",
    "foster": "'encourage' or 'cause'",
    "fostering": "'encouraging'",
    "leverage": "'use'",
    "leveraging": "'using'",
    "harness": "'use'",
    "unlock": "say what becomes possible",
    "elevate": "'raise' or 'improve'",
    "empower": "say what someone can now do",
    "embark": "'start'",
    "navigate": "'find your way', unless it is the literal UI action",
    "streamline": "'simplify' or 'speed up'",
    "spearhead": "'lead'",
    # adjectives that are opinions wearing a fact's clothes
    "robust": "say what it withstands",
    "crucial": "say why it matters",
    "pivotal": "say why it matters",
    "vital": "say why it matters",
    "meticulous": "say what was checked",
    "meticulously": "say what was checked",
    "intricate": "'complicated', or explain how",
    "intricacies": "'details'",
    "vibrant": "cut it",
    "profound": "cut it",
    "groundbreaking": "say what is new",
    "transformative": "say what it changes",
    "cutting-edge": "say what it does",
    "state-of-the-art": "say what it does",
    "world-class": "cut it",
    "best-in-class": "cut it",
    "seamless": "say what does not break",
    "seamlessly": "cut it",
    "effortlessly": "cut it",
    "holistic": "say what it covers",
    "bespoke": "'custom', or say what was tailored",
    "renowned": "say who says so",
    "esteemed": "cut it",
    "unparalleled": "cut it",
    "unwavering": "cut it",
    "enduring": "cut it",
    "invaluable": "say what it is worth",
    "nestled": "cut it",
}

# Whole phrases, matched case-insensitively.
PHRASES = {
    r"\bnot only\b[^.]{0,80}?\bbut also\b": "say both things plainly",
    r"\bit is not just\b|\bit's not just\b|\bisn't just\b": "say what it is",
    r"\bstands as a\b|\bserves as a\b|\bfunctions as a\b": "'is'",
    r"\brich (?:cultural |)heritage\b": "say what is old and why it matters",
    r"\bin the heart of\b": "'in'",
    r"\bdiverse array\b": "'range', or say how many",
    r"\bcommitment to\b": "say what is actually done",
    r"\bplays? a (?:key|vital|crucial|pivotal|significant) role\b": "say what it does",
    r"\bindustry reports?\b|\bexperts? (?:argue|say|suggest)\b": "name the source",
    r"\bobservers have\b|\bsome critics\b": "name who",
    r"\bvaluable insights?\b": "say what was learned",
    r"\balign(?:s|ed|ing)? with\b": "'matches' or 'fits'",
    r"\bresonate(?:s|d)? with\b": "say who agreed and why",
    r"\bindelible mark\b|\blasting legacy\b": "say what remains",
    r"\bevolving landscape\b": "say what is changing",
    r"\bin today's\b": "cut it",
    r"\bwhen it comes to\b": "cut it",
    r"\bit(?:'s| is) (?:important|worth) (?:to note|noting)\b": "just say the thing",
    r"\bneedless to say\b": "cut it",
    r"\bat the end of the day\b": "cut it",
}

# A trailing "-ing" clause bolted on to explain why the sentence mattered.
ING_TAIL = re.compile(
    r",\s+(?:highlighting|underscoring|emphasising|emphasizing|ensuring|reflecting|"
    r"symbolising|symbolizing|showcasing|demonstrating|fostering|cultivating|"
    r"contributing to|paving the way|allowing for|enabling)\b", re.I)

CURLY = re.compile(r"[‘’“”]")

# Technical vocabulary that happens to collide with a banned word. A checker
# that cries wolf on real terms gets turned off, and then it protects nothing.
EXEMPT = {
    # the UI action and the tool of that name, not the travel metaphor
    "navigate": r"[.\"'`]navigate|navigate\s*[`\"'(:]|def navigate|navigate tool",
    # gearing. "Leverage: net debt / EBITDA" is what the metric is called, and a
    # checker that renames it is worse than no checker.
    "leverage": r"Leverage|leverage\s*[:/]|net leverage|leverage ratio|"
                r"financial leverage|operating leverage",
    "key": r"\bkey(?:word|s)?\b|API key|primary key|foreign key",
}

# An em-dash separating a title from its subject ("Safaricom PLC — assumptions")
# is typography. The habit the rule is about is the mid-sentence dash, so only
# count the ones that follow a lowercase word.
SENTENCE_DASH = re.compile(r"[a-z]\s*—")


@dataclass
class Violation:
    term: str
    advice: str
    context: str

    def __str__(self):
        return f"{self.term!r} ({self.advice}) in: …{self.context}…"


def _context(text: str, at: int, width: int = 46) -> str:
    lo, hi = max(0, at - width // 2), min(len(text), at + width)
    return " ".join(text[lo:hi].split())


def check(text: str, *, allow_em_dash: int = 1) -> list[Violation]:
    """Return every house-style violation in one piece of user-facing copy."""
    if not text or not text.strip():
        return []
    out: list[Violation] = []
    low = text.lower()

    for word, advice in TELLS.items():
        for m in re.finditer(rf"\b{re.escape(word)}\b", low):
            pat = EXEMPT.get(word)
            if pat and re.search(pat, text[max(0, m.start() - 30):m.end() + 30], re.I):
                continue
            out.append(Violation(word, advice, _context(text, m.start())))

    for pattern, advice in PHRASES.items():
        for m in re.finditer(pattern, low):
            out.append(Violation(m.group(0), advice, _context(text, m.start())))

    for m in ING_TAIL.finditer(text):
        out.append(Violation(m.group(0).strip(), "put it in its own sentence, or cut it",
                             _context(text, m.start())))

    for m in CURLY.finditer(text):
        out.append(Violation(m.group(0), "use a straight quote", _context(text, m.start())))

    # An em-dash is fine once. Several in a paragraph is a machine breathing.
    mids = list(SENTENCE_DASH.finditer(text))
    if len(mids) > allow_em_dash:
        out.append(Violation("—", f"{len(mids)} mid-sentence em-dashes; "
                                  f"use a full stop or a comma",
                             _context(text, mids[0].start())))
    return out


def clean(text: str) -> bool:
    return not check(text)
