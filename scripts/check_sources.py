"""Curation aid: verify every source_url in the seed YAMLs resolves.

Run after editing seeds:  .venv/Scripts/python scripts/check_sources.py
Bot-blocked-but-reachable (403/405) is reported as WARN, not FAIL — the
citation is still valid for a human reader.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from prelude_data import config, http_client  # noqa: E402


def collect_urls(obj, found: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.endswith("source_url") and isinstance(v, str):
                found.add(v)
            else:
                collect_urls(v, found)
    elif isinstance(obj, list):
        for item in obj:
            collect_urls(item, found)


def main() -> int:
    urls: set[str] = set()
    for name in ("companies_seed.yaml", "wrappers_seed.yaml", "wrappers_overlay.yaml", "pipeline_overlay.yaml"):
        with open(config.DATA_DIR / name, encoding="utf-8") as fh:
            collect_urls(yaml.safe_load(fh) or {}, urls)

    failures, warns = [], []
    for url in sorted(urls):
        try:
            resp = http_client.get(url, ok_codes=(200, 301, 302, 403, 405, 429))
            if resp.status_code in (403, 405, 429):
                warns.append(f"WARN {resp.status_code} {url}")
            else:
                print(f"ok   {resp.status_code} {url}")
        except RuntimeError as exc:
            failures.append(f"FAIL {url} ({exc})")

    for w in warns:
        print(w)
    for f in failures:
        print(f)
    print(f"\n{len(urls)} urls: {len(urls) - len(failures) - len(warns)} ok, {len(warns)} warn, {len(failures)} fail")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
