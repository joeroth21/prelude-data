"""The validation gate: a feed that fails here is never published.

Rules check the two things the app's credibility rests on — every datum has
a source_url, and critical figures are fresh. Failures are returned as a
list of human-readable strings so the runner can log them loudly and keep
the last-good feed in place.
"""

from __future__ import annotations

import datetime as dt

from . import config


def _parse_when(value: str) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _age_days(value: str, now: dt.datetime) -> float | None:
    when = _parse_when(value)
    return None if when is None else (now - when).total_seconds() / 86400


def validate_companies(doc: dict, now: dt.datetime) -> list[str]:
    errors = []
    companies = doc.get("companies", [])
    if len(companies) < config.MIN_COMPANIES:
        errors.append(f"companies: only {len(companies)} entries (< {config.MIN_COMPANIES})")
    curation_age = _age_days(doc.get("as_of", ""), now)
    if curation_age is None:
        errors.append("companies: unparseable curated as_of")
    elif curation_age > config.MAX_CURATION_AGE_DAYS:
        errors.append(
            f"companies: curation is {curation_age:.0f} days old "
            f"(> {config.MAX_CURATION_AGE_DAYS}) — re-review companies_seed.yaml"
        )
    for c in companies:
        cid = c.get("id", "<no id>")
        for field in ("name", "sector", "profile", "ipo_status", "profile_source_url"):
            if not c.get(field):
                errors.append(f"companies[{cid}]: missing {field}")
        val = c.get("valuation") or {}
        if val.get("amount_usd_billions") is None:
            errors.append(f"companies[{cid}]: missing valuation.amount_usd_billions")
        if not val.get("source_url"):
            errors.append(f"companies[{cid}]: missing valuation.source_url")
        age = _age_days(val.get("as_of", ""), now)
        if age is None:
            errors.append(f"companies[{cid}]: unparseable valuation.as_of")
        elif age < 0:
            errors.append(f"companies[{cid}]: valuation.as_of is in the future")
    return errors


def validate_pipeline(doc: dict, now: dt.datetime) -> list[str]:
    errors = []
    covered = doc.get("source", {}).get("days_covered", [])
    if len(covered) < 3:
        errors.append(f"pipeline: only {len(covered)} EDGAR days covered (< 3) — feed too thin")
    age = _age_days(doc.get("generated_at", ""), now)
    if age is None or age > 1:
        errors.append("pipeline: generated_at missing or older than a day")
    for f in doc.get("filings", []):
        acc = f.get("accession_number", "<no accession>")
        for field in ("issuer", "cik", "filing_date", "form_type", "source_url"):
            if not f.get(field):
                errors.append(f"pipeline[{acc}]: missing {field}")
    return errors


def validate_wrappers(doc: dict, now: dt.datetime) -> list[str]:
    errors = []
    wrappers = doc.get("wrappers", [])
    if len(wrappers) < config.MIN_WRAPPERS:
        errors.append(f"wrappers: only {len(wrappers)} entries (< {config.MIN_WRAPPERS})")
    for w in wrappers:
        wid = w.get("id", "<no id>")
        for field in ("name", "structure", "issuer_url"):
            if not w.get(field):
                errors.append(f"wrappers[{wid}]: missing {field}")
        fees = w.get("fees") or {}
        if not fees.get("source_url") or (
            fees.get("expense_ratio_pct") is None and not fees.get("description")
        ):
            errors.append(
                f"wrappers[{wid}]: fees incomplete (need source_url and a ratio or description)"
            )
        liq = w.get("liquidity") or {}
        if not liq.get("terms") or not liq.get("source_url"):
            errors.append(f"wrappers[{wid}]: liquidity incomplete (need terms + source_url)")

        price = w.get("market_price")
        if w.get("ticker") and price is None:
            errors.append(f"wrappers[{wid}]: exchange-traded but market_price unavailable")
        if price:
            age = _age_days(price.get("as_of", ""), now)
            if age is None or age > config.MAX_PRICE_AGE_DAYS:
                errors.append(f"wrappers[{wid}]: market_price stale or unparseable as_of")
        nav = w.get("nav_per_share")
        if nav is None:
            if w.get("nav_expected", True):
                errors.append(f"wrappers[{wid}]: nav_per_share missing")
            elif not w.get("nav_note"):
                errors.append(f"wrappers[{wid}]: NAV not expected but nav_note missing")
        else:
            age = _age_days(nav.get("as_of", ""), now)
            if age is None or age > config.MAX_NAV_AGE_DAYS:
                errors.append(f"wrappers[{wid}]: nav_per_share stale (> {config.MAX_NAV_AGE_DAYS}d)")
            if not nav.get("source_url"):
                errors.append(f"wrappers[{wid}]: nav_per_share missing source_url")
        # If both sides exist in one currency, the computation must be present.
        if (
            price
            and nav
            and price.get("currency") == nav.get("currency")
            and w.get("premium_to_nav_pct") is None
        ):
            errors.append(f"wrappers[{wid}]: price and NAV present but premium_to_nav_pct missing")
    return errors


def validate_signals(doc: dict, now: dt.datetime) -> list[str]:
    errors = []
    for s in doc.get("signals", []):
        sid = s.get("company_id", "<no id>")
        sm = s.get("secondary_market") or {}
        if sm.get("status") not in ("available", "unavailable_tos", "unavailable"):
            errors.append(f"signals[{sid}]: secondary_market.status invalid")
        if sm.get("status") == "available" and sm.get("price_level") is None:
            errors.append(f"signals[{sid}]: status available but no price_level")
        for n in s.get("recent_news", []):
            if not n.get("url"):
                errors.append(f"signals[{sid}]: news item missing url")
    return errors


VALIDATORS = {
    "companies.json": validate_companies,
    "pipeline.json": validate_pipeline,
    "wrappers.json": validate_wrappers,
    "signals.json": validate_signals,
}


def validate_feed(docs: dict[str, dict], now: dt.datetime | None = None) -> list[str]:
    """Validate every product. Returns all failures ([] = publishable)."""
    now = now or dt.datetime.now(tz=dt.timezone.utc)
    errors: list[str] = []
    for filename, validator in VALIDATORS.items():
        if filename not in docs:
            errors.append(f"{filename}: missing from build")
            continue
        errors.extend(validator(docs[filename], now))
    return errors
