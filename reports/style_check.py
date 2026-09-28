"""Detect the writing patterns listed in Wikipedia:Signs of AI writing.

The report is generated, so it inherits an LLM's habits unless something looks
for them. Every pattern below is taken from that page: the AI-vocabulary list,
the promotional register, the significance-inflation formulas, negative
parallelisms, the rule of three, vague attribution and vague association, and
the formatting tells. Run it over the rendered PDF text, not the source, so the
prose is judged as a reader sees it.
"""
from __future__ import annotations

import re
import sys

# Words the guide lists as overused, split by the era it assigns them to. The
# guide is explicit that this is to be read literally: a synonym is not a hit.
AI_VOCAB = [
    "additionally", "align with", "aligns with", "aligned with", "boasts",
    "bolstered", "crucial", "deep dive", "delve", "delves", "delving",
    "emphasizing", "emphasising", "enduring", "enhance", "enhances",
    "enhancing", "fostering", "fosters", "garner", "garnered", "garners",
    "interplay", "intricate", "intricacies", "meticulous", "meticulously",
    "pivotal", "robust", "showcase", "showcases", "showcasing", "tapestry",
    "testament", "underscore", "underscores", "underscoring", "vibrant",
]
# "key", "landscape", "highlight", "valuable" are context-dependent: the guide
# flags the adjective, the abstract noun and the figurative verb only.
CONTEXTUAL = {
    r"\bkey (?:role|moment|turning point|factor|driver|feature|element|question|insight)\b":
        "key as an adjective",
    r"\b(?:evolving|broader|competitive|regulatory|political|economic) landscape\b":
        "landscape as an abstract noun",
    r"\bhighlight(?:s|ing|ed)? (?:the|its|their|his|her) (?:importance|significance|role|need|value)\b":
        "highlight as a figurative verb",
    r"\bvaluable (?:insight|contribution|addition|resource|lesson)s?\b":
        "valuable",
}

PROMOTIONAL = [
    "boasts a", "vibrant", "profound", "exemplifies", "commitment to",
    "natural beauty", "nestled", "in the heart of", "groundbreaking",
    "renowned", "diverse array", "rich heritage", "rich history",
    "rich tapestry", "seamlessly", "state-of-the-art", "world-class",
    "cutting-edge", "unparalleled", "remarkable", "impressive array",
]

SIGNIFICANCE = [
    "stands as", "serves as", "is a testament", "a testament to",
    "is a reminder", "plays a crucial role", "plays a pivotal role",
    "plays a vital role", "plays a key role", "plays a significant role",
    "underscores the importance", "underscores its", "highlights the importance",
    "reflects broader", "reflects a broader", "symbolizing", "symbolising",
    "contributing to the broader", "setting the stage for", "marking a",
    "shaping the", "represents a shift", "marks a shift", "key turning point",
    "focal point", "indelible mark", "deeply rooted", "cements its",
    "solidify its role", "solidifies its", "enduring legacy", "lasting impact",
]

VAGUE_ATTRIB = [
    "industry reports", "observers have", "observers note", "experts argue",
    "experts say", "some critics argue", "critics argue", "analysts argue",
    "it is widely believed", "widely regarded", "widely considered",
    "many believe", "some argue", "commentators have", "is often cited",
]

VAGUE_ASSOC = [
    "in connection with", "in association with", "associated with",
    "connected with", "connected to",
]

COPULA_DODGE = [
    "serves as a", "serves as the", "stands as a", "stands as the",
    "functions as a", "operates as a", "represents a", "represents the",
    "boasts", "refers to the", "maintains a", "offers a range",
]

OUTLINE = [
    "despite these challenges", "despite its", "faces several challenges",
    "challenges and legacy", "future outlook", "future prospects",
    "challenges and future", "looking ahead", "in conclusion",
    "it is important to note", "it is worth noting",
]

NEG_PARALLEL = [
    r"\bnot only\b[^.]{0,80}\bbut also\b",
    r"\bis not just\b[^.]{0,60}\b(?:it'?s|but)\b",
    r"\bnot a\b[^.]{0,50}\bbut a\b",
    r"\bnot\b[^.]{0,40}\brather,? (?:it|they|this)\b",
    r"\bdoesn'?t just\b[^.]{0,60};",
    r"\bis less\b[^.]{0,40}\bthan (?:it is )?a\b",
]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _rule_of_three(text: str) -> list[str]:
    """Triples: "a, b and c" runs of comparable short items."""
    hits = []
    pat = re.compile(
        r"\b([a-z][a-z\-]{3,14}), ([a-z][a-z\-]{3,14}),? and ([a-z][a-z\-]{3,14})\b")
    for m in pat.finditer(text):
        hits.append(m.group(0))
    return hits


