"""The in-platform assistant, on Groq.

It answers from the platform's own data, moves the user around, and proposes
edits that a human approves. It does not write anything itself.

Two rules shape the whole design.

FIGURES COME FROM TOOLS, NEVER FROM THE MODEL. Every number in an answer has to
have been returned by one of the read tools below, and the answer says which
company and year it came from. A language model that is allowed to recall a
revenue figure will eventually recall one that is wrong, and on this platform a
wrong number reaching a client is the failure that matters most. The system
prompt forbids it and the tools make it unnecessary.

WRITES ARE PROPOSALS. The model cannot change a company, an assumption or a
figure. It returns a proposed change, the platform shows it as a before-and-
after card, and a human presses Approve. The change is then recorded against
that person, not against the assistant, so the history stays meaningful.

Groq is the provider because this has to stay cheap at 500 companies: the
extraction path is deterministic and uses no model at all, so inference is only
ever spent on conversation.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Google speaks OpenAI's dialect at this address, tool calls included, so one
# code path serves both and adding a provider costs a line in the chain.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_PREFIX = "gemini:"

# TOKENS PER MINUTE is the binding constraint, not tokens per day.
#
# One question costs about 7,700 tokens over four tool-calling round trips, each
# resending the conversation, the tool results and the tool schemas. Measured on
# 2026-08-23 against gpt-oss-120b, not estimated: "what is safaricom FY2026
# revenue and ebitda margin" came to 7,732 across 4 calls.
#
# That is the one good thing to come out of the Llama retirement. The old chain
# cost about 16,700 a question against a 12,000 allowance, so a single question
# did not fit inside a minute. It now does, with room to spare. Against that,
# the free per-minute allowances are:
#
#     openai/gpt-oss-120b          8,000 TPM      200,000 TPD   (Groq)
#     qwen/qwen3.6-27b             8,000 TPM      200,000 TPD   (Groq)
#     gemini-3.6-flash            250,000 TPM        ~250 RPD   (Google)
#
# Groq retired the whole Llama 3.3 line, and this chain went on asking for
# llama-3.3-70b-versatile until every question came back as a raw Groq error.
# Only two models Groq still serves can call a tool at all: gpt-oss-120b and
# qwen3.6-27b. gpt-oss-20b answers 400 tool_use_failed and the compound models
# reject tools outright, so neither belongs in a chain whose whole purpose is
# tool calling. Checked against the live /models endpoint on 2026-08-23.
#
# A question now fits inside a Groq minute, so the assistant is usable on Groq
# alone. Two questions in quick succession still will not fit, which is what a
# Gemini key is for.
#
# Gemini's per-minute allowance is twenty times the question. It goes first when
# its key is present, and the assistant is usable; without it the Groq entries
# still work, one question at a time.
#
# THE TRADE: Google says free-tier prompts may be used to improve its products,
# and this platform holds proprietary research. Set AI_PREFER_GROQ=1 to put the
# Groq entries back in front and accept the rate limit.
MODEL = os.environ.get("AI_MODEL", "openai/gpt-oss-120b")
_GEMINI = GEMINI_PREFIX + os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# Groq entries ordered by per-minute allowance, since that is what a question
# runs into first, with the larger daily allowance breaking the tie.
_GROQ_CHAIN = [MODEL, "openai/gpt-oss-120b", "qwen/qwen3.6-27b"]

if os.environ.get("AI_MODEL_CHAIN", "1") == "0":
    MODEL_CHAIN = [MODEL]
elif os.environ.get("AI_PREFER_GROQ", "") == "1":
    MODEL_CHAIN = _GROQ_CHAIN + [_GEMINI]
else:
    MODEL_CHAIN = [_GEMINI] + _GROQ_CHAIN
# dict.fromkeys keeps the first occurrence: AI_MODEL is usually one of the
# entries below it, and a chain with the same model twice wastes a fallback.
MODEL_CHAIN = list(dict.fromkeys(m for m in MODEL_CHAIN if m))


def _endpoint(model: str) -> tuple[str, str, str]:
    """Where a chain entry is called, with what key. (url, key, bare model)."""
    if model.startswith(GEMINI_PREFIX):
        return GEMINI_URL, os.environ.get("GEMINI_API_KEY", ""), model[len(GEMINI_PREFIX):]
    return GROQ_URL, os.environ.get("GROQ_API_KEY", ""), model


def _keyed(chain: list[str]) -> list[str]:
    """Drop entries whose provider has no key, so an unset one costs nothing."""
    return [m for m in chain if _endpoint(m)[1]]

# Which models have spent their day, and when they free up. Without this every
# question pays a wasted round trip to rediscover the same limit, and that round
# trip comes out of the time budget for the answer.
_EXHAUSTED: dict[str, float] = {}


def _usable(chain: list[str]) -> list[str]:
    now = time.monotonic()
    chain = _keyed(chain) or chain
    live = [m for m in chain if _EXHAUSTED.get(m, 0) < now]
    return live or list(chain)          # all spent: try anyway rather than refuse


TIMEOUT = 20.0
MAX_STEPS = 4                      # tool calls per question, then it must answer

# The whole answer has to come back before the proxy in front of this gives up.
# Netlify's is about 26 seconds, and the retries added for rate limits and
# fumbled tool calls pushed a hard question past it: a 504 and an empty reply,
# which reads to the analyst as the assistant being broken. Better a short
# answer from what it has than nothing at all.
BUDGET = 18.0        # the proxy gives up near 26; leave room for the round trip

SYSTEM = """You are the AI analyst inside Aeon Nimbus, a platform covering
African listed companies. You work alongside the human analyst who owns the
coverage, and you answer investors reading it.

