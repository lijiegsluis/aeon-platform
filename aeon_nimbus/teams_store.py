"""Chat history, the change log and large files, kept in Microsoft 365.

A Teams "folder" is SharePoint. The Files tab of a channel is a document
library on the Team's SharePoint site, and that same site holds LISTS, which
are tables with columns, filters and an OData API. So one site gives both
halves of what this needs, through one API, with one app registration:

    chat history    -> a SharePoint list      (a row per message)
    change history  -> a SharePoint list      (a row per change)
    large files     -> the site's document library

Dataverse would also work, and the first version of this used it, but it is a
separate product with its own environment, its own licence and its own
application-user setup in the Power Platform admin centre. None of that is
needed to write a row to a list on a site you already have. Dataverse is still
supported for anyone who has one; see DATAVERSE_URL below.

MIRROR, NOT REPLACE. Every write here happens after the local database write
has already succeeded, and every failure is swallowed and logged. A tenant
outage must not cost a user their answer. Reads still come from the local
database, which is the fast path and works offline. SharePoint is the durable
copy that outlives a Cloud Run container, and the one a human can open, sort
and filter in a browser without asking anybody for a query.

WHAT TO SUPPLY (four values, all from the Azure portal and your browser bar):

    SP_TENANT_ID     Directory (tenant) ID
    SP_CLIENT_ID     Application (client) ID
    SP_CLIENT_SECRET the secret VALUE, not the secret ID
    SP_HOSTNAME      yourcompany.sharepoint.com
    SP_SITE_PATH     /sites/YourTeamSite

The app registration needs the APPLICATION permission `Sites.ReadWrite.All`
with admin consent granted. The lists are created on first use, so nothing has
to be set up by hand inside SharePoint.

Optional:
    SP_CHAT_LIST     default "Hamilcar Chat History"
    SP_CHANGE_LIST   default "Hamilcar Change History"
    SP_FILE_FOLDER   default "Hamilcar"

`check()` proves the configuration by using it and names the first thing that
failed, so it can be verified rather than assumed. Run it from /admin.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
TIMEOUT = 30
CHUNK = 8 * 320 * 1024          # Graph requires a multiple of 320 KiB

_lock = threading.Lock()
_tokens: dict[str, tuple[str, float]] = {}
_ids: dict[str, str] = {}       # resolved site / drive / list ids, cached for the process


# A real environment variable always wins. The .env fallback exists so that
# pasting the five values into .env and pressing "Test connection" works without
# restarting the server, which is how these get configured in practice. Cloud Run
# ships no .env, so there it is a no-op and the injected variables are used.
_ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
_dotenv: dict[str, str] = {}
_dotenv_key: tuple[str, float] = ("", -1.0)


def _env_file() -> str:
    """Resolved per call, so a reloaded module still honours an override."""
    return os.getenv("HAMILCAR_ENV_FILE") or _ENV_FILE


def _dotenv_values() -> dict[str, str]:
    """Parse .env, re-reading it whenever the path or its mtime changes."""
    global _dotenv, _dotenv_key
    path = _env_file()
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return {}
    if (path, stamp) != _dotenv_key:
        vals: dict[str, str] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.removeprefix("export ").strip()
                    val = val.strip()
                    # strip one matching pair of surrounding quotes
                    if len(val) > 1 and val[0] == val[-1] and val[0] in "\"'":
                        val = val[1:-1]
                    vals[key] = val
        except OSError:
            return _dotenv
        with _lock:
            _dotenv, _dotenv_key = vals, (path, stamp)
    return _dotenv


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or _dotenv_values().get(name, "") or default).strip()


def _chat_list() -> str:
    return _env("SP_CHAT_LIST", "Hamilcar Chat History")


def _change_list() -> str:
    return _env("SP_CHANGE_LIST", "Hamilcar Change History")


def enabled() -> bool:
    """True when enough is configured to attempt anything at all."""
    return bool(_env("SP_TENANT_ID") and _env("SP_CLIENT_ID") and _env("SP_CLIENT_SECRET")
                and _env("SP_HOSTNAME") and _env("SP_SITE_PATH"))


# Kept so callers written against the first version keep working. SharePoint
# carries both halves now, so one switch answers for both.
def chat_enabled() -> bool:
    return enabled() or _dataverse_enabled()


def files_enabled() -> bool:
    return enabled()


def _dataverse_enabled() -> bool:
    return bool(_env("DATAVERSE_URL") and _env("DATAVERSE_CHAT_TABLE")
                and _env("TEAMS_TENANT_ID") and _env("TEAMS_CLIENT_ID")
                and _env("TEAMS_CLIENT_SECRET"))


# ---------------------------------------------------------------- auth
def _token(scope: str = GRAPH.rsplit("/", 1)[0] + "/.default",
           *, tenant: str = "", client: str = "", secret: str = "") -> str:
    """A client-credentials token, cached until shortly before it expires."""
    tenant = tenant or _env("SP_TENANT_ID") or _env("TEAMS_TENANT_ID")
    client = client or _env("SP_CLIENT_ID") or _env("TEAMS_CLIENT_ID")
    secret = secret or _env("SP_CLIENT_SECRET") or _env("TEAMS_CLIENT_SECRET")
    key = f"{tenant}|{client}|{scope}"
    with _lock:
        hit = _tokens.get(key)
        if hit and hit[1] > time.time() + 60:
            return hit[0]

    r = requests.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={"grant_type": "client_credentials", "client_id": client,
              "client_secret": secret, "scope": scope},
        timeout=TIMEOUT)
    if r.status_code != 200:
        # The body carries the AADSTS code, which is the only part worth reading.
        raise RuntimeError(f"token request failed {r.status_code}: {r.text[:300]}")
    body = r.json()
    with _lock:
        _tokens[key] = (body["access_token"], time.time() + float(body.get("expires_in", 3600)))
    return body["access_token"]


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}


def _req(method: str, path: str, **kw):
    url = path if path.startswith("http") else GRAPH + path
    kw.setdefault("timeout", TIMEOUT)
    h = dict(_headers())
    h.update(kw.pop("headers", {}) or {})
    return requests.request(method, url, headers=h, **kw)


# ------------------------------------------------------- site, drive, lists
def site_id() -> str:
    if "site" not in _ids:
        r = _req("GET", f"/sites/{_env('SP_HOSTNAME')}:{_env('SP_SITE_PATH')}")
        if r.status_code != 200:
            raise RuntimeError(f"could not resolve the site {r.status_code}: {r.text[:300]}")
        _ids["site"] = r.json()["id"]
    return _ids["site"]


def drive_id() -> str:
    if "drive" not in _ids:
        r = _req("GET", f"/sites/{site_id()}/drive")
        if r.status_code != 200:
            raise RuntimeError(f"could not resolve the document library "
                               f"{r.status_code}: {r.text[:300]}")
        _ids["drive"] = r.json()["id"]
    return _ids["drive"]


# The columns each list carries. Text columns are created explicitly rather
# than left to SharePoint's defaults, because a multi-line answer will not fit
# in a single-line column and Graph rejects the whole row rather than truncating.
_CHAT_COLUMNS = [
    ("UserEmail", {"text": {}}),
    ("Role", {"text": {}}),
    ("Content", {"text": {"allowMultipleLines": True, "textType": "plain"}}),
    ("ToolsUsed", {"text": {"allowMultipleLines": True, "textType": "plain"}}),
    ("At", {"dateTime": {"format": "dateTime"}}),
]
_CHANGE_COLUMNS = [
    ("UserEmail", {"text": {}}),
    ("Action", {"text": {}}),
    ("Company", {"text": {}}),
    ("Field", {"text": {}}),
    ("OldValue", {"text": {"allowMultipleLines": True, "textType": "plain"}}),
    ("NewValue", {"text": {"allowMultipleLines": True, "textType": "plain"}}),
    # Where the change was made, e.g. "Safaricom PLC / Financials / revenue /
    # FY2026". Knowing something changed without knowing where is not much
    # better than not knowing.
    ("Where", {"text": {}}),
    ("Why", {"text": {"allowMultipleLines": True, "textType": "plain"}}),
    ("ViaAssistant", {"text": {}}),
    ("At", {"dateTime": {"format": "dateTime"}}),
]


def list_id(display_name: str, columns: list | None = None) -> str:
    """Find the list by display name, creating it the first time.

    Creating it here rather than asking for it to be made by hand is the
    difference between four values to supply and a setup document.
    """
    key = f"list:{display_name}"
    if key in _ids:
        return _ids[key]
    r = _req("GET", f"/sites/{site_id()}/lists",
             params={"$select": "id,displayName", "$top": "200"})
    if r.status_code != 200:
        raise RuntimeError(f"could not read the site's lists {r.status_code}: {r.text[:300]}")
    for row in r.json().get("value", []):
        if (row.get("displayName") or "").strip().lower() == display_name.strip().lower():
            _ids[key] = row["id"]
            return row["id"]

    body = {"displayName": display_name,
            "list": {"template": "genericList"},
            "columns": [{"name": n, **spec} for n, spec in (columns or [])]}
    r = _req("POST", f"/sites/{site_id()}/lists", json=body,
             headers={"Content-Type": "application/json"})
    if r.status_code not in (200, 201):
        raise RuntimeError(f"could not create the list {display_name!r} "
                           f"{r.status_code}: {r.text[:300]}")
    _ids[key] = r.json()["id"]
    return _ids[key]


def _utc(when: datetime | None) -> str:
    """Graph wants UTC with a Z.

    A naive value is ALREADY UTC here, because that is what the ChatMessage
    column stores. astimezone() on a naive value assumes local time and would
    shift it by the server's offset, which is the trap.
    """
    w = when or datetime.now(timezone.utc)
    w = (w.astimezone(timezone.utc) if w.tzinfo else w).replace(tzinfo=None)
    return w.isoformat(timespec="seconds") + "Z"


def _add_item(list_name: str, columns: list, fields: dict) -> bool:
    try:
        lid = list_id(list_name, columns)
        r = _req("POST", f"/sites/{site_id()}/lists/{lid}/items",
                 json={"fields": fields}, headers={"Content-Type": "application/json"})
        if r.status_code not in (200, 201):
            log.warning("sharepoint write to %s %s: %s", list_name, r.status_code, r.text[:300])
            return False
        return True
    except Exception as exc:                    # never cost the user their answer
        log.warning("sharepoint write to %s failed: %s", list_name, exc)
        return False


# ------------------------------------------------------------ chat history
def save_chat(user_email: str, role: str, content: str,
              tools_used: str = "", ts: datetime | None = None) -> bool:
    """Mirror one chat turn. Returns False on any failure, and never raises."""
    if enabled():
        when = _utc(ts)
        return _add_item(_chat_list(), _CHAT_COLUMNS, {
            "Title": f"{when[:16].replace('T', ' ')} {role} {(user_email or '')[:40]}"[:255],
            "UserEmail": (user_email or "").lower(),
            "Role": role,
            "Content": content or "",
            "ToolsUsed": tools_used or "",
            "At": when,
        })
    if _dataverse_enabled():
        return _dataverse_save_chat(user_email, role, content, tools_used, ts)
    return False


def fetch_chat(user_email: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Read history back out of SharePoint. Verification, and export."""
    if not enabled():
        return _dataverse_fetch_chat(user_email, limit) if _dataverse_enabled() else []
    try:
        lid = list_id(_chat_list(), _CHAT_COLUMNS)
        params = {"$expand": "fields", "$top": str(max(1, min(limit, 5000))),
                  "$orderby": "fields/At desc"}
        if user_email:
            safe = (user_email or "").lower().replace("'", "''")
            params["$filter"] = f"fields/UserEmail eq '{safe}'"
        r = _req("GET", f"/sites/{site_id()}/lists/{lid}/items", params=params,
                 headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"})
        if r.status_code != 200:
            log.warning("sharepoint chat read %s: %s", r.status_code, r.text[:300])
            return []
        out = []
        for row in r.json().get("value", []):
            f = row.get("fields") or {}
            out.append({"id": row.get("id"), "ts": f.get("At"),
                        "user_email": f.get("UserEmail"), "role": f.get("Role"),
                        "content": f.get("Content"), "tools_used": f.get("ToolsUsed")})
        return out
    except Exception as exc:
        log.warning("sharepoint chat read failed: %s", exc)
        return []


# ---------------------------------------------------------- change history
def save_change(user_email: str, action: str, *, company: str = "", field: str = "",
                old_value: Any = "", new_value: Any = "", via_assistant: bool = False,
                where: str = "", why: str = "",
                ts: datetime | None = None) -> bool:
    """Mirror one change to the durable log.

    The analyst asked to be able to backtrack, and to see who made a change and
    whether it came through the assistant. Both are columns here, so the answer
    is a sort in a browser rather than a query somebody has to write.
    """
    if not enabled():
        return False
    when = _utc(ts)
    return _add_item(_change_list(), _CHANGE_COLUMNS, {
        "Title": f"{when[:16].replace('T', ' ')} {action} {company or ''}"[:255],
        "UserEmail": (user_email or "").lower(),
        "Action": action,
        "Where": (where or "")[:255],
        "Why": why or "",
        "Company": company or "",
        "Field": field or "",
        "OldValue": "" if old_value is None else str(old_value)[:8000],
        "NewValue": "" if new_value is None else str(new_value)[:8000],
        "ViaAssistant": "yes" if via_assistant else "no",
        "At": when,
    })


def fetch_changes(company: str = "", limit: int = 500) -> list[dict[str, Any]]:
    if not enabled():
        return []
    try:
        lid = list_id(_change_list(), _CHANGE_COLUMNS)
        params = {"$expand": "fields", "$top": str(max(1, min(limit, 5000))),
                  "$orderby": "fields/At desc"}
        if company:
            safe = company.replace("'", "''")
            params["$filter"] = f"fields/Company eq '{safe}'"
        r = _req("GET", f"/sites/{site_id()}/lists/{lid}/items", params=params,
                 headers={"Prefer": "HonorNonIndexedQueriesWarningMayFailRandomly"})
        if r.status_code != 200:
            log.warning("sharepoint change read %s: %s", r.status_code, r.text[:300])
            return []
        out = []
        for row in r.json().get("value", []):
            f = row.get("fields") or {}
            out.append({"id": row.get("id"), "ts": f.get("At"),
                        "user_email": f.get("UserEmail"), "action": f.get("Action"),
                        "company": f.get("Company"), "field": f.get("Field"),
                        "old_value": f.get("OldValue"), "new_value": f.get("NewValue"),
                        "via_assistant": (f.get("ViaAssistant") == "yes")})
        return out
    except Exception as exc:
        log.warning("sharepoint change read failed: %s", exc)
        return []


# ---------------------------------------------------------------- files
def put_file(name: str, data: bytes, folder: str = "") -> str:
    """Upload bytes to the site's document library. Returns a URL, or "".

    Small files go in one PUT. Anything over 4MB has to go through an upload
    session in 320 KiB multiples, which is a Graph requirement rather than a
    preference. A finished workbook for 500 companies crosses that line, so the
    chunked path is the one that will actually get used.
    """
    if not enabled():
        return ""
    folder = (folder or _env("SP_FILE_FOLDER", "Hamilcar")).strip("/")
    path = f"{folder}/{name}" if folder else name
    try:
        base = f"{GRAPH}/drives/{drive_id()}/root:/{path}"
        if len(data) < 4 * 1024 * 1024:
            r = _req("PUT", f"{base}:/content", data=data, timeout=max(TIMEOUT, 120),
                     headers={"Content-Type": "application/octet-stream"})
            if r.status_code not in (200, 201):
                log.warning("sharepoint upload %s: %s", r.status_code, r.text[:300])
                return ""
            return r.json().get("webUrl", "")

        s = _req("POST", f"{base}:/createUploadSession",
                 json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
                 headers={"Content-Type": "application/json"})
        if s.status_code not in (200, 201):
            log.warning("sharepoint upload session %s: %s", s.status_code, s.text[:300])
            return ""
        url, total = s.json()["uploadUrl"], len(data)
        for start in range(0, total, CHUNK):
            end = min(start + CHUNK, total) - 1
            # The session URL is pre-authorised, so it carries no bearer token.
            r = requests.put(url, data=data[start:end + 1], timeout=max(TIMEOUT, 300),
                             headers={"Content-Length": str(end - start + 1),
                                      "Content-Range": f"bytes {start}-{end}/{total}"})
            if r.status_code not in (200, 201, 202):
                log.warning("sharepoint chunk %s-%s: %s %s",
                            start, end, r.status_code, r.text[:200])
                return ""
            if r.status_code in (200, 201):
                return r.json().get("webUrl", "")
        return ""
    except Exception as exc:
        log.warning("sharepoint upload failed: %s", exc)
        return ""


def list_files(folder: str = "") -> list[dict[str, Any]]:
    if not enabled():
        return []
    folder = (folder or _env("SP_FILE_FOLDER", "Hamilcar")).strip("/")
    try:
        p = f"/drives/{drive_id()}/root:/{folder}:/children" if folder \
            else f"/drives/{drive_id()}/root/children"
        r = _req("GET", p)
        if r.status_code != 200:
            return []
        return [{"name": x.get("name"), "size": x.get("size"),
                 "modified": x.get("lastModifiedDateTime"), "url": x.get("webUrl")}
                for x in r.json().get("value", [])]
    except Exception as exc:
        log.warning("sharepoint listing failed: %s", exc)
        return []


# ------------------------------------------------------- dataverse (optional)
def _dv_url(path: str) -> str:
    return f"{_env('DATAVERSE_URL').rstrip('/')}/api/data/v9.2/{path.lstrip('/')}"


def _dv_headers() -> dict[str, str]:
    tok = _token(_env("DATAVERSE_URL").rstrip("/") + "/.default",
                 tenant=_env("TEAMS_TENANT_ID"), client=_env("TEAMS_CLIENT_ID"),
                 secret=_env("TEAMS_CLIENT_SECRET"))
    return {"Authorization": f"Bearer {tok}", "OData-MaxVersion": "4.0",
            "OData-Version": "4.0", "Accept": "application/json",
            "Content-Type": "application/json"}


def _dv_cols() -> dict[str, str]:
    p = _env("DATAVERSE_COLUMN_PREFIX", "cr")
    return {"user": f"{p}_useremail", "role": f"{p}_role", "content": f"{p}_content",
            "tools": f"{p}_tools", "ts": f"{p}_ts", "name": f"{p}_name"}


def _dataverse_save_chat(user_email, role, content, tools_used, ts) -> bool:
    c, when = _dv_cols(), _utc(ts)
    body = {c["user"]: (user_email or "").lower(), c["role"]: role,
            c["content"]: content or "", c["tools"]: tools_used or "", c["ts"]: when,
            c["name"]: f"{when[:16]} {role} {(user_email or '')[:40]}"[:100]}
    try:
        r = requests.post(_dv_url(_env("DATAVERSE_CHAT_TABLE")), headers=_dv_headers(),
                          json=body, timeout=TIMEOUT)
        if r.status_code not in (200, 201, 204):
            log.warning("dataverse chat write %s: %s", r.status_code, r.text[:300])
            return False
        return True
    except Exception as exc:
        log.warning("dataverse chat write failed: %s", exc)
        return False


def _dataverse_fetch_chat(user_email: str, limit: int) -> list[dict[str, Any]]:
    c = _dv_cols()
    params = {"$top": str(max(1, min(limit, 5000))), "$orderby": f"{c['ts']} desc"}
    if user_email:
        safe = user_email.lower().replace("'", "''")
        params["$filter"] = f"{c['user']} eq '{safe}'"
    try:
        r = requests.get(_dv_url(_env("DATAVERSE_CHAT_TABLE")), headers=_dv_headers(),
                         params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return []
        return [{"ts": x.get(c["ts"]), "user_email": x.get(c["user"]),
                 "role": x.get(c["role"]), "content": x.get(c["content"]),
                 "tools_used": x.get(c["tools"])} for x in r.json().get("value", [])]
    except Exception as exc:
        log.warning("dataverse chat read failed: %s", exc)
        return []


# ---------------------------------------------------------------- diagnosis
def check() -> dict[str, Any]:
    """Prove the configuration by using it, and name what to go and fix.

    Client credentials fail in a handful of specific ways, and the HTTP status
    alone points at the wrong Azure blade for most of them, so each step says
    what it means in the words the portal uses.
    """
    out: dict[str, Any] = {"backend": "sharepoint", "configured": enabled(), "steps": []}

    def step(name, ok, detail="", **extra):
        out["steps"].append({"step": name, "ok": bool(ok), "detail": detail, **extra})

    if not enabled():
        missing = [k for k in ("SP_TENANT_ID", "SP_CLIENT_ID", "SP_CLIENT_SECRET",
                               "SP_HOSTNAME", "SP_SITE_PATH") if not _env(k)]
        step("configuration", False, "not set: " + ", ".join(missing))
        if _dataverse_enabled():
            out["backend"] = "dataverse"
            step("dataverse", True, "configured, and used for chat history")
        out["ok"] = False
        return out

    try:
        _token()
        step("sign in", True, f"tenant {_env('SP_TENANT_ID')[:8]}…")
    except Exception as exc:
        step("sign in", False,
             "AADSTS7000215 is a wrong client secret, and AADSTS700016 is a client id "
             "that does not exist in this tenant. " + str(exc)[:300])
        out["ok"] = False
        return out

    try:
        sid = site_id()
        step("find the site", True, f"{_env('SP_HOSTNAME')}{_env('SP_SITE_PATH')}", site_id=sid)
    except Exception as exc:
        step("find the site", False,
             "403 means Sites.ReadWrite.All was never granted as an APPLICATION "
             "permission, or admin consent was not given. 404 means SP_HOSTNAME or "
             "SP_SITE_PATH does not match the address bar when the site is open. "
             + str(exc)[:300])
        out["ok"] = False
        return out

    try:
        did = drive_id()
        step("find the document library", True, "the Files tab of the channel", drive_id=did)
    except Exception as exc:
        step("find the document library", False, str(exc)[:300])

    for label, name, cols in (("chat history list", _chat_list(), _CHAT_COLUMNS),
                              ("change history list", _change_list(), _CHANGE_COLUMNS)):
        try:
            lid = list_id(name, cols)
            step(label, True, f"{name!r} is ready", list_id=lid)
        except Exception as exc:
            step(label, False, f"{name!r}: {str(exc)[:300]}")

    out["ok"] = all(s["ok"] for s in out["steps"])
    return out


def self_test() -> dict[str, Any]:
    """Write a row and a small file, read them back, and say what happened.

    check() proves the configuration can be reached. This proves it can be
    written to, which is a different permission and the one that usually turns
    out to be missing.
    """
    if not enabled():
        return {"ok": False, "detail": "SharePoint is not configured."}
    stamp = _utc(None)
    wrote_chat = save_chat("selftest@hamilcar", "system",
                           f"connection test written at {stamp}", "self_test")
    wrote_change = save_change("selftest@hamilcar", "connection test",
                               company="(none)", field="(none)",
                               old_value="", new_value=stamp)
    url = put_file(f"connection-test-{stamp[:10]}.txt",
                   f"Written by the Hamilcar platform at {stamp}.\n".encode())
    back = fetch_chat("selftest@hamilcar", limit=1)
    return {"ok": bool(wrote_chat and wrote_change and url and back),
            "chat_row_written": wrote_chat,
            "change_row_written": wrote_change,
            "file_url": url or None,
            "read_back": bool(back),
            "detail": "A row was written to each list, a file was uploaded, and the "
                      "row was read back." if (wrote_chat and wrote_change and url and back)
                      else "Something did not go through; the server log names which call."}
