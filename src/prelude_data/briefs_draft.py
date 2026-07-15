"""Drafting stage — local Ollama (llama3.1:8b), zero external API.

Each topic's gathered material becomes one draft: headline, 150-300 word
body, a "why it matters" closer, and the sources list. Drafts that fail the
lint are regenerated (up to 3 attempts) with the failures fed back into the
prompt. Output lands as editable markdown with `reviewed: false` — nothing
publishes without a human flipping that flag.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path

import requests

from . import briefs_lint
from .briefs_gather import DRAFTS_ROOT

log = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.1:8b"
MAX_ATTEMPTS = 6

SYSTEM_STYLE = """You are the writer of The Brief, the news digest of PRELUDE, a
private-markets data product. Style: a sharp, factual markets newsletter.
Precise numbers, dry wit allowed, zero hype, zero advice.

ABSOLUTE RULES:
- Educational information only. NEVER recommend, rate, or suggest any action
  on any security. No "should", no "opportunity", no "attractive".
- Every factual claim must be supported by the provided source material.
  Do not invent numbers, dates, or quotes.
- Write original prose. NEVER reuse 7 or more consecutive words from the
  source material — restate every fact in fresh wording. At most ONE short
  quotation (under 15 words) in quotation marks.
- Every number you write MUST appear in the topic data or source material
  exactly. Never invent, estimate, combine, or garble figures.
- The body MUST be three substantial paragraphs of 60-90 words each
  (180-270 words total). Short bodies are rejected.
- After the body, one closing line beginning "Why it matters:" aimed at
  retail private-market watchers — factual context, not a nudge."""


def build_prompt(topic: dict, materials: list[dict], feedback: list[str] | None) -> str:
    material_block = "\n\n".join(
        f"SOURCE {i + 1} ({m['url']}):\n{m['text']}" for i, m in enumerate(materials)
    )
    topic_block = json.dumps(
        {k: v for k, v in topic.items() if k not in ("seed_urls",)}, indent=1, default=str
    )[:2000]
    feedback_block = (
        "\n\nYOUR PREVIOUS DRAFT FAILED THESE CHECKS — fix them:\n- " + "\n- ".join(feedback)
        if feedback
        else ""
    )
    return f"""{SYSTEM_STYLE}

TOPIC DATA (from the PRELUDE feed):
{topic_block}

SOURCE MATERIAL:
{material_block}
{feedback_block}

Respond with ONLY a JSON object, no other text:
{{"headline": "...",
 "paragraphs": ["first paragraph, 60-90 words", "second paragraph, 60-90 words", "third paragraph, 60-90 words"],
 "why_it_matters": "Why it matters: ..."}}"""


EXPAND_PROMPT = """{style}

Below is a draft that is TOO SHORT. Expand EACH paragraph to 70-95 words by
adding depth from the source material — more precise figures, dates, and
context. Do not add new claims that the sources do not support. Do not copy
7+ consecutive words from the sources. Keep the same headline and closer.

SOURCE MATERIAL:
{materials}

DRAFT TO EXPAND:
{draft}

