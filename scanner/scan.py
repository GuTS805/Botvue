"""Runs the user-agent matrix across the corpus and grades each domain.

The block classifier is not tuned yet, so the verdict here deliberately does not depend on
it. What it depends on is the comparison that no existing cloaking checker performs: whether
Googlebot receives the human page while an AI crawler receives something else. That question
is answerable from set overlap alone, and it is the number the pitch rests on.

    python -m scanner.scan [--limit N] [--workers N] [--fresh]
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .agents import AGENTS, BASELINE, CONTROL, headers_for
from .corpus import CORPUS_DIR, build
from .fetch import TIMEOUT, fetch_one
from .normalize import blocks_from_response

RESULTS_DIR = CORPUS_DIR / "results"

# Above this, two responses are the same page; below it they are different content.
SAME_PAGE = 0.90
DIFFERENT_CONTENT = 0.70

AI_AGENTS = [a for a in AGENTS if a not in (BASELINE, CONTROL)]


@dataclass
class DomainResult:
    domain: str
    url: str
    source: str
    verdict: str = "clean"
    statuses: dict = field(default_factory=dict)
    sizes: dict = field(default_factory=dict)
    similarity: dict = field(default_factory=dict)
    marker_headers: dict = field(default_factory=dict)
    divergent_agents: list = field(default_factory=list)
    blocked_agents: list = field(default_factory=list)
    note: str = ""


def _similarity(a_keys: set[str], b_keys: set[str]) -> float:
    if not a_keys and not b_keys:
        return 1.0
    if not a_keys or not b_keys:
        return 0.0
    return len(a_keys & b_keys) / len(a_keys | b_keys)


def scan_domain(entry: dict, use_cache: bool, delay: float) -> DomainResult:
    res = DomainResult(domain=entry["domain"], url=entry["url"], source=entry["source"])
    fetched: dict = {}

    for agent in AGENTS:
        with httpx.Client(http2=True, follow_redirects=True, timeout=TIMEOUT) as client:
            f = fetch_one(client, entry["url"], agent, "default", use_cache)
        fetched[agent] = f
        res.statuses[agent] = f.status if f.error is None else f.error[:40]
        res.sizes[agent] = len(f.body)
        if not use_cache:
            time.sleep(delay)

    base = fetched[BASELINE]
    if not base.ok:
        res.verdict = "baseline-failed"
        res.note = base.error or f"status {base.status}"
        return res

    keys = {}
    for agent, f in fetched.items():
        if f.ok:
            keys[agent] = {b.key for b in blocks_from_response(
                f.body, f.headers.get("content-type", ""))}

    if not keys.get(BASELINE):
        res.verdict = "no-text"
        res.note = "baseline extracted no comparable text"
        return res

    for agent in AGENTS:
        if agent in keys:
            res.similarity[agent] = round(_similarity(keys[BASELINE], keys[agent]), 3)

    for agent in AI_AGENTS + [CONTROL]:
        f = fetched[agent]
        if f.error is None and f.status >= 400 and base.status < 400:
            res.blocked_agents.append(agent)

    # Headers that appear for some agents and not others are how an edge marks a substituted
    # response; they survive even when the body diff is ambiguous.
    for agent, f in fetched.items():
        if f.error:
            continue
        for k, v in f.headers.items():
            kl = k.lower()
            if kl.startswith(("x-mobian", "x-agent", "x-ai-", "x-llm")):
                res.marker_headers.setdefault(kl, {})[agent] = v[:60]

    control_sim = res.similarity.get(CONTROL)
    res.divergent_agents = [
        a for a in AI_AGENTS
        if a in res.similarity and res.similarity[a] < DIFFERENT_CONTENT
    ]

    if res.marker_headers:
        res.verdict = "confirmed"
        res.note = "edge marked the response with agent-specific headers"
    elif res.divergent_agents and control_sim is not None and control_sim >= SAME_PAGE:
        res.verdict = "confirmed"
        res.note = "googlebot received the human page, ai crawlers did not"
    elif res.divergent_agents and control_sim is not None and control_sim < DIFFERENT_CONTENT:
        res.verdict = "all-bots-differ"
        res.note = "googlebot also diverges; existing checkers would catch this"
    elif res.divergent_agents:
        res.verdict = "divergent"
        res.note = "ai crawlers diverge; control inconclusive"
    elif res.blocked_agents:
        res.verdict = "blocked"
        res.note = f"refused: {', '.join(res.blocked_agents)}"

    return res


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.scan")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--corpus-limit", type=int, default=400)
    args = ap.parse_args()

    entries = build(args.corpus_limit)[: args.limit]
    print(f"scanning {len(entries)} domains x {len(AGENTS)} agents "
          f"({len(entries) * len(AGENTS)} requests)\n")

    results: list[DomainResult] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(scan_domain, e, not args.fresh, args.delay): e for e in entries
        }
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            if r.verdict in ("confirmed", "all-bots-differ", "divergent", "blocked"):
                marker = "*" if r.verdict == "confirmed" else " "
                print(f"{marker} [{i:>3}/{len(entries)}] {r.domain:<34} {r.verdict:<16}"
                      f" {r.note[:52]}")
            elif i % 25 == 0:
                print(f"  [{i:>3}/{len(entries)}] ...")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"scan-{stamp}.json"
    out.write_text(
        json.dumps([r.__dict__ for r in results], indent=1, default=str), encoding="utf-8"
    )

    tally: dict[str, int] = {}
    for r in results:
        tally[r.verdict] = tally.get(r.verdict, 0) + 1

    confirmed = [r for r in results if r.verdict == "confirmed"]
    usable = [r for r in results if r.verdict not in ("baseline-failed", "no-text")]

    print(f"\n{'-' * 70}\nscanned {len(results)} domains in {time.time() - started:.0f}s")
    for k, v in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} {v}")
    print(f"\nCONFIRMED divergence: {len(confirmed)} of {len(usable)} usable domains")
    for r in confirmed:
        agents = ", ".join(r.divergent_agents) or "header-marked"
        print(f"  {r.domain:<34} {agents}")
    print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
