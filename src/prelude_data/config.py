from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
FEED_DIR = REPO_ROOT / "feed" / "v1"
FEED_ROOT = REPO_ROOT / "feed"
STAGING_DIR = REPO_ROOT / "state" / "staging"
LOG_DIR = REPO_ROOT / "logs"

# SEC asks automated agents to identify themselves. Keep this honest.
USER_AGENT = "prelude-data/0.1 (private-markets research feed; joe.rotherham45@gmail.com)"

# Politeness: never more than one request per second to any host.
MIN_SECONDS_BETWEEN_REQUESTS = 1.0
REQUEST_TIMEOUT = 30
RETRIES = 3

# EDGAR daily form indexes: how many calendar days back to sweep for S-1s.
EDGAR_LOOKBACK_DAYS = 14

# ARK publishes fund holdings CSVs publicly (see README for terms posture).
ARK_HOLDINGS_URLS = {
    "ARKVX": "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_VENTURE_FUND_ARKVX_HOLDINGS.csv",
}

# Yahoo Finance unofficial chart endpoint (read-only, low volume; see README).
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Freshness thresholds used by the validation gate.
MAX_PRICE_AGE_DAYS = 7          # exchange-traded quotes (weekend/holiday slack)
MAX_NAV_AGE_DAYS = 400          # quarterly reporters + curation slack
MAX_CURATION_AGE_DAYS = 120     # companies_seed must be re-reviewed quarterly
MIN_COMPANIES = 40
MIN_WRAPPERS = 4