Respond with ONLY the same JSON shape:
{{"headline": "...", "paragraphs": ["...", "...", "..."], "why_it_matters": "..."}}"""


def ollama_generate(prompt: str, timeout: int = 300) -> str:
    resp = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.55, "num_ctx": 8192, "num_predict": 1400},
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["response"]


def _first_balanced_object(raw: str) -> dict | None:
    """Extract the first balanced {...} object from noisy output."""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def parse_response(raw: str) -> dict | None:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = _first_balanced_object(raw)
        if obj is None:
            log.warning("unparseable model output: %.200s", raw)
            return None
    if isinstance(obj.get("paragraphs"), list) and all(
        isinstance(p, str) for p in obj["paragraphs"]
    ):
        obj["body"] = "\n\n".join(p.strip() for p in obj["paragraphs"] if p.strip())
    if not all(isinstance(obj.get(k), str) for k in ("headline", "body", "why_it_matters")):
        return None
    return obj


def draft_topic(
    topic: dict,
    materials: list[dict],
    generate=ollama_generate,
) -> tuple[dict | None, list[str]]:
    """Draft one topic; returns (draft, lint_errors_of_last_attempt)."""
    sources = [m["url"] for m in materials]
    source_texts = [m["text"] for m in materials]
    # Topic data grounds figures (the feed computed them) but is NOT part of
    # the verbatim corpus — its phrasing is ours.
    grounding_texts = source_texts + [json.dumps(topic, default=str)]
    feedback: list[str] | None = None
    errors: list[str] = ["no attempt made"]

    def lint(parsed: dict) -> list[str]:
        return briefs_lint.lint_draft(
            parsed["headline"],
            parsed["body"],
            parsed["why_it_matters"],
            sources,
            source_texts,
            grounding_texts,
        )

    material_block = "\n\n".join(f"({m['url']}):\n{m['text']}" for m in materials)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = generate(build_prompt(topic, materials, feedback))
        parsed = parse_response(raw)
        if parsed is None:
            # Transport-level noise, not an editorial problem: retry without
            # polluting the feedback the model sees.
            errors = ["model did not return valid JSON"]
            continue
        errors = lint(parsed)

        # Length-only failure: run a targeted expansion pass on the same draft.
        if errors and all("min 150" in e for e in errors):
            expanded_raw = generate(
                EXPAND_PROMPT.format(
                    style=SYSTEM_STYLE,
                    materials=material_block,
                    draft=json.dumps(
                        {k: parsed[k] for k in ("headline", "paragraphs", "why_it_matters") if k in parsed}
                    ),
                )
            )
            expanded = parse_response(expanded_raw)
            if expanded is not None:
                expanded_errors = lint(expanded)
                if len(expanded_errors) <= len(errors):
                    parsed, errors = expanded, expanded_errors

        if not errors:
            return {**parsed, "sources": sources, "topic_id": topic["id"], "kind": topic["kind"]}, []
        log.info("draft %s attempt %d failed lint: %s", topic["id"], attempt, errors)
        feedback = errors
    return None, errors


# ---------------------------------------------------------------------------
# Markdown round-trip (drafts are edited by a human)
# ---------------------------------------------------------------------------

def draft_to_markdown(draft: dict, date: str) -> str:
    sources_yaml = "\n".join(f"  - {u}" for u in draft["sources"])
    tickers = sorted(set(re.findall(r"\b(SPCX|CBRS|DXYZ|XOVR|SSSS|ARKVX)\b", draft["body"])))
    tickers_yaml = "[" + ", ".join(tickers) + "]"
    title = draft["headline"].replace('"', "'")
    return (
        "---\n"
        f"id: {date}-{draft['topic_id']}\n"
        f"date: {date}\n"
        f'title: "{title}"\n'
        f"kind: {draft['kind']}\n"
        f"tickers: {tickers_yaml}\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "reviewed: false\n"
        "---\n"
        "\n"
        f"{draft['body']}\n"
        "\n"
        f"{draft['why_it_matters']}\n"
    )


def parse_draft_markdown(text: str) -> dict:
    """Parse frontmatter + body. Raises ValueError on malformed drafts."""
    m = re.match(r"^---\n([\s\S]+?)\n---\n([\s\S]*)$", text)
    if not m:
        raise ValueError("draft has no frontmatter block")
    import yaml

    meta = yaml.safe_load(m.group(1))
    body_full = m.group(2).strip()
    # last paragraph starting "Why it matters" is the closer
    parts = [p.strip() for p in body_full.split("\n\n") if p.strip()]
    why = ""
    if parts and parts[-1].lower().startswith("why it matters"):
        why = parts.pop()
    for field in ("id", "date", "title", "sources", "reviewed"):
        if field not in (meta or {}):
            raise ValueError(f"draft frontmatter missing '{field}'")
    return {
        "id": str(meta["id"]),
        "date": str(meta["date"]),
        "title": str(meta["title"]),
        "kind": str(meta.get("kind", "note")),
        "tickers": list(meta.get("tickers") or []),
        "sources": [str(s) for s in meta["sources"]],
        "reviewed": bool(meta["reviewed"]),
        "body": "\n\n".join(parts),
        "why_it_matters": why,
    }


def write_drafts(drafts: list[dict], date: str | None = None) -> Path:
    date = date or dt.date.today().isoformat()
    out_dir = DRAFTS_ROOT / date
    out_dir.mkdir(parents=True, exist_ok=True)
    for draft in drafts:
        path = out_dir / f"{draft['topic_id']}.md"
        path.write_text(draft_to_markdown(draft, date), encoding="utf-8", newline="\n")
        log.info("draft written: %s", path)
    return out_dir
