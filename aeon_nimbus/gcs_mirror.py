"""One small private object store, for the things that must outlive a container.

Cloud Run hands each container a fresh disk, so anything SQLite holds is gone
the moment the service scales to zero. The user register solved that first by
mirroring itself to a Cloud Storage object; the change log needs exactly the
same thing, and two copies of the same code would be two places to fix.

There is no SDK here on purpose. Cloud Run's metadata server issues the token
and Cloud Storage has a plain JSON API, so httpx — already a dependency — is
enough, and the container stays small.

Off Cloud Run there is no metadata server, so every call quietly does nothing.
That is what makes it safe to call from anywhere: a laptop, a test, a CI run.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

METADATA = ("http://metadata.google.internal/computeMetadata/v1/instance/"
            "service-accounts/default/token")

_token_cache: tuple[str, float] | None = None


def bucket() -> str:
    """The mirror bucket, or empty if mirroring should not happen.

    A real database makes this pointless and actively unhelpful: two stores
    that can disagree with each other about what happened.
    """
    if os.environ.get("DATABASE_URL"):
        return ""
    return os.environ.get("USER_STORE_BUCKET", "").strip()


def token() -> str | None:
    """A short-lived access token from the Cloud Run metadata server."""
    global _token_cache
    if _token_cache and _token_cache[1] > time.time() + 60:
        return _token_cache[0]
    try:
        r = httpx.get(METADATA, headers={"Metadata-Flavor": "Google"}, timeout=3)
        r.raise_for_status()
        d = r.json()
        _token_cache = (d["access_token"], time.time() + int(d.get("expires_in", 3000)))
        return _token_cache[0]
    except Exception:
        return None            # not on Cloud Run, which is normal locally


def reset_token_cache() -> None:
    """Forget the cached token. For tests, which must not inherit one."""
    global _token_cache
    _token_cache = None


def available() -> bool:
    return bool(bucket()) and token() is not None


def put(name: str, payload: dict[str, Any], what: str = "") -> bool:
    """Write one JSON object. Never raises: a failed mirror must not break the
    thing it was mirroring, which has already been committed locally."""
    b, t = bucket(), token()
    if not (b and t):
        return False
    try:
        r = httpx.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{b}/o",
            params={"uploadType": "media", "name": name},
            headers={"Authorization": f"Bearer {t}",
                     "Content-Type": "application/json"},
            content=json.dumps(payload, default=str), timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("could not mirror %s: %s", what or name, e)
        return False


def get(name: str, what: str = "") -> dict[str, Any] | None:
    """Read one JSON object back. None if absent or unreachable."""
    b, t = bucket(), token()
    if not (b and t):
        return None
    try:
        r = httpx.get(
            f"https://storage.googleapis.com/storage/v1/b/{b}/o/{name}",
            params={"alt": "media"},
            headers={"Authorization": f"Bearer {t}"}, timeout=10)
        if r.status_code == 404:
            return None        # first ever boot, nothing mirrored yet
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("could not read %s: %s", what or name, e)
        return None


def delete(name: str) -> bool:
    """Remove one object. Used to clean up after the self-test."""
    b, t = bucket(), token()
    if not (b and t):
        return False
    try:
        r = httpx.delete(f"https://storage.googleapis.com/storage/v1/b/{b}/o/{name}",
                         headers={"Authorization": f"Bearer {t}"}, timeout=10)
        return r.status_code in (200, 204, 404)
    except Exception:
        return False


def self_test() -> dict[str, Any]:
    """Prove the mirror by using it: write a probe, read it back, remove it.

    The change log is only as durable as this round trip, and nothing exercises
    it until somebody makes an edit — by which time a broken mirror has already
    cost the entry it was meant to keep. This makes it checkable on demand.
    """
    b = bucket()
    if not b:
        import os
        if os.environ.get("DATABASE_URL"):
            return {"ok": True, "detail": "A database is attached, so the change log "
                                          "persists on its own and no mirror is used."}
        return {"ok": False, "detail": "No mirror bucket is set, so the change log "
                                       "lives only on this machine's disk."}
    if not token():
        return {"ok": False, "detail": "No credentials for the bucket. This is normal "
                                       "off Cloud Run, where nothing is mirrored."}
    probe = {"probe": True, "value": 41.25}
    name = "_self_test.json"
    if not put(name, probe, what="the self-test probe"):
        return {"ok": False, "bucket": b,
                "detail": f"Could not write to gs://{b}. The service account needs "
                          f"object write on that bucket."}
    back = get(name, what="the self-test probe")
    delete(name)
    if back != probe:
        return {"ok": False, "bucket": b,
                "detail": "The probe was written but did not read back the same."}
    return {"ok": True, "bucket": b,
            "detail": f"Written and read back from gs://{b}. The change log survives "
                      f"a restart."}
