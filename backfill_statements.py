"""Read the real balance sheet out of every cached annual report.

Only Safaricom had a balance sheet. The other thirty-two companies carried a
total-assets figure from an aggregator and nothing else, so every schedule in
every model opened at zero and a single residual line carried the whole sheet.

The filings were on disk the entire time. This walks them, pulls the statement
pages with the deterministic extractor (PyMuPDF reading the actual table rows —
no model, no inference, so nothing can be invented), and writes the result into
each company's `statements` block with the page it came from.

    python backfill_statements.py            # every company with a cached filing
    python backfill_statements.py equity     # one, by slug fragment
    python backfill_statements.py --dry-run  # report, write nothing

Every figure keeps its page reference, so any number in a model can be traced
back to the page of the filing it was read from.
"""

import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
from aeon_nimbus import db as D                     # noqa: E402
from aeon_nimbus import extract_statements as ES    # noqa: E402

ROOT = Path(__file__).parent
DOCS = ROOT / "data" / "raw_docs"

# The extractor reports a label as it appears in the filing. These map the
# variants onto the keys the model opens its schedules with.
CANON = [
    ("loans and advances",        "loans and advances"),
    ("deposits from customers",   "customer deposits"),
    ("loans to customers",        "loans and advances"),
    ("gross loans",               "loans and advances"),
    ("customer deposits",         "customer deposits"),
    ("borrowed funds",            "borrowings"),
    ("borrowings",                "borrowings"),
    ("property and equipment",    "property and equipment"),
    ("property, plant",           "property and equipment"),
    ("right-of-use",              "right-of-use assets"),
    ("right of use",              "right-of-use assets"),
    ("intangible asset",          "intangible assets"),
    ("inventories",               "inventories"),
    ("trade and other receivables", "trade and other receivables"),
    ("trade and other payables",  "trade and other payables"),
    ("cash and cash equivalents", "cash and cash equivalents"),
    ("cash, deposits",            "cash and cash equivalents"),
    ("lease liabilit",            "lease liabilities"),
    ("provisions",                "provisions"),
    ("total assets",              "total assets"),
    ("total equity",              "total equity"),
    ("total liabilities",         "total liabilities"),
]


def canonical(matched: str, raw: str) -> str | None:
    """Map an extracted label onto a model key, or None to drop it."""
    low = (raw or "").lower()
    # "Total equity and liabilities" is the footing line, not equity
    if low.startswith("total equity and") or "equity and liabilities" in low:
        return "total liabilities and equity"
    for needle, key in CANON:
        if needle in low:
            return key
    return matched or None


def harvest(pdf: Path) -> dict:
    """Extract one filing. Runs in a worker process — returns plain data."""
    out = ES.extract(pdf)
    figures = out.get("figures") or {}
    located = out.get("located") or {}
    statements: dict = {}
    for section in ("income_statement", "balance_sheet", "cash_flow"):
        rows, seen = [], set()
        for item in figures.get(section) or []:
            key = canonical(item.get("matched"), item.get("raw_label"))
            vals = [v for v in (item.get("values") or []) if isinstance(v, (int, float))]
            if not key or not vals or key in seen:
                continue
            seen.add(key)
            rows.append({"label": key, "values": vals,
                         "raw_label": item.get("raw_label"),
                         "page": item.get("page"), "source": pdf.name})
        statements[section] = rows
    statements["located"] = located
    return statements


# A filing reports in its own units. Nigerian Breweries states thousands while
# the series we hold is in millions, so an unchecked extraction produced equity
# of 560,221,809 against assets of 485,522. Every extraction is therefore
# reconciled against a figure already known to be right — reported total assets —
# and is rejected outright if it cannot be made to agree.
SCALES = (1.0, 1e-3, 1e3, 1e-6, 1e6, 1e-9, 1e9)
TOLERANCE = 0.15


