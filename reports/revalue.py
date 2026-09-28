"""Re-run the published valuation under different assumptions.

The workbook publishes one answer. A reader needs to know which assumptions it
turns on, and that means running it again with one of them changed. This
reproduces the workbook's own arithmetic exactly, so a scenario differs from the
published number only by the assumption named.
"""
from __future__ import annotations

SHARES = 40065.4          # the share count the workbook divides by
TAX = 0.30


def _f(v, d=0.0):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else d


# The workbook now carries Ethiopian capital expenditure at the company's
# guidance itself, so these overrides default to OFF. They were needed while the
# model held a flat 26bn the issuer had contradicted; applying them on top of a
# corrected workbook would count the same cash twice. They are kept so the
# report can price what reverting the correction is worth.
ETH_CAPEX_MODEL = 26000.0
ETH_CAPEX_GUIDED = [7500.0, 10000.0, 10000.0, 10000.0, 10000.0]


def eth_capex_relief() -> list[float]:
    """Cash the model withholds by carrying Ethiopian capex above guidance."""
    return [ETH_CAPEX_MODEL - g for g in ETH_CAPEX_GUIDED]


# Applying the capex correction to the discounted leg alone would leave the exit
# leg valuing a company that spent cash the discounted leg says it did not. The
# correction is therefore carried into the FY2031 net debt the exit leg deducts.
# Two assumptions are needed to do that and both are stated in the report:
#   1. Cash not spent reduces net debt one for one, because the model funds any
#      shortfall with a borrowing plug and repays it when cash allows.
#   2. The assets not bought are not depreciated either, which raises taxable
#      profit and therefore tax and the dividend. That leakage is deducted, at
#      the statutory rate and the model's own payout, on a straight-line charge
#      over the 7.50-year life the model uses from FY2027.
ASSET_LIFE_YEARS = 7.50
PAYOUT = 0.80


def eth_capex_net_debt_relief(tax: float = TAX,
                              rel: list[float] | None = None) -> float:
    """Reduction in FY2031 net debt from spending at guidance, after leakage.

    `rel` names the years to price, so a counterfactual that reverts only some
    of them can add back only their share.
    """
    rel = eth_capex_relief() if rel is None else rel
    n = len(rel)
    dep_avoided = sum(rel[i] * (n - i) / ASSET_LIFE_YEARS for i in range(n))
    leakage = dep_avoided * tax + dep_avoided * (1 - tax) * PAYOUT
    return sum(rel) - leakage


def model_of(d: dict) -> dict:
    """The canonical model dict. Every caller must build it through here.

    The lease charge needs the schedules, and a caller that assembled the dict
    by hand without them got a silently different valuation. Missing rows are
    now an error rather than a quiet fallback.
    """
    missing = [k for k in ("val", "is_rows", "cf_rows", "sched_rows") if k not in d]
    if missing:
        raise KeyError("model_of needs " + ", ".join(missing))
    return {"valuation": d["val"], "is_rows": d["is_rows"],
            "cf_rows": d["cf_rows"], "sched_rows": d["sched_rows"]}


