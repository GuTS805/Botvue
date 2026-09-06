"""The gateway an agent calls before trusting a page.

Walking skeleton: request in, verdict out, attestation queued. It fetches the URL as a
browser, as Googlebot and as the AI crawlers, grades the result, and answers with a decision.

The decision that matters is `block` on a soft-block. A crawler that receives HTTP 200 and a
stub has, from the agent's point of view, succeeded — so passing that body through is the
harm itself. Saying "this is not the page" is the whole job.

    uvicorn scanner.gateway:app --reload
    curl -X POST localhost:8000/check -H 'content-type: application/json' \
      -d '{"url":"https://www.houstonchronicle.com/"}'
"""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI
from pydantic import BaseModel, Field

from .agents import AGENTS, BASELINE, CONTROL
from .attest import Attestor, QueueAttestor, build as build_attestation
from .fetch import TIMEOUT, fetch_one
from .grade import grade

app = FastAPI(title="Botvue", version="0.1.0")
attestor: Attestor = QueueAttestor()

# Honest refusal is not a finding for the caller: a 4xx already tells the agent it got
# nothing. Only responses that look like success need a decision.
DECISION = {
    "soft-blocked": "block",
    "substituted": "flag",
    "crawler-only-text": "pass",
    "all-bots-differ": "flag",
    "format-variant": "pass",
    "refused": "pass",
    "clean": "pass",
}

EXPLANATION = {
    "soft-blocked": (
        "This page returned HTTP 200 to AI crawlers with almost none of its content. "
        "A browser and Googlebot receive the full page. Treating this response as the "
        "article would mean reporting on something that was never read."
    ),
    "substituted": (
        "AI crawlers receive content that is not present in the version served to a "
        "browser."
    ),
    "all-bots-differ": (
        "Crawlers receive different content, but so does Googlebot, so this is not "
        "specific to AI agents."
    ),
    "crawler-only-text": (
        "AI crawlers receive some text the browser version does not contain, but nothing "
        "identifies it as promotional or instructional. Reported, not acted on: at this "
        "signal's measured precision, acting would mean blocking pages over navigation "
        "markup."
    ),
    "refused": "Crawlers were refused with a 4xx. The refusal is visible to the caller.",
    "format-variant": "Crawlers receive the same content in a different format.",
    "clean": "Crawlers and the browser receive the same content.",
}


class CheckRequest(BaseModel):
    url: str
    fresh: bool = Field(default=False, description="bypass the response cache")
    attest: bool = True


class CheckResponse(BaseModel):
    url: str
    decision: str
    verdict: str
    rule: str | None
    explanation: str
    reason: str
    human_words: int
    word_ratio: dict
    control_similarity: float | None
    statuses: dict
    evidence: dict
    attestation: dict
    elapsed_ms: int


def _rule_for(v) -> str | None:
    if v.soft_blocked:
        return "size-ratio"
    if v.verdict == "refused":
        return "status-code"
    if v.substituted:
        return "block-diff"
    return None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "agents": list(AGENTS)}


@app.post("/check", response_model=CheckResponse)
def check(req: CheckRequest) -> CheckResponse:
    started = time.perf_counter()
    host = urlparse(req.url).hostname or req.url

    bodies: dict[str, str] = {}
    for agent in AGENTS:
        with httpx.Client(http2=True, follow_redirects=True, timeout=TIMEOUT) as client:
            f = fetch_one(client, req.url, agent, "default", use_cache=not req.fresh)
        if not f.error:
            bodies[agent] = f.body_sha

    verdict = grade(host, req.url)
    rule = _rule_for(verdict)
    decision = DECISION.get(verdict.verdict, "pass")

    receipt = {"status": "skipped", "id": None}
    # Only findings are worth anchoring; a clean page has nothing anyone would later deny.
    if req.attest and rule and verdict.verdict not in ("clean", "format-variant"):
        agents = verdict.soft_blocked or verdict.substituted or [
            r.split(":")[0] for r in verdict.refused
        ]
        attestation = build_attestation(verdict, req.url, rule, agents, bodies)
        receipt = attestor.submit(attestation).__dict__

    return CheckResponse(
        url=req.url,
        decision=decision,
        verdict=verdict.verdict,
        rule=rule,
        explanation=EXPLANATION.get(verdict.verdict, ""),
        reason=verdict.reason,
        human_words=verdict.baseline_words,
        word_ratio=verdict.word_ratio,
        control_similarity=verdict.similarity.get(CONTROL),
        statuses=verdict.statuses,
        evidence=bodies,
        attestation=receipt,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
