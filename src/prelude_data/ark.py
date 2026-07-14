"""ARK Invest fund holdings — ARK publishes these CSVs publicly and daily.

Used for ARKVX (ARK Venture Fund, an interval fund holding late-stage
private companies). Each row: date, fund, company, ticker, cusip, weight.
"""

from __future__ import annotations

import csv
import io
import logging

from . import compute, config, http_client

log = logging.getLogger(__name__)


def parse_holdings_csv(text: str) -> list[dict]:
    """Parse an ARK holdings CSV into holding dicts (order preserved)."""
    holdings: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        company = (row.get("company") or "").strip()
        if not company:
            continue
        weight = compute.parse_ark_weight(row.get("weight (%)") or "")
        date_raw = (row.get("date") or "").strip()  # MM/DD/YYYY
        as_of = None
        if len(date_raw) == 10 and date_raw[2] == "/" and date_raw[5] == "/":
            mm, dd, yyyy = date_raw.split("/")
            as_of = f"{yyyy}-{mm}-{dd}"
        holdings.append(
            {
                "name": company,
                "ticker": (row.get("ticker") or "").strip() or None,
                "weight_pct": float(weight) if weight is not None else None,
                "as_of": as_of,
            }
        )
    return holdings


def fetch_holdings(fund: str) -> tuple[list[dict], str]:
    """Fetch holdings for a fund symbol. Returns (holdings, source_url)."""
    url = config.ARK_HOLDINGS_URLS[fund]
    text = http_client.get_text(url)
    holdings = parse_holdings_csv(text)
    log.info("ARK %s: %d holdings", fund, len(holdings))
    return holdings, url
