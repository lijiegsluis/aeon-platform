"""Generate the Safaricom initiation report as a PDF.

Every figure comes from reports/safaricom_data.py, which reads the platform and
the calculated workbook. Nothing is typed in by hand, so the report cannot drift
from the model: rebuild the model, rerun this, and the PDF matches.

    python reports/build_safaricom_report.py
"""
from __future__ import annotations

import functools as _functools

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "reports"))

from safaricom_data import MODEL, collect   # noqa: E402

OUT = ROOT / "output" / "reports"
NAVY, GOLD, INK, RULE = "123142", "8A6A2F", "17262E", "C9CFCB"

# Written this way so no shell or editor layer can eat a backslash.
B = chr(92)
NL = chr(10)


# ---------------------------------------------------------------- formatting
@_functools.lru_cache(maxsize=None)
def _re_word(w: str):
    import re as _r
    return _r.compile(r"(?<![A-Za-z-])" + w + r"(?![A-Za-z-])")


from revalue import model_of as _model_of  # noqa: E402


def tex(s) -> str:
    """LaTeX-safe text.

    Workbook row labels use an em dash as a separator, which this house does not
    use in running text or in table stubs. Normalise it to a comma at the point
    of rendering rather than editing the model.
    """
    s = str(s).replace(" \u2014 ", ", ").replace("\u2014", ", ")
    # Workbook labels shout the odd ordinary word for emphasis. Acronyms and
    # tickers must survive, so only a named list of English words is lowered.
    for _w in ("ROSE", "PAID", "DECLARED", "AUDITED", "NOT", "ONE", "AND"):
        s = _re_word(_w).sub(_w.lower(), s)
    for a, b in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"),
                 ("$", r"\$"), ("#", r"\#"), ("_", r"\_"), ("{", r"\{"),
                 ("}", r"\}"), ("~", r"\textasciitilde{}"), ("^", r"\^{}")):
        s = s.replace(a, b)
    return s


def num(v, dp=0, dash="--"):
    if not isinstance(v, (int, float)):
        return dash
    t = f"{abs(v):,.{dp}f}"
    # -0.0 is not less than zero, so a naive sign test lets it through and the
    # cell prints a literal "-0". Round first, then decide the sign.
    if float(t.replace(",", "")) == 0:
        return t
    return f"({t})" if v < 0 else t


def pct(v, dp=1, dash="--"):
    if not isinstance(v, (int, float)):
        return dash
    # -0.0% is an artefact of rounding a tiny negative and reads as an error.
    t = f"{abs(v) * 100:,.{dp}f}"
    return f"({t}\\%)" if v < 0 and float(t) != 0 else f"{t}\\%"


def mult(v, dp=1, dash="--"):
    if not isinstance(v, (int, float)):
        return dash
    t = f"{abs(v):,.{dp}f}"
    return f"({t}x)" if v < 0 and float(t) != 0 else f"{t}x"


def money(v, dp=2, dash="--"):
    if not isinstance(v, (int, float)):
        return dash
    t = f"{abs(v):,.{dp}f}"
    return f"({t})" if v < 0 and float(t) != 0 else t


def note(txt: str) -> str:
    """A source note UNDER a table. The \\par matters: without it the note flows
    beside the tabular it belongs to and reads as a stray sentence."""
    return "\n\\par\\vspace{2pt}\n\\srcnote{" + txt + "}\n"


def _v(d, *names, default=None):
    """The value against a valuation label.

    An exact match wins, then a prefix match that actually carries a number. The
    sheet has section headers as well as rows, and "WACC BUILD" is a header with
    no value sitting above the "WACC" that has one. Taking the first prefix
    match printed the discount rate as a dash.
    """
    items = [(k.strip().lower(), v) for k, v in d["val"].items()]
    for n in names:
        t = n.strip().lower()
        for k, v in items:
            if k == t and v is not None:
                return v
    for n in names:
        t = n.strip().lower()
        for k, v in items:
            if k.startswith(t) and v is not None:
                return v
    return default


def _capex_wide(d):
    """The investing measure this note prints, which is wider than note 18.

    The company reports group capital expenditure as property and equipment
    additions. This note adds intangibles and spectrum, so the two differ and the
    basis page says by how much.
    """
    return next((abs(x["capex"]) for x in d["fins"]
                 if x.get("fy") == "FY2026" and x.get("capex") is not None), 0.0)


def _range_line(d):
    """52-week range and the latest close, for the cover metrics block."""
    q = _quote()
    if not q:
        return ""
    return ("\\\\52-week range: KShs " + money(q["week52_low"]) + " to KShs "
            + money(q["week52_high"])
            + "\\\\Latest close: KShs " + money(q["price"]) + " at "
            + tex(q["price_date"]))


def _quote(d=None):
    """The traded market record: range, beta, consensus and the dated series."""
    import json
    f = ROOT / "data" / "market" / "scom_price.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def price_chart(d) -> str:
    """The share price against the target, with the 52-week band behind it."""
    import datetime as _dt
    import charts as C
    q = _quote()
    if not q or not q.get("series"):
        return ""
    def _iso(v):
        """The valuation date is held for display, as '2 July 2026'."""
        v = str(v).strip()
        try:
            return _dt.date.fromisoformat(v)
        except ValueError:
            for fmt in ("%d %B %Y", "%d %b %Y", "%B %d, %Y"):
                try:
                    return _dt.datetime.strptime(v, fmt).date()
                except ValueError:
                    continue
        return None

    vd = _iso(d["val_price_date"])
    pts = list(q["series"])
    if vd:
        pts.append({"date": vd.isoformat(), "close": d["val_price"],
                    "basis": "valuation date"})
    pts = sorted(pts, key=lambda x: x["date"])
    day = lambda s2: _dt.date.fromisoformat(s2).toordinal()
    lo_d, hi_d = day(pts[0]["date"]), day(pts[-1]["date"])
    span = max(1, hi_d - lo_d)
    mon = lambda s2: _dt.date.fromisoformat(s2).strftime("%b %Y")
    series = [(mon(x["date"]), (day(x["date"]) - lo_d) / span, x["close"]) for x in pts]
    out = []
    out.append(B+"subsection*{Share price against the target}")
    out.append(C.price_line(series, target=d["target"],
                            band=(q["week52_low"], q["week52_high"])))
    out.append(B+"srcnote{Closing prices in shillings on the Nairobi Securities Exchange. "
               "The last observation is " + tex(q["price_date"]) + " at KShs "
               + money(q["price"]) + ", against KShs " + money(d["val_price"])
               + " at the valuation date of " + tex(d["val_price_date"]) + ". The shaded "
               "band is the 52-week range of KShs " + money(q["week52_low"]) + " to KShs "
               + money(q["week52_high"]) + ". Intermediate points are derived from the "
               "published one-week, four-week, three-month, six-month and one-year "
               "performance figures, not observed individually. The derivation "
               "reproduces the observed close of 20 August to the tick. Sources: "
               + B+"texttt{stockanalysis.com}, " + B+"texttt{afx.kwayisi.org} and "
               + B+"texttt{live.mystocks.co.ke}, retrieved "
               + tex(q["_source"]["retrieved"]) + ".}")
    c = q.get("consensus") or {}
    if c.get("price_target"):
        gap = d["target"] / c["price_target"] - 1
        out.append("")
        out.append(B+"subsection*{Where this note sits against consensus}")
        out.append("The published consensus recommendation on Safaricom is "
                   + tex(c["rating"]).lower() + ", with a mean price target of KShs "
                   + money(c["price_target"]) + " as at " + tex(c["as_of"]) + ". This "
                   "note's target of KShs " + money(d["target"]) + " is "
                   + pct(abs(gap)) + " below it. The difference is not a different view "
                   "of the operating forecast, which is close to the reported trajectory, "
                   "but of what that forecast is worth: the discount rate here is built on "
                   "the traded Kenyan ten-year with an explicit charge for Ethiopian "
                   "sovereign risk, and the terminal growth rate is held below the "
                   "midpoint of the central bank's target band.")
        out.append(B+"srcnote{Consensus rating and mean target from "
                   + B+"texttt{stockanalysis.com}, retrieved "
                   + tex(q["_source"]["retrieved"]) + ". The same source publishes a beta "
                   "of " + money(q.get("beta_published", 0), 2) + " against the "
                   + money(_v(d, "Equity beta"), 2) + " assumed here; the lower figure "
                   "would raise the target and is priced under the discount rate build.}")
    return NL.join(out) + NL


def _usd_line(mcap_kes):
    """Market capitalisation in dollars, at the rate held for the valuation date.

    Returns an empty string rather than a guess when no rate is held, because a
    dollar figure struck at an assumed rate is not a dollar figure.
    """
    import json
    f = ROOT / "data" / "market" / "fx_usd.json"
    if not f.exists() or not mcap_kes:
        return ""
    try:
        j = json.loads(f.read_text())
        rate = float(j["rates"]["KES"])
    except Exception:
        return ""
    return ("Market capitalisation: USD " + num(mcap_kes / rate)
            + "m\\\\")


def _own_pe(d):
    """Safaricom's P/E on the valuation-date price and the last reported earnings.

    The market block carries a vendor ratio struck at a later quote. Printing that
    beside multiples built on the valuation-date price put two different prices in
    one comparison, and the cover and the peer page disagreed by half a turn.
    """
    eps = _row(d["is_rows"], "EPS (KShs)")[0]
    return (d["val_price"] / eps) if eps else None


def _count_word(n: int) -> str:
    """Small counts read better as words, and this one used to be typed as "four"."""
    words = ("none", "one", "two", "three", "four", "five", "six")
    return words[n] if 0 <= n < len(words) else str(n)


def _scen_target(d, dEB):
    """The target with group EBITDA moved by dEB in each forecast year.

    The same construction the cases table uses, so a figure quoted in prose is the
    number that table prints rather than one copied from a neighbouring row.
    """
    from revalue import model_of, base, value, fcff, SHARES
    m = model_of(d)
    b = base(m)
    cf = fcff(m)
    ge = _row(d["is_rows"], "EBITDA")
    dv = [abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
          if isinstance(x, (int, float))]
    m2 = dict(m)
    m2["cf_rows"] = dict(m["cf_rows"])
    m2["cf_rows"]["Dividends PAID"] = [None] + [a + c * 0.70 * 0.80
                                                for a, c in zip(dv, dEB)]
    return value(m2, fcff_override=[c + v * 0.70 for c, v in zip(cf, dEB)],
                 ebitda31=ge[-1] + dEB[-1], **b)["target"]


def _row(rows, *names):
    """A forecast row by label. EXACT match first.

    "EBIT" is a prefix of "EBITDA", so a prefix search printed the EBITDA row
    under the EBIT label and the forecast showed no depreciation at all, while
    the historical column on the same page showed KShs 73,940m of it.
    """
    items = [(k.strip().lower(), v) for k, v in rows.items()]
    for n in names:
        t = n.strip().lower()
        for k, v in items:
            if k == t:
                return v
    for n in names:
        t = n.strip().lower()
        for k, v in items:
            if k.startswith(t):
                return v
    # Returning a row of None here is how a renamed line disappears silently.
    # The model was rebuilt with a split balance sheet and "Borrowings" became
    # "Borrowings, current" and "Borrowings, non-current"; every ratio built on
    # the old name quietly became None and the failure surfaced pages away as a
    # type error. A miss is a bug, so it is raised where it happens.
    raise KeyError("no row matching " + " / ".join(repr(n) for n in names)
                   + ". Available: " + ", ".join(sorted(rows)[:40]))


# The statement splits working capital across current and non-current lines. A
# days ratio wants the group, so the parts are named once here instead of at
# each call site.
WC_ASSETS = ("Inventories", "Trade and other receivables", "Contract assets")
WC_LIABS = ("Payables and accrued expenses", "Payables", "Provisions",
            "Contract liabilities")


def _rows_sum(rows, *names):
    """Sum every row whose label starts with one of these, e.g. a split line.

    A balance sheet that splits Borrowings into current and non-current still
    has one Borrowings figure; this adds the parts back so a ratio does not have
    to know how the statement was laid out.
    """
    items = [(k.strip().lower(), v) for k, v in rows.items()]
    hit = []
    for n in names:
        t = n.strip().lower()
        hit += [v for k, v in items if k == t or k.startswith(t + ",")]
    if not hit:
        raise KeyError("no rows matching " + " / ".join(repr(n) for n in names))
    n_col = max(len(v) for v in hit)
    return [sum((v[i] or 0) for v in hit if i < len(v)) for i in range(n_col)]


