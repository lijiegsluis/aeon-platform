"""Session-based authentication for Aeon Nimbus.

Three roles: admin (full access + user management), analyst (read + edit),
viewer (read only). Sessions are signed cookies, 12hr expiry, never plaintext.

Login accepts username OR email.

Env vars:
  ADMIN_EMAIL    — bootstrapped admin email (default: glasmikgamer@gmail.com)
  ADMIN_USERNAME — bootstrapped admin username (default: admin)
  ADMIN_PASSWORD — bootstrapped admin password (default: aeonnimbus)
  SESSION_SECRET — signing key for cookies (randomly generated if absent)
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import Column, DateTime, Integer, String, Boolean
from sqlalchemy.orm import Session

from aeon_nimbus.db import Base, SessionLocal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLES = ("admin", "analyst", "viewer")
RANK  = {"viewer": 0, "analyst": 1, "admin": 2}

# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "auth_users"

    id            = Column(Integer, primary_key=True)
    email         = Column(String, unique=True, nullable=False, index=True)
    username      = Column(String, unique=True, nullable=True, index=True)
    name          = Column(String, default="")
    password_hash = Column(String, nullable=False)
    role          = Column(String, default="viewer")
    active        = Column(Boolean, default=True)
    created_at    = Column(DateTime(timezone=True),
                           default=lambda: datetime.now(timezone.utc))
    created_by    = Column(String, default="system")
    last_login    = Column(DateTime(timezone=True), nullable=True)


# ---------------------------------------------------------------------------
# Password helpers (bcrypt)
# ---------------------------------------------------------------------------

def hash_password(plaintext: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plaintext.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Lookup by username or email
# ---------------------------------------------------------------------------

def find_user(db, identifier: str) -> Optional[User]:
    """Find a user by username or email (case-insensitive)."""
    identifier = identifier.strip().lower()
    # Try username first
    user = db.query(User).filter(
        User.username == identifier
    ).first()
    if user:
        return user
    # Fall back to email
    return db.query(User).filter(User.email == identifier).first()


# ---------------------------------------------------------------------------
# Cookie session
# ---------------------------------------------------------------------------
COOKIE = "aeon_session"
SESSION_TTL = 12 * 3600  # 12 hours

_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
_serializer = URLSafeTimedSerializer(_SECRET)


def issue_session(response: Response, user: User) -> None:
    token = _serializer.dumps({"uid": user.id, "role": user.role})
    response.set_cookie(
        COOKIE, token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("HTTPS") == "1",
    )


def _decode_token(token: str) -> Optional[dict]:
    try:
        return _serializer.loads(token, max_age=SESSION_TTL)
    except (SignatureExpired, BadSignature):
        return None


def _get_user_from_request(request: Request) -> Optional[User]:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    data = _decode_token(token)
    if not data:
        return None
    with SessionLocal() as db:
        user = db.get(User, data["uid"])
        if user and user.active:
            return user
    return None


def touch_login(db, user: User) -> None:
    user.last_login = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

def current_user(request: Request) -> Optional[User]:
    return _get_user_from_request(request)


def optional_user(request: Request) -> Optional[User]:
    return _get_user_from_request(request)


def require_viewer(request: Request) -> User:
    user = _get_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in required")
    return user


def require_analyst(request: Request) -> User:
    user = require_viewer(request)
    if RANK.get(user.role, 0) < RANK["analyst"]:
        raise HTTPException(status_code=403, detail="Analyst access required")
    return user


def require_admin(request: Request) -> User:
    user = require_viewer(request)
    if RANK.get(user.role, 0) < RANK["admin"]:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# Bootstrap first admin
# ---------------------------------------------------------------------------

def ensure_first_admin() -> None:
    """Create the admin account on first run. Idempotent."""
    email    = os.environ.get("ADMIN_EMAIL",    "glasmikgamer@gmail.com")
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "aeonnimbus")
    with SessionLocal() as db:
        existing = db.query(User).filter(
            (User.email == email) | (User.username == username)
        ).first()
        if existing:
            # Ensure username is set if missing on existing account
            if not existing.username:
                existing.username = username
                db.commit()
            return
        user = User(
            email=email,
            username=username,
            name="Admin",
            password_hash=hash_password(password),
            role="admin",
            active=True,
            created_by="bootstrap",
        )
        db.add(user)
        db.commit()
        log.info("Bootstrap admin created: %s / %s", username, email)


# ---------------------------------------------------------------------------
# Auth middleware — blocks unauthenticated requests
# ---------------------------------------------------------------------------
OPEN_PATHS    = {"/", "/login", "/api/auth/login", "/api/auth/logout",
                 "/api/health", "/favicon.ico"}
OPEN_PREFIXES = ("/static/", "/assets/", "/api/market/live-prices", "/api/companies/",
                 "/api/bulk/", "/api/screener/", "/api/compare/", "/api/export/", "/api/watchlist/")
# Note: /api/companies/ prefix opens live-quote and other read-only company endpoints
# New APIs (bulk, screener, compare, export, watchlist) are public for platform features


def install(app) -> None:
    """Install auth middleware onto a FastAPI app."""
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import RedirectResponse as SR
    from urllib.parse import quote

    class _AuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            if path in OPEN_PATHS or any(path.startswith(p) for p in OPEN_PREFIXES):
                return await call_next(request)
            token = request.cookies.get(COOKIE)
            if not token or not _decode_token(token):
                return SR(f"/login?next={quote(path)}", status_code=303)
            return await call_next(request)

    app.add_middleware(_AuthMiddleware)
