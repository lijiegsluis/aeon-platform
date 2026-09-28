"""Sending the welcome note, when a mailbox is configured.

The note itself has always been composed; what was missing was a way to send
it, so an admin had to copy it into their own mail client. That works, and it
stays the fallback, because a platform that cannot reach a mail server must
still be able to issue an account.

Nothing is sent unless SMTP_HOST and SMTP_PASSWORD are set. A password is never
typed into the app or stored in the database: it goes into Secret Manager the
same way the session secret does, through deploy.sh reading .env.

For Gmail on ithamilcar@gmail.com the values are:

    SMTP_HOST      smtp.gmail.com
    SMTP_PORT      587
    SMTP_USER      ithamilcar@gmail.com
    SMTP_PASSWORD  a 16-character App Password, NOT the account password
    SMTP_FROM      Aeon Nimbus <ithamilcar@gmail.com>     (optional)

The App Password comes from myaccount.google.com/apppasswords and needs
2-Step Verification switched on first. Google rejects the normal account
password over SMTP, which is the usual reason this appears to be misconfigured
when every value looks right.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

log = logging.getLogger(__name__)

TIMEOUT = 20


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def enabled() -> bool:
    return bool(_env("SMTP_HOST") and _env("SMTP_PASSWORD"))


def _from() -> str:
    return _env("SMTP_FROM") or _env("SMTP_USER") or _env("CONTACT_EMAIL", "ithamilcar@gmail.com")


def send(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send one plain-text message. Never raises; says what happened."""
    if not enabled():
        return {"sent": False, "reason": "No mailbox is configured, so the note was not "
                                         "sent. Copy it and send it yourself."}
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = _from(), to, subject
    msg.set_content(body)
    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT", "587") or 587)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx) as sm:
                sm.login(_env("SMTP_USER") or _from(), _env("SMTP_PASSWORD"))
                sm.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=TIMEOUT) as sm:
                sm.starttls(context=ctx)
                sm.login(_env("SMTP_USER") or _from(), _env("SMTP_PASSWORD"))
                sm.send_message(msg)
        return {"sent": True, "to": to, "from": _from()}
    except smtplib.SMTPAuthenticationError as exc:
        # By far the most common failure, and the message Google returns does
        # not say the thing that actually fixes it.
        log.warning("smtp auth failed: %s", exc)
        return {"sent": False, "reason": "The mail server rejected the sign-in. For Gmail "
                                         "this is almost always the account password being "
                                         "used instead of a 16-character App Password."}
    except Exception as exc:
        log.warning("smtp send failed: %s", exc)
        return {"sent": False, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}


def check() -> dict[str, Any]:
    """Connect and sign in without sending anything."""
    if not enabled():
        missing = [k for k in ("SMTP_HOST", "SMTP_PASSWORD") if not _env(k)]
        return {"ok": False, "configured": False,
                "detail": "not set: " + ", ".join(missing)
                          + ". The welcome note is still composed and shown for copying."}
    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT", "587") or 587)
    try:
        ctx = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ctx) as sm:
                sm.login(_env("SMTP_USER") or _from(), _env("SMTP_PASSWORD"))
        else:
            with smtplib.SMTP(host, port, timeout=TIMEOUT) as sm:
                sm.starttls(context=ctx)
                sm.login(_env("SMTP_USER") or _from(), _env("SMTP_PASSWORD"))
        return {"ok": True, "configured": True,
                "detail": f"signed in to {host}:{port} as {_from()}"}
    except smtplib.SMTPAuthenticationError:
        return {"ok": False, "configured": True,
                "detail": "The mail server rejected the sign-in. For Gmail this is almost "
                          "always the account password being used instead of a "
                          "16-character App Password from myaccount.google.com/apppasswords."}
    except Exception as exc:
        return {"ok": False, "configured": True, "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}
