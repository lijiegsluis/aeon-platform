"""What the workbook actually says, read back from the workbook.

Three builders produce models here: a hand-built flagship for Safaricom, a
sector-aware builder for everything else, and a generic fallback. Each writes
formulas, and the flagship's are specific to it. Any attempt to mirror all three
in Python drifts the moment one of them changes, and a drift here means the
screen and the workbook tell a reader two different things about the same
company. They already did, on 27 of 33 target prices.

So the number is not recomputed. It is read from the workbook, by calculating
the workbook, which makes Excel's arithmetic the single authority and matches
how the export already works. That costs a few seconds per company, so it runs
when models are built rather than when a page is rendered, and the answer is
stored on the company record for the screen to read.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

KEY = "_published_valuation"


def read(path: str | Path) -> dict[str, Any]:
    """Calculate a built workbook and return the valuation it publishes."""
    warnings.filterwarnings("ignore")
    import formulas
    from openpyxl import load_workbook

    path = Path(path)
    if not path.exists():
        return {}
    sol = formulas.ExcelModel().loads(str(path)).finish().calculate()
    wb = load_workbook(path)
    name = path.name.upper()
    sheet = "Valuation" if "Valuation" in wb.sheetnames else (
        "Val_Weighted" if "Val_Weighted" in wb.sheetnames else None)
    if not sheet:
        return {}

    def val(cell: str):
        for k, v in sol.items():
            if k.upper().endswith(f"'[{name}]{sheet.upper()}'!{cell.upper()}"):
                try:
                    return v.value[0, 0]
                except Exception:
                    return v
        return None

    ws = wb[sheet]
    out: dict[str, Any] = {"source": f"{path.name}:{sheet}"}
    for r in range(1, ws.max_row + 1):
        label = str(ws.cell(row=r, column=1).value or "").strip().lower()
        if not label:
            continue
        # the sector and flagship sheets put the figure in D, the weighted sheet in B
        v = val(f"D{r}")
        if v is None:
            v = val(f"B{r}")
        # First match wins, and the explicit label wins over the loose one. The
        # sensitivity grid below also carries a row called "Weighted target",
        # and taking the last match read Safaricom's target as 5.34 instead of
        # the 26.72 the sheet actually publishes.
        if label.startswith("weighted target price"):
            out["target"] = v
        elif label.startswith("weighted target") and "target" not in out:
            out["target"] = v
        elif label.startswith("upside to target") and "upside" not in out:
            out["upside"] = v
        elif (label.startswith("recommendation") or label.startswith("rating (")) \
                and "stance" not in out:
            out["stance"] = v
        elif (label.startswith("quoted price") or label.startswith("current price")) \
                and "price" not in out:
            out["price"] = v
    if isinstance(out.get("stance"), str) and "—" in out["stance"]:
        out["not_rated_reason"] = out["stance"].split("—", 1)[1].strip()
        out["stance"] = "NR"
    return out


def refresh(db, *, model_path) -> dict[str, int]:
    """Read every built workbook and store what it publishes. Returns a tally."""
    from aeon_nimbus import db as D
    done = skipped = failed = 0
    for c in db.query(D.Company).all():
        p = Path(model_path(c.slug))
        if not p.exists():
            skipped += 1
            continue
        try:
            pub = read(p)
        except Exception as exc:
            log.warning("could not read %s: %s: %s", c.slug, type(exc).__name__, exc)
            failed += 1
            continue
        if not pub.get("target") and not pub.get("stance"):
            skipped += 1
            continue
        ex = dict(c.extracted or {})
        ex[KEY] = pub
        c.extracted = ex
        # SQLAlchemy does not see a mutated JSON column without this.
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(c, "extracted")
        done += 1
    db.commit()
    return {"stored": done, "skipped": skipped, "failed": failed}


def persist_to_seed(db, *, data_dir) -> dict[str, int]:
    """Write each stored valuation into the seed file it was read from.

    Cloud Run has no managed database, so its SQLite is rebuilt from
    data/extracted on every cold start. A valuation held only in the local
    database would therefore never reach the live site, and the screen there
    would silently fall back to computing its own number again, which is the
    disagreement this was built to remove.
    """
    import json
    from pathlib import Path

    from aeon_nimbus import db as D
    written = missing = 0
    for c in db.query(D.Company).all():
        pub = (c.extracted or {}).get(KEY)
        if not pub:
            continue
        p = Path(data_dir) / "extracted" / f"{c.slug}.json"
        if not p.exists():
            missing += 1
            continue
        rec = json.loads(p.read_text())
        rec[KEY] = pub
        p.write_text(json.dumps(rec, indent=2, ensure_ascii=False))
        written += 1
    return {"written": written, "no_seed_file": missing}
