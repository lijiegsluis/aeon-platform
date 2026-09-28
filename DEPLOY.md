# Deploying the Aeon Nimbus interactive backend

This guide deploys the **interactive** platform — the dashboard *plus* the
Data Studio (Collect-from-web, file upload, recalculate, **Excel export**) and live
scenario editing — which needs a running Python/FastAPI server. If you also run a
static showcase build (`build_platform.py`), host it wherever you like (Netlify,
Vercel, GitHub Pages, etc.) and point `LIVE_APP_URL` (below) at this backend.

The app is one self-contained FastAPI process. It **auto-seeds** its coverage
database on startup from `data/extracted/` + `data/universe.json`, needs **no API
keys**, and serves the live dashboard at `/` and the API under `/api`. Verified
booting on `0.0.0.0:$PORT` with a fresh DB.

Artifacts in this repo: `Dockerfile`, `render.yaml` (Render Blueprint), `Procfile`,
`.dockerignore`.

---

## Option A — Render (recommended; free tier, uses your GitHub)

1. **Push this repo to GitHub** (signed in as your own account):
   ```bash
   gh repo create aeon-nimbus --private --source=. --remote=origin --push
   ```

2. On **render.com** → **New → Blueprint** → connect the `aeon-nimbus` repo.
   Render reads `render.yaml` and provisions the service automatically. Deploy.

3. You get a URL like `https://aeon-nimbus.onrender.com`. Open it — the full
   interactive platform.

   *Free tier note:* the service sleeps after ~15 min idle, so the first hit after
   idle cold-starts (~50s). Fine for a demo; upgrade to a paid instance ($7/mo) for
   always-on.

## Option B — Fly.io (always-on, deploys from this folder, no GitHub push)

```bash
brew install flyctl          # or: curl -L https://fly.io/install.sh | sh
fly auth login               # you authorize your Fly account
fly launch --now             # detects the Dockerfile; pick a name/region when asked
```
Fly builds the Dockerfile and deploys to `https://<name>.fly.dev`. A small
always-on machine is a few $/mo. For durable visitor edits add a volume:
```bash
fly volumes create data --size 1
# then in fly.toml: [mounts] source="data" destination="/data"
# and set env DATABASE_URL = "sqlite:////data/platform.db"
```

## Option C — Railway

```bash
npm i -g @railway/cli && railway login && railway up
```
Uses the `Dockerfile`. Set the start port to `$PORT` (already handled).

---

## Data persistence

The DB is SQLite at `data/platform.db`, controlled by the `DATABASE_URL` env var
(the app already reads it — 12-factor). On an ephemeral host it is **re-seeded from
the committed data on every boot**, so the coverage baseline is always intact;
what resets is *visitor* activity (uploads, Collect proposals, scenario edits). For
durability, attach a disk (Fly volume / Render disk) or point `DATABASE_URL` at a
managed Postgres — no code change needed.

## Final step — reveal the "Launch interactive version" button

Once the backend URL is live, set it in `aeon_nimbus/templates/platform.html`:
```js
const LIVE_APP_URL = "https://aeon-nimbus.onrender.com";   // your live URL
```
then rebuild + redeploy the static showcase:
```bash
python3 build_platform.py
netlify deploy --prod --dir output/platform --site <your-netlify-site-id>
```
The gold **Launch interactive version →** button then appears on your static
showcase, linking to the live app. (While `LIVE_APP_URL` is empty the button stays
hidden, so the static site never shows a broken link.)

## Local run

```bash
./run.sh          # http://127.0.0.1:8100  (dashboard + Data Studio + /docs)
```
