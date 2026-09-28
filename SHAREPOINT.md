# SharePoint sync (Microsoft Graph)

Use your organization's SharePoint library as the platform's shared file store:

- **Source code** → `…/Automation_Platform/Platform_Source_Code/` (mirrored tree)
- **Data** → `…/Automation_Platform/Platform_Files/<Company>/` — one folder per
  company, holding its `extracted/<slug>.json` and, if built, its Excel model.

`aeon_nimbus/sharepoint_sync.py` does this over the Microsoft Graph API. It
authenticates *itself*, so it can run on demand or on a schedule — no browser.

> **Honest scope.** SharePoint is file storage, not a query database. This makes
> it an excellent *shared/backup/delivery* store for the code and per-company
> data files, and the app can pull those files back. It is **not** a transactional
> DB for the live app's runtime state (that stays SQLite/Postgres). "Cloud
> database" here means: the firm's single shared copy of the platform + its data.

---

## One-time setup — register an app (≈ 5 min)

You need an Azure AD **Application (client) ID**. I can't create this — it's an
identity in your tenant — but here are the exact steps.

1. **Entra admin center** → *Microsoft Entra ID* → *App registrations* → **New registration**
   - Name: `Aeon Nimbus Platform Sync`; accounts: *this organizational directory only*; no redirect URI.
2. Copy the **Application (client) ID** and **Directory (tenant) ID** from the Overview.
3. **Authentication** → *Advanced settings* → **Allow public client flows** → **Yes**
   (enables device-code sign-in).
4. **API permissions** → *Add a permission* → *Microsoft Graph* → **Delegated** →
   add `Sites.ReadWrite.All` and `Files.ReadWrite.All` → **Grant admin consent**
   (an admin may need to click this).

That's the delegated (you-sign-in) path. For unattended automation instead, add a
**client secret** and grant the same two permissions as **Application** type.

---

## Run it

```bash
pip install -r requirements.txt          # installs msal

export SP_TENANT_ID="<your tenant id>"
export SP_CLIENT_ID="<the app's client id>"
# app-only only: export SP_CLIENT_SECRET="<secret>"

python -m aeon_nimbus.sharepoint_sync check          # sign in, resolve the site
python -m aeon_nimbus.sharepoint_sync push-all       # source + all company data
python -m aeon_nimbus.sharepoint_sync list Platform_Files   # read it back
```

On the first `check`/`push`, delegated mode prints:
> *To sign in, use a web browser to open https://microsoft.com/devicelogin and enter the code XXXX…*

You authenticate with **your own** account (the tool never sees your password); the
token is cached at `~/.aeon_nimbus_sp_tokencache.json` so later runs are silent.

Add `--dry-run` to any command to print exactly what it would upload without
calling Graph.

### Targets (required — no default tenant/site is assumed)

| Env | Example |
|-----|---------|
| `SP_HOSTNAME` | `yourorg.sharepoint.com` |
| `SP_SITE_PATH` | `/sites/your-site` |
| `SP_BASE` | `Documents/Automation_Platform` (defaults to this if unset) |

---

## Scheduling (optional)

With **app-only** auth (client secret + Application permissions), add a cron entry
so the shared copy stays current, e.g. nightly:

```cron
0 2 * * *  cd /path/to/platform && python -m aeon_nimbus.sharepoint_sync push-all >> sync.log 2>&1
```
