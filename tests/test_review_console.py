"""Review-console helpers — the pieces that guard the editorial gate."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import briefs_review as console  # noqa: E402


GOOD = (
    "---\n"
    "id: 2026-07-16-test\n"
    "date: 2026-07-16\n"
    'title: "A Test Piece"\n'
    "kind: status_change\n"
    "tickers: [SPCX]\n"
    "sources:\n"
    "  - https://a.example\n"
    "  - https://b.example\n"
    "reviewed: false\n"
    "---\n\n"
    + " ".join(f"Sentence number {i} is factual." for i in range(40))
    + "\n\nWhy it matters: context.\n"
)


class TestLintMarkdown:
    def test_clean_draft(self):
        parsed, errors = console.lint_markdown(GOOD)
        assert errors == []
        assert parsed["title"] == "A Test Piece"
        assert parsed["reviewed"] is False

    def test_forbidden_language_caught_live(self):
        bad = GOOD.replace("Sentence number 1 is factual.", "We recommend this to everyone.")
        _, errors = console.lint_markdown(bad)
        assert any("forbidden" in e for e in errors)

    def test_unparseable_markdown_reports_not_raises(self):
        parsed, errors = console.lint_markdown("no frontmatter at all")
        assert parsed is None
        assert any("unparseable" in e for e in errors)


class TestSetReviewed:
    def test_flips_only_the_flag(self, tmp_path):
        p = tmp_path / "draft.md"
        p.write_text(GOOD, encoding="utf-8")
        console.set_reviewed(p, True)
        text = p.read_text(encoding="utf-8")
        assert "reviewed: true" in text
        assert text.count("reviewed:") == 1
        console.set_reviewed(p, False)
        assert "reviewed: false" in p.read_text(encoding="utf-8")


class TestCycleDiscovery:
    def test_latest_cycle_dir_is_newest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(console, "DRAFTS_ROOT", tmp_path)
        (tmp_path / "2026-07-10").mkdir()
        (tmp_path / "2026-07-14").mkdir()
        assert console.latest_cycle_dir().name == "2026-07-14"

    def test_no_cycles_is_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(console, "DRAFTS_ROOT", tmp_path)
        assert console.latest_cycle_dir() is None