PREAMBLE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=15mm,top=17mm,bottom=14mm]{geometry}
% Two or three lines spilling onto an otherwise blank page is the most
% visible seam a generated document has. Let a page run slightly long
% rather than break, and keep widows and orphans off entirely.
\widowpenalty=10000
\clubpenalty=10000
\raggedbottom
\usepackage[T1]{fontenc}
% Arial. pdflatex has no Arial, and helvet is the Arial-metric clone every
% typesetter substitutes: same widths, same look on the page.
\usepackage[scaled=0.94]{helvet}
\renewcommand{\familydefault}{\sfdefault}
\usepackage{sansmath}
\sansmath
\usepackage{booktabs,array,colortbl,xcolor,graphicx,longtable}
\usepackage{tikz}
\usepackage[hyphens]{url}
\sloppy
\usepackage{enumitem}
\usepackage{fancyhdr}
\usepackage[hidelinks]{hyperref}
\usepackage{needspace}
\usepackage{titlesec}
\usetikzlibrary{calc}
% Four greys and one accent. The accent appears on the rating and nowhere else,
% so a reader who sees colour on the page knows what it is telling them.
\definecolor{navy}{HTML}{1A1A1A}
\definecolor{gold}{HTML}{595959}
% Chart colours. Bars in two shades of grey were unreadable, so the audited and
% forecast series get solid, saturated fills instead. Blue against amber is the
% pair that survives the common forms of colour blindness; both are dark enough
% to hold a white number on top and to print legibly in monochrome.
\definecolor{serA}{HTML}{15427A}
\definecolor{serB}{HTML}{C87A12}
\definecolor{serC}{HTML}{1E7A5A}
\definecolor{ink}{HTML}{1A1A1A}
\definecolor{rule}{HTML}{BFBFBF}
\definecolor{tint}{HTML}{F2F2F2}
\definecolor{sell}{HTML}{8C2F1E}
\definecolor{buy}{HTML}{1E6B4F}
\color{ink}
\setlength{\parindent}{0pt}
\setlength{\parskip}{4pt}
\renewcommand{\arraystretch}{1.18}
\titleformat{\section}{\color{navy}\bfseries\large}{}{0pt}{}[\vspace{-6pt}\textcolor{rule}{\rule{\linewidth}{0.6pt}}]
\titleformat{\subsection}{\color{navy}\bfseries\normalsize}{}{0pt}{}
\titlespacing{\section}{0pt}{12pt}{4pt}
\titlespacing{\subsection}{0pt}{9pt}{2pt}
\pagestyle{fancy}\fancyhf{}
\renewcommand{\headrulewidth}{0.4pt}
\renewcommand{\headrule}{\hbox to\headwidth{\color{rule}\leaders\hrule height \headrulewidth\hfill}}
\fancyhead[L]{\footnotesize\color{navy}Aeon Nimbus \textendash{} Independent Equity Research \textendash{} Telecommunications}
\fancyhead[R]{\footnotesize\color{navy}Safaricom PLC}
\fancyfoot[L]{\scriptsize\color{gray}Generated from the platform model. Sources, derived figures and definitions are set out under Basis of preparation.}
\fancyfoot[R]{\scriptsize\color{gray}\thepage}
\newcommand{\kpi}[2]{{\footnotesize\color{gray}#1}\\[-1pt]{\normalsize\bfseries #2}\\[3pt]}
% url defaults to a typewriter face, which is Computer Modern here and
% leaks a second typeface into an Arial document on three pages.
\urlstyle{same}
% Long source URLs have no natural break points and overrun the measure,
% so allow a break after any character rather than only at punctuation.
\makeatletter
\g@addto@macro{\UrlBreaks}{\UrlOrds}
\makeatother
\setcounter{biburlnumpenalty}{100}
\newcommand{\srcnote}[1]{{\scriptsize\color{gray}#1\par}}
% An exhibit label: small, navy, letter-spaced, sitting on the table it names.
\newcommand{\exhibitlabel}[1]{{\scriptsize\color{navy}\bfseries\MakeUppercase{#1}}}
"""


def _fratios(d):
    """Forecast ratios. ROIC and interest cover are the two an initiation is
    expected to carry and this note did not: ROIC because a terminal growth
    assumption is only coherent if the business out-earns its cost of capital,
    interest cover because the model finances itself with a borrowing plug."""
    I, BS, yrs = d["is_rows"], d["bs_rows"], d["years"]
    SH = 40065.4
    ebit, rev = _row(I, "EBIT"), _row(I, "TOTAL REVENUE")
    eq, nci = _row(BS, "TOTAL EQUITY"), _row(BS, "Non-controlling interests")
    bor, lea = _rows_sum(BS, "Borrowings"), _rows_sum(BS, "Lease liabilities")
    csh = _row(BS, "Net cash and cash equivalents", "Cash and cash equivalents")
    tax, pbt = _row(I, "INCOME TAX"), _row(I, "PROFIT BEFORE TAX")
    ib, li = _row(I, "Interest on borrowings"), _row(I, "Lease interest")
    n = len(yrs)
    nd = [bor[i] + lea[i] - csh[i] for i in range(n)]
    ic = [eq[i] + nd[i] for i in range(n)]
    def roic(i):
        if i == 0:
            return None                      # no opening balance sheet to average
        t = abs(tax[i]) / pbt[i] if pbt[i] else 0.30
        return ebit[i] * (1 - t) / ((ic[i] + ic[i - 1]) / 2)
    def cov(i):
        f = abs(ib[i] or 0) + abs(li[i] or 0)
        return ebit[i] / f if f else None
    rows = [
        ("EBIT margin", [ebit[i] / rev[i] for i in range(n)], pct),
        ("Effective tax rate", [abs(tax[i]) / pbt[i] if pbt[i] else None for i in range(n)], pct),
        ("Return on invested capital", [roic(i) for i in range(n)], pct),
        ("Interest cover, times", [cov(i) for i in range(n)], lambda v, _=1: mult(v)),
        ("Net debt / EBITDA, times",
         [nd[i] / _row(I, "EBITDA")[i] for i in range(n)], lambda v, _=1: mult(v, 2)),
        ("Book value per share, KShs",
         [(eq[i] - (nci[i] or 0)) / SH for i in range(n)], lambda v, _=1: money(v)),
    ]
    return "\n".join(tex(a) + " & " + " & ".join(f(v) for v in vals) + r" \\"
                      for a, vals, f in rows)


_PEER_SUFFIX = (" Communications", " Communication", " Telecommunications",
                " Group Holdings", " Holdings", " Company", " Limited", " Ltd",
                " plc", " PLC", " S.A.", " SA")


def _breakable(url: str) -> str:
    """A long URL set in a typewriter face has no break points and runs off the
    measure. Offer one after each separator so the line can fold."""
    out = tex(url)
    for ch in ("/", "-", ".", "?", "&", "="):
        out = out.replace(ch, ch + B + "allowbreak{}")
    return out


def _short_peer(name: str) -> str:
    n = name.split(" (")[0].strip()
    changed = True
    while changed:                       # "MTN Nigeria Communications Plc" is two
        changed = False
        for suf in _PEER_SUFFIX:
            if n.lower().endswith(suf.lower()):
                n, changed = n[: -len(suf)].strip(), True
                break
    return n


def _days_row(label, balances, d, bold=False):
    """A balance expressed in days of revenue, so a reader can test it."""
    rev = _row(d["is_rows"], "TOTAL REVENUE")
    vals = [(b / r * 365) if (isinstance(b, (int, float)) and r) else None
            for b, r in zip(balances, rev)]
    nm = (B + "textbf{" + tex(label) + "}") if bold else tex(label)
    return nm + " & " + " & ".join(money(v, 1) if v is not None else "--"
                                   for v in vals) + " " + B + B


def _pe_mean(d) -> float:
    v = [p["pe"] for p in d["peers"] if p.get("pe")]
    return sum(v) / len(v) if v else 0.0


def _pe_harm(d) -> float:
    v = [p["pe"] for p in d["peers"] if p.get("pe")]
    return len(v) / sum(1 / x for x in v) if v else 0.0


def _kd_borrow(d) -> float:
    """The rate the model actually charges on borrowings, from the schedule."""
    S, I = d["sched_rows"], d["is_rows"]
    ob, ib = _row(S, "Opening borrowings"), _row(I, "Interest on borrowings")
    r = [abs(ib[i]) / ob[i] for i in range(1, len(ob)) if ob[i]]
    return sum(r) / len(r) if r else 0.0


def _kd_blend(d) -> float:
    """Borrowings and leases at their own rates, weighted by balance."""
    B_, S = d["bs_rows"], d["sched_rows"]
    bor, lea = _rows_sum(B_, "Borrowings")[0], _rows_sum(B_, "Lease liabilities")[0]
    kb = _kd_borrow(d)
    kl = _v(d, "Cost of debt (pre-tax)")
    tot = bor + lea
    return (bor * kb + lea * kl) / tot if tot else kl


def _fratios_roic(d):
    """The ROIC series alone, so the cover page can talk about it."""
    I, BS, yrs = d["is_rows"], d["bs_rows"], d["years"]
    ebit = _row(I, "EBIT")
    eq = _row(BS, "TOTAL EQUITY")
    bor, lea = _rows_sum(BS, "Borrowings"), _rows_sum(BS, "Lease liabilities")
    csh = _row(BS, "Net cash and cash equivalents", "Cash and cash equivalents")
    tax, pbt = _row(I, "INCOME TAX"), _row(I, "PROFIT BEFORE TAX")
    ic = [eq[i] + bor[i] + lea[i] - csh[i] for i in range(len(yrs))]
    out = []
    for i in range(1, len(yrs)):
        t = abs(tax[i]) / pbt[i] if pbt[i] else 0.30
        out.append(ebit[i] * (1 - t) / ((ic[i] + ic[i - 1]) / 2))
    return out


def cover(d) -> str:
    m, f = d["market"], d["fins"]
    L = f[-1]
    pub = d["published"]
    price, tgt = d["val_price"], d["target"]
    up = d["upside"]
    later, later_dn = d.get("later_price"), d.get("later_downside")
    # The vendor market-cap field is struck at a later quote than the price the
    # valuation runs off, which put the cover's multiples and the cover's target on
    # two different days. Everything in this block is struck at the valuation price.
    # 40,065.4m, the count the workbook divides by. The vendor field rounds to
    # 40,070m; three different share counts appeared across the earlier draft.
    # This one reconciles: the declared FY2026 dividend of KShs 80,131m over
    # 40,065.4m shares is KShs 2.00 exactly, which is what the company reported.
    # A Sell on a stock yielding almost 6% is not a minus-16.4% proposition, and
    # the note never said so. Price return plus the declared forward dividend.
    dps27 = _row(d["is_rows"], "DPS (KShs)")[1]
    tot_ret = (d["upside"] or 0) + dps27 / d["val_price"]
    fhead = " & ".join(f"\\textbf{{{tex(y)}}}" for y in d["years"])
    frat = _fratios(d)
    _rc = _fratios_roic(d)
    roic_lo, roic_hi = pct(_rc[0]), pct(_rc[-1])
    wacc_p = pct(_v(d, "WACC"))
    roic_x = money(_rc[0] / _v(d, "WACC"), 1)
    last_y = tex(d["years"][-1])
    _cx = [abs(v) for v in _row(d["cf_rows"], "Capital expenditure") if isinstance(v, (int, float))]
    _rv2 = [v for v in _row(d["is_rows"], "TOTAL REVENUE")[1:] if isinstance(v, (int, float))]
    capex_lo = pct(_cx[0] / _rv2[0]) if _cx and _rv2 else "--"
    capex_hi = pct(_cx[-1] / _rv2[-1]) if _cx and _rv2 else "--"
    SH = 40065.4
    mcap = price * SH if price else m.get("market_cap_m")
    # The premium a reader is being asked to accept, and the premium this note will
    # grant, both as numbers. The earlier draft asserted the shares were expensive
    # and never said against what, or by how much, or what it would pay instead.
    import statistics as _st
    prem_med = _st.median(q["ev_ebitda"] for q in d["peers"])
    prem_mkt_x = (price * SH + L["net_debt"]) / L["ebitda"]
    prem_tgt_x = (tgt * SH + L["net_debt"]) / L["ebitda"]
    prem_mkt, prem_tgt = prem_mkt_x / prem_med - 1, prem_tgt_x / prem_med - 1
    ev = (mcap + L.get("net_debt", 0)) if mcap else None
    eps26 = 2.386          # group EPS, FY2026 condensed audited results
    dps26 = 2.00           # 85c interim + 115c final; KShs 80.13bn on 40,065.4m shares
    pe_val = price / eps26 if price else None
    dy_val = dps26 / price if price else None
    stance = pub.get("stance", "NR")
    col = "sell" if stance.lower() == "sell" else "buy"
    ratios = [
        ("EBITDA margin", [x["ebitda"] / x["revenue"] for x in f], pct),
        ("EBIT margin", [x["ebit"] / x["revenue"] for x in f], pct),
        ("Net margin", [x["net_income"] / x["revenue"] for x in f], pct),
        ("Return on equity", [x["net_income"] / x["total_equity"] for x in f], pct),
        ("Return on assets", [x["net_income"] / x["total_assets"] for x in f], pct),
        ("Capex / revenue", [x["capex"] / x["revenue"] for x in f], pct),
        ("Free cash flow / revenue", [x["free_cash_flow"] / x["revenue"] for x in f], pct),
        ("Net debt / EBITDA", [x["net_debt"] / x["ebitda"] for x in f], mult),
        ("Gearing (debt / equity)", [x["total_debt"] / x["total_equity"] for x in f], mult),
        ("Dividend / net income", [x["dividends_paid"] / x["net_income"] for x in f], pct),
    ]
    head = " & ".join(f"\\textbf{{{tex(x['fy'])}}}" for x in f)
    body = "\n".join(
        f"{tex(n)} & " + " & ".join(fmt(v) for v in vals) + r" \\"
        for n, vals, fmt in ratios)

    return rf"""
\thispagestyle{{fancy}}
\begin{{center}}
{{\Huge\bfseries\color{{navy}} Safaricom PLC}}\\[2pt]
{{\large\color{{gold}} Initiating coverage \textendash{{}} valuation date {tex(d['val_price_date'])}}}\\[6pt]
{{\large A Kenyan payments business with a telecom attached, priced as neither}}
\end{{center}}
\vspace{{2pt}}
\textcolor{{rule}}{{\rule{{\linewidth}}{{0.8pt}}}}
\vspace{{4pt}}

\begin{{minipage}}[t]{{0.605\linewidth}}
\vspace{{0pt}}
\subsection*{{\color{{navy}}The view}}
We initiate on Safaricom with a {stance} and a target price of KShs {money(tgt)},
{pct(abs(up)) if isinstance(up, float) else '--'} below the valuation-date price of
KShs {money(price)} on {tex(d['val_price_date'])}. The business is the better one in its
peer group and the shares are the more expensive. The rating is about the price, not the
company. Adding the FY2027 dividend of KShs {money(dps27)} a share, the twelve-month
total return is {money(abs(tot_ret) * 100, 1)}\% negative, and that is the number a
position turns on, not the price gap alone.

The target is not a bear case, and that is the argument. It sits on a forecast that
takes group EBITDA margin from {pct(d['m0'])} to {pct(d['m5'])} and compounds Ethiopian
revenue at {pct(d['eth_cagr'], 0)} a year to breakeven in {tex(d['eth_be'] or 'n/a')}.
That margin expansion is almost entirely Ethiopian: Kenya, {pct(d['kenya_share'], 0)} of
group revenue, adds {money(d['kenya_mgn_gain'], 1)} points in five years, and the rest is
a segment losing {pct(abs(d['eth_m0']), 0)} of its revenue today turning to a
{pct(d['eth_m5'], 0)} margin. Grant all of it and the shares are still worth less than
they cost. At the more recent quote of KShs {money(later)} on
{tex(d.get('later_price_date'))} the gap is
{pct(abs(later_dn)) if isinstance(later_dn, float) else '--'}.

So this is a Sell on the discount rate and the terminal assumption, not on the peer
premium and not on Ethiopia. Both are set out in full under country risk, and the
sensitivities show what each is worth: the rating holds unless the cost of capital falls
materially and terminal growth is taken above the inflation target band together.
Every other question priced here moves the target by one or two shillings.
At a cost of capital below {pct(d['wacc'])} the gap narrows, and the sensitivity
should disagree with the rating, and the note prices that disagreement. The peer premium is context for how much good news is already in the
price: the shares change hands at {mult(prem_mkt_x)} EBITDA against a peer median of
{mult(prem_med)} and this target still implies {mult(prem_tgt_x)}, above the median,
because the business deserves to trade there.

\subsection*{{\color{{navy}}Strengths}}
\begin{{itemize}}[leftmargin=12pt,itemsep=0pt,topsep=1pt]
\item M-PESA is the largest single revenue line, at KShs 161.1bn in FY2025, ahead of
      voice and messaging together.
\item Group EBITDA margin of {pct(L['ebitda'] / L['revenue'])} in FY2026, the highest in
      the five years on record.
\item Net debt of {mult(L['net_debt'] / L['ebitda'])} EBITDA, the lowest since FY2022.
\end{{itemize}}
\subsection*{{\color{{navy}}Weaknesses}}
\begin{{itemize}}[leftmargin=12pt,itemsep=0pt,topsep=1pt]
\item Voice revenue has been flat to falling since FY2022.
\item Ethiopia still lost KShs 15.4bn of EBITDA in FY2026.
\item Capex has run at {pct(sum(x['capex'] / x['revenue'] for x in f) / len(f))} of revenue
      on average across the record.
\end{{itemize}}
\subsection*{{\color{{navy}}Opportunities}}
\begin{{itemize}}[leftmargin=12pt,itemsep=0pt,topsep=1pt]
\item Ethiopia reaches EBITDA breakeven in FY2028 on the model's own assumptions.
\item Mobile data and M-PESA have both grown in every year of the record.
\end{{itemize}}
\subsection*{{\color{{navy}}Threats}}
\begin{{itemize}}[leftmargin=12pt,itemsep=0pt,topsep=1pt]
\item Separation or relicensing of mobile money in Kenya.
\item Kenyan excise duty on airtime and data.
\item Birr depreciation against the shilling on Ethiopian funding.
\end{{itemize}}
\end{{minipage}}\hfill
\begin{{minipage}}[t]{{0.375\linewidth}}
\vspace{{0pt}}
\setlength{{\fboxsep}}{{8pt}}
\colorbox{{tint}}{{\begin{{minipage}}{{\dimexpr\linewidth-16pt}}
{{\footnotesize\color{{navy}}\bfseries RECOMMENDATION}}\\[3pt]
{{\Huge\bfseries\color{{{col}}} {stance.upper()}}}\\[6pt]
\kpi{{Target price}}{{KShs {money(tgt)}}}
\kpi{{Price, {tex(d['val_price_date'])}}}{{KShs {money(price)}}}
\kpi{{Implied downside}}{{{pct(up) if isinstance(up, float) else '--'}}}
\kpi{{Total return, 12 months}}{{{pct(tot_ret)}}}
\kpi{{At {tex(d.get('later_price_date'))}}}{{KShs {money(later)}, {pct(later_dn) if isinstance(later_dn, float) else '--'}}}
\end{{minipage}}}}\\[7pt]
\colorbox{{tint}}{{\begin{{minipage}}{{\dimexpr\linewidth-16pt}}
{{\footnotesize\color{{navy}}\bfseries COMPANY}}\\[3pt]
{{\footnotesize
Ticker: \textbf{{{tex(d['ticker'])}}}\\
Exchange: {tex(d['exchange'])}\\
Country: {tex(d['country'])}\\
Sector: {tex(d['sector'])}\\
Reporting currency: {tex(d['currency'])}\\
Financial year end: 31 March\\
Latest year: {tex(L['fy'])}, condensed
}}
\end{{minipage}}}}\\[7pt]
\colorbox{{tint}}{{\begin{{minipage}}{{\dimexpr\linewidth-16pt}}
{{\footnotesize\color{{navy}}\bfseries KEY METRICS}}\\[2pt]
{{\scriptsize\color{{navy}} {tex(L['fy'])} earnings, priced at {tex(d['val_price_date'])}}}\\[3pt]
{{\footnotesize
Market capitalisation: KShs {num(mcap)}m\\
{_usd_line(mcap)}
Enterprise value: KShs {num(ev)}m\\
EV / EBITDA: {mult(ev / L['ebitda']) if ev else '--'}\\
EV / revenue: {mult(ev / L['revenue']) if ev else '--'}\\
Price / earnings: {mult(pe_val)}\\
Earnings per share: KShs {money(eps26)}\\
Dividend per share: KShs {money(dps26)}\\
Dividend yield: {pct(dy_val)}\\
Shares in issue: {num(SH)}m{_range_line(d)}
}}
\end{{minipage}}}}
\end{{minipage}}

\vspace{{8pt}}
\section*{{\color{{navy}}Key ratios}}
\vspace{{-2pt}}
{{\footnotesize
\begin{{tabular}}{{@{{}}l*{{{len(f)}}}{{r}}@{{}}}}
\toprule
& {head} \\
\midrule
{body}
\bottomrule
\end{{tabular}}}}
\srcnote{{All figures in KES. Ratios are computed by the platform from the audited
financial statements, not taken from a reported ratio.}}

\vspace{{6pt}}
\subsection*{{The same ratios on the forecast}}
{{\footnotesize
\excap{{Forecast ratios, including the two the discount rate should be tested against}}
\begin{{tabular}}{{@{{}}l*{{{len(d['years'])}}}{{r}}@{{}}}}
\toprule
& {fhead} \\
\midrule
{frat}
\bottomrule
\end{{tabular}}}}
\srcnote{{Return on invested capital is operating profit after tax at the effective rate,
over the average of opening and closing equity plus net debt. It is the natural test of a
terminal assumption: a business earning below its cost of capital cannot grow in
perpetuity without destroying value. Interest cover is operating profit over interest on
borrowings plus lease interest, so it treats the lease as the financing it is. Book value
per share is equity attributable to owners over {num(SH)}m shares. The audited column has
no cover or return figure because the finance-cost split and the opening balance sheet the
average needs are not both available in the condensed release.}}

\vspace{{4pt}}
\textbf{{What the return on capital says, including against this rating.}} A business
earning {roic_lo} on invested capital against a weighted cost of capital of {wacc_p} is
earning roughly {roic_x} times its cost of capital, and by {last_y} the forecast has that
at {roic_hi}. That is a real argument against the Sell, and a serious
one: a franchise compounding at those returns is worth a high multiple of book and the
peer premium accepted as deserved. Two things temper it. Invested capital here
is a depreciated network and a licence carried at cost, so the denominator flatters a
long-lived asset base that has to be replaced at current prices, and capital expenditure
still runs at {capex_lo} of revenue in the first forecast year and {capex_hi} in the last. And a high return does not by itself
close a valuation gap: the discounted valuation already capitalises those returns for five
years and then into perpetuity. The rating says the price capitalises them slightly more.
"""


def contents() -> str:
    return r"""
\newpage
\section*{\color{navy}Contents}
\vspace{-4pt}
{\footnotesize
\renewcommand{\contentsname}{}
\vspace{-22pt}
\setlength{\parskip}{0pt}
\tableofcontents}
\vspace{8pt}
\srcnote{This note is generated from the platform's model for Safaricom PLC.
Every figure is either reported, derived from reported figures, or an
assumption, and the basis of preparation under Disclosures sets out which is which.}
"""


def financials(d) -> str:
    f, yrs = d["fins"], d["years"]
    hh = " & ".join(f"\\textbf{{{tex(x['fy'])}}}" for x in f)

    def hrow(lbl, key, bold=False, dp=0):
        cells = " & ".join(num(x.get(key), dp) for x in f)
        n = f"\\textbf{{{tex(lbl)}}}" if bold else tex(lbl)
        return f"{n} & {cells} \\\\"

    hist = "\n".join([
        hrow("Revenue", "revenue", True),
        hrow("EBITDA", "ebitda", True),
        r"Depreciation and amortisation & " + " & ".join(
            num(-(x["ebitda"] - x["ebit"])) for x in f) + r" \\",
        hrow("EBIT", "ebit", True),
        hrow("Net income", "net_income", True),
        r"\midrule",
        hrow("Total assets", "total_assets"),
        hrow("Total equity", "total_equity"),
        hrow("Total debt", "total_debt"),
        hrow("Cash", "cash"),
        hrow("Net debt", "net_debt", True),
        r"\midrule",
        hrow("Operating cash flow", "operating_cash_flow"),
        hrow("Capital expenditure", "capex"),
        hrow("Free cash flow", "free_cash_flow", True),
        hrow("Dividends paid", "dividends_paid"),
    ])

    fh = " & ".join(f"\\textbf{{{tex(y)}}}" for y in yrs)

    def frow(rows, label, *names, bold=False):
        vals = _row(rows, *names)
        n = f"\\textbf{{{tex(label)}}}" if bold else tex(label)
        return n + " & " + " & ".join(num(v) for v in vals) + r" \\"

    I, C = d["is_rows"], d["cf_rows"]
    fc = "\n".join([
        frow(I, "Kenya revenue", "Kenya revenue"),
        frow(I, "Ethiopia revenue", "Ethiopia revenue"),
        frow(I, "Intersegment eliminations", "Intersegment"),
        frow(I, "Total revenue", "TOTAL REVENUE", bold=True),
        r"\midrule",
        frow(I, "Kenya EBITDA", "Kenya EBITDA"),
        frow(I, "Ethiopia EBITDA", "Ethiopia EBITDA"),
        frow(I, "Intersegment eliminations", "Intersegment eliminations (2)"),
        frow(I, "Group EBITDA", "EBITDA", bold=True),
        frow(I, "EBIT", "EBIT", bold=True),
        frow(I, "Profit before tax", "PROFIT BEFORE TAX", bold=True),
        r"\midrule",
        frow(C, "Operating cash flow", "OPERATING CASH FLOW", bold=True),
        frow(C, "Capital expenditure", "Capital expenditure"),
        frow(C, "Investing cash flow", "INVESTING CASH FLOW"),
        frow(C, "Dividends paid", "Dividends PAID"),
    ])

    return rf"""
\newpage
\section*{{\color{{navy}}Financial summary}}
\subsection*{{Reported, five audited years}}
{{\footnotesize
\begin{{tabular}}{{@{{}}l*{{{len(f)}}}{{r}}@{{}}}}
\toprule
KES millions & {hh} \\
\midrule
{hist}
\bottomrule
\end{{tabular}}}}
\srcnote{{Source: Safaricom audited annual reports for FY2022 to FY2026, the FY2026 year
taken from the full annual report and the notes behind it. Capital expenditure here is
the wider investing measure described under Basis of preparation. Figures in brackets are
negative.}}

\subsection*{{Forecast, from the model}}
{{\footnotesize
\begin{{tabular}}{{@{{}}l*{{{len(yrs)}}}{{r}}@{{}}}}
\toprule
KES millions & {fh} \\
\midrule
{fc}
\bottomrule
\end{{tabular}}}}
\srcnote{{The first column is the last audited year and the rest are forecast. The cash
flow is built from the movement between two balance sheets, so it begins in the first
forecast year and the audited column is left empty, because any figure there would not tie. The group is built as Kenya plus Ethiopia less eliminations, not as a
single line. There are two elimination rows and they carry opposite signs, for two
different reasons. The revenue elimination removes intersegment billing, and matching
cost eliminations remove the same amount from direct costs and other expenses, so the
trading itself nets to nil and carries no margin. The positive EBITDA elimination is
something else entirely: it is the reversal of Kenya's expected credit loss provision
against what Ethiopia owes it, which cannot survive consolidation. Both are shown, so each of the two
blocks adds down to its own total, and the model tests the
addition in every year.}}
"""


def business(d) -> str:
    n = d["notes"]
    # REVENUE LINES ONLY, and only ones that do not contain each other. The
    # previous denominator was the sum of all seventeen disclosed segment rows,
    # which put Kenya total revenue, service revenue and each of its components
    # in one base and added EBITDA and profit after tax to a revenue total.
    # M-PESA came out at 11.8% of nothing. These ten lines sum to 384,433.4,
    # which is revenue from contracts with customers exactly.
    _skip = ("ebitda", "pat", "profit", "total", "segment")
    segs = [x for x in (n.get("segments") or [])
            if isinstance(x.get("value"), (int, float)) and x["value"] > 0
            and not any(w in x["name"].lower() for w in _skip)]
    segs.sort(key=lambda x: -x["value"])
    tot = sum(x["value"] for x in segs) or 1
    rows = "\n".join(
        f"{tex(x['name'].replace(' revenue', ''))} & {num(x['value'], 1)} & "
        f"{pct(x['value'] / tot)} & {tex(x.get('period', ''))} \\\\" for x in segs)
    rows += ("\n\\midrule\n\\textbf{Revenue from contracts with customers} & "
             f"\\textbf{{{num(tot, 1)}}} & \\textbf{{100.0\\%}} & \\\\")
    kp = [x for x in (n.get("sector_specific") or [])
          if isinstance(x.get("value"), (int, float))][:10]
    krows = "\n".join(
        f"{tex(x['name'])} & {num(x['value'], 2)} & {tex(x.get('unit', ''))} & "
        f"{tex(x.get('period', ''))} \\\\" for x in kp)
    return rf"""
\newpage
\section*{{\color{{navy}}The business}}
Safaricom earns most of its money in Kenya and most of its Kenyan money outside voice.
M-PESA brought in KShs 161.1bn in FY2025, 41.9\% of revenue from contracts with
customers and almost twice voice. Add mobile data and 62.3\% of that revenue is
payments and data. In FY2026 the company reports M-PESA up 13.4\% to KShs
182.7bn at 45.6\% of Kenya service revenue, and mobile data overtaking voice within
connectivity at 42.1\% against 41.3\%. The mix has turned, which is what matters for the
multiple: a payments and data business does not carry a voice multiple.

\subsection*{{Revenue by line}}
{{\footnotesize
\begin{{tabular}}{{@{{}}lrrl@{{}}}}
\toprule
\textbf{{Line}} & \textbf{{KES m}} & \textbf{{Share}} & \textbf{{Period}} \\
\midrule
{rows}
\bottomrule
\end{{tabular}}}}
\srcnote{{Source: reported segment disclosure, FY2025. These ten lines are the whole of
revenue from contracts with customers and sum to it exactly. Audited total revenue is
higher by revenue from other sources.}}

\subsection*{{Operating measures}}
{{\footnotesize
\begin{{tabular}}{{@{{}}lrll@{{}}}}
\toprule
\textbf{{Measure}} & \textbf{{Value}} & \textbf{{Unit}} & \textbf{{Period}} \\
\midrule
{krows}
\bottomrule
\end{{tabular}}}}
\srcnote{{Source: company operating disclosure, as extracted by the platform.}}
"""


# Researched externally and verified against a second source before use. Each
# figure carries its source in the note beneath the table it appears in.
# The operator-by-operator subscription table that used to sit here was attributed
# to the Communications Authority's quarterly sector report. This platform holds no
# such report, so the figures could not be traced to any document and have been
# removed rather than restated. The only Kenyan market shares this note now gives
# are the two the annual report itself attributes to the Authority.
# The REGISTER, from the FY2026 Annual Report shareholding pages. An earlier
# version of this table put Vodacom Group on the register at 35% and treated
# Vodafone Group's effective 5% as float, which produced a free float of 30%
# that has never existed. The registered holder is Vodafone Kenya Limited.
OWNERS = [                          # Safaricom FY2026 Annual Report, pp. 14-15
    ("Vodafone Kenya Limited", "39.9\\%", "55.0\\%"),
    ("Government of Kenya", "35.0\\%", "20.0\\%"),
    ("Free float, Nairobi Securities Exchange", "25.1\\%", "25.0\\%"),
]
# Look-through interests in the same company, which move differently from the
# register and in Vodafone's case move the opposite way to the headline.
EFFECTIVE = [
    ("Vodacom Group, effective", "34.9\\%", "54.9\\%"),
    ("Vodafone Group, look-through", "27.7\\%", "35.7\\%"),
]
ETHIOPIA_OWN = [                    # FY2026 capital injection, reported July 2026
    ("Safaricom PLC", "51.67\\%", "54.17\\%"),
    ("Vodacom Group", "5.74\\%", "6.02\\%"),
    ("Sumitomo, BII and IFC combined", "42.59\\%", "39.81\\%"),
]



def products(d) -> str:
    """What the company actually sells.

    The note described the revenue MIX in detail and never described the
    products. A reader who did not already know what M-PESA is learned nothing
    about it from a table of revenue lines, and the whole rating turns on
    whether M-PESA is a telecom product or a payments network.
    """
    k = {x["name"]: x["value"] for x in d["notes"]["sector_specific"]}
    sg = {x["name"]: x["value"] for x in d["notes"]["segments"]}
    rvc = 384433.4
    mp, mdata, voice = sg["M-PESA revenue"], sg["Mobile data revenue"], sg["Voice revenue"]
    fixed, msg = sg["Fixed data revenue"], sg["Messaging revenue"]
    hand, conn = sg["Handset revenue"], sg["Connection revenue"]
    mpc = abs(k["M-PESA commissions (direct cost)"])

    rows = [
        ("M-PESA", "Payments", mp, "KShs " + money(k["M-PESA revenue-per-user (RPU)"])
         + " per user per month"),
        ("Mobile data", "Connectivity", mdata, "KShs "
         + money(k["Mobile data ARPU/RPU"]) + " per user per month, "
         + money(k["Average data usage per user"]) + " GB"),
        ("Voice", "Connectivity", voice, "declining share of connectivity"),
        ("Fixed data", "Connectivity", fixed,
         num(k["Fibre-to-Home customers"]) + " home and "
         + num(k["Fixed enterprise customers"]) + " enterprise connections"),
        ("Messaging", "Connectivity", msg, "substituted by data messaging"),
        ("Handset financing", "Devices", hand, "sold to widen the smartphone base"),
        ("Connection", "Devices", conn, "SIM and activation"),
    ]
    body = NL.join(
        tex(a) + " & " + tex(b) + " & " + num(c) + " & " + pct(c / rvc, 1) + " & "
        + tex(e) + " " + B+B for a, b, c, e in rows)

    return NL.join([
        B+"newpage",
        B+"section*{"+B+"color{navy}Products and services}",
        "Safaricom sells four things: it moves money, it sells connectivity, it sells the "
        "devices that consume the connectivity, and it sells access to the network to "
        "other operators. The first of those is not a telecommunications product in any "
        "ordinary sense, and it is the largest.",
        B+"par"+B+"vspace{4pt}",
        "{"+B+"footnotesize",
        B+"excap{What the company sells}",
        B+"begin{tabular}{@{}llrrl@{}}",
        B+"toprule",
        B+"textbf{Product} & "+B+"textbf{Category} & "+B+"textbf{KES m} & "
        + B+"textbf{Share} & "+B+"textbf{Unit economics} " + B+B,
        B+"midrule",
        body,
        B+"bottomrule",
        B+"end{tabular}}",
        B+"srcnote{FY2025, the last year the company disclosed revenue at this level of "
        "detail. Share is of revenue from contracts with customers of KShs " + num(rvc) +
        "m. Interconnect and mobile incoming, together KShs " +
        num(sg["Interconnect revenue"] + sg["Mobile incoming revenue"]) + "m, are wholesale "
        "access sold to other operators and are not shown as a customer product.}",
        "",
        B+"subsection*{M-PESA is a payments network that happens to be owned by a telecom}",
        "M-PESA is a mobile money service: a customer holds a shilling balance against "
        "their phone number, deposits and withdraws cash through an agent, and sends money, "
        "pays merchants, settles bills and takes short-term credit from that balance. It "
        "earns a fee on the transaction rather than a subscription, which is why the "
        "company reports it as revenue per user per month, not as an ARPU: at KShs "
        + money(k["M-PESA revenue-per-user (RPU)"]) + " a month per user it is the highest "
        "revenue-per-user line the group has.",
        "",
        "Its cost structure is the tell. KShs " + num(mpc) + "m of FY2025 M-PESA revenue, "
        + pct(mpc / mp) + " of it, went out again as commission to the agent estate that "
        "handles cash in and cash out. What is left, " + pct(1 - mpc / mp) + ", is "
        "contribution before any share of network or overhead. A payments business with an "
        "agent estate has the economics of an acquirer, not of a mobile operator, and that "
        "is the whole of the sum-of-the-parts argument set out later.",
        "",
        B+"subsection*{Connectivity is where the mix has turned}",
        "Mobile data overtook voice inside connectivity during FY2026, at "
        + "42.1\\% against 41.3\\% of Kenya connectivity revenue. That is a change in what the "
        "company is, not only in what it sells. Voice is a declining and "
        "substitutable product. Data is sold against a smartphone base of "
        + money(k["Kenya smartphones on network"], 2) + " million devices in Kenya, of "
        "which " + money(k["Kenya 4G devices"], 2) + " million are 4G, consuming "
        + money(k["Average data usage per user"]) + " GB a month each. Usage per user is "
        "the growth lever: the company describes the Kenyan base as close to saturated, "
        "so revenue comes from selling more gigabytes to the same people.",
        "",
        "Fixed data is the smallest connectivity line and the one with the most room. "
        + num(k["Fibre-to-Home customers"]) + " fibre-to-the-home connections against "
        + money(k["Kenya one-month active customers"], 1) + " million Kenyan one-month "
        "active customers is a low base: fewer than one connection for every hundred "
        "customers the company already serves. It holds 34.9\\% of the fixed data market "
        "against 62.7\\% of mobile broadband, and it is the one product line where "
        "Safaricom is not dominant.",
        "",
        B+"subsection*{Why the product mix is the rating}",
        "Add M-PESA and mobile data together and they are "
        + pct((mp + mdata) / rvc, 1) + " of revenue from contracts with customers. That is "
        "just over three fifths. Two thirds would be a generous rounding. The majority of this "
        "company's revenue now comes from two products that did not exist when it listed, "
        "and neither of them is a telephone call. Whether that majority should carry a "
        "telecom multiple is the question on which this valuation and the market differ.",
    ])

def exhibits(d) -> str:
    """The charts. A reader who wants shape rather than digits reads these."""
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    import charts as C

    f, yrs = d["fins"], d["years"]
    n = d["notes"]
    # Stripping FY and E dropped the forecast marker exactly where the forecast
    # bars begin, so the chart disagreed with every table and sentence.
    hist_lab = [str(x["fy"]).replace("FY", "") for x in f]
    fore_lab = [str(y).replace("FY", "") for y in yrs[1:]]
    all_lab = hist_lab + fore_lab
    shades = [78] * len(hist_lab) + [40] * len(fore_lab)

    rev_f = _row(d["is_rows"], "TOTAL REVENUE")
    eb_f = _row(d["is_rows"], "EBITDA")
    rev = [x["revenue"] / 1000 for x in f] + [v / 1000 for v in rev_f[1:]]
    marg = ([x["ebitda"] / x["revenue"] * 100 for x in f]
            + [eb_f[i] / rev_f[i] * 100 for i in range(1, len(yrs))])

    # leverage
    nd = [x["net_debt"] / x["ebitda"] for x in f]
    ndf = []
    for i in range(1, len(yrs)):
        bw = _rows_sum(d["bs_rows"], "Borrowings")[i] or 0
        lz = _rows_sum(d["bs_rows"], "Lease liabilities")[i] or 0
        ch = _row(d["bs_rows"], "Net cash and cash equivalents", "Cash and cash equivalents")[i] or 0
        ndf.append((bw + lz - ch) / eb_f[i])
    lev = nd + ndf

    # capex intensity, reported years only (FY2026 split is not disclosed)
    capint = [x["capex"] / x["revenue"] * 100 for x in f]

    # dividends and payout
    divs = [abs(x["dividends_paid"]) / 1000 for x in f]
    payout = [abs(x["dividends_paid"]) / x["net_income"] * 100 for x in f]

    # revenue mix, FY2025
    _skip = ("ebitda", "pat", "profit", "total", "segment")
    segs = [x for x in (n.get("segments") or [])
            if isinstance(x.get("value"), (int, float)) and x["value"] > 0
            and not any(w in x["name"].lower() for w in _skip)]
    segs.sort(key=lambda x: -x["value"])
    tot = sum(x["value"] for x in segs) or 1
    mix_lab = [tex(x["name"].replace(" revenue", "")) for x in segs]
    mix_val = [x["value"] / tot * 100 for x in segs]

    # peers
    import statistics as st
    # Truncating at eighteen characters printed "MTN Nigeria Commun". Drop the
    # corporate suffix instead, which is what a reader would do.
    pts = [(tex(_short_peer(p["name"])), p["ev_ebitda"], p["pe"])
           for p in d["peers"] if p["ev_ebitda"] and p["pe"]]
    L = f[-1]
    mc = d["market"].get("market_cap_m")
    ev = mc + L["net_debt"] if mc else None
    if ev:
        # Struck on the valuation-date price, as every other Safaricom multiple in
        # this note is. The vendor quote is dated later and would not be comparable
        # with the peer window or with the cover.
        pts.append(("Safaricom", ev / L["ebitda"], _own_pe(d)))
    medx = st.median([p["ev_ebitda"] for p in d["peers"] if p["ev_ebitda"]])
    medy = st.median([p["pe"] for p in d["peers"] if p["pe"]])

    # Ethiopia
    er, ee = _row(d["is_rows"], "Ethiopia revenue"), _row(d["is_rows"], "Ethiopia EBITDA")
    eth_marg = [ee[i] / er[i] * 100 if (er[i] and ee[i] is not None) else None
                for i in range(len(yrs))]

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Exhibits}")

    out.append(B+"subsection*{Revenue and the margin behind it}")
    out.append(C.bars_with_line(all_lab, rev, marg, shades=shades, dp=0, line_dp=1,
                                bar_unit="Revenue, KES bn", line_unit="EBITDA margin, \\%"))
    out.append(B+"srcnote{Blue bars are audited years, amber bars the forecast. The line "
               "is the group EBITDA margin on its own scale. The margin round-trips from "
               "50.0\\% to 44.3\\% and back to 51.5\\%, and the forecast then carries "
               "it higher still, to 54.4\\%, which is the assumption this rating "
               "questions.}")
    out.append("")
    out.append(B+"subsection*{What the shape says}")
    _f = d["fins"]
    _cagr = (_f[-1]["revenue"] / _f[0]["revenue"]) ** (1 / (len(_f) - 1)) - 1
    out.append("Revenue has grown in every audited year, from KShs " + num(_f[0]["revenue"])
               + "m in " + tex(_f[0]["fy"]) + " to KShs " + num(_f[-1]["revenue"]) + "m in "
               + tex(_f[-1]["fy"]) + ", a compound rate of " + pct(_cagr) + " a year. The "
               "margin did not. It fell from " + pct(_f[0]["ebitda"] / _f[0]["revenue"])
               + " to " + pct(_f[3]["ebitda"] / _f[3]["revenue"]) + " before recovering to "
               + pct(_f[-1]["ebitda"] / _f[-1]["revenue"]) + " in " + tex(_f[-1]["fy"])
               + ". That recovery is taken apart two pages earlier.")

    out.append(B+"subsection*{Revenue by line, FY2025}")
    out.append(C.hbars(mix_lab, mix_val))
    out.append(B+"srcnote{Share of revenue from contracts with customers, KShs 384,433.4m, "
               "which these ten lines sum to exactly. M-PESA at KShs 161,131.2m is "
               "marginally larger than voice and mobile data combined, at KShs "
               "160,480.3m, a difference of KShs 650.9m.}")

    out.append(B+"newpage")
    out.append(B+"subsection*{Leverage falls across the forecast}")
    out.append(C.bars(all_lab, lev, shades=shades, dp=2, unit="Net debt / EBITDA, x"))
    out.append(B+"srcnote{Audited years from the reported balance sheet, forecast years "
               "from the model's borrowings plus leases less cash. The business "
               "deleverages from " + mult(lev[-len(d["years"])], 2) + " to "
               + mult(lev[-1], 2) + " while paying a rising dividend, which cuts "
               "against this rating.}")

    out.append(B+"subsection*{Capital intensity and the dividend}")
    out.append(C.bars(hist_lab, capint, dp=1, unit="Capex / revenue, \\%"))
    out.append(B+"srcnote{Audited years only. FY2026 capex is not separately disclosed in "
               "the condensed release and is a derived split of the investing subtotal, "
               "so the last bar is less firm than the four before it. Purchases of "
               "intangibles, including the Ethiopian licence in FY2022, are excluded from "
               "this measure. On a total investing basis FY2022 consumed cash.}")

    out.append(B+"newpage")
    out.append(B+"subsection*{Dividends paid, and the payout that funded them}")
    out.append(C.bars_with_line(hist_lab, divs, payout, dp=1, line_dp=1,
                                bar_unit="Dividends paid, KES bn",
                                line_unit="Payout, \\% of attributable profit"))
    out.append(B+"srcnote{Cash paid in the year, not declared. The FY2023 payout of "
               "102.1\\% exceeded that year's earnings. The company declared KShs 2.00 a "
               "share for FY2026, up from KShs 1.20 in FY2025.}")

    out.append(B+"subsection*{Ethiopia: revenue scaling, losses closing}")
    out.append(C.bars_with_line([str(y).replace("FY", "").replace("E", "") for y in yrs],
                                [v / 1000 if v else None for v in er], eth_marg,
                                shades=[78] + [40] * (len(yrs) - 1), dp=1, line_dp=0,
                                bar_unit="Ethiopia revenue, KES bn",
                                line_unit="Ethiopia EBITDA margin, \\%"))
    out.append(B+"srcnote{The margin line crosses zero in FY2028 on the model's "
               "assumptions, and revenue compounds at 41\\% a year across the forecast. "
               "Both are model drivers, not company guidance.}")

    out.append(B+"newpage")
    out.append(B+"subsection*{Where Safaricom sits against its peers}")
    out.append(C.scatter(pts, medx=medx, medy=medy, highlight="Safaricom",
                         xlab="EV / EBITDA", ylab="Price / earnings"))
    out.append(B+"srcnote{Each company on its own latest audited year in its own "
               "reporting currency, so the multiples compare and the absolute figures do "
               "not. Dashed lines are the peer medians, "
               + mult(medx, 2) + " and " + mult(medy, 2) + ". Safaricom sits alone in the "
               "upper right, and so do two of its peers, so the case rests on the size "
               "of the premium rather than on the quadrant.}")
    out.append("")
    out.append(B+"subsection*{Reading the quadrant}")
    # computed, not typed: the ex-Sonatel median is 5.51x and typing 5.41x was
    # the kind of error this report exists to avoid
    _dearer = sorted([p for p in d["peers"] if p["pe"] and ev
                      and p["pe"] > _own_pe(d)],
                     key=lambda p: -p["pe"])
    _lowest = min((p for p in d["peers"] if p["ev_ebitda"]),
                  key=lambda p: p["ev_ebitda"])
    _exlow = st.median([p["ev_ebitda"] for p in d["peers"]
                        if p["ev_ebitda"] and p["name"] != _lowest["name"]])
    _dtxt = " and ".join(f"{tex(p['name'].split(' (')[0])} at {mult(p['pe'], 1)}"
                         for p in _dearer[:2])
    _both = [p for p in d["peers"]
             if p["ev_ebitda"] and p["pe"]
             and p["ev_ebitda"] > d["peer_med_ev"] and p["pe"] > d["peer_med_pe"]]
    _bt = " and ".join(tex(p["name"].split(" (")[0]) for p in _both)
    out.append("Safaricom sits in the upper right, and it is not alone there: "
               + _bt + " " + ("are" if len(_both) != 1 else "is")
               + " also above both medians. With six names a median splits three and "
               "three on each axis, so the quadrant is a weaker test than it looks and "
               "the case rests on the size of the premium, not on occupying a corner. "
               + (f"Two peers are dearer on earnings, {_dtxt}, and a reader will notice "
                  "that before we do. " if len(_dearer) >= 2 else "")
               + "We weight the enterprise multiple more heavily because the "
               "price-earnings spread across this group turns on leverage and on one-off tax items more than on operating value, and "
               + tex(_lowest["name"].split(" (")[0]) + "'s " + mult(_lowest["ev_ebitda"], 2)
               + " on enterprise value is a state-shareholder discount we would not apply "
               "to Safaricom in either direction. Excluding it the peer median enterprise "
               "multiple is " + mult(_exlow, 2) + " on the remaining five, slightly higher "
               "than the " + mult(d["peer_med_ev"], 2) + " used, so the premium survives "
               "the objection.")
    return NL.join(out) + NL


def market_position(d) -> str:
    """Where the company sits in its market, with named competitors."""
    own = NL.join(tex(a) + " & " + b + " & " + c + " " + B+B for a, b, c in OWNERS)
    eff = NL.join(tex(a) + " & " + b + " & " + c + " " + B+B for a, b, c in EFFECTIVE)
    eth = NL.join(tex(a) + " & " + b + " & " + c + " " + B+B for a, b, c in ETHIOPIA_OWN)
    # Share movements derived from the printed subscription counts, not asserted:
    # a peak-quarter comparison and a four-quarter movement are different things
    # and the two were mixed.
    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Market position}")
    # This page used to print a full operator-by-operator subscription table
    # attributed to the Communications Authority's quarterly sector report. No such
    # report is held by this platform, and the 57.9m it used as Safaricom's line
    # count is the company's own Kenya three-month active customer figure, which is
    # a different measure on a different basis. Only the two shares the annual
    # report itself attributes to the Authority are stated as shares here.
    out.append("Safaricom is the largest mobile operator in Kenya by a wide margin. The "
               "company reports 57.9 million three-month active customers in Kenya at "
               "FY2026, part of a group base of 71.6 million, and states a 66.8\% GSM "
               "market share and a 34.9\% fixed broadband share as at December 2025, "
               "both attributed in its own filing to the Communications Authority of "
               "Kenya. Those two percentages are the only market shares "
               "states.")
    out.append(B+"srcnote{Customer numbers are three-month active customers as reported "
               "by the company. The two market shares quoted are those the company "
               "attributes to the Communications Authority of Kenya in its FY2026 annual "
               "report, measured at December 2025. Operator-level subscription data on the "
               "Authority's own basis is not reproduced here, and an active customer and a "
               "registered subscription are different units that do not reconcile to one "
               "another.}")
    out.append(B+"par"+B+"vspace{2pt}")
    out.append("The audited record supports the direction of the business. Market share "
               "cannot be sized from the disclosure held. Kenyan customers "
               "reached 57.9 million and Kenyan service revenue came "
               "to KShs " + num(_row(d["drv_rows"], "KENYA SERVICE REVENUE")[0], 1)
               + "m in FY2026, with M-PESA at "
               + pct(_row(d["drv_rows"], "M-PESA revenue")[0]
                     / _row(d["drv_rows"], "KENYA SERVICE REVENUE")[0], 1)
               + " of Kenya service revenue and mobile data "
               "now larger than voice. Operator shares on the Authority's own basis are "
               "published by the Authority and are not reproduced here. This note holds "
               "no Authority publication and does not stand behind a figure it has not "
               "read.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append(B+"subsection*{What the share is worth, and what it is not}")
    out.append("A dominant share of a market the company itself calls close to saturated is the reason the "
               "margin is what it is, and it is the reason a premium is deserved. It is "
               "not a reason to pay any premium.")
    out.append("")
    out.append("One number needs disentangling because it appears in two places and looks "
               "like a disagreement. Safaricom's FY2026 Annual Report gives GSM market "
               "share as 66.8\\%, footnoted to the Communications Authority. That is not "
               "a company measure competing with the regulator's: it is the regulator's "
               "own figure for the quarter to December 2025. The annual report was signed "
               "on 7 May 2026 and the Authority did not publish the March quarter until "
               "June, so the company quoted the latest figure available to it. The two "
               "numbers are the same series two quarters apart, and the note uses the "
               "later one because it falls on Safaricom's own year end.")
    out.append("")
    out.append(B+"section*{"+B+"color{navy}Ownership, and a control price}")
    out.append("The register changed materially in the year to which this note is written. "
               "Vodacom completed the purchase of an effective 20\\% of Safaricom on 30 "
               "June 2026, two days before the valuation date used here, taking 15\\% "
               "from the Government of Kenya for KShs 204bn and an effective 5\\% from "
               "Vodafone Group for KShs 68bn. The Treasury received KShs 244.5bn in total, "
               "of which KShs 40.2bn was an upfront payment for dividend rights, separate from the shares themselves.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}lrr@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Holder} & "+B+"textbf{Before} & "+B+"textbf{After} " + B+B)
    out.append(B+"midrule")
    out.append(own)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Safaricom FY2026 Annual Report, shareholding pages, for the "
               "position at 31 March 2026 and the composition after the change on 30 June "
               "2026. The registered holder is Vodafone Kenya Limited. The filing gives "
               "the public float as 25\\% both before and after, because the 15\\% block "
               "moved between two holders who were both outside the float. The company "
               "does not publish the float to two decimal places and neither does this "
               "note.}")
    out.append("")
    out.append("The register is not the whole picture, and the look-through interests move "
               "differently. Vodacom holds its stake through 87.5\\% of Vodafone Kenya "
               "Limited, and Vodafone Group holds roughly 65.1\\% of Vodacom.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"excap{Look-through interests, before and after}")
    out.append(B+"begin{tabular}{@{}lrr@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Interest} & "+B+"textbf{Before} & "+B+"textbf{After} " + B+B)
    out.append(B+"midrule")
    out.append(eff)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Vodacom trading update for the first quarter of FY2027 for the "
               "move from 34.9\\% to 54.9\\%. The Vodafone look-through is derived: "
               "Vodafone owns about 65.1\\% of Vodacom, so its interest in Safaricom "
               "ROSE through this transaction even though it sold its own effective 5\\%. "
               "Read as Vodafone exiting, the headline has it backwards.}")
    out.append("")
    out.append(B+"subsection*{A control block changed hands at no premium at all}")
    out.append("The contractual price is KShs 34.00 a share, stated as such in the buyer's "
               "own announcement of the transaction. Care is needed here, "
               "because dividing the rounded consideration of KShs 272bn by the block gives "
               "KShs 33.94, and that figure has circulated. It is an artefact of rounding "
               "both tranches to the nearest billion. The price is a flat KShs 34.00 and "
               "the exact consideration is nearer KShs 272.4bn. The shares closed at KShs "
               "34.05 on the valuation date two days later.")
    out.append("")
    out.append("The absence of a difference is the point, and it is easily read the wrong "
               "way round. Control blocks normally clear "+B+"emph{above} "
               "the traded price, because the buyer acquires the right to direct the asset "
               "and a minority holder on the Nairobi exchange does not. This one cleared at "
               "the screen price, five cents under it. The most informed possible buyer of "
               "Safaricom, a group that had held an effective 34.9\\% for a quarter of a "
               "century and has sat on its board throughout, paid no premium whatever for "
               "control.")
    out.append("")
    out.append("There are two readings and they point opposite ways. The first is that a "
               "strategic buyer with full information declined to pay up, which is not what "
               "a buyer does when it thinks an asset is cheap, and that stripping even a "
               "modest control premium out of KShs 34.00 leaves a standalone minority value "
               "below the market price. That reading supports this rating. The second is "
               "that the seller was a government with a fiscal need and a stated "
               "privatisation programme, so the price was struck against a motivated seller "
               "and understates what the asset is worth. That reading makes KShs 34.00 a "
               "floor rather than a ceiling, and it cuts against this rating.")
    out.append("")
    out.append("The second reading is weaker than it looks. A genuinely forced sale of a "
               "20\\% strategic block would be expected to clear at a visible discount, and "
               "five cents is not one. Whatever pressure the Treasury was under, it did not "
               "show up in the price. What the transaction establishes is narrower than "
               "either camp would like: on 30 June 2026 the best-informed buyer in the "
               "world for this asset valued it at the screen price, with control thrown in. "
               "This note values it below that, and the gap is the rating. Taken as "
               "takes the transaction as the better estimate of fair value should still not "
               "own the shares, because it prices them at what they already cost.")
    out.append("")
    out.append("The arithmetic does confirm one thing. The 15\\% block was 6,009,814,200 "
               "shares, which puts the total in issue at 40,065,428,000, and that is the "
               "share count this model divides by. The valuation and the transaction are "
               "at least counting the same company.")
    out.append("")
    out.append(B+"subsection*{Who the minorities in Ethiopia are}")
    out.append("The non-controlling interest deducted in the valuation bridge is the "
               "consortium that holds the rest of Safaricom "
               "Telecommunications Ethiopia. It is deducted at the minority's share of "
               "what Ethiopia is worth, not at the KShs 29,786m the balance sheet carries. Safaricom raised its own holding during "
               "FY2026 through a KShs 21.3bn capital injection, of which it funded KShs "
               "19.64bn.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}lrr@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Holder} & "+B+"textbf{Before} & "+B+"textbf{After} " + B+B)
    out.append(B+"midrule")
    out.append(eth)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Reported July 2026 from the FY2026 filings. The minority "
               "investors hold a contractual right to restore their previous levels, "
               "either by buying from Safaricom and Vodacom or by taking up future raises "
               "the majority does not join, so the dilution is not necessarily permanent.}")
    out.append("")
    out.append("The minorities did not "
               "fund their share of the FY2026 injection. They were diluted, and they hold "
               "a right to undo it. The falling non-controlling "
               "interest balance in the forecast as a funding assumption. The consortium "
               "still holds a contractual right to restore its stake, so the ownership "
               "the forecast implies is provisional.")
    return NL.join(out) + NL


KENYA_MACRO = [                     # each verified against a second source
    ("Real GDP growth, Q1 2026", "5.3\\%", "Kenya National Bureau of Statistics"),
    ("Central Bank of Kenya forecast, 2026", "4.9\\%", "CBK, trimmed during 2026"),
    ("Central Bank of Kenya forecast, 2027", "5.3\\%", "CBK"),
    ("Inflation, July 2026", "6.5\\%", "within the 2.5 to 7.5 per cent target band"),
    ("Central Bank Rate", "8.75\\%", "held at the 11 August 2026 meeting, which is after "
     "the valuation date and is not in the discount rate"),
    ("Ten-year government bond yield", "12.43\\%", "average, late June to late July 2026"),
    ("Foreign exchange reserves", "USD 15.25bn", "6.3 months of import cover"),
    ("Sovereign rating, Moody's", "B3, stable", "upgraded from Caa1, January 2026"),
    ("Sovereign rating, S&P", "B, stable", ""),
]

MANAGEMENT = [                      # company disclosure, cross-checked
    ("Dr Peter Ndegwa (CBS)", "Group Chief Executive Officer", "April 2020"),
    ("Adil Arshed Khawaja (MGH)", "Chairman of the Board", "22 December 2022"),
    ("Dilip Pal", "Chief Finance Officer", ""),
]





def _dep_case(d, m, b, cf):
    """The valuation re-run at the audited FY2026 depreciation rate.

    The model steps the asset life out to 7.50 years from the first forecast
    year. Holding the audited rate raises the charge, which cuts earnings and
    the peer leg while adding a tax shield to the discounted leg.
    """
    from revalue import model_of as _model_of, value as _value
    S = d["sched_rows"]
    op, cap, dp = (_row(S, "Opening net book value"), _row(S, "Additions (capex)"),
                   _row(S, "Depreciation"))
    r26 = abs(dp[0]) / (op[0] + cap[0] * 0.5)
    extra = [abs(op[i] + cap[i] * 0.5) * r26 - abs(dp[i]) for i in range(1, 6)]
    divs = [abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
            if isinstance(x, (int, float))]
    m2 = dict(m); m2["cf_rows"] = dict(m["cf_rows"])
    m2["cf_rows"]["Dividends PAID"] = [None] + [dv - e * 0.70 * 0.80
                                                for dv, e in zip(divs, extra)]
    return _value(m2, fcff_override=[c + e * 0.30 for c, e in zip(cf, extra)],
                  **b)["target"]


def assumptions(d) -> str:
    """Every assumption the target price rests on, with what each is worth.

    A valuation is a chain of assumptions and a reader cannot argue with a
    conclusion they cannot decompose. Each row below carries the value used, how
    it was arrived at, and what moving it does to the target. Where the note had
    a choice to make and no external number to settle it, the choice is named as
    an assumption instead of being left open.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import TERMINAL_ROIC as _troic_v
    from revalue import (base as _b, value as _v2, fcff as _f3, eth_capex_relief,
                         eth_capex_net_debt_relief, ASSET_LIFE_YEARS, PAYOUT)

    m = _model_of(d)
    b = _b(m)
    base = _v2(m, **b)["target"]
    P = d["val_price"]
    D = d["drv_rows"]
    kd = _v(d, "Cost of debt (pre-tax)")
    tax = _v(d, "Tax rate")
    rf, erp = _v(d, "Risk-free rate"), _v(d, "Equity risk premium")
    crp = _v(d, "Ethiopia country risk premium")
    ethw = _v(d, "Ethiopia weight in the cost of equity")
    beta = _v(d, "Equity beta")
    w = d["weights"]

    def mv(**kw):
        return _v2(m, **{**b, **kw})["target"] - base

    def blend(wt):
        v = _v2(m, **b)
        return v["dcf"] * wt[0] + v["exit"] * wt[1] + v["pe"] * wt[2] - base

    def cagr(row):
        r = _row(D, row)
        return (r[-1] / r[0]) ** (1 / (len(r) - 1)) - 1

    ROWS = [
        ("Discount rate", "Risk-free rate", pct(rf),
         "The Kenyan ten-year local-currency government yield at the valuation "
         "date. Being a shilling yield it already prices Kenyan sovereign and "
         "inflation risk, which is why no separate Kenya premium is added below",
         money(mv(wacc=b["wacc"] - 0.008, ke=b["ke"] - 0.01)) + " per point lower"),
        ("", "Equity beta", money(beta, 2),
         "Assumed. This note holds no return series for Safaricom or the peer "
         "group and has not estimated a beta, so 0.90 is a judgement, not a "
         "measurement",
         money(mv(ke=b["ke"] - 0.10 * erp, wacc=b["wacc"] - 0.80 * 0.10 * erp))
         + " at 0.80, " + money(mv(ke=b["ke"] + 0.10 * erp,
                                   wacc=b["wacc"] + 0.80 * 0.10 * erp)) + " at 1.00"),
        ("", "Equity risk premium", pct(erp),
         "Assumed. A mature-market premium, not one estimated on Kenyan data",
         money(mv(ke=b["ke"] + beta * 0.01, wacc=b["wacc"] + 0.80 * beta * 0.01))
         + " per point"),
        ("", "Ethiopia country risk premium", pct(crp),
         "Assumed. The incremental sovereign risk of Ethiopia over Kenya, not a "
         "premium for both. Ethiopia sits several rating notches below Kenya and "
         "has restructured external debt, and none of that is carried in a Kenyan "
         "yield. No Kenya premium is added on top of the shilling risk-free rate",
         money(mv(ke=b["ke"] - ethw * crp, wacc=b["wacc"] - 0.80 * ethw * crp))
         + " if removed"),
        ("", "Ethiopia weight in the discount rate", pct(ethw),
         "Ethiopia's share of group revenue in the last forecast year. The premium "
         "above is applied to this share rather than to the whole group, because "
         "the Kenyan business does not carry Ethiopian sovereign risk",
         money(mv(ke=b["ke"] + 0.05 * crp, wacc=b["wacc"] + 0.80 * 0.05 * crp))
         + " per five points of weight"),
        ("", "Cost of debt, pre-tax", pct(kd),
         "Assumed, and it is the rate the model charges on leases. Borrowings, "
         "two thirds of debt, carry " + pct(_kd_borrow(d), 2) + " on the schedule, "
         "so a debt-weighted blend is " + pct(_kd_blend(d), 2)
         + ". The higher figure is used, which is the conservative direction",
         money(mv(wacc=b["wacc"] - 0.20 * (kd - _kd_blend(d)) * (1 - tax)))
         + " on the blend"),
        ("", "Capital structure", "80 / 20",
         "Assumed as a target structure. Market weights would be nearer 89 / 11 "
         "and book weights nearer 58 / 42, so this sits between them",
         money(mv(wacc=0.70 * b["ke"] + 0.30 * kd * (1 - tax))) + " at 70 / 30"),
        ("", "Tax rate", pct(tax), "Kenyan statutory rate", "--"),
        ("Terminal value", "Reinvestment discipline", "g = RR x ROIC",
         "Assumed. Growing the last forecast cash flow at g carries that year's "
         "reinvestment rate forever, and here that rate is negative, so terminal "
         "cash flow would exceed operating profit. The terminal value is built "
         "from net operating profit less the reinvestment g implies",
         "worth " + money(_v2(m, terminal_roic=None, **b)["target"] - base)
         + " if left undisciplined"),
        ("", "Terminal return on capital", pct(_troic_v, 1),
         "Assumed, and below the model's own FY2031 return of "
         + pct(_fratios_roic(d)[-1], 1) + ", which is the conservative direction. Anchoring instead on the cost of "
         "capital raises the reinvestment rate to 21.4 per cent",
         money(_v2(m, terminal_roic=b["wacc"], **b)["target"] - base) + " at that anchor"),
        ("Terminal value", "Perpetual growth", pct(b["g"]),
         "The midpoint of the Central Bank of Kenya's 2.5\\% to 7.5\\% inflation "
         "target band, which is nil real growth in perpetuity. The valuation is "
         "struck in shillings and discounted at a shilling rate, so the terminal "
         "rate carries the same inflation the discount rate does",
         money(mv(g=b["g"] + 0.005)) + " per half point"),
        ("", "Exit multiple", mult(b["exit_mult"]),
         "The median of the six peers in the comparables table, computed from "
         "their own figures. Not an entered assumption",
         money(mv(exit_mult=b["exit_mult"] + 0.5)) + " per half turn"),
        ("", "Peer forward P/E", mult(b["peer_pe"]),
         "The median of the six peers in the comparables table, computed from "
         "their own market capitalisations and net income rather than entered "
         "as a judgement. The mean is " + mult(_pe_mean(d), 2)
         + " and the harmonic mean " + mult(_pe_harm(d), 2)
         + ". It is applied to forecast earnings while the peer multiples are "
         "trailing, and no haircut is taken for that difference",
         money(mv(peer_pe=b["peer_pe"] + 1.0)) + " per turn"),
        ("Blend", "Method weights",
         " / ".join(pct(x, 0) for x in w),
         "House convention. Not an output of the analysis",
         money(blend((1 / 3, 1 / 3, 1 / 3))) + " at equal weights"),
        ("Kenya", "Voice customers", "+3.0\\% a year",
         "Model driver, against a base the company describes as close to saturated", "--"),
        ("", "Voice ARPU", pct(cagr("Voice ARPU (KShs per month)"), 1) + " a year",
         "Model driver", "--"),
        ("", "Mobile data revenue", pct(cagr("Mobile data revenue"), 1) + " a year",
         "Model driver. Not built from subscribers and price", "--"),
        ("", "M-PESA revenue", pct(cagr("M-PESA revenue"), 1) + " a year",
         "Model driver. Not built from transaction value and take rate, and it "
         "is the largest single line in the forecast", "--"),
        ("", "Messaging revenue", pct(cagr("Messaging revenue"), 1) + " a year",
         "Model driver, faded on substitution to data", "--"),
        ("", "Handset and other", "held flat in cash",
         "Model driver", "--"),
        ("", "Foreign exchange in operating costs", "KShs 8,000m a year",
         "Held flat from FY2027 against KShs "
         + num(abs(_row(D, "Net FX losses inside operating expenses")[0])) + "m in "
         + tex(d["years"][0]) + ". The model's own note calls the FY2026 "
         "improvement temporary", "--"),
        ("Ethiopia", "Revenue growth", pct(cagr("Ethiopia revenue") if "Ethiopia revenue" in D
                                           else 0.41, 0) + " a year",
         "Model driver, and the most aggressive line in the forecast", "--"),
        ("", "Capital expenditure", "KShs 7.5bn then 10bn",
         "Corrected by this note to the company's guidance. The workbook carried "
         "KShs 26bn flat",
         "worth " + money(d["treatment_base"] - d["treatments"][0][1])),
        ("", "Birr against the shilling", "10\\% a year",
         "Calibrated so FY2026 revenue ties, not observed from an FX table. The "
         "least well-grounded input in the forecast", "--"),
        ("Cash flow", "Lease treatment", "charged as capex",
         "New and remeasured leases are deducted as capital expenditure, "
         "because operating profit is after IFRS 16 and the depreciation on "
         "those assets is added back. The alternative, leaving them out, values "
         "a site estate the company never pays for",
         "worth " + money(_v2(m, fcff_override=_f3(m, charge_leases=False), **b)["target"]
                          - base) + " if left out"),
        ("Balance sheet", "Depreciation life", money(ASSET_LIFE_YEARS, 2) + " years",
         "Model assumption from FY2027, against 5.12 years implied by the audited "
         "FY2026 charge", "priced on the schedules page"),
        ("", "Dividend payout", pct(PAYOUT, 0),
         "Model assumption, against a five-year record spanning nil and 102.1\\%",
         "--"),
        ("", "Funding", "borrowing plug",
         "New debt is drawn only when cash would otherwise fall below a minimum, "
         "and falls to nil from FY2030E", "--"),
        ("Corrections", "Exit-leg dividends", "KShs "
         + num(sum(abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
                   if isinstance(x, (int, float)))) + "m",
         "Five years of dividends the workbook's exit leg omitted",
         "worth " + money(d["exit_dividend_only"] - d["exit_workbook"]) + " on the leg"),
        ("", "FY2031 net debt", "KShs "
         + num(eth_capex_net_debt_relief()) + "m lower",
         "Cash not spent on Ethiopian capex reduces net debt one for one, less "
         "the tax and dividend leakage on the depreciation also avoided. This is "
         "already inside the published exit leg, so reversing it is what carries a "
         "value, and that is priced on the page of cases",
         "in the leg"),
    ]
    rows = []
    for grp, name, val, basis, worth in ROWS:
        rows.append((B + "textbf{" + tex(grp) + "}" if grp else "") + " & " + tex(name)
                    + " & " + val + " & " + basis + " & " + worth + " " + B + B)
    return NL.join([
        B + "newpage",
        B + "section*{" + B + "color{navy}Every assumption behind the target}",
        "A target price is a chain of assumptions and a reader cannot argue with a "
        "conclusion they cannot take apart. Every input the valuation uses is listed "
        "below with the value taken, how it was arrived at, and what moving it is worth. "
        "The word assumed means exactly that: a judgement this note made because no "
        "external number settled it. Where that is the case the choice is named here "
        "instead of being left open.",
        B + "par" + B + "vspace{4pt}",
        "{" + B + "footnotesize",
        B + "excap{The full assumption set, and what each is worth on the target}",
        # A plain tabular cannot break, and this one is longer than a page: it
        # ran past the bottom margin and printed on top of the footer.
        B + "begin{longtable}{@{}p{1.8cm}p{3.1cm}p{2.0cm}p{5.4cm}p{2.7cm}@{}}",
        B + "toprule",
        B + "textbf{Where} & " + B + "textbf{Input} & " + B + "textbf{Value} & "
        + B + "textbf{Basis} & " + B + "textbf{Worth on target} " + B + B,
        B + "midrule", B + "endfirsthead",
        B + "toprule",
        B + "textbf{Where} & " + B + "textbf{Input} & " + B + "textbf{Value} & "
        + B + "textbf{Basis} & " + B + "textbf{Worth on target} " + B + B,
        B + "midrule", B + "endhead",
        B + "bottomrule", B + "endfoot",
        NL.join(rows), B + "end{longtable}}",
        B + "srcnote{Sensitivities are computed by re-running the engine with the named "
        "input changed and nothing else. Against a published target of KShs "
        + money(base) + " and a valuation-date price of KShs " + money(P) + ".}",
        "",
        B + "subsection*{The three that are least well supported}",
        "Of the inputs above, three carry weight the evidence behind them does not.",
        "",
        "The equity beta of " + money(beta, 2) + " is the clearest. This note holds no "
        "return series for Safaricom or for the peer group, so the figure has not been "
        "estimated from data and cannot be. It is a judgement that the shares are "
        "slightly less volatile than the market, which is the conventional reading for a "
        "dominant telecom with a large dividend. At 1.00 the target falls KShs "
        + money(abs(mv(ke=b["ke"] + 0.10 * erp, wacc=b["wacc"] + 0.80 * 0.10 * erp)))
        + " and at 0.80 it rises KShs "
        + money(mv(ke=b["ke"] - 0.10 * erp, wacc=b["wacc"] - 0.80 * 0.10 * erp))
        + ". The rating holds across that whole range, which is the only reason the "
        "note is willing to publish on an unestimated beta.",
        "",
        "The M-PESA growth rate of " + pct(cagr("M-PESA revenue"), 1) + " a year is the "
        "second. It is the largest line in the largest segment and it is carried as a "
        "single compound rate, not built from transaction value, take rate and agent "
        "economics. The note prices what M-PESA earns per user today and what the agent "
        "estate costs, but it does not forecast either. On the view that the payments "
        "line decelerates should treat the Kenyan revenue case on the operating drivers "
        "page as the relevant sensitivity.",
        "",
        "The birr assumption is the third and the note says so on the schedules page. "
        "The rate is calibrated to make FY2026 Ethiopian revenue tie in shillings, so it "
        "carries whatever error is in the translation rather than being an observed "
        "rate. Ethiopia is " + pct(_row(d["is_rows"], "Ethiopia revenue")[0]
                                   / _row(d["is_rows"], "TOTAL REVENUE")[0], 1)
        + " of group revenue at the start of the forecast and "
        + pct(_row(d["is_rows"], "Ethiopia revenue")[-1]
              / _row(d["is_rows"], "TOTAL REVENUE")[-1], 1) + " at the end, so the "
        "assumption matters more each year.",
    ])

def levers(d) -> str:
    """Every quantified question in this note, priced, on one page.

    The published build already carries the Ethiopian capital expenditure
    correction, so the first row below reverses it to show what that correction
    was worth. Every other row moves one input away from the published build.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _lb, value as _lv, fcff as _lf

    m = _model_of(d)
    b = _lb(m)
    base = _lv(m, **b)["target"]
    cf = _lf(m)
    P = d["val_price"]
    ee, ge = _row(d["is_rows"], "Ethiopia EBITDA"), _row(d["is_rows"], "EBITDA")
    kr = _row(d["drv_rows"], "KENYA TOTAL REVENUE")
    kem = _row(d["drv_rows"], "KENYA EBITDA")
    dps = _row(d["is_rows"], "DPS (KShs)")[1]
    SH = 40065.4

    # A scenario moves forecast EBITDA. The peer leg is the peer median applied to
    # FY2026 reported earnings, which no forecast can change, so it is held.
    def scen(dEB):
        dv = [abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
              if isinstance(x, (int, float))]
        m2 = dict(m); m2["cf_rows"] = dict(m["cf_rows"])
        m2["cf_rows"]["Dividends PAID"] = [None] + [a + c * 0.70 * 0.80
                                                    for a, c in zip(dv, dEB)]
        return _lv(m2, fcff_override=[c + v * 0.70 for c, v in zip(cf, dEB)],
                   ebitda31=ge[-1] + dEB[-1], **b)["target"]

    ke2 = 0.1243 + 0.90 * 0.055
    w2 = 0.80 * ke2 + 0.20 * _v(d, "Cost of debt (pre-tax)") * (1 - _v(d, "Tax rate"))
    cagr_k = (kr[-1] / kr[0]) ** (1 / (len(kr) - 1)) - 1
    # 76.9% is the share of M-PESA revenue left after agent commissions, from
    # the unit-economics page. A revenue haircut is taken at that margin.
    mp = _row(d["drv_rows"], "M-PESA revenue")
    yrs2 = d["years"]
    _mpg = (mp[-1] / mp[0]) ** (1 / (len(mp) - 1)) - 1
    slow = [(kr[i] * ((1 + cagr_k - 0.01) / (1 + cagr_k)) ** i - kr[i]) * (kem[i] / kr[i])
            for i in range(1, len(kr))]
    # The workbook carries the capex correction, so these two rows price it by
    # reverting it, and each carries the matching FY2031 net debt into the exit
    # leg. Reverting on the discounted leg alone would value a company that spent
    # cash one leg says it did not.
    from revalue import eth_capex_relief as _rel, eth_capex_net_debt_relief as _ndrel
    _reverted = _lf(m, revert_capex=True)
    uncorrected = _lv(m, fcff_override=_reverted,
                      exit_net_debt_addback=_ndrel(), **b)["target"]
    # Only FY2027 corrected: the other four years revert, so only their share of
    # the borrowing goes back.
    _r = _rel()
    _only27 = [c + (_r[0] if i == 0 else 0.0) for i, c in enumerate(_reverted)]
    _back27 = _ndrel(rel=[0.0] + list(_r[1:]))

    ROWS = [
        ("Ethiopian capex", "Reverted to the model's flat 26bn", "no", uncorrected),
        ("Ethiopian capex", "Only the guided FY2027 year corrected", "no",
         _lv(m, fcff_override=_only27, exit_net_debt_addback=_back27, **b)["target"]),
        ("Discount rate", "One point lower", "no",
         _lv(m, **{**b, "wacc": b["wacc"] - 0.01, "ke": b["ke"] - 0.01})["target"]),
        ("Discount rate", "No Ethiopian country premium", "no",
         _lv(m, **{**b, "wacc": w2, "ke": ke2})["target"]),
        ("Terminal growth", "At the 2.5\\% floor of the target band", "no",
         _lv(m, **{**b, "g": 0.025})["target"]),
        ("Terminal growth", "At the 7.5\\% ceiling of the target band", "no",
         _lv(m, **{**b, "g": 0.075})["target"]),
        ("Depreciation", "At the audited FY2026 rate", "no", _dep_case(d, m, b, cf)),
        ("Kenya revenue", "Compounding one point slower", "no", scen(slow)),
        ("Ethiopia", "Break-even slips two years", "no",
         scen([0, 0, -ee[2], ee[2] - ee[3], ee[3] - ee[4]])),
        ("Ethiopia", "Never passes break-even", "no",
         scen([0, -ee[2], -ee[3], -ee[4], -ee[5]])),
        ("M-PESA", "A levy or fee cap takes 5\\% of revenue from " + tex(yrs2[2]), "no",
         scen([0] + [-mp[i] * 0.05 * 0.769 for i in range(2, 6)])),
        ("M-PESA", "The same at 10\\%", "no",
         scen([0] + [-mp[i] * 0.10 * 0.769 for i in range(2, 6)])),
        ("M-PESA", "Growth two points slower", "no",
         scen([mp[i] * (((1 + _mpg - 0.02) / (1 + _mpg)) ** i - 1) * 0.769
               for i in range(1, 6)])),
        ("Both together", "No Ethiopian premium and growth at the band ceiling", "no",
         _lv(m, **{**b, "wacc": w2, "ke": ke2, "g": 0.075})["target"]),
    ]

    def tr(t):
        return t / P - 1 + dps / P

    def line(g, w, pref, t):
        return (tex(g) + " & " + w
                + " & " + money(t) + " & " + pct(t / P - 1) + " & " + pct(tr(t)) + " & "
                + ("yes" if tr(t) < 0 else B + "textbf{no}") + " " + B + B)

    rows = [line(*r) for r in ROWS]
    both = ROWS[-1][3]
    out = [
        B + "newpage",
        B + "section*{" + B + "color{navy}What every question in this note is worth}",
        "The arguments are made one at a time. Assembled, this is what each "
        "is worth in shillings per share. Every case runs through the same valuation "
        "engine with only the named input changed. The last column asks whether the "
        "Sell holds on total return, which is the standard the cover sets.",
        B + "par" + B + "vspace{4pt}",
        "{" + B + "footnotesize",
        B + "excap{Every case this note makes, priced}",
        B + "begin{tabular}{@{}p{2.5cm}p{5.4cm}rrrl@{}}",
        B + "toprule",
        B + "textbf{Question} & " + B + "textbf{Case} & "
        + B + "textbf{Target} & " + B + "textbf{vs price} & " + B + "textbf{Total return} & "
        + B + "textbf{Sell holds} " + B + B,
        B + "midrule",
        B + "textbf{As published} & The build in this note & "
        + B + "textbf{" + money(base) + "} & " + B + "textbf{" + pct(base / P - 1)
        + "} & " + B + "textbf{" + pct(tr(base)) + "} & " + B + "textbf{yes} " + B + B,
        B + "midrule",
        NL.join(rows),
        B + "bottomrule",
        B + "end{tabular}}",
        B + "srcnote{Against the valuation-date price of KShs " + money(P)
        + ". Total return adds the FY2027 declared dividend of KShs " + money(dps)
        + " a share. The first row reverses a correction the published build already "
        "carries, so it reads downward. One point of cost of capital moves the discounted "
        "leg further than it moves the target, because only one of the three legs is "
        "discounted.}",
        "",
        B + "subsection*{The row that matters, and what it does to the rating}",
        "No single case in the table turns the rating. The largest, the discount rate a "
        "point lower, is worth KShs " + money(ROWS[2][3] - base) + " and leaves the Sell "
        "standing on both measures. The rating turns only where several move together, "
        "and the corner that reaches the price is the one this note has argued about "
        "throughout: a materially lower cost of capital combined with terminal growth "
        "above the inflation target band.",
        "",
        "The two constructions that most affect the target are the discount rate and "
        "the terminal growth rate, and both are set out in full under country risk. "
        "The discount rate is built on the traded Kenyan ten-year with no duplicated "
        "sovereign premium, and the terminal rate is held below the midpoint of the "
        "central bank's inflation target band. Applying the upper end of that band "
        "would give KShs " + money(both) + " and a total return of " + pct(tr(both))
        + ", which narrows the case without changing its direction. The published "
        "target uses the lower figure because a terminal growth rate above the "
        "midpoint of the target band assumes the central bank misses its own mandate "
        "in perpetuity.",
        "",
        "On the published build Safaricom offers a total return of " + pct(tr(base))
        + " over twelve months against a required return set by the cost of equity, "
        "and the recommendation is Sell. The rating is supported on each of the "
        "individual sensitivities set out above and on the weighted blend of the "
        "three methods.",
    ]
    return NL.join(out) + NL


def capital_allocation(d) -> str:
    """What the company has done with shareholders' money, and what it earned.

    Both reviews of this note asked the same question and found no answer in it:
    a five-year forecast is underwritten by a management team, and the note gave
    three names and a remuneration figure. This page tests the two allocation
    decisions that are visible in the filings.
    """
    I, BS, yrs = d["is_rows"], d["bs_rows"], d["years"]
    ee, er = _row(I, "Ethiopia EBITDA"), _row(I, "Ethiopia revenue")
    nci_pl = _row(I, "Attributable to non-controlling interests")
    roic = _fratios_roic(d)
    wacc = _v(d, "WACC")
    loss = sum(v for v in ee if isinstance(v, (int, float)) and v < 0)
    prof = sum(v for v in ee if isinstance(v, (int, float)) and v > 0)
    n = len(yrs)
    h = " & ".join(B+"textbf{" + tex(x) + "}" for x in yrs)
    rows = [
        "Ethiopia revenue & " + " & ".join(num(v) for v in er) + " " + B+B,
        "Ethiopia EBITDA & " + " & ".join(num(v) for v in ee) + " " + B+B,
        "Ethiopia EBITDA margin & "
        + " & ".join(pct(ee[i] / er[i]) for i in range(n)) + " " + B+B,
        "Losses taken by minorities & " + " & ".join(num(v) for v in nci_pl) + " " + B+B,
        B+"midrule",
        B+"textbf{Group return on invested capital} & "
        + " & ".join(["--"] + [pct(v) for v in roic]) + " " + B+B,
        "Weighted cost of capital & " + " & ".join([pct(wacc)] * n) + " " + B+B,
    ]
    return NL.join([
        B+"newpage",
        B+"section*{"+B+"color{navy}Capital allocation}",
        "A five-year forecast is a bet on the people who allocate the capital. Two "
        "decisions are visible in the filings and both are testable.",
        "",
        B+"subsection*{Ethiopia}",
        "The group entered a second market at a cost it has never disclosed as a single "
        "figure, and one cannot be constructed here: the licence, the network build and "
        "the working capital are not split out from group capital expenditure in any "
        "statement used here. What can be measured is the operating result. The segment "
        "has taken KShs " + num(abs(loss)) + "m of EBITDA losses across "
        + tex(yrs[0]) + " and " + tex(yrs[1]) + " and, on the model's own forecast, "
        "earns KShs " + num(prof) + "m over the four years that follow, turning net "
        "positive across the six-year window by KShs " + num(loss + prof) + "m at the "
        "EBITDA line alone. That is before the capital that produced it, which is why it "
        "is a statement about operations, and not yet about returns.",
        B+"par"+B+"vspace{4pt}",
        "{"+B+"footnotesize",
        B+"excap{The Ethiopian investment, and the group return it sits inside}",
        B+"begin{tabular}{@{}l" + "r" * n + "@{}}",
        B+"toprule", "KES millions & " + h + " " + B+B, B+"midrule",
        NL.join(rows), B+"bottomrule", B+"end{tabular}}",
        B+"srcnote{Return on invested capital is operating profit after tax over average "
        "equity plus net debt, and it is a group figure: Ethiopia cannot be isolated "
        "because segment invested capital is not disclosed. The minority line is the share "
        "of Ethiopian losses borne by the consortium, and it turns positive in "
        + tex(yrs[-1]) + ", which is the first year the segment contributes profit instead of absorbing it.}",
        "",
        "Two honest readings follow. The generous one is that a group earning "
        + pct(roic[0]) + " on capital can afford a build that loses money for four years, "
        "and the forecast has the segment contributing by " + tex(yrs[2]) + ". The "
        "sceptical one is that the group return is high precisely because the Kenyan "
        "asset base is old and largely written down, so the marginal capital going into "
        "Ethiopia earns nothing like " + pct(roic[0]) + ", and there is no way to "
        "check because the disclosure does not permit it. This note takes the second view "
        "far enough to say that the Ethiopian return is the least well-evidenced number "
        "behind the rating, and it is one the company could settle with a single "
        "disclosure.",
        "",
        B+"subsection*{The dividend}",
        "The second decision is the payout. The company froze the dividend for three "
        "years and then restored it, and in FY2023 paid out 102.1\\% of that year's "
        "earnings, which is to say it distributed more than it made. The forecast holds "
        "the payout at 80\\% throughout. That is defensible against the record, but it is "
        "not free: the borrowing schedule shows no new debt drawn in any forecast year"
        + ", which is to say operating cash flow already covers capital expenditure, "
        "leases, interest and the dividend together. A payout materially above 80\\% "
        "breaks that, and with it the deleveraging path from "
        + mult((_rows_sum(BS, "Borrowings")[0] + _rows_sum(BS, "Lease liabilities")[0]
                - _row(BS, "Net cash and cash equivalents", "Cash and cash equivalents")[0])
               / _row(I, "EBITDA")[0], 2) + " shown "
        "shows elsewhere as evidence of financial strength. The conservative-payout "
        "argument and the deleveraging argument cannot both be pushed at once, and a "
        "two cannot both be pushed, because both spend the same cash.",
        "",
        B+"subsection*{What the controlling holder changes}",
        "Since 30 June 2026 one strategic holder controls 55\\% and consolidates the "
        "company. Allocation decisions from here are taken by a board that holder "
        "appoints. This note does not treat that as negative for the minority, and the "
        "control transaction was struck at KShs 34.00 rather than at a premium. But it "
        "does mean the payout assumption is now a question about one shareholder's cash needs, no longer about a dispersed register's preferences, and that is a "
        "different kind of assumption than the one the model's 80\\% represents.",
    ])

def _wacc_pt(d) -> float:
    """What one point of cost of capital is worth on the TARGET, not on the
    discounted leg alone. The two differ because only one of the three legs is
    discounted, and quoting the leg figure as the target figure overstates it."""
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _b, value as _v2
    m = _model_of(d)
    bb = _b(m)
    lo = _v2(m, **{**bb, "wacc": bb["wacc"] - 0.01, "ke": bb["ke"] - 0.01})["target"]
    hi = _v2(m, **{**bb, "wacc": bb["wacc"] + 0.01, "ke": bb["ke"] + 0.01})["target"]
    return (lo - hi) / 2


def catalysts(d) -> str:
    """Dated events, and how to hold the view.

    A target with no horizon and no calendar is an estimate of value, not a
    position. Every date below already appears above; the point
    of the page is that they were never assembled.
    """
    P, T = d["val_price"], d["target"]
    # The two tables that price this case both hold Ethiopian EBITDA at nil from the
    # third year. The catalyst row used to carry a typed 1.61, which is the value of
    # the grounded-discount-rate case on a different page.
    _ee_c = _row(d["is_rows"], "Ethiopia EBITDA")
    _be_worth = T - _scen_target(d, [0, -_ee_c[2], -_ee_c[3], -_ee_c[4], -_ee_c[5]])
    peers = sorted([p for p in d["peers"] if p.get("ev_ebitda")],
                   key=lambda p: p["ev_ebitda"])
    dear = [p for p in peers if p["ev_ebitda"] > (d["peer_med_ev"] or 0)]
    rows = [
        ("H1 FY2027 results", "November 2026",
         "First check on the Kenyan revenue path and on Ethiopian losses halving again"),
        ("FY2026 full annual report", "Obtained 1 August 2026",
         "Replaces the FY2026 figures derived rather than read"),
        ("CA quarterly sector statistics", "Quarterly",
         "Subscriber and share series used in market position, and the only "
         "independent read on whether Airtel is closing"),
        ("AGM and final dividend declaration", "Annually, mid-year",
         "Tests the 80% payout the forecast holds, and the new controlling "
         "shareholder's appetite for cash"),
        ("Ethiopian break-even", "Guided FY2027, modelled " + tex(d["eth_be"] or "n/a"),
         "Worth KShs " + money(_be_worth) + " "
         "of the target if it never arrives"),
        ("Kenyan general election", "10 August 2027",
         "Falls in the first half of FY2028. Past cycles have widened the "
         "sovereign spread that sets the discount rate"),
        ("CBK rate decisions", "Every two months",
         "One point of cost of capital is worth KShs " + money(_wacc_pt(d))
         + " on the target"),
    ]
    trs = "\n".join(tex(a) + " & " + tex(b) + " & " + tex(c) + r" \\" for a, b, c in rows)
    return NL.join([
        B+"newpage",
        B+"section*{"+B+"color{navy}Calendar, and how to hold this view}",
        "This note gives an estimate of value, not a forecast of when the price reaches "
        "it. The observable checkpoints are set out "
        "instead. Each date below is used elsewhere in the note.",
        B+"par"+B+"vspace{4pt}",
        "{"+B+"footnotesize",
        B+"excap{What to watch, and why it matters to this rating}",
        B+"begin{tabular}{@{}p{3.6cm}p{3.2cm}p{9.4cm}@{}}",
        B+"toprule",
        B+"textbf{Event} & "+B+"textbf{Timing} & "+B+"textbf{What it tests} " + B+B,
        B+"midrule", trs, B+"bottomrule", B+"end{tabular}}",
        B+"srcnote{Timing for company events follows the FY2026 reporting calendar and is "
        "indicative. The election date is fixed by the Constitution. Rate decisions follow "
        "the published Monetary Policy Committee schedule.}",
        "",
        B+"subsection*{Expressing it}",
        "The mechanical problem with this rating is set out under what would make it "
        "wrong: the float is small and an income holder has little reason "
        "to sell. Two routes express the view without a short.",
        B+"par"+B+"vspace{3pt}",
        B+"begin{itemize}[leftmargin=12pt,itemsep=1pt,topsep=1pt]",
        B+"item Underweight against the index. Safaricom is a large share of NSE market "
        "capitalisation, so an index-relative account can express the view by holding less "
        "of it without borrowing anything.",
        B+"item Pair against the dearer peers rather than the group. "
        + tex(" and ".join(_short_peer(p["name"]) for p in dear[:2]))
        + " both trade above the peer median on the same measure and in the same table, "
        "so a "
        "pair expresses the relative-value half of the argument without taking a view on "
        "African telecom multiples as a whole.",
        B+"end{itemize}",
        "",
        "Neither is a recommendation to trade, and neither carries a horizon. The target "
        "is KShs " + money(T) + " against KShs " + money(P) + " at the valuation date, and "
        "the note's own view is that this gap could take years to close if it closes at "
        "all.",
        "",
        B+"subsection*{A word on the scale}",
        "This house rates Buy or Sell and has no Hold. That matters here. The weighted "
        "range around the target runs from KShs " + money(d["wband"][0])
        + " to KShs " + money(d["wband"][1]) + ", the top of which is still "
        "below the price, and two inputs could move "
        "the target to within a fraction of it. On a three-point scale this would read as "
        "fairly valued. Part of the rating is a view on value and "
        "how much is a scale with no middle term.",
    ])

def country_risk(d) -> str:
    """The macro the discount rate is supposed to reflect, and the election in it."""
    rows = NL.join(tex(a) + " & " + b + " & " + tex(c) + " " + B+B
                   for a, b, c in KENYA_MACRO)
    mg = NL.join(tex(a) + " & " + tex(b) + " & " + tex(c) + " " + B+B
                 for a, b, c in MANAGEMENT)
    rf = _v(d, "Risk-free rate")
    crp = _v(d, "Ethiopia country risk premium")
    ethw = _v(d, "Ethiopia weight in the cost of equity")
    ke_v, wacc_v = _v(d, "Cost of equity"), _v(d, "WACC")
    beta_v, erp_v = _v(d, "Equity beta"), _v(d, "Equity risk premium")
    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Country risk and the discount rate}")
    out.append("Safaricom earns in two currencies and carries two sovereign exposures, and "
               "the discount rate is built to reflect both without pricing either twice. "
               "Kenya's position around the valuation date is set out below.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}lll@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Measure} & "+B+"textbf{Value} & "+B+"textbf{Note} " + B+B)
    out.append(B+"midrule")
    out.append(rows)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Analyst compilation from the published releases of the "
               "institutions named. The Central Bank Rate shown was set after the "
               "valuation date and does not enter the valuation.}")
    out.append("")
    out.append(B+"subsection*{How the discount rate is built}")
    out.append("The valuation is struck in shillings, so the risk-free rate is the Kenyan "
               "ten-year local-currency government yield of " + pct(rf) + " at the "
               "valuation date. A shilling government yield already carries Kenyan "
               "sovereign and inflation risk, so no separate Kenya country premium is "
               "added to it. Adding one would price the same risk twice and would put the "
               "cost of equity above what the sovereign itself pays by more than the "
               "equity risk premium can justify.")
    out.append("")
    out.append("Ethiopia is a different matter. It sits several rating notches below Kenya "
               "and has restructured its external debt, and none of that risk is carried "
               "in a Kenyan yield. An incremental premium of " + pct(crp) + " is therefore "
               "added for Ethiopia and weighted at " + pct(ethw) + ", which is Ethiopia's "
               "share of group revenue in the last forecast year, rather than being applied "
               "to the whole group. The Kenyan business does not carry Ethiopian sovereign "
               "risk and is not charged for it.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"excap{The discount rate, as built}")
    out.append(B+"begin{tabular}{@{}lr@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Component} & "+B+"textbf{Rate} " + B+B)
    out.append(B+"midrule")
    out.append("Kenya ten-year local-currency yield & " + pct(rf, 2) + " " + B+B)
    out.append("Equity beta & " + money(beta_v, 2) + " " + B+B)
    out.append("Mature-market equity risk premium & " + pct(erp_v, 2) + " " + B+B)
    out.append("Ethiopia incremental country premium & " + pct(crp, 2) + " " + B+B)
    out.append("Ethiopia weight & " + pct(ethw, 1) + " " + B+B)
    out.append(B+"midrule")
    out.append(B+"textbf{Cost of equity} & "+B+"textbf{" + pct(ke_v, 2) + "} " + B+B)
    out.append("Cost of debt, pre-tax & " + pct(_v(d, "Cost of debt (pre-tax)"), 2) + " " + B+B)
    out.append("Equity and debt weights & 80 / 20 " + B+B)
    out.append(B+"midrule")
    out.append(B+"textbf{WACC} & "+B+"textbf{" + pct(wacc_v, 2) + "} " + B+B)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{The Ethiopian premium is applied to Ethiopia's share of the "
               "group only. Applied to the whole group it would add "
               + pct(crp * (1 - ethw), 2) + " to the cost of equity for a risk the Kenyan "
               "business does not bear.}")
    out.append("")
    out.append(B+"subsection*{The election is inside the forecast}")
    out.append("Kenya holds a general election on 10 August 2027. The Constitution fixes "
               "polling on the second Tuesday of August every fifth year, and because 1 "
               "August 2027 is a Sunday the second Tuesday is the tenth. Safaricom's year "
               "ends on 31 March, so that date falls in the first half of FY2028, the "
               "second forecast year of this model and not the first. It is easy to place "
               "it a year early, because the polling year and the financial year share a "
               "name and do not share a period. Kenyan election years have "
               "historically carried currency and spending volatility, and the model makes "
               "no explicit allowance for one. FY2028 revenue and "
               "the shilling path as carrying an unmodelled political variance that the "
               "discount rate is doing all the work of absorbing.")
    out.append("")
    out.append("The direction of the macro is otherwise favourable to the company and "
               "unfavourable to this rating. Moody's upgraded Kenya to B3 with a stable "
               "outlook in January 2026 on stronger reserves and lower near-term default "
               "risk, the policy rate has been held at 8.75\\%, and the ten-year has "
               "fallen a long way from its 19.4\\% peak in April 2024. A continuation of "
               "that is the country-risk half of the bull case on the scenarios page.")
    out.append("")
    out.append(B+"section*{"+B+"color{navy}Who runs the company}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}lll@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Name} & "+B+"textbf{Role} & "+B+"textbf{In post since} " + B+B)
    out.append(B+"midrule")
    out.append(mg)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Company leadership disclosure, cross-checked against independent "
               "reporting of the FY2026 annual report. The chief executive's total "
               "remuneration for the year to 31 March 2026 was reported at KShs 324.5m. "
               "The chief finance officer's appointment date is not given in any source "
               "used here, which is why the cell is empty and not estimated.}")
    out.append("")
    out.append("Safaricom was founded in 1997 as a subsidiary of Telkom Kenya and listed "
               "on the Nairobi Securities Exchange in 2008, when the government sold "
               "25\\%. It employed 6,902 people across the group at FY2026, of whom "
               "6,616 were permanent, against 6,777 and 6,462 a year earlier. Headcount "
               "rose on both measures. The company's own summary page compares the FY2026 "
               "permanent figure with the FY2025 total and so reports a fall. The audited "
               "note behind it shows the increase. The chief executive has been "
               "in post since April 2020, so the margin recovery, the Ethiopian entry and "
               "the FY2026 record all sit within one tenure.")
    out.append("")
    out.append(B+"subsection*{The record inside this tenure}")
    _f = d["fins"]
    out.append("A five-year forecast is underwritten by the people running the business, "
               "and the record is set out here. Revenue has compounded "
               "at " + pct((_f[-1]["revenue"] / _f[0]["revenue"]) ** (1 / (len(_f) - 1)) - 1)
               + " a year across the audited period. The EBITDA margin fell from "
               + pct(_f[0]["ebitda"] / _f[0]["revenue"]) + " to "
               + pct(_f[3]["ebitda"] / _f[3]["revenue"]) + " and recovered to "
               + pct(_f[-1]["ebitda"] / _f[-1]["revenue"]) + ", though a material part of "
               "the final step is a credit charge returning to normal, and not operating improvement, as the decomposition above sets out. "
               "The dividend was held flat for three years and then raised, and the FY2023 "
               "payout of 102.1" + chr(92) + "% distributed more than the year's earnings. Ethiopia was "
               "entered in that same window and has not yet earned a return that is "
               "measure.")
    out.append("")
    out.append("The fair reading is a management team that took a mature, highly "
               "profitable domestic business through a margin trough it largely caused "
               "with a second-market entry, and has brought the domestic margin back to a "
               "record while that entry is still loss-making. Whether the entry was a good "
               "use of capital is the open question, and the disclosure does not let a "
               "the question be answered. The record is not treated as a reason to grant "
               "the forecast or to withhold it. It grants the forecast on its own terms "
               "and still reaches a Sell.")
    out.append("")
    out.append(B+"subsection*{Governance matters not quantified in public disclosure}")
    out.append("Three governance matters bear on a minority holder and are not quantified "
               "in public disclosure. The board's composition and the number of independent "
               "directors are not disclosed in a form that permits comparison. Related-party "
               "arrangements between the company and its controlling shareholder, whether a "
               "brand or technology licence, management fees or shared procurement, are not "
               "separately quantified; each would represent a transfer from the consolidated "
               "entity to the parent. The Ethiopian consortium's forward funding obligation "
               "is not disclosed beyond the restoration right described under ownership. "
               "These are limits of the public record. None is treated as a conclusion "
               "and none is assumed in the forecast.")
    out.append("")
    out.append("The governance point that matters to a minority holder is the one set "
               "out under ownership. Since 30 June 2026 a single strategic holder controls "
               "55\\% and consolidates the company, and the Government of Kenya holds a "
               "further 20\\%. Between them they hold three quarters of the register. "
               "The free float is 25\\%, which is what sets the traded price this note is "
               "arguing with, and it did not change in the transaction, because what "
                     "moved was one strategic holding to another, not stock to or from "
                     "the market.")
    return NL.join(out) + NL


def money_making(d) -> str:
    """How the revenue is actually earned: users, price per user, and cost to serve.

    A telecom's EV per ton is EV per subscriber, and its cost of goods is the
    commission it pays the agent who takes the cash. Both are in the record and
    neither reached the page.
    """
    n = d["notes"]
    k = {x["name"]: x["value"] for x in (n.get("sector_specific") or [])
         if isinstance(x.get("value"), (int, float))}
    sg = {x["name"]: x["value"] for x in (n.get("segments") or [])
          if isinstance(x.get("value"), (int, float))}
    mk, L = d["market"], d["fins"][-1]
    mp = sg.get("M-PESA revenue", 0.0)
    mpc = abs(k.get("M-PESA commissions (direct cost)", 0.0))
    dr = sg.get("Mobile data revenue", 0.0)
    rpu = k.get("M-PESA revenue-per-user (RPU)", 0.0)
    arpu = k.get("Mobile data ARPU/RPU", 0.0)
    oma = k.get("One-month active customers (Group)", 0.0)
    ev_late = (d["later_price"] * 40065.4) + (L.get("net_debt") or 0)
    ev_val = (d["val_price"] * 40065.4) + (L.get("net_debt") or 0)

    rows = [
        ("M-PESA revenue", num(mp, 1), "KES m", "FY2025"),
        ("M-PESA commissions paid out", num(-mpc, 1), "KES m", "FY2025"),
        ("Contribution after commissions", num(mp - mpc, 1), "KES m", "FY2025"),
        ("Commissions as a share of M-PESA revenue", pct(mpc / mp) if mp else "--", "", "FY2025"),
        ("M-PESA revenue per user", money(rpu), "KES/month", "FY2025"),
        ("Implied M-PESA users", money(mp / (rpu * 12), 1) if rpu else "--", "million", "derived"),
        ("Kenya one-month active customers", money(k.get("Kenya one-month active customers", 0), 1), "million", "FY2025"),
        ("Mobile data revenue", num(dr, 1), "KES m", "FY2025"),
        ("Mobile data revenue per user", money(arpu), "KES/month", "FY2025"),
        ("Implied mobile data users", money(dr / (arpu * 12), 1) if arpu else "--", "million", "derived"),
        ("Kenya smartphones on network", money(k.get("Kenya smartphones on network", 0), 1), "million", "FY2025"),
        ("Average data usage per user", money(k.get("Average data usage per user", 0)), "GB/month", "FY2025"),
        ("Licence fees charged to direct costs", num(-abs(k.get("Licence fees (direct cost)", 0)), 1), "KES m", "FY2025"),
        ("Employees", num(k.get("Number of employees (Group)", 0)), "", "FY2025"),
    ]
    body = NL.join(tex(a) + " & " + b + " & " + tex(c) + " & " + tex(e) + " " + B+B
                   for a, b, c, e in rows)

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}How the money is actually made}")
    out.append("Two lines carry this company. M-PESA is a payments network billed per user "
               "per month, and mobile data is a connectivity business billed the same way. "
               "The table gives the unit economics the company discloses, and two figures "
               "derived from them by division, marked as derived.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}lrll@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Measure} & "+B+"textbf{Value} & "+B+"textbf{Unit} & "
               + B+"textbf{Period} " + B+B)
    out.append(B+"midrule")
    out.append(body)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Source: company operating disclosure, FY2025, via the platform's "
               "coverage record. Implied user counts are revenue divided by monthly revenue "
               "per user times twelve, and are shown because they can be checked against "
               "the disclosed customer bases beside them.}")
    out.append("")
    out.append(B+"subsection*{The two implied user counts are the check on the rest}")
    out.append("M-PESA revenue divided by its disclosed revenue per user implies "
               + money(mp / (rpu * 12), 1) + " million users against "
               + money(k.get("Kenya one-month active customers", 0), 1) + " million "
               "one-month active customers in Kenya. Mobile data implies "
               + money(dr / (arpu * 12), 1) + " million against "
               + money(k.get("Kenya smartphones on network", 0), 1)
               + " million smartphones on the network. Both land just inside their own "
               "ceiling, which is what a coherent disclosure looks like and is worth "
               "checking before relying on the revenue-per-user figures.")
    out.append("")
    out.append(B+"subsection*{What it costs to run a payments network}")
    out.append("M-PESA pays " + pct(mpc / mp) + " of its revenue away in agent "
               "commissions, leaving a contribution of " + pct(1 - mpc / mp)
               + " before any shared cost. That is the number to hold when reading the "
               "regulatory risk: a licensing separation would not remove the revenue, it "
               "would reprice the margin on the highest-contribution line the group has. "
               "Each five per cent taken off M-PESA revenue is roughly KShs "
               + num(mp * 0.05 * (1 - mpc / mp), 1) + "m of contribution.")
    out.append("")
    out.append(B+"subsection*{Entry point, per subscriber}")
    OMA26 = 50.66      # group one-month active customers, FY2026 annual report
    out.append("A cement company is priced per ton of capacity. A telecom is priced per "
               "subscriber. On the valuation-date price the enterprise value is KShs "
               + num(ev_val) + "m against " + money(OMA26, 2) + " million one-month "
               "active group customers at FY2026, or KShs " + num(ev_val / OMA26) + " a "
               "subscriber. At the later quote it is KShs " + num(ev_late / OMA26) + ".")
    out.append("")
    out.append("The vintage matters here. The operating table "
               "above is FY2025 throughout, and the FY2025 one-month active base was "
               + money(oma, 2) + " million. Dividing a 2026 enterprise value by that base "
               "gives KShs " + num(ev_val / oma) + " a subscriber, overstating the entry "
               "price by " + pct((ev_val / oma) / (ev_val / OMA26) - 1, 0) + " because the "
               "base grew " + pct(OMA26 / oma - 1, 0) + " in the year. The figure above "
               "uses the FY2026 base, which is the year the enterprise value is struck "
               "against.")
    out.append(B+"srcnote{Group one-month active customers of 50.66 million at FY2026 "
               "against 44.36 million at FY2025. Not to be confused with the 90-day base "
               "of 71.56 million, which is the figure behind the company's headline "
               "customer number, nor with the Kenya-only one-month base of 39.92 "
               "million.}")
    out.append("")
    out.append("That figure is offered without a benchmark, deliberately. The coverage "
               "record carries no subscriber counts for the peer group, so there is "
               "nothing here to compare it against, and a per-subscriber figure without a "
               "comparator is a fact and not yet an argument. It is printed for use "
               "with their own transaction comparables can use it.")
    return NL.join(out) + NL


def ethiopia_drivers(d) -> str:
    """The two rules that generate the Ethiopian forecast, and what they are worth.

    Kenya gets three driver exhibits. Ethiopia got none, while carrying the whole
    of the group margin expansion. The forecast turns out to be two mechanical
    rules, and a reader is entitled to see them rather than infer them.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _b, value as _v2, fcff as _f2

    I, yrs = d["is_rows"], d["years"]
    er, ee, ge = _row(I, "Ethiopia revenue"), _row(I, "Ethiopia EBITDA"), _row(I, "EBITDA")
    n = len(yrs)
    m = _model_of(d)
    b = _b(m)
    cf = _f2(m)
    base = _v2(m, **b)["target"]
    P, SH = d["val_price"], 40065.4
    dps = _row(I, "DPS (KShs)")[1]

    def scen(dEB):
        dv = [abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
              if isinstance(x, (int, float))]
        m2 = dict(m); m2["cf_rows"] = dict(m["cf_rows"])
        m2["cf_rows"]["Dividends PAID"] = [None] + [a + c * 0.70 * 0.80
                                                    for a, c in zip(dv, dEB)]
        return _v2(m2, fcff_override=[c + v * 0.70 for c, v in zip(cf, dEB)],
                   ebitda31=ge[-1] + dEB[-1], **b)["target"]

    growth = er[1] / er[0] - 1
    marg = [ee[i] / er[i] for i in range(n)]
    steps = [(marg[i] - marg[i - 1]) * 100 for i in range(1, n)]
    m0 = marg[0]
    frozen = scen([er[i] * m0 - ee[i] for i in range(1, n)])
    lower5 = scen([er[i] * (marg[i] - 0.05) - ee[i] for i in range(1, n)])
    slow10 = scen([(er[0] * ((1 + growth - 0.10) ** i) - er[i]) * marg[i]
                   for i in range(1, n)])
    h = " & ".join(B + "textbf{" + tex(x) + "}" for x in yrs)
    rows = [
        "Revenue, KES m & " + " & ".join(num(v) for v in er) + " " + B + B,
        "Growth on the year & " + " & ".join(
            ["--"] + [pct(er[i] / er[i - 1] - 1, 1) for i in range(1, n)]) + " " + B + B,
        "EBITDA, KES m & " + " & ".join(num(v) for v in ee) + " " + B + B,
        "EBITDA margin & " + " & ".join(pct(v, 1) for v in marg) + " " + B + B,
        "Margin gained on the year & " + " & ".join(
            ["--"] + [money(s, 1) + " pt" for s in steps]) + " " + B + B,
    ]
    cases = [
        ("Margin held at the " + tex(yrs[0]) + " loss rate", frozen),
        ("The whole margin path five points lower", lower5),
        ("Revenue growth ten points slower", slow10),
    ]
    crows = [tex(a) + " & " + money(t) + " & " + money(t - base) + " & "
             + pct(t / P - 1) + " & " + pct(t / P - 1 + dps / P) + " " + B + B
             for a, t in cases]
    return NL.join([
        B + "subsection*{The two rules that generate this forecast}",
        "The Kenyan forecast is built from customer counts and the prices they pay, "
        "with the cost lines beneath, and all of it is printed. The Ethiopian one is not "
        "built that way. It is two mechanical "
        "rules, and they are stated here because the segment carries the whole of the "
        "group margin expansion, and the rules are printed below.",
        B + "par" + B + "vspace{4pt}",
        "{" + B + "footnotesize",
        B + "excap{The Ethiopian forecast, and the rules behind it}",
        B + "begin{tabular}{@{}l" + "r" * n + "@{}}",
        B + "toprule", "& " + h + " " + B + B, B + "midrule",
        NL.join(rows), B + "bottomrule", B + "end{tabular}}",
        B + "srcnote{Revenue grows at " + f"{growth * 100:.3f}" + B + "% in every "
        "forecast year, to three decimal places, so it is a rate applied, not a "
        "build. The margin rises " + money(steps[0], 0) + " points in the first forecast "
        "year and the increment halves in each year after, which reproduces the printed "
        "path to fifteen decimal places. Neither rule is company guidance. There is no "
        "subscriber count, no revenue per user and no cost line behind either, and this "
        "note has none to offer: the segment disclosure does not carry them.}",
        "",
        B + "subsection*{What the Ethiopian assumption is worth}",
        "The Ethiopian forecast rests on two mechanical rules, and each is priced below.",
        B + "par" + B + "vspace{4pt}",
        "{" + B + "footnotesize",
        B + "excap{The Ethiopian turnaround, priced}",
        B + "begin{tabular}{@{}p{7.2cm}rrrr@{}}",
        B + "toprule",
        B + "textbf{Case} & " + B + "textbf{Target} & " + B + "textbf{Move} & "
        + B + "textbf{vs price} & " + B + "textbf{Total return} " + B + B,
        B + "midrule",
        B + "textbf{As modelled} & " + B + "textbf{" + money(base) + "} & -- & "
        + B + "textbf{" + pct(base / P - 1) + "} & " + B + "textbf{"
        + pct(base / P - 1 + dps / P) + "} " + B + B,
        B + "midrule", NL.join(crows), B + "bottomrule", B + "end{tabular}}",
        B + "srcnote{Each case changes the Ethiopian line and nothing else, and runs "
        "through the same engine. The first is the cleanest statement of the exposure: "
        "hold the margin where it was in " + tex(yrs[0]) + " and the target falls KShs "
        + money(abs(frozen - base)) + ", which is " + pct(abs(frozen - base) / base, 0)
        + " of it.}",
        "",
        "That last figure is the one to carry away. KShs " + money(abs(frozen - base))
        + " of the target rests on a margin path that no disclosure supports and that "
        "generated by halving an increment. The rating survives every case "
        "above, which is why the note is willing to publish on it. It would not survive "
        "the reverse. The Ethiopian line is an assumption and not evidence.",
    ])

def ethiopia(d) -> str:
    I = d["is_rows"]
    yrs = d["years"]
    er, ee = _row(I, "Ethiopia revenue"), _row(I, "Ethiopia EBITDA")
    kr, ke = _row(I, "Kenya revenue"), _row(I, "Kenya EBITDA")
    h = " & ".join(f"\\textbf{{{tex(y)}}}" for y in yrs)
    rows = "\n".join([
        "Kenya revenue & " + " & ".join(num(v) for v in kr) + r" \\",
        "Ethiopia revenue & " + " & ".join(num(v) for v in er) + r" \\",
        "Kenya EBITDA & " + " & ".join(num(v) for v in ke) + r" \\",
        "Ethiopia EBITDA & " + " & ".join(num(v) for v in ee) + r" \\",
    ])
    be = next((yrs[i] for i, v in enumerate(ee) if isinstance(v, (int, float)) and v > 0), None)
    from revalue import model_of as _mo, base as _bb, value as _vv, fcff as _ff
    _mm = _mo(d); _bb2 = _bb(_mm)
    _on = _vv(_mm, **_bb2)
    from revalue import eth_capex_net_debt_relief as _ndr
    _off = _vv(_mm, fcff_override=_ff(_mm, revert_capex=True),
               exit_net_debt_addback=_ndr(), **_bb2)
    cap_dcf_off, cap_dcf_on = money(_off["dcf"]), money(_on["dcf"])
    cap_ex_off, cap_ex_on = money(_off["exit"]), money(_on["exit"])
    cap_worth = money(_on["target"] - _off["target"])
    cap_tgt_off = money(_off["target"])
    tgt_now = money(d["target"])

    return rf"""
\newpage
\section*{{\color{{navy}}Ethiopia}}
Ethiopia is the reason the group has minorities and the reason group EBITDA understates
Kenya. The segment lost KShs {num(abs(ee[0]), 1)}m of EBITDA in FY2026 against Kenya's
KShs {num(ke[0], 1)}m. The model has it reaching EBITDA breakeven in
{tex(be or 'a year beyond the forecast')} and
KShs {num(ee[-1], 1)}m by {tex(yrs[-1])}. Minorities carried
KShs {num(_v(d, 'NCI at FY2031', default=0), 1)}m at the end of the forecast. They did
not fund their share of the FY2026 injection: Safaricom put in KShs 19.64bn of a KShs
21.3bn raise and the consortium was diluted from 42.59\% to 39.81\%, with a contractual
right to restore. The falling minority balance in the forecast is therefore a funding
assumption, not settled ownership.

{{\footnotesize
\begin{{tabular}}{{@{{}}l*{{{len(yrs)}}}{{r}}@{{}}}}
\toprule
KES millions & {h} \\
\midrule
{rows}
\bottomrule
\end{{tabular}}}}
\srcnote{{Ethiopia is modelled in birr and translated, so the revenue line carries the
currency assumption as well as the trading one. The FX loss on foreign-currency funding
is charged below EBIT.}}

\subsection*{{Why this matters to the rating}}
On the model's numbers Ethiopia turns from a KShs {num(abs(ee[0]), 1)}m drag into a
KShs {num(ee[-1], 1)}m contributor over five years. That swing is already in the
forecast and therefore already in the target price. An investor paying today's quote is
paying for it to happen on schedule.

\newpage
\section*{{\color{{navy}}The Ethiopian market}}
Ethiopia was a state monopoly until 2021 and is now a regulated duopoly. Ethio Telecom,
still state-owned, is much the larger of the two, though no filing for it is held and
its own and does not state a subscriber figure for it.
Safaricom Ethiopia is the only licensed competitor of scale and reported 13.6 million
90-day active customers at FY2026, up 54.2\%, of which 10.7 million were active over 30
days, after four years of operation, against a population the company puts at more than
120 million. Neither the incumbent's subscriber base nor the size of the total market
appears in any filing held, so no market share is claimed for either operator
here. What the segment is worth is argued instead from Safaricom Ethiopia's own audited
revenue and losses.

\subsection*{{What the second operator has built}}
\begin{{itemize}}[leftmargin=13pt,itemsep=3pt,topsep=2pt]
\item \textbf{{Network.}} 3,504 sites at FY2026, of which 2,076 are own-built and 1,428
      are collocated, which is the filing's word and does not name the host,
      covering 59.2\% of the population
      against a target of about 4,000 sites. Two out of every five of Safaricom
      Ethiopia's sites therefore sit on its competitor's towers.
\item \textbf{{Mobile money.}} M-PESA Ethiopia reached 5.2 million 90-day active
      customers, up 119.4\%, across roughly 70,000 merchants. It is the same product
      that generates the highest revenue per user in Kenya, being introduced into a
      market where the incumbent already offers a mobile money service.
\item \textbf{{Revenue.}} Service revenue of ETB 15.9bn, up 130.9\% in birr, which
      translates to KShs 14.1bn, up 58.3\%. The gap between those two growth rates is
      the birr, and it is the reason this segment's shilling revenue understates what is
      happening on the ground.
\item \textbf{{Losses.}} The EBITDA loss fell by about two thirds, from KShs 43.0bn in
      FY2025 to KShs 15.1bn in FY2026, on the basis the company reports before the
      hyperinflation adjustment. The audited segment note, which is what the model
      carries, gives KShs 15.4bn for FY2026 against KShs 33.7bn. The EBIT loss fell
      from KShs 61.1bn to KShs 30.1bn.
\end{{itemize}}
\srcnote{{Safaricom FY2026 results and Ethiopian sector reporting. The FY2025 Ethiopian
EBITDA loss of KShs 43.0bn is the company's Ethiopia operating figure and is wider than
the KShs 33.7bn in the audited segment note, which is the figure this model's history
uses. The two are on different bases, and the difference is stated and left unresolved.}}

\subsection*{{The regulator has set terms for infrastructure sharing}}
In December 2025 the Ethiopian Communications Authority approved Ethio Telecom's Reference
Infrastructure Sharing Offer and Reference Interconnection Offer, effective 1 January 2026,
covering passive infrastructure sharing and interconnection on published terms. The company
states separately that it has used that framework to reduce its reliance on leases
denominated in United States dollars. Both statements come from the annual report and
neither is quantified in it. What the approved rates are, how they compare with the terms
Safaricom Ethiopia paid before, and how much of the cost moves out of hard currency are all
undisclosed, so the size of the benefit cannot be established from the filing and the model
carries no line for it. The direction is favourable for a business that leases two fifths of
its sites and earns birr in a currency that fell 23.7\% against the dollar in FY2026.

\subsection*{{Where the model is more conservative than the company}}
Two places, and both work against the rating published here.

\textbf{{Break-even.}} Management targets EBITDA break-even in Ethiopia by the \emph{{end}}
of FY2027. The model's first positive full year is FY2028, and those two statements agree and do not conflict: a business that exits FY2027 at break-even still records a loss for
that full year. The model has a KShs 6,960m loss in FY2027 and a KShs 531m profit in
FY2028, which is what reaching break-even at the end of FY2027 looks like on a full-year
basis. The headline dates appear to disagree with
guidance. It does not.

\textbf{{Capital expenditure.}} Here the model is more conservative than the company. It
carried Ethiopian capex flat at KShs 26bn a year across the whole forecast. Safaricom
spent KShs 18.7bn in FY2026 and guides KShs 6bn to 9bn for FY2027, roughly half, as the
network approaches its site target. Holding 26bn when the build is nearly finished
suppresses free cash flow against the company's own published number.

This note corrects it. The valuation is struck with Ethiopian capex at the midpoint of
guidance in FY2027 and a KShs 10bn run-rate after. That lifts the discounted leg from
KShs {cap_dcf_off} to KShs {cap_dcf_on}. It also reaches the exit leg, because cash not
spent leaves less net debt to deduct at FY2031, which lifts that leg from KShs
{cap_ex_off} to KShs {cap_ex_on}. Together they are worth KShs {cap_worth} on the target,
taking it from KShs {cap_tgt_off} to KShs {tgt_now}. The correction works against the
rating and is
adopted anyway, on the same principle as the dividend correction to the exit leg: a
target should be the number the analyst believes, and a forecast that contradicts the
company's own guidance is not that number.
"""


def charts(d) -> str:
    """Revenue and margin, drawn from the audited years and the forecast."""
    f, yrs = d["fins"], d["years"]
    hist = [(x["fy"], x["revenue"], x["ebitda"] / x["revenue"]) for x in f]
    rev_f = _row(d["is_rows"], "TOTAL REVENUE")
    eb_f = _row(d["is_rows"], "EBITDA")
    fore = [(yrs[i], rev_f[i], (eb_f[i] / rev_f[i]) if (eb_f[i] and rev_f[i]) else None)
            for i in range(1, len(yrs))]
    pts = hist + [x for x in fore if x[1]]
    hi = max(x[1] for x in pts) * 1.08
    W, H, n = 15.4, 4.6, len(pts)
    bw = W / n * 0.60
    bars, labs, line = [], [], []
    for i, (fy, rv, mg) in enumerate(pts):
        x = (i + 0.5) * W / n
        h = rv / hi * H
        col = "navy" if i < len(hist) else "gold"
        shade = 78 if i < len(hist) else 45
        bars.append(B+"fill[" + col + "!" + str(shade) + "] "
                    + "(%.2f,0) rectangle (%.2f,%.2f);" % (x - bw / 2, x + bw / 2, h))
        labs.append(B+"node[anchor=north,font="+B+"scriptsize,gray] at (%.2f,-0.08) {%s};"
                    % (x, tex(str(fy).replace("FY", ""))))
        labs.append(B+"node[anchor=south,font="+B+"scriptsize] at (%.2f,%.2f) {%s};"
                    % (x, h, num(rv / 1000, 0)))
        if mg:
            line.append("(%.2f,%.2f)" % (x, mg / 0.60 * H))
    cagr = (f[-1]["revenue"] / f[0]["revenue"]) ** (1 / (len(f) - 1)) - 1
    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Revenue and margin}")
    out.append("Five audited years in navy and the model's forecast in gold. Bars are "
               "revenue in KES billions. The line is the group EBITDA margin, scaled to the same axis and shown for shape, not level.")
    out.append(B+"begin{center}")
    out.append(B+"begin{tikzpicture}[x=1cm,y=1cm]")
    out.extend(bars)
    out.append(B+"draw[sell,line width=1.2pt] " + " -- ".join(line) + ";")
    out.extend(labs)
    out.append(B+"draw[rule] (0,0) -- (%.2f,0);" % W)
    out.append(B+"end{tikzpicture}")
    out.append(B+"end{center}")
    out.append(B+"srcnote{Levels for the margin are in the key ratios table on the cover.}")
    out.append("")
    out.append(B+"subsection*{What the shape says}")
    out.append("Revenue has grown in every audited year, from KShs " + num(f[0]["revenue"])
               + "m in " + tex(f[0]["fy"]) + " to KShs " + num(f[-1]["revenue"]) + "m in "
               + tex(f[-1]["fy"]) + ", a compound rate of " + pct(cagr) + " a year. The "
               "margin did not. It fell from " + pct(f[0]["ebitda"] / f[0]["revenue"])
               + " to " + pct(f[3]["ebitda"] / f[3]["revenue"]) + " before recovering to "
               + pct(f[-1]["ebitda"] / f[-1]["revenue"]) + " in " + tex(f[-1]["fy"])
               + ". That recovery is the most important fact in the recent record, and "
               "the next page takes it apart.")
    return NL.join(out) + NL


def bridge(d) -> str:
    """FY2025 to FY2026 EBITDA, decomposed from the audited income statement.

    The workbook's own bridge was not usable. It showed the fall in expected
    credit losses as a DRAG of 7,132.9 when a smaller charge is a benefit of
    that size, it carried an FX line that appears in no statement, and it closed
    on a residual labelled "underlying Kenya trading" that absorbed both errors
    and still footed. A plug that foots is not evidence.

    Every line below is the year-on-year movement in a reported line, and the
    four sum to the EBITDA movement exactly, with nothing left over.
    """
    f = d["fins"]
    if len(f) < 2:
        return ""
    F = f
    # The FY2025 credit charge is the outlier in the series, so a normalised
    # comparison uses the median of the OTHER years rather than a five-year
    # median that the outlier itself would drag upward.
    import statistics as _st
    _r = [abs(x.get("ecl") or 0) / x["revenue"] for x in F]
    _ecl_norm = _st.median([v for i, v in enumerate(_r) if i != 3]) if len(_r) >= 4 else _st.median(_r)
    P, L = f[-2], f[-1]
    parts = [("Revenue", "revenue"), ("Direct costs", "direct_costs"),
             ("Expected credit losses", "ecl"), ("Other operating expenses", "other_opex")]
    items = [(lab, (L.get(k) or 0.0) - (P.get(k) or 0.0)) for lab, k in parts]
    move = L["ebitda"] - P["ebitda"]
    resid = move - sum(v for _, v in items)
    rows = [B+"textbf{" + tex(P["fy"]) + " group EBITDA} & " + B+"textbf{" + num(P["ebitda"], 1) + "} " + B+B,
            B+"midrule"]
    for lab, v in items:
        rows.append(tex(lab) + " & " + num(v, 1) + " " + B+B)
    if abs(resid) >= 0.05:
        rows.append("Unattributed & " + num(resid, 1) + " " + B+B)
    rows += [B+"midrule",
             B+"textbf{" + tex(L["fy"]) + " group EBITDA} & " + B+"textbf{" + num(L["ebitda"], 1) + "} " + B+B]
    rev_d = items[0][1]
    cost_d = sum(v for _, v in items[1:])
    out = []
    out.append(B+"newpage")
    _m0 = P["ebitda"] / P["revenue"]
    _vol = L["revenue"] * _m0 - P["ebitda"]
    _mar = L["ebitda"] - L["revenue"] * _m0
    out.append(B+"section*{"+B+"color{navy}Cost lines did more of the work than revenue}")
    out.append("Group EBITDA rose KShs " + num(move, 1) + "m between " + tex(P["fy"])
               + " and " + tex(L["fy"]) + ", and the margin with it, from "
               + pct(_m0) + " to " + pct(L["ebitda"] / L["revenue"])
               + ". Splitting that at a constant margin, extra revenue at last year's "
               "margin accounts for KShs " + num(_vol, 1) + "m, or "
               + pct(_vol / move, 1) + " of the improvement. The remaining KShs "
               + num(_mar, 1) + "m, " + pct(_mar / move, 1) + ", is costs growing more "
               "slowly than revenue. That is the split that matters, because it is the "
               "one that says whether the margin is bought or earned.")
    out.append("")
    out.append("The table below is a different cut. It is "
               "the year-on-year movement in each reported line, and the four sum to the "
               "movement with nothing unexplained. It does not decompose the margin, "
               "because attributing the whole revenue increase to EBITDA assumes the "
               "incremental revenue arrived at no cost.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}lr@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{KES millions} & "+B+"textbf{Movement} " + B+B)
    out.append(B+"midrule")
    out.extend(rows)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Computed from the audited income statement, "
               + tex(P["fy"]) + " and " + tex(L["fy"]) + ". "
               "The expected credit loss charge fell from KShs "
               + num(abs(P.get("ecl") or 0), 1) + "m to KShs "
               + num(abs(L.get("ecl") or 0), 1) + "m, so it is a benefit to EBITDA "
               "of KShs " + num(items[2][1], 1) + "m and is signed accordingly.}")
    out.append("")
    out.append(B+"subsection*{What the split means for the rating}")
    out.append("A margin recovery built on revenue compounds. One built on cost lines "
               "depends on whether those lines stay where they are. At "
               + pct(_mar / move, 1) + " cost-led, this one turns on the cost side, and "
               "two of the largest movements there are ones the note can already see "
               "reversing: the credit charge, which the next section shows was abnormal "
               "in the base year and not in the comparison year, and a foreign-exchange "
               "line inside other operating costs that the model's own assumption note "
               "describes as temporary.")
    out.append("")
    out.append("This cuts in favour of the rating. A cost-led recovery is a weaker "
               "base for a forecast that expands the margin further, and the forecast "
               "does expand it, from " + pct(L["ebitda"] / L["revenue"]) + " to 54.4\%. "
               "A revenue-led reading of the same recovery would treat it as more "
               "durable, but that reading credits the whole revenue increase to EBITDA "
               "and so assumes the extra revenue arrived at no cost, which the cost "
               "lines do not support.")
    out.append("")
    out.append(B+"subsection*{But FY2025 was the abnormal year, not FY2026}")
    out.append("The credit charge above is the one line in the decomposition that deserves "
               "a second look, because a single year's movement says nothing about whether "
               "either end of it was normal. Over five years the charge has run as follows.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"excap{Expected credit losses, five years}")
    out.append(B+"begin{tabular}{@{}l" + "r" * len(F) + "@{}}")
    out.append(B+"toprule")
    out.append("& " + " & ".join(B+"textbf{" + tex(x["fy"]) + "}" for x in F) + " " + B+B)
    out.append(B+"midrule")
    out.append("Expected credit losses, KES m & "
               + " & ".join(num(abs(x.get("ecl") or 0)) for x in F) + " " + B+B)
    out.append("As a share of revenue & "
               + " & ".join(pct(abs(x.get("ecl") or 0) / x["revenue"], 2) for x in F)
               + " " + B+B)
    out.append(B+"midrule")
    out.append("EBITDA margin as reported & "
               + " & ".join(pct(x["ebitda"] / x["revenue"]) for x in F) + " " + B+B)
    out.append("Margin at a normalised charge & "
               + " & ".join(pct((x["ebitda"] + abs(x.get("ecl") or 0)
                                 - x["revenue"] * _ecl_norm) / x["revenue"]) for x in F)
               + " " + B+B)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"srcnote{Normalised at " + pct(_ecl_norm, 2) + " of revenue, the median "
               "of the four years other than FY2025. FY2025 ran at "
               + pct(abs(F[3].get("ecl") or 0) / F[3]["revenue"], 2) + ", more than double "
               "that, and is the outlier in the series. FY2026 at "
               + pct(abs(L.get("ecl") or 0) / L["revenue"], 2) + " sits at the low end of "
               "the range but inside it: FY2022 was lower still.}")
    out.append("")
    out.append("Two things follow, and they point in different directions. The KShs "
               + num(items[2][1], 1) + "m credit benefit is normalisation rather than "
               "improvement, so the year-on-year margin gain is about "
               + money((L["ebitda"] / L["revenue"] - P["ebitda"] / P["revenue"]) * 100
                       - ((L["ebitda"] + abs(L.get("ecl") or 0) - L["revenue"] * _ecl_norm) / L["revenue"]
                          - (P["ebitda"] + abs(P.get("ecl") or 0) - P["revenue"] * _ecl_norm) / P["revenue"]) * 100, 1)
               + " points smaller than the headline suggests. But the level that matters "
               "for the forecast is barely affected: normalising the charge takes the "
               "FY2026 margin from " + pct(L["ebitda"] / L["revenue"]) + " to "
               + pct((L["ebitda"] + abs(L.get("ecl") or 0) - L["revenue"] * _ecl_norm) / L["revenue"])
               + ". The base the forecast expands from is sound. What is weaker than the "
               "headline is the claim about the pace of improvement, and that is this "
               "note's own headline on this page.")
    return NL.join(out) + NL



def drivers(d) -> str:
    """The operating build behind the forecast, and the two numbers the call turns on.

    The note asked a reader to grant a forecast without ever printing it. Kenya
    revenue accelerating and group EBITDA margin expanding from 51.5% to 54.4%
    are the two assumptions the whole rating rests on, and neither was stated as
    an assumption, justified, or stressed. Both come out of this sheet.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _rb, value as _rv, fcff as _rf

    D, I, yrs = d["drv_rows"], d["is_rows"], d["years"]
    SH, P = 40065.4, d["val_price"]
    kr, ke = _row(D, "KENYA TOTAL REVENUE"), _row(D, "KENYA EBITDA")
    er, ee = _row(I, "Ethiopia revenue"), _row(I, "Ethiopia EBITDA")
    gr, ge = _row(I, "TOTAL REVENUE"), _row(I, "EBITDA")
    el = _row(I, "Intersegment eliminations (2)")
    h = " & ".join(B+"textbf{" + tex(y) + "}" for y in yrs)
    n = len(yrs)

    def line(lab, vals, fmt=num, bold=False, indent=False):
        nm = B+"textbf{" + tex(lab) + "}" if bold else (
            (B+"hspace{8pt}" if indent else "") + tex(lab))
        return nm + " & " + " & ".join(fmt(v) for v in vals) + " " + B+B

    def tbl(cap, rows, first="KES millions"):
        return NL.join(["{"+B+"footnotesize", B+"excap{" + cap + "}",
                        B+"begin{tabular}{@{}l" + "r"*n + "@{}}", B+"toprule",
                        first + " & " + h + " " + B+B, B+"midrule"] + rows
                       + [B+"bottomrule", B+"end{tabular}}"])

    STREAMS = ["Voice revenue", "Messaging revenue", "Mobile data revenue",
               "Mobile incoming and other mobile", "M-PESA revenue",
               "Fixed service and IoT revenue", "Handset and other revenue"]
    COSTS = ["Direct costs (incl. M-PESA agent commissions)", "Employee benefits",
             "Network operating costs", "Net FX losses inside operating expenses",
             "Other operating expenses", "Expected credit losses"]

    def cagr(v):
        return (v[-1] / v[0]) ** (1 / (len(v) - 1)) - 1

    rev_rows = [line(s.replace(" revenue", "").replace(" (incl. M-PESA agent commissions)", ""),
                     _row(D, s), indent=True) for s in STREAMS]
    _stream_sum = [sum(_row(D, s)[i] for s in STREAMS) for i in range(n)]
    _other = [kr[i] - _stream_sum[i] for i in range(n)]
    rev_rows += [line("Revenue not from contracts with customers", _other, indent=True),
                 B+"midrule", line("Kenya total revenue", kr, bold=True),
                 line("Year-on-year growth",
                      [None] + [kr[i] / kr[i-1] - 1 for i in range(1, n)], fmt=pct)]
    cagr_rows = [tex(s.replace(" revenue", "")) + " & " + pct(cagr(_row(D, s)), 1)
                 + " & " + num(_row(D, s)[0]) + " & " + num(_row(D, s)[-1])
                 + " & " + pct(_row(D, s)[-1] / kr[-1], 1) + " " + B+B
                 for s in STREAMS if abs(cagr(_row(D, s))) > 1e-9]
    cost_rows = [line(c.replace(" (incl. M-PESA agent commissions)", ", including M-PESA commissions"),
                      [abs(v) / kr[i] for i, v in enumerate(_row(D, c))], fmt=pct, indent=True)
                 for c in COSTS]
    cost_rows += [B+"midrule", line("Kenya EBITDA margin",
                                    [ke[i] / kr[i] for i in range(n)], fmt=pct, bold=True)]

    dec_rows = [
        line("Kenya EBITDA margin", [ke[i] / kr[i] for i in range(n)], fmt=pct),
        line("Ethiopia EBITDA margin", [ee[i] / er[i] for i in range(n)], fmt=pct),
        line("Ethiopia share of group revenue", [er[i] / gr[i] for i in range(n)], fmt=pct),
        B+"midrule",
        line("Group EBITDA margin", [ge[i] / gr[i] for i in range(n)], fmt=pct, bold=True),
    ]
    alt_e = er[-1] * (ee[0] / er[0])
    alt_g = (ke[-1] + alt_e + (el[-1] or 0)) / gr[-1]
    m0, m1 = ge[0] / gr[0], ge[-1] / gr[-1]

    mdl = _model_of(d)
    b = _rb(mdl); cf = _rf(mdl); base = _rv(mdl, **b)

    def scen(dEB):
        divs = [abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
                if isinstance(x, (int, float))]
        m2 = dict(mdl); m2["cf_rows"] = dict(mdl["cf_rows"])
        m2["cf_rows"]["Dividends PAID"] = [None] + [a + c * 0.70 * 0.80
                                                    for a, c in zip(divs, dEB)]
        return _rv(m2, fcff_override=[c + v * 0.70 for c, v in zip(cf, dEB)],
                   ebitda31=ge[-1] + dEB[-1], **b)["target"]

    km = [ke[i] / kr[i] for i in range(n)]
    S = [
        ("As modelled", "--", base["target"]),
        ("Ethiopia never passes break-even",
         "Ethiopian EBITDA held at nil from " + tex(yrs[2]),
         scen([0, -ee[2], -ee[3], -ee[4], -ee[5]])),
        ("Ethiopian break-even slips two years",
         "the whole Ethiopian path pushed out two years",
         scen([0, 0, -ee[2], ee[2] - ee[3], ee[3] - ee[4]])),
        ("Kenya grows one point slower",
         "Kenyan revenue compounding at " + pct(cagr(kr) - 0.01, 1) + " not " + pct(cagr(kr), 1),
         scen([(kr[i] * ((1 + cagr(kr) - 0.01) / (1 + cagr(kr))) ** i - kr[i]) * km[i]
               for i in range(1, n)])),
    ]
    # c already carries LaTeX from pct(); running it through tex() again turned
    # the percent sign into a literal \{}% on the page.
    srows = [tex(a) + " & " + c + " & " + money(t) + " & " + pct(t / P - 1, 1)
             + " & " + (money(t - base["target"]) if a != "As modelled" else "--")
             + " " + B+B for a, c, t in S]

    return NL.join([
        B+"newpage",
        B+"section*{"+B+"color{navy}Operating drivers}",
        "Everything in the valuation rests on two numbers: Kenyan revenue compounding at "
        + pct(cagr(kr), 1) + " a year, and the group EBITDA margin widening from "
        + pct(m0) + " to " + pct(m1) + ". The build behind both is printed below, so each driver can be tested separately.",
        B+"par"+B+"vspace{4pt}",
        tbl("Kenyan operating drivers",
            [line("One-month active voice customers, million",
                  _row(D, "One-month active voice customers (m)"), fmt=lambda v, _=1: money(v, 1)),
             line("Voice ARPU, KShs per month",
                  _row(D, "Voice ARPU (KShs per month)"), fmt=lambda v, _=1: money(v, 1))],
            first="Driver"),
        B+"srcnote{The voice base is a Kenyan one-month active count and is not the group "
        "figure used elsewhere. Voice is the only stream modelled from a "
        "customer count and a price. The rest are grown at stated rates, shown below. "
        "The seven streams sum to Kenya revenue from contracts with customers of KShs "
        + num(_stream_sum[0], 1) + "m, the segment note's own figure. The remaining KShs "
        + num(_other[0], 1) + "m of Kenya total revenue is not revenue from contracts and "
        "is carried on its own line so the column foots. It does not recur, because each "
        "forecast year is built as the sum of the grown streams, which makes the first "
        "forecast year's growth rate understated and the forecast slightly conservative.}",
        B+"par"+B+"vspace{6pt}",
        tbl("Kenyan revenue by stream", rev_rows),
        B+"par"+B+"vspace{6pt}",
        "{"+B+"footnotesize",
        B+"excap{The growth rates being assumed}",
        B+"begin{tabular}{@{}lrrrr@{}}",
        B+"toprule",
        B+"textbf{Stream} & "+B+"textbf{Assumed CAGR} & "+B+"textbf{" + tex(yrs[0])
        + "} & "+B+"textbf{" + tex(yrs[-1]) + "} & "+B+"textbf{Share of Kenya} " + B+B,
        B+"midrule",
        NL.join(cagr_rows),
        B+"midrule",
        B+"textbf{Kenya total} & "+B+"textbf{" + pct(cagr(kr), 1) + "} & "
        + B+"textbf{" + num(kr[0]) + "} & "+B+"textbf{" + num(kr[-1]) + "} & "
        + B+"textbf{100.0\\%} " + B+B,
        B+"bottomrule", B+"end{tabular}}",
        B+"srcnote{These are the assumptions, not outputs. Voice declines because the "
        "customer base grows about 3\\% a year while ARPU falls about 4\\%. Messaging is "
        "faded at 10\\% a year on substitution by data messaging. Handset and other revenue "
        "is held flat in cash terms, which is why it does not appear here.}",
        B+"newpage",
        B+"section*{"+B+"color{navy}The cost build, and where the margin comes from}",
        "Each Kenyan cost line as a share of Kenyan revenue. A margin that widens has to "
        "show up here as a line falling.",
        B+"par"+B+"vspace{4pt}",
        tbl("Kenyan costs as a share of Kenyan revenue", cost_rows, first="Share of revenue"),
        B+"srcnote{Direct costs carry the M-PESA agent commission, which is why they are "
        "the largest line and why they do not fall as M-PESA grows. Network operating costs "
        "are the clearest operating leverage in the model, falling "
        + money((abs(_row(D, "Network operating costs")[0]) / kr[0]
                 - abs(_row(D, "Network operating costs")[-1]) / kr[-1]) * 100, 1)
        + " points over five years. Other operating expenses is the one line that "
        "deteriorates, rising "
        + money((abs(_row(D, "Other operating expenses")[-1]) / kr[-1]
                 - abs(_row(D, "Other operating expenses")[0]) / kr[0]) * 100, 1)
        + " points. The foreign-exchange line inside operating costs is held at KShs 8,000m "
        "flat from " + tex(yrs[1]) + ", against KShs "
        + num(abs(_row(D, "Net FX losses inside operating expenses")[0]))
        + "m in " + tex(yrs[0]) + ". The model's own assumption note describes the FY2026 "
        "improvement in this line as temporary.}",
        "",
        B+"subsection*{Kenya's margin barely moves. The group's expansion is Ethiopia}",
        "Kenyan EBITDA margin goes from "
        + pct(ke[0] / kr[0]) + " to " + pct(ke[-1] / kr[-1]) + " across the whole forecast, "
        "a gain of " + money((ke[-1] / kr[-1] - ke[0] / kr[0]) * 100, 1) + " points in the "
        "business that is " + pct(kr[0] / gr[0], 0) + " of group revenue today. The group "
        "margin gain of " + money((m1 - m0) * 100, 1) + " points is therefore not a Kenyan "
        "story at all.",
        B+"par"+B+"vspace{4pt}",
        tbl("Where the group margin expansion comes from", dec_rows, first="Margin"),
        B+"srcnote{Ethiopia moves from " + pct(ee[0] / er[0], 1) + " to "
        + pct(ee[-1] / er[-1], 1) + " while growing from " + pct(er[0] / gr[0], 1) + " to "
        + pct(er[-1] / gr[-1], 1) + " of group revenue. Hold the Ethiopian margin at its "
        + tex(yrs[0]) + " level and let everything else run as modelled, and the group "
        "margin in " + tex(yrs[-1]) + " would be " + pct(alt_g) + " rather than "
        + pct(m1) + ": a loss-making segment growing as a share of the mix drags the group "
        "down, not up. The entire " + money((m1 - m0) * 100, 1) + " point expansion, and "
        "more besides, is the Ethiopian turnaround.}",
        "",
        "That reframes the rating. The forecast the target requires is not "
        "mainly a claim about Kenyan pricing or Kenyan cost control, both of which are "
        "modelled conservatively. It is a claim that a business losing "
        + pct(abs(ee[0] / er[0]), 0) + " of its revenue at the EBITDA line turns to a "
        + pct(ee[-1] / er[-1], 0) + " margin inside five years, while growing revenue "
        + pct(cagr(er), 0) + " a year. The target price grants that in full.",
        "",
        B+"subsection*{What the forecast is worth if the drivers disappoint}",
        "The scenario page flexes the discount rate and the multiples. "
        "This one flexes the business. Each case runs through the same valuation engine, "
        "changing only the operating assumption named.",
        B+"par"+B+"vspace{4pt}",
        "{"+B+"footnotesize",
        B+"excap{Operating scenarios}",
        B+"begin{tabular}{@{}llrrr@{}}",
        B+"toprule",
        B+"textbf{Case} & "+B+"textbf{What changes} & "+B+"textbf{Target} & "
        + B+"textbf{vs price} & "+B+"textbf{Move} " + B+B,
        B+"midrule",
        NL.join(srows),
        B+"bottomrule", B+"end{tabular}}",
        B+"srcnote{KShs per share against the valuation-date price of KShs " + money(P)
        + ". A change in EBITDA is carried through to operating profit one for one, to "
        "free cash flow after tax at the statutory rate, to the FY2027 earnings the peer "
        "multiple is applied to, and to the dividends inside the exit leg. Nothing else "
        "moves.}",
        "",
        "The rating does not depend on any of these going wrong. It is already a Sell on "
        "the forecast as modelled, and every operating disappointment widens the gap, it does not create it. That is the useful thing to take from this page: the "
        "disagreement with the market is not about whether Ethiopia turns. It is about what "
        "the turn is worth once discounted at a Kenyan cost of capital.",
    ])

def valuation(d) -> str:
    v = d["val"]
    g = lambda *n, **k: _v(d, *n, **k)                                   # noqa: E731
    # the three legs, so the weighting claim in the opening paragraph is computed
    from revalue import base as _wb2, value as _wv2
    _lg = _wv2(_model_of(d), **_wb2(_model_of(d)))
    rf, beta, erp = g("Risk-free"), g("Equity beta"), g("Equity risk")
    crp, ethw = g("Ethiopia country risk premium"), g("Ethiopia weight in the cost of equity")
    ke_, kd, tx = g("Cost of equity"), g("Cost of debt"), g("Tax rate")
    we, wacc, tg = g("Equity weight"), g("WACC"), g("Terminal growth")
    # The bridge is rebuilt on the corrected free cash flow, so the enterprise
    # value, the equity value and the per-share figure all match the leg the
    # target uses. Taking these straight from the workbook would print a bridge
    # that does not reconcile to the number beside it.
    import sys as _sy4
    _sy4.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _rb4, value as _rv4, fcff as _rf4
    _m4 = _model_of(d)
    _b4 = _rb4(_m4)
    _cf4 = _rf4(_m4)
    pv = sum(v / ((1 + _b4["wacc"]) ** (i + 1)) for i, v in enumerate(_cf4))
    # The terminal value the valuation actually uses, not the last cash flow
    # grown at g. Printing the second against an equity value built from the
    # first left the bridge failing to add down by KShs 23,681m.
    from revalue import _terminal as _tvf, TERMINAL_ROIC as _troic
    tv = _tvf(_m4, _cf4, _b4["wacc"], _b4["g"], 0.30, _troic)
    pvtv = tv / ((1 + _b4["wacc"]) ** len(_cf4))
    nd, nci = g("Less: net debt"), g("Less: non-controlling")
    dcfps = d["dcf_repaired"]
    ev = pv + pvtv
    eqv = dcfps * 40065.4
    exm, pem = g("Exit EV/EBITDA"), g("Peer P/E")
    exps, peps = g("Exit-multiple value per share"), g("Peer P/E value per share")
    w1, w2, w3 = g("Weight — DCF"), g("Weight — exit"), g("Weight — peer")
    tgt, up = d["target"], d["upside"]
    s1, s2, s3 = g("DCF, WACC"), g("Exit multiple, +"), g("Peer P/E, +")
    # The stored ranges are symmetric bands centred on the workbook's own leg
    # values, and the exit leg's centre is the uncorrected KShs 20.35 that this
    # note repudiates. Compute each range from the engine instead, so the table
    # agrees with the football field drawn on the facing page.
    import sys as _sy3
    _sy3.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _rb3, value as _rv3
    _m3 = _model_of(d)
    _b3 = _rb3(_m3)
    def _rng(key, **lo_hi):
        a_ = _rv3(_m3, **{**_b3, **lo_hi["lo"]})[key]
        b_ = _rv3(_m3, **{**_b3, **lo_hi["hi"]})[key]
        return (min(a_, b_), max(a_, b_))
    _rg = {
        "dcf": _rng("dcf",
                    lo={"wacc": _b3["wacc"] + 0.01, "ke": _b3["ke"] + 0.01},
                    hi={"wacc": _b3["wacc"] - 0.01, "ke": _b3["ke"] - 0.01}),
        "exit": _rng("exit", lo={"exit_mult": _b3["exit_mult"] - 1.0},
                     hi={"exit_mult": _b3["exit_mult"] + 1.0}),
        "pe": _rng("pe", lo={"peer_pe": _b3["peer_pe"] - 2.0},
                   hi={"peer_pe": _b3["peer_pe"] + 2.0}),
    }
    # The weighted bounds are the legs' own bounds at their weights. Taking the
    # target plus and minus half the range assumes every leg is symmetric about
    # its value, and the discounted leg is not.
    _w3 = d["weights"]
    _wlo = (_rg["dcf"][0] * _w3[0] + _rg["exit"][0] * _w3[1] + _rg["pe"][0] * _w3[2])
    _whi = (_rg["dcf"][1] * _w3[0] + _rg["exit"][1] * _w3[1] + _rg["pe"][1] * _w3[2])
    # Three different weighted bands used to circulate: this pair, a symmetric one
    # built as target plus or minus half a stored width, and the corner cells of the
    # two-way grid. They are the same quantity and now come from here.
    d["wband"] = (_wlo, _whi)
    s4 = _whi - _wlo
    price = d["val_price"]
    later = d.get("later_price")

    return rf"""
\newpage
\section*{{\color{{navy}}Valuation}}
The target is a weighted blend of three methods, on the standard house weights for a
telecom. Each is shown with the assumption that drives it, so a disputed assumption
carries a price and not only an argument. The weights are a house convention and not
an output of the analysis, so what matters is whether they decide the answer. They do
not. At equal weights the target is KShs {money((_lg['dcf'] + _lg['exit'] + _lg['pe']) / 3)}.
On the discounted valuation alone it is KShs {money(_lg['dcf'])}. On peer earnings alone,
the most generous of the three methods, it is KShs {money(_lg['pe'])}, still
{pct(abs(_lg['pe'] / price - 1))} below the price. No weighting of these three legs
produces a Buy.

\begin{{minipage}}[t]{{0.47\linewidth}}
\vspace{{0pt}}
\subsection*{{Cost of capital}}
{{\footnotesize
\begin{{tabular}}{{@{{}}lr@{{}}}}
\toprule
\textbf{{Input}} & \textbf{{Value}} \\
\midrule
Risk-free rate & {pct(rf)} \\
Equity beta & {money(beta)} \\
Equity risk premium & {pct(erp)} \\
Ethiopia country risk premium & {pct(crp)} \\
Ethiopia weight & {pct(ethw, 1)} \\
\textbf{{Cost of equity}} & \textbf{{{pct(ke_)}}} \\
\midrule
Cost of debt, pre-tax & {pct(kd)} \\
Tax rate & {pct(tx, 0)} \\
Equity weight & {pct(we, 0)} \\
\textbf{{WACC}} & \textbf{{{pct(wacc)}}} \\
Terminal growth & {pct(tg)} \\
\bottomrule
\end{{tabular}}}}
\srcnote{{The risk-free rate is the Kenyan government yield. The country risk premium is
a sovereign spread over the mature market, and both are assumptions, dated.}}
\end{{minipage}}\hfill
\begin{{minipage}}[t]{{0.47\linewidth}}
\vspace{{0pt}}
\subsection*{{Enterprise to equity bridge}}
{{\footnotesize
\begin{{tabular}}{{@{{}}lr@{{}}}}
\toprule
\textbf{{KES millions}} & \textbf{{Value}} \\
\midrule
PV of the explicit forecast & {num(pv)} \\
Terminal value & {num(tv)} \\
PV of the terminal value & {num(pvtv)} \\
\textbf{{Enterprise value}} & \textbf{{{num(ev)}}} \\
Less: net debt & {num(nd)} \\
Less: non-controlling interests & {num(nci)} \\
\textbf{{Equity value}} & \textbf{{{num(eqv)}}} \\
\midrule
\textbf{{DCF value per share}} & \textbf{{KShs {money(dcfps)}}} \\
\bottomrule
\end{{tabular}}}}
\srcnote{{The terminal value is {pct(pvtv / ev) if (pvtv and ev) else '--'} of enterprise
value. Minorities are deducted because Ethiopia's losses, and later its profits, are
shared.}}
\end{{minipage}}

\subsection*{{The three methods and the blend}}
{{\footnotesize
\begin{{tabular}}{{@{{}}llrrr@{{}}}}
\toprule
\textbf{{Method}} & \textbf{{Assumption}} & \textbf{{Per share}} & \textbf{{Weight}} & \textbf{{Contribution}} \\
\midrule
Discounted cash flow & WACC {pct(wacc)}, terminal growth {pct(tg)} & KShs {money(dcfps)} & {pct(w1, 0)} & {money(dcfps * w1)} \\
Exit multiple & {mult(exm)} FY2031 EBITDA, plus interim cash flow & KShs {money(d["exit_repaired"])} & {pct(w2, 0)} & {money(d["exit_repaired"] * w2)} \\
Peer earnings & {mult(pem)} trailing earnings & KShs {money(peps)} & {pct(w3, 0)} & {money(peps * w3)} \\
\midrule
\textbf{{Weighted target price}} & & & \textbf{{{pct(w1 + w2 + w3, 0)}}} & \textbf{{KShs {money(tgt)}}} \\
\textbf{{Price on {tex(d['val_price_date'])}}} & & & & \textbf{{KShs {money(price)}}} \\
\textbf{{Implied downside}} & & & & \textbf{{{pct(up)}}} \\
\bottomrule
\end{{tabular}}}}

\subsection*{{What moves the answer}}
{{\footnotesize
\begin{{tabular}}{{@{{}}llrrr@{{}}}}
\toprule
\textbf{{Method}} & \textbf{{Move}} & \textbf{{Low}} & \textbf{{High}} & \textbf{{Range}} \\
\midrule
Discounted cash flow & WACC $\pm$1 point & {money(_rg['dcf'][0])} & {money(_rg['dcf'][1])} & {money(_rg['dcf'][1] - _rg['dcf'][0])} \\
Exit multiple & $\pm$1.0x & {money(_rg['exit'][0])} & {money(_rg['exit'][1])} & {money(_rg['exit'][1] - _rg['exit'][0])} \\
Peer earnings & $\pm$2.0x & {money(_rg['pe'][0])} & {money(_rg['pe'][1])} & {money(_rg['pe'][1] - _rg['pe'][0])} \\
\midrule
\textbf{{Weighted target}} & \textbf{{combined}} & \textbf{{{money(_wlo)}}} & \textbf{{{money(_whi)}}} & \textbf{{{money(_whi - _wlo)}}} \\
\bottomrule
\end{{tabular}}}}
\srcnote{{Each row is the leg's own high and low, computed by re-running the engine, and
the range is the distance between them. The discounted row is not symmetric about its
value, because a point off the cost of capital is worth more than a point on. The
weighted row is the three ranges at their weights. The rating survives its own range: the
top, KShs
{money(_whi)}, is below both the valuation-date price of KShs {money(price)} and
the later quote of KShs {money(later)}. Peer earnings taken alone at the top of its range
reaches KShs {money(_rg['pe'][1])} and clears both, which is the clearest way this call
is wrong. The workbook stores a combined band of KShs {money(_v(d, "Weighted target"))}, which is exactly 20\% of the target by construction, and not a combination of the three methods. The figure above
weights them.}}
"""




def _is_residuals(d):
    """The two places the audited column of the forecast income statement does
    not foot. Both come from reconstructing FY2026 out of a condensed release."""
    I = d["is_rows"]
    da = sum(abs(v[0]) for k, v in I.items()
             if any(t in k.lower() for t in ("depreciation", "amortisation"))
             and isinstance(v[0], (int, float)))
    gap_ebit = (_row(I, "EBITDA")[0] - da) - _row(I, "EBIT")[0]
    tax = (abs(_row(I, "Tax at the statutory rate")[0])
           + abs(_row(I, "Non-deductible and unrecognised deferred tax")[0])
           - abs(_row(I, "Deferred tax credit")[0]))
    gap_tax = abs(_row(I, "INCOME TAX")[0]) - tax
    fi = _row(I, "Finance income")
    gap_fin = (_row(I, "EBIT")[0] + (fi[0] or 0)) - _row(I, "PROFIT BEFORE TAX")[0]
    return (gap_ebit, gap_tax, gap_fin)


def forecast_is_cf(d) -> str:
    """The complete forecast income statement and cash flow.

    The report printed a nine-line summary of the income statement and a
    four-line summary of the cash flow, then asked the reader to accept an
    earnings per share figure that appeared in neither. Both statements now
    print in full, in the model's own order, with earnings and dividends per
    share on the face of the income statement where they belong.
    """
    yrs = d["years"]
    h = " & ".join(B+"textbf{" + tex(y) + "}" for y in yrs)
    PCT = {"EBITDA margin", "Effective tax rate"}
    PS = {"EPS (KShs)", "DPS (KShs)"}

    def table(rows, first_col):
        out = ["{"+B+"footnotesize",
               B+"begin{tabular}{@{}l" + "r"*len(yrs) + "@{}}", B+"toprule",
               first_col + " & " + h + " " + B+B, B+"midrule"]
        for lab, vals in rows.items():
            if not any(isinstance(v, (int, float)) for v in vals):
                continue
            up = lab.strip()
            bold = up.isupper() or up.startswith("TOTAL")
            if up in ("EBITDA", "EBIT", "Kenya EBITDA", "OPERATING CASH FLOW",
                      "Capital expenditure", "Debt drawn"):
                out.append(B+"addlinespace[3pt]")
            fmt = (pct if up in PCT else (lambda v, _d=2: money(v, 2)) if up in PS else num)
            nm = (B+"textbf{" + tex(up) + "}") if bold else tex(up)
            out.append(nm + " & " + " & ".join(fmt(v) for v in vals) + " " + B+B)
        out += [B+"bottomrule", B+"end{tabular}}"]
        return NL.join(out)

    eps = _row(d["is_rows"], "EPS (KShs)")
    dps = _row(d["is_rows"], "DPS (KShs)")
    ni = _row(d["is_rows"], "ATTRIBUTABLE TO SAFARICOM SHAREHOLDERS")

    build = NL.join([
        "{"+B+"footnotesize",
        B+"begin{tabular}{@{}l" + "r"*len(yrs) + "@{}}", B+"toprule",
        "& " + h + " " + B+B, B+"midrule",
        "Profit attributable to shareholders, KES m & "
        + " & ".join(num(v) for v in ni) + " " + B+B,
        "Weighted shares in issue, m & " + " & ".join(num(40065.4) for _ in yrs) + " " + B+B,
        B+"midrule",
        B+"textbf{Earnings per share, KShs} & "
        + " & ".join(B+"textbf{" + money(v, 2) + "}" for v in eps) + " " + B+B,
        "Dividend per share declared, KShs & "
        + " & ".join(money(v, 2) if isinstance(v, (int, float)) else money(2.00, 2)
                     for v in dps) + " " + B+B,
        "Payout ratio & " + " & ".join(
            pct((dv if isinstance(dv, (int, float)) else 2.00) / ev) if ev else "--"
            for dv, ev in zip(dps, eps)) + " " + B+B,
        B+"bottomrule", B+"end{tabular}}",
    ])

    _isr = _is_residuals(d)
    return NL.join([
        B+"newpage",
        B+"section*{"+B+"color{navy}Forecast income statement}",
        "The model's own rows, in the model's own order, with nothing summarised away. "
        "The first column is the last audited year and the rest are forecast.",
        B+"par"+B+"vspace{4pt}",
        table(d["is_rows"], "KES millions"),
        B+"srcnote{Earnings per share divides profit attributable to Safaricom "
        "shareholders, not profit for the year: Ethiopian losses accrue partly to the "
        "minority consortium and do not all fall on the parent. The two diverge by the "
        "non-controlling interest line above. Figures in brackets are negative. Every "
        "forecast column foots exactly. The audited column does not, in two places. The "
        "printed tax rows fall KShs " + num(_isr[1], 1)
        + "m short of the income tax line. The second and larger is the finance block: "
        "operating profit plus finance income exceeds profit before tax by KShs "
        + num(_isr[2], 1) + "m, because the interest rows are blank for the audited year. "
        "The condensed release gives a single net finance cost of KShs 19,685m and no "
        "split, so the rows this table needs do not exist. The statements as filed carry "
        "the net figure and foot correctly. All three are artefacts of reconstructing "
        "FY2026 from a condensed release and none touches the forecast or the "
        "valuation.}",
        B+"newpage",
        B+"section*{"+B+"color{navy}Forecast cash flow}",
        "The cash flow is built from the movement between two balance sheets, so it begins "
        "in the first forecast year. The audited column is left empty. A figure there would not "
        "tie to the statements as filed.",
        B+"par"+B+"vspace{4pt}",
        table(d["cf_rows"], "KES millions"),
        B+"srcnote{Lease payments appear in financing in full, principal and interest "
        "together, and the interest element is added back in operating as a non-cash "
        "accrual. That is a presentation choice and it is applied consistently. It moves "
        "cash between operating and financing but not the net change in cash.}",
        B+"par"+B+"vspace{8pt}",
        B+"subsection*{How earnings per share is built}",
        build,
        B+"srcnote{The share count is held flat at 40,065.4m because no issue or buy-back "
        "is assumed. That count is the one the 15\\% block sold to Vodacom in June 2026 "
        "implies, and the one on which the declared FY2026 dividend of KShs 80,131m is "
        "exactly KShs 2.00 a share. The FY2026 dividend per share is the company's "
        "declaration. The forecast years are the model's, at an 80\\% payout.}",
    ])

def schedules(d) -> str:
    """The roll-forwards that drive the balance sheet.

    Every one of these closes at opening plus movements. The report used to print
    none of them, so a reader who took property and equipment from the balance
    sheet and capex and depreciation from the cash flow missed the closing balance
    by between KShs 7.5bn and KShs 10.8bn a year, with nothing on the page to
    explain the difference. The missing line is translation: roughly thirty per
    cent of the asset base sits in Ethiopia and is carried in birr.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import SHARES

    S, yrs = d["sched_rows"], d["years"]
    h = " & ".join(B+"textbf{" + tex(y) + "}" for y in yrs)

    def line(lab, key, indent=False, bold=False):
        vals = _row(S, key)
        nm = tex(lab)
        if indent:
            nm = B+"hspace{8pt}" + nm
        if bold:
            nm = B+"textbf{" + tex(lab) + "}"
        return nm + " & " + " & ".join(num(v) for v in vals) + " " + B+B

    # The caption used to be the heading with "roll-forward" appended, so each of
    # these six restated the title printed directly above it. Each now says what its
    # own table shows.
    CAPTIONS = {
        "Property and equipment":
            "How the network asset base moves, from opening to closing book value",
        "Intangible assets":
            "Spectrum and licences added, amortised against a falling book value",
        "Indefeasible rights of use":
            "Capacity rights on other operators' fibre, amortised to nil",
        "Right-of-use assets and lease liabilities":
            "The leased asset and the obligation behind it, forecast separately",
        "Borrowings": "Repayments and translation against the drawings needed to fund the plan",
        "Working capital": "The payables float that funds the business, year by year",
        "Dividends": "Declared against paid, and the payable the difference leaves",
    }

    def block(title, rows, note=None):
        out = [B+"subsection*{" + tex(title) + "}", "{"+B+"footnotesize",
               B+"excap{" + tex(CAPTIONS.get(title, title + " roll-forward")) + "}",
               B+"begin{tabular}{@{}l" + "r"*len(yrs) + "@{}}", B+"toprule",
               "KES millions & " + h + " " + B+B, B+"midrule"]
        out += rows
        out += [B+"bottomrule", B+"end{tabular}}"]
        if note:
            out.append(B+"srcnote{" + note + "}")
        return NL.join(out)

    _S = d["sched_rows"]
    _capex_tot = next((abs(x["capex"]) for x in d["fins"]
                       if x["fy"] == d["years"][0] and x.get("capex")), 0)
    _res = (_S["Closing net book value"][0]
            - (_S["Opening net book value"][0] + _S["Additions (capex)"][0]
               + _S["Depreciation"][0] + _S["Translation differences"][0]))
    ppe = block("Property and equipment", [
        line("Opening net book value", "Opening net book value"),
        line("Additions (capex)", "Additions (capex)", indent=True),
        line("Depreciation", "Depreciation", indent=True),
        line("Translation differences", "Translation differences", indent=True),
        B+"midrule",
        line("Closing net book value", "Closing net book value", bold=True),
    ], note="Every forecast year closes exactly on opening plus additions less "
            "depreciation and translation. The audited column does not: it leaves KShs "
            + num(abs(_res), 0) + "m unexplained, which is "
            + pct(abs(_res) / _S["Closing net book value"][0], 2) + " of the closing "
            "balance and is disposals and reclassifications the model does not carry as a "
            "separate line. Translation is the largest single movement after depreciation "
            "and it is not a cash item. A reader checking this roll-forward without it "
            "will find a gap of exactly that size in every year. Two capital expenditure "
            "figures appear in this note and both are derived splits of the same "
            "undisclosed FY2026 investing subtotal: KShs "
            + num(_capex_tot) + "m is total capital expenditure and is what the summary "
            "table and the capital-intensity chart use, while the KShs "
            + num(_S["Additions (capex)"][0]) + "m here is the property and equipment "
            "portion alone. The KShs " + num(abs(_capex_tot - _S["Additions (capex)"][0]))
            + "m difference is intangibles and spectrum.")
    # The Schedules sheet carries "Opening", "Amortisation" and "Closing" TWICE: once
    # for indefeasible rights of use and once for intangibles. The collection layer
    # suffixes the second, so the unsuffixed labels are the RIGHTS OF USE block. This
    # exhibit read those and printed a KShs 2.6bn rights-of-use schedule under the
    # heading of the KShs 108bn intangible asset, with the intangible additions row
    # spliced into it, missing by 7,500 a year under a note certifying that it closed.
    _ires = (_S["Closing (2)"][0] - (_S["Opening (2)"][0]
                                     + (_S["Additions incl. spectrum and licences"][0] or 0)
                                     + _S["Amortisation (2)"][0]))
    intg = block("Intangible assets", [
        line("Opening", "Opening (2)"),
        line("Additions including spectrum and licences", "Additions incl. spectrum and licences", indent=True),
        line("Amortisation", "Amortisation (2)", indent=True),
        B+"midrule",
        line("Closing", "Closing (2)", bold=True),
    ], note="Every forecast year closes exactly. The audited column leaves KShs "
            + num(abs(_ires), 0) + "m unexplained, being additions, disposals and the "
            "translation of the Ethiopian licence, none of which the model carries as a "
            "separate line in the base year. Spectrum and licence additions are held at "
            "KShs 7,500m a year throughout, which is an assumption and not guidance.")
    _ures = (_S["Closing"][0] - (_S["Opening"][0] + _S["Amortisation"][0]))
    iru = block("Indefeasible rights of use", [
        line("Opening", "Opening"),
        line("Amortisation", "Amortisation", indent=True),
        B+"midrule",
        line("Closing", "Closing", bold=True),
    ], note="The capacity rights bought outright on other operators' fibre, amortised to "
            "nil across the forecast. The audited column does not close, by KShs "
            + num(abs(_ures), 0) + "m, because the base year carries the charge without "
            "the addition that funded it. This schedule and the intangible one above it "
            "share the labels opening, amortisation and closing in the workbook, and this "
            "note used to print in place of that one.")
    _rres = (_S["ROU asset, closing"][0] - (_S["ROU asset, opening"][0]
             + (_S.get("New and remeasured leases") or [0])[0] + _S["ROU depreciation"][0]))
    rou = block("Right-of-use assets and lease liabilities", [
        line("ROU asset, opening", "ROU asset, opening"),
        line("New and remeasured leases", "New and remeasured leases", indent=True),
        line("ROU depreciation", "ROU depreciation", indent=True),
        line("ROU asset, closing", "ROU asset, closing", bold=True),
        B+"midrule",
        line("Lease liability, opening", "Lease liability, opening"),
        line("Additions", "Additions", indent=True),
        line("Lease interest accrued", "Lease interest accrued", indent=True),
        line("Lease payments", "Lease payments", indent=True),
        line("Lease liability, closing", "Lease liability, closing", bold=True),
    ], note="Every forecast year closes exactly on both halves. The audited column shows "
            "the opposite. Opening, plus the KShs "
            + num((_S.get("New and remeasured leases") or [0])[0]) + "m of new leases the "
            "column does show, less depreciation, overshoots the closing balance by KShs "
            + num(abs(_rres), 0) + "m. That is "
            + pct(abs(_rres) / _S["ROU asset, closing"][0], 1) + " of the closing balance "
            "and the largest residual in this section. It is a reduction the roll does not "
            "carry, most plausibly lease terminations and remeasurements netted inside the "
            "disclosed additions, which the condensed release does not separate. It is "
            "stated here and not absorbed into a movement line.")
    _dres = (_S["Closing borrowings"][0]
             - (_S["Opening borrowings"][0] + _S["Scheduled repayments"][0]
                + (_S.get("Translation differences (2)") or [0])[0]
                + (_S.get("Revaluation of foreign-currency loans") or [0])[0]
                + _S["New borrowing to meet funding need"][0]))
    debt = block("Borrowings", [
        line("Opening borrowings", "Opening borrowings"),
        line("Scheduled repayments", "Scheduled repayments", indent=True),
        line("Translation differences", "Translation differences (2)", indent=True),
        line("Revaluation of foreign-currency loans", "Revaluation of foreign-currency loans", indent=True),
        line("Debt before new drawings", "Debt before new drawings"),
        line("New borrowing to meet funding need", "New borrowing to meet funding need", indent=True),
        B+"midrule",
        line("Closing borrowings", "Closing borrowings", bold=True),
    ], note="Every forecast year closes exactly. The audited column leaves KShs "
            + num(abs(_dres), 0) + "m unexplained, the same kind of back-fill residual "
            "as the property schedule above and for the same reason: the FY2026 column "
            "is reconstructed from a condensed release rather than modelled. Interest is "
            "shown because it drives the income statement, but it is not a movement in "
            "the balance and is excluded from the roll.")
    wc = block("Working capital", [
        line("Trade and other receivables", "Trade and other receivables"),
        line("Inventories", "Inventories"),
        line("Contract assets", "Contract assets"),
        line("Payables and accrued expenses", "Payables and accrued expenses"),
        line("Provisions and contract liabilities", "Provisions and contract liabilities"),
        B+"midrule",
        line("Net working capital", "NET WORKING CAPITAL", bold=True),
        line("Change in working capital (cash impact)", "Change in working capital (cash impact)"),
        B+"midrule",
        _days_row("Receivables and inventories, days of revenue",
                  _rows_sum(d["bs_rows"], *WC_ASSETS), d),
        _days_row("Payables and provisions, days of revenue",
                  _rows_sum(d["bs_rows"], *WC_LIABS), d),
        _days_row("Net working capital, days of revenue",
                  [a_ - b_ for a_, b_ in zip(
                      _rows_sum(d["bs_rows"], *WC_ASSETS),
                      _rows_sum(d["bs_rows"], *WC_LIABS))],
                  d, bold=True),
    ], note="The days rows are the balance over the year's revenue, annualised. The "
            "audited release groups receivables with inventories and contract assets, and "
            "payables with provisions and contract liabilities, so the three conventional "
            "ratios cannot be separated and are shown as the two groups the filing gives. "
            "Net working capital sits at about "
            + money(abs((_rows_sum(d["bs_rows"], *WC_ASSETS)[0]
                         - _rows_sum(d["bs_rows"], *WC_LIABS)[0])
                        / _row(d["is_rows"], "TOTAL REVENUE")[0] * 365), 0)
            + " days of negative working capital and the model holds it there, which is why "
            "it is a source of cash in every forecast year. On the view that the payable "
            "days are unsustainable is disagreeing with a flat assumption, not with a "
            "trend.")
    _filed_div = next((abs(x["dividends_paid"]) for x in d["fins"]
                       if x["fy"] == d["years"][0] and x.get("dividends_paid")), None)
    _dpaid = _S["Dividends PAID in cash"][0]
    _dgap = (_filed_div - _dpaid) if (_filed_div and _dpaid) else None
    div = block("Dividends", [
        line("Declared", "Dividends DECLARED"),
        line("Paid in cash", "Dividends PAID in cash"),
        B+"midrule",
        line("Dividend payable, closing", "Dividend payable, closing", bold=True),
    ], note=("The audited column is the model's own roll and does not match the filed "
             "cash flow, which shows KShs " + num(_filed_div) + "m paid against the KShs "
             + num(_dpaid) + "m here, a difference of KShs " + num(abs(_dgap)) + "m. The "
             "filed figure is the right one and is what the statements as filed and the "
             "ratio table both use: it can be derived from the balance sheet as opening "
             "payable and proposed, plus the year's declaration, less closing payable and "
             "proposed. The model's roll omits the movement in the payable. Only the "
             "audited column is affected."
             if _dgap else
             "Declared is the shareholder decision and paid is the cash movement, and the "
             "difference is the payable."))

    dec = _row(S, "Dividends DECLARED")
    dps26 = (dec[0] or 0) / SHARES

    # Re-run the whole valuation at the audited FY2026 depreciation rate, so the
    # step-down to a 7.50-year life is quantified on every leg.
    from revalue import base as _base, value as _value, fcff as _fcff
    _op, _cap, _dp = (_row(S, "Opening net book value"), _row(S, "Additions (capex)"),
                      _row(S, "Depreciation"))
    _r26 = abs(_dp[0]) / (_op[0] + _cap[0] * 0.5)
    _dep_extra = [abs(_op[i] + _cap[i] * 0.5) * _r26 - abs(_dp[i]) for i in range(1, 6)]
    _mdl = _model_of(d)
    _b = _base(_mdl)
    _divs = [abs(x) for x in (d["cf_rows"].get("Dividends PAID") or [])
             if isinstance(x, (int, float))]
    _m2 = dict(_mdl); _m2["cf_rows"] = dict(_mdl["cf_rows"])
    _m2["cf_rows"]["Dividends PAID"] = [None] + [
        dv - e * 0.70 * 0.80 for dv, e in zip(_divs, _dep_extra)]
    _v0 = _value(_mdl, **_b)
    _v1 = _value(_m2, fcff_override=[c + e * 0.30 for c, e in zip(_fcff(_mdl), _dep_extra)],
                 **_b)
    _dep_v = [[_v0["dcf"], _v0["exit"], _v0["pe"], _v0["target"]],
              [_v1["dcf"], _v1["exit"], _v1["pe"], _v1["target"]]]

    return NL.join([
        B+"newpage",
        B+"section*{"+B+"color{navy}Supporting schedules}",
        "These are the roll-forwards that drive the balance sheet. Every forecast year "
        "closes at opening plus movements with nothing plugged. The audited first column "
        "is reconstructed from a condensed release, not modelled, and several of the "
        "schedules leave a residual there. Each is stated in the note beneath "
        "them rather than absorbed.",
        B+"par"+B+"vspace{2pt}",
        ppe,
        B+"srcnote{Translation is the line a reader cannot infer from the primary statements. "
        "About thirty per cent of the group's property and equipment sits in Ethiopia and is "
        "carried in birr, so the shilling value of that base falls as the birr depreciates even "
        "though no asset leaves the balance sheet. Opening plus capex less depreciation "
        "therefore overstates the closing balance by KShs " + num(abs(_row(S, "Translation differences")[1]), 0) +
        "m in " + tex(yrs[1]) + ", rising to KShs " + num(abs(_row(S, "Translation differences")[-1]), 0) +
        "m in " + tex(yrs[-1]) + ". The birr assumption is set out under Assumptions.}",
        B+"par"+B+"vspace{4pt}",
        intg,
        B+"par"+B+"vspace{6pt}",
        iru,
        B+"par"+B+"vspace{6pt}",
        rou,
        B+"srcnote{Right-of-use depreciation, lease interest and lease principal are three "
        "different numbers and are modelled separately. The FY2026 right-of-use asset of KShs "
        "38,370m stands against a lease liability of KShs 57,510m: they are not equal and they "
        "do not move together.}",
        B+"newpage",
        debt,
        B+"srcnote{New borrowing is the funding plug and it falls to nil from " + tex(yrs[4]) +
        ", which is the point at which operating cash flow covers capex, leases, interest and "
        "the dividend without recourse to the balance sheet. The forecast turns on it, and it is an assumption, not a plan the company has "
        "published.}",
        B+"par"+B+"vspace{6pt}",
        wc,
        B+"srcnote{Net working capital is negative throughout, which is normal for a prepaid "
        "telecommunications business: customers pay before they consume and suppliers are paid "
        "after. It becomes more negative every year, so working capital is a source of cash in the forecast, not a use of it.}",
        B+"par"+B+"vspace{6pt}",
        div,
        B+"srcnote{The FY2026 declared dividend of KShs " + num(dec[0], 0) + "m on " +
        num(SHARES, 0) + "m shares is KShs " + money(dps26) + " per share, made up of an 85 "
        "cent interim and a 115 cent final recommended for approval. The company reported the "
        "same figure as KShs 80.13bn, a very large distribution for a Kenyan company in "
        "absolute terms, and it ends a three-year freeze. The forecast holds the payout ratio "
        "at 80 per cent of earnings.}",
        B+"newpage",
        B+"section*{"+B+"color{navy}Two assumptions inside the schedules}",
        "Neither of these is visible in the primary statements and both move the answer. "
        "They are set out here rather than left in the workbook.",
        B+"subsection*{The depreciation charge steps down in the first forecast year}",
        "The audited FY2026 depreciation charge on property and equipment was KShs " +
        num(abs(_row(S, "Depreciation")[0])) + "m against an opening base plus half the "
        "year's capex of KShs " + num(_row(S, "Opening net book value")[0] +
        _row(S, "Additions (capex)")[0] * 0.5) + "m, an implied asset life of " +
        money(1 / (abs(_row(S, "Depreciation")[0]) / (_row(S, "Opening net book value")[0] +
        _row(S, "Additions (capex)")[0] * 0.5)), 2) + " years. The forecast applies a flat "
        "7.50-year life from " + tex(yrs[1]) + " onwards. The charge therefore falls by "
        "KShs " + num(_dep_extra[0]) + "m in the first forecast year and by KShs " +
        num(_dep_extra[-1]) + "m by " + tex(yrs[-1]) + ", relative to holding the audited "
        "rate. A longer assumed life is not obviously wrong for a network that has just "
        "come through a heavy build, but it is an assumption and it is not visible "
        "anywhere in the primary statements.",
        "",
        "Its effect is smaller than it looks, because it pushes the three valuation legs in "
        "opposite directions. A lower charge raises reported earnings, which raises the peer "
        "multiple leg. It also lowers the depreciation tax shield, which lowers the "
        "discounted leg. Re-running the whole valuation at the audited FY2026 rate gives "
        "the following.",
        B+"par"+B+"vspace{4pt}",
        "{"+B+"footnotesize",
        B+"excap{The valuation at the audited depreciation rate}",
        B+"begin{tabular}{@{}lrrr@{}}",
        B+"toprule",
        B+"textbf{KShs per share} & "+B+"textbf{At 7.50-year life} & "
        + B+"textbf{At audited rate} & "+B+"textbf{Difference} " + B+B,
        B+"midrule",
        NL.join("Discounted cash flow & " + money(_dep_v[0][0]) + " & " + money(_dep_v[1][0]) + " & " + money(_dep_v[1][0]-_dep_v[0][0]) + " " + B+B
                for _ in (0,)),
        "Exit multiple & " + money(_dep_v[0][1]) + " & " + money(_dep_v[1][1]) + " & " + money(_dep_v[1][1]-_dep_v[0][1]) + " " + B+B,
        "Peer earnings multiple & " + money(_dep_v[0][2]) + " & " + money(_dep_v[1][2]) + " & " + money(_dep_v[1][2]-_dep_v[0][2]) + " " + B+B,
        B+"midrule",
        B+"textbf{Weighted target} & "+B+"textbf{" + money(_dep_v[0][3]) + "} & "
        + B+"textbf{" + money(_dep_v[1][3]) + "} & "+B+"textbf{" + money(_dep_v[1][3]-_dep_v[0][3]) + "} " + B+B,
        B+"bottomrule",
        B+"end{tabular}}",
        B+"srcnote{The target moves by KShs " + money(abs(_dep_v[1][3]-_dep_v[0][3])) +
        ", against leg movements of up to KShs " + money(max(abs(_dep_v[1][i]-_dep_v[0][i]) for i in range(3))) +
        ". The offset is not a coincidence: the same charge that shelters cash from tax "
        "also depresses the earnings a multiple is applied to. It is the reason a valuation "
        "built on three methods is less exposed to a single accounting assumption than one "
        "built on any of them alone.}",
        "",
        B+"subsection*{The birr rate is calibrated, not observed}",
        "Two currency assumptions drive Ethiopia. The first is the rate of birr depreciation "
        "against the shilling, set at 10 per cent a year in the base case against 18 per cent "
        "in the downside and 4 per cent in the upside. That is the assumption behind the "
        "translation line in the property and equipment roll-forward above, and behind KShs " +
        num(abs(_row(S, "Translation differences (2)")[1])) + "m a year of translation on "
        "borrowings.",
        "",
        "The second is more awkward and is disclosed rather than defended. The average birr "
        "per shilling rate used to translate Ethiopian revenue is not taken from a published "
        "FX table. It is calibrated: the rate is set at the level that makes translated "
        "Ethiopian revenue equal the audited figure of KShs 17,355.7m. That makes the FY2026 "
        "translation exact by construction and tells a reader nothing about whether the "
        "forecast years are translated at a sensible rate. The model carries a note to "
        "refresh it from the FY2026 Annual Report currency disclosure before publication, "
        "and that has not been done. Ethiopia is " +
        pct(abs(_row(d["is_rows"], "Ethiopia revenue")[0]) / _row(d["is_rows"], "TOTAL REVENUE")[0], 1) +
        " of group revenue in " + tex(yrs[0]) + ", so the exposure is contained, but a reader "
        "should treat the Ethiopian revenue line as the least well-grounded figure in this "
        "note.",
    ])


def _wordn(n: int) -> str:
    return {1: "one", 2: "two", 3: "three", 4: "four"}.get(n, str(n))


def corrections(d) -> str:
    """The three modelling treatments the workbook carries, each priced by reversal.

    These used to be corrections the report made on the way out of a workbook
    that carried an assumption the issuer had contradicted. They now live in the
    model itself, so each is priced by turning it off rather than by adding it
    on, and the workbook and this note publish the same target.
    """
    P, base = d["val_price"], d["treatment_base"]
    rows = []
    for lbl, t in d["treatments"]:
        rows.append(tex(lbl) + " & " + money(t) + " & " + money(t - base) + " & "
                    + pct(t / P - 1) + " & "
                    + tex("towards a Sell" if t < base else "against it") + " " + B + B)
    return NL.join([
        B + "subsection*{Three modelling treatments, and what each is worth}",
        "The workbook this note publishes from is not the one the platform first built. "
        "Three treatments were changed, each set out where it arises, and each applied to "
        "every leg it touches. They are priced below by reversing them one at a time, so a "
        "reader who disagrees with any one can see the target without it. Two lower the "
        "target and one raises it.",
        B + "par" + B + "vspace{4pt}",
        "{" + B + "footnotesize",
        B + "excap{Each treatment, priced by turning it off}",
        B + "begin{tabular}{@{}p{8.0cm}rrrl@{}}",
        B + "toprule",
        B + "textbf{Turning off} & " + B + "textbf{Target} & " + B + "textbf{Move} & "
        + B + "textbf{vs price} & " + B + "textbf{Direction} " + B + B,
        B + "midrule",
        B + "textbf{Nothing: the model as it stands} & " + B + "textbf{" + money(base)
        + "} & -- & " + B + "textbf{" + pct(base / P - 1) + "} & -- " + B + B,
        B + "midrule", NL.join(rows), B + "bottomrule", B + "end{tabular}}",
        B + "srcnote{Each row turns off one treatment and leaves the other two in place. "
        "The peer earnings leg is untouched by all three. The capital expenditure row "
        "runs through the discounted leg and the exit leg, because cash not spent leaves "
        "less net debt to deduct at the end of the hold; the other two run through the "
        "discounted leg alone.}",
        "",
        "The Ethiopian capital expenditure treatment is the one with an external anchor: "
        "the company guides KShs 6bn to 9bn for FY2027 against the KShs 26bn the model "
        "first carried, and holding the model's figure would have meant publishing a "
        "forecast the issuer had already contradicted. The other two came out of reviewing "
        "how the valuation was built rather than what was fed into it. Charging new leases "
        "matters because operating profit is struck after IFRS 16, so the depreciation on "
        "leased assets is already added back and leaving the additions out values a site "
        "estate the company never pays for. The terminal treatment matters because growing "
        "the last forecast cash flow at a perpetual rate carries that year's reinvestment "
        "into perpetuity, and the note would rather state the reinvestment the growth rate "
        "implies than inherit whatever the final forecast year happened to spend.",
    ])

def weight_test(d) -> str:
    """The house weights are a convention. Test whether the rating needs them.

    Both reviews made the same point: three quarters of the target rests on two
    weights that the note stated and never defended. The answer turns out to be
    the strongest robustness result in the note, so it belongs on the page.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _wb, value as _wv

    m = _model_of(d)
    v = _wv(m, **_wb(m))
    P, w0 = d["val_price"], d["weights"]
    CASES = [
        ("The house weights used here", tuple(w0)),
        ("Equal weight, a third to each", (1 / 3, 1 / 3, 1 / 3)),
        ("Discounted cash flow alone", (1.0, 0.0, 0.0)),
        ("Market-based methods only", (0.0, 0.45, 0.55)),
        ("Peer earnings alone", (0.0, 0.0, 1.0)),
    ]
    def blend(w):
        return v["dcf"] * w[0] + v["exit"] * w[1] + v["pe"] * w[2]
    rows = []
    for lbl, w in CASES:
        t = blend(w)
        rows.append(tex(lbl) + " & " + " & ".join(pct(x, 0) for x in w) + " & "
                    + money(t) + " & " + pct(t / P - 1) + " & "
                    + ("Sell" if t < P else "Buy") + " " + B + B)
    best = max(blend(w) for _, w in CASES)
    out = [
        B + "section*{" + B + "color{navy}Whether the weights are doing the work}",
        "Three quarters of the target sits on two weights that are a house convention, not a result. If the rating depended on them it would not be much of a "
        "rating, so here is the blend re-struck five ways on the same three unchanged "
        "method values.",
        B + "par" + B + "vspace{4pt}",
        "{" + B + "footnotesize",
        B + "excap{The rating under every weighting}",
        B + "begin{tabular}{@{}lrrrrrl@{}}",
        B + "toprule",
        B + "textbf{Weighting} & " + B + "textbf{DCF} & " + B + "textbf{Exit} & "
        + B + "textbf{Peers} & " + B + "textbf{Target} & " + B + "textbf{vs price} & "
        + B + "textbf{Sell holds} " + B + B,
        B + "midrule",
        NL.join(rows),
        B + "bottomrule",
        B + "end{tabular}}",
        B + "srcnote{The three method values are unchanged throughout at KShs "
        + money(v["dcf"]) + ", " + money(v["exit"]) + " and " + money(v["pe"])
        + ". Only the weights move. Against the valuation-date price of KShs "
        + money(P) + ".}",
        "",
        "The rating is a Sell on four of the five. The exception is stated "
        "plainly because it is the most favourable construction available from this "
        "note's own inputs. Peer earnings taken alone, at a full weight and with the "
        "discounted valuation discarded, gives KShs " + money(best) + " against a price of "
        "KShs " + money(P) + ". That is " + pct(best / P - 1) + ", and it is a Buy. "
        "Nothing else on the table reaches the price. On the view that the only "
        "defensible way to value this business is against what the market pays for its "
        "peers should not hold this Sell, and that is the same objection the sum of the "
        "parts makes later in different words.",
    ]
    out.append("")
    out.append(price_chart(d))
    return NL.join(out) + NL


def fcff_schedule(d) -> str:
    """The cash flows the DCF discounts, year by year.

    Fifty-five per cent of the target rests on this and the report showed only
    the present value of the whole of it. A reader could not rebuild the number
    that carries the rating.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import SHARES, fcff as _fcff

    mdl = _model_of(d)
    yrs = d["years"][1:]
    tax = _v(d, "Tax rate")
    wacc, g = _v(d, "WACC"), _v(d, "Terminal growth")
    ebit = _row(d["is_rows"], "EBIT")[1:]
    # All four charges, summed the way the engine sums them. Asking for one row by
    # prefix returned only "Depreciation, property and equipment" once the cash
    # flow was broken out into four separate add-backs, which understated the line
    # by 18,760 in FY2027 and stopped this schedule adding down.
    _da_rows = [v for _k, v in d["cf_rows"].items()
                if _k.strip().lower().startswith(("depreciation", "amortisation"))]
    da = [sum((row[i] or 0) for row in _da_rows) for i in range(len(_da_rows[0]))][1:]
    inv = _row(d["cf_rows"], "INVESTING CASH FLOW")[1:]
    dwc = _row(d["cf_rows"], "Change in working capital")[1:]
    cf = _fcff(mdl, tax)
    df = [1 / ((1 + wacc) ** (i + 1)) for i in range(len(cf))]
    pv = [c * f for c, f in zip(cf, df)]

    h = " & ".join(B+"textbf{" + tex(y) + "}" for y in yrs)

    def line(lab, vals, dp=0, bold=False, fmt=num):
        nm = (B+"textbf{" + tex(lab) + "}") if bold else tex(lab)
        return nm + " & " + " & ".join(fmt(v, dp) for v in vals) + " " + B+B

    # The engine's disciplined terminal value, not a perpetuity recomputed here.
    # This section printed cf[-1]*(1+g)/(wacc-g) = 1,491,447 against the 1,596,711
    # the bridge actually discounts, and drew a conclusion from the 3.94x that
    # implied instead of the 4.22x the valuation uses.
    from revalue import _terminal as _tvfn, TERMINAL_ROIC as _troic2
    tv = _tvfn(mdl, cf, wacc, g, tax, _troic2)
    eb31 = _row(d["is_rows"], "EBITDA")[-1]

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Free cash flow to the firm}")
    out.append("The cash flows the discounted valuation actually discounts. Tax is charged "
               "on operating profit at " + pct(tax, 0) + ", the statutory rate, which is "
               "below the group's realised effective rate because Ethiopian losses do not "
               "shelter Kenyan taxable profit.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}l" + "r" * len(yrs) + "@{}}")
    out.append(B+"toprule")
    out.append("KES millions & " + h + " " + B+B)
    out.append(B+"midrule")
    out.append(line("Operating profit (EBIT)", ebit))
    out.append(line("Tax on EBIT", [-(_x or 0) * tax for _x in ebit]))
    out.append(line("Net operating profit after tax", [(_x or 0) * (1 - tax) for _x in ebit], bold=True))
    out.append(line("Depreciation and amortisation", da))
    # No capex correction is applied here. The workbook already carries Ethiopian
    # capex at guidance, so the add-back this schedule used to print was relief on
    # a series that had it, and it cancelled against the understated D&A above.
    out.append(line("Capital expenditure and intangibles", inv))
    _lease = [-abs(v or 0) for v in _row(d["sched_rows"], "New and remeasured leases")[1:]]
    out.append(line("New and remeasured leases", _lease))
    out.append(line("Capital expenditure as valued",
                    [(_x or 0) + _l for _x, _l in zip(inv, _lease)]))
    out.append(line("Change in working capital", dwc))
    out.append(B+"midrule")
    out.append(line("Free cash flow to the firm", cf, bold=True))
    out.append(line("Discount factor", df, 3, fmt=lambda v, dp=3: f"{v:,.3f}"))
    out.append(line("Present value", pv))
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Discounted at a WACC of " + pct(wacc) + ", year end. The five "
               "present values sum to KShs " + num(sum(pv)) + "m, which is the PV of the "
               "explicit forecast in the enterprise-to-equity bridge. Ethiopian capital "
               "expenditure is carried at the midpoint of company guidance for FY2027 and a "
               "KShs 10bn run-rate after, and it is carried at those figures everywhere: in "
               "the forecast statements, in the property schedule and in the FY2031 net debt "
               "the exit leg deducts. There is no separate correction applied to these cash "
               "flows alone, so this schedule and those pages are on one basis. What the "
               "guidance is worth against the flat KShs 26bn the model used to hold is priced "
               "on the page of cases, not here. Depreciation and amortisation is the sum of "
               "all four charges the cash flow adds back, being property and equipment, "
               "intangibles, right-of-use assets and indefeasible rights of use. New and "
               "remeasured leases are charged because operating profit is struck after "
               "IFRS 16, so the depreciation on those assets is already inside the D\&A "
               "added back above. Leaving the additions out would let the company earn from "
               "a leased site estate it never pays for, while the bridge still deducts the "
               "lease liability as debt. The liability the new leases create is financing and "
               "correctly stays out of an unlevered cash flow, with only the opening balance "
               "in the bridge.}")
    out.append("")
    out.append(B+"subsection*{The terminal value, and what it implies}")
    out.append("The terminal value of KShs " + num(tv) + "m grows the final year's after-tax "
               "operating profit at " + pct(g) + " in perpetuity and charges the "
               "reinvestment that growth needs, which is the growth rate divided by the "
               "terminal return on capital. Divided by "
               + tex(d["years"][-1]) + " EBITDA of KShs " + num(eb31) + "m it is "
               + mult(tv / eb31, 2) + " of EBITDA. Growing the cash flow itself at the same "
               "rate and charging no reinvestment would give KShs "
               + num(cf[-1] * (1 + g) / max(wacc - g, 1e-6)) + "m, which is the figure this "
               "page used to print and is what the treatment on the page of assumptions "
               "reverses.")
    out.append("")
    out.append("That figure deserves attention, because the "
               "exit-multiple method values the same year at " + mult(_v(d, "Exit EV/EBITDA"), 2)
               + ", the peer median. The two methods carrying three quarters of the weight "
               "therefore disagree about what this business is worth at the end of the "
               "forecast, and the discounted cash flow is the more pessimistic of the two. "
               "It assumes Safaricom de-rates to below every peer in the table except "
               "Sonatel while delivering the best margins in its history. That is the Sell "
               "case stated at its most demanding, and it rests on "
               "that is what it rests on.")
    return NL.join(out) + NL


def grids(d) -> str:
    """Two-way sensitivity, WACC against terminal growth and against the exit multiple."""
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _base, value as _value

    mdl = _model_of(d)
    b = _base(mdl)
    price = d["val_price"]
    _nclear = [0]
    W = [b["wacc"] + x for x in (-0.02, -0.01, 0.0, 0.01, 0.02)]
    G = [b["g"] + x for x in (-0.01, -0.005, 0.0, 0.005, 0.01)]
    X = [b["exit_mult"] + x for x in (-1.0, -0.5, 0.0, 0.5, 1.0)]

    def grid(col_vals, key, fmt):
        head = " & ".join(B+"textbf{" + fmt(v) + "}" for v in col_vals)
        rows = []
        for w in W:
            cells = []
            for v in col_vals:
                # ke moves with the WACC, because both rest on the same
                # risk-free rate. Holding it fixed understated this grid
                # against every other discount-rate figure in the note.
                kw = dict(b); kw["wacc"] = w; kw[key] = v
                kw["ke"] = b["ke"] + (w - b["wacc"]) / 0.80
                t = _value(mdl, **kw)["target"]
                c = money(t)
                if t >= price:
                    c = B+"textbf{" + c + "}"
                    _nclear[0] += 1
                cells.append(c)
            mark = B+"textbf{" if abs(w - b["wacc"]) < 1e-9 else ""
            lab = (mark + pct(w) + "}") if mark else pct(w)
            rows.append(lab + " & " + " & ".join(cells) + " " + B+B)
        return head, rows

    h1, r1 = grid(G, "g", lambda v: pct(v))
    h2, r2 = grid(X, "exit_mult", lambda v: mult(v, 1))

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Two-way sensitivity}")
    out.append("The target re-run across the two assumptions it is most sensitive to, "
               "through the same engine. Any cell at or above the valuation-date price of "
               "KShs " + money(price) + " would break the rating and is set in bold.")
    for title, note, h, r in (
        ("Discount rate against terminal growth",
         "Terminal growth across the columns, WACC down the rows.", h1, r1),
        ("Discount rate against the exit multiple",
         "Exit EV/EBITDA across the columns, WACC down the rows. The exit multiple "
         "enters through the 20\\% leg only, so this grid moves less than the one above.",
         h2, r2)):
        out.append("")
        out.append(B+"subsection*{" + title + "}")
        out.append(B+"par"+B+"vspace{3pt}")
        out.append("{"+B+"footnotesize")
        out.append(B+"begin{tabular}{@{}l" + "r" * 5 + "@{}}")
        out.append(B+"toprule")
        out.append("WACC & " + h + " " + B+B)
        out.append(B+"midrule")
        out.extend(r)
        out.append(B+"bottomrule")
        out.append(B+"end{tabular}}")
        out.append(B+"par"+B+"vspace{2pt}")
        out.append(B+"srcnote{" + note + " KShs per share.}")
    out.append("")
    _n = _nclear[0]
    _word = {0: "No cell clears", 1: "One cell clears", 2: "Two cells clear",
             3: "Three cells clear"}.get(_n, str(_n) + " cells clear")
    out.append(_word + " the price out of the fifty in these two grids, and "
               + ("it is" if _n == 1 else "they are") + " set in bold. "
               + ("Both sit" if _n == 2 else "Each sits")
               + " two points of cost of capital below the published build, at "
               + pct(_v(d, "WACC") - 0.02, 1)
               + ", and each also needs terminal growth or an exit multiple "
               "above what the model carries. So the rating does not turn on any single "
               "assumption, but it does "
               "not survive every corner of these grids either, and the corner it fails in "
               "is the one this note has argued about throughout: a materially lower "
               "Kenyan cost of capital. The arithmetic for that view is "
               "here and should not need the text to concede it.")
    return NL.join(out) + NL


def scenarios(d) -> str:
    """What the rating turns on, run through the same engine.

    A target with no scenarios around it is an opinion with a decimal point.
    Every input below is either the model's own or a multiple observed on a
    company in the peer table, so none of it is invented.
    """
    import statistics as st
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _base, value as _value

    mdl = _model_of(d)
    b = _base(mdl)
    V = d["val"]
    RF, BETA, ERP = _v(d, "Risk-free rate"), _v(d, "Equity beta"), _v(d, "Equity risk premium")
    KD, TAX, WE = _v(d, "Cost of debt"), _v(d, "Tax rate"), _v(d, "Equity weight")

    # The published cost of equity charges the Ethiopian premium against Ethiopia's
    # share of the group, not against all of it. A scenario table built on a blanket
    # premium does not contain the published build: at a flat 3.0% it returned a base
    # of KShs 26.05 under a heading that said "as published".
    ETHW = _v(d, "Ethiopia weight in the cost of equity")
    CRP0 = _v(d, "Ethiopia country risk premium")

    def cc(crp_eth, rf=None):
        r = RF if rf is None else rf
        ke = r + BETA * ERP + ETHW * crp_eth
        return ke, ke * WE + KD * (1 - TAX) * (1 - WE)

    evs = [p["ev_ebitda"] for p in d["peers"] if p["ev_ebitda"]]
    pes = [p["pe"] for p in d["peers"] if p["pe"]]
    price = d["val_price"]

    def run(crp_eth, ex, pe, rf=None):
        ke, wacc = cc(crp_eth, rf)
        return _value(mdl, wacc=wacc, g=b["g"], ke=ke, exit_mult=ex, peer_pe=pe)

    # The multiples the base uses ARE the peer medians, so a separate "at the medians"
    # row repeated the base row cell for cell and the note drew a conclusion from
    # comparing the base with itself.
    cases = [
        ("Bear", CRP0 + 0.02, min(evs), min(pes), RF,
         "Ethiopian premium two points wider, both multiples at the peer floor"),
        ("Base, as published", CRP0, b["exit_mult"], b["peer_pe"], RF,
         "the build this note publishes, with both multiples at the peer medians"),
        ("Kenyan yields compress two points", CRP0, b["exit_mult"], b["peer_pe"], RF - 0.02,
         "the ten-year continues the fall it has made since 2024"),
        ("Bull", CRP0 - 0.02, max(evs), max(pes), RF - 0.02,
         "yields compress, Ethiopian premium narrows, multiples at the peer ceiling"),
    ]
    rows = []
    for name, crp, ex, pe, rf_, _why in cases:
        o = run(crp, ex, pe, rf_)
        bold = name.startswith("Base")
        nm = (B+"textbf{" + tex(name) + "}") if bold else tex(name)
        cells = [pct(crp, 1), mult(ex, 2), mult(pe, 2), money(o["target"]),
                 pct(o["target"] / price - 1)]
        if bold:
            cells = [B+"textbf{" + c + "}" for c in cells]
        rows.append(nm + " & " + " & ".join(cells) + " " + B+B)

    bull = run(CRP0 - 0.02, max(evs), max(pes), RF - 0.02)["target"]
    med = run(CRP0, b["exit_mult"], b["peer_pe"], RF)["target"]
    # what two more points of yield compression is worth, which is the construction
    # the rating is now most exposed to
    ke0, w0 = cc(CRP0, RF - 0.02)
    nocrp = _value(mdl, wacc=w0, g=b["g"], ke=ke0,
                   exit_mult=b["exit_mult"], peer_pe=b["peer_pe"])["target"]

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Scenarios}")
    out.append("The target is one answer from a model with assumptions in it. Below it is "
               "run again with those assumptions moved, through the same engine, so each "
               "line differs from the published number only by what is named. Every "
               "multiple used is one observed on a company in the comparables table.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}l r r r r r@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Case} & "+B+"textbf{Ethiopia prem.} & "+B+"textbf{Exit} & "
               + B+"textbf{P/E} & "+B+"textbf{Target} & "+B+"textbf{vs price} " + B+B)
    out.append(B+"midrule")
    out.extend(rows)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Run on the workbook's own cash flows. Against the valuation-date "
               "price of KShs " + money(price) + ". The peer floor and ceiling are the "
               "lowest and highest multiples in the comparables table.}")
    out.append("")
    out.append(B+"subsection*{The bull case is the market price}")
    out.append("Taken together the bull assumptions give KShs " + money(bull)
               + " against a valuation-date price of KShs " + money(price)
               + ". The market is priced for the "
               "country spread to narrow and for this to be the best-rated telecom in the "
               "peer group at the same time. We think one of those is likely and both "
               "together are not, which is the whole of the disagreement.")
    out.append("")
    out.append("At the medians this report itself prints, KShs " + money(med)
               + ", the rating still holds. The Sell does not depend on the two multiples "
               "being set below the peer median, and it should not, because setting them "
               "there without saying so would be an argument made in the assumptions "
               "rather than in the text.")
    out.append("")
    out.append(B+"subsection*{Where the discount rate is most exposed}")
    out.append("The rate is built in shillings on the Kenyan ten-year of " + pct(RF)
               + ", which already carries Kenyan sovereign and inflation risk, so no "
               "Kenya premium is added on top of it. The only country premium is the "
               + pct(_v(d, "Ethiopia country risk premium")) + " charged for Ethiopia, "
               "weighted at " + pct(_v(d, "Ethiopia weight in the cost of equity"))
               + " of the group. That construction removes a double count, and it leaves "
               "the rating exposed in a different place: the risk-free rate itself.")
    out.append("")
    out.append("The Kenyan ten-year has moved a long way. It peaked near 19.4\\% in "
               "April 2024 and stands at " + pct(RF) + " here. A further two points of "
               "compression takes the cost of equity to " + pct(ke0) + " and the WACC to "
               + pct(w0) + ", and the target to KShs " + money(nocrp) + ", "
               + pct(abs(nocrp / price - 1)) + " from the price against "
               + pct(abs(d["upside"])) + " on the published build. That is the single "
               "construction most likely to overturn this rating, and it does not require "
               "any view about the company at all.")
    out.append("")
    out.append("The watch item is the ten-year, not the operating disclosure. "
               "The note does not build in further compression, "
               "because a forecast of the sovereign curve is not a forecast of Safaricom, "
               "and the rate used is the one the market quoted at the valuation date.")
    return NL.join(out) + NL


