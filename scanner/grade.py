"""Turns cached responses into verdicts.

Grading is deliberately separate from fetching. The first pass graded on set overlap alone
and produced false positives on pages where extraction yields only a handful of blocks — a
single rotated headline was enough to drop the overlap below threshold and trip a verdict.
Anything judged here has to survive boilerplate stripping, the raw-text haystack check and
the classifier, and a domain whose baseline is too thin to judge is reported as such rather
than counted either way.

    python -m scanner.grade [--min-material N]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from .agents import AGENTS, BASELINE, CONTROL
from .classify import Classification, classify
from .corpus import CORPUS_DIR
from .diff import compare
from .normalize import blocks_from_response, haystack, text_similarity

CACHE_DIR = CORPUS_DIR / "cache"
RESULTS_DIR = CORPUS_DIR / "results"

# A page must carry some text before any comparison means anything.
MIN_BASELINE_WORDS = 120
# The control has to look like the human page for the comparison to say anything about the
# AI crawlers; news pages rotate headlines between requests, so this is not 1.0.
CONTROL_SAME_PAGE = 0.93
# Below this, a crawler is not being served the human page.
CRAWLER_DIVERGENT = 0.80
# Below this many material blocks, divergence is indistinguishable from rotating content.
MIN_MATERIAL_BLOCKS = 3

AI_AGENTS = [a for a in AGENTS if a not in (BASELINE, CONTROL)]


@dataclass
class Verdict:
    domain: str
    verdict: str = "clean"
    reason: str = ""
    baseline_blocks: int = 0
    baseline_words: int = 0
    similarity: dict = field(default_factory=dict)
    material: dict = field(default_factory=dict)
    worst: str = "COSMETIC"
    statuses: dict = field(default_factory=dict)
    sizes: dict = field(default_factory=dict)
    marker_headers: dict = field(default_factory=dict)
    refused: list = field(default_factory=list)
    samples: list = field(default_factory=list)

    @property
    def is_confirmed(self) -> bool:
        return self.verdict in ("confirmed", "header-marked")


def _load(domain: str) -> dict:
    out = {}
    d = CACHE_DIR / domain
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("variant") == "default":
            out[rec["agent"]] = rec
    return out


def grade(domain: str) -> Verdict:
    by_agent = _load(domain)
    v = Verdict(domain=domain)
    if not by_agent:
        v.verdict, v.reason = "no-data", "nothing cached"
        return v

    for a, r in by_agent.items():
        v.statuses[a] = r["status"] if not r.get("error") else "error"
        v.sizes[a] = len(r.get("body") or "")

    if len(by_agent) < len(AGENTS):
        v.verdict, v.reason = "incomplete", f"only {len(by_agent)}/{len(AGENTS)} agents cached"
        return v

    base = by_agent.get(BASELINE)
    if not base or base.get("error") or not (200 <= base["status"] < 300):
        v.verdict, v.reason = "baseline-failed", "browser fetch did not succeed"
        return v

    for a, r in by_agent.items():
        if a == BASELINE or r.get("error"):
            continue
        if r["status"] >= 400 or r["status"] == 402:
            v.refused.append(f"{a}:{r['status']}")
        for k, val in (r.get("headers") or {}).items():
            if k.lower().startswith(("x-mobian", "x-agent", "x-ai-", "x-llm")):
                v.marker_headers.setdefault(k.lower(), {})[a] = val[:60]

    base_blocks = blocks_from_response(base["body"], base["headers"].get("content-type", ""))
    base_hay = haystack(base["body"])
    v.baseline_blocks = len(base_blocks)
    v.baseline_words = len(base_hay.split())

    worst = Classification.COSMETIC
    for a, r in by_agent.items():
        if a == BASELINE or r.get("error") or not (200 <= r["status"] < 300):
            continue
        hay = haystack(r["body"])
        v.similarity[a] = round(text_similarity(base_hay, hay), 3)

        blocks = blocks_from_response(r["body"], r["headers"].get("content-type", ""))
        d = compare(base_blocks, blocks, base_hay, hay)
        mat = [c for c in classify(d.machine_only) if c.is_material]
        v.material[a] = len(mat)
        for c in mat:
            worst = max(worst, c.classification)
        if a != CONTROL and len(mat) >= MIN_MATERIAL_BLOCKS and not v.samples:
            v.samples = [
                {"agent": a, "class": c.classification.name, "text": c.block.text[:220]}
                for c in sorted(mat, key=lambda c: -c.classification)[:4]
            ]
    v.worst = worst.name

    control_sim = v.similarity.get(CONTROL)
    divergent = [
        a for a in AI_AGENTS
        if a in v.similarity and v.similarity[a] < CRAWLER_DIVERGENT
    ]

    if v.marker_headers:
        v.verdict = "header-marked"
        v.reason = "edge tagged the response with agent-specific headers"
    elif v.baseline_words < MIN_BASELINE_WORDS:
        v.verdict = "thin"
        v.reason = f"baseline carries only {v.baseline_words} words; not judgeable"
    elif control_sim is None:
        v.verdict = "no-control"
        v.reason = "googlebot fetch unusable, cannot rule out ordinary cloaking"
    elif divergent and control_sim >= CONTROL_SAME_PAGE:
        v.verdict = "confirmed"
        v.reason = f"googlebot got the human page ({control_sim}), divergent: " + \
                   ", ".join(f"{a}={v.similarity[a]}" for a in divergent)
    elif divergent:
        v.verdict = "all-bots-differ"
        v.reason = f"control diverges too ({control_sim}); existing checkers would see this"
    elif v.refused:
        v.verdict = "refused"
        v.reason = f"crawlers refused: {', '.join(v.refused)}"
    return v


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.grade")
    ap.add_argument("--json", action="store_true", help="write graded results to disk")
    args = ap.parse_args()

    domains = sorted(p.name for p in CACHE_DIR.iterdir() if p.is_dir())
    results = [grade(d) for d in domains]

    tally: dict[str, int] = {}
    for r in results:
        tally[r.verdict] = tally.get(r.verdict, 0) + 1

    judgeable = [r for r in results if r.verdict not in
                 ("no-data", "baseline-failed", "thin")]
    confirmed = [r for r in results if r.is_confirmed]

    print(f"graded {len(results)} domains from cache\n")
    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} {n}")

    print(f"\nCONFIRMED: {len(confirmed)} of {len(judgeable)} judgeable domains")
    for r in sorted(confirmed, key=lambda r: -max(r.material.values() or [0])):
        agents = ", ".join(f"{a}:{n}" for a, n in sorted(r.material.items())
                           if a != CONTROL and n >= MIN_MATERIAL_BLOCKS)
        print(f"  {r.domain:<32} {r.worst:<18} {agents or r.reason[:40]}")

    if args.json:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / "graded.json"
        out.write_text(json.dumps([r.__dict__ for r in results], indent=1), encoding="utf-8")
        print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
