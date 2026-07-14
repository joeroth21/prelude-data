import datetime as dt

from prelude_data.edgar import (
    daily_index_url,
    filing_index_url,
    parse_form_idx,
    quarter,
)

SAMPLE_IDX = """Description:           Daily Index of EDGAR Dissemination Feed by Form Type
Last Data Received:    Jul 10, 2026
Comments:              webmaster@sec.gov

Form Type   Company Name                                                  CIK
      Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
10-K             ADM TRONICS UNLIMITED, INC.                                   849401      20260710    edgar/data/849401/0001437749-26-023316.txt
S-1              Apnimed, Inc.                                                 1745648     20260710    edgar/data/1745648/0001193125-26-300909.txt
S-1              Canary Litecoin ETF                                           2039461     20241015    edgar/data/2039461/0001999371-24-013330.txt
S-1/A            Holtec Nuclear Corp                                           2104277     20260710    edgar/data/2104277/0001193125-26-301023.txt
S-3              Someone Else Inc                                              999999      20260710    edgar/data/999999/0001111111-26-000001.txt
"""


class TestParseFormIdx:
    def test_extracts_only_wanted_forms(self):
        filings = parse_form_idx(SAMPLE_IDX)
        assert [f["form_type"] for f in filings] == ["S-1", "S-1", "S-1/A"]

    def test_fields_are_exact(self):
        f = parse_form_idx(SAMPLE_IDX)[0]
        assert f["issuer"] == "Apnimed, Inc."
        assert f["cik"] == "1745648"
        assert f["filing_date"] == "2026-07-10"
        assert f["accession_number"] == "0001193125-26-300909"

    def test_source_url_points_to_filing_index(self):
        f = parse_form_idx(SAMPLE_IDX)[0]
        assert f["source_url"] == (
            "https://www.sec.gov/Archives/edgar/data/1745648/"
            "000119312526300909/0001193125-26-300909-index.htm"
        )

    def test_fund_keyword_tagging(self):
        filings = parse_form_idx(SAMPLE_IDX)
        by_name = {f["issuer"]: f["fund_keyword_match"] for f in filings}
        assert by_name["Canary Litecoin ETF"] is True
        assert by_name["Apnimed, Inc."] is False
        assert by_name["Holtec Nuclear Corp"] is False

    def test_empty_input(self):
        assert parse_form_idx("") == []


class TestUrls:
    def test_quarter_mapping(self):
        assert quarter(1) == 1
        assert quarter(3) == 1
        assert quarter(4) == 2
        assert quarter(7) == 3
        assert quarter(12) == 4

    def test_daily_index_url(self):
        assert daily_index_url(dt.date(2026, 7, 10)) == (
            "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/form.20260710.idx"
        )

    def test_filing_index_url_strips_leading_zeros(self):
        assert filing_index_url("0001745648", "0001193125-26-300909").startswith(
            "https://www.sec.gov/Archives/edgar/data/1745648/"
        )
