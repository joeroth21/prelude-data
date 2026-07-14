"""Market quotes via Yahoo Finance's unofficial chart endpoint.

Read-only, no key, single request per symbol per nightly run. This is not
an official API; the README documents the posture. Every quote carries its
exchange timestamp as as_of — we publish what Yahoo reported and when.
"""

from __future__ import annotations

import datetime as dt
import logging

from . import config, http_client

log = logging.getLogger(__name__)


def parse_chart_meta(payload: dict) -> dict | None:
    """Extract a quote from a chart response.

    Yahoo's meta block (regularMarketPrice/Time) is occasionally stale for
    thinly covered symbols while the daily bar series is current, so take
    whichever of the two carries the later timestamp.
    """
    try:
        result = payload["chart"]["result"][0]
        meta = result["meta"]
    except (KeyError, IndexError, TypeError):
        return None

    candidates: list[tuple[int, float]] = []
    price = meta.get("regularMarketPrice")
    ts = meta.get("regularMarketTime")
    if price is not None and ts is not None:
        candidates.append((ts, price))

    series_ts = result.get("timestamp") or []
    quote_block = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote_block.get("close") or []
    for t, close in zip(series_ts, closes):
        if t is not None and close is not None:
            candidates.append((t, close))

    if not candidates:
        return None
    best_ts, best_price = max(candidates, key=lambda pair: pair[0])
    return {
        "price": round(float(best_price), 4),
        "currency": meta.get("currency"),
        "as_of": dt.datetime.fromtimestamp(best_ts, tz=dt.timezone.utc).isoformat(),
        "exchange": meta.get("fullExchangeName"),
        "instrument_type": meta.get("instrumentType"),
    }


def fetch_quote(symbol: str) -> dict | None:
    """Latest regular-market quote, or None if unavailable (never raises)."""
    url = config.YAHOO_CHART_URL.format(symbol=symbol) + "?range=5d&interval=1d"
    try:
        payload = http_client.get_json(url)
    except RuntimeError as exc:
        log.warning("quote unavailable for %s: %s", symbol, exc)
        return None
    quote = parse_chart_meta(payload)
    if quote is None:
        log.warning("quote payload unusable for %s", symbol)
        return None
    quote["source_url"] = f"https://finance.yahoo.com/quote/{symbol}"
    return quote
