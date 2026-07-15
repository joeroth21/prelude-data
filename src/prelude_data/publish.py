"""Atomic feed writes, freshness manifest, git publish.

Strategy: build every document in memory, validate, then write each file
via temp-file + os.replace (atomic on NTFS). The feed directory in git IS
the last-good copy — a failed validation never touches it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from . import SCHEMA_VERSION, __version__, config

log = logging.getLogger(__name__)


def dumps(doc: dict) -> str:
    return json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def build_meta(docs: dict[str, dict], rendered: dict[str, str]) -> dict:
    files = {}
    for name, doc in docs.items():
        files[name] = {
            "as_of": doc.get("as_of"),
            "generated_at": doc.get("generated_at"),
            "sha256": hashlib.sha256(rendered[name].encode("utf-8")).hexdigest(),
            "bytes": len(rendered[name].encode("utf-8")),
            "record_count": _record_count(name, doc),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": __version__,
        "generated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(timespec="seconds"),
        "compliance": (
            "Publicly available factual data compiled for educational display. "
            "No recommendations, ratings, or trade signals."
        ),
        "files": files,
    }


def _record_count(name: str, doc: dict) -> int | None:
    for key in ("companies", "filings", "wrappers", "signals", "briefs"):
        if key in doc:
            return len(doc[key])
    return None


def write_feed(docs: dict[str, dict], feed_dir: Path | None = None) -> list[Path]:
    """Atomically write all documents + feed_meta.json + version index."""
    feed_dir = feed_dir or config.FEED_DIR
    rendered = {name: dumps(doc) for name, doc in docs.items()}
    meta = build_meta(docs, rendered)
    rendered["feed_meta.json"] = dumps(meta)

    written = []
    for name, content in rendered.items():
        path = feed_dir / name
        atomic_write(path, content)
        written.append(path)

    index = {
        "latest_version": "v1",
        "versions": ["v1"],
        "generated_at": meta["generated_at"],
    }
    index_path = feed_dir.parent / "index.json"
    atomic_write(index_path, dumps(index))
    written.append(index_path)
    return written


def git_publish(message: str) -> bool:
    """Commit the feed and push. Returns False (loudly) on any git failure."""
    try:
        subprocess.run(["git", "add", "feed"], cwd=config.REPO_ROOT, check=True, capture_output=True)
        status = subprocess.run(
            ["git", "status", "--porcelain", "feed"],
            cwd=config.REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            log.info("feed unchanged since last publish — nothing to push")
            return True
        subprocess.run(
            ["git", "commit", "-m", message], cwd=config.REPO_ROOT, check=True, capture_output=True
        )
        subprocess.run(["git", "push"], cwd=config.REPO_ROOT, check=True, capture_output=True)
        log.info("feed pushed")
        return True
    except subprocess.CalledProcessError as exc:
        log.error("git publish FAILED: %s\nstdout=%s\nstderr=%s", exc, exc.stdout, exc.stderr)
        return False
