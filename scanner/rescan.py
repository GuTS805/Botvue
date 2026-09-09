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

import httpx

from .agents import AGENTS
from .archive import EVIDENCE_DIR, STUB_DIR, collect, dedupe_corpus, grade_corpus
from .fetch import fetch_one
from .grade import UNJUDGEABLE
from .service import DECISION

MANIFEST = EVIDENCE_DIR / "manifest.json"
CHANGELOG = EVIDENCE_DIR / "changelog.json"
# The public manifest only lists findings, so a domain that soft-blocks and then goes
# unjudgeable for a run vanishes from it -- if the diff compared against manifest.json
# alone, its true prior state would be lost, and the next successful grade of it would
# read as brand new rather than a return to what it already was. This file is the ground
# truth the diff actually compares against: every domain ever successfully graded, kept
# current only when a grade succeeds.
LAST_VERDICTS = EVIDENCE_DIR / "last_verdicts.json"
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


def _verdict_labels(entries: list[dict], graded: dict[tuple[str, str], object]) -> dict[str, str]:
    """Every entry's verdict label, including the ones collect() drops (clean,
    unjudgeable) -- the public manifest only lists findings, but a domain leaving that
    list is itself something worth being able to say happened, not silently losing its
    history. Reads from an already-graded map rather than calling grade() again -- the
    corpus is graded exactly once per run, in grade_corpus()."""
    out: dict[str, str] = {}
    for e in entries:
        v = graded[(e["domain"], e["url"])]
        out[e["domain"]] = "unjudgeable" if v.verdict in UNJUDGEABLE else v.verdict
    return out


def _decision_for(verdict: str) -> str:
    return DECISION.get(verdict, "pass")


def _diff(last_good: dict[str, str], new_verdicts: dict[str, str], observed_at: str) -> list[dict]:
    """Compares against the last verdict this project actually managed to observe for
    each domain -- not against whatever the previous run happened to produce. A domain
    that fails to grade this run (a timeout, a rate limit, one bad minute from a CDN) is
    silently skipped rather than scored: "we could not observe it" is not evidence that
    anything changed, and reporting it as one would be the exact failure this project
    spends the rest of its time reporting in other people's services.
    """
    changed: list[dict] = []
    for domain, after in sorted(new_verdicts.items()):
        if after in UNJUDGEABLE or after == "unjudgeable":
            continue  # no reliable reading this run; leave last_good exactly as it was

        before = last_good.get(domain)
        if before == after:
            continue
        before_rank = _RANK.get(_decision_for(before), 0) if before else 0
        after_rank = _RANK[_decision_for(after)]
        if before_rank != after_rank:
            changed.append({
                "observedAt": observed_at,
                "domain": domain,
                "from": before or "not previously observed",
                "to": after,
                "kind": "worsened" if after_rank > before_rank else "improved",
            })
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.rescan")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    deduped = dedupe_corpus()

    _fetch_all(deduped, args.workers, args.delay)

    observed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    # Graded exactly once. new_verdicts (this diff) and manifest (the public archive)
    # both read from `graded` below, rather than each grading the corpus themselves --
    # that used to cost this script two full passes over ~570 entries instead of one.
    graded = grade_corpus(deduped)
    new_verdicts = _verdict_labels(deduped, graded)

    last_good: dict[str, str] = {}
    if LAST_VERDICTS.exists():
        last_good = json.loads(LAST_VERDICTS.read_text(encoding="utf-8"))
    elif MANIFEST.exists():
        # First run under this scheme: bootstrap from the manifest that already exists,
        # which is the best record available even though it only covers findings.
        old_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        last_good = {p["domain"]: p["verdict"] for p in old_manifest.get("properties", [])}

    if MANIFEST.exists():
        old_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        # Keep one dated copy of whatever was live before this run overwrites it -- the
        # same convention the first archive was saved under, so history does not start
        # only from the day this script existed.
        dated = EVIDENCE_DIR / f"manifest-{old_manifest.get('captured_at', 'unknown')[:10]}.json"
        if not dated.exists():
            dated.write_text(json.dumps(old_manifest, indent=1), encoding="utf-8")

    changes = _diff(last_good, new_verdicts, observed_at)

    # Only a domain graded successfully this run gets its stored state touched. A domain
    # that came back unjudgeable keeps whatever it last legitimately was, so the *next*
    # successful grade of it is compared against the truth, not against "unknown".
    for domain, verdict in new_verdicts.items():
        if verdict not in UNJUDGEABLE and verdict != "unjudgeable":
            last_good[domain] = verdict
    LAST_VERDICTS.write_text(json.dumps(last_good, indent=1, sort_keys=True), encoding="utf-8")

    manifest, stubs = collect(entries=deduped, graded=graded)
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

    skipped = sum(1 for v in new_verdicts.values() if v in UNJUDGEABLE or v == "unjudgeable")
    print(f"archived {len(manifest['properties'])} properties as of {observed_at}"
          f" ({skipped} unjudgeable this run, left unscored)")
    if changes:
        print(f"\n{len(changes)} decision change(s) since the last run:")
        for c in changes:
            print(f"  {c['kind']:<9} {c['domain']:<32} {c['from']} -> {c['to']}")
    else:
        print("\nno decision changes since the last run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
