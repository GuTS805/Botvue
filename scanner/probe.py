"""Single-URL probe.

Answers the question the whole project rests on: does this URL serve different content to an
AI crawler than to a browser, and does Googlebot — the only user-agent existing cloaking
checkers test — see the human version or the crawler version?

    python -m scanner.probe https://example.com/article [--fresh] [--variants]
"""

from __future__ import annotations

import argparse
import sys

from .agents import BASELINE, CONTROL
from .diff import compare
from .fetch import fetch_matrix
from .normalize import blocks_from_response


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.probe")
    ap.add_argument("url")
    ap.add_argument("--fresh", action="store_true", help="bypass the response cache")
    ap.add_argument("--variants", action="store_true", help="also try Accept: text/markdown and .md")
    ap.add_argument("--show", type=int, default=6, help="machine-only blocks to print")
    args = ap.parse_args()

    variants = ["default", "accept-md", "path-md"] if args.variants else ["default"]
    results = fetch_matrix(args.url, variants=variants, use_cache=not args.fresh)

    baseline = next(
        (r for r in results if r.agent == BASELINE and r.variant == "default"), None
    )
    if baseline is None or not baseline.ok:
        print(f"baseline fetch failed: {baseline.error if baseline else 'no result'}")
        return 1

    baseline_blocks = blocks_from_response(
        baseline.body, baseline.headers.get("content-type", "")
    )

    print(f"\nURL      {args.url}")
    print(f"baseline {BASELINE}: {baseline.status} · {len(baseline.body):,}b · "
          f"{len(baseline_blocks)} blocks · {baseline.body_sha[:12]}")
    print(f"\n{'agent':<16}{'var':<10}{'st':<5}{'bytes':>9}{'blocks':>8}"
          f"{'new':>6}{'gone':>6}{'bps':>7}  sha")
    print("-" * 82)

    findings = []
    for r in results:
        if r.agent == BASELINE and r.variant == "default":
            continue
        if not r.ok:
            print(f"{r.agent:<16}{r.variant:<10}{r.status:<5}{'—':>9}{'—':>8}"
                  f"{'—':>6}{'—':>6}{'—':>7}  {(r.error or '')[:28]}")
            continue

        blocks = blocks_from_response(r.body, r.headers.get("content-type", ""))
        d = compare(baseline_blocks, blocks)
        same = r.body_sha == baseline.body_sha
        print(f"{r.agent:<16}{r.variant:<10}{r.status:<5}{len(r.body):>9,}{len(blocks):>8}"
              f"{len(d.machine_only):>6}{len(d.human_only):>6}{d.divergence_bps:>7}"
              f"  {r.body_sha[:12]}{'  (identical)' if same else ''}")
        if d.is_material:
            findings.append((r, d))

    signals = {}
    for r in results:
        for k, v in r.signals().items():
            if k.lower().startswith("x-") or k.lower() == "cache-control":
                signals.setdefault(k, set()).add(f"{r.agent}={v[:40]}")
    interesting = {k: v for k, v in signals.items() if len(v) > 1 or k.lower().startswith("x-mob")}
    if interesting:
        print("\nheaders differing by agent:")
        for k, v in sorted(interesting.items()):
            print(f"  {k}: {', '.join(sorted(v))[:150]}")

    control = next((r for r in results if r.agent == CONTROL and r.variant == "default"), None)
    if control and control.ok:
        cd = compare(baseline_blocks, blocks_from_response(
            control.body, control.headers.get("content-type", "")))
        ai_divergent = [r.agent for r, _ in findings if r.agent != CONTROL]
        print(f"\ncontrol: {CONTROL} sees {len(cd.machine_only)} machine-only blocks")
        if ai_divergent and not cd.is_material:
            print("  >> Googlebot gets the human version while "
                  f"{', '.join(sorted(set(ai_divergent)))} do not.")
            print("  >> Every cloaking checker that compares Googlebot to a browser "
                  "returns clean here.")

    for r, d in findings[:3]:
        print(f"\nmachine-only blocks — {r.agent} ({r.variant}):")
        for b in d.machine_only[: args.show]:
            print(f"  · {b.text[:200]}")
        if len(d.machine_only) > args.show:
            print(f"  … {len(d.machine_only) - args.show} more")

    if not findings:
        print("\nno material divergence detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
