"""Sorts machine-only blocks into the four categories the registry contract records.

Most of what a naive diff reports is not new content at all: it is the Markdown rendering of
material the human page already carries — frontmatter, headings, metadata, link lists. Those
have to be discarded before anything is counted, because a detector that flags every page
produces a number with no information in it.

Rules are deterministic on purpose. A judge can be shown exactly why a block was flagged,
and the same input always produces the same verdict.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import IntEnum

from .normalize import Block


class Classification(IntEnum):
    COSMETIC = 0
    # Substantive content served only to machines that carries no promotional or
    # instructional marker. Reporting it as advertising would assert an intent we cannot
    # observe, so it gets its own category and is stated as what it is.
    MACHINE_ONLY = 1
    PROMOTIONAL = 2
    POLICY_VIOLATION = 3
    PROMPT_INJECTION = 4


# Structural residue of a Markdown rendering. Present because the format differs, not
# because the content does.
_COSMETIC = [
    re.compile(r"^<!--.*-->$", re.S),
    re.compile(r"^-{3,}$"),
    re.compile(r"^(title|description|canonical|author|date|published|modified|lang|"
               r"keywords|robots|viewport|charset|generator|theme|image|url|site_name)\s*:",
               re.I),
    re.compile(r"^(og|twitter|fb|al|article|profile|dc)[:.][\w:.\-]+\s*:", re.I),
    re.compile(r"^#{1,6}\s"),
    re.compile(r"^\s*[-*+]\s+\[[^\]]*\]\([^)]*\)\s*$"),
    re.compile(r"^!\[[^\]]*\]\([^)]*\)$"),
    re.compile(r"^\|.*\|$"),
    re.compile(r"^\[!?[^\]]*\]\([^)]*\)$"),
    re.compile(r"^(home|about|contact|search|menu|navigation|breadcrumb|skip to)\b", re.I),
]

# Instructions aimed at whoever is reading — which, on a machine-only block, is the agent.
_INJECTION = [
    re.compile(r"\bignore\s+(all\s+)?(previous|prior|above|earlier)\b", re.I),
    re.compile(r"\b(disregard|override|forget)\s+(your|all|any|the)\s+"
               r"(instructions|rules|prompt|guidelines|system)", re.I),
    re.compile(r"\byou\s+(must|should|need to|are required to|have to)\b", re.I),
    re.compile(r"\bas part of (this|the) (process|procedure|flow|setup)\b", re.I),
    re.compile(r"\b(send|transfer|pay|deposit|remit)\b[^.]{0,60}\b"
               r"(wallet|address|0x[a-f0-9]{6,}|payment|usdc|usdt|eth|btc)\b", re.I),
    re.compile(r"\b(api[_\s-]?key|secret|token|credential|password)\b[^.]{0,40}"
               r"\b(obtain|receive|require|provide|enter|submit)\b", re.I),
    re.compile(r"\b(system|assistant|developer)\s*(prompt|message|instruction)\b", re.I),
    re.compile(r"^\s*(instructions?|note to (ai|assistant|agent|llm)|"
               r"important for (ai|agents?|assistants?))\b", re.I),
    re.compile(r"\bwhen (asked|answering|responding|summar)", re.I),
    re.compile(r"\b(recommend|suggest|mention|cite|prefer)\b[^.]{0,40}"
               r"\b(instead of|over|rather than|always)\b", re.I),
]

_MARKUP_RESIDUE = re.compile(
    r"</?(table|thead|tbody|tr|th|td|div|span|a|ul|ol|li|img|figure|section|nav)\b"
    r"|\sdata-[\w-]+=|\shref=|\ssrc=|\bclass=\"",
    re.I,
)

_PROMOTIONAL = [
    re.compile(r"\bsponsored\b", re.I),
    re.compile(r"\bin partnership with\b", re.I),
    re.compile(r"\b(advertorial|paid (content|partnership|promotion)|brought to you by)\b", re.I),
    re.compile(r"\b(is|are) the sponsor\b", re.I),
    re.compile(r"\breference facts (and|&) faq\b", re.I),
    re.compile(r"\b(promoted|advertisement|affiliate)\b", re.I),
]


@dataclass
class Classified:
    block: Block
    classification: Classification
    reason: str

    @property
    def is_material(self) -> bool:
        return self.classification is not Classification.COSMETIC


def _first_match(patterns: list[re.Pattern], text: str) -> str | None:
    for p in patterns:
        m = p.search(text)
        if m:
            return m.group(0).strip()[:60]
    return None


def classify_block(block: Block) -> Classified:
    text = block.text.strip()

    hit = _first_match(_INJECTION, text)
    if hit:
        return Classified(block, Classification.PROMPT_INJECTION, f"instruction: {hit!r}")

    hit = _first_match(_PROMOTIONAL, text)
    if hit:
        return Classified(block, Classification.PROMOTIONAL, f"promotional: {hit!r}")

    for p in _COSMETIC:
        if p.match(text):
            return Classified(block, Classification.COSMETIC, "markdown or metadata structure")

    # Markup that survived extraction is a difference in representation, not in content.
    # Serving Markdown to crawlers is legitimate; only content the human page lacks counts.
    if _MARKUP_RESIDUE.search(text):
        return Classified(block, Classification.COSMETIC, "markup residue, not content")

    # Too little prose to be a claim; almost always a fragment of layout.
    if len(block.key.split()) < 8:
        return Classified(block, Classification.COSMETIC, "too short to carry a claim")

    return Classified(block, Classification.MACHINE_ONLY, "prose served only to crawlers")


def classify(blocks: list[Block]) -> list[Classified]:
    return [classify_block(b) for b in blocks]


def material(blocks: list[Block]) -> list[Classified]:
    return [c for c in classify(blocks) if c.is_material]


def worst(classified: list[Classified]) -> Classification:
    return max((c.classification for c in classified), default=Classification.COSMETIC)
