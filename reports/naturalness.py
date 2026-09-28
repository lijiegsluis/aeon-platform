"""Measures the report against the house AI-writing framework.

docs/reference/ai_writing_framework.md is the standard. This implements the parts
of it that can be counted, so a change can be shown to have moved the number rather
than argued about. It does NOT implement the parts that need judgement: topic
substitutability, structural asymmetry and evidence of intellectual cost are read
by a person, because a regex cannot tell whether a paragraph survives having its
subject swapped.

Rates are per 1,000 words so documents of different lengths compare.
"""
from __future__ import annotations
import re
from collections import Counter

# Layer 1. Judged in context downstream: "robust" on an estimator and "leverage"
# as a noun meaning debt are correct financial usage, not model vocabulary.
LEXICAL = ["delve", "intricate", "meticulous", "pivotal", "crucial", "nuanced",
           "interplay", "foster", "showcase", "underscore", "testament", "vibrant",
           "enduring", "holistic", "seamless", "groundbreaking", "myriad",
           "plethora", "multifaceted", "transformative"]
PHRASES = [r"plays? a pivotal role", r"underscores? the importance",
           r"serves? as a testament", r"rapidly evolving", r"a nuanced understanding",
           r"offers? valuable insights?", r"commitment to excellence",
           r"in today's[^.]{0,40}landscape"]

# Layer 2
COPULA = [r"\bserves as\b", r"\bstands as\b", r"\brepresents\b", r"\bfeatures\b",
          r"\bconstitutes\b"]
NEG_PARALLEL = [r"\bnot only\b[^.]{0,60}\bbut also\b", r"\bnot\s+\w+[^.]{0,40},\s*but\b",
                r"\brather than\b", r",\s+not\s+(?:the|a|an|because|about|on|by|that|its)\b"]
SIGNPOST = r"(?:^|\.\s+)(Additionally|Furthermore|Moreover|Consequently|Ultimately|In contrast|On the other hand|At the same time|Notably|Importantly)\b"

# Layer 3
INTERPRETIVE = [r"[Tt]his (?:highlights|underscores|demonstrates|reflects|illustrates|shows) the",
                r"which (?:highlights|underscores|demonstrates|reflects)\b",
                r"\bhighlighting the\b", r"\bunderscoring the\b", r"\breflecting the\b",
                r"\bdemonstrating the\b"]
INFLATED = [r"marked a (?:pivotal|significant|turning)", r"represented a significant",
            r"a turning point", r"enduring legacy", r"transformative",
            r"significant shift", r"plays a key role"]

# Layer 5
HEDGE_LOOSE = [r"\bmay suggest\b", r"\bcould potentially\b", r"\bit is important to (?:note|consider)\b",
               r"\bappears to indicate\b", r"\bit is worth noting\b", r"\bsome argue\b"]
VAGUE_ATTRIB = [r"\bexperts?\s+(?:say|argue|note|suggest|believe)", r"\bobservers?\s+(?:note|have noted)",
                r"\bindustry reports?\s+suggest", r"\banalysts? (?:say|suggest|argue)\b",
                r"\bit is (?:widely|generally) (?:understood|believed|accepted|repeated)",
                r"\bseveral studies\b", r"\bhas circulated\b", r"\bwidely repeated\b",
                r"\bthe reason usually given\b", r"\bpress coverage\b"]
PROMO = [r"\brenowned\b", r"\bvibrant\b", r"\bgroundbreaking\b", r"\bboasts\b",
         r"\bpowerful\b", r"\bcutting-edge\b", r"\bworld-class\b", r"\bbest-in-class\b"]

