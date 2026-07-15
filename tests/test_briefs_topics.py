from prelude_data.briefs_gather import find_topics


def feed(companies=(), wrappers=(), filings=()):
    return {
        "companies.json": {"companies": list(companies)},
        "wrappers.json": {"wrappers": list(wrappers)},
        "pipeline.json": {"filings": list(filings)},
    }


def co(cid="spacex", status="private", billions=400.0):
    return {
        "id": cid,
        "name": cid,
        "ipo_status": status,
        "ipo_status_source_url": "https://example.com/status",
        "profile_source_url": "https://example.com/profile",
        "valuation": {"amount_usd_billions": billions, "source_url": "https://example.com/val"},
    }


def wr(wid="dxyz", premium=5.56, nav=24.56, holdings_weight=None):
    top = (
        [{"name": "SpaceX", "weight_pct": holdings_weight, "as_of": "2026-03-31"}]
        if holdings_weight is not None
        else []
    )
    return {
        "id": wid,
        "name": wid.upper(),
        "structure": "listed_closed_end_fund",
        "issuer_url": "https://example.com/issuer",
        "fees": {"expense_ratio_pct": 1.0},
        "liquidity": {"terms": "Trades on NYSE."},
        "premium_to_nav_pct": premium,
        "market_price": {"value": 25.9, "source_url": "https://example.com/quote"},
        "nav_per_share": {"value": nav, "source_url": "https://example.com/nav"},
        "holdings": {"source_url": "https://example.com/holdings", "top_holdings": top},
    }


class TestFindTopics:
    def test_status_change_detected(self):
        base = feed(companies=[co(status="private")], wrappers=[wr()])
        cur = feed(companies=[co(status="listed")], wrappers=[wr()])
        topics = find_topics(cur, base)
        status = [t for t in topics if t["kind"] == "status_change"]
        assert len(status) == 1
        assert status[0]["from"] == "private" and status[0]["to"] == "listed"

    def test_valuation_change_detected_only_without_status_change(self):
        base = feed(companies=[co(billions=400)])
        cur = feed(companies=[co(billions=500)])
        topics = find_topics(cur, base)
        assert [t["kind"] for t in topics] == ["valuation_change"]

    def test_premium_swing_detected(self):
        base = feed(wrappers=[wr(premium=5.0)])
        cur = feed(wrappers=[wr(premium=12.0)])
        kinds = [t["kind"] for t in find_topics(cur, base)]
        assert "wrapper_move" in kinds

    def test_small_premium_move_ignored(self):
        base = feed(wrappers=[wr(premium=5.0)])
        cur = feed(wrappers=[wr(premium=7.0)])
        assert all(t["kind"] != "wrapper_move" for t in find_topics(cur, base))

    def test_spotlight_deep_discount(self):
        cur = feed(wrappers=[wr(wid="ssss", premium=-23.38)])
        topics = find_topics(cur, None)
        assert [t["id"] for t in topics] == ["spotlight-ssss"]

    def test_spotlight_concentration(self):
        cur = feed(wrappers=[wr(wid="xovr", premium=None, holdings_weight=44.65)])
        topics = find_topics(cur, None)
        assert [t["id"] for t in topics] == ["spotlight-xovr"]
        assert topics[0]["concentrated_holding"]["weight_pct"] == 44.65

    def test_no_duplicate_wrapper_topic(self):
        base = feed(wrappers=[wr(wid="ssss", premium=-5.0)])
        cur = feed(wrappers=[wr(wid="ssss", premium=-23.38)])
        ids = [t["id"] for t in find_topics(cur, base)]
        assert ids.count("wrapper-ssss") == 1
        assert "spotlight-ssss" not in ids

    def test_new_s1_wave_excludes_funds_and_known(self):
        known = {"form_type": "S-1", "fund_keyword_match": False, "accession_number": "a1", "source_url": "u"}
        fund = {"form_type": "S-1", "fund_keyword_match": True, "accession_number": "a2", "source_url": "u"}
        fresh = {"form_type": "S-1", "fund_keyword_match": False, "accession_number": "a3", "source_url": "u"}
        base = feed(filings=[known])
        cur = feed(filings=[known, fund, fresh])
        waves = [t for t in find_topics(cur, base) if t["kind"] == "pipeline_wave"]
        assert len(waves) == 1
        assert waves[0]["count"] == 1
