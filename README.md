# prelude-data

The data pipeline powering **PRELUDE**, a private-markets intelligence iOS app.
Runs nightly on Windows Task Scheduler, builds four JSON data products, validates
them, and publishes versioned static files to GitHub Pages.

**Feed root:** `https://joeroth21.github.io/prelude-data/feed/`

## Compliance posture

This pipeline compiles **publicly available factual data for educational
display**. There are no recommendations, ratings, price targets, or buy/sell
signals anywhere in the schema, and field names are deliberately neutral
(`premium_to_nav_pct`, never anything judgmental). Every datum carries a
`source_url` and an `as_of` timestamp — if a fact can't be sourced and dated,
it isn't published. Where a computation is shown (premium/discount to NAV),
the feed includes the arithmetic (`premium_to_nav_calculation`) and the
timestamps of both inputs.

## Data products (`feed/v1/`)

| File | Contents | Refresh |
|---|---|---|
| `companies.json` | ~55 curated late-stage private companies: sector, factual profile, latest publicly reported valuation (amount, basis, date, source), IPO status | Curated by hand; validation forces re-review every 120 days |
| `pipeline.json` | New S-1 / S-1/A filings from SEC EDGAR daily indexes (14-day sliding window) merged with `data/pipeline_overlay.yaml` (hand-curated: expected pricing windows, tickers, confirmed retail brokers — each with its own source) | Nightly |
| `wrappers.json` | Retail-accessible vehicles with private-company exposure: structure, fees, liquidity terms, live market price, latest NAV, computed `premium_to_nav_pct` with the math shown, holdings | Nightly (prices, ARKVX holdings); NAV marks quarterly via overlay |
| `signals.json` | Per-company secondary-market availability status + funding-round news links | Nightly |
| `feed_meta.json` | Freshness manifest: per-file `as_of`, sha256, byte size, record counts | Nightly |

## Sources, terms posture, cadence

| Source | Used for | Terms posture | Cadence |
|---|---|---|---|
| **SEC EDGAR daily form indexes** (`sec.gov/Archives/edgar/daily-index/`) | S-1 pipeline; NAV citations | Public-domain government data. SEC automated-access guidance followed: identifying User-Agent, ≤1 request/second | Nightly |
| **SEC EDGAR filings** (424B3, 10-Q) | NAV marks for DXYZ, SSSS (via hand-curated `wrappers_overlay.yaml`, exact filing cited) | Same as above | Quarterly, by hand |
| **ARK Invest holdings CSV** (`assets.ark-funds.com`) | ARKVX top holdings | Published openly by the issuer for public consumption; ingested read-only, attributed | Nightly |
| **Yahoo Finance chart endpoint** (`query1.finance.yahoo.com/v8/finance/chart/`) | Market prices (DXYZ, XOVR, SSSS) and ARKVX NAV (fund transacts at NAV) | **Unofficial, undocumented endpoint** — no API contract. Used read-only at ~5 requests/night with attribution and per-quote `as_of`. If it breaks or access posture changes, quotes go absent and validation holds the last-good feed rather than publishing stale prices | Nightly |
| **Yahoo Finance search endpoint** (`query1.finance.yahoo.com/v1/finance/search`) | Status cross-check only: resolving company names/aliases to trading symbols | Same unofficial posture as the chart endpoint; ~60 read-only requests/night at ≤1 req/sec. Used to *refuse* publishing on mismatch, never to publish a datum directly | Nightly |
| **Issuer pages/PDFs** (ARK, ERShares, Fundrise, Destiny) | Fee, liquidity, and holdings facts in `wrappers_seed.yaml` | Ordinary public web pages, cited as sources for hand-curated facts (not scraped nightly) | Quarterly, by hand |
| **Wikipedia** | `companies.json` profile/valuation citations (stable summary pages which themselves cite primary reporting) | CC BY-SA; used as citation links only, no content republished | With curation |
| **Hiive** (secondary-market pricing) | *Not ingested.* | Hiive's Terms of Use prohibit automated access, systematic retrieval, and republication. `signals.json` marks `secondary_market.status = "unavailable_tos"` rather than violating those terms. The field is reserved for licensed or expressly permitted data | — |

## Engineering guarantees

- **Idempotent**: re-running produces the same feed for the same inputs; the
  EDGAR sweep dedupes by accession number; unchanged feeds produce no commit.
- **Atomic writes**: every file lands via temp-file + `os.replace`.
- **Validation gate**: `validate.py` refuses to publish on missing
  `source_url`s, missing/stale prices or NAVs, thin EDGAR coverage, stale
  curation, or schema drift. On refusal the pipeline exits 1, logs each
  failure to stderr and `logs/run_<date>.log`, and the **last-good feed stays
  published** (the git-committed feed is the last-good copy).
