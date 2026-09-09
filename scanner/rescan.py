"""Re-fetches the whole corpus live and rewrites the evidence archive, so the manifest
is a standing observation rather than a photograph from one afternoon.

`cache-control: no-store` on these responses is the whole reason the archive exists: a
CDN rule can change between one run of this and the next. Running it once and freezing
the result forever would make the manifest exactly the kind of unverifiable snapshot this
project spends the rest of its time arguing against -- so this re-observes on a schedule
instead, and records what changed, not just what is true today.

    python -m scanner.rescan [--workers N] [--delay SECONDS]

Intended to run from a scheduled job (see .github/workflows/rescan.yml), not by hand
against the live sites more than daily -- a corpus this size fetched too often stops being
a respectful crawl and starts being a load test on newsrooms that did nothing to deserve one.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from .agents import AGENTS
from .archive import EVIDENCE_DIR, STUB_DIR, collect
from .corpus import build, build_chains
from .fetch import fetch_one
from .grade import UNJUDGEABLE, grade
from .service import DECISION

MANIFEST = EVIDENCE_DIR / "manifest.json"
CHANGELOG = EVIDENCE_DIR / "changelog.json"
MAX_CHANGELOG_ENTRIES = 500

# Ordinal severity of what a caller would be told, not of the verdict label itself --
# "worsened" and "improved" are judgements about the decision an agent receives, and two
# verdicts that both decide `pass` are not a change worth reporting even if their names differ.
_RANK = {"pass": 0, "flag": 1, "block": 2}


def _fetch_all(entries: list[dict], workers: int, delay: float) -> None:
    def one(entry: dict) -> tuple[str, int]:
        failures = 0
        for agent in AGENTS:
            with httpx.Client(http2=True, follow_redirects=True, timeout=30) as client:
                f = fetch_one(client, entry["url"], agent, "default", use_cache=False)
            if f.error:
                failures += 1
            else:
                time.sleep(delay)
        return entry["domain"], failures

    print(f"fetching {len(entries)} domains x {len(AGENTS)} agents, live", flush=True)
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, e) for e in entries]
        for i, fut in enumerate(as_completed(futures), 1):
            domain, failures = fut.result()
            if i % 25 == 0 or failures:
                note = f" ({failures} failed)" if failures else ""
                print(f"  [{i:>3}/{len(entries)}] {domain}{note}", flush=True)
    print(f"fetched in {time.time() - started:.0f}s\n", flush=True)


def _all_verdicts(entries: list[dict]) -> dict[str, str]:
    """Every entry's verdict, including the ones collect() drops (clean, unjudgeable) --
    the public manifest only lists findings, but a domain leaving that list is itself
    something worth being able to say happened, not silently losing its history."""
    out: dict[str, str] = {}
    for e in entries:
        v = grade(e["domain"], e["url"])
        out[e["domain"]] = "unjudgeable" if v.verdict in UNJUDGEABLE else v.verdict
    return out


def _decision_for(verdict: str) -> str:
    return DECISION.get(verdict, "pass")


def _diff(old_properties: list[dict], new_verdicts: dict[str, str], observed_at: str) -> list[dict]:
    old_verdicts = {p["domain"]: p["verdict"] for p in old_properties}
    changed: list[dict] = []
    for domain in sorted(set(old_verdicts) | set(new_verdicts)):
        before = old_verdicts.get(domain)
        after = new_verdicts.get(domain)
        if before == after:
            continue
        before_rank = _RANK.get(_decision_for(before), 0) if before else 0
        after_rank = _RANK.get(_decision_for(after), 0) if after else 0
        if before_rank == after_rank:
            # Same decision either side (e.g. refused -> format-variant, both `pass`) --
            # a real change in what happened, but not one that changes what a caller is
            # told to do, so it does not belong in a feed meant to surface that.
            continue
        kind = "worsened" if after_rank > before_rank else "improved"
        changed.append({
            "observedAt": observed_at,
            "domain": domain,
            "from": before,
            "to": after or "unjudgeable",
            "kind": kind,
        })
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.rescan")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    entries = build_chains() + build(400)
    seen: set[tuple] = set()
    deduped = []
    for e in entries:
        key = (e["domain"], e["url"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    _fetch_all(deduped, args.workers, args.delay)

    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    new_verdicts = _all_verdicts(deduped)

    old_properties: list[dict] = []
    if MANIFEST.exists():
        old_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        old_properties = old_manifest.get("properties", [])
        # Keep one dated copy of whatever was live before this run overwrites it -- the
        # same convention the first archive was saved under, so history does not start
        # only from the day this script existed.
        dated = EVIDENCE_DIR / f"manifest-{old_manifest.get('captured_at', 'unknown')[:10]}.json"
        if not dated.exists():
            dated.write_text(json.dumps(old_manifest, indent=1), encoding="utf-8")

    changes = _diff(old_properties, new_verdicts, observed_at)

    manifest, stubs = collect()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    STUB_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    for sha, body in stubs.items():
        if sha in manifest["shared_stub_bodies"]:
            (STUB_DIR / f"{sha[:12]}.txt").write_text(body, encoding="utf-8")

    log: list[dict] = []
    if CHANGELOG.exists():
        log = json.loads(CHANGELOG.read_text(encoding="utf-8"))
    log = (changes + log)[:MAX_CHANGELOG_ENTRIES]
    CHANGELOG.write_text(json.dumps(log, indent=1), encoding="utf-8")

    print(f"archived {len(manifest['properties'])} properties as of {observed_at}")
    if changes:
        print(f"\n{len(changes)} decision change(s) since the last run:")
        for c in changes:
            print(f"  {c['kind']:<9} {c['domain']:<32} {c['from']} -> {c['to']}")
    else:
        print("\nno decision changes since the last run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
