"""Material gathering for The Brief.

Diffs the current feed against the last brief cycle's baseline to find
candidate topics, then fetches 2-4 corroborating public documents per topic
(EDGAR filings, issuer pages, the sources our feed already cites) and
extracts text for the drafting stage. Only hosts already established in
the README's terms table are touched, at the usual <=1 req/sec.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from . import config, http_client

log = logging.getLogger(__name__)

BASELINE_DIR = config.REPO_ROOT / "state" / "briefs_baseline"
DRAFTS_ROOT = config.REPO_ROOT / "briefs_drafts"

# Premium/discount movement worth a story (percentage points since baseline).
PREMIUM_SWING_PP = 5.0
# NAV drift below this (percent) is noise, not news.
NAV_MOVE_MIN_PCT = 2.0
# Current-state facts notable enough to stand alone (no diff required).
SPOTLIGHT_ABS_PREMIUM = 15.0
SPOTLIGHT_CONCENTRATION_PCT = 25.0
MAX_TOPICS = 6
MAX_SOURCE_CHARS = 2600  # per corroborating document, into the draft prompt


def load_feed_dir(path: Path) -> dict[str, dict] | None:
    out = {}
    for name in ("companies.json", "pipeline.json", "wrappers.json"):
        f = path / name
        if not f.exists():
            return None
        out[name] = json.loads(f.read_text(encoding="utf-8"))
    return out


def save_baseline(feed_dir: Path = config.FEED_DIR) -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("companies.json", "pipeline.json", "wrappers.json"):
        (BASELINE_DIR / name).write_text(
            (feed_dir / name).read_text(encoding="utf-8"), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Topic discovery — diffs of our own feed
# ---------------------------------------------------------------------------

def find_topics(current: dict[str, dict], baseline: dict[str, dict] | None) -> list[dict]:
    """Candidate topics, most newsworthy first. Pure; unit-testable."""
    topics: list[dict] = []

    cur_companies = {c["id"]: c for c in current["companies.json"]["companies"]}
    base_companies = (
        {c["id"]: c for c in baseline["companies.json"]["companies"]} if baseline else {}
    )

    # 1. IPO status changes (the big ones)
    for cid, c in cur_companies.items():
        before = base_companies.get(cid)
        if before and before["ipo_status"] != c["ipo_status"]:
            topics.append(
                {
                    "kind": "status_change",
                    "id": f"status-{cid}",
                    "company": c,
                    "from": before["ipo_status"],
                    "to": c["ipo_status"],
                    "seed_urls": [
                        u
                        for u in (
                            c.get("ipo_status_source_url"),
                            c["valuation"]["source_url"],
                            c["profile_source_url"],
                        )
                        if u
                    ],
                }
            )

    # 2. Valuation mark changes
    for cid, c in cur_companies.items():
        before = base_companies.get(cid)
        if (
            before
            and before["ipo_status"] == c["ipo_status"]  # not already covered above
            and before["valuation"]["amount_usd_billions"] != c["valuation"]["amount_usd_billions"]
        ):
            topics.append(
                {
                    "kind": "valuation_change",
                    "id": f"valuation-{cid}",
                    "company": c,
                    "from_billions": before["valuation"]["amount_usd_billions"],
                    "to_billions": c["valuation"]["amount_usd_billions"],
                    "seed_urls": [c["valuation"]["source_url"], c["profile_source_url"]],
                }
            )

    # 3. Wrapper premium/discount swings + NAV mark changes
    cur_wrappers = {w["id"]: w for w in current["wrappers.json"]["wrappers"]}
    base_wrappers = (
        {w["id"]: w for w in baseline["wrappers.json"]["wrappers"]} if baseline else {}
    )
    for wid, w in cur_wrappers.items():
        before = base_wrappers.get(wid)
        cur_p = w.get("premium_to_nav_pct")
        base_p = before.get("premium_to_nav_pct") if before else None
        nav_moved = (
            before
            and w.get("nav_per_share")
            and before.get("nav_per_share")
            and before["nav_per_share"]["value"]
            and abs(w["nav_per_share"]["value"] / before["nav_per_share"]["value"] - 1) * 100
            >= NAV_MOVE_MIN_PCT
        )
        swing = (
            cur_p is not None and base_p is not None and abs(cur_p - base_p) >= PREMIUM_SWING_PP
        )
        if swing or nav_moved:
            topics.append(
                {
                    "kind": "wrapper_move",
                    "id": f"wrapper-{wid}",
                    "wrapper": slim_wrapper(w),
                    "premium_from": base_p,
                    "premium_to": cur_p,
                    "nav_moved": bool(nav_moved),
                    "nav_from": before["nav_per_share"]["value"] if nav_moved else None,
                    "seed_urls": [
                        u
                        for u in (
                            (w.get("nav_per_share") or {}).get("source_url"),
                            (w.get("market_price") or {}).get("source_url"),
                            w.get("issuer_url"),
                        )
                        if u
                    ],
                }
            )

    # 3b. Wrapper spotlights — standing extremes worth a piece even without
    # a diff: deep premium/discount, or heavy single-name concentration.
    covered = {t["id"] for t in topics}
    for wid, w in cur_wrappers.items():
        if f"wrapper-{wid}" in covered:
            continue
        cur_p = w.get("premium_to_nav_pct")
        top = (w.get("holdings") or {}).get("top_holdings") or []
        big_position = next(
            (
                h
                for h in top
                if h.get("weight_pct") is not None
                and h["weight_pct"] >= SPOTLIGHT_CONCENTRATION_PCT
            ),
            None,
        )
        if (cur_p is not None and abs(cur_p) >= SPOTLIGHT_ABS_PREMIUM) or big_position:
            topics.append(
                {
                    "kind": "wrapper_spotlight",
                    "id": f"spotlight-{wid}",
                    "wrapper": slim_wrapper(w),
                    "premium_to_nav_pct": cur_p,
                    "concentrated_holding": big_position,
                    "seed_urls": [
                        u
                        for u in (
                            (w.get("nav_per_share") or {}).get("source_url"),
                            (w.get("holdings") or {}).get("source_url"),
                            w.get("issuer_url"),
                        )
                        if u
                    ],
                }
            )

    # 4. New operating-company S-1s since baseline
    base_accessions = (
        {f["accession_number"] for f in baseline["pipeline.json"]["filings"]} if baseline else set()
    )
    fresh = [
        f
        for f in current["pipeline.json"]["filings"]
        if f["form_type"] == "S-1"
        and not f["fund_keyword_match"]
        and f["accession_number"] not in base_accessions
    ]
    if fresh:
        topics.append(
            {
                "kind": "pipeline_wave",
                "id": "pipeline-new-s1s",
                "filings": fresh[:8],
                "count": len(fresh),
                "seed_urls": [f["source_url"] for f in fresh[:3]],
            }
        )

    return topics[:MAX_TOPICS]


# ---------------------------------------------------------------------------
# Corroboration — fetch and extract from already-trusted sources
# ---------------------------------------------------------------------------

def slim_wrapper(w: dict) -> dict:
    """Compact wrapper facts for the drafting prompt (full doc is too big)."""
    return {
        "id": w["id"],
        "name": w["name"],
        "structure": w["structure"],
        "ticker": w.get("ticker"),
        "expense_ratio_pct": (w.get("fees") or {}).get("expense_ratio_pct"),
        "market_price": w.get("market_price"),
        "nav_per_share": w.get("nav_per_share"),
        "premium_to_nav_pct": w.get("premium_to_nav_pct"),
        "premium_calculation": w.get("premium_to_nav_calculation"),
        "liquidity_terms": (w.get("liquidity") or {}).get("terms", "")[:220],
    }


def _extract_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?|&#160;|&amp;|&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def corroborate(topic: dict, extra_urls: list[str] | None = None) -> list[dict]:
    """Fetch 2-4 documents for a topic; return [{url, text}] extracts."""
    urls: list[str] = []
    for u in (topic.get("seed_urls") or []) + (extra_urls or []):
        if u not in urls:
            urls.append(u)
    materials = []
    for url in urls[:4]:
        try:
            resp = http_client.get(url, ok_codes=(200, 403, 404))
            if resp.status_code != 200:
                log.info("corroboration source %s -> HTTP %s (skipped)", url, resp.status_code)
                continue
            text = _extract_text(resp.text)
            # SEC prospectuses front-load legal boilerplate; window around the
            # offering facts instead of taking the cover page.
            if "sec.gov" in url:
                anchor = text.lower().find("initial public offering price")
                if anchor > 400:
                    text = text[anchor - 300 : anchor + MAX_SOURCE_CHARS - 300]
            materials.append({"url": url, "text": text[:MAX_SOURCE_CHARS]})
        except RuntimeError as exc:
            log.warning("corroboration fetch failed %s: %s", url, exc)
    return materials
