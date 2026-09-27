# The Aeon ecosystem — what's actually built

This is the current, code-verified picture of the Aeon product family, replacing
the older docs in [`docs/archive/`](docs/archive/), several of which describe a
"4-product suite" that includes a product with no code anywhere in this repo.
Treat this file, not the marketing site's copy, as the source of truth for
what's real — update it here first when that changes.

## The four layers (from the landing page's own framing)

| Layer | Product | What it does | Status | Where | Dev ports |
|---|---|---|---|---|---|
| Foundation | **Aeon Analysis** | Deep single-ticker research: Fusion valuation ensemble, CAPM DCF, Council of Agents, grounded persona commentary, Houston research-ops (brief/gates/notes). "Know WHAT to buy." | **Live** | `analytics/` + `frontend/` | backend :8000, frontend :5173 |
| Event | **Aeon Intelligence** | Event/catalyst countdown dashboard — "Buy the rumor, sell the news" timing matrix. "Know WHEN to trade." | **Live, but demo data**: events are synthetic and regenerate per restart, no ticker filter until this redesign, no auth. | `intelligence/` | backend :8001, frontend :5175 |
| Research | "Aeon Platform" | Filings-to-published-research pipeline. | **Not built.** No directory exists anywhere in this repo. The marketing site currently lists this as `status: 'live'` in `aeonnimbus-landing/src/products.ts` — that's a customer-facing claim about a product that doesn't exist in code and needs a business decision, not a code fix. | — | — |
| Execution | **Aeon Terminal** | Real-time execution/monitoring dashboard prototype. | **WIP** — real hand-written scaffold, not a stub, but incomplete. Distinct from Aeon Analysis's removed in-app "Terminal" launcher tab and from Aeon Analysis's own former internal branding (both since renamed/removed to stop the collision). | `terminal/frontend/` | frontend has no assigned port yet — defaults to Vite's 5173, which **will collide** with Aeon Analysis if both are run at once. Assign it a dedicated port before running alongside Aeon Analysis. |

Separately, **Nipun AI** (root `cli/` + `worker/`) is a real, independently
shipped `npx aeon-ai` single-ticker report CLI running on Cloudflare Workers
(BYOK). It is architecturally disconnected from the four layers above — no
shared code, no shared data — and isn't part of this redesign.

## How the pieces actually connect today

- Aeon Analysis links out to Aeon Intelligence per-ticker (Rumor/News tab,
  Houston brief) — a plain navigation link to Intelligence's own UI at
  `:5175/?ticker=...`, not shared data or a shared backend. Intelligence reads
  that query param to filter/highlight its own (still-synthetic) event feed.
- That's it by design: Intelligence's event data is 100% synthetic and
  regenerates on every backend restart, so fusing it into Aeon Analysis's
  otherwise-real signals would import fake data into a product that has
  worked hard to be honest about what's real vs. approximated (see
  `frontend/src/dataProvenance.ts`). A link that's clearly labeled as opening
  a separate demo view is the honest version of that integration.
- Aeon Analysis's own "Alpha Digest" (new in this redesign) is where its own
  real signals — Fusion, DCF, Council of Agents, personas, Houston gates —
  get synthesized into one per-ticker view. This organically covers the
  "Research Layer" gap that "Aeon Platform" was meant to fill, from inside a
  product that's actually shipped.

## Naming history (for future readers)

Three different things have been called "Terminal" in this repo at various
points: (1) a launcher tab inside Aeon Analysis for the third-party Fincept
terminal — removed; (2) Aeon Analysis's own earlier internal branding
("Aeon Nimbus Terminal") — renamed to "Aeon Nimbus Analysis" to match its
actual header/footer branding; (3) the real, distinct Execution-Layer product
above, which keeps the name "Aeon Terminal." If you find a doc or comment
still saying "Terminal" and meaning Aeon Analysis, it's stale — see
`docs/archive/`.