NEVER state a figure a tool has not returned in this conversation. You have no
reliable memory of any company's numbers. Need one? Call a tool. Tool has not
got it? Say so. Never estimate, never round from memory, never fill a gap with
a plausible number. A wrong figure here reaches a client.

Give the fiscal year with every figure, and say if the platform marked it
calculated rather than reported.

The same rule covers NAMES, not only numbers. Products, subsidiaries, brands,
executives, segments: write the name exactly as the tool returned it, or leave
it out. Do not tidy it, expand it, or add a second name beside it. Asked what
Safaricom does, one model wrote "the M-Pay mobile money platform (M-Pesa)" —
M-Pay does not exist, and a client reading that has been told something untrue
about the company. If the tool gave you a one-line description, stay inside it.

Currencies differ between companies. Never add, rank or average across them
without saying so.

You can look up a figure, a series with its growth, a computed ratio; screen and
rank coverage; compare companies; read filed statements with their page; read a
model's controls; and say what changed and who changed it. Call `list_metrics` if
unsure what exists. Chain tools when a question needs more than one.

WHERE A VALUE MAY COME FROM decides which tool you use, and this is the line
you must not cross:

- The analyst typed the value in this conversation, or a tool returned it, or
  they told you to copy it from a named filing they gave you. Then call
  `apply_change`. It writes immediately, is recorded against them, and can be
  put back in one click.
- You worked the value out, inferred it, or remember it. Then call
  `propose_change`. They see the before and after and decide.

If you are not certain which case you are in, it is the second one. Never write
a figure nobody gave you. Say what you applied and what it replaced.

Only an analyst may change data. If `apply_change` says the user lacks access,
tell them plainly and do not try again.

You also direct people around the platform. Someone asking where something is,
or how to do something, wants `where_is` and then `navigate` — open the page for
them rather than describing where to click. Answer the question and take them
there in the same turn.

ANSWER PROPERLY. A one-line answer to a real question is a worse answer. Give
what was asked, then the context that makes it useful:

- the figure, its fiscal year, its currency and unit
- the trend, if a series was returned: what it did and over how long
- the composition, if segments came back: the lines and what each contributes
- where it came from: the filing, and the page if a tool gave one
- anything the platform flagged, such as a failing control or a calculated
  rather than reported figure

Use short markdown. `##` for a heading when there is more than one part,
`- ` for lists, `**bold**` for a label at the start of a line, and a table when
comparing companies or years. Never bold a number inside a sentence.

Asked what a company DOES, answer from the business line the record gives you
and the reported segments, with their figures. That is the platform's own
description. Do not enlarge on it from memory.

End every answer with a line beginning "Next:" offering two or three specific
things to ask next, each a real question about this company or this coverage,
separated by " · ". Not "let me know if you need anything else".

Write plainly, as an analyst writes to a colleague. Never: delve, robust,
crucial, pivotal, vital, landscape, tapestry, testament, showcase, leverage as a
verb, seamless, comprehensive, holistic, meticulous, underscore, foster, garner,
unlock, empower, transformative. Say "is", not "serves as". Say "has", not
"boasts". No "not only X but also Y". No trailing "-ing" clause explaining why
it mattered. One em-dash at most. Straight quotes.

