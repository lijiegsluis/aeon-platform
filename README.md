# Aeon Nimbus Research — Equity Research Automation Platform

A global equity-research platform: a source-linked coverage universe (any
country, any exchange), an interactive research dashboard, a live **Data
Studio** (search a company → auto-research it from its filing and public
sources → review → recompute → export), and a formula-driven Excel model
generator.

**Live showcase:** set your own static-hosting URL here once deployed (frozen
static snapshot; the Data Studio, add-company and export features run in the
interactive app below).

---

## Run the interactive platform

```bash
pip install -r requirements.txt
./run.sh                 # serves http://127.0.0.1:8100  (PORT=9000 ./run.sh to change)
```

The dashboard + Data Studio are at `/`; API docs at `/docs`.

## Build the static showcase (no server)

```bash
python3 build_platform.py          # writes output/platform/index.html (self-contained)
```

## Tests

```bash
python3 -m pytest -q                # unit + API + model tests
```

---

## What's here

| Path | What it is |
|------|------------|
| `aeon_nimbus/` | The application package |
| &nbsp;&nbsp;`api.py`, `studio_api.py` | FastAPI app + Data Studio endpoints (collect / upload / review / recompute / export, add / delete company, reference search) |
| &nbsp;&nbsp;`ingest.py`, `studio_core.py`, `jobs.py` | Web/file collectors, staging + confidence-threshold merge, background jobs |
| &nbsp;&nbsp;`platform_data.py`, `render.py` | Assembles the coverage payload; renders the dashboard |
| &nbsp;&nbsp;`excel_model.py`, `scenarios.py`, `controls.py` | Formula-driven Excel model, Bull/Base/Bear scenarios, QC controls |
| &nbsp;&nbsp;`templates/platform.html` | The single-file dashboard (Plotly, self-contained) |
| `data/` | `extracted/` (per-company sourced financials), `universe.json` (coverage universe — any market), `reference_universe.json` (searchable directory), macro/market inputs |
| `tests/` | Test suite |
| `build_platform.py`, `run.sh` | Static build + interactive launcher |
| `Dockerfile`, `render.yaml`, `Procfile`, `DEPLOY.md` | Backend deployment |

**Not included in this bundle:** `data/raw_docs/` (≈530 MB of raw filing PDFs — the
source *materials* behind the figures, delivered separately) and the local
`data/platform.db` (runtime state; recreated from the seed on first run).

---

## Data discipline

Every figure traces to a source. Collected/uploaded figures are staged with a
confidence score and only merge into a model above the caller's threshold — and a
lower-confidence web figure never auto-overwrites a higher-confidence (e.g.
audited) one. Nothing unsourced reaches a model; qualitative gaps are marked, not
invented.