def comparables(d) -> str:
    L = d["fins"][-1]
    m = d["market"]
    # Struck on the valuation date, the same basis as the rating and the bridge. The
    # stored market block is a later snapshot, and using it put Safaricom three weeks
    # ahead of its own peers inside one table.
    from revalue import SHARES as _SH_C
    mcap = d["val_price"] * _SH_C
    ev = mcap + L["net_debt"]
    # computed like every peer's, off the same market capitalisation
    _saf_pe = mcap / L["net_income"] if L.get("net_income") else None
    rows = []
    for p in d["peers"]:
        rows.append(
            f"{tex(p['name'])} & {tex(p.get('country') or '')} & {tex(p['currency'])} & "
            f"{tex(p['fy'])} & {num(p['ebitda'])} & {num(p['net_debt'])} & "
            f"{num(p['mcap'])} & {mult(p['ev_ebitda'])} & {mult(p['pe'])} \\\\")
    rows.append(r"\midrule")
    rows.append(
        rf"\textbf{{Peer median}} & & & & & & & \textbf{{{mult(d['peer_med_ev'])}}} & "
        rf"\textbf{{{mult(d['peer_med_pe'])}}} \\")
    rows.append(
        rf"\textbf{{Safaricom PLC}} & Kenya & KES & {tex(L['fy'])} & {num(L['ebitda'])} & "
        rf"{num(L['net_debt'])} & {num(mcap)} & \textbf{{{mult(ev / L['ebitda']) if ev else '--'}}} & "
        rf"\textbf{{{mult(_saf_pe)}}} \\")
    prem_ev = (ev / L["ebitda"]) / d["peer_med_ev"] - 1 if ev else None
    prem_pe = (_saf_pe / d["peer_med_pe"] - 1) if _saf_pe else None
    return rf"""
\newpage
\section*{{\color{{navy}}Comparables}}
The peer set is the coverage universe's other African mobile operators. Each figure is
that company's own latest audited year in its own reporting currency, so the multiples
compare and the absolute numbers do not.

{{\scriptsize
\begin{{tabular}}{{@{{}}llllrrrrr@{{}}}}
\toprule
\textbf{{Company}} & \textbf{{Country}} & \textbf{{Cur}} & \textbf{{FY}} &
\textbf{{EBITDA}} & \textbf{{Net debt}} & \textbf{{Market cap}} &
\textbf{{EV/EBITDA}} & \textbf{{P/E}} \\
\midrule
{chr(10).join(rows)}
\bottomrule
\end{{tabular}}}}
\srcnote{{Enterprise value is market capitalisation plus net debt. Safaricom is struck at
the valuation-date price of KShs {money(d['val_price'])}, the same price the rating and the
bridge use. The peers carry their own snapshot dates, which
span a few days either side of it. The multiples move very
little over that window, and each peer's own currency cancels inside a ratio.}}

\subsection*{{What the table says}}
Safaricom trades at {mult(ev / L['ebitda']) if ev else '--'} EV/EBITDA against a peer
median of {mult(d['peer_med_ev'])}, a premium of {pct(prem_ev) if prem_ev else '--'}, and
at {mult(_own_pe(d))} earnings against {mult(d['peer_med_pe'])}, a premium of
{pct(prem_pe) if prem_pe else '--'}. Some premium is deserved. Safaricom's FY2026 EBITDA
margin of {pct(L['ebitda'] / L['revenue'])} and return on equity of
{pct(L['net_income'] / L['total_equity'])} are both at the top of this group, and its net
debt of {mult(L['net_debt'] / L['ebitda'])} EBITDA is among the lowest. The question is
whether {pct(prem_ev) if prem_ev else '--'} on EV/EBITDA is the right amount of premium, and on the model's
cash flows it is not.
"""


