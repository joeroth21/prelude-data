"""The Brief — CLI.

    python -m prelude_data.briefs_cli gather-draft   # diff feed, corroborate,
                                                     # draft via local Ollama,
                                                     # leave markdown for review
    python -m prelude_data.briefs_cli publish        # human gate + lint +
                                                     # assemble + push
    python -m prelude_data.briefs_cli publish --dir briefs_drafts/2026-07-14
    python -m prelude_data.briefs_cli publish --no-push

Publishing is ALWAYS manual. The scheduled jobs only ever gather + draft.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

from . import config, market
from .briefs_draft import draft_topic, write_drafts
from .briefs_gather import (
    BASELINE_DIR,
    DRAFTS_ROOT,
    corroborate,
    find_topics,
    load_feed_dir,
    save_baseline,
)
from .briefs_publish import ReviewGateError, publish_cycle

log = logging.getLogger("prelude_data.briefs")


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOG_DIR / f"briefs_{dt.date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(logfile, encoding="utf-8")],
    )




def cmd_gather_draft() -> int:
    current = load_feed_dir(config.FEED_DIR)
    if current is None:
        log.error("no feed at %s — run the nightly pipeline first", config.FEED_DIR)
        return 2
    baseline = load_feed_dir(BASELINE_DIR)
    if baseline is None:
        log.warning("no briefs baseline — first cycle diffs against nothing; seeding baseline AFTER drafting")

    topics = find_topics(current, baseline)
    if not topics:
        log.info("no candidate topics since last cycle — nothing drafted")
        return 0
    log.info("%d candidate topics: %s", len(topics), [t["id"] for t in topics])

    today_dir = DRAFTS_ROOT / dt.date.today().isoformat()
    drafts = []
    failures = []
    skipped = 0
    for topic in topics:
        if (today_dir / f"{topic['id']}.md").exists():
            skipped += 1  # already drafted this cycle — reruns converge
            continue
        materials = corroborate(topic)
        # Live quote as an additional sourced fact for listed-status stories.
        ticker = (
            topic.get("company", {}).get("listed_ticker")
            if topic["kind"] == "status_change"
            else None
        )
        if ticker:
            quote = market.fetch_quote(ticker)
            if quote:
                topic["latest_quote"] = quote
                materials.append(
                    {
                        "url": quote["source_url"],
                        "text": (
                            f"Yahoo Finance quote data for {ticker}: last price "
                            f"{quote['price']} {quote['currency']} as of {quote['as_of']} "
                            f"on {quote['exchange']}."
                        ),
                    }
                )
        # Wrapper topics: the feed's own computed figures are sourced facts —
        # present them as material so drafts always have >=2 documents.
        if topic["kind"] in ("wrapper_move", "wrapper_spotlight"):
            w = topic["wrapper"]
            price = w.get("market_price") or {}
            nav = w.get("nav_per_share") or {}
            lines = [f"PRELUDE feed data for {w['name']} ({w.get('ticker') or 'unlisted'}):"]
            if price:
                lines.append(
                    f"market price {price.get('value')} {price.get('currency')} as of {price.get('as_of')}."
                )
            if nav:
                lines.append(
                    f"stated NAV per share {nav.get('value')} as of {nav.get('as_of')} ({nav.get('source', '')})."
                )
            if w.get("premium_to_nav_pct") is not None:
                lines.append(f"premium_to_nav_pct: {w['premium_to_nav_pct']} percent.")
            if w.get("expense_ratio_pct") is not None:
                lines.append(f"expense ratio {w['expense_ratio_pct']} percent.")
            lines.append(f"liquidity: {w.get('liquidity_terms', '')}")
            src = price.get("source_url") or nav.get("source_url")
            if src:
                materials.append({"url": src, "text": " ".join(lines)})
        if len(materials) < 2:
            log.warning("topic %s: only %d corroborating docs — skipped", topic["id"], len(materials))
            failures.append(topic["id"])
            continue
        log.info("drafting %s from %d sources ...", topic["id"], len(materials))
        draft, errors = draft_topic(topic, materials)
        if draft is None:
            log.error("topic %s failed drafting after retries: %s", topic["id"], errors)
            failures.append(topic["id"])
        else:
            drafts.append(draft)

    if not drafts and skipped == 0:
        log.error("all %d topics failed to draft", len(topics))
        return 1
    if not drafts:
        log.info("nothing new to draft (%d already in today's cycle)", skipped)
        return 0

    out_dir = write_drafts(drafts)
    log.info(
        "=== DRAFTS READY FOR REVIEW ===\n%d draft(s) in %s (failed topics: %s)\n"
        "Review console: scripts/briefs_review.py (Desktop shortcut 'Review The Brief')\n"
        "CLI fallback: edit the .md files, set reviewed: true, then briefs_cli publish",
        len(drafts),
        out_dir,
        failures or "none",
    )
    return 0


def cmd_publish(cycle_dir: str | None, push: bool) -> int:
    try:
        path = publish_cycle(Path(cycle_dir) if cycle_dir else None, push=push)
    except ReviewGateError as exc:
        log.error("%s", exc)
        return 1
    except (ValueError, RuntimeError) as exc:
        log.error("publish refused: %s", exc)
        return 1
    log.info("published %s", path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prelude-briefs")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("gather-draft")
    pub = sub.add_parser("publish")
    pub.add_argument("--dir", default=None, help="cycle directory (default: latest)")
    pub.add_argument("--no-push", action="store_true")
    sub.add_parser("seed-baseline")
    lint_p = sub.add_parser("lint")
    lint_p.add_argument("--dir", default=None, help="cycle directory (default: latest)")
    args = parser.parse_args(argv)

    setup_logging()
    if args.command == "gather-draft":
        return cmd_gather_draft()
    if args.command == "seed-baseline":
        save_baseline()
        log.info("baseline seeded from current feed")
        return 0
    if args.command == "lint":
        from .briefs_publish import latest_cycle_dir, lint_cycle, load_cycle

        cycle = Path(args.dir) if args.dir else latest_cycle_dir()
        errors = lint_cycle(load_cycle(cycle))
        if errors:
            for e in errors:
                log.error("LINT: %s", e)
            return 1
        log.info("lint clean for all drafts in %s", cycle)
        return 0
    return cmd_publish(args.dir, push=not args.no_push)


if __name__ == "__main__":
    sys.exit(main())
