import datetime as dt

from prelude_data.crosscheck import (
    crosscheck_companies,
    names_match,
    normalize_name,
    s1_escalations,
)

NOW = dt.datetime(2026, 7, 14, 12, 0, tzinfo=dt.timezone.utc)


def company(**over):
    base = {
        "id": "spacex",
        "name": "SpaceX",
        "aliases": ["Space Exploration Technologies"],
        "ipo_status": "private",
        "listed_ticker": None,
    }
    base.update(over)
    return base


def filing(**over):
    base = {
        "form_type": "S-1",
        "issuer": "Space Exploration Technologies Corp",
        "filing_date": "2026-05-20",
        "source_url": "https://example.com/s1",
    }
    base.update(over)
    return base


SPCX_HIT = [
    {
        "symbol": "SPCX",
        "exchange": "NMS",
        "longname": "Space Exploration Technologies Corp.",
        "shortname": "Space Exploration Technologies ",
    }
]


def searcher(results_by_query):
    def search(query):
        return results_by_query.get(query, [])
    return search


class TestNameMatching:
    def test_normalize_strips_suffixes(self):
        assert normalize_name("Space Exploration Technologies Corp.") == "space exploration"
        assert normalize_name("Stripe, Inc.") == "stripe"

    def test_alias_equality_via_normalization(self):
        assert names_match("Space Exploration Technologies Corp.", "Space Exploration Technologies")

    def test_short_names_do_not_phrase_match(self):
        # "Ramp" must not match "Ramp Metals Inc" style collisions via phrases
        assert not names_match("Ramp", "Ramp Metals")
        assert names_match("Ramp", "Ramp, Inc.")  # equality after suffix strip is fine

    def test_unrelated_names_do_not_match(self):
        assert not names_match("Stripe", "Stripes Group Fund V")
        assert not names_match("Notion", "Notional Financial Corp")


class TestPrivateButTrading:
    def test_spacex_case_fails_validation(self):
        errors = crosscheck_companies(
            [company()],
            [],
            now=NOW,
            search=searcher({"SpaceX": [], "Space Exploration Technologies": SPCX_HIT}),
            trading_check=lambda symbol, now: symbol == "SPCX",
        )
        assert len(errors) == 1
        assert "marked private" in errors[0]
        assert "SPCX" in errors[0]

    def test_private_with_no_symbol_passes(self):
        errors = crosscheck_companies(
            [company(id="anthropic", name="Anthropic", aliases=[])],
            [],
            now=NOW,
            search=searcher({}),
            trading_check=lambda s, n: False,
        )
        assert errors == []

    def test_candidate_with_unmatched_name_is_ignored(self):
        # A search hit whose name doesn't match the company must not flag.
        noise = [{"symbol": "SPCE", "exchange": "NYQ", "longname": "Virgin Galactic Holdings", "shortname": "Virgin Galactic"}]
        errors = crosscheck_companies(
            [company(aliases=[])],
            [],
            now=NOW,
            search=searcher({"SpaceX": noise}),
            trading_check=lambda s, n: True,
        )
        assert errors == []

    def test_non_listed_exchange_hits_are_ignored(self):
        otc = [dict(SPCX_HIT[0], exchange="PNK")]
        errors = crosscheck_companies(
            [company()],
            [],
            now=NOW,
            search=searcher({"Space Exploration Technologies": otc}),
            trading_check=lambda s, n: True,
        )
        assert errors == []

    def test_resolving_but_not_trading_passes(self):
        errors = crosscheck_companies(
            [company()],
            [],
            now=NOW,
            search=searcher({"Space Exploration Technologies": SPCX_HIT}),
            trading_check=lambda s, n: False,
        )
        assert errors == []

    def test_crosscheck_skip_opts_out_visibly(self):
        errors = crosscheck_companies(
            [company(crosscheck_skip=True)],
            [filing()],
            now=NOW,
            search=searcher({"Space Exploration Technologies": SPCX_HIT}),
            trading_check=lambda s, n: True,
        )
        assert errors == []


class TestListedSide:
    def test_listed_with_active_ticker_passes(self):
        errors = crosscheck_companies(
            [company(ipo_status="listed", listed_ticker="SPCX")],
            [],
            now=NOW,
            search=searcher({}),
            trading_check=lambda symbol, now: symbol == "SPCX",
        )
        assert errors == []

    def test_listed_without_ticker_fails(self):
        errors = crosscheck_companies(
            [company(ipo_status="listed", listed_ticker=None)],
            [],
            now=NOW,
            search=searcher({}),
            trading_check=lambda s, n: True,
        )
        assert any("no listed_ticker" in e for e in errors)

    def test_listed_with_dead_ticker_fails(self):
        errors = crosscheck_companies(
            [company(ipo_status="listed", listed_ticker="ZZZZ")],
            [],
            now=NOW,
            search=searcher({}),
            trading_check=lambda s, n: False,
        )
        assert any("no active quote" in e for e in errors)


class TestS1Escalation:
    def test_private_company_with_matching_s1_fails(self):
        errors = s1_escalations([company()], [filing()])
        assert len(errors) == 1
        assert "escalate status" in errors[0]

    def test_match_works_through_alias_not_just_name(self):
        # 'SpaceX' alone would never match the legal issuer name
        errors = s1_escalations([company(aliases=[])], [filing()])
        assert errors == []
        errors = s1_escalations([company()], [filing()])
        assert len(errors) == 1

    def test_s1_filed_status_is_consistent_no_error(self):
        errors = s1_escalations([company(ipo_status="s1_filed")], [filing()])
        assert errors == []

    def test_unrelated_issuer_no_error(self):
        errors = s1_escalations([company()], [filing(issuer="Apnimed, Inc.")])
        assert errors == []

    def test_one_error_per_company_even_with_multiple_filings(self):
        errors = s1_escalations(
            [company()],
            [filing(), filing(form_type="S-1/A", filing_date="2026-06-03")],
        )
        assert len(errors) == 1
