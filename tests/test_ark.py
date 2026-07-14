from prelude_data.ark import parse_holdings_csv

SAMPLE_CSV = """date,fund,company,ticker,cusip,weight (%)
06/30/2026,ARKVX,SpaceX,SPCX,84615Q103,13.78%
06/30/2026,ARKVX,OpenAI,,,6.33%
06/30/2026,ARKVX,"Anthropic, Inc.",,,4.59%
06/30/2026,ARKVX,Cash Position,,,
"""


class TestParseHoldings:
    def test_parses_rows_in_order(self):
        holdings = parse_holdings_csv(SAMPLE_CSV)
        assert [h["name"] for h in holdings] == [
            "SpaceX",
            "OpenAI",
            "Anthropic, Inc.",
            "Cash Position",
        ]

    def test_weights_exact(self):
        holdings = parse_holdings_csv(SAMPLE_CSV)
        assert holdings[0]["weight_pct"] == 13.78
        assert holdings[1]["weight_pct"] == 6.33

    def test_missing_ticker_is_none(self):
        holdings = parse_holdings_csv(SAMPLE_CSV)
        assert holdings[0]["ticker"] == "SPCX"
        assert holdings[1]["ticker"] is None

    def test_date_converted_to_iso(self):
        assert parse_holdings_csv(SAMPLE_CSV)[0]["as_of"] == "2026-06-30"

    def test_blank_weight_is_none_not_zero(self):
        assert parse_holdings_csv(SAMPLE_CSV)[3]["weight_pct"] is None

    def test_quoted_company_names_survive(self):
        assert parse_holdings_csv(SAMPLE_CSV)[2]["name"] == "Anthropic, Inc."

    def test_empty_csv(self):
        assert parse_holdings_csv("date,fund,company,ticker,cusip,weight (%)\n") == []
