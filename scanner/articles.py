"""Finds a real article URL for each publisher.

Homepages are the wrong unit to test. They rotate between requests, which shows up as
divergence that has nothing to do with the crawler, and the substitution seen so far was
served on article pages. Feeds are the cheapest reliable source of article URLs: one request
per domain, and the publisher is advertising them deliberately.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from .corpus import CORPUS_DIR

FEED_PATHS = ("/feed/", "/rss/", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml", "/feed")
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

_LINK_HREF = re.compile(r"<link[^>]+href=[\"']([^\"']+)[\"']", re.I)
_ITEM_LINK = re.compile(r"<link>\s*([^<\s]+)\s*</link>", re.I)
_GUID = re.compile(r"<guid[^>]*>\s*(https?://[^<\s]+)\s*</guid>", re.I)
_FEED_DISCOVER = re.compile(
    r"<link[^>]+type=[\"']application/(?:rss|atom)\+xml[\"'][^>]*>", re.I
)


def _article_links(xml: str, domain: str) -> list[str]:
    urls: list[str] = []
    for pattern in (_ITEM_LINK, _GUID, _LINK_HREF):
        for m in pattern.findall(xml):
            u = m.strip()
            if not u.startswith("http") or "," in u or " " in u:
                continue
            parsed = urlparse(u)
            if domain.replace("www.", "") not in parsed.netloc:
                continue
            if parsed.netloc.startswith(("feeds.", "feed.", "rss.")):
                continue
            # A feed's self-link and the site root are not articles.
            if parsed.path.rstrip("/") in ("", "/feed", "/rss") or u.endswith(".xml"):
                continue
            if len(parsed.path.strip("/").split("/")) < 2:
                continue
            if u not in urls:
                urls.append(u)
        if len(urls) >= 3:
            break
    return urls


def find_article(domain: str, timeout: float = 15.0) -> str | None:
    base = f"https://{domain}"
    headers = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
    try:
        with httpx.Client(
            follow_redirects=True, timeout=timeout, headers=headers, http2=True
        ) as client:
            for path in FEED_PATHS:
                try:
                    r = client.get(urljoin(base, path))
                except Exception:
                    continue
                if r.status_code == 200 and ("<item" in r.text or "<entry" in r.text):
                    found = _article_links(r.text, domain)
                    if found:
                        return found[0]

            try:
                home = client.get(base)
            except Exception:
                return None
            if home.status_code != 200:
                return None
            for tag in _FEED_DISCOVER.findall(home.text):
                href = _LINK_HREF.search(tag)
                if not href:
                    continue
                try:
                    r = client.get(urljoin(base, href.group(1)))
                except Exception:
                    continue
                if r.status_code == 200:
                    found = _article_links(r.text, domain)
                    if found:
                        return found[0]
    except Exception:
        return None
    return None


def build_article_corpus(domains: list[str], workers: int = 12) -> list[dict]:
    out: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(find_article, d): d for d in domains}
        for fut in as_completed(futures):
            domain = futures[fut]
            url = fut.result()
            if url:
                out.append({"domain": domain, "url": url, "source": "publisher-article"})
    return out


if __name__ == "__main__":
    import argparse

    from .corpus import PUBLISHERS

    ap = argparse.ArgumentParser(prog="scanner.articles")
    ap.add_argument("--out", default=str(CORPUS_DIR / "articles.json"))
    args = ap.parse_args()

    entries = build_article_corpus(PUBLISHERS)
    Path(args.out).write_text(json.dumps(entries, indent=1), encoding="utf-8")
    print(f"found articles for {len(entries)} of {len(PUBLISHERS)} publishers")
    for e in entries[:10]:
        print(f"  {e['domain']:<26} {e['url'][:80]}")
