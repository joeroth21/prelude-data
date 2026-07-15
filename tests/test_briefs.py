import datetime as dt
from pathlib import Path

import pytest

from prelude_data import validate
from prelude_data.briefs_draft import draft_to_markdown, parse_draft_markdown
from prelude_data.briefs_lint import (
    check_forbidden_language,
    check_quotes,
    check_verbatim,
    lint_draft,
)
from prelude_data.briefs_publish import ReviewGateError, gate, load_cycle

NOW = dt.datetime(2026, 7, 14, 12, 0, tzinfo=dt.timezone.utc)

GOOD_BODY = " ".join(["Factual sentence number {}.".format(i) for i in range(40)])  # ~160 words
SOURCES = ["https://example.com/a", "https://example.com/b"]


def j(*parts: str) -> str:
    return "".join(parts)


class TestForbiddenLanguage:
    def test_clean_text_passes(self):
        assert check_forbidden_language("SPCX closed at $136.08, 0.8% above its offer price.") == []

    def test_advice_language_fails(self):
        assert check_forbidden_language(j("Investors, you should ", "buy this dip.")) != []
        assert check_forbidden_language(j("We ", "recommend the annual plan of shares.")) != []
        assert check_forbidden_language(j("A strong ", "buy if ever there was one.")) != []

    def test_case_insensitive(self):
        assert check_forbidden_language(j("TIME TO ", "BUY").title()) != []


class TestVerbatim:
    SOURCE = (
        "The initial public offering price is one hundred and thirty five dollars "
        "per share of Class A common stock as set forth on the cover page"
    )

    def test_copied_passage_fails(self):
        draft = "As the prospectus put it, the initial public offering price is one hundred and thirty five dollars per share of Class A stock."
        assert check_verbatim(draft, [self.SOURCE]) != []

    def test_original_synthesis_passes(self):
        draft = "SpaceX priced its offering at $135.00, valuing the launch company near $1.8 trillion at listing."
        assert check_verbatim(draft, [self.SOURCE]) == []

    def test_nine_word_overlap_is_allowed(self):
        # windows below the threshold are fine (shared phrases happen)
        draft = "price is one hundred and thirty five dollars per"  # 9 words
        assert check_verbatim(draft, [self.SOURCE]) == []


class TestQuotes:
    def test_one_short_quote_ok(self):
        assert check_quotes('The filing calls it "a defining moment" for the company.') == []

    def test_two_quotes_fail(self):
        text = 'One "short quote" and another "second quote" as well.'
        assert check_quotes(text) != []

    def test_long_quote_fails(self):
        long_quote = '"' + " ".join(["word"] * 16) + '"'
        assert check_quotes(f"The prospectus says {long_quote}.") != []


class TestNumericGrounding:
    from prelude_data.briefs_lint import check_numeric_grounding as _check

    SOURCE = ["NAV per share was $14.24 as of March 31, 2026; price 10.91; discount -23.38%."]

    def test_grounded_numbers_pass(self):
        from prelude_data.briefs_lint import check_numeric_grounding

        draft = "At $10.91 against a stated $14.24 NAV, the discount is 23.38%."
        assert check_numeric_grounding(draft, self.SOURCE) == []

    def test_invented_number_fails(self):
        from prelude_data.briefs_lint import check_numeric_grounding

        draft = "Revenue reached $510 million while NAV stood at $14.24."
        errors = check_numeric_grounding(draft, self.SOURCE)
        assert errors and "510" in errors[0]

    def test_years_and_dates_are_exempt(self):
        from prelude_data.briefs_lint import check_numeric_grounding

        draft = "On March 31, 2026 the figure was $14.24; by July 14 it had not been restated."
        assert check_numeric_grounding(draft, self.SOURCE) == []

    def test_no_grounding_texts_skips_check(self):
        from prelude_data.briefs_lint import check_numeric_grounding

        assert check_numeric_grounding("Any $999.99 at all.", []) == []


class TestAdviceAdjacentLanguage:
    def test_wild_caught_phrases_fail(self):
        # Phrases that slipped past the first list in real model output.
        for text in [
            j("significant discounts can provide attractive", " entry points"),
            j("investors ", "should carefully evaluate the stock"),
            j("the market may be under", "valuing these assets"),
            j("when making investment ", "decisions"),
        ]:
            assert check_forbidden_language(text) != [], text


