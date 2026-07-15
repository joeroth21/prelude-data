"""Editorial lint — hard rules every Brief draft must pass, twice
(after drafting, and again at publish, because a human edited in between).

Rules:
  * no investment advice / recommendation language (same class of list the
    PRELUDE app enforces at build time)
  * no verbatim passages from source material: any 10+ word contiguous
    overlap with gathered source text fails (drafts are original synthesis)
  * at most one quotation per piece, under 15 words, and it must be marked
    with quotation marks
  * body between 150 and 300 words; headline non-empty
  * at least 2 sources, all http(s) URLs
"""

from __future__ import annotations

import re

# Assembled from fragments so this file never trips a naive text scan.
def _j(*parts: str) -> str:
    return "".join(parts)
FORBIDDEN_PHRASES: list[str] = [
    _j("you should ", "buy"),
    _j("you should ", "sell"),
    _j("you should ", "invest"),
    _j("we ", "recommend"),
    _j("best ", "investment"),
    _j("top ", "pick"),
    _j("strong ", "buy"),
    _j("buy ", "rating"),
    _j("sell ", "rating"),
    _j("buy ", "now"),
    _j("must ", "buy"),
    _j("must-", "buy"),
    _j("don't ", "miss"),
    _j("dont ", "miss"),
    _j("can't ", "lose"),
    _j("guaranteed ", "return"),
    _j("get in ", "before"),
    _j("time to ", "buy"),
    _j("worth ", "buying"),
    _j("under", "valued gem"),
    _j("screaming ", "buy"),
    _j("our ", "rating"),
    _j("investment ", "score"),
    _j("moon", "shot opportunity"),
    # advice-adjacent framing (caught in the wild from model drafts)
    _j("investors ", "should"),
    _j("investment ", "decision"),
    _j("entry ", "point"),
    _j("attractive", ""),
    _j("under", "valu"),
    _j("over", "valu"),
    _j("consider ", "buying"),
    _j("consider ", "investing"),
    _j("opportunity ", "to invest"),
    _j("upside ", "potential"),
]

VERBATIM_WINDOW = 10  # words
QUOTE_MAX_WORDS = 15
BODY_MIN_WORDS = 150
BODY_MAX_WORDS = 300
MIN_SOURCES = 2


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9\s]", " ", text.lower()).split()


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def check_forbidden_language(text: str) -> list[str]:
    lowered = text.lower()
    return [
        f"forbidden phrase present: '{phrase}'"
        for phrase in FORBIDDEN_PHRASES
        if phrase in lowered
    ]


def check_verbatim(text: str, source_texts: list[str]) -> list[str]:
    """Fail on any contiguous VERBATIM_WINDOW-word run copied from a source."""
    draft_grams = _ngrams(_words(text), VERBATIM_WINDOW)
    if not draft_grams:
        return []
    errors = []
    for i, source in enumerate(source_texts):
        overlap = draft_grams & _ngrams(_words(source), VERBATIM_WINDOW)
        if overlap:
            sample = " ".join(next(iter(overlap)))
            errors.append(
                f"verbatim {VERBATIM_WINDOW}+ word passage shared with source #{i + 1}: \"{sample[:80]}...\""
            )
    return errors


def check_quotes(text: str) -> list[str]:
    """At most one quoted span, under QUOTE_MAX_WORDS words."""
    spans = re.findall(r'[""]([^""]+)[""]|"([^"]{2,})"', text)
    quotes = [a or b for a, b in spans]
    errors = []
    if len(quotes) > 1:
        errors.append(f"{len(quotes)} quotations found — maximum is one per piece")
    for q in quotes:
        n = len(q.split())
        if n >= QUOTE_MAX_WORDS:
            errors.append(f"quotation of {n} words exceeds the {QUOTE_MAX_WORDS}-word limit")
    return errors


_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _digit_tokens(text: str) -> set[str]:
    """Significant numeric tokens: strip commas; skip day-of-month ints and years."""
    out = set()
    for tok in _NUMBER_RE.findall(text):
        plain = tok.replace(",", "")
        if "." not in plain:
            n = int(plain)
            if n <= 31:  # dates, ordinals, small counts
                continue
            if 1900 <= n <= 2100:  # years
                continue
        out.add(plain)
    return out


def _scaled_variants(token: str) -> set[str]:
    """Unit rescalings a writer may legitimately use ($1,767B -> $1.77T)."""
    try:
        n = float(token)
    except ValueError:
        return set()
    out = set()
    for factor in (1000.0, 1_000_000.0, 1_000_000_000.0):
        for scaled in (n / factor, n * factor):
            for nd in (0, 1, 2, 3):
                r = round(scaled, nd)
                if r == int(r):
                    out.add(str(int(r)))
                else:
                    out.add(str(r))
    return out


def check_numeric_grounding(text: str, grounding_texts: list[str]) -> list[str]:
    """Every significant number in the draft must appear in the material.

    Guards against invented or garbled figures. Skipped when no grounding
    texts are provided (post-review publish lint — the human owns edits).
    """
    if not grounding_texts:
        return []
    ground = set()
    for g in grounding_texts:
        ground |= _digit_tokens(g)
    for token in list(ground):
        ground |= _scaled_variants(token)
    ungrounded = sorted(_digit_tokens(text) - ground)
    if ungrounded:
        return [f"numbers not present in any source material: {', '.join(ungrounded[:8])}"]
    return []


def check_shape(headline: str, body: str, sources: list[str]) -> list[str]:
    errors = []
    if not headline.strip():
        errors.append("empty headline")
    n = len(body.split())
    if n < BODY_MIN_WORDS:
        errors.append(f"body is {n} words (min {BODY_MIN_WORDS})")
    if n > BODY_MAX_WORDS:
        errors.append(f"body is {n} words (max {BODY_MAX_WORDS})")
    if len(sources) < MIN_SOURCES:
        errors.append(f"only {len(sources)} source(s) — minimum {MIN_SOURCES}")
    for s in sources:
        if not s.startswith("http"):
            errors.append(f"source is not a URL: {s!r}")
    return errors


def lint_draft(
    headline: str,
    body: str,
    why_it_matters: str,
    sources: list[str],
    source_texts: list[str],
    grounding_texts: list[str] | None = None,
) -> list[str]:
    """All lint failures for one draft ([] = clean).

    source_texts: external material — the verbatim (no-copying) corpus.
    grounding_texts: where numbers may legitimately come from (defaults to
    source_texts; the drafting stage adds the feed's own topic data, whose
    phrasing is ours to reuse but whose figures still count as grounded).
    """
    full_text = f"{headline}\n{body}\n{why_it_matters}"
    errors: list[str] = []
    errors.extend(check_forbidden_language(full_text))
    errors.extend(check_verbatim(full_text, source_texts))
    errors.extend(
        check_numeric_grounding(
            full_text, grounding_texts if grounding_texts is not None else source_texts
        )
    )
    errors.extend(check_quotes(full_text))
    errors.extend(check_shape(headline, body, sources))
    if not why_it_matters.strip():
        errors.append("missing 'why it matters' closing line")
    return errors