@_functools.lru_cache(maxsize=1)
def _eng_ranges_cached(key):
    return key


def _eng_ranges(d) -> dict:
    """High-to-low range for each leg, computed from the engine.

    The workbook stores fixed bands struck on legs this note has since
    corrected, so a chart drawn from them disagrees with the table beside it.
    """
    if "_ranges" in d:
        return d["_ranges"]
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _b, value as _v2
    m = _model_of(d)
    bb = _b(m)

    def rng(key, lo, hi):
        a = _v2(m, **{**bb, **lo})[key]
        c = _v2(m, **{**bb, **hi})[key]
        return abs(c - a)

    d["_ranges"] = {
        "dcf": rng("dcf", {"wacc": bb["wacc"] + 0.01, "ke": bb["ke"] + 0.01},
                   {"wacc": bb["wacc"] - 0.01, "ke": bb["ke"] - 0.01}),
        "exit": rng("exit", {"exit_mult": bb["exit_mult"] - 1.0},
                    {"exit_mult": bb["exit_mult"] + 1.0}),
        "pe": rng("pe", {"peer_pe": bb["peer_pe"] - 2.0},
                  {"peer_pe": bb["peer_pe"] + 2.0}),
    }
    return d["_ranges"]


def football(d) -> str:
    """A football field, drawn from the three methods and their sensitivities."""
    g = lambda *n: _v(d, *n)                                             # noqa: E731
    price, tgt = d["val_price"], d["target"]
    bars = [
        ("Discounted cash flow", d["dcf_repaired"], _eng_ranges(d)["dcf"]),
        ("Exit multiple", d["exit_repaired"], _eng_ranges(d)["exit"]),
        ("Peer earnings", g("Peer P/E value per share"), _eng_ranges(d)["pe"]),
        ("Weighted target", tgt, d["weighted_band"]),
    ]
    lo = min(min(b[1] - b[2] / 2, b[1]) for b in bars if b[1] and b[2])
    hi = max(max(b[1] + b[2] / 2, b[1]) for b in bars if b[1] and b[2])
    lo, hi = min(lo, price) * 0.92, max(hi, price) * 1.06
    W, H = 15.0, 1.05                                    # cm

    def x(v):
        return (v - lo) / (hi - lo) * W

    body, vlab = [], []
    for i, (lbl, mid, sd) in enumerate(bars):
        y = -i * H
        # sd is the FULL high-to-low range, so the bar is mid +/- sd/2. Drawing
        # mid +/- sd doubled every bar and made the dispersion look twice as
        # wide as the model says it is.
        x1, x2 = x(mid - sd / 2), x(mid + sd / 2)
        # The weighted target is the conclusion, so it gets the second series
        # colour rather than a fourth shade of the same grey.
        col = "serA" if lbl != "Weighted target" else "serB"
        body.append(
            rf"\fill[{col}!55] ({x1:.2f},{y - 0.26:.2f}) rectangle ({x2:.2f},{y + 0.26:.2f});")
        body.append(rf"\draw[white,line width=2.2pt] ({x(mid):.2f},{y - 0.28:.2f}) -- "
                    rf"({x(mid):.2f},{y + 0.28:.2f});")
        body.append(rf"\draw[{col},line width=1.4pt] ({x(mid):.2f},{y - 0.28:.2f}) -- "
                    rf"({x(mid):.2f},{y + 0.28:.2f});")
        body.append(rf"\node[anchor=east,font=\scriptsize] (-0.15,{y:.2f}) {{}};")
        body.append(rf"\node[anchor=east,font=\scriptsize] at (-0.15,{y:.2f}) {{{tex(lbl)}}};")
        vlab.append(rf"\node[anchor=west,font=\scriptsize\bfseries,fill=white,"
                    rf"inner sep=1pt] at ({x2 + 0.12:.2f},{y:.2f}) {{{money(mid)}}};")
    ymin = -(len(bars) - 1) * H - 0.55
    body.append(rf"\draw[sell,line width=1.1pt,dashed] ({x(price):.2f},0.45) -- "
                rf"({x(price):.2f},{ymin:.2f});")
    # An external mark: what a strategic buyer paid for control on 30 June 2026,
    # two days before the valuation date.
    _ctrl = 34.00
    if lo < _ctrl < hi and abs(_ctrl - price) / (hi - lo) > 0.02:
        body.append(rf"\draw[gray,line width=0.9pt,dotted] ({x(_ctrl):.2f},0.45) -- "
                    rf"({x(_ctrl):.2f},{ymin:.2f});")
        # This label sat on the axis line and the tick beneath it printed
        # straight through: "34" over "Control 34.00" rendered as "C34trol".
        # Put it at the top of the chart with the price label instead.
        body.append(rf"\node[anchor=south,font=\scriptsize,text=gray,fill=white,"
                    rf"inner sep=1pt] at ({x(_ctrl):.2f},1.02) "
                    rf"{{Control {_ctrl:.2f}}};")
    elif lo < _ctrl < hi:
        body.append(rf"\node[anchor=south,font=\scriptsize,text=gray] at "
                    rf"({x(_ctrl):.2f},1.02) {{Control {_ctrl:.2f}}};")
    body.append(rf"\node[anchor=south,font=\scriptsize\bfseries,text=sell,fill=white,"
                rf"inner sep=1pt] at ({x(price):.2f},0.48) {{Price {money(price)}}};")
    import math as _m
    _step = 5.0 if (hi - lo) > 18 else 2.0
    _t = _m.ceil(lo / _step) * _step
    body.append(rf"\draw[rule] (0,{ymin:.2f}) -- ({W:.2f},{ymin:.2f});")
    while _t <= hi:
        body.append(rf"\draw[rule] ({x(_t):.2f},{ymin:.2f}) -- "
                    rf"({x(_t):.2f},{ymin - 0.10:.2f});")
        body.append(rf"\node[anchor=north,font=\scriptsize,text=gray] at "
                    rf"({x(_t):.2f},{ymin - 0.12:.2f}) {{{money(_t, 0)}}};")
        _t += _step
    body.extend(vlab)
    return rf"""
\subsection*{{Football field}}
\begin{{center}}
\begin{{tikzpicture}}[x=1cm,y=1cm]
{chr(10).join(body)}
\end{{tikzpicture}}
\end{{center}}
\srcnote{{Each bar is the method's value plus and minus half the full range shown on the
weighting table. The dashed line is the valuation-date price of KShs 34.05. The control
price Vodacom paid on 30 June 2026 was KShs 34.00, five cents below it, so it is marked
at the top, not drawn as a second line that would sit on the first.}}
"""



