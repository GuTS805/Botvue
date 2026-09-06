"""Reduces a response to comparable text blocks.

Separating material divergence from cosmetic divergence is the whole problem: every page's
Markdown rendering differs from its HTML, so a naive diff marks everything divergent and the
signal is zero. Comparison keys are aggressively normalised, while the original text is kept
alongside so a finding can still be shown to a human.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

import trafilatura
from bs4 import BeautifulSoup

MIN_BLOCK_CHARS = 25

_MD_SYNTAX = re.compile(r"^[#>\-\*\+\s]+|[*_`]+")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")
_BLOCK_SPLIT = re.compile(r"\n\s*\n|\r\n\s*\r\n")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

# Present on nearly every page and identical across user-agents; keeping them would drown
# the machine-only blocks we are actually looking for.
_BOILERPLATE = re.compile(
    r"^(cookie|accept all|subscribe|sign in|log in|menu|skip to|share this|"
    r"follow us|all rights reserved|privacy policy|terms of|advertisement)\b",
    re.I,
)


@dataclass(frozen=True)
class Block:
    text: str
    key: str

    def __hash__(self) -> int:
        return hash(self.key)


def extract_text(body: str, content_type: str = "") -> str:
    """HTML goes through boilerplate stripping; Markdown and plain text are already text."""
    if not body.strip():
        return ""

    looks_markup = "<html" in body[:2000].lower() or "<body" in body[:2000].lower()
    is_text_type = any(t in content_type.lower() for t in ("markdown", "text/plain"))

    if is_text_type and not looks_markup:
        return body

    extracted = trafilatura.extract(
        body,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
    )
    if extracted:
        return extracted

    soup = BeautifulSoup(body, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
    return soup.get_text("\n")


def normalise_key(text: str) -> str:
    """Comparison key: case, punctuation, Markdown syntax and whitespace all removed, so the
    same sentence rendered as HTML and as Markdown collapses to one key."""
    text = unicodedata.normalize("NFKC", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_SYNTAX.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip().lower()


def to_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    seen: set[str] = set()

    for raw in _BLOCK_SPLIT.split(text):
        raw = raw.strip()
        if not raw:
            continue
        for piece in _SENTENCE_SPLIT.split(raw) if len(raw) > 400 else [raw]:
            piece = _WS.sub(" ", piece).strip()
            if len(piece) < MIN_BLOCK_CHARS or _BOILERPLATE.match(piece):
                continue
            key = normalise_key(piece)
            if len(key) < MIN_BLOCK_CHARS or key in seen:
                continue
            seen.add(key)
            blocks.append(Block(text=piece, key=key))

    return blocks


def blocks_from_response(body: str, content_type: str = "") -> list[Block]:
    return to_blocks(extract_text(body, content_type))


def shingles(normalised: str, width: int = 5) -> set[str]:
    """Overlapping word runs. Two renderings of the same page share most of their runs even
    when tag structure, ordering and extraction quality differ; genuinely different content
    does not."""
    words = normalised.split()
    if len(words) < width:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + width]) for i in range(len(words) - width + 1)}


def text_similarity(a: str, b: str) -> float:
    """Containment of the smaller text in the larger.

    Not Jaccard: a Markdown variant is legitimately a fraction of the size of the HTML page,
    and Jaccard would score that as divergent purely on length. What matters is whether the
    smaller text is *made of* the larger one — content the other side does not have at all is
    what this is trying to surface.
    """
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0 if (sa or sb) else 1.0
    return len(sa & sb) / min(len(sa), len(sb))


def haystack(body: str) -> str:
    """Every scrap of visible text in the response, as one normalised string.

    Article extraction is tuned to drop navigation and furniture, and it drops different
    amounts from HTML than from Markdown. That asymmetry alone makes real article text look
    machine-only. Checking a candidate block against the raw text of the human page — not
    against its extracted article — is what removes those false positives.
    """
    if not body.strip():
        return ""
    soup = BeautifulSoup(body, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return normalise_key(soup.get_text(" "))
