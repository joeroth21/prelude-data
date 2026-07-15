import datetime as dt

from prelude_data import builders


class TestLifecycleMapping:
    def test_status_to_lifecycle(self):
        m = builders.LIFECYCLE_FROM_STATUS
        assert m["private"] == "private"
        assert m["rumored"] == "private"  # rumor is curation nuance, not state
        assert m["s1_filed"] == "s1_filed"
        assert m["priced"] == "priced"
        assert m["listed"] == "listed"


class TestMarkAge:
    TODAY = dt.date(2026, 7, 14)

    def test_exact_age(self):
        assert builders.mark_age_days("2026-06-12", self.TODAY) == 32
        assert builders.mark_age_days("2025-07-14", self.TODAY) == 365
        assert builders.mark_age_days("2025-07-13", self.TODAY) == 366

    def test_datetime_strings_truncate(self):
        assert builders.mark_age_days("2026-06-12T15:00:00+00:00", self.TODAY) == 32

    def test_garbage_is_none(self):
        assert builders.mark_age_days("not-a-date", self.TODAY) is None


class TestGraduationOutcome:
    def test_change_pct_exact(self, monkeypatch):
        monkeypatch.setattr(
            builders.market,
            "fetch_quote",
            lambda t: {
                "price": 136.08,
                "currency": "USD",
                "as_of": "2026-07-14T20:00:00+00:00",
                "source_url": "https://finance.yahoo.com/quote/SPCX",
            },
        )
        out = builders.graduation_outcome(135.00, "SPCX")
        assert out["change_from_ipo_pct"] == 0.8  # (136.08/135 - 1)*100 = 0.8
        assert out["ipo_price_usd"] == 135.00
        assert out["current_price_usd"] == 136.08

    def test_negative_outcome(self, monkeypatch):
        monkeypatch.setattr(
            builders.market,
            "fetch_quote",
            lambda t: {"price": 30.0, "currency": "USD", "as_of": "x", "source_url": "u"},
        )
        assert builders.graduation_outcome(40.00, "KLAR")["change_from_ipo_pct"] == -25.0

    def test_no_quote_is_none(self, monkeypatch):
        monkeypatch.setattr(builders.market, "fetch_quote", lambda t: None)
        assert builders.graduation_outcome(135.00, "SPCX") is None


class TestUniverseMatch:
    def test_matches_via_alias(self):
        companies = [
            {"id": "spacex", "name": "SpaceX", "aliases": ["Space Exploration Technologies"]}
        ]
        entries = [
            {"issuer": "SPACE EXPLORATION TECHNOLOGIES CORP"},
            {"issuer": "Apnimed, Inc."},
        ]
        builders.match_universe(entries, companies)
        assert entries[0]["universe_company_id"] == "spacex"
        assert entries[1]["universe_company_id"] is None


class TestAccessRoutes:
    LINKS = [
        {"company_id": "openai", "venue_id": "equityzen", "url": "https://equityzen.com/company/openai/", "as_of": "2026-07-14"},
        {"company_id": "anthropic", "venue_id": "equityzen", "url": "https://x", "as_of": "2026-07-14"},
    ]

    def test_listed_company_gets_public_listing_only(self):
        seed = {"id": "spacex", "ipo_status": "listed", "listed_ticker": "SPCX", "listing_exchange": "Nasdaq"}
        routes = builders.derive_access_routes(seed, self.LINKS)
        assert routes == [
            {
                "kind": "public_listing",
                "ticker": "SPCX",
                "exchange": "Nasdaq",
                "url": "https://finance.yahoo.com/quote/SPCX",
            }
        ]

    def test_private_company_gets_only_its_verified_links(self):
        seed = {"id": "openai", "ipo_status": "private"}
        routes = builders.derive_access_routes(seed, self.LINKS)
        assert len(routes) == 1
        assert routes[0]["kind"] == "venue_page"
        assert routes[0]["venue_id"] == "equityzen"

    def test_private_company_without_links_gets_empty_routes(self):
        seed = {"id": "notion", "ipo_status": "private"}
        assert builders.derive_access_routes(seed, self.LINKS) == []