class TestLintDraft:
    def test_good_draft_passes(self):
        errors = lint_draft("A headline", GOOD_BODY, "Why it matters: context.", SOURCES, [])
        assert errors == []

    def test_short_body_fails(self):
        errors = lint_draft("H", "too short", "Why it matters: x.", SOURCES, [])
        assert any("min 150" in e for e in errors)

    def test_single_source_fails(self):
        errors = lint_draft("H", GOOD_BODY, "Why it matters: x.", ["https://one.example"], [])
        assert any("minimum 2" in e for e in errors)

    def test_missing_closer_fails(self):
        errors = lint_draft("H", GOOD_BODY, "   ", SOURCES, [])
        assert any("why it matters" in e.lower() for e in errors)


def make_draft_file(tmp_path: Path, name: str, reviewed: bool, body: str = GOOD_BODY) -> Path:
    draft = {
        "topic_id": name,
        "kind": "status_change",
        "headline": f"Headline {name}",
        "body": body,
        "why_it_matters": "Why it matters: factual context for watchers.",
        "sources": SOURCES,
    }
    md = draft_to_markdown(draft, "2026-07-14")
    if reviewed:
        md = md.replace("reviewed: false", "reviewed: true")
    path = tmp_path / f"{name}.md"
    path.write_text(md, encoding="utf-8")
    return path


class TestReviewGate:
    def test_unreviewed_draft_refuses_publish(self, tmp_path):
        make_draft_file(tmp_path, "one", reviewed=True)
        make_draft_file(tmp_path, "two", reviewed=False)
        drafts = load_cycle(tmp_path)
        with pytest.raises(ReviewGateError, match="two.md"):
            gate(drafts)

    def test_all_reviewed_passes_gate(self, tmp_path):
        make_draft_file(tmp_path, "one", reviewed=True)
        gate(load_cycle(tmp_path))  # no raise

    def test_markdown_round_trip(self, tmp_path):
        path = make_draft_file(tmp_path, "one", reviewed=False)
        parsed = parse_draft_markdown(path.read_text(encoding="utf-8"))
        assert parsed["id"] == "2026-07-14-one"
        assert parsed["reviewed"] is False
        assert parsed["sources"] == SOURCES
        assert parsed["why_it_matters"].startswith("Why it matters:")
        assert len(parsed["body"].split()) >= 150

    def test_malformed_draft_raises(self, tmp_path):
        (tmp_path / "bad.md").write_text("no frontmatter here", encoding="utf-8")
        with pytest.raises(ValueError, match="frontmatter"):
            load_cycle(tmp_path)


class TestBriefsSchema:
    def brief(self, **over):
        base = {
            "id": "2026-07-14-status-spacex",
            "date": "2026-07-14",
            "title": "SpaceX trades",
            "kind": "status_change",
            "tickers": ["SPCX"],
            "body": GOOD_BODY,
            "why_it_matters": "Why it matters: context.",
            "sources": SOURCES,
        }
        base.update(over)
        return base

    def test_valid_doc_passes(self):
        doc = {"briefs": [self.brief()]}
        assert validate.validate_briefs(doc, NOW) == []

    def test_empty_briefs_is_valid(self):
        assert validate.validate_briefs({"briefs": []}, NOW) == []

    def test_missing_fields_fail(self):
        doc = {"briefs": [self.brief(title="")]}
        assert any("missing title" in e for e in validate.validate_briefs(doc, NOW))

    def test_single_source_fails(self):
        doc = {"briefs": [self.brief(sources=["https://one.example"])]}
        assert any("fewer than 2" in e for e in validate.validate_briefs(doc, NOW))

    def test_duplicate_ids_fail(self):
        doc = {"briefs": [self.brief(), self.brief()]}
        assert any("duplicate id" in e for e in validate.validate_briefs(doc, NOW))

    def test_forbidden_language_in_published_brief_fails(self):
        doc = {"briefs": [self.brief(body=GOOD_BODY + j(" We ", "recommend it."))]}
        assert any("forbidden" in e for e in validate.validate_briefs(doc, NOW))
