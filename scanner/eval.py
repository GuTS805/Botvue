"""Measures whether substitution detection is trustworthy enough to ship.

Soft-block detection is settled: the word-ratio distribution is bimodal, so there is no
threshold to argue about. Substitution is the opposite — it depends on the classifier
separating content the human page lacks from the Markdown rendering of content it has, and
that judgement is where this project can quietly start lying.

So this measures the thing that would break: across every domain the classifier calls
substituted, what fraction of the blocks it flags are actually content rather than markup,
metadata or boilerplate? A precision figure decides whether substitution leads the pitch,
supports it, or gets dropped.

    python -m scanner.eval [--sample N]
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter

from .agents import BASELINE, CONTROL
from .classify import Classification, classify
from .corpus import CORPUS_DIR, build, build_chains
from .diff import compare
from .grade import MIN_MATERIAL_BLOCKS, _load, grade
from .normalize import blocks_from_response, haystack

# Shapes that are definitionally not "content the human page lacks". A flagged block matching
# one of these is a false positive regardless of what the classifier decided.
NOT_CONTENT = [
    (re.compile(r"^\s*<"), "raw markup"),
    (re.compile(r"^\s*[-*+#>|]{1,4}\s*$"), "bare markdown token"),
    (re.compile(r"^\s*(https?://|/)\S*\s*$"), "bare url"),
    (re.compile(r"^[\W\d\s]+$"), "no words"),
    (re.compile(r"\bdata-[\w-]+=|\shref=|\ssrc=|\bclass=\""), "markup attributes"),
    (re.compile(r"^\s*(---|\+\+\+)"), "frontmatter fence"),
    (re.compile(r"^\s*```"), "code fence"),
    # Metadata does not have to be one key per line: a Markdown head serialises several
    # onto one long line, which reads as prose to a naive length check.
    (re.compile(r"(^|\s)(og|twitter|fb|al|article|dc)[:.][\w:.\-]+\s*:"), "social metadata"),
    (re.compile(r"(^|\s)(canonical|viewport|charset|robots|generator|theme-color|"
                r"published_time|modified_time|site_name)\s*:"), "head metadata"),
    (re.compile(r"^\s*\w[\w .-]{0,30}:\s*\S+\s*$"), "key-value metadata"),
    # Headline and link rails: many capitalised fragments, few sentences. Present for
    # humans too; only the extraction differs.
    (re.compile(r"^(?=(?:.*\b[A-Z][a-z]+\b){6,})(?!.*[.!?]\s+[A-Z]).{80,}$"), "headline rail"),
]


def looks_like_content(text: str) -> tuple[bool, str]:
    stripped = text.strip()
    for pattern, why in NOT_CONTENT:
        if pattern.search(stripped):
            return False, why
    # Prose has verbs and function words; a nav strip or heading fragment usually does not.
    words = stripped.split()
    if len(words) < 6:
        return False, "too short for a claim"
    return True, "prose"


def machine_only_blocks(domain: str, url: str):
    by_agent = _load(domain, url)
    base = by_agent.get(BASELINE)
    if not base or base.get("error"):
        return {}
    base_blocks = blocks_from_response(base["body"], base["headers"].get("content-type", ""))
    base_hay = haystack(base["body"])

    out = {}
    for agent, r in by_agent.items():
        if agent in (BASELINE, CONTROL) or r.get("error"):
            continue
        if not (200 <= r["status"] < 300):
            continue
        hay = haystack(r["body"])
        blocks = blocks_from_response(r["body"], r["headers"].get("content-type", ""))
        d = compare(base_blocks, blocks, base_hay, hay)
        mat = [c for c in classify(d.machine_only) if c.is_material]
        if len(mat) >= MIN_MATERIAL_BLOCKS:
            out[agent] = mat
    return out


def main() -> int:
    ap = argparse.ArgumentParser(prog="scanner.eval")
    ap.add_argument("--sample", type=int, default=8, help="blocks to print per domain")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    entries = build_chains() + build(400)
    seen: set[tuple] = set()

    flagged = 0
    content = 0
    reasons: Counter[str] = Counter()
    by_class: Counter[str] = Counter()
    per_domain: list[dict] = []

    for e in entries:
        key = (e["domain"], e["url"])
        if key in seen:
            continue
        seen.add(key)

        v = grade(e["domain"], e["url"])
        if v.verdict != "substituted":
            continue

        blocks = machine_only_blocks(e["domain"], e["url"])
        if not blocks:
            continue

        domain_total = domain_content = 0
        samples = []
        for agent, mat in blocks.items():
            for c in mat:
                ok, why = looks_like_content(c.block.text)
                flagged += 1
                domain_total += 1
                by_class[c.classification.name] += 1
                if ok:
                    content += 1
                    domain_content += 1
                else:
                    reasons[why] += 1
                if len(samples) < args.sample:
                    samples.append({
                        "agent": agent,
                        "class": c.classification.name,
                        "content": ok,
                        "why": why,
                        "text": c.block.text[:150],
                    })

        precision = domain_content / domain_total if domain_total else 0.0
        per_domain.append({
            "domain": e["domain"],
            "flagged": domain_total,
            "content": domain_content,
            "precision": round(precision, 3),
            "samples": samples,
        })

    print(f"domains graded 'substituted': {len(per_domain)}")
    print(f"blocks flagged as material:   {flagged}")
    print(f"of those, actually content:   {content}")
    if flagged:
        print(f"PRECISION:                    {content / flagged:.1%}\n")
    print("classifier labels:", dict(by_class))
    if reasons:
        print("false positives by kind:", dict(reasons.most_common()))

    print("\nper domain:")
    for d in sorted(per_domain, key=lambda d: d["precision"]):
        print(f"  {d['domain']:<26} {d['content']:>3}/{d['flagged']:<4} precision={d['precision']:.0%}")

    for d in sorted(per_domain, key=lambda d: d["precision"]):
        print(f"\n--- {d['domain']}")
        for s in d["samples"]:
            mark = "OK " if s["content"] else "FP "
            print(f"  {mark}[{s['class']:<16}] {s['text'][:120]}")
            if not s["content"]:
                print(f"      -> {s['why']}")

    if args.json:
        out = CORPUS_DIR / "results" / "classifier-eval.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(per_domain, indent=1), encoding="utf-8")
        print(f"\nsaved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
