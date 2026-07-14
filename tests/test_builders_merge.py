from prelude_data.builders import merge_pipeline


def filing(cik="2104277", **over):
    base = {
        "form_type": "S-1",
        "issuer": "Holtec Nuclear Corp",
        "cik": cik,
        "filing_date": "2026-07-10",
        "accession_number": "0001193125-26-301023",
        "source_url": "https://example.com/filing",
        "fund_keyword_match": False,
    }
    base.update(over)
    return base


class TestPipelineOverlayMerge:
    def test_overlay_attaches_by_cik(self):
        overlay = {
            "issuers": [
                {
                    "cik": 2104277,
                    "ticker": "HOLX",
                    "exchange": "NYSE",
                    "retail_brokers": ["Robinhood"],
                    "expected_pricing_window": "2026-Q3",
                    "source_url": "https://example.com/report",
                    "as_of": "2026-07-14",
                }
            ]
        }
        doc = merge_pipeline([filing()], ["2026-07-10"], overlay)
        curated = doc["filings"][0]["curated"]
        assert curated["ticker"] == "HOLX"
        assert curated["retail_brokers"] == ["Robinhood"]
        assert curated["source_url"] == "https://example.com/report"

    def test_overlay_handles_leading_zero_ciks(self):
        overlay = {"issuers": [{"cik": 2104277, "ticker": "X", "source_url": "u", "as_of": "d"}]}
        doc = merge_pipeline([filing(cik="0002104277")], [], overlay)
        assert doc["filings"][0]["curated"]["ticker"] == "X"

    def test_no_overlay_no_curated_key(self):
        doc = merge_pipeline([filing()], [], {"issuers": []})
        assert "curated" not in doc["filings"][0]

    def test_unknown_overlay_fields_are_not_leaked(self):
        overlay = {
            "issuers": [
                {"cik": 2104277, "ticker": "X", "source_url": "u", "as_of": "d", "rating": "BUY"}
            ]
        }
        doc = merge_pipeline([filing()], [], overlay)
        # schema is neutral by construction: no rating-like field survives the merge
        assert "rating" not in doc["filings"][0]["curated"]

    def test_days_covered_recorded(self):
        doc = merge_pipeline([], ["2026-07-09", "2026-07-10"], {"issuers": []})
        assert doc["source"]["days_covered"] == ["2026-07-09", "2026-07-10"]