# Layer 6
ARTIFACT = [r"contentReference", r"oaicite", r"turn\d+search\d+", r"\[Insert",
            r"\[Add source", r"(?:^|\.\s+)Here is the", r"(?:^|\.\s+)Certainly!",
            r"I hope this helps", r"\bAs an AI\b", r"knowledge cutoff"]


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def scan(text: str) -> dict:
    words = re.findall(r"[A-Za-z][A-Za-z'-]+", text)
    n = max(1, len(words))
    per_k = lambda c: round(c * 1000 / n, 2)
    out = {"words": n, "sentences": len(_sentences(text)), "flags": {}, "rates": {}}

    def count(name, pats, flags=re.I):
        hits = []
        for p in pats:
            hits += [m.group(0) for m in re.finditer(p, text, flags)]
        out["flags"][name] = hits
        out["rates"][name] = per_k(len(hits))
        return len(hits)

    count("lexical", [rf"\b{w}\w*\b" for w in LEXICAL])
    count("stock phrases", PHRASES)
    count("copula avoidance", COPULA)
    count("negative parallelism", NEG_PARALLEL)
    count("signposting", [SIGNPOST])
    count("interpretive suffix", INTERPRETIVE)
    count("inflated significance", INFLATED)
    count("loose hedging", HEDGE_LOOSE)
    count("vague attribution", VAGUE_ATTRIB)
    count("promotional", PROMO)
    count("process artifact", ARTIFACT)

    readers = re.findall(r"[Aa] reader who [^.]{0,90}", text)
    out["flags"]["reader-address template"] = readers
    out["rates"]["reader-address template"] = per_k(len(readers))

    # participial tails: a clause ending in ", <verb>ing ..." that closes the sentence
    tails = [t for t in re.findall(r",\s+\w+ing\b[^.]{10,90}\.", text)
             if not re.search(r"\d", t)]
    out["flags"]["participial tail"] = tails
    out["rates"]["participial tail"] = per_k(len(tails))

    # rule of three: "a, b and c" where all three are single words of the same shape
    triples = [m for m in re.findall(r"\b(\w{4,14}), (\w{4,14}) and (\w{4,14})\b", text)
               if not any(w[0].isupper() or w.isdigit() for w in m)]
    out["flags"]["rule of three"] = [" ".join(t) for t in triples]
    out["rates"]["rule of three"] = per_k(len(triples))

    # uniform informational weight: the framework says human prose is UNEVEN
    lens = [len(s.split()) for s in _sentences(text) if len(s.split()) > 3]
    if lens:
        mean = sum(lens) / len(lens)
        sd = (sum((x - mean) ** 2 for x in lens) / len(lens)) ** 0.5
        out["sentence_mean"] = round(mean, 1)
        out["sentence_sd"] = round(sd, 1)
        out["sentence_cv"] = round(sd / mean, 3)     # higher is more human

    # repeated sentence openings: a template shows up as a concentrated stem
    stems = Counter(" ".join(s.split()[:3]).lower() for s in _sentences(text)
                    if len(s.split()) >= 4)
    out["top_openings"] = stems.most_common(8)
    out["total_flags"] = sum(len(v) for v in out["flags"].values())
    out["flag_rate"] = per_k(out["total_flags"])
    return out


def report(text: str) -> str:
    r = scan(text)
    lines = [f"words {r['words']:,}   sentences {r['sentences']:,}",
             f"sentence length mean {r.get('sentence_mean')}  sd {r.get('sentence_sd')}  "
             f"cv {r.get('sentence_cv')}  (higher cv = more uneven = more human)", ""]
    lines.append(f"{'category':<24}{'count':>7}{'per 1k words':>14}")
    for k in sorted(r["flags"], key=lambda x: -len(r["flags"][x])):
        lines.append(f"  {k:<22}{len(r['flags'][k]):>7}{r['rates'][k]:>14}")
    lines.append(f"  {'TOTAL':<22}{r['total_flags']:>7}{r['flag_rate']:>14}")
    lines.append("")
    lines.append("most repeated sentence openings:")
    for stem, c in r["top_openings"]:
        if c > 2:
            lines.append(f"  {c:>3}x  {stem}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys, fitz
    doc = fitz.open(sys.argv[1])
    body = "\n".join(p.get_text() for p in doc)
    body = re.sub(r"Aeon Nimbus[^\n]*\n", "", body)
    print(report(body))
