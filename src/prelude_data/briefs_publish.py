"""Publish stage for The Brief — the human gate lives here.

`publish(dir)` parses every draft in the cycle directory and REFUSES the
entire publish if any draft still has `reviewed: false`. Reviewed drafts
are re-linted (a human edited them), assembled into briefs.json (appended
to existing briefs, newest first), validated, written atomically with an
incremental feed_meta update, and pushed. The gather baseline advances only
after a successful publish.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from pathlib import Path

from . import SCHEMA_VERSION, briefs_lint, config, publish, validate
from .briefs_draft import parse_draft_markdown
from .briefs_gather import DRAFTS_ROOT, save_baseline

log = logging.getLogger(__name__)

MAX_PUBLISHED_BRIEFS = 60


class ReviewGateError(Exception):
    """Raised when any draft in the cycle is not human-reviewed."""


def load_cycle(cycle_dir: Path) -> list[dict]:
    drafts = []
    for path in sorted(cycle_dir.glob("*.md")):
        drafts.append({**parse_draft_markdown(path.read_text(encoding="utf-8")), "_file": path.name})
    if not drafts:
        raise ValueError(f"no drafts found in {cycle_dir}")
    return drafts


def gate(drafts: list[dict]) -> None:
    unreviewed = [d["_file"] for d in drafts if not d["reviewed"]]
    if unreviewed:
        raise ReviewGateError(
            "REVIEW GATE: refusing to publish — these drafts are not marked "
            f"'reviewed: true': {', '.join(unreviewed)}. Edit each draft, flip "
            "the flag, and run publish again (delete a file to drop the piece)."
        )


def lint_cycle(drafts: list[dict]) -> list[str]:
    errors = []
    for d in drafts:
        # Post-edit lint: no source texts on disk anymore — language, quote,
        # shape and source rules still apply in full.
        for e in briefs_lint.lint_draft(d["title"], d["body"], d["why_it_matters"], d["sources"], []):
            errors.append(f"{d['_file']}: {e}")
    return errors


def assemble(drafts: list[dict], existing: dict | None, now: dt.datetime) -> dict:
    prior = existing.get("briefs", []) if existing else []
    new_ids = {d["id"] for d in drafts}
    kept = [b for b in prior if b["id"] not in new_ids]
    briefs = [
        {
            "id": d["id"],
            "date": d["date"],
            "title": d["title"],
            "kind": d["kind"],
            "tickers": d["tickers"],
            "body": d["body"],
            "why_it_matters": d["why_it_matters"],
            "sources": d["sources"],
        }
        for d in drafts
    ] + kept
    briefs.sort(key=lambda b: (b["date"], b["id"]), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "as_of": now.isoformat(timespec="seconds"),
        "editorial_note": (
            "Human-reviewed editorial summaries of publicly available facts. "
            "Educational information, not investment advice."
        ),
        "briefs": briefs[:MAX_PUBLISHED_BRIEFS],
    }


def validate_briefs_doc(doc: dict) -> list[str]:
    return validate.validate_briefs(doc, dt.datetime.now(tz=dt.timezone.utc))


def publish_cycle(cycle_dir: Path | None = None, push: bool = True) -> Path:
    cycle_dir = cycle_dir or latest_cycle_dir()
    drafts = load_cycle(cycle_dir)
    gate(drafts)  # <- the non-negotiable human step

    errors = lint_cycle(drafts)
    if errors:
        raise ValueError("post-review lint failed:\n  - " + "\n  - ".join(errors))

    briefs_path = config.FEED_DIR / "briefs.json"
    existing = json.loads(briefs_path.read_text(encoding="utf-8")) if briefs_path.exists() else None
    doc = assemble(drafts, existing, dt.datetime.now(tz=dt.timezone.utc))

    errors = validate_briefs_doc(doc)
    if errors:
        raise ValueError("briefs validation failed:\n  - " + "\n  - ".join(errors))

    rendered = publish.dumps(doc)
    publish.atomic_write(briefs_path, rendered)
    _update_meta_incremental("briefs.json", doc, rendered)
    log.info("briefs.json written with %d briefs", len(doc["briefs"]))

    if push:
        ok = publish.git_publish(f"feed: The Brief — {cycle_dir.name} cycle ({len(drafts)} pieces)")
        if not ok:
            raise RuntimeError("git publish failed — briefs written locally but not pushed")
    save_baseline()  # next gather diffs from this point
    return briefs_path


def _update_meta_incremental(name: str, doc: dict, rendered: str) -> None:
    meta_path = config.FEED_DIR / "feed_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["files"][name] = {
        "as_of": doc.get("as_of"),
        "generated_at": doc.get("generated_at"),
        "sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "bytes": len(rendered.encode("utf-8")),
        "record_count": len(doc.get("briefs", [])),
    }
    publish.atomic_write(meta_path, publish.dumps(meta))


def latest_cycle_dir() -> Path:
    dirs = sorted([p for p in DRAFTS_ROOT.iterdir() if p.is_dir()]) if DRAFTS_ROOT.exists() else []
    if not dirs:
        raise ValueError(f"no draft cycles under {DRAFTS_ROOT}")
    return dirs[-1]
