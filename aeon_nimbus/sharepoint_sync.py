"""Sync the platform to SharePoint via the Microsoft Graph API.

Uses your organization's SharePoint document library as a shared file store:
pushes the source code to ``Platform_Source_Code`` and one data folder per
company under ``Platform_Files/<Company>/``. Also reads back (``pull``/``list``)
so the library can act as the shared "cloud" copy of the platform's data.

Why Graph (not the browser): a real integration authenticates itself and runs
headless/scheduled. This module needs one thing from you — an Azure AD app
registration (a client_id). See SHAREPOINT.md for the 3-step setup.

Auth — set env vars, pick ONE mode:
  Delegated device-code (you sign in in a browser; nothing secret is stored):
      SP_TENANT_ID   your tenant id (or 'organizations')
      SP_CLIENT_ID   the app registration's Application (client) ID
  App-only (unattended / scheduled; needs admin-consented app permissions):
      SP_TENANT_ID, SP_CLIENT_ID, SP_CLIENT_SECRET

Target (all required — no default tenant/site is assumed; set these via env):
  SP_HOSTNAME   e.g. yourorg.sharepoint.com
  SP_SITE_PATH  e.g. /sites/your-site
  SP_BASE       e.g. "Documents/Automation_Platform"

CLI:
  python -m aeon_nimbus.sharepoint_sync check          # auth + resolve site/drive
  python -m aeon_nimbus.sharepoint_sync push-source    # mirror the source tree -> Platform_Source_Code
  python -m aeon_nimbus.sharepoint_sync push-data       # one folder per company -> Platform_Files
  python -m aeon_nimbus.sharepoint_sync push-all
  python -m aeon_nimbus.sharepoint_sync list [subpath] # read back
  Add --dry-run to any command to print the plan without calling Graph.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
GRAPH = "https://graph.microsoft.com/v1.0"

HOSTNAME = os.environ.get("SP_HOSTNAME", "")
SITE_PATH = os.environ.get("SP_SITE_PATH", "")
BASE = os.environ.get("SP_BASE", "Automation_Platform")

# files/dirs never pushed as "source"
_SKIP_DIRS = {".git", ".netlify", ".pytest_cache", "__pycache__", ".claude", "raw_docs",
              "models", "node_modules", ".idea", ".vscode"}
_SKIP_EXT = {".pyc", ".pyo", ".db"}
_SKIP_NAMES = {".DS_Store"}


# --------------------------------------------------------------------------- auth
def _acquire_token() -> str:
    try:
        import msal
    except ImportError:  # pragma: no cover
        raise SystemExit("Missing dependency: pip install msal")
    tenant = os.environ.get("SP_TENANT_ID")
    client = os.environ.get("SP_CLIENT_ID")
    if not (tenant and client):
        raise SystemExit("Set SP_TENANT_ID and SP_CLIENT_ID (see SHAREPOINT.md).")
    authority = f"https://login.microsoftonline.com/{tenant}"
    secret = os.environ.get("SP_CLIENT_SECRET")
    if secret:  # app-only
        app = msal.ConfidentialClientApplication(client, authority=authority, client_credential=secret)
        res = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    else:  # delegated device-code, cached so you sign in once
        cache = msal.SerializableTokenCache()
        cpath = Path.home() / ".aeon_nimbus_sp_tokencache.json"
        if cpath.exists():
            cache.deserialize(cpath.read_text())
        app = msal.PublicClientApplication(client, authority=authority, token_cache=cache)
        scopes = ["Sites.ReadWrite.All", "Files.ReadWrite.All"]
        accts = app.get_accounts()
        res = app.acquire_token_silent(scopes, account=accts[0]) if accts else None
        if not res:
            flow = app.initiate_device_flow(scopes=scopes)
            if "user_code" not in flow:
                raise SystemExit("Could not start device flow: " + json.dumps(flow, indent=2))
            print("\n" + flow["message"] + "\n", flush=True)   # "go to microsoft.com/devicelogin, enter code…"
            res = app.acquire_token_by_device_flow(flow)
        if cache.has_state_changed:
            cpath.write_text(cache.serialize())
    if "access_token" not in res:
        raise SystemExit("Auth failed: " + res.get("error_description", str(res))[:300])
    return res["access_token"]


class Graph:
    def __init__(self, token: str):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {token}"
        self._site_id: Optional[str] = None
        self._drive_id: Optional[str] = None

    def _req(self, method: str, url: str, **kw):
        r = self.s.request(method, url if url.startswith("http") else GRAPH + url, timeout=60, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {url} -> {r.status_code}: {r.text[:300]}")
        return r

    @property
    def site_id(self) -> str:
        if not HOSTNAME or not SITE_PATH:
            raise RuntimeError("Set SP_HOSTNAME and SP_SITE_PATH (see SHAREPOINT.md) — "
                               "no default tenant is assumed.")
        if self._site_id is None:
            self._site_id = self._req("GET", f"/sites/{HOSTNAME}:{SITE_PATH}").json()["id"]
        return self._site_id

    @property
    def drive_id(self) -> str:
        if self._drive_id is None:
            self._drive_id = self._req("GET", f"/sites/{self.site_id}/drive").json()["id"]
        return self._drive_id

    def _item_path(self, rel: str) -> str:
        rel = rel.strip("/")
        return f"/drives/{self.drive_id}/root:/{rel}" if rel else f"/drives/{self.drive_id}/root"

    def ensure_folder(self, rel: str) -> None:
        """Create each missing segment of a drive-relative folder path."""
        parts, cur = [p for p in rel.strip("/").split("/") if p], ""
        for p in parts:
            parent = self._item_path(cur) + (":" if cur else "")
            try:
                self._req("POST", f"{parent}/children",
                          json={"name": p, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
            except RuntimeError as e:
                if "409" not in str(e) and "nameAlreadyExists" not in str(e):
                    raise
            cur = f"{cur}/{p}" if cur else p

    def upload(self, rel: str, content: bytes) -> None:
        """Upload bytes to a drive-relative path (simple <4 MB, else session)."""
        parent = "/".join(rel.split("/")[:-1])
        if parent:
            self.ensure_folder(parent)
        if len(content) < 4_000_000:
            self._req("PUT", f"{self._item_path(rel)}:/content",
                      headers={"Content-Type": "application/octet-stream"}, data=content)
        else:
            up = self._req("POST", f"{self._item_path(rel)}:/createUploadSession",
                           json={"item": {"@microsoft.graph.conflictBehavior": "replace"}}).json()["uploadUrl"]
            size, chunk, i = len(content), 5 * 1024 * 1024, 0
            while i < size:
                part = content[i:i + chunk]
                hdr = {"Content-Length": str(len(part)),
                       "Content-Range": f"bytes {i}-{i + len(part) - 1}/{size}"}
                requests.put(up, headers=hdr, data=part, timeout=120).raise_for_status()
                i += len(part)

    def list_folder(self, rel: str = "") -> list:
        path = self._item_path(rel) + (":" if rel.strip("/") else "")
        return self._req("GET", f"{path}/children?$select=name,size,folder,file").json().get("value", [])


# --------------------------------------------------------------------------- planning
def _sanitize(name: str) -> str:
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "")
    return name.strip().rstrip(".") or "company"


def _source_files() -> list[tuple[Path, str]]:
    """(local path, drive-relative dest) for every source file to mirror."""
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if p.suffix in _SKIP_EXT or p.name in _SKIP_NAMES:
            continue
        if rel.parts[0] == "output" and rel.parts[1:2] != ("platform",):
            continue  # keep the built showcase, drop other build artefacts
        out.append((p, f"{BASE}/Platform_Source_Code/{rel.as_posix()}"))
    return out


def _companies() -> list[dict]:
    uni = json.loads((ROOT / "data" / "universe.json").read_text())
    cos = uni if isinstance(uni, list) else uni.get("companies", [])
    return [{"name": c.get("name"), "slug": c.get("slug"), "ticker": c.get("ticker")} for c in cos]


def _company_files(co: dict) -> list[tuple[Path, str]]:
    """(local path, drive-relative dest) for one company's data folder."""
    folder = f"{BASE}/Platform_Files/{_sanitize(co['name'])}"
    out = []
    ext = ROOT / "data" / "extracted" / f"{co['slug']}.json"
    if ext.exists():
        out.append((ext, f"{folder}/{co['slug']}.json"))
    model = ROOT / "output" / "models" / f"{co['slug']}_model.xlsx"
    if model.exists():
        out.append((model, f"{folder}/{co['slug']}_model.xlsx"))
    return out