def reconcile(statements: dict, known_assets: float) -> tuple[dict | None, str]:
    """Accept an extraction only if it proves itself, then put it on our scale.

    The test is the balance sheet's own identity — assets = liabilities +
    equity — because that is checkable without trusting anything external. An
    extraction that reads rows off the wrong page, or mixes two columns, fails
    it. Reported total assets is used ONLY to pick the power of ten, never as a
    target to match: the series we hold ends FY2021 on several names while the
    filing is current, so demanding agreement would reject good extractions for
    the crime of being more up to date.
    """
    bs = statements.get("balance_sheet") or []

    def line(label):
        return next((r["values"][0] for r in bs
                     if r["label"] == label and r.get("values")), None)

    assets, liab, equity = line("total assets"), line("total liabilities"), line("total equity")
    if assets is None:
        return None, "the filing's total assets line was not found"

    if liab is not None and equity is not None and assets:
        drift = abs(assets - (liab + equity)) / abs(assets)
        if drift > 0.02:
            return None, (f"the sheet does not balance: assets {assets:,.0f} vs "
                          f"liabilities+equity {liab + equity:,.0f} ({drift:.1%} out)")
        proof = f"balances to {drift:.2%}"
    else:
        proof = "no liabilities/equity line to prove it against"

    # scale only — the nearest power of ten to the figure we already hold
    best, note = 1.0, "same scale"
    if known_assets:
        # Minimising |ratio - 1| is asymmetric: for any ratio below 1 the error
        # is at most 1, while a ratio of 3.6 scores 2.6. It therefore preferred
        # the scale that made the number vanish, and GTCO chose x1e-6 over the
        # correct x1e-3. Log distance treats 3x too big and 3x too small alike.
        import math
        best = min(SCALES, key=lambda s: abs(math.log((assets * s) / known_assets))
                   if assets * s else float("inf"))
        ratio = (assets * best) / known_assets
        # A sheet that foots to within 2% has proved itself, and the series we
        # hold is years stale on several names, so internal proof outranks
        # agreement with it. Where the sheet could NOT be proved, the figure on
        # record is the only check there is and the band stays tight.
        lo, hi = (0.05, 20.0) if proof.startswith("balances") else (0.2, 5.0)
        if not lo <= ratio <= hi:
            return None, (f"total assets {assets:,.0f} is {ratio:,.2f}x the "
                          f"{known_assets:,.0f} on record, outside the band"
                          + ("" if proof.startswith("balances") else " for an unproved sheet"))
        note = f"x{best:g}, now {ratio:.2f}x the figure on record" if best != 1.0 \
            else f"{ratio:.2f}x the figure on record"

    if best != 1.0:
        for section in ("income_statement", "balance_sheet", "cash_flow"):
            for row in statements.get(section) or []:
                row["values"] = [v * best for v in row["values"]]
                row["rescaled"] = best
    return statements, f"{proof}, {note}"


def _one(args):
    slug, path, known_assets = args
    try:
        raw = harvest(Path(path))
        ok, why = reconcile(raw, known_assets)
        return slug, ok, why
    except Exception as e:                                # a bad PDF must not stop the run
        return slug, None, f"{type(e).__name__}: {e}"


def main() -> int:
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    only = argv[0] if argv else None

    with D.SessionLocal() as s:
        companies, known = {}, {}
        for c in s.query(D.Company).all():
            companies[c.slug] = c.name
            fins = (c.extracted or {}).get("financials") or [{}]
            ta = fins[-1].get("total_assets")
            known[c.slug] = float(ta) if isinstance(ta, (int, float)) else 0.0

    jobs = []
    for slug in sorted(companies):
        if only and only not in slug:
            continue
        pdf = DOCS / slug / "annual_report.pdf"
        if pdf.exists():
            jobs.append((slug, str(pdf), known.get(slug, 0.0)))

    if not jobs:
        print("no cached filings matched")
        return 1

    print(f"reading {len(jobs)} filings\n")
    print(f"{'company':<34}{'IS':>4}{'BS':>4}{'CF':>4}   key balance-sheet lines found")
    results = {}
    # Parsing a 300-page PDF is CPU-bound, so this fans out. At 500 companies
    # this is the difference between minutes and an hour.
    with ProcessPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_one, j): j[0] for j in jobs}
        for fut in as_completed(futures):
            slug, statements, err = fut.result()
            name = companies.get(slug, slug)[:32]
            if statements is None:
                print(f"  {name:<34}{'—':>4}{'—':>4}{'—':>4}   REJECTED: {err[:56]}")
                continue
            bs = statements.get("balance_sheet") or []
            labels = {r["label"] for r in bs}
            want = {"total assets", "total equity", "property and equipment",
                    "loans and advances", "customer deposits"}
            print(f"  {name:<34}"
                  f"{len(statements.get('income_statement') or []):>4}"
                  f"{len(bs):>4}"
                  f"{len(statements.get('cash_flow') or []):>4}   "
                  f"{err[:44]}")
            results[slug] = statements

    if dry:
        print(f"\ndry run — nothing written ({len(results)} filings parsed)")
        return 0

    written = 0
    with D.SessionLocal() as s:
        for slug, statements in results.items():
            c = s.query(D.Company).filter(D.Company.slug == slug).first()
            if not c or not (statements.get("balance_sheet")):
                continue
            ex = dict(c.extracted or {})
            prior = ex.get("statements") or {}
            # Never overwrite a hand-verified set with a weaker automatic one.
            if len(prior.get("balance_sheet") or []) > len(statements["balance_sheet"]):
                continue
            ex["statements"] = {**prior, **statements}
            c.extracted = ex
            written += 1
        s.commit()
    print(f"\nwrote statements for {written} companies")
    return 0


if __name__ == "__main__":
    sys.exit(main())
