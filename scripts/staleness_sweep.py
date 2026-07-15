"""Curation aid: for every stale valuation mark (>365 days), fetch the
company's cited profile page and surface recent valuation-ish sentences so
a human can refresh the seed with citations. Prints candidates; changes
nothing.

Run: .venv/Scripts/python scripts/staleness_sweep.py
"""

from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from prelude_data import config, http_client  # noqa: E402

STALE_AFTER_DAYS = 365
VALUATION_RE = re.compile(
    r"[^.]*(?:valu(?:ed|ation)|worth|tender|share sale|secondary|raised)[^.]*\$\s?[\d,.]+\s?(?:billion|bn|B)[^.]*\.",
    re.IGNORECASE,
)
RECENT_RE = re.compile(r"\b(202[5-6])\b")


def main() -> int:
    today = dt.date.today()
    with open(config.DATA_DIR / "companies_seed.yaml", encoding="utf-8") as fh:
        seed = yaml.safe_load(fh)
    stale = []
    for c in seed["companies"]:
        v = c["valuation"]
        if v["basis"] == "public_market":
            continue
        age = (today - dt.date.fromisoformat(str(v["as_of"])[:10])).days
        if age > STALE_AFTER_DAYS:
            stale.append((c, age))
    print(f"{len(stale)} stale marks (> {STALE_AFTER_DAYS}d) of {len(seed['companies'])} companies\n")

    for c, age in stale:
        url = c["profile_source_url"]
        print(f"### {c['id']} — ${c['valuation']['amount_usd_billions']}B as of {c['valuation']['as_of']} ({age}d) — {url}")
        try:
            html = http_client.get_text(url)
        except RuntimeError as exc:
            print("   FETCH FAILED:", exc)
            continue
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&#\d+;|&nbsp;?|&amp;", " ", text)
        text = re.sub(r"\s+", " ", text)
        candidates = [s.strip() for s in VALUATION_RE.findall(text) if RECENT_RE.search(s)]
        seen: set[str] = set()
        shown = 0
        for s in candidates:
            key = s[:80]
            if key in seen or len(s) > 420:
                continue
            seen.add(key)
            print("   *", s[:400])
            shown += 1
            if shown >= 4:
                break
        if shown == 0:
            print("   (no recent valuation sentences found — stays stale)")
        print()
    return 0


if __name__ == "__main__":
    main()
