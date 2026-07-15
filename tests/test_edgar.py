import datetime as dt

from prelude_data.edgar import (
    REGISTRATION_FORMS,
    daily_index_url,
    filing_index_url,
    normalize_display,
    parse_424_price,
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
F-1              Global Widgets PLC                                            1888888     20260710    edgar/data/1888888/0001111111-26-000002.txt
424B4            SPACE EXPLORATION TECHNOLOGIES CORP                           1181412     20260612    edgar/data/1181412/0001628280-26-042639.txt
S-3              Someone Else Inc                                              999999      20260710    edgar/data/999999/0001111111-26-000001.txt
"""


class TestParseFormIdx:
    def test_registrations_include_f1(self):
        filings = parse_form_idx(SAMPLE_IDX, REGISTRATION_FORMS)
        assert [f["form_type"] for f in filings] == ["S-1", "S-1", "S-1/A", "F-1"]

    def test_pricing_forms_parse_separately(self):
        pricings = parse_form_idx(SAMPLE_IDX, ("424B4",))
        assert len(pricings) == 1
        assert pricings[0]["issuer"] == "SPACE EXPLORATION TECHNOLOGIES CORP"
        assert pricings[0]["filing_date"] == "2026-06-12"

    def test_fields_are_exact(self):
        f = parse_form_idx(SAMPLE_IDX, REGISTRATION_FORMS)[0]
        assert f["issuer"] == "Apnimed, Inc."
        assert f["cik"] == "1745648"
        assert f["accession_number"] == "0001193125-26-300909"

    def test_display_name_populated(self):
        f = parse_form_idx(SAMPLE_IDX, ("424B4",))[0]
        assert f["display_name"] == "Space Exploration Technologies"
        assert f["entity_suffix"] == "Corp"

    def test_source_url_points_to_filing_index(self):
        f = parse_form_idx(SAMPLE_IDX, REGISTRATION_FORMS)[0]
        assert f["source_url"] == (
            "https://www.sec.gov/Archives/edgar/data/1745648/"
            "000119312526300909/0001193125-26-300909-index.htm"
        )

    def test_fund_keyword_tagging(self):
        filings = parse_form_idx(SAMPLE_IDX, REGISTRATION_FORMS)
        by_name = {f["issuer"]: f["fund_keyword_match"] for f in filings}
        assert by_name["Canary Litecoin ETF"] is True
        assert by_name["Apnimed, Inc."] is False

    def test_empty_input(self):
        assert parse_form_idx("", REGISTRATION_FORMS) == []


class TestNormalizeDisplay:
    def test_all_caps_with_suffix(self):
        assert normalize_display("SPACE EXPLORATION TECHNOLOGIES CORP") == (
            "Space Exploration Technologies",
            "Corp",
        )

    def test_mixed_case_preserved(self):
        assert normalize_display("Apnimed, Inc.") == ("Apnimed", "Inc")

    def test_short_acronyms_survive_in_mixed_case(self):
        display, suffix = normalize_display("Shield AI Holdings, Inc.")
        assert display == "Shield AI Holdings"
        assert suffix == "Inc"

    def test_multi_token_suffix(self):
        assert normalize_display("Global Widgets Holdings Ltd") == ("Global Widgets Holdings", "Ltd")

    def test_state_tag_stripped(self):
        display, _ = normalize_display("TAILORED BRANDS, INC. /DE/")
        assert display == "Tailored Brands"

    def test_pure_suffix_name_kept_verbatim(self):
        assert normalize_display("CO INC") == ("CO INC", None)


class TestParse424Price:
    def test_spacex_style_phrasing(self):
        doc = "<p>The initial public offering price is $135.00 per share.</p>"
        assert parse_424_price(doc) == 135.00

    def test_cerebras_style_phrasing(self):
        doc = "based upon the initial public offering price of $185.00 per share of Class A"
        assert parse_424_price(doc) == 185.00

    def test_price_to_public_phrasing(self):
        doc = "at a price to the public of $21.50 per share"
        assert parse_424_price(doc) == 21.50

    def test_comma_grouped_price(self):
        doc = "initial public offering price is $1,250.00 per share"
        assert parse_424_price(doc) == 1250.00

    def test_html_entities_and_tags_do_not_block(self):
        doc = "price&nbsp;to the public of&#160;$ 42.00 per share"
        assert parse_424_price(doc) == 42.00

    def test_spac_unit_pricing(self):
        doc = "the offering of 25,000,000 units at $10.00 per unit"
        assert parse_424_price(doc) == 10.00

    def test_no_price_returns_none(self):
        assert parse_424_price("This supplement updates the prospectus.") is None

    def test_implausible_price_rejected(self):
        assert parse_424_price("initial public offering price is $999,999,999 per share") is None


class TestUrls:
    def test_quarter_mapping(self):
        assert quarter(1) == 1
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
