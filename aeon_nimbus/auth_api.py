"""Auth + admin REST endpoints for Aeon Nimbus."""

from __future__ import annotations

import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aeon_nimbus import auth
from aeon_nimbus.db import SessionLocal

router = APIRouter()

TEMPLATES = Path(__file__).parent / "templates"

# ---------------------------------------------------------------------------
# Template helper
# ---------------------------------------------------------------------------

def _page(name: str, **vars) -> str:
    text = (TEMPLATES / name).read_text()
    for k, v in vars.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _db():
    with SessionLocal() as db:
        yield db


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    user = auth.current_user(request)
    if user:
        return RedirectResponse("/", status_code=303)
    return _page("login.html", NEXT=next)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = auth.require_admin(request)
    return _page("admin.html", BRAND="Aeon Nimbus")


# ---------------------------------------------------------------------------
# Auth API
# ---------------------------------------------------------------------------

class LoginIn(BaseModel):
    identifier: str   # username or email
    password: str
    # legacy field alias so old clients sending "email" still work
    email: Optional[str] = None


@router.post("/api/auth/login")
def login(body: LoginIn, response: Response, next: str = "/"):
    identifier = (body.identifier or body.email or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="Username or email required")
    with SessionLocal() as db:
        user = auth.find_user(db, identifier)
        if not user or not auth.verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid username/email or password")
        if not user.active:
            raise HTTPException(status_code=403, detail="Account suspended")
        auth.touch_login(db, user)
        auth.issue_session(response, user)
        return {"ok": True, "next": next or "/"}


@router.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
    return {"ok": True}


@router.get("/api/auth/me")
def me(request: Request):
    user = auth.require_viewer(request)
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


@router.post("/api/auth/change-password")
def change_password(body: ChangePasswordIn, request: Request):
    user = auth.require_viewer(request)
    if not auth.verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password incorrect")
    with SessionLocal() as db:
        u = db.get(auth.User, user.id)
        u.password_hash = auth.hash_password(body.new_password)
        db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin API
# ---------------------------------------------------------------------------

@router.get("/api/admin/users")
def list_users(request: Request):
    auth.require_admin(request)
    with SessionLocal() as db:
        users = db.query(auth.User).order_by(auth.User.created_at).all()
        return {"results": [
            {"id": u.id, "email": u.email, "name": u.name, "role": u.role,
             "active": u.active,
             "created_at": u.created_at.isoformat() if u.created_at else None,
             "created_by": u.created_by,
             "last_login": u.last_login.isoformat() if u.last_login else None}
            for u in users
        ]}


def _gen_password(n: int = 16) -> str:
    alpha = string.ascii_letters + string.digits
    return "".join(secrets.choice(alpha) for _ in range(n))


class CreateUserIn(BaseModel):
    email: str
    name: Optional[str] = ""
    role: str = "viewer"


@router.post("/api/admin/users")
def create_user(body: CreateUserIn, request: Request):
    auth.require_admin(request)
    if body.role not in auth.ROLES:
        raise HTTPException(status_code=400, detail=f"Role must be one of {auth.ROLES}")
    pw = _gen_password()
    with SessionLocal() as db:
        if db.query(auth.User).filter_by(email=body.email.strip().lower()).first():
            raise HTTPException(status_code=409, detail="Email already registered")
        actor = auth.current_user(request)
        u = auth.User(
            email=body.email.strip().lower(),
            name=body.name or "",
            password_hash=auth.hash_password(pw),
            role=body.role,
            active=True,
            created_by=actor.email if actor else "admin",
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        return {
            "user": {"id": u.id, "email": u.email, "name": u.name, "role": u.role},
            "password": pw,
            "welcome": {
                "to": u.email,
                "subject": "Your Aeon Nimbus access",
                "body": (
                    f"Hello{' ' + u.name if u.name else ''},\n\n"
                    f"Your account has been created.\n\n"
                    f"  Email:    {u.email}\n"
                    f"  Password: {pw}\n"
                    f"  Role:     {u.role}\n\n"
                    "Sign in at your platform URL. Change your password after first sign-in.\n\n"
                    "Aeon Nimbus"
                ),
                "sent": False,
                "delivery": "Copy this note and send it yourself.",
            },
        }


@router.post("/api/admin/users/{uid}/role")
def set_role(uid: int, role: str = Query(...), request: Request = None):
    auth.require_admin(request)
    if role not in auth.ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    with SessionLocal() as db:
        u = db.get(auth.User, uid)
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.role = role
        db.commit()
    return {"ok": True}


@router.post("/api/admin/users/{uid}/active")
def set_active(uid: int, active: bool = Query(...), request: Request = None):
    auth.require_admin(request)
    with SessionLocal() as db:
        u = db.get(auth.User, uid)
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.active = active
        db.commit()
    return {"ok": True}


@router.post("/api/admin/users/{uid}/reset-password")
def reset_password(uid: int, request: Request):
    auth.require_admin(request)
    pw = _gen_password()
    with SessionLocal() as db:
        u = db.get(auth.User, uid)
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        u.password_hash = auth.hash_password(pw)
        db.commit()
        return {
            "password": pw,
            "welcome": {
                "to": u.email,
                "subject": "Your Aeon Nimbus password has been reset",
                "body": (
                    f"Hello{' ' + u.name if u.name else ''},\n\n"
                    f"Your password has been reset.\n\n"
                    f"  Email:    {u.email}\n"
                    f"  Password: {pw}\n\n"
                    "Sign in at your platform URL.\n\nAeon Nimbus"
                ),
                "sent": False,
                "delivery": "Copy this note and send it yourself.",
            },
        }


@router.delete("/api/admin/users/{uid}")
def delete_user(uid: int, request: Request):
    auth.require_admin(request)
    with SessionLocal() as db:
        u = db.get(auth.User, uid)
        if not u:
            raise HTTPException(status_code=404, detail="User not found")
        email = u.email
        db.delete(u)
        db.commit()
    return {"ok": True, "note": f"{email} was deleted."}