def fcff(model: dict, tax: float = TAX, *,
         charge_leases: bool = True, revert_capex: bool = False) -> list[float]:
    """EBIT(1-t) + D&A - capex - intangibles + change in working capital.

    The workbook already carries the Ethiopian capital expenditure correction, so
    this returns the corrected series. `revert_capex` takes it back to the flat
    26bn the model used to hold, which is how the note prices what the guidance is
    worth. There is deliberately no switch that adds the correction ON: one existed,
    the workbook changed underneath it, and it then applied the same relief a second
    time to a series that already had it.

    `charge_leases` deducts new and remeasured leases as capital expenditure.
    Operating profit here is struck after IFRS 16, so right-of-use depreciation
    sits inside the D&A that gets added back. Without charging the additions the
    company earns from a leased site estate it never pays for, while the bridge
    still deducts the lease liability as debt. Under IFRS 16 a new lease IS an
    asset acquisition, so it belongs in capital expenditure; the liability it
    creates is financing and correctly stays out of an unlevered cash flow, with
    only the opening balance deducted in the bridge.
    """
    I, C = model["is_rows"], model["cf_rows"]
    def r(rows, *names):
        for n in names:
            for k, v in rows.items():
                if k.strip().lower() == n.lower():
                    return v
        for n in names:
            for k, v in rows.items():
                if k.strip().lower().startswith(n.lower()):
                    return v
        return [None] * 6
    ebit = r(I, "EBIT")
    # Every non-cash depreciation and amortisation addback on the cash flow, not
    # the first row whose label happens to start with "Depreciation". The cash
    # flow now breaks the charge into four lines, and matching one of them
    # silently understated the add-back by three.
    da_rows = [v for k2, v in C.items()
               if k2.strip().lower().startswith(("depreciation", "amortisation"))]
    if not da_rows:
        da = r(C, "Depreciation, amortisation and ROU depre", "Depreciation")
    else:
        da = [sum(_f(row[i]) for row in da_rows) for i in range(len(da_rows[0]))]
    inv = r(C, "INVESTING CASH FLOW")
    dwc = r(C, "Change in working capital")
    out = []
    for t in range(1, 6):                       # FY2027E..FY2031E
        out.append(_f(ebit[t]) * (1 - tax) + _f(da[t]) + _f(inv[t]) + _f(dwc[t]))
    if revert_capex:
        # Take the workbook's guided Ethiopian capex back to the flat 26bn it
        # used to carry, so the note can price what the guidance is worth.
        out = [v - r for v, r in zip(out, eth_capex_relief())]
    if charge_leases:
        sched = model.get("sched_rows")
        if sched is None:
            raise KeyError(
                "fcff(charge_leases=True) needs sched_rows; build the model "
                "with revalue.model_of(d) so the lease charge cannot be lost")
        adds = None
        for k, v in sched.items():
            if k.strip().lower().startswith("new and remeasured leases"):
                adds = v
                break
        if adds:
            out = [v - abs(_f(adds[i + 1])) for i, v in enumerate(out)]
    return out


# Growing the last forecast free cash flow at g forever assumes the company
# keeps that year's reinvestment rate in perpetuity. In this model that rate is
# NEGATIVE: FY2031 capital expenditure as valued is below depreciation and
# working capital releases cash, so terminal free cash flow comes out ABOVE
# net operating profit. A business cannot grow at 3.5% a year forever while
# disinvesting. The terminal value is therefore built from net operating profit
# and a reinvestment rate implied by g = reinvestment x return on capital, with
# the return anchored on the model's own FY2031 figure. Pass terminal_roic=None
# to reproduce the undisciplined convention.
TERMINAL_ROIC = 0.466


def _terminal(model: dict, cf: list[float], wacc: float, g: float, tax: float,
              roic: float | None) -> float:
    if roic is None or roic <= g:
        return cf[-1] * (1 + g) / max(wacc - g, 1e-6)
    I = model["is_rows"]
    ebit = None
    for k, v in I.items():
        if k.strip().lower() == "ebit":
            ebit = v
            break
    if not ebit:
        return cf[-1] * (1 + g) / max(wacc - g, 1e-6)
    nopat = _f(ebit[-1]) * (1 - tax)
    reinvestment_rate = g / roic
    return nopat * (1 + g) * (1 - reinvestment_rate) / max(wacc - g, 1e-6)


