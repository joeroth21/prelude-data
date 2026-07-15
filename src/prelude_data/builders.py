"""Assemble the four data products from live sources + curated YAML.

Every datum carries source_url and as_of. Curated facts come from YAML in
/data (hand-edited, each entry cites its source); live facts come from the
fetchers and carry the timestamp the source reported.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

import yaml

from . import ark, compute, config, crosscheck, edgar, market

log = logging.getLogger(__name__)


def utcnow_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat(timespec="seconds")


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# companies.json
# ---------------------------------------------------------------------------

# ipo_status carries curation nuance (rumored); lifecycle is the hard state.
LIFECYCLE_FROM_STATUS = {
    "private": "private",
    "rumored": "private",
    "s1_filed": "s1_filed",
    "priced": "priced",
    "listed": "listed",
}

STALE_AFTER_DAYS = 365


def mark_age_days(as_of: str, today: dt.date) -> int | None:
    try:
        marked = dt.date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return None
    return (today - marked).days


def graduation_outcome(ipo_price_usd: float, ticker: str) -> dict | None:
    """IPO price vs current price for a graduated (listed) company."""
    quote = market.fetch_quote(ticker)
    if quote is None:
        return None
    change = (Decimal(str(quote["price"])) / Decimal(str(ipo_price_usd)) - 1) * 100
    return {
        "ipo_price_usd": ipo_price_usd,
        "current_price_usd": quote["price"],
        "currency": quote["currency"],
        "price_as_of": quote["as_of"],
        "change_from_ipo_pct": float(change.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)),
        "source_url": quote["source_url"],
    }


def derive_access_routes(company_seed: dict, overlay_links: list[dict]) -> list[dict]:
    """Access routes for one company — facts about where it trades.

    Graduated companies get exactly one route: the public listing. Private
    companies get any hand-verified venue pages from the access overlay
    (often none — the app falls back to the global venue directory).
    """
    if company_seed["ipo_status"] == "listed":
        ticker = company_seed.get("listed_ticker")
        return [
            {
                "kind": "public_listing",
                "ticker": ticker,
                "exchange": company_seed.get("listing_exchange"),
                "url": f"https://finance.yahoo.com/quote/{ticker}" if ticker else None,
            }
        ]
    return [
        {
            "kind": "venue_page",
            "venue_id": link["venue_id"],
            "url": link["url"],
            "as_of": str(link["as_of"]),
        }
        for link in overlay_links
        if link["company_id"] == company_seed["id"]
    ]


def build_companies(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    seed = load_yaml(config.DATA_DIR / "companies_seed.yaml")
    venues = load_yaml(config.DATA_DIR / "access_venues.yaml")["venues"]
    access_links = load_yaml(config.DATA_DIR / "access_overlay.yaml").get("links", [])
    companies = []
    for c in seed["companies"]:
        lifecycle = LIFECYCLE_FROM_STATUS[c["ipo_status"]]
        graduated = lifecycle == "listed"
        age = mark_age_days(c["valuation"]["as_of"], today)
        # Public-market marks track the tape; staleness is a private-mark concept.
        stale = (
            not graduated
            and c["valuation"]["basis"] != "public_market"
            and age is not None
            and age > STALE_AFTER_DAYS
        )
        entry = {
            "id": c["id"],
            "name": c["name"],
            "summary": c["summary"],
            "sector": c["sector"],
            "profile": c["profile"],
            "access_routes": derive_access_routes(c, access_links),
            "profile_source_url": c["profile_source_url"],
            "ipo_status": c["ipo_status"],  # private|rumored|s1_filed|priced|listed
            "lifecycle": lifecycle,
            "graduated": graduated,
            "listing_date": str(c["listing_date"]) if c.get("listing_date") else None,
            "ipo_status_source_url": c.get("ipo_status_source_url"),
            "listed_ticker": c.get("listed_ticker"),
            "aliases": c.get("aliases", []),
            "crosscheck_skip": c.get("crosscheck_skip", False),
            "valuation": {
                "amount_usd_billions": c["valuation"]["amount_usd_billions"],
                "basis": c["valuation"]["basis"],  # priced_round|secondary_sale|tender_offer|media_report|public_market
                "round_label": c["valuation"].get("round_label"),
                "as_of": str(c["valuation"]["as_of"]),
                "mark_age_days": age,
                "stale": stale,
                "source_url": c["valuation"]["source_url"],
            },
            "as_of": str(seed["curated_as_of"]),
        }
        if graduated and c.get("ipo_price_usd") and c.get("listed_ticker"):
            entry["graduation_outcome"] = graduation_outcome(
                float(c["ipo_price_usd"]), c["listed_ticker"]
            )
        else:
            entry["graduation_outcome"] = None
        companies.append(entry)
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "as_of": str(seed["curated_as_of"]),
        "notes": seed.get("notes"),
        "access_venues": venues,
        "companies": companies,
    }


# ---------------------------------------------------------------------------
# pipeline.json — EDGAR registrations + pricings + curated overlay
# ---------------------------------------------------------------------------

def match_universe(entries: list[dict], companies: list[dict]) -> None:
    """Tag each filing with the universe company it matches, if any."""
    for entry in entries:
        entry["universe_company_id"] = None
        for c in companies:
            if any(
                crosscheck.names_match(entry["issuer"], n)
                for n in crosscheck.company_names(c)
            ):
                entry["universe_company_id"] = c["id"]
                break


def build_pipeline(companies: list[dict], today: dt.date | None = None) -> dict:
    registrations, pricings, covered = edgar.fetch_window(today=today)
    overlay = load_yaml(config.DATA_DIR / "pipeline_overlay.yaml")

    match_universe(registrations, companies)
    match_universe(pricings, companies)

    # Published priced lane: real pricing prospectuses (424B4/424B1) plus any
    # universe-matched 424B3. Unmatched 424B3s are overwhelmingly supplements
    # (interval funds file them monthly) — detected, but not lane material.
    pricings = [
        p
        for p in pricings
        if p["form_type"] in ("424B4", "424B1") or p["universe_company_id"] is not None
    ]

    # Offer prices: cached results are free and always used; the network
    # budget covers universe matches first, then the most recent pricings.
    # The cache fills a little more every night until the window is priced.
    budget = config.MAX_PRICE_FETCHES
    for p in pricings:
        hit, price = edgar.cached_price(p["accession_number"])
        if hit:
            p["price_usd"] = price
            continue
        if p["universe_company_id"] is not None:
            p["price_usd"] = edgar.fetch_pricing_price(p["cik"], p["accession_number"])
        elif budget > 0 and not p["fund_keyword_match"]:
            budget -= 1
            p["price_usd"] = edgar.fetch_pricing_price(p["cik"], p["accession_number"])
        else:
            p["price_usd"] = None

    return merge_pipeline(registrations, pricings, covered, overlay)


def merge_pipeline(
    filings: list[dict], pricings: list[dict], covered: list[str], overlay: dict
) -> dict:
    """Attach hand-curated overlay facts (keyed by CIK) to EDGAR filings."""
    by_cik = {str(o["cik"]): o for o in overlay.get("issuers", [])}
    merged = []
    for f in filings:
        entry = dict(f)
        extra = by_cik.get(str(int(f["cik"])))
        if extra:
            entry["curated"] = {
                k: extra[k]
                for k in (
                    "expected_pricing_window",
                    "ticker",
                    "exchange",
                    "retail_brokers",
                    "notes",
                    "source_url",
                    "as_of",
                )
                if k in extra
            }
        merged.append(entry)
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "as_of": utcnow_iso(),
        "source": {
            "name": "SEC EDGAR daily form indexes",
            "url": "https://www.sec.gov/Archives/edgar/daily-index/",
            "days_covered": covered,
        },
        "form_types": list(edgar.REGISTRATION_FORMS),
        "pricing_form_types": list(edgar.PRICING_FORMS),
        "pricing_lane_policy": (
            "424B4/424B1 pricing prospectuses, plus 424B3 only when the filer "
            "matches the tracked universe — unmatched 424B3s are routine "
            "supplements, detected but excluded from the lane."
        ),
        "filings": merged,
        "pricings": pricings,
    }


# ---------------------------------------------------------------------------
# wrappers.json — retail-accessible vehicles with private exposure
# ---------------------------------------------------------------------------

def build_wrappers() -> dict:
    seed = load_yaml(config.DATA_DIR / "wrappers_seed.yaml")
    overlay = load_yaml(config.DATA_DIR / "wrappers_overlay.yaml")
    nav_overlay = {o["id"]: o for o in overlay.get("navs", [])}

    wrappers = []
    for w in seed["wrappers"]:
        entry = {
            "id": w["id"],
            "name": w["name"],
            "structure": w["structure"],
            "ticker": w.get("ticker"),
            "exchange": w.get("exchange"),
            "issuer_url": w["issuer_url"],
            "fees": w["fees"],  # {expense_ratio_pct, description, source_url, as_of}
            "liquidity": w["liquidity"],  # {terms, source_url, as_of}
            "private_exposure_notes": w.get("private_exposure_notes"),
            "nav_expected": w.get("nav_expected", True),
            "nav_note": w.get("nav_note"),
            "nav_unavailable_reason": w.get("nav_unavailable_reason"),
        }

        # --- one quote fetch; it is either the market price or (for funds
        # that transact at NAV) the NAV itself ---
        quote = market.fetch_quote(w["quote_symbol"]) if w.get("quote_symbol") else None
        quote_block = None
        if quote:
            quote_block = {
                "value": quote["price"],
                "currency": quote["currency"],
                "as_of": quote["as_of"],
                "source": "Yahoo Finance quote (unofficial endpoint)",
                "source_url": quote["source_url"],
            }

        price_block = None
        nav_block = None
        if w.get("nav_mode") == "quote_is_nav":
            if quote_block:
                nav_block = dict(
                    quote_block, source="Fund transacts at NAV; NAV via Yahoo Finance quote"
                )
        else:
            price_block = quote_block
        entry["market_price"] = price_block

        if nav_block is None and w["id"] in nav_overlay:
            o = nav_overlay[w["id"]]
            nav_block = {
                "value": o["nav_per_share"],
                "currency": o.get("currency", "USD"),
                "as_of": str(o["as_of"]),
                "source": o["source"],
                "source_url": o["source_url"],
            }
        entry["nav_per_share"] = nav_block

        # --- premium/discount, computed, with the math shown ---
        price_v = price_block["value"] if price_block else None
        nav_v = nav_block["value"] if nav_block else None
        same_ccy = (
            price_block and nav_block and price_block.get("currency") == nav_block.get("currency")
        )
        pct = compute.premium_to_nav_pct(price_v, nav_v) if same_ccy else None
        entry["premium_to_nav_pct"] = float(pct) if pct is not None else None
        entry["premium_to_nav_calculation"] = compute.premium_calculation_note(price_v, nav_v, pct)
        if pct is not None:
            entry["premium_to_nav_inputs_as_of"] = {
                "market_price": price_block["as_of"],
                "nav_per_share": nav_block["as_of"],
            }

        # --- holdings ---
        holdings_block = None
        if w.get("holdings_mode") == "ark_csv":
            try:
                holdings, src = ark.fetch_holdings(w["ark_fund"])
                top = [h for h in holdings if h["weight_pct"] is not None][:15]
                holdings_block = {
                    "as_of": top[0]["as_of"] if top else None,
                    "source": "ARK Invest published holdings CSV",
                    "source_url": src,
                    "top_holdings": top,
                }
            except RuntimeError as exc:
                log.warning("holdings unavailable for %s: %s", w["id"], exc)
        elif w.get("holdings_curated"):
            holdings_block = w["holdings_curated"]
        entry["holdings"] = holdings_block

        wrappers.append(entry)

    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "as_of": utcnow_iso(),
        "notes": seed.get("notes"),
        "wrappers": wrappers,
    }


# ---------------------------------------------------------------------------
# briefs.json — editorial (published separately via briefs_cli; the nightly
# passes the existing document through so the manifest and validation cover it)
# ---------------------------------------------------------------------------

def build_briefs_passthrough() -> dict:
    path = config.FEED_DIR / "briefs.json"
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "as_of": utcnow_iso(),
        "editorial_note": (
            "Human-reviewed editorial summaries of publicly available facts. "
            "Educational information, not investment advice."
        ),
        "briefs": [],
    }


# ---------------------------------------------------------------------------
# signals.json — secondary-market availability + funding news links
# ---------------------------------------------------------------------------

def build_signals() -> dict:
    companies = load_yaml(config.DATA_DIR / "companies_seed.yaml")
    signals = []
    for c in companies["companies"]:
        signals.append(
            {
                "company_id": c["id"],
                "company_name": c["name"],
                "secondary_market": {
                    "status": "unavailable_tos",
                    "note": (
                        "Hiive and comparable secondary-market venues prohibit "
                        "automated access and republication in their terms of use; "
                        "no price level is ingested. Field reserved for licensed or "
                        "expressly permitted data."
                    ),
                    "price_level": None,
                    "as_of": utcnow_iso(),
                },
                "recent_news": [
                    {
                        "kind": "funding_round_report",
                        "url": c["valuation"]["source_url"],
                        "as_of": str(c["valuation"]["as_of"]),
                    }
                ],
                "as_of": str(companies["curated_as_of"]),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "as_of": str(companies["curated_as_of"]),
        "signals": signals,
    }
