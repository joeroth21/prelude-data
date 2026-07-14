"""One-shot status audit over the whole curated universe.

Runs the nightly cross-check (Yahoo symbol resolution + our pipeline's S-1s)
AND a deeper EDGAR company-name probe for S-1/424B4 filings that predate the
pipeline's sliding window. Human-reviewed: prints flags, changes nothing.

Run: .venv/Scripts/python scripts/audit_status.py
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import yaml  # noqa: E402

from prelude_data import config, crosscheck, http_client  # noqa: E402

EDGAR_COMPANY_SEARCH = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={name}"
    "&type={form}&dateb=&owner=include&count=10&output=atom"
)


def edgar_company_hits(name: str, form: str) -> list[str]:
    """Company names EDGAR returns for a name-prefix + form-type search."""
    url = EDGAR_COMPANY_SEARCH.format(name=urllib.parse.quote(name), form=form)
    try:
        text = http_client.get_text(url)
    except RuntimeError:
        return []
    # atom output: <title>COMPANY NAME (CIK ...)</title> entries + CIK links
    titles = re.findall(r"<title>([^<]+)</title>", text)
    return [t for t in titles if "EDGAR" not in t][:10]


def main() -> int:
    with open(config.DATA_DIR / "companies_seed.yaml", encoding="utf-8") as fh:
        seed = yaml.safe_load(fh)
    companies = seed["companies"]

    print(f"=== cross-check (Yahoo symbol resolution) over {len(companies)} companies ===")
    errors = crosscheck.crosscheck_companies(companies, filings=[])
    for e in errors:
        print("FLAG", e)
    if not errors:
        print("no symbol-resolution flags")

    print("\n=== EDGAR company-name S-1/424B4 probe (private/rumored only) ===")
    flags = 0
    for c in companies:
        if c["ipo_status"] not in ("private", "rumored"):
            continue
        for name in crosscheck.company_names(c):
            for form in ("S-1",):
                hits = edgar_company_hits(name, form)
                matched = [
                    h for h in hits if crosscheck.names_match(re.sub(r"\(.*", "", h), name)
                ]
                if matched:
                    flags += 1
                    print(f"FLAG edgar[{c['id']}] name '{name}' -> {matched}")
    if flags == 0:
        print("no EDGAR name-probe flags")

    print("\naudit complete:", len(errors), "symbol flags,", flags, "EDGAR flags")
    return 0


if __name__ == "__main__":
    main()
