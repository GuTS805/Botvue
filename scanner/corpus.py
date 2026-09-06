"""Builds the scan list.

Sites publishing an llms.txt have already decided how they want AI crawlers treated, which
makes them both the highest-yield pool and the only pool where a declared policy can be
compared against what is actually served. The curated seeds cover the segments that pool
misses: publishers monetising AI traffic, shopping surfaces an agent would consult, and the
package-documentation sites that SEO-poisoning campaigns imitate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx

CORPUS_DIR = Path(__file__).resolve().parents[1] / "corpus"
LLMSTXT_INDEX = "https://llmstxt.site/"
_LLMS_DOMAIN = re.compile(r"https?://([a-z0-9\-]+\.[a-z0-9.\-]+)/llms\.txt")

PUBLISHERS = [
    "time.com", "fortune.com", "theatlantic.com", "businessinsider.com", "axios.com",
    "vox.com", "theverge.com", "wired.com", "forbes.com", "newsweek.com",
    "usatoday.com", "nypost.com", "people.com", "sciencealert.com", "sfgate.com",
    "thedailybeast.com", "salon.com", "rollingstone.com", "variety.com", "cnet.com",
    "zdnet.com", "techcrunch.com", "engadget.com", "mashable.com", "gizmodo.com",
    "arstechnica.com", "thehill.com", "politico.com", "semafor.com", "theguardian.com",
]

ECOMMERCE = [
    "bestbuy.com", "target.com", "etsy.com", "ebay.com", "wayfair.com",
    "homedepot.com", "lowes.com", "ikea.com", "zappos.com", "chewy.com",
    "rei.com", "nike.com", "adidas.com", "sephora.com", "macys.com",
    "nordstrom.com", "overstock.com", "newegg.com", "asos.com", "uniqlo.com",
]

DOCS = [
    "pypi.org", "npmjs.com", "docs.python.org", "developer.mozilla.org",
    "kubernetes.io", "nodejs.org", "react.dev", "vuejs.org", "go.dev",
    "docs.docker.com",
]


def _fetch_llmstxt_domains(limit: int) -> list[str]:
    r = httpx.get(
        LLMSTXT_INDEX,
        timeout=45,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (compatible; Botvue corpus builder)"},
    )
    r.raise_for_status()
    seen: list[str] = []
    for domain in _LLMS_DOMAIN.findall(r.text):
        if domain not in seen:
            seen.append(domain)
    return seen[:limit]


def build(llmstxt_limit: int = 400, refresh: bool = False) -> list[dict]:
    out_path = CORPUS_DIR / "urls.json"
    if out_path.exists() and not refresh:
        return json.loads(out_path.read_text(encoding="utf-8"))

    entries: list[dict] = []
    seen: set[str] = set()

    def add(domain: str, source: str) -> None:
        domain = domain.strip().lower().rstrip("/")
        if not domain or domain in seen:
            return
        seen.add(domain)
        entries.append({"domain": domain, "url": f"https://{domain}/", "source": source})

    for d in PUBLISHERS:
        add(d, "publisher")
    for d in ECOMMERCE:
        add(d, "ecommerce")
    for d in DOCS:
        add(d, "docs")
    for d in _fetch_llmstxt_domains(llmstxt_limit):
        add(d, "llmstxt")

    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(entries, indent=1), encoding="utf-8")
    return entries


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="scanner.corpus")
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    built = build(args.limit, args.refresh)
    counts: dict[str, int] = {}
    for e in built:
        counts[e["source"]] = counts.get(e["source"], 0) + 1
    print(f"{len(built)} domains: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