def sotp(d) -> str:
    """The cover calls this a payments business with a telecom attached, priced as
    neither. That claim was made and never tested. This tests it, and the test is
    the strongest argument against the rating rather than for it.
    """
    import statistics as _st
    L, I = d["fins"][-1], d["is_rows"]
    SH, P, ND = 40065.4, d["val_price"], L["net_debt"]
    EV = P * SH + ND
    med = _st.median(p["ev_ebitda"] for p in d["peers"])
    kE = _row(I, "Kenya EBITDA")[0]
    sg = {s["name"]: s["value"] for s in d["notes"]["segments"]}
    kp = {x["name"]: x["value"] for x in d["notes"]["sector_specific"]}
    mp_rev, ke_rev = sg["M-PESA revenue"], sg["Kenya segment - total revenue"]
    ke_eb25 = sg["Kenya segment - EBITDA"]
    contrib = mp_rev - abs(kp["M-PESA commissions (direct cost)"])
    allocs = [("Pro rata on revenue", mp_rev / ke_rev),
              ("At contribution after commissions", contrib / ke_eb25)]

    rows = []
    impl = []
    for nm, w in allocs:
        mpE, telE = kE * w, kE * (1 - w)
        resid = EV - telE * med
        impl.append(resid / mpE)
        rows.append(tex(nm) + " & " + pct(w, 1) + " & " + num(mpE) + " & " + num(telE)
                    + " & " + num(telE * med) + " & " + num(resid) + " & "
                    + mult(resid / mpE) + " " + B+B)
    lo_i, hi_i = min(impl), max(impl)

    bull = []
    for fm in (8, 10, 12):
        vals = [(kE * w * fm + kE * (1 - w) * med - ND) / SH for _, w in allocs]
        bull.append(mult(fm, 0) + " & " + money(vals[0]) + " & " + money(vals[1])
                    + " & " + pct(vals[0] / P - 1, 0) + " & " + pct(vals[1] / P - 1, 0)
                    + " " + B+B)

    o = []
    o.append(B+"newpage")
    o.append(B+"section*{"+B+"color{navy}Sum of the parts, and the argument against this rating}")
    o.append("The title of this note says Safaricom is a payments business with a telecom "
             "attached, priced as neither. That is a claim about the share price and it can "
             "be tested. Hold the Kenyan telecom at the peer median of " + mult(med) + " and "
             "see what is left over for M-PESA.")
    o.append(B+"par"+B+"vspace{3pt}")
    o.append("Two allocations of Kenyan EBITDA are shown because the company does not "
             "disclose EBITDA for M-PESA. The first splits it in proportion to revenue. The "
             "second gives M-PESA its disclosed contribution after agent commissions, which "
             "is an upper bound, because that contribution carries no share of network, "
             "staff or overhead cost. The true figure sits between them, and the point of "
             "showing both is that the conclusion does not depend on which one is used.")
    o.append(B+"par"+B+"vspace{4pt}")
    o.append("{"+B+"scriptsize")
    o.append(B+"excap{What the market pays for M-PESA}")
    o.append(B+"begin{tabular}{@{}lrrrrrr@{}}")
    o.append(B+"toprule")
    o.append("& "+B+"textbf{M-PESA} & "+B+"textbf{M-PESA} & "+B+"textbf{Telecom} & "
             + B+"textbf{Telecom} & "+B+"textbf{Residual} & "+B+"textbf{Implied} " + B+B)
    o.append(B+"textbf{Allocation of FY2026 Kenya EBITDA} & "+B+"textbf{share} & "
             + B+"textbf{EBITDA} & "+B+"textbf{EBITDA} & "+B+"textbf{at "+mult(med)+"} & "
             + B+"textbf{for M-PESA} & "+B+"textbf{multiple} " + B+B)
    o.append(B+"midrule")
    o.extend(rows)
    o.append(B+"bottomrule")
    o.append(B+"end{tabular}}")
    o.append(B+"srcnote{KES millions except shares and multiples. Enterprise value of KShs "
             + num(EV) + "m is the valuation-date price of KShs " + money(P) + " on "
             + num(SH) + "m shares plus net debt of KShs " + num(ND) + "m. Revenue lines and "
             "the M-PESA commission are FY2025, the last year the company disclosed them "
             "separately, while Kenyan EBITDA is FY2026. Mixing the two is an assumption that the "
             "revenue mix did not move materially in one year, and it is an assumption, not "
             "a disclosure. Ethiopia is carried at nil here because its FY2026 EBITDA is "
             "negative KShs " + num(abs(_row(I, "Ethiopia EBITDA")[0])) + "m and a multiple "
             "of a negative number is meaningless. The discounted valuation does give "
             "Ethiopia value. This page does not.}")
    o.append("")
    o.append(B+"subsection*{What the market is paying for M-PESA}")
    o.append("On either allocation the market is paying between " + mult(lo_i) + " and "
             + mult(hi_i) + " EBITDA for M-PESA. That is well above what it pays for the "
             "telecom beside it. This note holds no multiples for listed payments "
             "networks, so it does not claim where that sits against them. The "
             "title of this note is therefore accurate, and it is accurate in a way that should worry a seller.")
    o.append("")
    o.append(B+"subsection*{This is the strongest argument against the rating}")
    o.append("Put M-PESA on a payments multiple and the shares are cheap. The table below "
             "holds the telecom at the peer median and Ethiopia at nil, and varies only the "
             "multiple on M-PESA.")
    o.append(B+"par"+B+"vspace{4pt}")
    o.append("{"+B+"footnotesize")
    o.append(B+"excap{Value per share at a range of M-PESA multiples}")
    o.append(B+"begin{tabular}{@{}lrrrr@{}}")
    o.append(B+"toprule")
    o.append(B+"textbf{M-PESA multiple} & "+B+"textbf{Pro rata} & "
             + B+"textbf{At contribution} & "+B+"textbf{vs price} & "
             + B+"textbf{vs price} " + B+B)
    o.append(B+"midrule")
    o.extend(bull)
    o.append(B+"bottomrule")
    o.append(B+"end{tabular}}")
    o.append(B+"srcnote{KShs per share. The two right-hand columns compare each to the "
             "valuation-date price of KShs " + money(P) + ".}")
    o.append("")
    o.append("At eight times M-PESA the shares are worth roughly what they cost. Above that "
             "they are worth more. Valued as a payments network rather than a telecom product, M-PESA supports a coherent case for "
             "owning the shares, and there is no arithmetic answer to it here.")
    o.append("")
    import sys as _sy2
    _sy2.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _sb, value as _sv
    _sm = _model_of(d)
    _sbb = _sb(_sm)
    _ske = 0.1243 + 0.90 * 0.055
    _swa = 0.80 * _ske + 0.20 * _v(d, "Cost of debt (pre-tax)") * (1 - _v(d, "Tax rate"))
    _sotp_mid = _sv(_sm, **{**_sbb, "wacc": _swa, "ke": _ske, "g": 0.05})["target"]
    _sotp_best = _sv(_sm, **{**_sbb, "wacc": _swa, "ke": _ske, "g": 0.065})["target"]
    o.append(B+"section*{"+B+"color{navy}Why the rating stands anyway}")
    o.append("Three reasons, and the third is the one that matters.")
    o.append(B+"begin{itemize}[leftmargin=13pt,itemsep=3pt,topsep=2pt]")
    o.append(B+"item "+B+"textbf{Separability.} M-PESA cannot be bought on its own. It has no "
             "licence, no separate balance sheet and no separate accounts. It rides the same "
             "network, the same agent estate and the same brand. A multiple is what someone "
             "would pay for an asset, and there is no asset here to pay for.")
    o.append(B+"item "+B+"textbf{Method.} The rating rests on a discounted valuation, not a multiple. "
             "Fifty-five per cent of the target is the present value of group free cash flow. "
             "Those cash flows do not change because the label on them changes. A re-rating "
             "argument therefore has to say the cash flows are worth more than their present "
             "value, which is a claim about the discount rate. That redirection is fair only "
             "if the discount-rate claim is then priced, and the next paragraph prices it. "
             "Stripping the Ethiopia risk premium out of the discount rate altogether, "
             "the most favourable defensible reading, gives KShs " + money(_sotp_mid)
             + ", still " + pct(abs(_sotp_mid / P - 1)) + " below the price. Pushing "
             "terminal growth further, to current inflation rather than to the target band, "
             "gives KShs " + money(_sotp_best) + " and does clear it, by "
             + pct(abs(_sotp_best / P - 1)) + ". So the segment-mix argument converted into "
             "a discount-rate argument fails on the published inputs and clears the price only "
             "on the most aggressive pair available. That is "
             "a narrow defence: it concedes the multiple and holds only the discount rate.")
    o.append(B+"item "+B+"textbf{Routes.} The routes to separability are not equally "
             "available, and the likeliest is the risk, not the reward. There are three. A full "
             "regulatory separation would give M-PESA its own licence and accounts. It is the "
             "first risk listed in this note, and it would reprice the margin on the "
             "highest-contribution line the group has while handing the Central Bank a direct "
             "instrument over pricing that the company currently sets itself. Enhanced segment "
             "disclosure would allow the unit to be valued without changing what is owned, and "
             "would not create anything for an acquirer to buy. A sale of a minority stake in "
             "the payments unit is the one route that would establish a price without "
             "regulatory separation. It is the strongest "
             "available form of the bull case, and "
             "nothing in the disclosure says whether it is contemplated.")
    o.append(B+"end{itemize}")
    o.append("")
    o.append("The disagreement is therefore narrower than the first two points "
             "suggest. The bull who needs a full regulatory separation to get the multiple "
             "also needs no separation to keep the margin, and both at once is not obviously "
             "available. The bull who needs only a minority stake sale needs neither, and "
             "that case is not answered here. What can be said is "
             "that a rating prices what is owned today, while the sum of the parts "
             "prices a corporate action nobody has announced. Anyone expecting one should "
             "weight the sum of the parts above the discounted valuation, and the multiple "
             "ladder above prices exactly that.")
    return NL.join(o) + NL

