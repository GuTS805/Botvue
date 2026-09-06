"""Freezes the scan findings into a committed, verifiable evidence set.

These responses carry `no-store`, and a CDN rule can be changed in an afternoon. Once that
happens there is nothing left to point at, so what was served has to be written down while
it is still being served — with hashes, so the record can be checked rather than believed.

The manifest records exactly what was observed and nothing about why. A body hash, a status
code and a timestamp are facts; intent is not, and is not recorded here.

    python -m scanner.archive
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .agents import BASELINE, CONTROL
from .corpus import CORPUS_DIR, build, build_chains
from .grade import UNJUDGEABLE, _load, grade
from .normalize import haystack

EVIDENCE_DIR = CORPUS_DIR.parent / "evidence"
STUB_DIR = EVIDENCE_DIR / "stubs"
KEEP_HEADERS = (
    "content-type", "content-encoding", "cache-control", "server", "via",
    "x-served-by", "x-cache", "cf-cache-status", "content-length",
)
STUB_MAX_BYTES = 20_000


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _agent_record(rec: dict) -> dict:
    body = rec.get("body") or ""
    return {
        "status": rec.get("status"),
        "bytes": len(body),
        "sha256": _sha(body),
        "words": len(haystack(body).split()) if body else 0,
        "headers": {
            k.lower(): v for k, v in (rec.get("headers") or {}).items()
            if k.lower() in KEEP_HEADERS or k.lower().startswith(("x-mobian", "x-llm"))
        },
    }


def collect() -> dict:
    entries = build_chains() + build(400)
    seen: set[tuple] = set()
    properties: list[dict] = []
    stub_bodies: dict[str, str] = {}

    for e in entries:
        key = (e["domain"], e["url"])
        if key in seen:
            continue
        seen.add(key)

        v = grade(e["domain"], e["url"])
        if v.verdict in UNJUDGEABLE or v.verdict == "clean":
            continue

        by_agent = _load(e["domain"], e["url"])
        agents = {a: _agent_record(r) for a, r in sorted(by_agent.items())}

        for a in v.soft_blocked:
            body = (by_agent.get(a) or {}).get("body") or ""
            if body and len(body) <= STUB_MAX_BYTES:
                stub_bodies[_sha(body)] = body

        properties.append({
            "domain": e["domain"],
            "chain": e["source"],
            "url": e["url"],
            "verdict": v.verdict,
            "control_similarity": v.similarity.get(CONTROL),
            "baseline_words": v.baseline_words,
            "word_ratio": v.word_ratio,
            "soft_blocked": v.soft_blocked,
            "substituted": v.substituted,
            "refused": v.refused,
            "agents": agents,
        })

    properties.sort(key=lambda p: (p["chain"], p["domain"]))

    stub_index: dict[str, dict] = {}
    for sha, body in stub_bodies.items():
        served_on = sorted({
            p["domain"] for p in properties
            if any(p["agents"].get(a, {}).get("sha256") == sha for a in p["soft_blocked"])
        })
        if len(served_on) < 2:
            continue
        stub_index[sha] = {"bytes": len(body), "served_identically_on": served_on}

    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "baseline_agent": BASELINE,
        "control_agent": CONTROL,
        "note": (
            "Observed facts only: these bytes were served to this user-agent at this time. "
            "No claim is made about intent."
        ),
        "shared_stub_bodies": stub_index,
        "properties": properties,
    }, stub_bodies


def main() -> int:
    manifest, stubs = collect()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    STUB_DIR.mkdir(parents=True, exist_ok=True)

    (EVIDENCE_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    for sha, body in stubs.items():
        if sha in manifest["shared_stub_bodies"]:
            (STUB_DIR / f"{sha[:12]}.txt").write_text(body, encoding="utf-8")

    tally: dict[str, int] = {}
    for p in manifest["properties"]:
        tally[p["verdict"]] = tally.get(p["verdict"], 0) + 1

    print(f"archived {len(manifest['properties'])} properties")
    for k, n in sorted(tally.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<18} {n}")
    print("\nbyte-identical stub bodies:")
    for sha, info in manifest["shared_stub_bodies"].items():
        print(f"  {sha[:12]}  {info['bytes']:>6} bytes  on "
              f"{len(info['served_identically_on'])} properties")
    print(f"\n-> {EVIDENCE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
