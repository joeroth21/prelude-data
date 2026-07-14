import datetime as dt

from prelude_data.validate import (
    validate_companies,
    validate_feed,
    validate_pipeline,
    validate_signals,
    validate_wrappers,
)

NOW = dt.datetime(2026, 7, 14, 12, 0, tzinfo=dt.timezone.utc)


def company(**over):
    base = {
        "id": "acme",
        "name": "Acme",
        "sector": "AI",
        "profile": "Does things.",
        "profile_source_url": "https://example.com/acme",
        "ipo_status": "private",
        "valuation": {
            "amount_usd_billions": 10,
            "basis": "priced_round",
            "as_of": "2025-06-01",
            "source_url": "https://example.com/round",
        },
    }
    base.update(over)
    return base


def companies_doc(companies, as_of="2026-07-01"):
    return {"companies": companies, "as_of": as_of}


class TestCompanies:
    def test_valid_doc_passes(self):
        doc = companies_doc([company(id=f"c{i}") for i in range(45)])
        assert validate_companies(doc, NOW) == []

    def test_too_few_companies_fails(self):
        doc = companies_doc([company()])
        assert any("only 1 entries" in e for e in validate_companies(doc, NOW))

    def test_stale_curation_fails(self):
        doc = companies_doc([company(id=f"c{i}") for i in range(45)], as_of="2025-12-01")
        assert any("re-review companies_seed" in e for e in validate_companies(doc, NOW))

    def test_missing_valuation_source_fails(self):
        bad = company()
        bad["valuation"] = dict(bad["valuation"], source_url=None)
        errors = validate_companies(companies_doc([bad] * 45), NOW)
        assert any("valuation.source_url" in e for e in errors)

    def test_future_valuation_date_fails(self):
        bad = company()
        bad["valuation"] = dict(bad["valuation"], as_of="2027-01-01")
        errors = validate_companies(companies_doc([bad] * 45), NOW)
        assert any("in the future" in e for e in errors)

    def test_missing_profile_fails(self):
        errors = validate_companies(companies_doc([company(profile="")] * 45), NOW)
        assert any("missing profile" in e for e in errors)


def pipeline_doc(**over):
    base = {
        "generated_at": NOW.isoformat(),
        "source": {"days_covered": ["2026-07-08", "2026-07-09", "2026-07-10"]},
        "filings": [
            {
                "issuer": "Acme",
                "cik": "123",
                "filing_date": "2026-07-10",
                "form_type": "S-1",
                "accession_number": "0000000000-26-000001",
                "source_url": "https://example.com/f",
            }
        ],
    }
    base.update(over)
    return base


class TestPipeline:
    def test_valid_doc_passes(self):
        assert validate_pipeline(pipeline_doc(), NOW) == []

    def test_thin_coverage_fails(self):
        doc = pipeline_doc(source={"days_covered": ["2026-07-10"]})
        assert any("too thin" in e for e in validate_pipeline(doc, NOW))

    def test_stale_generated_at_fails(self):
        doc = pipeline_doc(generated_at="2026-07-01T00:00:00+00:00")
        assert any("older than a day" in e for e in validate_pipeline(doc, NOW))

    def test_filing_without_source_url_fails(self):
        doc = pipeline_doc()
        doc["filings"][0].pop("source_url")
        assert any("missing source_url" in e for e in validate_pipeline(doc, NOW))


def wrapper(**over):
    base = {
        "id": "wrap",
        "name": "Wrapper",
        "structure": "listed_closed_end_fund",
        "ticker": "WRAP",
        "issuer_url": "https://example.com",
        "fees": {"expense_ratio_pct": 1.0, "source_url": "https://example.com/fees"},
        "liquidity": {"terms": "Trades on NYSE.", "source_url": "https://example.com/liq"},
        "nav_expected": True,
        "market_price": {
            "value": 25.88,
            "currency": "USD",
            "as_of": "2026-07-13T20:00:00+00:00",
            "source_url": "https://example.com/quote",
        },
        "nav_per_share": {
            "value": 24.56,
            "currency": "USD",
            "as_of": "2026-03-31",
            "source_url": "https://example.com/nav",
        },
        "premium_to_nav_pct": 5.37,
    }
    base.update(over)
    return base


class TestWrappers:
    def test_valid_doc_passes(self):
        doc = {"wrappers": [wrapper(id=f"w{i}") for i in range(4)]}
        assert validate_wrappers(doc, NOW) == []

    def test_exchange_traded_without_price_fails(self):
        doc = {"wrappers": [wrapper(market_price=None)] * 4}
        assert any("market_price unavailable" in e for e in validate_wrappers(doc, NOW))

    def test_stale_price_fails(self):
        stale = wrapper()
        stale["market_price"] = dict(stale["market_price"], as_of="2026-06-01T00:00:00+00:00")
        doc = {"wrappers": [stale] * 4}
        assert any("market_price stale" in e for e in validate_wrappers(doc, NOW))

    def test_stale_nav_fails(self):
        stale = wrapper()
        stale["nav_per_share"] = dict(stale["nav_per_share"], as_of="2024-01-01")
        doc = {"wrappers": [stale] * 4}
        assert any("nav_per_share stale" in e for e in validate_wrappers(doc, NOW))

    def test_missing_nav_fails_when_expected(self):
        doc = {"wrappers": [wrapper(nav_per_share=None, premium_to_nav_pct=None)] * 4}
        assert any("nav_per_share missing" in e for e in validate_wrappers(doc, NOW))

    def test_missing_nav_ok_when_not_expected_with_note(self):
        w = wrapper(
            nav_per_share=None,
            premium_to_nav_pct=None,
            nav_expected=False,
            nav_note="Issuer publishes NAV but not machine-readably.",
        )
        doc = {"wrappers": [w] * 4}
        assert validate_wrappers(doc, NOW) == []

    def test_price_and_nav_without_premium_fails(self):
        doc = {"wrappers": [wrapper(premium_to_nav_pct=None)] * 4}
        assert any("premium_to_nav_pct missing" in e for e in validate_wrappers(doc, NOW))

    def test_nav_without_source_fails(self):
        w = wrapper()
        w["nav_per_share"] = dict(w["nav_per_share"], source_url=None)
        doc = {"wrappers": [w] * 4}
        assert any("missing source_url" in e for e in validate_wrappers(doc, NOW))


class TestSignals:
    def test_valid_unavailable_tos_passes(self):
        doc = {
            "signals": [
                {
                    "company_id": "acme",
                    "secondary_market": {"status": "unavailable_tos", "price_level": None},
                    "recent_news": [{"url": "https://example.com"}],
                }
            ]
        }
        assert validate_signals(doc, NOW) == []

    def test_available_without_price_fails(self):
        doc = {
            "signals": [
                {
                    "company_id": "acme",
                    "secondary_market": {"status": "available", "price_level": None},
                    "recent_news": [],
                }
            ]
        }
        assert any("no price_level" in e for e in validate_signals(doc, NOW))

    def test_invalid_status_fails(self):
        doc = {
            "signals": [
                {"company_id": "acme", "secondary_market": {"status": "hot_deal"}, "recent_news": []}
            ]
        }
        assert any("status invalid" in e for e in validate_signals(doc, NOW))


class TestFeedGate:
    def test_missing_product_refuses_feed(self):
        errors = validate_feed({"companies.json": companies_doc([company()] * 45)}, NOW)
        assert any("pipeline.json: missing from build" in e for e in errors)
        assert any("wrappers.json: missing from build" in e for e in errors)