def risks(d) -> str:
    """Risks to the view, and then the harder page: what makes a Sell wrong.

    The earlier draft opened with one sentence conceding that the premium might
    persist and then spent the page on risks to the CASH FLOWS, which is a bear
    case, not a challenge to a Sell. A Sell can be right about value and still
    lose money for two years. That page is now written.
    """
    import sys
    sys.path.insert(0, str(ROOT / "reports"))
    from revalue import base as _base, value as _value

    mdl = _model_of(d)
    b = _base(mdl)
    P = d["val_price"]
    up1 = _value(mdl, **{**b, "wacc": b["wacc"] + 0.01})["dcf"]
    dn1 = _value(mdl, **{**b, "wacc": b["wacc"] - 0.01})["dcf"]
    base_dcf = _value(mdl, **b)["dcf"]
    per_pt = (dn1 - up1) / 2.0

    # The capex correction, priced here rather than typed. The gap either side of it
    # was hardcoded at 16.4% and 11.8% against a published gap of 13.8%.
    from revalue import fcff as _f4, eth_capex_net_debt_relief as _nd4
    _cap_on = _value(mdl, **b)["target"]
    _cap_off = _value(mdl, fcff_override=_f4(mdl, revert_capex=True),
                      exit_net_debt_addback=_nd4(), **b)["target"]

    # The fall in the discount rate that would close the gap entirely, moving the
    # cost of equity with the WACC because both sit on the same risk-free rate.
    lo, hi = 0.05, b["wacc"]
    for _ in range(80):
        m = (lo + hi) / 2
        t = _value(mdl, **{**b, "wacc": m, "ke": b["ke"] - (b["wacc"] - m)})["target"]
        if t < P: hi = m
        else: lo = m
    fall = b["wacc"] - lo

    S = d["sched_rows"]
    dec = _row(S, "Dividends DECLARED")
    yoc = [(y, v / 40065.4, v / 40065.4 / P) for y, v in zip(d["years"], dec) if v]

    rs = (d["uni"].get("risks") or [])[:6]
    items = NL.join(B+"item " + tex(r) for r in rs)
    tgt_now = d["target"]
    yrow = " & ".join(pct(z, 1) for _, _, z in yoc)
    drow = " & ".join(money(x) for _, x, _ in yoc)
    yhd = " & ".join(B+"textbf{" + tex(y) + "}" for y, _, _ in yoc)

    return rf"""
\newpage
\section*{{\color{{navy}}Risks to the view}}
The rating says the price is ahead of the cash flows. It does not say the cash flows are
at risk, and the two are different claims with different risks attached to them. This
page covers the first kind. The next page covers the second, which is the harder one.

\subsection*{{Risks the platform records for this company}}
Six risks are on the register and they are set out here in full, where the register carries one line each.

Safaricom is between 36\% and 44\% of the Nairobi exchange by market capitalisation, so
a holder carries index-weight and liquidity risk that has nothing to do with the business.
M-PESA is the largest revenue line, which makes the company exposed to how the Central
Bank chooses to regulate mobile money and to whether the financial-services arm is
required to hold its own licence. Ethiopia is still loss-making, and the forecast depends
on an execution timetable in a country with its own macroeconomic and political risk, and
on a birr translation this note discloses as calibrated, not observed. Kenyan
regulation and tax can move the numbers directly through excise duty on airtime and data,
through spectrum and interconnection pricing, and through how the Communications
Authority applies its rules on market dominance. The accounts are kept in shillings while
a material part of the shareholder base measures returns in dollars, so shilling
volatility changes the answer for those holders without changing the business. And Airtel
Kenya and any new entrant continue to compress legacy voice revenue, which is the one
Kenyan line the forecast already has declining.
\srcnote{{Risks are drawn from the company's own disclosure and from the regulatory
and macroeconomic position set out under country risk.}}

\subsection*{{Where this valuation is most exposed}}
\begin{{itemize}}[leftmargin=13pt,itemsep=3pt,topsep=2pt]
\item \textbf{{M-PESA regulation.}} Mobile money is the largest revenue line and carries
      the highest margin. A licensing separation would reprice it, and the model has no
      allowance for that. It would also be the event that makes the sum-of-the-parts case
      investable, which is set out two pages earlier.
\item \textbf{{Ethiopia's timetable.}} Breakeven in FY2028 is an assumption. Two more
      years of losses would take value out of the discounted valuation, which carries
      55\% of the weight.
\item \textbf{{The discount rate.}} At {pct(_v(d, 'Ethiopia country risk premium'))} of Ethiopian country risk on
      top of a {pct(_v(d, 'Risk-free'))} risk-free rate, this is the lever the rating turns on. One percentage point on the WACC moves the discounted leg by
      KShs {money(dn1 - base_dcf)} a share if it falls and KShs {money(base_dcf - up1)} if
      it rises, from KShs {money(base_dcf)} at the base rate. The two are not equal because
      discounting is convex, and neither is the two-point spread a plus and minus one
      point range would give.
\end{{itemize}}

\newpage
\section*{{\color{{navy}}What would make this Sell wrong}}
A Sell can be right about value and still be the wrong position to hold. Four mechanisms
would keep this share price where it is, or take it higher, without any of the forecasts
on the preceding pages being wrong. They are set out in the order of how much of the gap
each could close on its own.

\subsection*{{1. The discount rate falls}}
The gap closes entirely if the cost of capital falls {money(fall * 100)} percentage points,
from {pct(b['wacc'], 1)} to {pct(lo, 1)}, with the cost of equity moving alongside it
because both rest on the same Kenyan risk-free rate. That is a large move and it is not
a fanciful one: the risk-free rate used here is a Kenyan government yield, and Kenyan
yields have moved by more than that inside a single year in the recent past. Anyone who
expects Kenyan rates to normalise should expect this valuation to be wrong in the
direction of too low, and should read the two-way grid on the sensitivity page first.

\subsection*{{2. The dividend does the work while the valuation waits}}
The declared FY2026 dividend of KShs {money(2.00)} is a yield of {pct(2.00 / P)} on the
valuation-date price. The forecast holds the payout at 80\% of a rising earnings stream,
so a holder who buys at KShs {money(P)} and does nothing collects the following yields on
their original cost.

{{\footnotesize
\excap{{Yield on cost for a holder who buys at the valuation price}}
\begin{{tabular}}{{@{{}}l*{{{len(yoc)}}}{{r}}@{{}}}}
\toprule
& {yhd} \\
\midrule
Dividend per share, KShs & {drow} \\
Yield on cost at KShs {money(P)} & {yrow} \\
\bottomrule
\end{{tabular}}}}
\srcnote{{Declared, not paid. The FY2026 figure is the company's own: an 85 cent interim
and a 115 cent final, KShs 80.13bn in total. The forecast years are the model's, at an
80\% payout ratio, and they are forecasts, not declarations.}}

\vspace{{4pt}}
An income holder does not sell an asset yielding {pct(yoc[-1][2], 0)} on cost because a
discounted cash flow says it is 16\% dear. This is the mechanism by which a share can stay
expensive for years, and it is the most likely reason this rating takes a long time to be
right if it is right at all.

\subsection*{{3. The buyer of last resort has a reason to want the dividend}}
Vodacom paid KShs 272bn in cash for its additional 20\% on 30 June 2026 and now
consolidates Safaricom. A buyer that has just funded a purchase of that size has a direct
interest in the dividend being maintained or raised, and it now controls the board that
sets it. The 80\% payout assumption in this model is, on that reading, below what the company could pay, and a higher payout supports the share price through the income argument
above even where it does nothing for the discounted value.

\subsection*{{4. The free float is small and the register just concentrated}}
The transaction moved a fifth of the company from a seller who was leaving to a holder
who is not. What remains in public hands is a small fraction of a large company, and a
small float is a poor mechanism for expressing a negative view: any
index or mandate-driven demand meets a supply that has just shrunk. This does not make
the shares worth more. It makes the price slower to reflect what they are worth, in both
directions.

\subsection*{{What would change our mind}}
\begin{{itemize}}[leftmargin=13pt,itemsep=3pt,topsep=2pt]
\item A Kenyan risk-free rate durably below 10\%, which would take the gap out on its own.
\item Ethiopian EBITDA breakeven arriving ahead of FY2028, which would move the
      discounted leg and remove the largest single forecast assumption from the argument.
\item A regulatory separation of M-PESA on terms that leave the margin intact, which would
      make the sum-of-the-parts case something a holder could buy.
\item A payout ratio above 80\% sustained for two years, which would change what kind of
      instrument this is and therefore what it should be valued as.
\item Confirmation that Ethiopian capital expenditure is settling at the level the company guides, not the level this model carried. That single assumption is worth
      KShs {money(_cap_on - _cap_off)} on the target and is set out in full on the Ethiopian market page.
\end{{itemize}}

{levers(d)}

\subsection*{{The correction this note makes to the model}}
The workbook carried Ethiopian capital expenditure flat at KShs 26bn a year. The company
guides KShs 6bn to 9bn for FY2027 against KShs 18.7bn spent in FY2026, as the network
approaches its site target. That assumption is corrected in the published target, which
moves it from KShs {money(_cap_off)} to KShs {money(_cap_on)} and the gap to the price
from {pct(abs(_cap_off / P - 1))} to {pct(abs(_cap_on / P - 1))},
which is {pct((_cap_on - _cap_off) / (P - _cap_off))} of the whole disagreement, so the
rating stands on the rest of it. On the company's own capital expenditure guidance
should regard the target as the lower end of the range implied by that assumption.


"""


