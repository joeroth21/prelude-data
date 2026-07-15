"""Assemble the four data products from live sources + curated YAML.

Every datum carries source_url and as_of. Curated facts come from YAML in
/data (hand-edited, each entry cites its source); live facts come from the
fetchers and carry the timestamp the source reported.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import yaml

from . import ark, compute, config, edgar, market

log = logging.getLogger(__name__)


def utcnow_iso() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat(timespec="seconds")


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# companies.json
# ---------------------------------------------------------------------------

def build_companies() -> dict:
    seed = load_yaml(config.DATA_DIR / "companies_seed.yaml")
    companies = []
    for c in seed["companies"]:
        companies.append(
            {
                "id": c["id"],
                "name": c["name"],
                "sector": c["sector"],
                "profile": c["profile"],
                "profile_source_url": c["profile_source_url"],
                "ipo_status": c["ipo_status"],  # private|rumored|s1_filed|priced|listed
                "ipo_status_source_url": c.get("ipo_status_source_url"),
                "listed_ticker": c.get("listed_ticker"),
                "aliases": c.get("aliases", []),
                "crosscheck_skip": c.get("crosscheck_skip", False),
                "valuation": {
                    "amount_usd_billions": c["valuation"]["amount_usd_billions"],
                    "basis": c["valuation"]["basis"],  # priced_round|secondary_sale|tender_offer|media_report|public_market
                    "round_label": c["valuation"].get("round_label"),
                    "as_of": str(c["valuation"]["as_of"]),
                    "source_url": c["valuation"]["source_url"],
                },
                "as_of": str(seed["curated_as_of"]),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utcnow_iso(),
        "as_of": str(seed["curated_as_of"]),
        "notes": seed.get("notes"),
        "companies": companies,
    }


# ---------------------------------------------------------------------------
# pipeline.json — EDGAR S-1 flow + curated overlay
# ---------------------------------------------------------------------------

def build_pipeline(today: dt.date | None = None) -> dict:
    filings, covered = edgar.fetch_recent_s1_filings(today=today)
    overlay = load_yaml(config.DATA_DIR / "pipeline_overlay.yaml")
    return merge_pipeline(filings, covered, overlay)


def merge_pipeline(filings: list[dict], covered: list[str], overlay: dict) -> dict:
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
        "form_types": list(edgar.FORM_TYPES),
        "filings": merged,
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
