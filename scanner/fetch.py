"""Fetches one URL across the user-agent matrix.

Every response is cached to disk on first fetch. Publishers serve these responses with
`cache-control: no-store` and the CDNs in front of them rate-limit repeat probes, so a
response we fail to keep is one we may not be able to get again.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import httpx

from .agents import AGENTS, SIGNAL_HEADERS, VARIANTS, headers_for

CACHE_DIR = Path(__file__).resolve().parents[1] / "corpus" / "cache"
TIMEOUT = httpx.Timeout(25.0, connect=15.0)
POLITE_DELAY = 1.5


@dataclass
class Fetched:
    url: str
    agent: str
    variant: str
    status: int
    final_url: str
    headers: dict
    body: str
    elapsed_ms: int
    fetched_at: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    @property
    def body_sha(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8", "replace")).hexdigest()

    def signals(self) -> dict:
        return {k: v for k, v in self.headers.items() if k.lower() in SIGNAL_HEADERS}


def _md_variant(url: str) -> str:
    parts = urlparse(url)
    path = parts.path.rstrip("/")
    if path.endswith(".md"):
        return url
    return urlunparse(parts._replace(path=(path or "/index") + ".md"))


def _cache_path(url: str, agent: str, variant: str) -> Path:
    key = hashlib.sha256(f"{url}|{agent}|{variant}".encode()).hexdigest()[:24]
    host = (urlparse(url).hostname or "unknown").replace(":", "_")
    return CACHE_DIR / host / f"{key}.json"


def fetch_one(
    client: httpx.Client,
    url: str,
    agent: str,
    variant: str = "default",
    use_cache: bool = True,
) -> Fetched:
    target = _md_variant(url) if VARIANTS[variant]["md_suffix"] else url
    path = _cache_path(target, agent, variant)

    if use_cache and path.exists():
        return Fetched(**json.loads(path.read_text(encoding="utf-8")))

    started = time.perf_counter()
    try:
        r = client.get(target, headers=headers_for(agent, variant))
        result = Fetched(
            url=target,
            agent=agent,
            variant=variant,
            status=r.status_code,
            final_url=str(r.url),
            headers=dict(r.headers),
            body=r.text,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            fetched_at=time.time(),
        )
    except Exception as exc:
        result = Fetched(
            url=target,
            agent=agent,
            variant=variant,
            status=0,
            final_url=target,
            headers={},
            body="",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            fetched_at=time.time(),
            error=f"{type(exc).__name__}: {exc}",
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), ensure_ascii=False), encoding="utf-8")
    return result


def fetch_matrix(
    url: str,
    agents: list[str] | None = None,
    variants: list[str] | None = None,
    use_cache: bool = True,
) -> list[Fetched]:
    agents = agents or list(AGENTS)
    variants = variants or ["default"]

    results: list[Fetched] = []
    # One connection per agent: reusing a session across user-agents would let an edge
    # correlate the probes and is not how the real crawlers arrive.
    for variant in variants:
        for agent in agents:
            with httpx.Client(
                http2=True, follow_redirects=True, timeout=TIMEOUT, verify=True
            ) as client:
                fetched = fetch_one(client, url, agent, variant, use_cache)
            results.append(fetched)
            if fetched.error is None and not use_cache:
                time.sleep(POLITE_DELAY)
    return results