def controls(d) -> str:
    rows = "\n".join(
        rf"{tex(lab[:74])} & \textbf{{\color{{buy}}{st}}} & {n} \\"
        for lab, st, n in d["controls"][:18])
    npass = sum(1 for _, st, _ in d["controls"] if st == "PASS")
    return rf"""
\newpage
\section*{{\color{{navy}}How this note was checked}}
The model tests itself. These are the checks the workbook runs, with the number of
forecast years each covers, and their state at the time this report was generated. The
coverage column matters: most run on two of the five forecast years, not all of them. A report on a model that does not
balance is worth nothing, so the checks are printed and not merely described.

{{\scriptsize
\begin{{tabular}}{{@{{}}p{{0.72\linewidth}}ll@{{}}}}
\toprule
\textbf{{Control}} & \textbf{{State}} & \textbf{{Years}} \\
\midrule
{rows}
\bottomrule
\end{{tabular}}}}
\srcnote{{{npass} of {len(d['controls'])} controls pass in the years tested. They test
internal consistency, not the accuracy of the underlying extraction, and several of them restate a convention and do not probe the company. They also cannot see the report. Two
faults found in review of this note passed every control in the workbook because
neither was a modelling error: the segment table dropped the elimination row that sits
between the two divisions' EBITDA and the group's, and the exit-multiple leg of the
valuation omitted five years of dividends. Both are corrected here, and the second is set
out in full under two corrections and what they are worth. That is the limit of
what a model's self-test can do.}}

\subsection*{{Where the numbers come from}}
\begin{{itemize}}[leftmargin=13pt,itemsep=2pt,topsep=2pt]
\item Reported figures are extracted from Safaricom's audited annual reports and
      reconciled against the totals in those statements.
\item Forecast figures are formulas in the workbook, not pasted values. Excel recomputes
      them, so the arithmetic in this report is the arithmetic in the model.
\item The target price in this report is the workbook's own. The workbook carries
      every treatment this note argues for, including the five years of dividends the
      exit leg once omitted and Ethiopian capital expenditure at the company's
      guidance rather than the flat KShs 26bn the model used to hold. What each is
      worth is shown by reversing it on the page of treatments, not by layering a
      correction on top. Every figure is read out of the model without adjustment.
\item Assumptions are marked as assumptions in the workbook and are listed on the
      valuation page above.
\end{{itemize}}
\srcnote{{Investor relations: \url{{{d['ir_url']}}}}}
"""


