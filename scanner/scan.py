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
from .corpus import build
from .fetch import TIMEOUT, fetch_one
from .grade import CONTROL, MIN_MATERIAL_BLOCKS, RESULTS_DIR, grade


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
    args = ap.parse_args()

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

    results = [grade(e["domain"]) for e in entries]
    by_domain = {e["domain"]: e for e in entries}

    tally: dict[str, int] = {}
    for r in results:
        tally[r.verdict] = tally.get(r.verdict, 0) + 1

    judgeable = [r for r in results
                 if r.verdict not in ("no-data", "baseline-failed", "thin", "incomplete")]
    confirmed = [r for r in results if r.is_confirmed]

    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} {n}")

    print(f"\nCONFIRMED: {len(confirmed)} of {len(judgeable)} judgeable "
          f"({len(results)} scanned)")
    for r in sorted(confirmed, key=lambda r: -max(r.material.values() or [0])):
        agents = ", ".join(
            f"{a}={r.similarity.get(a)}" for a, n in sorted(r.material.items())
            if a != CONTROL and n >= MIN_MATERIAL_BLOCKS
        )
        src = by_domain.get(r.domain, {}).get("source", "?")
        print(f"  {r.domain:<32} {src:<10} {r.worst:<16} {agents or r.reason[:44]}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"scan-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps([r.__dict__ for r in results], indent=1), encoding="utf-8")
    print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
