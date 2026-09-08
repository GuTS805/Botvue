"""Turns cached responses into verdicts.

Three behaviours, one shape — what the agent read is not what a person gets:

  refused      the crawler is turned away with a 4xx. Honest.
  soft-blocked the crawler gets HTTP 200 and an empty page. The agent believes it read the
               article. Neither the agent nor the reader ever learns otherwise.
  substituted  the crawler's version contains content the human version does not.

The soft-block test is deliberately the cheapest one here: it is a ratio of extracted words
between two responses and needs no text classification at all, so it sidesteps the whole
markdown-versus-HTML noise problem that makes substitution hard to measure.

Serving Markdown to crawlers is *not* substitution. Substitution means content the human
version does not have, not a different representation of the same content.

    python -m scanner.grade [--json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .agents import AGENTS, BASELINE, CONTROL
from .classify import Classification, classify
from .corpus import CORPUS_DIR
from .diff import compare
from .normalize import blocks_from_response, haystack, text_similarity

CACHE_DIR = CORPUS_DIR / "cache"
RESULTS_DIR = CORPUS_DIR / "results"

# A page must carry some text before any comparison means anything.
MIN_BASELINE_WORDS = 200
# A response holding this fraction of the human page's words is a stub, not the article.
STUB_WORD_RATIO = 0.15
# ...unless it is long enough to be a real short page regardless of the ratio.
STUB_ABSOLUTE_WORDS = 80
# The control must look like the human page, or the comparison says nothing about the
# crawlers. News pages rotate headlines between requests, so this is not 1.0.
CONTROL_SAME_PAGE = 0.93
# Below this containment, the crawler is not being served the human page's content.
CRAWLER_DIVERGENT = 0.80
# Below this many material blocks, divergence is indistinguishable from rotating content.
MIN_MATERIAL_BLOCKS = 3

AI_AGENTS = [a for a in AGENTS if a not in (BASELINE, CONTROL)]
UNJUDGEABLE = ("no-data", "baseline-failed", "thin", "incomplete")


# The longest injected sentence measured in the wild runs to about 180 characters, so a
# hard 220-character cut usually survives one. Usually is not good enough for evidence:
# a sentence truncated mid-clause reads as if the tool could not quote it, which is the
# opposite of what an excerpt is for. Cut at the last sentence end instead, and only fall
# back to a hard cut when the text carries no sentence end at all.
EXCERPT_CHARS = 260
EXCERPT_MIN = 80


def excerpt(text: str) -> str:
    """A quotable piece of a block: whole sentences where the text has them."""
    text = " ".join(text.split())
    if len(text) <= EXCERPT_CHARS:
        return text
    window = text[:EXCERPT_CHARS]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if cut >= EXCERPT_MIN:
        return window[: cut + 1]
    return window.rstrip() + "…"


@dataclass
class Verdict:
    domain: str
    url: str = ""
    verdict: str = "clean"
    reason: str = ""
    baseline_words: int = 0
    statuses: dict = field(default_factory=dict)
    sizes: dict = field(default_factory=dict)
    word_ratio: dict = field(default_factory=dict)
    similarity: dict = field(default_factory=dict)
    material: dict = field(default_factory=dict)
    worst: str = "COSMETIC"
    differential_headers: dict = field(default_factory=dict)
    refused: list = field(default_factory=list)
    soft_blocked: list = field(default_factory=list)
    substituted: list = field(default_factory=list)
    samples: list = field(default_factory=list)

    @property
    def is_finding(self) -> bool:
        return self.verdict in ("soft-blocked", "substituted", "refused")

    @property
    def control_clean(self) -> bool:
        sim = self.similarity.get(CONTROL)
        return sim is not None and sim >= CONTROL_SAME_PAGE


def _load(domain: str, url: str | None = None) -> dict:
    """A domain directory can hold several pages once articles are scanned as well as
    homepages; mixing them would compare one page against another."""
    out = {}
    # Responses are cached under the URL's own hostname, which is not always the corpus
    # domain: an article at www.example.com belongs to the entry for example.com.
    host = urlparse(url).hostname if url else None
    d = CACHE_DIR / (host or domain)
    if not d.is_dir():
        d = CACHE_DIR / domain
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("variant") != "default":
            continue
        if url and rec.get("url") != url:
            continue
        out[rec["agent"]] = rec
    return out


def grade(domain: str, url: str | None = None) -> Verdict:
    by_agent = _load(domain, url)
    v = Verdict(domain=domain)
    if not by_agent:
        v.verdict, v.reason = "no-data", "nothing cached"
        return v

    for a, r in by_agent.items():
        v.statuses[a] = r["status"] if not r.get("error") else "error"
        v.sizes[a] = len(r.get("body") or "")
    v.url = next(iter(by_agent.values())).get("url", "")

    if len(by_agent) < len(AGENTS):
        v.verdict, v.reason = "incomplete", f"only {len(by_agent)}/{len(AGENTS)} agents cached"
        return v

    base = by_agent.get(BASELINE)
    if not base or base.get("error") or not (200 <= base["status"] < 300):
        v.verdict, v.reason = "baseline-failed", "browser fetch did not succeed"
        return v

    base_hay = haystack(base["body"])
    base_words = len(base_hay.split())
    v.baseline_words = base_words
    if base_words < MIN_BASELINE_WORDS:
        v.verdict = "thin"
        v.reason = f"baseline carries only {base_words} words; not judgeable"
        return v

    baseline_headers = {k.lower() for k in (base.get("headers") or {})}
    marker_headers: dict = {}
    base_blocks = blocks_from_response(base["body"], base["headers"].get("content-type", ""))
    worst = Classification.COSMETIC
    stub_samples: list[dict] = []
    subst_samples: list[dict] = []

    for a, r in by_agent.items():
        if a == BASELINE or r.get("error"):
            continue

        for k, val in (r.get("headers") or {}).items():
            if k.lower().startswith(("x-mobian", "x-agent", "x-ai-", "x-llm")):
                marker_headers.setdefault(k.lower(), {})[a] = val[:60]

        if r["status"] >= 400 or r["status"] == 402:
            v.refused.append(f"{a}:{r['status']}")
            continue
        if not (200 <= r["status"] < 300):
            continue

        hay = haystack(r["body"])
        words = len(hay.split())
        v.word_ratio[a] = round(words / base_words, 3)
        v.similarity[a] = round(text_similarity(base_hay, hay), 3)

        # Signal 1, and the cheapest: a 200 that carries almost none of the page.
        if words < STUB_ABSOLUTE_WORDS and v.word_ratio[a] < STUB_WORD_RATIO:
            v.soft_blocked.append(a)
            # `hay` is folded for comparison — lowercased, punctuation dropped. Quoting
            # that would misrepresent what the server sent, so the excerpt comes from the
            # extracted blocks instead. A stub has very few, so this parse is cheap.
            if a != CONTROL:
                stub = " ".join(
                    b.text for b in
                    blocks_from_response(r["body"], r["headers"].get("content-type", ""))
                ).strip()
                if stub:
                    text = excerpt(stub)
                    # Several crawlers usually receive the identical stub. Repeating it
                    # once per agent pads the evidence without adding any; name them all
                    # against the one quote instead.
                    same = next((x for x in stub_samples if x["text"] == text), None)
                    if same:
                        same["agent"] += ", " + a
                    else:
                        stub_samples.append({"agent": a, "class": "STUB", "text": text})
            continue

        blocks = blocks_from_response(r["body"], r["headers"].get("content-type", ""))
        d = compare(base_blocks, blocks, base_hay, hay)
        mat = [c for c in classify(d.machine_only) if c.is_material]
        v.material[a] = len(mat)
        for c in mat:
            worst = max(worst, c.classification)
        if a != CONTROL and len(mat) >= MIN_MATERIAL_BLOCKS:
            v.substituted.append(a)
            if not subst_samples:
                subst_samples = [
                    {"agent": a, "class": c.classification.name, "text": excerpt(c.block.text)}
                    for c in sorted(mat, key=lambda c: -c.classification)[:4]
                ]
    v.worst = worst.name

    # A marker means nothing if the browser receives it too — that is disclosure, not
    # substitution.
    v.differential_headers = {
        k: agents for k, agents in marker_headers.items()
        if k not in baseline_headers and CONTROL not in agents
    }

    ai_soft = [a for a in v.soft_blocked if a != CONTROL]
    ai_subst = [a for a in v.substituted if a != CONTROL]

    # Unmatched text alone is not enough to call something substitution. Measured against
    # hand-checked samples it is right about 58% of the time — navigation rails and Markdown
    # frontmatter survive extraction differently on each side and look like new content.
    # A confirmed substitution needs evidence of *what kind* of content it is: an explicit
    # promotional marker, an instruction aimed at the reader, or an edge header the browser
    # never receives. Everything else is reported as unclassified rather than counted.
    marked = worst >= Classification.PROMOTIONAL or bool(v.differential_headers)

    if ai_soft and v.control_clean:
        v.verdict = "soft-blocked"
        v.samples = stub_samples[:4]
        v.reason = ("200 with an empty page for " + ", ".join(ai_soft)
                    + f"; googlebot got the article ({v.similarity.get(CONTROL)})")
    elif ai_subst and v.control_clean and marked:
        v.verdict = "substituted"
        v.samples = subst_samples
        v.reason = ("crawler-only content for " + ", ".join(ai_subst)
                    + (f"; edge markers: {', '.join(v.differential_headers)}"
                       if v.differential_headers else f"; classified {worst.name.lower()}"))
    elif ai_subst and v.control_clean:
        v.verdict = "crawler-only-text"
        v.samples = subst_samples
        v.reason = ("text not found in the browser version for " + ", ".join(ai_subst)
                    + "; no promotional or instructional marker, so not counted")
    elif ai_soft or ai_subst:
        v.verdict = "all-bots-differ"
        v.samples = stub_samples[:4] or subst_samples
        v.reason = f"control diverges too ({v.similarity.get(CONTROL)})"
    elif v.refused:
        v.verdict = "refused"
        v.reason = "crawlers turned away: " + ", ".join(v.refused)
    elif any(v.similarity.get(a, 1.0) < CRAWLER_DIVERGENT for a in AI_AGENTS):
        v.verdict = "format-variant"
        v.reason = "different representation, no crawler-only content"
    return v


def report(results: list[Verdict]) -> None:
    tally: dict[str, int] = {}
    for r in results:
        tally[r.verdict] = tally.get(r.verdict, 0) + 1

    judgeable = [r for r in results if r.verdict not in UNJUDGEABLE]
    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} {n}")

    for kind, title in (
        ("soft-blocked", "SOFT-BLOCKED — HTTP 200, empty page, agent believes it read it"),
        ("substituted", "SUBSTITUTED — crawler-only content"),
        ("refused", "REFUSED — honest 4xx"),
    ):
        hits = [r for r in results if r.verdict == kind]
        if not hits:
            continue
        print(f"\n{title}: {len(hits)} of {len(judgeable)} judgeable")
        for r in hits[:25]:
            if kind == "soft-blocked":
                detail = ", ".join(
                    f"{a}={int(r.word_ratio.get(a, 0) * 100)}%" for a in r.soft_blocked
                )
                print(f"  {r.domain:<34} human={r.baseline_words}w  {detail}")
            elif kind == "substituted":
                print(f"  {r.domain:<34} {r.worst:<14} {', '.join(r.substituted)}")
            else:
                print(f"  {r.domain:<34} {', '.join(r.refused)}")
        if len(hits) > 25:
            print(f"  … {len(hits) - 25} more")


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.grade")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    domains = sorted(p.name for p in CACHE_DIR.iterdir() if p.is_dir())
    results = [grade(d) for d in domains]
    print(f"graded {len(results)} domains from cache\n")
    report(results)

    if args.json:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / "graded.json"
        out.write_text(json.dumps([r.__dict__ for r in results], indent=1), encoding="utf-8")
        print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
