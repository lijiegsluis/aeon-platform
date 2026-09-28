"""Keep the change log alive across Cloud Run cold starts.

The History screen reads one table: audit_log. That table lives in SQLite, on a
disk Cloud Run throws away when the service scales to zero. So an analyst could
edit a figure on Monday, and on Tuesday the log that recorded it — who changed
what, from what, and the way to put it back — was simply empty. The change had
happened and the record of it had not survived.

SharePoint was configured for this and does not solve it on its own: it is a
one-way copy, written after the local commit and never read back, so a cold
start still leaves the screen blank. This is the copy the platform restores
FROM, so the log the user opens is the log they left.

Same shape as the user register, and now the same code underneath it:
`gcs_mirror` holds the token and the two HTTP calls.

Set DATABASE_URL and this stands down entirely — Postgres persists on its own.
"""

from __future__ import annotations

import logging
from datetime import datetime

from aeon_nimbus import gcs_mirror

log = logging.getLogger(__name__)

OBJECT = "changes.json"

# The whole object is rewritten on every change, so it has to stay small enough
# that writing it is cheap. A few analysts make a handful of edits a day, and
# 4000 entries is years of that. Older entries fall off the mirror; they are
# still in whatever database or SharePoint list holds the full history.
KEEP = 4000

FIELDS = ("actor", "action", "entity", "entity_id", "detail")


def enabled() -> bool:
    return gcs_mirror.available()


def worth_mirroring(action: str) -> bool:
    """Whether this audit row is one the change log actually shows.

    audit() records far more than the History screen lists — every question
    asked of the AI analyst goes through it. Rewriting the whole object on each
    of those would put a bucket write, with a ten second timeout, inside the
    request path of every chat message, to mirror a row nobody can see.
    """
    from aeon_nimbus.studio_api import CHANGES
    return action in CHANGES


def _rows(db, limit: int | None = None) -> list[dict]:
    # Read at call time, not bound as a default: a default argument is
    # evaluated once when the module loads, so the cap could never be changed.
    limit = limit or KEEP
    from aeon_nimbus import db as D
    from aeon_nimbus.studio_api import CHANGES
    q = (db.query(D.AuditLog)
           .filter(D.AuditLog.action.in_(list(CHANGES)))
           .order_by(D.AuditLog.id.desc()).limit(limit).all())
    out = []
    for r in reversed(q):                       # oldest first, so ids re-issue in order
        row = {f: getattr(r, f) for f in FIELDS}
        row["ts"] = r.ts.isoformat() if r.ts else None
        out.append(row)
    return out


def save(db) -> bool:
    """Mirror the change log. Never raises: the change is already committed."""
    if not gcs_mirror.bucket():
        return False
    rows = _rows(db)
    return gcs_mirror.put(OBJECT, {"changes": rows}, what="the change log")


def load(db) -> int:
    """Restore the log into an empty table. Returns how many came back.

    Only into an EMPTY table. A container that already has entries has either
    restored once or written its own, and merging two partial logs by id would
    invent history that never happened.
    """
    if not gcs_mirror.bucket():
        return 0
    from aeon_nimbus import db as D
    if db.query(D.AuditLog).first():
        return 0
    payload = gcs_mirror.get(OBJECT, what="the change log")
    if not payload:
        return 0
    rows = payload.get("changes") or []
    n = 0
    for row in rows:
        ts = row.get("ts")
        try:
            when = datetime.fromisoformat(ts) if ts else None
        except (TypeError, ValueError):
            when = None
        db.add(D.AuditLog(actor=row.get("actor") or "system",
                          action=row.get("action") or "",
                          entity=row.get("entity") or "",
                          entity_id=str(row.get("entity_id") or ""),
                          detail=row.get("detail") or {},
                          **({"ts": when} if when else {})))
        n += 1
    db.commit()
    if n:
        log.info("change log restored: %d entries", n)
    return n
