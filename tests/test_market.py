from prelude_data.market import parse_chart_meta


def payload(meta: dict, timestamps=None, closes=None) -> dict:
    result = {"meta": meta}
    if timestamps is not None:
        result["timestamp"] = timestamps
        result["indicators"] = {"quote": [{"close": closes}]}
    return {"chart": {"result": [result]}}


class TestParseChartMeta:
    def test_happy_path(self):
        quote = parse_chart_meta(
            payload(
                {
                    "regularMarketPrice": 25.88,
                    "regularMarketTime": 1784042406,
                    "currency": "USD",
                    "fullExchangeName": "NYSE",
                    "instrumentType": "EQUITY",
                }
            )
        )
        assert quote["price"] == 25.88
        assert quote["currency"] == "USD"
        assert quote["as_of"] == "2026-07-14T15:20:06+00:00"
        assert quote["exchange"] == "NYSE"

    def test_missing_price_returns_none(self):
        assert parse_chart_meta(payload({"regularMarketTime": 1784042406})) is None

    def test_missing_timestamp_returns_none(self):
        assert parse_chart_meta(payload({"regularMarketPrice": 25.88})) is None

    def test_stale_meta_loses_to_fresher_series_bar(self):
        # SSSS case: meta stuck two weeks back while daily bars are current
        quote = parse_chart_meta(
            payload(
                {"regularMarketPrice": 12.54, "regularMarketTime": 1782856800, "currency": "USD"},
                timestamps=[1783942200, 1784028600],
                closes=[11.02, 10.91],
            )
        )
        assert quote["price"] == 10.91
        assert quote["as_of"] > "2026-07-13"

    def test_none_bars_are_skipped(self):
        # today's bar can be None pre-close; it must not win or crash
        quote = parse_chart_meta(
            payload(
                {"regularMarketPrice": 12.54, "regularMarketTime": 1784028600, "currency": "USD"},
                timestamps=[1784028600, 1784115000],
                closes=[10.91, None],
            )
        )
        assert quote["price"] == 12.54

    def test_series_only_payload_works(self):
        quote = parse_chart_meta(
            payload({"currency": "USD"}, timestamps=[1784028600], closes=[10.91])
        )
        assert quote["price"] == 10.91

    def test_malformed_payloads_return_none(self):
        assert parse_chart_meta({}) is None
        assert parse_chart_meta({"chart": {"result": []}}) is None
        assert parse_chart_meta({"chart": {"result": None}}) is None