No preamble, no filler, no narrating what you are about to do, and never show
your reasoning: the analyst reads the answer, not the working."""


# Where things live. The keys are what a person asks for, the values are the tab
# as it is actually labelled in the interface. Taken from the template's own tab
# list, because a locator that invents a page is worse than no locator.
WHERE = {
    "overview":   ("Overview", "company",
                   "the rating, target price, upside and the one-line case"),
    "report":     ("Report", "company",
                   "the full written research note, section by section"),
    "studio":     ("Data Studio", "company",
                   "the editable model grid, where figures are changed and recomputed"),
    "financials": ("Financials", "company",
                   "revenue, EBITDA, EBIT, net income, capex and the ratios by year"),
    "statements": ("Statements", "company",
                   "the filed income statement, balance sheet and cash flow, with page numbers"),
    "valuation":  ("Valuation", "company",
                   "the DCF, WACC build, terminal value, peer multiples and the weighted target"),
    "peers":      ("Peers", "company", "the peer set and peer medians"),
    "history":    ("History", "company",
                   "every change to this company: what, when, who, and put-back"),
    "kpis":       ("KPIs", "company", "the operating drivers for the sector"),
    "profile":    ("Profile", "company", "what the company does, its listing and its currency"),
    "sources":    ("Sources & QC", "company",
                   "the filings each figure came from, and the failing quality checks"),
    "coverage":   ("", "coverage", "every company in coverage, searchable and sortable"),
    "changes":    ("", "history", "the change log across all companies, with put-back"),
    "admin":      ("", "admin", "people and access, admins only"),
}

# What someone might call a thing, mapped to the key above. Asked "where is the
# DCF", the answer has to be the Valuation tab, not a shrug.
_ALIAS = {
    "dcf": "valuation", "wacc": "valuation", "target price": "valuation",
    "target": "valuation", "fair value": "valuation", "terminal value": "valuation",
    "discount rate": "valuation", "multiple": "valuation", "upside": "overview",
    "rating": "overview", "recommendation": "overview", "buy": "overview",
    "sell": "overview", "thesis": "overview", "case": "overview",
    "note": "report", "research": "report", "write-up": "report",
    "model": "studio", "grid": "studio", "spreadsheet": "studio",
    "edit": "studio", "assumption": "studio", "scenario": "studio",
    "recompute": "studio", "sheet": "studio", "workbook": "studio",
    "revenue": "financials", "ebitda": "financials", "margin": "financials",
    "capex": "financials", "net debt": "financials", "ratio": "financials",
    "growth": "financials", "earnings": "financials", "profit": "financials",
    "balance sheet": "statements", "cash flow": "statements",
    "income statement": "statements", "filing": "statements",
    "annual report": "statements", "page": "statements",
    "peer": "peers", "comparable": "peers", "comps": "peers",
    "change": "history", "changed": "history", "who changed": "history",
    "audit": "history", "revert": "history", "undo": "history",
    "put back": "history", "log": "history",
    "driver": "kpis", "subscriber": "kpis", "arpu": "kpis",
    "source": "sources", "provenance": "sources", "quality": "sources",
    "check": "sources", "control": "sources",
    "company": "coverage", "companies": "coverage", "universe": "coverage",
    "screen": "coverage", "list": "coverage",
    "user": "admin", "account": "admin", "access": "admin", "password": "admin",
}


def locate(thing: str) -> dict:
    """Where in the platform a person finds `thing`."""
    t = (thing or "").strip().lower()
    key = t if t in WHERE else None
    if not key:
        # longest alias first, so "income statement" beats "statement"
        for a in sorted(_ALIAS, key=len, reverse=True):
            if a in t:
                key = _ALIAS[a]
                break
    if not key:
        return {"found": False,
                "say": "That is not a page I know. The tabs are: "
                       + ", ".join(v[0] for v in WHERE.values() if v[0])
                       + ", plus Coverage, Change log and Admin."}
    tab, target, holds = WHERE[key]
    return {"found": True, "tab": tab, "target": target, "holds": holds,
            "say": (f"the {tab} tab" if tab else f"the {key} page") + f": {holds}"}


# Which page answers a question, given the tool that answered it. Derived from
# the tools the assistant actually called rather than asked of the model: the
# model can forget to offer a link, and a link it invents is worse than none.
_TOOL_PAGE = {
    "get_company":    ("Overview", "overview"),
    "get_metric":     ("Financials", "financials"),
    "get_ratio":      ("Financials", "financials"),
    "get_statements": ("Statements", "statements"),
    "get_sources":    ("Sources & QC", "sources"),
    "get_controls":   ("Sources & QC", "sources"),
    "get_valuation":  ("Valuation", "valuation"),
    "get_peers":      ("Peers", "peers"),
    "get_history":    ("History", "history"),
    "apply_change":   ("History", "history"),
    "propose_change": ("Data Studio", "studio"),
}


def _links(steps: list[dict], resolve=None) -> list[dict]:
    """The pages worth opening after this answer, newest intent first.

    One per tab, because three links to Financials is noise. The company comes
    from whichever tool argument named one, so a question about Safaricom links
    to Safaricom's pages rather than to whatever was last on screen.
    """
    slug = None
    for st in steps:
        a = st.get("args") or {}
        for k in ("slug", "slug_or_ticker"):
            if a.get(k):
                slug = a[k]
                break
        if slug:
            break
    if slug and resolve:
        # An unresolvable name becomes no company rather than a bad one: a link
        # to a slug that is not in coverage opens nothing and says so, which is
        # worse than offering the coverage page instead.
        slug = resolve(slug)

    out, seen = [], set()
    for st in steps:
        name = st.get("tool")
        if name == "where_is":
            got = locate((st.get("args") or {}).get("thing") or "")
            if got.get("found") and got.get("tab"):
                pair = (got["tab"], got["tab"].lower())
            else:
                continue
        else:
            pair = _TOOL_PAGE.get(name)
        if not pair or pair[0] in seen:
            continue
        seen.add(pair[0])
        out.append({"label": pair[0], "tab": pair[1], "slug": slug,
                    "target": "company" if slug else "coverage"})
    return out[:4]


def _fn(name, desc, props, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props,
                       "required": required or []}}}


_CO = {"slug_or_ticker": {"type": "string", "description": "name, slug or ticker"}}

TOOLS = [
    # ---------------------------------------------------------------- finding
    _fn("search_companies", "Find companies by name, ticker, sector or country. "
        "'banks' and 'telcos' work.", {"query": {"type": "string"}}, ["query"]),
    _fn("list_metrics", "Which metrics, ratios and statements exist. Call if unsure.", {}),

    # ------------------------------------------------------------------ facts
    _fn("get_company", "One company: financials by year, sector, country, currency, market "
        "data. Call before stating a figure.", dict(_CO), ["slug_or_ticker"]),
    _fn("get_metric", "A metric by year with growth and CAGR.",
        {**_CO, "metric": {"type": "string",
                           "description": "revenue|ebitda|ebit|net income|capex|total assets|"
                                          "equity|net debt|operating cash flow|free cash flow"}},
        ["slug_or_ticker", "metric"]),
    _fn("get_ratio", "A computed ratio by year, with its definition.",
        {**_CO, "ratio": {"type": "string",
                          "description": "ebitda margin|ebit margin|net margin|roe|roa|"
                                         "capex intensity|net debt to ebitda|gearing"}},
        ["slug_or_ticker", "ratio"]),
    _fn("get_statements", "Filed statement lines with the page they came from.",
        {**_CO, "statement": {"type": "string",
                              "enum": ["income_statement", "balance_sheet", "cash_flow"]}},
        ["slug_or_ticker"]),
    _fn("get_sources", "Where a company's figures came from, per year.",
        dict(_CO), ["slug_or_ticker"]),

    # -------------------------------------------------------------- analysing
    _fn("compare", "Companies side by side on one metric, latest year.",
        {"companies": {"type": "string", "description": "comma separated"},
         "metric": {"type": "string"}}, ["companies"]),
    _fn("screen", "Filter coverage by sector, country and a ratio threshold.",
        {"sector": {"type": "string"}, "country": {"type": "string"},
         "ratio": {"type": "string", "description": "roe, ebitda margin, gearing, and so on"},
         "minimum": {"type": "number",
                     "description": "either 0.20 or 20 for twenty percent; both are understood"},
         "maximum": {"type": "number", "description": "same, either form"}}),
    _fn("rank", "Order coverage by a metric, largest first.",
        {"metric": {"type": "string"}, "sector": {"type": "string"},
         "top": {"type": "integer"}, "ascending": {"type": "boolean"}}),

    # ------------------------------------------------------------- the models
    _fn("get_valuation", "Methods, weights, target price, WACC build, recommendation.",
        dict(_CO), ["slug_or_ticker"]),
    _fn("get_controls", "The model's own checks and any failing control.",
        dict(_CO), ["slug_or_ticker"]),
    _fn("get_peers", "Peer set and peer medians.", dict(_CO), ["slug_or_ticker"]),
    _fn("get_history", "Recent changes, by whom, and the previous value.",
        {**_CO, "limit": {"type": "integer"}}),

    # --------------------------------------------------------------- doing it
    _fn("where_is", "Where in the platform something lives. Ask this before "
        "answering any 'where do I find', 'how do I' or 'which page' question, "
        "then open it with `navigate`.",
        {"thing": {"type": "string",
                   "description": "what they are looking for, in their words: "
                                  "'the DCF', 'who changed this', 'peer multiples'"}},
        ["thing"]),
    _fn("navigate", "Open a page for them. Always prefer this over describing "
        "where to click.",
        {"target": {"type": "string",
                    "enum": ["company", "coverage", "admin", "history", "grid"]},
         "slug": {"type": "string"},
         "tab": {"type": "string",
                 "description": "overview|report|studio|financials|statements|"
                                "valuation|peers|history|kpis|profile|sources"}},
        ["target"]),
    _fn("apply_change",
        "Make the change now. Use ONLY when the analyst gave you the value in "
        "this conversation, or a tool returned it. It writes immediately, is "
        "recorded against them, and can be put back. Never use it for a figure "
        "you worked out or remember: use `propose_change` for those.",
        {"action": {"type": "string",
                    "enum": ["add_company", "edit_field", "set_assumption", "recompute"]},
         "slug": {"type": "string", "description": "the company, for anything but add_company"},
         "field": {"type": "string", "description": "e.g. market.share_price"},
         "new_value": {"type": "string"},
         "assumption": {"type": "string"},
         "name": {"type": "string"}, "ticker": {"type": "string"},
         "exchange": {"type": "string"}, "country": {"type": "string"},
         "sector": {"type": "string"},
         "why": {"type": "string",
                 "description": "where the value came from, for the change log"}},
        ["action", "why"]),
    _fn("propose_change",
        "Propose a change for the analyst to approve. Use when the value is "
        "yours rather than theirs: you derived it, inferred it or recall it. "
        "This does NOT apply it.",
        {"action": {"type": "string",
                    "enum": ["add_company", "edit_field", "set_assumption", "recompute"]},
         "slug": {"type": "string", "description": "the company, for anything but add_company"},
         "field": {"type": "string", "description": "e.g. market.share_price"},
         "new_value": {"type": "string"},
         "assumption": {"type": "string"},
         "name": {"type": "string"}, "ticker": {"type": "string"},
         "exchange": {"type": "string"}, "country": {"type": "string"},
         "sector": {"type": "string"},
         "why": {"type": "string", "description": "one line for the analyst"}},
        ["action", "why"]),
]


def available() -> tuple[bool, str]:
    """Whether the assistant can run, and why not if it cannot."""
    if _keyed(MODEL_CHAIN):
        return True, ""
    return False, ("No AI key is set on the server. GROQ_API_KEY is free from "
                   "console.groq.com; GEMINI_API_KEY is free from "
                   "aistudio.google.com and has a separate allowance, so setting "
                   "both roughly doubles what a day can answer.")


class ToolCallFailed(RuntimeError):
    """The model produced a function call Groq would not accept."""


class RateLimited(RuntimeError):
    """Groq said slow down, and waiting did not clear it."""


_THINK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.S | re.I)


def _clean(text: str) -> str:
    """Remove any reasoning the model left in the answer.

    reasoning_format="hidden" handles this at Groq for the models that support
    it. This catches the rest: an unclosed <think> that ran to the end of the
    response is the case that actually reached the screen.
    """
    t = _THINK.sub("", text or "")
    # an opening tag with no closing one: everything after it was thinking
    m = re.search(r"<(?:think|thinking|reasoning)>", t, re.I)
    if m:
        t = t[:m.start()]
    t = re.sub(r"</?(?:think|thinking|reasoning)>", "", t, flags=re.I)
    return t.strip()


def _is_retired(body: str) -> bool:
    """Whether the provider is saying this model no longer exists.

    Matched on the body rather than the status, because the same 400 carries
    both a retired model and a malformed tool call, and only one of them is
    worth retrying.
    """
    t = (body or "").lower()
    return any(k in t for k in ("model_not_found", "does not exist",
                                "decommissioned", "has been deprecated",
                                "no longer supported"))


def _call_groq(messages: list[dict], *, attempts: int = 4, tools_on: bool = True,
               deadline: float | None = None) -> dict:
    """One call, with backoff on a rate limit.

    The free tier limits requests per minute and a single question can take
    several calls, so 429 is a normal condition rather than an error. Groq
    returns Retry-After, which is worth far more than a guess: it says exactly
    how long the window has left.
    """
    chain = _usable(list(dict.fromkeys(MODEL_CHAIN)))
    url, key, bare = _endpoint(chain[0])
    payload = {"model": bare, "messages": messages, "temperature": 0,
               # These models think out loud, and the whole <think> block was
               # arriving in `content` and being rendered to the analyst. Groq
               # strips it at the source with reasoning_format; _clean() below
               # is the belt to that braces, for any model that ignores it.
               "reasoning_format": "hidden",
               # 1200 truncated answers mid-sentence once they carried figures,
               # a source line and what to look at next.
               "max_tokens": 2400}
    entry = chain[0]        # the chain name, which differs from the model sent
    if tools_on:
        payload.update({"tools": TOOLS, "tool_choice": "auto"})
    delay = 1.0
    tried: set[str] = set()
    for attempt in range(attempts + len(chain)):
        if deadline and time.monotonic() > deadline:
            raise RateLimited("Ran out of time waiting for Groq.")
        try:
            per_call = TIMEOUT if not deadline else max(
                2.0, min(TIMEOUT, deadline - time.monotonic()))
            url, key, payload["model"] = _endpoint(entry)
            r = httpx.post(url, timeout=per_call,
                           headers={"Authorization": f"Bearer {key}",
                                    "Content-Type": "application/json"},
                           json=payload)
        except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError,
                httpx.WriteError, httpx.PoolTimeout) as e:
            # A dropped connection is weather, not a verdict. Groq closes one
            # often enough that a single attempt is not a fair test.
            if attempt == attempts - 1 or (deadline and
                                            time.monotonic() + delay > deadline):
                raise RateLimited(f"Groq closed the connection ({type(e).__name__}). "
                                  f"Ask again.")
            time.sleep(delay); delay *= 2
            continue
        # A provider that has RETIRED a model answers 400/404 model_not_found,
        # and that never gets better by retrying. This used to reach
        # raise_for_status and the raw Groq JSON was shown to the analyst:
        # "Groq does not recognise the model 'llama-3.3-70b-versatile'". The
        # chain already held a working model, so the outage was entirely the
        # handling. Treated like a spent allowance now, except permanent.
        if r.status_code in (400, 404) and _is_retired(r.text):
            _EXHAUSTED[entry] = time.monotonic() + 86400
            nxt = next((m for m in chain if m not in tried and m != entry), None)
            log.warning("model %s is no longer served by the provider; "
                        "falling through to %s", entry, nxt or "nothing")
            if nxt:
                tried.add(entry)
                entry = nxt
                continue
            raise RateLimited(
                "None of the configured models is still served. The provider "
                "retires models periodically. An administrator can set AI_MODEL "
                "to a current one, listed at console.groq.com/docs/models.")

        if r.status_code == 429:
            # A DAILY limit will not clear by waiting, so move to a model that
            # still has allowance instead of sleeping until the window resets.
            daily = "per day" in (r.text or "") or "TPD" in (r.text or "")
            if daily:
                # Remember it, so the next question does not pay to find out.
                try:
                    secs = float(r.headers.get("retry-after") or 900)
                except (TypeError, ValueError):
                    secs = 900.0
                _EXHAUSTED[entry] = time.monotonic() + min(secs, 3600)
            nxt = next((m for m in chain if m not in tried and m != entry), None)
            # ANY 429 is a reason to move, not only a daily one. Sleeping and
            # resending the same payload spends the same tokens twice against
            # the same limit, which is how one question turned into seven calls
            # and 16,700 tokens. Another entry has its own allowance; a wait
            # only makes sense when there is nothing else to try.
            if nxt:
                tried.add(entry)
                entry = nxt
                continue
            if attempt == attempts - 1 or daily:
                raise RateLimited(
                    "Every model on this key has used its allowance. Groq's free "
                    "tier allows 12,000 tokens a minute and a question costs about "
                    "16,700, so one question does not fit. Setting GEMINI_API_KEY "
                    "gives 250,000 a minute on a separate free account.")
            wait = delay
            hdr = r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset-requests")
            try:                                  # "2", "2s" and "1.5s" all appear
                wait = max(wait, float(str(hdr).rstrip("smh")))
            except (TypeError, ValueError):
                pass
            # Never sleep past the deadline. Waiting 20 seconds inside a
            # 21-second budget guarantees the 504 this was meant to prevent.
            if deadline:
                left = deadline - time.monotonic()
                if left <= 1.0:
                    raise RateLimited("Groq is rate-limiting this key and there is no "
                                      "time left to wait. Try again in a moment.")
                wait = min(wait, left - 0.5)
            time.sleep(max(0.0, min(wait, 20.0)))
            delay *= 2
            continue
        # Llama sometimes emits a malformed function call and Groq rejects the
        # request with 400 tool_use_failed. The retry drops the tools so the
        # model can answer in words — but a model with no tools and no leash
        # answers from memory: this fallback first listed Attijariwafa Bank and
        # Ecobank Transnational as covered, and neither is in the universe.
        # Exactly the failure the whole design exists to prevent. So the retry
        # also nails it to what the conversation already contains.
        if r.status_code == 400 and "tool_use_failed" in (r.text or ""):
            # First, let it try again. At temperature 0 the same malformed call
            # comes back, so the retry samples differently; a fumbled call is
            # usually a one-off rather than a question it cannot handle.
            if payload.get("tools") and payload.get("temperature") == 0:
                payload = {**payload, "temperature": 0.4}
                continue
            if payload.get("tools"):
                payload = {k: v for k, v in payload.items()
                           if k not in ("tools", "tool_choice")}
                # The instruction depends on whether anything was actually
                # retrieved. Softening it unconditionally let the model answer
                # with NO tool results at all: it named Guaranty Trust, Stanbic
                # IBTC and Zenith as screening above 20% on equity, said "the
                # tool returned" them, and Stanbic is not even in coverage.
                got_results = any(m.get("role") == "tool" for m in payload["messages"])
                leash = ("The tool results already in this conversation are yours. "
                         "Read them and answer from them. Introduce no company, "
                         "figure or fact that is not in them. If they do not answer "
                         "the question, say exactly: 'I could not retrieve that. "
                         "Please ask again.'"
                         if got_results else
                         "No tool returned anything for this question, so you have "
                         "nothing to answer from. Reply with exactly this and nothing "
                         "else: 'I could not retrieve that. Please ask again.'")
                payload["messages"] = list(payload["messages"]) + [{
                    "role": "system",
                    "content": leash + " Never say a tool returned something. Never "
                                       "name a company from memory."}]
                continue
            raise ToolCallFailed(
                "The model could not form a valid tool call for that question. "
                "Try asking it more plainly, or one thing at a time.")
        if r.status_code >= 500 and attempt < attempts - 1:
            if deadline and time.monotonic() + delay > deadline:
                raise RateLimited("Groq is erroring and there is no time left to retry.")
            time.sleep(delay); delay *= 2
            continue
        r.raise_for_status()
        return r.json()["choices"][0]["message"]
    raise RateLimited("Groq did not answer after several attempts.")


def answer(question: str, history: list[dict], tools: dict[str, Any],
           apply=None) -> dict:
    """Run one turn. `tools` maps a tool name to a callable.

    `apply` commits a change and returns its receipt. It is passed only for a
    user allowed to make one, so the permission lives with the caller and the
    model cannot talk its way past it: without the callable the tool refuses.

    Returns {reply, navigate, proposal, applied, steps} — the caller renders the
    reply, follows any navigation, shows any proposal as an approval card, and
    shows anything applied as a receipt with a put-back.
    """
    ok, why = available()
    if not ok:
        return {"reply": f"The assistant is not configured. {why}", "steps": []}

    messages = [{"role": "system", "content": SYSTEM}]
    for h in history[-8:]:                      # keep the window small and cheap
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"][:4000]})
    messages.append({"role": "user", "content": question[:4000]})

    # A shared bag rather than three locals: a proposal made on step two must
    # survive a failure on step three. It did not, so the assistant would say it
    # had proposed a change and the analyst would be shown nothing.
    state = {"nav": None, "proposal": None, "applied": [], "refused": False,
             "steps": [], "apply": apply, "deadline": time.monotonic() + BUDGET}

    def finish(out: dict) -> dict:
        """Attach the pages worth opening, on every path out of here.

        Including the failures. A question that ran out of allowance still knows
        which company was asked about, and a link is more use than an apology.
        """
        out.setdefault("steps", state["steps"])
        out["links"] = _links(state["steps"], tools.get("_resolve_slug"))
        return out

    try:
        return finish(_run(messages, tools, state))
    except (RateLimited, ToolCallFailed) as e:
        return finish({"reply": str(e), "navigate": state["nav"],
                       "proposal": state["proposal"], "applied": state["applied"],
                       "steps": state["steps"]})
    except httpx.HTTPStatusError as e:                          # noqa: F841
        # Say WHICH failure. "HTTPStatusError" told us nothing when this went
        # wrong on the live site, and a status code would have named it at once.
        code = e.response.status_code
        detail = (e.response.text or "")[:200]
        human = {401: "Groq rejected the API key. Check GROQ_API_KEY on the server.",
                 403: "Groq refused this key. Check the account is active.",
                 404: f"Groq does not recognise the model {MODEL!r}.",
                 413: "The conversation is too long for the model."}.get(
                     code, f"Groq returned {code}.")
        return finish({"reply": f"{human} ({detail})" if detail else human,
                       "navigate": state["nav"], "proposal": state["proposal"],
                       "applied": state["applied"], "refused": state["refused"],
                       "steps": state["steps"]})
    except httpx.HTTPError as e:
        return finish({"reply": f"The assistant could not reach Groq: {type(e).__name__}. "
                                f"Anything it worked out before that is below.",
                       "navigate": state["nav"], "proposal": state["proposal"],
                       "applied": state["applied"], "refused": state["refused"],
                       "steps": state["steps"]})


def _run(messages, tools, state):
    steps = state["steps"]
    for _ in range(MAX_STEPS):
        if time.monotonic() > state["deadline"]:
            return _wrap_up(messages, state, "out of time")
        msg = _call_groq(messages, deadline=state["deadline"])
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"reply": _clean(msg.get("content")),
                    "navigate": state["nav"], "proposal": state["proposal"],
                    "applied": state["applied"], "refused": state["refused"], "refused": state["refused"],
                "applied": state["applied"], "refused": state["refused"],
                    "steps": steps}

        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            steps.append({"tool": name, "args": args})

            if name == "navigate":
                state["nav"] = args
                result: Any = {"ok": True, "opened": args}
            elif name == "where_is":
                result = locate(args.get("thing") or "")
            elif name == "apply_change":
                result = _apply(state, args)
            elif name == "propose_change":
                state["proposal"] = args
                result = {"ok": True,
                          "staged": "The analyst has been shown an approval card. "
                                    "Nothing has changed yet."}
            else:
                fn = tools.get(name)
                result = fn(**args) if fn else {"error": f"no such tool: {name}"}

            # Re-sent on every later call in this question, so a fat result is
            # paid for several times. Groq's free tier allows 12,000 tokens a
            # MINUTE, and one question was using all of it.
            body = json.dumps(result, default=str)
            if len(body) > 1400:
                body = body[:1400] + '…","truncated":"ask for one thing at a time"}'
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "name": name, "content": body})

    return _wrap_up(messages, state, "out of steps")



def _apply(state, args) -> dict:
    """Commit a change the analyst asked for, and keep the receipt.

    Fails closed. A viewer's turn carries no callable, so the tool refuses here
    rather than anywhere the model could argue with: the permission is a missing
    function, not a rule it has been asked to respect.
    """
    fn = state.get("apply")
    if not fn:
        # Noted on the turn, not only returned to the model. Whether the person
        # is told cannot depend on the model choosing to mention it.
        state["refused"] = True
        return {"ok": False,
                "refused": "Changing data needs analyst access, and this user "
                           "does not have it. Tell them, and do not retry."}
    try:
        receipt = fn(args)
    except PermissionError as e:
        return {"ok": False, "refused": str(e)}
    except Exception as e:                       # a bad field, a missing company
        return {"ok": False, "failed": str(e)[:300],
                "note": "Nothing was changed. Say so."}
    state["applied"].append({**receipt, "why": args.get("why") or ""})
    return {"ok": True, "applied": receipt.get("detail") or "done",
            "note": "This is now live. Say what changed and what it replaced."}


def _wrap_up(messages, state, why):
    """Answer from what the tools already returned, without calling more."""
    got = [m for m in messages if m.get("role") == "tool"]
    if not got:
        return {"reply": "I could not retrieve that in time. Please ask again, or "
                         "ask for one thing at a time.",
                "navigate": state["nav"], "proposal": state["proposal"],
                "applied": state["applied"], "refused": state["refused"],
                "steps": state["steps"]}
    messages = list(messages) + [{
        "role": "system",
        "content": ("Answer now, from the tool results in this conversation only. "
                    "Do not call any more tools. Introduce no company, figure or "
                    "fact that is not in those results.")}]
    try:
        final = _call_groq(messages, tools_on=False, attempts=1,
                           deadline=time.monotonic() + 8.0)
        reply = _clean(final.get("content"))
    except Exception:
        reply = ("I ran out of time putting that together. What came back is "
                 "listed below; ask again for the rest.")
    return {"reply": reply, "navigate": state["nav"],
            "proposal": state["proposal"], "applied": state["applied"],
            "refused": state["refused"], "steps": state["steps"]}


def health() -> dict:
    """Ask the provider which of the configured models it still serves.

    Groq retired the whole Llama 3.3 line without this platform noticing, and
    the first sign was an analyst being shown raw provider JSON instead of an
    answer. Checking is one HTTP call, so it should never again be discovered
    by a failed question.
    """
    chain = _keyed(MODEL_CHAIN)
    if not chain:
        return {"ok": False, "detail": "No AI key is set on the server.",
                "models": []}
    groq_key = os.environ.get("GROQ_API_KEY", "").strip()
    served: set[str] = set()
    listed = False
    if groq_key:
        try:
            r = httpx.get("https://api.groq.com/openai/v1/models", timeout=8,
                          headers={"Authorization": f"Bearer {groq_key}"})
            r.raise_for_status()
            served = {m["id"] for m in r.json().get("data", [])}
            listed = True
        except Exception as e:
            log.warning("could not list the provider's models: %s", e)

    rows = []
    for entry in chain:
        gemini = entry.startswith(GEMINI_PREFIX)
        bare = entry[len(GEMINI_PREFIX):] if gemini else entry
        if gemini:
            state = "configured"          # Google has no comparable free listing
        elif not listed:
            state = "unknown"
        else:
            state = "served" if bare in served else "retired"
        rows.append({"model": entry, "state": state,
                     "spent_until": round(max(0.0, _EXHAUSTED.get(entry, 0)
                                              - time.monotonic()))})
    usable = [r for r in rows if r["state"] in ("served", "configured", "unknown")]
    if not usable:
        return {"ok": False, "models": rows,
                "detail": "Every configured model has been retired by the provider. "
                          "Set AI_MODEL to one that is still served; the list is at "
                          "console.groq.com/docs/models."}
    retired = [r["model"] for r in rows if r["state"] == "retired"]
    if retired:
        return {"ok": True, "models": rows,
                "detail": f"Working. {len(usable)} model(s) still served. "
                          f"Retired and skipped: {', '.join(retired)}. Removing "
                          f"them from the chain saves a wasted call per question."}
    return {"ok": True, "models": rows,
            "detail": f"Working. {len(usable)} model(s) still served by the provider."}
