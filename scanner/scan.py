"""Fetches the corpus across the user-agent matrix, then grades it.

This module only fetches. Every verdict comes from `scanner.grade`, which reads the cache —
an earlier version graded inline as well, and the two implementations drifted far enough
apart to report a number that was wrong.

    python -m scanner.scan [--limit N] [--workers N] [--fresh]
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

from .agents import AGENTS
from .corpus import CORPUS_DIR, build
from .fetch import TIMEOUT, fetch_one
from .grade import RESULTS_DIR, UNJUDGEABLE, grade, report


def fetch_domain(entry: dict, use_cache: bool, delay: float) -> tuple[str, int]:
    failures = 0
    for agent in AGENTS:
        with httpx.Client(http2=True, follow_redirects=True, timeout=TIMEOUT) as client:
            f = fetch_one(client, entry["url"], agent, "default", use_cache)
        if f.error:
            failures += 1
        elif not use_cache:
            time.sleep(delay)
    return entry["domain"], failures


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.scan")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--delay", type=float, default=0.6)
    ap.add_argument("--fresh", action="store_true", help="ignore cached responses")
    ap.add_argument("--corpus-limit", type=int, default=400)
    ap.add_argument("--articles", action="store_true",
                    help="scan publisher article URLs instead of homepages")
    args = ap.parse_args()

    if args.articles:
        entries = json.loads(
            (CORPUS_DIR / "articles.json").read_text(encoding="utf-8"))[: args.limit]
    else:
        entries = build(args.corpus_limit)[: args.limit]
    print(f"fetching {len(entries)} domains x {len(AGENTS)} agents", flush=True)

    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(fetch_domain, e, not args.fresh, args.delay) for e in entries
        ]
        for i, fut in enumerate(as_completed(futures), 1):
            domain, failures = fut.result()
            if i % 25 == 0 or failures:
                note = f" ({failures} failed)" if failures else ""
                print(f"  [{i:>3}/{len(entries)}] {domain}{note}", flush=True)

    print(f"fetched in {time.time() - started:.0f}s; grading\n", flush=True)

    results = [grade(e["domain"], e["url"]) for e in entries]
    judgeable = [r for r in results if r.verdict not in UNJUDGEABLE]
    print(f"{len(judgeable)} of {len(results)} domains judgeable\n")
    report(results)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"scan-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps([r.__dict__ for r in results], indent=1), encoding="utf-8")
    print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
