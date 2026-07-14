"""SEC EDGAR: new S-1 / S-1/A filings from the daily form indexes.

Source: https://www.sec.gov/Archives/edgar/daily-index/ — plain-text form.idx
files, one per business day. This is SEC's documented dissemination feed;
we identify ourselves via User-Agent and stay at <=1 request/second.
"""

from __future__ import annotations

import datetime as dt
import logging
import re

from . import config, http_client

log = logging.getLogger(__name__)

DAILY_INDEX_URL = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{qtr}/form.{ymd}.idx"
FORM_TYPES = ("S-1", "S-1/A")

# Registrations of funds/trusts/ETFs also use S-1; tag them so the app can
# separate operating-company IPOs from product registrations. Neutral fact:
# the name matches a fund-like keyword. No judgement encoded.
FUND_KEYWORD_RE = re.compile(r"\b(ETF|Fund|Trust|Acquisition|SPAC)\b", re.IGNORECASE)


def quarter(month: int) -> int:
    return (month - 1) // 3 + 1


def daily_index_url(day: dt.date) -> str:
    return DAILY_INDEX_URL.format(year=day.year, qtr=quarter(day.month), ymd=day.strftime("%Y%m%d"))


def parse_form_idx(text: str, wanted: tuple[str, ...] = FORM_TYPES) -> list[dict]:
    """Parse a form.idx file into filing dicts for the wanted form types.

    The file is fixed-width-ish but reliably splittable: form type ends at
    the first run of 2+ spaces; the tail columns are CIK, date, file name.
    """
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
        form_type, company, cik, date_filed, file_name = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
            parts[3].strip(),
            parts[4].strip(),
        )
        if form_type not in wanted:
            continue
        accession = file_name.rsplit("/", 1)[-1].removesuffix(".txt")
        filings.append(
            {
                "form_type": form_type,
                "issuer": company,
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


def fetch_recent_s1_filings(
    today: dt.date | None = None, lookback_days: int = config.EDGAR_LOOKBACK_DAYS
) -> tuple[list[dict], list[str]]:
    """Sweep the last N days of daily indexes. Weekends/holidays 404 — skipped.

    Returns (filings deduped by accession, list of ISO dates actually covered).
    """
    today = today or dt.date.today()
    seen: dict[str, dict] = {}
    covered: list[str] = []
    for offset in range(lookback_days, -1, -1):
        day = today - dt.timedelta(days=offset)
        if day.weekday() >= 5:  # EDGAR publishes business days only
            continue
        url = daily_index_url(day)
        try:
            resp = http_client.get(url, ok_codes=(200, 403, 404))
        except RuntimeError as exc:
            log.warning("daily index unavailable for %s: %s", day, exc)
            continue
        if resp.status_code != 200:
            log.info("no daily index for %s (HTTP %s — likely holiday)", day, resp.status_code)
            continue
        covered.append(day.isoformat())
        for filing in parse_form_idx(resp.text):
            seen.setdefault(filing["accession_number"], filing)
    filings = sorted(seen.values(), key=lambda f: (f["filing_date"], f["issuer"]), reverse=True)
    return filings, covered