def value(model: dict, *, wacc: float, g: float, ke: float, exit_mult: float,
          peer_pe: float, weights=(0.55, 0.20, 0.25), tax: float = TAX,
          net_debt: float | None = None, nci: float | None = None,
          fcff_override: list[float] | None = None,
          ebitda31: float | None = None, eps27: float | None = None,
          exit_net_debt_addback: float = 0.0,
          terminal_roic: float | None = TERMINAL_ROIC) -> dict:
    V, I = model["valuation"], model["is_rows"]
    def _byprefix(pref):
        for k, v in V.items():
            if k.strip().lower().startswith(pref) and isinstance(v, (int, float)):
                return float(v)
        return 0.0
    nd = _byprefix("less: net debt") if net_debt is None else net_debt
    nc = _byprefix("less: non-controlling") if nci is None else nci
    cf = fcff_override if fcff_override is not None else fcff(model, tax)
    pv = sum(v / ((1 + wacc) ** (i + 1)) for i, v in enumerate(cf))
    tv = _terminal(model, cf, wacc, g, tax, terminal_roic)
    ev = pv + tv / ((1 + wacc) ** len(cf))
    dcf = (ev + nd + nc) / SHARES              # nd and nci are stored negative
    eb31 = ebitda31 if ebitda31 is not None else _f(I["EBITDA"][-1])
    # Applying a capex counterfactual to the discounted leg alone would leave the
    # exit leg valuing a company that spent cash the discounted leg says it did
    # not, so the caller passes the matching FY2031 net-debt movement here. The
    # stored figure already reflects spending at guidance; reverting to the higher
    # run-rate puts that borrowing back, which is a positive add-back.
    nd31 = _f(V.get("Net debt at FY2031")) + exit_net_debt_addback
    nci31 = _f(V.get("NCI at FY2031"))
    # A five-year hold pays five years of dividends before the exit. The workbook's
    # exit leg discounts only the terminal equity and drops them, which is why it
    # returned a lower value than the DCF on a HIGHER multiple than the DCF's own
    # implied exit -- an impossibility that is how the omission was found. The
    # interim leg is discounted at the cost of equity, the same rate as the exit,
    # so a scenario that moves the cost of equity moves both legs together.
    ex_terminal = (exit_mult * eb31 - nd31 - nci31) / SHARES / ((1 + ke) ** 5)
    divs = [abs(_f(x)) for x in (model.get("cf_rows", {}).get("Dividends PAID") or [])
            if isinstance(x, (int, float))]
    ex_interim = sum(v / ((1 + ke) ** (i + 1)) for i, v in enumerate(divs)) / SHARES
    ex = ex_terminal + ex_interim
    # The forecast earnings the peer multiple is applied to. Back-deriving them
    # from the stored per-share value used the SAME cost of equity they are then
    # discounted by, so the (1+ke) cancelled and this leg returned an identical
    # number in every scenario -- a quarter of the target frozen against the one
    # input the rating turns on. Take the earnings from the model instead.
    # Trailing on trailing. The peer multiples are struck on each peer's last
    # reported year, so they are applied to Safaricom's last reported year too and
    # the result is already a value at the valuation date. Applying a trailing
    # multiple to a forecast year and discounting it back mixed the two bases and
    # overstated this leg whenever earnings were growing.
    if eps27 is not None:
        eps_used = eps27
    else:
        eps_used = 0.0
        for k, v in I.items():
            if k.strip().lower().startswith("eps"):
                eps_used = _f(v[0])          # FY2026, the last reported year
                break
    pe = peer_pe * eps_used
    tgt = dcf * weights[0] + ex * weights[1] + pe * weights[2]
    return {"dcf": dcf, "exit": ex, "exit_terminal": ex_terminal,
            "exit_interim": ex_interim, "pe": pe, "target": tgt,
            "ev": ev, "pv_explicit": pv, "terminal": tv}


def base(model: dict) -> dict:
    V = model["valuation"]
    return dict(wacc=_f(V["WACC"]), g=_f(V["Terminal growth"]),
                ke=_f(V["Cost of equity"]),
                exit_mult=_f(V["Exit EV/EBITDA multiple"]),
                peer_pe=_f(V["Peer P/E"]))