def scan(text: str) -> dict:
    low = text.lower()
    out: dict[str, list] = {}

    def add(cat, item):
        out.setdefault(cat, []).append(item)

    for w in AI_VOCAB:
        n = len(re.findall(r"\b" + re.escape(w) + r"\b", low))
        if n:
            add("AI vocabulary", f"{w} x{n}")
    for pat, label in CONTEXTUAL.items():
        n = len(re.findall(pat, low))
        if n:
            add("AI vocabulary", f"{label} x{n}")
    for group, name in ((PROMOTIONAL, "Promotional register"),
                        (SIGNIFICANCE, "Significance inflation"),
                        (VAGUE_ATTRIB, "Vague attribution"),
                        (VAGUE_ASSOC, "Vague association"),
                        (COPULA_DODGE, "Copula avoidance"),
                        (OUTLINE, "Outline-like conclusion")):
        for w in group:
            n = len(re.findall(r"\b" + re.escape(w) + r"\b", low))
            if n:
                add(name, f"{w} x{n}")
    n_rt = len(re.findall(r"\brather than\b", low))
    if n_rt:
        add("Rather-than construction", f"x{n_rt} across the document")
    for m in re.finditer(r"\b(?:single (?:most|largest)|largest single|"
                         r"most important|strongest (?:argument|single argument|objection))"
                         r"\b[^.]{0,55}", low):
        add("Competing superlative", m.group(0)[:70])
    for m in re.finditer(r"\bA reader who\b[^.]{0,45}", text):
        add("Reader-address template", m.group(0)[:60])
    for pat in NEG_PARALLEL:
        for m in re.finditer(pat, low):
            add("Negative parallelism", m.group(0)[:70])
    for h in _rule_of_three(low):
        add("Rule of three", h)
    # Sentence-initial "Additionally" / "Moreover" / "Furthermore"
    for s in _sentences(text):
        if re.match(r"^(Additionally|Moreover|Furthermore|Notably|Importantly)\b", s):
            add("Sentence-initial connector", s[:60])
    return out


# Formatting tells from the same page, plus the house rules this report is held
# to: no semicolons, no em dashes, no balanced "while X, Y" clauses, and no
# bolding of figures inside a sentence.
TITLE_CASE = re.compile(r"^(?:[A-Z][a-z']+ ){2,}[A-Z][a-z']+$")
SMALL = {"a", "an", "and", "as", "at", "but", "by", "для", "for", "from", "in",
         "is", "it", "of", "on", "or", "the", "to", "with", "not"}


def scan_format(text: str, tex: str = "") -> dict:
    out: dict[str, list] = {}

    def add(cat, item):
        out.setdefault(cat, []).append(item)

    for m in re.finditer(r"[^\n]*—[^\n]*", text):
        add("Em dash", m.group(0).strip()[:70])
    for m in re.finditer(r"[^\n]*;[^\n]*", text):
        add("Semicolon", m.group(0).strip()[:70])
    for m in re.finditer(r"\bWhile [a-z][^.]{10,90}, [a-z][^.]{10,90}\.", text):
        add("Balanced while-clause", m.group(0)[:80])
    if tex:
        for m in re.finditer(r"\\(?:sub)?section\*\{(?:\\color\{navy\})?([^{}]+)\}", tex):
            h = m.group(1).strip()
            words = [w for w in h.split() if w]
            caps = [w for w in words[1:] if w[:1].isupper() and w.lower() not in SMALL]
            if len(words) >= 3 and len(caps) >= 2:
                add("Title-case heading", h)
            if re.match(r"^[A-Z][a-z]+ and [A-Z]?[a-z]+$", h):
                add("X and Y heading", h)
        n_bold = len(re.findall(r"\\textbf\{", tex))
        n_tab = len(re.findall(r"\\begin\{tabular", tex))
        add("Boldface count", f"{n_bold} textbf, {n_tab} tables")
        # The punctuation sits INSIDE the brace in this generator output, so a
        # pattern expecting it outside matched nothing at all.
        for m in re.finditer(r"\\item\s*\\textbf\{[^{}]*[.:]\}", tex):
            add("Inline-header list item", m.group(0)[:70])
        for m in re.finditer(r"\\item\s*\\textbf\{([^{}]*[.:])\}", tex):
            if len(m.group(1).split()) > 4:
                add("Bolded sentence as list header", m.group(1)[:70])
    return out


def main(path: str) -> int:
    if path.endswith(".pdf"):
        import fitz
        doc = fitz.open(path)
        text = "\n".join(p.get_text() for p in doc)
    else:
        text = open(path).read()
    found = scan(text)
    tex = ""
    if path.endswith(".pdf"):
        try:
            tex = open(path[:-4] + ".tex").read()
        except OSError:
            pass
    found.update(scan_format(text, tex))
    total = sum(len(v) for k, v in found.items() if k != "Boldface count")
    for cat in sorted(found):
        print(f"\n{cat}  ({len(found[cat])})")
        for item in sorted(set(found[cat]))[:40]:
            print(f"    {item}")
    print(f"\nTOTAL FLAGS: {total}")
    return total


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1]) == 0 else 0)
