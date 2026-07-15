"""SEC EDGAR: registration and pricing filings from the daily form indexes.

Source: https://www.sec.gov/Archives/edgar/daily-index/ — plain-text form.idx
files, one per business day. This is SEC's documented dissemination feed;
we identify ourselves via User-Agent and stay at <=1 request/second.

The window is a 180-day rolling sweep. Past days' indexes are immutable, so
they are cached on disk (state/edgar_cache/) — after the first backfill a
nightly run fetches only the days it has never seen.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path

from . import config, http_client

log = logging.getLogger(__name__)

DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{qtr}/form.{ymd}.idx"

# Registrations open the pipeline; F-1s are the foreign-issuer equivalent.
REGISTRATION_FORMS = ("S-1", "S-1/A", "F-1", "F-1/A")
# Pricing prospectuses close it. 424B4/424B1 are IPO pricings; 424B3 is
# included per spec but is also used for supplements, so consumers should
# lean on fund_keyword_match and universe matching to separate signal.
PRICING_FORMS = ("424B4", "424B1", "424B3")

CACHE_DIR = config.REPO_ROOT / "state" / "edgar_cache"

# Registrations of funds/trusts/ETFs also use these forms; tag them so the
# app can separate operating-company IPOs from product registrations.
FUND_KEYWORD_RE = re.compile(r"\b(ETF|Fund|Trust|Acquisition|SPAC)\b", re.IGNORECASE)

# Corporate suffix tokens stripped into the secondary display line.
DISPLAY_SUFFIXES = {
    "inc",
    "inc.",
    "incorporated",
    "corp",
    "corp.",
    "corporation",
    "ltd",
    "ltd.",
    "limited",
    "llc",
    "l.l.c.",
    "lp",
    "l.p.",
    "plc",
    "co",
    "co.",
    "company",
    "sa",
    "s.a.",
    "nv",
    "n.v.",
    "ag",
    "se",
}


def quarter(month: int) -> int:
    return (month - 1) // 3 + 1


def daily_index_url(day: dt.date) -> str:
    return DAILY_INDEX_URL.format(year=day.year, qtr=quarter(day.month), ymd=day.strftime("%Y%m%d"))


# ---------------------------------------------------------------------------
# Display normalization
# ---------------------------------------------------------------------------

def normalize_display(issuer: str) -> tuple[str, str | None]:
    """('SPACE EXPLORATION TECHNOLOGIES CORP', ...) -> ('Space Exploration Technologies', 'Corp').

    Trailing corporate suffix tokens move to a secondary line; the remainder
    is title-cased (short all-caps tokens like 'AI' survive as-is unless the
    whole name was shouting).
    """
    cleaned = re.sub(r"\s*/[A-Z]{2}/?\s*$", "", issuer.strip())  # '/DE' state tags
    tokens = cleaned.replace(",", " ").split()
    suffix_parts: list[str] = []
    while tokens and tokens[-1].lower() in DISPLAY_SUFFIXES:
        suffix_parts.insert(0, tokens.pop().rstrip("."))
    if not tokens:  # the whole name was suffixes — keep original
        return cleaned, None

    all_shouting = cleaned.isupper()
    words = []
    for tok in tokens:
        if not all_shouting and tok.isupper() and len(tok) <= 4:
            words.append(tok)  # deliberate acronym in mixed-case name
        elif tok.isupper() or tok.islower():
            words.append(tok.capitalize())
        else:
            words.append(tok)  # already mixed case — trust the filer
    suffix = " ".join(p.capitalize() for p in suffix_parts) if suffix_parts else None
    return " ".join(words), suffix


# ---------------------------------------------------------------------------
# Index fetch + cache
# ---------------------------------------------------------------------------

def fetch_daily_index_text(day: dt.date, today: dt.date) -> str | None:
    """Index text for a business day, from cache when immutable."""
    cache_path = CACHE_DIR / f"form.{day.strftime('%Y%m%d')}.idx"
    if day < today and cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="ignore")
    try:
        resp = http_client.get(daily_index_url(day), ok_codes=(200, 403, 404))
    except RuntimeError as exc:
        log.warning("daily index unavailable for %s: %s", day, exc)
        return None
    if resp.status_code != 200:
        return None
    if day < today:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(resp.text, encoding="utf-8")
    return resp.text


def parse_form_idx(text: str, wanted: tuple[str, ...]) -> list[dict]:
    """Parse a form.idx file into filing dicts for the wanted form types."""
    filings: list[dict] = []
    in_body = False
    for line in text.splitlines():
        if line.startswith("---"):
            in_body = True
            continue
        if not in_body or not line.strip():
            continue
        parts = re.split(r"\s{2,}", line.strip())
        if len(parts) < 5:
            continue
        form_type, company, cik, date_filed, file_name = (p.strip() for p in parts[:5])
        if form_type not in wanted:
            continue
        accession = file_name.rsplit("/", 1)[-1].removesuffix(".txt")
        display, suffix = normalize_display(company)
        filings.append(
            {
                "form_type": form_type,
                "issuer": company,
                "display_name": display,
                "entity_suffix": suffix,
                "cik": cik,
                "filing_date": f"{date_filed[0:4]}-{date_filed[4:6]}-{date_filed[6:8]}",
                "accession_number": accession,
                "source_url": filing_index_url(cik, accession),
                "fund_keyword_match": bool(FUND_KEYWORD_RE.search(company)),
            }
        )
    return filings


def filing_index_url(cik: str, accession: str) -> str:
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"
    )


def fetch_window(
    today: dt.date | None = None, lookback_days: int = config.EDGAR_LOOKBACK_DAYS
) -> tuple[list[dict], list[dict], list[str]]:
    """Sweep the rolling window. Returns (registrations, pricings, days covered)."""
    today = today or dt.date.today()
    registrations: dict[str, dict] = {}
    pricings: dict[str, dict] = {}
    covered: list[str] = []
    wanted = REGISTRATION_FORMS + PRICING_FORMS
    for offset in range(lookback_days, -1, -1):
        day = today - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        text = fetch_daily_index_text(day, today)
        if text is None:
            continue
        covered.append(day.isoformat())
        for filing in parse_form_idx(text, wanted):
            bucket = registrations if filing["form_type"] in REGISTRATION_FORMS else pricings
            bucket.setdefault(filing["accession_number"], filing)
    reg = sorted(registrations.values(), key=lambda f: (f["filing_date"], f["issuer"]), reverse=True)
    pri = sorted(pricings.values(), key=lambda f: (f["filing_date"], f["issuer"]), reverse=True)
    return reg, pri, covered


# ---------------------------------------------------------------------------
# 424 price extraction
# ---------------------------------------------------------------------------

PRICE_PATTERNS = [
    re.compile(r"initial public offering price (?:is|of|will be)\s*\$\s?([\d,]+(?:\.\d+)?) per (?:share|unit)", re.I),
    re.compile(r"public offering price of\s*\$\s?([\d,]+(?:\.\d+)?) per (?:share|unit)", re.I),
    re.compile(r"price to the public of\s*\$\s?([\d,]+(?:\.\d+)?) per (?:share|unit)", re.I),
    re.compile(r"\$\s?([\d,]+(?:\.\d+)?) per unit", re.I),
]


def parse_424_price(document_text: str) -> float | None:
    """Offer price per share from a 424 prospectus body, or None."""
    text = re.sub(r"<[^>]+>", " ", document_text)
    text = re.sub(r"&nbsp;?|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    for pattern in PRICE_PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                price = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if 0 < price < 100_000:
                return price
    return None


def cached_price(accession: str) -> tuple[bool, float | None]:
    """(cache_hit, price) without touching the network."""
    cache_path = CACHE_DIR / "prices" / f"{accession}.txt"
    if not cache_path.exists():
        return False, None
    cached = cache_path.read_text(encoding="utf-8").strip()
    return True, (None if cached == "none" else float(cached))


def fetch_pricing_price(cik: str, accession: str) -> float | None:
    """Fetch a pricing filing's primary document and extract the offer price.

    Results are cached (filings are immutable).
    """
    cache = CACHE_DIR / "prices"
    cache.mkdir(parents=True, exist_ok=True)
    cache_path = cache / f"{accession}.txt"
    if cache_path.exists():
        cached = cache_path.read_text(encoding="utf-8").strip()
        return None if cached == "none" else float(cached)

    index_url = filing_index_url(cik, accession)
    try:
        index_html = http_client.get_text(index_url)
        docs = re.findall(r'href="([^"]+\.htm)"', index_html)
        primary = next(
            (
                d
                for d in docs
                if "index" not in d and d.startswith("/Archives/edgar/data/")
            ),
            None,
        )
        if primary is None:
            cache_path.write_text("none", encoding="utf-8")
            return None
        doc_text = http_client.get_text(f"https://www.sec.gov{primary}")
    except RuntimeError as exc:
        log.warning("pricing doc unavailable for %s: %s", accession, exc)
        return None  # transient — do not cache

    price = parse_424_price(doc_text)
    cache_path.write_text("none" if price is None else str(price), encoding="utf-8")
    return price
