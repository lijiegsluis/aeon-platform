# Aeon Nimbus editorial style

Reference for whoever authors a company seed's memo fields (`memo.executive_summary`,
`memo.business_model`, etc. in `data/extracted/<slug>.json`) — human or
LLM-assisted ingestion. `report_initiation.py` deliberately **selects and
formats, it does not invent**: every sentence that reaches a report traces back
to a sourced field in the seed. This doc is how that sourced prose should read,
not a license to generate unsourced analysis.

## Voice

- **Institutional, literary but precise.** British restraint. Modelled on the
  register of "We believe / We note / We observe" — never "I think" or "it
  seems".
- **Data before opinion.** State the figure and its source, then the
  implication. Never lead with a view the numbers haven't earned yet.
- **No hedging.** No "may potentially", no "could possibly". If the evidence
  doesn't support a clean claim, say what's uncertain and why — don't blur it
  with qualifiers.
- **No AI-sounding language.** No "in today's fast-paced market", no
  "leveraging synergies", no throat-clearing. Say the thing plainly.

## Structural discipline

- **Every claim sourced.** "No source, no number" — the same standard already
  enforced by the QC checklist in `controls.py`. A gap is marked for analyst
  input, never smoothed over with invented language.
- **Timestamped before the outcome is known.** A rating, once published, is
  not revised to look better in hindsight. If the thesis breaks, that's a
  tracked outcome, not a deleted call.
- **Entry, target, stop, position size, horizon — always together.** This is
  the core promise: a formal call is incomplete without all five. The report
  template (`templates/report.html`) renders `stop_loss`, `position_size_pct`
  and `horizon_months` from the seed's `investment_view` alongside target and
  current price — populate them when initiating coverage with a formal
  position, not just a valuation view.
- **Wins and losses both.** Coverage that goes wrong stays visible in the
  track record, not quietly dropped from the universe.

## Applying this beyond Africa

None of the above is market-specific — the same discipline applies whether
the company trades on the NSE, the LSE, or the NYSE. The only market-specific
inputs are the *numbers* (country risk premium, local WACC, FX), which live in
`country_risk.py` and `config.py`, not in the editorial voice.
