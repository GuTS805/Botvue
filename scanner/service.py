"""The check, independent of how it was asked for.

Transport belongs to adapters. This module knows nothing about HTTP, MCP or payment — it
takes a URL and returns a decision, so a second adapter costs a translation layer rather
than a reimplementation. The Bazantic integration shape is still unconfirmed, so this is
where the logic stays regardless of which shape it turns out to be.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from .agents import AGENTS, CONTROL
from .attest import Attestor, NullAttestor, build as build_attestation
from .fetch import TIMEOUT, fetch_one
from .grade import Verdict, grade

# What the caller should do with the body. `block` is reserved for responses that would
# otherwise be mistaken for the page: an honest 4xx already tells the agent it got nothing,
# so there is nothing to protect it from.
DECISION = {
    "soft-blocked": "block",
    "substituted": "flag",
    "all-bots-differ": "flag",
    "crawler-only-text": "pass",
    "format-variant": "pass",
    "refused": "pass",
    "clean": "pass",
}

EXPLANATION = {
    "soft-blocked": (
        "This page returned HTTP 200 to AI crawlers with almost none of its content. A "
        "browser and Googlebot receive the full page. Treating this response as the article "
        "would mean reporting on something that was never read."
    ),
    "substituted": (
        "AI crawlers receive content that is not present in the version served to a browser, "
        "identified by a promotional or instructional marker or by a response header the "
        "browser never receives."
    ),
    "crawler-only-text": (
        "AI crawlers receive some text the browser version does not contain, but nothing "
        "identifies it as promotional or instructional. Reported, not acted on: this signal "
        "measures about 58% precision against hand-checked samples, so acting on it would "
        "mean blocking pages over navigation markup."
    ),
    "all-bots-differ": (
        "Crawlers receive different content, but so does Googlebot, so this is not specific "
        "to AI agents."
    ),
    "refused": "Crawlers were refused with a 4xx. The refusal is visible to the caller.",
    "format-variant": "Crawlers receive the same content in a different format.",
    "clean": "Crawlers and the browser receive the same content.",
    "thin": "The page carries too little text to compare.",
    "baseline-failed": "The browser fetch did not succeed, so there is nothing to compare.",
}

NOT_ATTESTED = ("clean", "format-variant", "crawler-only-text", "thin", "baseline-failed")


@dataclass
class CheckResult:
    url: str
    domain: str
    decision: str
    verdict: str
    rule: str | None
    explanation: str
    reason: str
    human_words: int
    word_ratio: dict = field(default_factory=dict)
    control_similarity: float | None = None
    statuses: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    worst: str = "COSMETIC"
    samples: list = field(default_factory=list)
    attestation: dict = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def is_finding(self) -> bool:
        return self.decision in ("block", "flag")


def rule_for(v: Verdict) -> str | None:
    if v.soft_blocked:
        return "size-ratio"
    if v.verdict == "refused":
        return "status-code"
    if v.substituted:
        return "block-diff"
    return None


def check(
    url: str,
    *,
    fresh: bool = False,
    attestor: Attestor | None = None,
) -> CheckResult:
    started = time.perf_counter()
    domain = urlparse(url).hostname or url
    attestor = attestor or NullAttestor()

    evidence: dict[str, str] = {}
    for agent in AGENTS:
        with httpx.Client(http2=True, follow_redirects=True, timeout=TIMEOUT) as client:
            fetched = fetch_one(client, url, agent, "default", use_cache=not fresh)
        if not fetched.error:
            evidence[agent] = fetched.body_sha

    verdict = grade(domain, url)
    rule = rule_for(verdict)

    receipt = {"status": "skipped", "id": None}
    if rule and verdict.verdict not in NOT_ATTESTED:
        agents = verdict.soft_blocked or verdict.substituted or [
            r.split(":")[0] for r in verdict.refused
        ]
        receipt = attestor.submit(
            build_attestation(verdict, url, rule, agents, evidence)
        ).__dict__

    return CheckResult(
        url=url,
        domain=domain,
        decision=DECISION.get(verdict.verdict, "pass"),
        verdict=verdict.verdict,
        rule=rule,
        explanation=EXPLANATION.get(verdict.verdict, ""),
        reason=verdict.reason,
        human_words=verdict.baseline_words,
        word_ratio=verdict.word_ratio,
        control_similarity=verdict.similarity.get(CONTROL),
        statuses=verdict.statuses,
        evidence=evidence,
        worst=verdict.worst,
        samples=verdict.samples,
        attestation=receipt,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