- **Status cross-check** (`crosscheck.py`, added after the SpaceX incident —
  the seed said `private` for a month after SPCX listed): every nightly run
  verifies curated `ipo_status` against independent evidence. A company
  marked private/rumored/s1_filed whose name or curated alias resolves to an
  actively trading listed equity fails validation; a listed company whose
  ticker no longer quotes fails; an S-1 in our own `pipeline.json` matching a
  company still marked private/rumored fails. Matching is conservative
  (normalized equality / full-phrase containment over name + `aliases`);
  `crosscheck_skip: true` opts out a known collision, visibly. Nothing is
  auto-corrected — a human updates the seed with a source, then the feed
  publishes. `--skip-crosscheck` exists for offline dev only.
  `scripts/audit_status.py` runs the same checks plus a deeper EDGAR
  company-name probe (catches S-1s older than the pipeline's 14-day window)
  on demand.
- **Freshness manifest**: `feed_meta.json` carries per-file `as_of` + sha256.
- **Dry-run**: `python -m prelude_data.pipeline --dry-run` builds and
  validates into `state/staging/` without touching `feed/` or git.
- **Tests**: 69 unit tests, including exact-value premium/discount math
  (Decimal, banker's rounding), EDGAR/ARK parsers, overlay merge, validators,
  and atomic writes.

## Running

```powershell
cd C:\Dev\prelude-data
.venv\Scripts\python -m pytest -q          # tests
.venv\Scripts\python -m prelude_data.pipeline --dry-run
.venv\Scripts\python -m prelude_data.pipeline           # build + validate + push
.venv\Scripts\python scripts\check_sources.py           # verify seed source URLs
```

### Nightly schedule (Task Scheduler)

Task `PreludeData Nightly` runs `scripts\run_nightly.ps1` daily at 02:30
local time. Recreate with:

```powershell
schtasks /Create /TN "PreludeData Nightly" /SC DAILY /ST 02:30 ^
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Dev\prelude-data\scripts\run_nightly.ps1"
```

## The Brief (editorial stage)

Twice weekly (Task Scheduler `PreludeBriefs Mon` / `PreludeBriefs Thu`, 07:00),
the pipeline gathers material and drafts 4-5 short news pieces for the app's
`briefs.json`:

1. **Gather** (`briefs_gather.py`): diffs the feed against the last cycle's
   baseline (new S-1s, status changes, premium/discount swings, valuation
   marks; plus standing extremes — deep discounts, heavy single-name
   concentration), then fetches 2-4 corroborating documents per topic from
   already-trusted sources (EDGAR, issuer pages, the feed's own citations) at
   the usual ≤1 req/sec.
2. **Draft** (`briefs_draft.py`): local Ollama (`llama3.1:8b`), zero external
   API. Every draft is linted (`briefs_lint.py`) and regenerated on failure:
   no recommendation language (same class of list the app enforces), no 10+
   word verbatim passages from sources, max one quotation under 15 words,
   150-300 word body, ≥2 sources. Drafts land in `briefs_drafts/YYYY-MM-DD/`
   as markdown with `reviewed: false`, and a marker file lands on the Desktop.
3. **Review gate — the human step, non-negotiable**: the scheduled job
   starts the review console (localhost:8377) and fires a Windows toast;
   clicking it opens the console (Desktop shortcut "Review The Brief" works
   any time). The console lists the cycle with status chips, renders each
   piece exactly as the app will (serif reader, drop cap, source chips),
   offers inline markdown editing with live lint, source links for
   verification, per-piece Approve toggles, and one PUBLISH button that
   runs the full gate (re-lint -> reviewed flags -> publish -> push -> app
   snapshot refresh -> "live on Pages" confirmation). CLI fallback
   unchanged: edit drafts, set `reviewed: true`, run
   `python -m prelude_data.briefs_cli publish`. **Publishing is always
   manual** — the scheduled jobs only gather and draft; nothing publishes
   without explicit approval.

The nightly pipeline passes the published `briefs.json` through its own
validation gate (schema, ≥2 sources per piece, forbidden-language scan), so
editorial content is held to the same bar as data.

## Editing the overlays

- `data/pipeline_overlay.yaml` — facts EDGAR can't give (expected pricing
  window, ticker, confirmed retail brokers), keyed by CIK. Cite every entry.
- `data/wrappers_overlay.yaml` — NAV marks from SEC filings, one entry per
  wrapper, exact filing cited. The validation gate rejects NAVs older than
  400 days, so a forgotten quarterly update fails loudly instead of rotting.
- `data/companies_seed.yaml` — the curated universe. Bump `curated_as_of`
  when you review it; validation forces a re-review every 120 days.

After editing, run `scripts/check_sources.py` and a `--dry-run`.
