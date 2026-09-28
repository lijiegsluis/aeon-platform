"""Numbered references, so a claim in the report points at its source.

A report that says "source: the platform" has not cited anything. Every source
the platform holds for Safaricom is registered here once, gets a number, and the
text carries that number. The reference list at the back is generated from
whatever was actually cited, so it cannot list a source nothing uses or omit one
something does.
"""
from __future__ import annotations


class References:
    def __init__(self):
        self._order: list[str] = []

    def cite(self, key: str) -> str:
        """Register a source and return its superscript marker."""
        if key not in self._order:
            self._order.append(key)
        return "\\textsuperscript{%d}" % (self._order.index(key) + 1)

    def n(self, key: str) -> int:
        if key not in self._order:
            self._order.append(key)
        return self._order.index(key) + 1

    def used(self) -> list[tuple[int, str]]:
        return [(i + 1, k) for i, k in enumerate(self._order)]

    def __len__(self):
        return len(self._order)


def build(rec: dict) -> dict:
    """The citable sources for this company, keyed by a short handle."""
    st = rec.get("statements") or {}
    by_year = st.get("source_by_year") or {}
    out = {}
    for fy, src in by_year.items():
        out[f"fs:{fy}"] = src
    out["fs:basis"] = st.get("source") or ""
    out["fs:note"] = st.get("note") or ""
    for i, s in enumerate(rec.get("sources") or []):
        out[f"src:{i}"] = s
    q = rec.get("qualitative") or {}
    seen = {}
    for block in ("market_overview", "revenue_drivers", "cost_pressures",
                  "non_financial", "outlook", "sentiment"):
        for pt in (q.get(block) or []):
            # sentiment is a list of plain strings; the rest are {point, source}
            if not isinstance(pt, dict):
                continue
            s = (pt.get("source") or "").strip()
            if s and s not in seen:
                seen[s] = f"q:{len(seen)}"
                out[seen[s]] = s
    out["_qual_by_source"] = seen
    return out
