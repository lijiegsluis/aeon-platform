"""Shared test setup.

Every route behind /api needs a session. Tests sign in via the bootstrap admin
rather than bypassing the guard — a test that passed anonymously would mean
the guard had been lost.
"""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("COOKIE_INSECURE", "1")
os.environ.setdefault("ADMIN_EMAIL",    "tests@aeon.test")
os.environ.setdefault("ADMIN_USERNAME", "testadmin")
os.environ.setdefault("ADMIN_PASSWORD", "tests-bootstrap-9271")


def sign_in(c):
    """Sign a TestClient in as the bootstrap admin. Returns the same client."""
    r = c.post("/api/auth/login", json={
        "identifier": os.environ["ADMIN_EMAIL"],
        "password":   os.environ["ADMIN_PASSWORD"],
    })
    assert r.status_code == 200, f"test client could not sign in: {r.text}"
    return c
