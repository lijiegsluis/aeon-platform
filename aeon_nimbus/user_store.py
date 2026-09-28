"""Keep the user register alive across Cloud Run cold starts.

Cloud Run hands each container a fresh disk, so the SQLite file — and every
account in it — disappears the moment the service scales to zero. An admin
would add a colleague on Monday and find them gone on Tuesday.

Until a managed Postgres is attached, the register is mirrored to one small
object in Cloud Storage: written whenever it changes, read back when a
container starts with an empty table. Only the user table is mirrored;
everything else re-seeds deterministically from the repository baseline.

Set DATABASE_URL and this stands down entirely — Postgres persists on its own
and a second copy of the register would only be a way to disagree with itself.

The object holds bcrypt hashes, never plaintext, and the bucket is private.
Cloud Storage buckets are private by default; deploy.sh does not open it.

No SDK: the Cloud Run metadata server issues the token and Cloud Storage has a
plain JSON API, so httpx (already a dependency) is enough. Off Cloud Run there
is no metadata server, so every call here quietly does nothing.
"""

from __future__ import annotations

import logging

from aeon_nimbus import gcs_mirror

log = logging.getLogger(__name__)

OBJECT = "users.json"

FIELDS = ("email", "name", "password_hash", "role", "active", "created_by")


_bucket = gcs_mirror.bucket
_token = gcs_mirror.token


def enabled() -> bool:
    return gcs_mirror.available()


def save(db) -> None:
    """Mirror the register. Never raises: a failed mirror must not break a login."""
    if not gcs_mirror.bucket():
        return
    from aeon_nimbus.auth import User
    rows = [{f: getattr(u, f) for f in FIELDS} for u in db.query(User).all()]
    if gcs_mirror.put(OBJECT, {"users": rows}, what="the user register"):
        log.info("user register mirrored: %d accounts", len(rows))


def load(db) -> int:
    """Restore the register into an empty table. Returns how many came back."""
    if not gcs_mirror.bucket():
        return 0
    from aeon_nimbus.auth import User
    if db.query(User).first():
        return 0               # this container already has accounts; leave them
    payload = gcs_mirror.get(OBJECT, what="the user register")
    if not payload:
        return 0
    rows = payload.get("users", [])
    for row in rows:
        db.add(User(**{f: row.get(f) for f in FIELDS}))
    db.commit()
    log.info("user register restored: %d accounts", len(rows))
    return len(rows)
