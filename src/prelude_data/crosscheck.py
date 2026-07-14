"""Status cross-check — the guard against the SpaceX class of error.

Curated ipo_status is verified against two independent signals before any
feed publishes:

  1. Symbol resolution: a company marked private/rumored/s1_filed whose
     name (or a curated alias) resolves to an actively trading equity is a
     validation failure. Conversely, a company marked listed must have a
     listed_ticker that actually quotes.
  2. Our own pipeline: an S-1/S-1/A in pipeline.json whose issuer matches a
     company name/alias while that company is marked private or rumored is
     a validation failure (the filing escalates the status; a human updates
     the seed with a source).

Mismatches FAIL VALIDATION LOUDLY — the pipeline refuses to publish and the
last-good feed stays up until the seed is corrected. Nothing here rewrites
curated data automatically.

Matching is deliberately conservative (normalized equality or full-phrase
containment against name + aliases) because a false link is worse than a
missed one; `crosscheck_skip: true` on a seed entry opts out a known
collision, visibly.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import urllib.parse

from . import config, http_client, market

log = logging.getLogger(__name__)

SUFFIXES = {
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "plc",
    "corp",
    "corporation",
    "company",
    "co",
    "holdings",
    "group",
    "technologies",
}

# Exchanges that count as "actively listed" for the private-status check.
LISTED_EXCHANGES = {"NMS", "NGM", "NCM", "NYQ", "ASE", "NAS", "NYS"}


def normalize_name(raw: str) -> str:
    words = [
        w
        for w in re.sub(r"[^a-z0-9\s]", " ", raw.lower()).split()
        if w and w not in SUFFIXES
    ]
    return " ".join(words)


def names_match(a: str, b: str) -> bool:
    """Normalized equality, or full-phrase containment for multi-word names."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) < 5:  # too short to phrase-match safely
        return False
    return (
        longer.startswith(f"{shorter} ")
        or longer.endswith(f" {shorter}")
        or f" {shorter} " in longer
    )


def company_names(company: dict) -> list[str]:
    return [company["name"], *company.get("aliases", [])]


# ---------------------------------------------------------------------------
# Signal 1: symbol resolution via Yahoo search + quote freshness
# ---------------------------------------------------------------------------

def search_equities(query: str) -> list[dict]:
    """Equity candidates from Yahoo's search endpoint (name, symbol, exchange)."""
    url = config.YAHOO_SEARCH_URL.format(query=urllib.parse.quote(query))
    try:
        payload = http_client.get_json(url)
    except RuntimeError as exc:
        log.warning("symbol search unavailable for %r: %s", query, exc)
        return []
    out = []
    for q in payload.get("quotes", []):
        if q.get("quoteType") != "EQUITY":
            continue
        out.append(
            {
                "symbol": q.get("symbol"),
                "exchange": q.get("exchange"),
                "longname": q.get("longname") or "",
                "shortname": q.get("shortname") or "",
            }
        )
    return out


def is_actively_trading(symbol: str, now: dt.datetime) -> bool:
    quote = market.fetch_quote(symbol)
    if quote is None:
        return False
    try:
        as_of = dt.datetime.fromisoformat(quote["as_of"])
    except ValueError:
        return False
    return (now - as_of).total_seconds() / 86400 <= config.CROSSCHECK_ACTIVE_DAYS


def resolve_listed_equity(
    company: dict,
    now: dt.datetime,
    search=search_equities,
    trading_check=is_actively_trading,
) -> dict | None:
    """A listed-exchange equity whose name matches this company, if one trades."""
    for query in company_names(company):
        for candidate in search(query):
            if candidate["exchange"] not in LISTED_EXCHANGES:
                continue
            candidate_names = [candidate["longname"], candidate["shortname"]]
            if not any(
                names_match(cn, known)
                for cn in candidate_names
                if cn
                for known in company_names(company)
            ):
                continue
            if trading_check(candidate["symbol"], now):
                return candidate
    return None


# ---------------------------------------------------------------------------
# Signal 2: our own pipeline's S-1s
# ---------------------------------------------------------------------------

def s1_escalations(companies: list[dict], filings: list[dict]) -> list[str]:
    errors = []
    for company in companies:
        if company["ipo_status"] not in ("private", "rumored"):
            continue
        if company.get("crosscheck_skip"):
            continue
        for filing in filings:
            if any(names_match(filing["issuer"], n) for n in company_names(company)):
                errors.append(
                    f"crosscheck[{company['id']}]: marked {company['ipo_status']} but "
                    f"{filing['form_type']} filed {filing['filing_date']} by "
                    f"'{filing['issuer']}' ({filing['source_url']}) — escalate status in seed"
                )
                break
    return errors


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def crosscheck_companies(
    companies: list[dict],
    filings: list[dict],
    now: dt.datetime | None = None,
    search=search_equities,
    trading_check=is_actively_trading,
) -> list[str]:
    """All cross-check failures ([] = statuses are consistent with evidence)."""
    now = now or dt.datetime.now(tz=dt.timezone.utc)
    errors: list[str] = []

    for company in companies:
        if company.get("crosscheck_skip"):
            log.info("crosscheck: skipping %s (crosscheck_skip set in seed)", company["id"])
            continue
        status = company["ipo_status"]
        if status in ("private", "rumored", "s1_filed"):
            match = resolve_listed_equity(company, now, search, trading_check)
            if match:
                errors.append(
                    f"crosscheck[{company['id']}]: marked {status} but "
                    f"'{match['longname'] or match['shortname']}' trades as "
                    f"{match['symbol']} on {match['exchange']} — correct the seed"
                )
        elif status == "listed":
            ticker = company.get("listed_ticker")
            if not ticker:
                errors.append(f"crosscheck[{company['id']}]: listed but no listed_ticker")
            elif not trading_check(ticker, now):
                errors.append(
                    f"crosscheck[{company['id']}]: listed as {ticker} but no active quote "
                    f"within {config.CROSSCHECK_ACTIVE_DAYS} days — verify ticker"
                )

    errors.extend(s1_escalations(companies, filings))
    return errors