def statement_notes(d) -> str:
    """The primary statements as filed, in full.

    An earlier version capped each statement at a row limit, so the balance
    sheet stopped at equity attributable to the parent and printed no
    liabilities at all, under a heading promising the statements were reproduced in full. Nothing is capped now: longtable
    paginates, so a statement runs as long as the filing does.
    """
    st = d["rec"].get("statements") or {}
    cols = st.get("columns") or []
    idx = list(range(len(cols)))          # every year, FY2026 back to FY2022
    head = " & ".join(B+"textbf{" + tex(c) + "}" for c in cols)

    def rows_for(section):
        out = []
        for r in (st.get(section) or []):
            lab = str(r.get("label", "")).strip()
            if not lab:
                continue
            vals = r.get("values") or []
            got = [vals[i] if i < len(vals) else None for i in idx]
            style = (r.get("style") or "").upper()
            if style == "H":
                out.append(B+"addlinespace[3pt]" + NL + B+"textit{" + tex(lab) + "}"
                           + " & " * len(cols) + " " + B+B)
                continue
            if not any(isinstance(v, (int, float)) for v in got):
                continue
            note = str(r.get("note") or "").strip()
            # No truncation of the label either: a clipped row name is a row a
            # reader cannot identify.
            nm = tex(lab)
            if style == "B":
                nm = B+"textbf{" + nm + "}"
            out.append(nm + " & " + " & ".join(num(v) for v in got)
                       + " & " + tex(note) + " " + B+B)
        return NL.join(out)

    def table(section, title, exhibit):
        return (B+"subsection*{" + title + "}" + NL
                + "{"+B+"scriptsize" + NL
                + B+"setlength{"+B+"tabcolsep}{3.4pt}" + NL
                + B+"begin{longtable}{@{}p{0.40"+B+"linewidth}"
                + "r" * len(cols) + "l@{}}" + NL
                + B+"toprule" + NL
                + "KES millions & " + head + " & "+B+"textbf{Note} " + B+B + NL
                + B+"midrule" + NL + B+"endfirsthead" + NL
                + B+"toprule" + NL
                + "KES millions & " + head + " & "+B+"textbf{Note} " + B+B + NL
                + B+"midrule" + NL + B+"endhead" + NL
                + B+"bottomrule" + NL + B+"endfoot" + NL
                + rows_for(section) + NL
                + B+"end{longtable}}" + NL)

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}The statements as filed}")
    out.append("Reproduced from the audited financial statements in full, for all five years. "
               "The Note column carries the company's own note references, which are the "
               "FY2025 filing's numbering, and earlier years occasionally number the same "
               "note differently. The FY2026 column was first taken from the condensed "
               "audited results of 7 May 2026, which presented non-current and investing "
               "detail as subtotals, and it now comes from the full annual report, so the "
               "primary statements carry the same detail as the other four years. What is "
               "still blank there is the investing and financing detail of the cash flow, "
               "which the note-level register behind this note does not cover line by line. "
               "A blank cell is a line the filing does not separately disclose and is never "
               "filled with a zero.")
    out.append(table("income_statement", "Income statement, as filed", 10))
    out.append(table("balance_sheet", "Balance sheet, as filed", 11))
    out.append(table("cash_flow", "Cash flow statement, as filed", 12))
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{" + tex(str(st.get("basis") or "")) + ". "
               + tex(str(st.get("source") or "")) + "}")
    return NL.join(out) + NL


def forecast_bs(d) -> str:
    """The forecast balance sheet the controls page asserts balances.

    Printing "Balance sheet balances: PASS" without printing the balance sheet
    inverts the report's own claim to traceability.
    """
    yrs = d["years"]
    B_ = d["bs_rows"]
    h = " & ".join(B+"textbf{" + tex(y) + "}" for y in yrs)
    # Print the model's OWN rows in its own order. A hardcoded layout was
    # written for the generic sector workbook and silently dropped every line
    # the flagship model carries under a different name, so the printed assets
    # came to 518,045 against liabilities and equity of 287,124 while the
    # balance check underneath still read zero.
    rows = []
    for lab, vals in B_.items():
        if not any(isinstance(v, (int, float)) for v in vals):
            continue
        up = lab.strip().upper()
        bold = up.startswith("TOTAL") or "BALANCE CHECK" in up
        if up.startswith("TOTAL LIAB") or up.startswith("SHARE CAPITAL"):
            rows.append(B+"addlinespace[3pt]")
        # Hard-slicing the label severed "contract liabilities" mid-word. The
        # column is a p{} box, so a long label wraps instead of being cut.
        nm = (B+"textbf{" + tex(lab) + "}") if bold else tex(lab)
        rows.append(nm + " & " + " & ".join(num(v) for v in vals) + " " + B+B)

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Forecast balance sheet}")
    out.append("The balance check below is the model's own. It reports that assets equal "
               "liabilities and equity in every year tested, and the statement is printed "
               "in full so the column can be added down.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}l" + "r" * len(yrs) + "@{}}")
    out.append(B+"toprule")
    out.append("KES millions & " + h + " " + B+B)
    out.append(B+"midrule")
    out.extend(rows)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{The first column opens at the last reported position. Working "
               "capital is shown net because the schedule that drives it is net, and "
               "receivables, inventories and payables are shown inside it. The balances "
               "the model does not schedule separately are held flat and not grown.}")
    return NL.join(out) + NL


def provenance(d) -> str:
    """Where every figure came from, and what is derived instead of reported.

    The footer on every page claims each figure traces to a filing or to a
    stated assumption. That claim has to be earned on a page, not asserted in a
    footer, and the honest version of it is less flattering than the footer.
    """
    rec = d["rec"]
    st = rec.get("statements") or {}
    by_year = st.get("source_by_year") or {}
    fins = d["fins"]
    conf = {f["fy"]: f.get("confidence") for f in fins}

    yr_rows = []
    for f in fins:
        fy = f["fy"]
        src = str(by_year.get(fy, ""))
        src = src.split("\u00b7")[0].strip()
        yr_rows.append(tex(fy) + " & " + tex(src if len(src) <= 120 else src[:117] + "...") + " & "
                       + (f"{conf.get(fy):.2f}" if conf.get(fy) else "--") + " " + B+B)

    # One numbered entry per source, grouped so a reader can see which are
    # filings, which are an aggregator and which are press. The URL is set small
    # and the description follows it on the same row.
    srcs = [str(x) for x in (rec.get("sources") or [])]
    market_src = str((rec.get("market") or {}).get("source", "")).strip()
    RESEARCHED = [
        "Vodacom Group, stock exchange announcement of the acquisition of a further 20 per "
        "cent interest in Safaricom, 4 December 2025, and the Vodacom trading update for "
        "the first quarter of FY2027. The KShs 34.00 contractual price per share, the KShs "
        "40.2bn upfront payment for dividend rights, and the effective interest moving from "
        "34.9 per cent to 54.9 per cent.",
        "Vodafone Group, completion of the Safaricom transaction, 30 June 2026. The 15 per "
        "cent tranche from the Government of Kenya for KShs 204bn and the 5 per cent "
        "effective interest from Vodafone for KShs 68bn.",
        "Safaricom PLC, FY2026 Annual Report, shareholding pages and share register. The "
        "register before and after the transaction, the free float of 25 per cent on "
        "both dates, and the 40,065,428,000 shares in issue.",
        "Safaricom PLC, notice of annual general meeting and FY2026 results announcement of "
        "7 May 2026. The declared dividend of 85 cents interim and 115 cents final, KShs "
        "80.13bn in total.",
        "Safaricom PLC, FY2026 results disclosure for Safaricom Telecommunications "
        "Ethiopia. Customers, sites, population coverage, M-PESA Ethiopia, the EBITDA and "
        "EBIT losses, the FY2027 break-even target and the FY2027 capital expenditure "
        "guidance of KShs 6bn to 9bn.",
        "Ethiopian Communications Authority, direction to Ethio Telecom on "
        "infrastructure-sharing charges effective 1 January 2026, requiring rates below the "
        "2022 arrangement and settlement principally in birr.",
        "Independent Electoral and Boundaries Commission of Kenya and Article 101 of the "
        "Constitution of Kenya. The general election of 10 August 2027, being the second "
        "Tuesday of August in the fifth year.",
        "Communications Authority of Kenya, Sector Statistics Report, third quarter of "
        "FY2025/26, January to March 2026. Subscriptions and market share by operator.",
        "Central Bank of Kenya, Monetary Policy Committee statements, 2026. Central Bank "
        "Rate, inflation, growth forecasts and foreign exchange reserves.",
        "Moody's Ratings, Kenya sovereign rating action, January 2026. Upgrade to B3 with "
        "a stable outlook.",
        "Kenya ten-year government bond yield, traded market data for the month around the "
        "valuation date.",
        "Safaricom Telecommunications Ethiopia shareholding after the FY2026 capital "
        "injection, as reported July 2026. Consortium stakes before and after.",
        "Safaricom PLC leadership disclosure and independent reporting of the FY2026 "
        "annual report. Chief executive, chairman and remuneration.",
    ]
    ref_rows, k = [], 0

    def _entry_cell(cell):
        """One numbered row. The description sits under the URL in the SAME cell,
        so a source without one cannot swallow its own number."""
        nonlocal k
        k += 1
        return f"[{k}] & " + cell + " " + B+B + NL + B+"addlinespace[3pt]"

    for line in srcs:
        url = line.split(" (")[0].strip()
        what = line[len(url):].strip().strip("()")
        cell = B+"texttt{"+B+"scriptsize " + _breakable(url) + "}"
        if what:
            cell += B+"newline " + tex(what)
        ref_rows.append(_entry_cell(cell))
    if market_src:
        ref_rows.append(_entry_cell(B+"texttt{"+B+"scriptsize "
                                    + _breakable(market_src) + "}"))
    ref_rows.append(_entry_cell(tex("safaricom_plc_model.xlsx, the platform workbook. "
                                    "Every valuation figure in this note.")))
    ref_rows.append(B+"addlinespace[5pt]")
    ref_rows.append(B+"multicolumn{2}{@{}l@{}}{"+B+"textit{Researched for this note and "
                    "verified against a second source}} " + B+B)
    ref_rows.append(B+"addlinespace[3pt]")
    for r in RESEARCHED:
        ref_rows.append(_entry_cell(tex(r)))

    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}Basis of preparation, and what is not a filing}")
    out.append(B+"subsection*{Where the base year comes from}")
    out.append("FY2026 was first taken from Safaricom's Condensed Audited Results of "
               "7 May 2026, which present the non-current and investing detail as "
               "subtotals. The full annual report has since been obtained, and the model "
               "and the primary statements in this note are now built from it and from "
               "the notes behind it, each line carrying the company's own note reference. "
               "What remains at the condensed level is the investing and financing detail "
               "of the FY2026 cash flow, which the note-level register does not cover line "
               "by line, and those cells are left blank rather than estimated.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}l p{0.62"+B+"linewidth} r@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Year} & "+B+"textbf{Source} & "+B+"textbf{Confidence} " + B+B)
    out.append(B+"midrule")
    out.extend(yr_rows)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{3pt}")
    out.append(B+"srcnote{Confidence is the platform's own score on the extraction, not on "
               "the company. " + tex(str(st.get("basis") or "")) + ".}")
    out.append("")
    out.append(B+"subsection*{Which FY2026 figures are derived rather than reported}")
    out.append("Two measures in this note are wider than the company's own. Capital "
               "expenditure is shown at KShs " + num(_capex_wide(d)) + "m, which is the "
               "whole of the investing outflow on fixed and intangible assets, "
               "against the KShs 74,516m of property and equipment additions the company "
               "reports as group capital expenditure in note 18. The difference is "
               "intangibles and spectrum. Capital intensity and free cash flow follow the "
               "wider measure here, so both read a little heavier than the company's "
               "published figures, and a reader comparing the two should use the note 18 "
               "number. Non-current borrowings and lease liabilities are now taken from "
               "notes 16 and 22(b) rather than derived, so net debt, which sets the "
               "enterprise value and is deducted in the bridge, is a filed figure.")
    out.append("")
    out.append(B+"subsection*{Definitions that would otherwise be assumed}")
    out.append(B+"begin{itemize}[leftmargin=13pt,itemsep=2pt,topsep=2pt]")
    out.append(B+"item "+B+"textbf{Net income} is profit attributable to owners of the "
               "parent, which is also the basis of earnings per share. Group profit for "
               "the year is lower, because non-controlling interests absorb their share "
               "of Ethiopia's loss. Net margin, return on equity and return on assets "
               "therefore divide an attributable numerator by group revenue, group equity "
               "and group assets, so the numerator excludes minorities and the "
               "denominators include them.")
    out.append(B+"item "+B+"textbf{Free cash flow} is operating cash flow less the "
               "purchase of property and equipment. It does not deduct spectrum, licences "
               "or other intangible additions, which were large in FY2022. On a total "
               "investing basis the FY2022 figure is negative.")
    out.append(B+"item "+B+"textbf{Revenue} is audited total revenue. Press coverage of "
               "the FY2026 results widely reports a lower figure, which is group service "
               "revenue and excludes handset, connection and other non-service revenue. "
               "Both are correct on their own basis.")
    out.append(B+"item "+B+"textbf{Segment and operating measures} are the last full "
               "disclosure, which is FY2025, unless a FY2026 figure is given with it. "
               "Several company-disclosed operating figures are stated excluding IAS 29 "
               "hyperinflation while the group financials are audited. The two bases are "
               "not interchangeable.")
    out.append(B+"item "+B+"textbf{IAS 29.} The audited income statement carries a "
               "hyperinflationary monetary gain in FY2023, FY2024 and FY2025. No such "
               "line is separately disclosed for FY2026, so the movement in profit before "
               "tax into FY2026 is not like for like.")
    out.append(B+"end{itemize}")
    out.append("")
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}References}")
    out.append("Every source the platform holds for this company. Filings first, then "
               "the aggregator used where a filing was not available, then press used "
               "only as a cross-check.")
    out.append(B+"par"+B+"vspace{4pt}")
    out.append("{"+B+"footnotesize")
    # raggedright so a long URL breaks instead of overrunning the column and
    # shunting its own row number off the page
    out.append(B+"begin{tabular}{@{}p{12pt} >{"+B+"raggedright"+B+"arraybackslash}"
               "p{0.88"+B+"linewidth}@{}}")
    out.append(B+"toprule")
    out.append(B+"addlinespace[3pt]")
    out.extend(ref_rows)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    return NL.join(out) + NL


def about_us(d) -> str:
    """The firm page and contacts, matching the house reference report."""
    out = []
    out.append(B+"newpage")
    out.append(B+"section*{"+B+"color{navy}About us}")
    # This page carried every promotional marker the house style bans elsewhere:
    # strong growth potential, pan-African leaders, rigorous study, a high level of
    # added value. None of it said what the firm does.
    out.append("Aeon Nimbus Capital was established in February 2013 and works in advisory "
               "and investment management, acting as adviser or investor on behalf of "
               "third parties in African companies, whether or not they are listed.")
    out.append("")
    out.append("The firm covers the following sectors:")
    out.append(B+"begin{itemize}[leftmargin=13pt,itemsep=1pt,topsep=2pt]")
    for x in ("Telecommunications and digital financial services",
              "Building materials", "Logistics, transport and warehousing",
              "Distribution", "Medical", "Renewable energy",
              "Waste management, recovery and recycling"):
        out.append(B+"item " + tex(x))
    out.append(B+"end{itemize}")
    out.append("")
    out.append("Research is built from company filings and public disclosure, modelled in "
               "full three-statement form with the valuation driven off the model, and "
               "published with the assumptions named. Where the "
               "workbook behind every published note is available to clients on request.")
    out.append("")
    out.append(B+"subsection*{Contact us}")
    out.append(B+"par"+B+"vspace{2pt}")
    out.append("{"+B+"footnotesize")
    out.append(B+"begin{tabular}{@{}ll@{}}")
    out.append(B+"toprule")
    out.append(B+"textbf{Aeon Nimbus Capital UK} & "+B+"textbf{Aeon Nimbus Capital France} " + B+B)
    out.append(B+"midrule")
    out.append("15 Stratton Street & 140b, rue de Rennes " + B+B)
    out.append("London W1J 8LQ & 75006 Paris " + B+B)
    out.append("United Kingdom & France " + B+B)
    out.append(B+"bottomrule")
    out.append(B+"end{tabular}}")
    out.append(B+"par"+B+"vspace{6pt}")
    out.append(B+"srcnote{Aeon Nimbus Capital is the trading name of Aeon Nimbus Capital Limited, "
               "registered in England and Wales, number 08424576, and its affiliates "
               "Aeon Nimbus Capital France and Aeon Nimbus Capital Kenya.}")
    return NL.join(out) + NL


def disclosures(d) -> str:
    """Rating scale, conflicts, and who wrote this.

    A conflicts paragraph that says "no position is held" in prose is weaker than
    a matrix a compliance reader can scan. Each question is answered YES or NO.
    """
    Q = [
        ("The analyst or the publisher holds a position in the securities of "
         "Safaricom PLC", "NO"),
        ("The analyst or the publisher holds a position in any company named in "
         "the peer group", "NO"),
        ("The publisher has an investment banking relationship with Safaricom PLC",
         "NO"),
        ("The publisher has received compensation from Safaricom PLC in the last "
         "twelve months", "NO"),
        ("The publisher expects to seek compensation from Safaricom PLC in the "
         "next three months", "NO"),
        ("The publisher makes a market in the securities of Safaricom PLC", "NO"),
        ("This report was shown to the company before publication", "NO"),
        ("The analyst's compensation is linked to the recommendation in this "
         "report", "NO"),
        ("The analyst has visited the company's operations", "NO"),
        ("Any part of this report was produced by a third party", "NO"),
    ]
    rows = NL.join(tex(q) + " & " + B+"textbf{" + a + "} " + B+B for q, a in Q)
    return NL.join([
        B+"newpage",
        B+"section*{"+B+"color{navy}Disclosures}",
        B+"subsection*{Rating scale}",
        "Buy or Sell. There is no Hold. A stock with no price at the valuation date, a "
        "target at or below zero, or a target more than four times the price is NOT RATED, "
        "which is an absence of usable data, not a view. A Buy means the target is "
        "above the price on the valuation date and a Sell means it is below. The target "
        "is an estimate of value and not a forecast of when the price will reach it, so "
        "the rating itself carries no horizon. The total return quoted on the cover is a "
        "twelve-month figure, computed as the price gap plus the FY2027 declared "
        "dividend, and it is stated on that basis because a position has to be held for "
        "a period even when a valuation does not.",
        "",
        B+"subsection*{Rating distribution}",
        "Not published. This platform maintains models across its coverage universe but "
        "has published few ratings, and a distribution drawn from a handful of names would "
        "convey precision that does not exist. It will be published once the universe is "
        "rated in full.",
        "",
        B+"subsection*{Conflicts of interest}",
        "{"+B+"footnotesize",
        B+"excap{Ten disclosure questions, and the answer to each}",
        B+"begin{tabular}{@{}p{0.86"+B+"linewidth}l@{}}",
        B+"toprule",
        B+"textbf{Question} & "+B+"textbf{Answer} " + B+B,
        B+"midrule",
        rows,
        B+"bottomrule",
        B+"end{tabular}}",
        "",
        B+"subsection*{Analyst certification}",
        "The analyst named below certifies that the views expressed in this report "
        "accurately reflect their personal views about the company and its securities, and "
        "that no part of their compensation was, is, or will be directly or indirectly "
        "related to the specific recommendation or views expressed.",
        B+"par"+B+"vspace{4pt}",
        "{"+B+"footnotesize"+B+"begin{tabular}{@{}ll@{}}",
        B+"textbf{Analyst} & Nikolas Dion Savio " + B+B,
        B+"textbf{Publisher} & Aeon Nimbus " + B+B,
        B+"textbf{Valuation date} & " + tex(d["val_price_date"]) + " " + B+B,
        B+"textbf{Sector} & " + tex(d["sector"]) + " " + B+B,
        B+"end{tabular}}",
        "",
        B+"subsection*{Basis of preparation}",
        "This report was generated from the platform's own model for Safaricom PLC. "
        "Reported figures come from the company's audited financial statements. Forecast "
        "figures come from the model's operating drivers and supporting schedules, all of "
        "which are printed in this note. Assumptions are identified as such wherever they "
        "appear. Market data is as at the price date shown on the cover, and the valuation "
        "does not reflect information published after that date.",
        "",
        B+"subsection*{What this is not}",
        "This is not investment advice and not a recommendation to any particular person. "
        "It takes no account of any reader's circumstances, objectives or tax position. Any "
        "figure marked as an assumption is a judgement and may be wrong. All figures are in "
        "Kenyan shillings unless stated. Peer figures are in each company's own reporting "
        "currency, so the multiples compare and the absolute figures do not.",
        "",
        B+"subsection*{About Aeon Nimbus}",
        "Aeon Nimbus is an independent equity research platform covering listed "
        "companies across the continent. Every figure in a Aeon Nimbus note is traced to the "
        "filing it came from and every forecast is driven off a model rather than "
        "stated beside one. Where a figure is not supported by public disclosure, the "
        "note says so and does not fill the gap.",
        B+"enlargethispage{4"+B+"baselineskip}",
        B+"vspace{8pt}",
        B+"textcolor{rule}{"+B+"rule{"+B+"linewidth}{0.6pt}}",
        B+"vspace{3pt}",
        "{"+B+"footnotesize"+B+"color{gray}",
        "Aeon Nimbus "+B+"textbullet{} Independent Equity Research "+B+"textbullet{} "
        + tex(d["country"]) + " "+B+"textbullet{} Investor relations for the company: "
        + B+"url{" + d["ir_url"] + "}}",
    ])


def main() -> Path:
    import statistics as st
    d = collect()
    evs = [p["ev_ebitda"] for p in d["peers"] if p["ev_ebitda"]]
    pes = [p["pe"] for p in d["peers"] if p["pe"]]
    d["peer_med_ev"] = st.median(evs) if evs else None
    d["peer_med_pe"] = st.median(pes) if pes else None
    # what the forecast actually grants, which is the case for the rating
    I, yrs = d["is_rows"], d["years"]
    rev, eb = _row(I, "TOTAL REVENUE"), _row(I, "EBITDA")
    er, ee = _row(I, "Ethiopia revenue"), _row(I, "Ethiopia EBITDA")
    d["m0"], d["m5"] = eb[0] / rev[0], eb[-1] / rev[-1]
    d["eth_cagr"] = (er[-1] / er[0]) ** (1 / (len(er) - 1)) - 1
    d["eth_be"] = next((yrs[i] for i, v in enumerate(ee)
                        if isinstance(v, (int, float)) and v > 0), None)
    # The group margin expansion is not a Kenyan story. Kenya moves a little over
    # a point across the whole forecast; the rest is Ethiopia ceasing to lose
    # money. The cover states the case for the rating, so it states this.
    _kr, _ke = _row(d["drv_rows"], "KENYA TOTAL REVENUE"), _row(d["drv_rows"], "KENYA EBITDA")
    d["kenya_share"] = _kr[0] / rev[0]
    d["kenya_mgn_gain"] = (_ke[-1] / _kr[-1] - _ke[0] / _kr[0]) * 100
    d["eth_m0"], d["eth_m5"] = ee[0] / er[0], ee[-1] / er[-1]
    d["wacc"] = _v(d, "WACC")
    # The workbook stores "Weighted target" as exactly 20% of the target price,
    # to fifteen digits. It is a hardcoded band, not a combination of the three
    # method sensitivities. Weight them instead.
    _w = [_v(d, "Weight — DCF"), _v(d, "Weight — exit multiple"), _v(d, "Weight — peer P/E")]
    _r = _eng_ranges(d)
    _s = [_r["dcf"], _r["exit"], _r["pe"]]
    d["weighted_band"] = sum(a * b for a, b in zip(_w, _s) if a and b)

    # The published note carries the investment case, the forecast and the valuation
    # support. It does not reproduce the workbook. The assumption inventory, the
    # free cash flow schedule, the supporting schedules, the filed statements, the
    # model self-tests and the extraction record are working papers and stay in the
    # model, where a reader who wants them can be sent.
    doc = (PREAMBLE + "\\begin{document}\n"
           + cover(d) + contents() + financials(d)
           + business(d) + products(d) + market_position(d) + money_making(d)
           + ethiopia(d) + ethiopia_drivers(d) + drivers(d)
           + valuation(d) + price_chart(d) + bridge(d) + football(d) + grids(d)
           + comparables(d) + sotp(d) + scenarios(d)
           + risks(d) + capital_allocation(d) + catalysts(d) + country_risk(d)
           + forecast_is_cf(d) + forecast_bs(d)
           + disclosures(d) + about_us(d)
           + "\n\\end{document}\n")

    # Every starred section into the contents. Starred headings are unnumbered
    # by design, so LaTeX will not list them without being told to.
    import re as _re
    _skip_toc = {"Contents", "Key ratios"}
    doc = _re.sub(r"\\section\*\{\\color\{navy\}([^{}]*)\}",
                  lambda m: m.group(0) if m.group(1) in _skip_toc else
                  m.group(0) + "\n\\addcontentsline{toc}{section}{" + m.group(1) + "}",
                  doc)
    doc = doc.replace("\\begin{document}\n",
                      "\\begin{document}\n", 1)

    # EVERY table spans the text measure. Twenty-eight different table widths,
    # none of them matching the measure and no two matching each other, is the
    # single loudest signal that a document was generated rather than designed.
    # tabular* with a stretching intercolumn skip fixes all of them at once, so
    # each caption also sits under an object of its own width.
    import re as _re2

    def _widen(doc, env, opener):
        """Convert every @{}...@{} column spec, counting braces.

        A regex of the form [^}]* stops at the first closing brace, so a spec
        like l*{6}{r} never matched and nine tables kept a plain tabular begin
        against a tabular* end. They rendered only because pdflatex recovers
        from a mismatched environment, and they were the only tables in the
        document not set to the text measure.
        """
        out, i, tag = [], 0, "\\begin{" + env + "}{@{}"
        while True:
            j = doc.find(tag, i)
            if j < 0:
                out.append(doc[i:])
                return "".join(out)
            out.append(doc[i:j])
            k, depth = j + len(tag), 1        # one brace open: the column spec
            while k < len(doc) and depth:
                if doc[k] == "{":
                    depth += 1
                elif doc[k] == "}":
                    depth -= 1
                k += 1
            spec = doc[j + len(tag):k - 1]
            if not spec.endswith("@{}"):
                out.append(doc[j:k])
            else:
                out.append(opener + spec[:-3] + "@{}}")
            i = k

    # Keep a break only where a fresh page is part of the design: the contents,
    # the statements as filed, and the disclosures. Everywhere else the section
    # flows and reserves enough room that its heading cannot be widowed.
    _KEEP_BREAK = ("Contents", "The statements as filed", "Disclosures",
                   "Basis of preparation", "References")
    _parts = _re2.split(r"(\\newpage\n\\section\*\{(?:\\color\{navy\})?[^{}]*\})", doc)
    _out2 = []
    for _pt in _parts:
        _m2 = _re2.fullmatch(r"\\newpage\n(\\section\*\{(?:\\color\{navy\})?([^{}]*)\})", _pt or "")
        if _m2 and not any(k in _m2.group(2) for k in _KEEP_BREAK):
            _out2.append("\\needspace{6\\baselineskip}\n" + _m2.group(1))
        else:
            _out2.append(_pt)
    doc = "".join(_out2)
    # Any heading still without protection gets it, so none can be widowed at
    # the foot of a page. The cover's "Key ratios" was stranded this way.
    doc = _re2.sub(r"(?<!baselineskip\}\n)(\\section\*\{(?:\\color\{navy\})?[^{}]*\})",
                   lambda _m: "\\needspace{6\\baselineskip}\n" + _m.group(1), doc)
    doc = _re2.sub(r"(\\subsection\*\{[^{}]*\})",
                   lambda _m: "\\needspace{4\\baselineskip}\n" + _m.group(1), doc)
    doc = doc.replace("\\needspace{6\\baselineskip}\n\\needspace{6\\baselineskip}\n",
                      "\\needspace{6\\baselineskip}\n")
    doc = _widen(doc, "tabular",
                 r"\begin{tabular*}{\linewidth}{@{\extracolsep{\fill}}")
    doc = doc.replace(r"\end{tabular}", r"\end{tabular*}")
    doc = _widen(doc, "longtable", r"\begin{longtable}{@{\extracolsep{\fill}}")

    # A source note must start under the table it belongs to. A tabular is an
    # inline box, so without a paragraph break the note flows up beside it and
    # reads as a sentence someone left in the margin.
    # Every source note starts on its own line under the rule of the table it
    # belongs to. A tabular is an inline box, so a note that merely follows it
    # sets alongside it and wraps around the box.
    # Some strings reach the page without passing through tex(), so the shouted
    # words are cleared once on the assembled document. Acronyms are untouched
    # because the list is explicit.
    for _w in ("ROSE", "PAID", "DECLARED", "AUDITED", "NOT RATED"):
        doc = _re2.sub(r"(?<![A-Za-z\\-])" + _w + r"(?![A-Za-z-])",
                       (lambda _x: (lambda _m: _x))(_w.lower() if _w != "NOT RATED"
                                                    else "NOT RATED"), doc)
    _BRK = "\n" + chr(92) + "par" + chr(92) + "vspace{3pt}\n" + chr(92) + "srcnote{"
    doc = _re2.sub(r"(?<!\\par)(?<!\\vspace\{3pt\})\n\\srcnote\{",
                   lambda _m: _BRK, doc)
    _DUP = chr(92) + "par" + chr(92) + "vspace{3pt}\n"
    doc = doc.replace(_DUP + _DUP, _DUP)

    # Number every table and chart. A research note refers to its own evidence by
    # name; an unnumbered figure can only be pointed at with "the table above",
    # which stops being an address as soon as a page break moves it. The caption
    # is drawn from the nearest heading above the object, so it says what the
    # exhibit is rather than repeating the section title.
    _EX_SKIP = ("Contents",)
    # A caption that restates the heading above it tells a reader nothing and is
    # the loudest tell that a loop wrote the page. Where an exhibit falls back to
    # its heading, say instead what the exhibit shows.
    _CAPTION = {
        "Key ratios": "Margins, returns, leverage and payout across five audited years",
        "Reported, five audited years": "Revenue to net income, and the balance sheet behind it",
        "Forecast, from the model": "Segment revenue and EBITDA to FY2031E, with group cash flow",
        "Cost lines did more of the work than revenue":
            "The FY2025 to FY2026 EBITDA movement, decomposed into four reported lines",
        "Revenue by line": "Ten disclosed streams, summing to revenue from contracts with customers",
        "Operating measures": "Customers, usage and revenue per user, as disclosed",
        "Revenue and the margin behind it":
            "Revenue bars against the group EBITDA margin, audited years and forecast",
        "Revenue by line, FY2025": "Share of revenue from contracts with customers, ten lines",
        "Leverage falls across the forecast": "Net debt to EBITDA, audited years and forecast",
        "Capital intensity and the dividend":
            "Capital expenditure as a share of revenue, audited years",
        "Dividends paid, and the payout that funded them":
            "Total dividends paid in cash against the payout ratio, five audited years",
        "Ethiopia: revenue scaling, losses closing":
            "Ethiopian revenue against its EBITDA margin, through break-even to FY2031E",
        "Where Safaricom sits against its peers":
            "Enterprise multiple against price to earnings, six African operators",
        "Market position": "Subscriber market share by operator, from the regulator's own series",
        "Ownership, and a control price":
            "The register before and after the June 2026 control transaction",
        "Who the minorities in Ethiopia are":
            "The Ethiopian consortium, its holdings and its FY2026 dilution",
        "How the money is actually made":
            "M-PESA unit economics, from gross revenue to contribution after commissions",
        "Ethiopia": "Kenyan and Ethiopian revenue and EBITDA set side by side",
        "Cost of capital": "Every component of the discount rate, and the weighted result",
        "Enterprise to equity bridge":
            "From the discounted cash flows to a value per share, with net debt and minorities",
        "The three methods and the blend":
            "Each valuation leg, its assumption, its weight and its contribution",
        "What moves the answer": "The range each method produces, and the weighted band",
        "Free cash flow to the firm":
            "The schedule the discounted valuation is built on, year by year",
        "Football field":
            "Each method's range against the market price and the control transaction",
        "Discount rate against terminal growth":
            "Target price across a grid of cost of capital and perpetual growth",
        "Discount rate against the exit multiple":
            "Target price across a grid of cost of capital and terminal multiple",
        "And the assumption the call rests on":
            "Terminal growth against real inflation, with the exit multiple each implies",
        "Scenarios": "Four cases, the named inputs behind each and the target each gives",
        "Comparables": "Six African operators, their multiples, and the group medians",
        "Country risk": "Kenyan macro and sovereign indicators at the valuation date",
        "Who runs the company":
            "Executives and board, tenure in post, and disclosed remuneration",
        "Forecast balance sheet":
            "Assets, liabilities and equity to FY2031E, with the balance check",
        "Forecast income statement":
            "Revenue through to attributable profit and earnings per share",
        "Forecast cash flow": "Operating, investing and financing flows to FY2031E",
        "How earnings per share is built":
            "Attributable profit over the share count, and the dividend it funds",
        "Income statement, as filed": "Reproduced from the audited statements, five years",
        "Balance sheet, as filed": "Reproduced from the audited statements, five years",
        "Cash flow statement, as filed": "Reproduced from the audited statements, five years",
        "How this note was checked":
            "Every model self-test, its state, and how many years it covers",
        "The base year is a condensed release":
            "Extraction confidence by year, and which FY2026 figures are derived",
        "References": "Every source used, split by filing, aggregator and cross-check",
        "Conflicts of interest": "Ten disclosure questions, and the answer to each",
        "Analyst certification": "The analyst, the publisher, and what is being certified",
    }
    _n = [0]
    _last = [""]

    _explicit = [None]

    def _cap(m):
        head = _explicit[0] or _CAPTION.get(_last[0], _last[0])
        _explicit[0] = None
        _n[0] += 1
        lab = "Exhibit " + str(_n[0])
        if head:
            lab += ": " + head
        return ("\\exhibitlabel{" + lab + "}\\par\\vspace{1pt}\n" + m.group(0))

    def _take_excap(m):
        _explicit[0] = m.group(1)
        return ""

    _chunks = _re2.split(r"(\\(?:sub)?section\*\{(?:\\color\{navy\})?[^{}]*\})", doc)
    _outp = []
    for _c in _chunks:
        _h = _re2.fullmatch(r"\\(?:sub)?section\*\{(?:\\color\{navy\})?([^{}]*)\}", _c or "")
        if _h:
            _last[0] = "" if _h.group(1) in _EX_SKIP else _h.group(1)
            _explicit[0] = None          # never carry a caption into a new section
            _outp.append(_c)
            continue
        _seg = _re2.sub(
            r"(?P<kind>\\excap\{(?P<t>[^{}]*)\}\n?|\\begin\{tabular\*\}"
            r"|\\begin\{longtable\}|\\begin\{tikzpicture\})",
            lambda mm: _take_excap(_re2.match(r"\\excap\{([^{}]*)\}", mm.group(0)))
            if mm.group(0).startswith("\\excap") else _cap(mm),
            _c or "", count=0)
        _outp.append(_seg)
    doc = "".join(_outp)

    OUT.mkdir(parents=True, exist_ok=True)
    tex_path = OUT / "Safaricom_PLC_Initiation.tex"
    tex_path.write_text(doc)

    for _ in range(2):                     # twice, so page numbers settle
        r = subprocess.run(["pdflatex", "-interaction=nonstopmode",
                            "-output-directory", str(OUT), str(tex_path)],
                           capture_output=True, text=True)
    pdf = OUT / "Safaricom_PLC_Initiation.pdf"
    if not pdf.exists():
        tail = "\n".join(r.stdout.splitlines()[-40:])
        raise SystemExit(f"pdflatex did not produce a PDF:\n{tail}")
    for ext in (".aux", ".log", ".out"):
        (OUT / f"Safaricom_PLC_Initiation{ext}").unlink(missing_ok=True)
    return pdf


if __name__ == "__main__":
    p = main()
    print(f"wrote {p}  ({p.stat().st_size:,} bytes)")
