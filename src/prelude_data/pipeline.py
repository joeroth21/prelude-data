"""Nightly pipeline entry point.

    python -m prelude_data.pipeline            # build, validate, write, push
    python -m prelude_data.pipeline --dry-run  # build + validate only (writes
                                               # to state/staging for review)
    python -m prelude_data.pipeline --no-push  # write feed/, skip git push

Exit codes: 0 published (or clean dry run), 1 validation refused the feed,
2 build crashed. Validation failure leaves the last-good feed untouched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from . import builders, config, crosscheck, publish, validate

log = logging.getLogger("prelude_data")


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = config.LOG_DIR / f"run_{dt.date.today().isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr), logging.FileHandler(logfile, encoding="utf-8")],
    )


def build_all() -> dict[str, dict]:
    docs: dict[str, dict] = {}
    log.info("building companies.json ...")
    docs["companies.json"] = builders.build_companies()
    log.info("building pipeline.json (EDGAR %d-day window) ...", config.EDGAR_LOOKBACK_DAYS)
    docs["pipeline.json"] = builders.build_pipeline(docs["companies.json"]["companies"])
    log.info("building wrappers.json (quotes + NAV + holdings) ...")
    docs["wrappers.json"] = builders.build_wrappers()
    log.info("building signals.json ...")
    docs["signals.json"] = builders.build_signals()
    docs["briefs.json"] = builders.build_briefs_passthrough()
    return docs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prelude-data")
    parser.add_argument("--dry-run", action="store_true", help="build + validate, do not touch feed/ or git")
    parser.add_argument("--no-push", action="store_true", help="write feed/ but skip git commit/push")
    parser.add_argument(
        "--skip-crosscheck",
        action="store_true",
        help="skip the networked status cross-check (offline dev only — nightly runs it)",
    )
    args = parser.parse_args(argv)

    setup_logging()
    log.info("=== prelude-data run start (dry_run=%s) ===", args.dry_run)

    try:
        docs = build_all()
    except Exception:
        log.exception("BUILD CRASHED — last-good feed untouched")
        return 2

    errors = validate.validate_feed(docs)
    if not args.skip_crosscheck:
        log.info("cross-checking curated statuses against market + our own pipeline ...")
        errors.extend(
            crosscheck.crosscheck_companies(
                docs["companies.json"]["companies"], docs["pipeline.json"]["filings"]
            )
        )
    else:
        log.warning("cross-check SKIPPED (--skip-crosscheck)")
    if errors:
        log.error("VALIDATION REFUSED THE FEED — %d problem(s); last-good feed stays published:", len(errors))
        for e in errors:
            log.error("  - %s", e)
        return 1
    log.info("validation + cross-check passed for all %d products", len(docs))

    if args.dry_run:
        staged = publish.write_feed(docs, feed_dir=config.STAGING_DIR / "v1")
        log.info("dry run: staged %d files under %s", len(staged), config.STAGING_DIR)
        return 0

    written = publish.write_feed(docs)
    log.info("wrote %d feed files", len(written))

    if args.no_push:
        log.info("skipping git publish (--no-push)")
        return 0

    ok = publish.git_publish(f"feed: nightly build {dt.date.today().isoformat()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