# --------------------------------------------------------------------------- commands
def cmd_check(dry: bool) -> None:
    if dry:
        print(f"Would resolve https://{HOSTNAME}{SITE_PATH} and its default document library.")
        return
    g = Graph(_acquire_token())
    print(f"Authenticated. Site id: {g.site_id[:40]}…  Drive id: {g.drive_id[:40]}…")
    print("Base folder:", BASE)


def cmd_push_source(dry: bool) -> None:
    files = _source_files()
    print(f"Source: {len(files)} files -> {BASE}/Platform_Source_Code/")
    if dry:
        for _, dest in files[:10]:
            print("  ", dest)
        print("   …" if len(files) > 10 else "")
        return
    g = Graph(_acquire_token())
    for i, (p, dest) in enumerate(files, 1):
        g.upload(dest, p.read_bytes())
        if i % 20 == 0 or i == len(files):
            print(f"  uploaded {i}/{len(files)}")


def cmd_push_data(dry: bool) -> None:
    cos = _companies()
    plan = [(co, _company_files(co)) for co in cos]
    total = sum(len(f) for _, f in plan)
    print(f"Data: {len(cos)} companies, {total} files -> {BASE}/Platform_Files/")
    if dry:
        for co, files in plan[:6]:
            print(f"  {_sanitize(co['name'])}/  ({len(files)} files)")
        print("   …" if len(plan) > 6 else "")
        return
    g = Graph(_acquire_token())
    for co, files in plan:
        for p, dest in files:
            g.upload(dest, p.read_bytes())
        print(f"  {_sanitize(co['name'])}  ({len(files)} files)")


def cmd_list(dry: bool, sub: str = "") -> None:
    rel = f"{BASE}/{sub}".strip("/") if sub else BASE
    if dry:
        print("Would list:", rel)
        return
    g = Graph(_acquire_token())
    for it in g.list_folder(rel):
        kind = "dir " if it.get("folder") else "file"
        print(f"  {kind}  {it['name']}")


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    cmd = argv[0] if argv else "check"
    if cmd == "check":
        cmd_check(dry)
    elif cmd == "push-source":
        cmd_push_source(dry)
    elif cmd == "push-data":
        cmd_push_data(dry)
    elif cmd == "push-all":
        cmd_push_source(dry)
        cmd_push_data(dry)
    elif cmd == "list":
        cmd_list(dry, argv[1] if len(argv) > 1 else "")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
