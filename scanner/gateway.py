"""HTTP adapter for the check.

All logic lives in `scanner.service`; this module is transport and payment only, so a
second adapter — an MCP server, say — reuses the same code rather than reimplementing it.

    uvicorn scanner.gateway:app --reload
    curl -X POST localhost:8000/check -H 'content-type: application/json' \
      -d '{"url":"https://www.houstonchronicle.com/"}'
"""

from __future__ import annotations

from fastapi import FastAPI, Header, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import json
import os
from pathlib import Path
from urllib.parse import urlparse

from .agents import AGENTS, BASELINE, CONTROL
from .attest import QueueAttestor
from .payments import OpenVerifier, build_payment_layer
from .service import DECISION, EXPLANATION, CheckResult, check

app = FastAPI(
    title="Botvue",
    version="0.1.0",
    summary="Reports what a website serves to AI crawlers that it does not serve to people.",
    description=(
        "Fetches one URL as a browser, as Googlebot, and as each major AI crawler, then "
        "compares the responses.\n\n"
        "Three behaviours are reported. A site may **refuse** a crawler with a 4xx, which is "
        "honest and needs no action. It may **soft-block**: answer HTTP 200 with a page "
        "carrying almost none of the content, so the agent believes it read the article when "
        "it read nothing. Or it may **substitute**: serve the crawler content the human "
        "version does not contain.\n\n"
        "Googlebot is the control. Existing cloaking checkers compare Googlebot against a "
        "browser, so a site that treats Googlebot normally and AI crawlers differently is "
        "invisible to them."
    ),
)

WEB = Path(__file__).resolve().parents[1] / "apps" / "web"

attestor = QueueAttestor()
verifier, terms = build_payment_layer()


class CheckRequest(BaseModel):
    url: str = Field(description="Absolute URL to check.",
                     examples=["https://www.houstonchronicle.com/"])
    fresh: bool = Field(default=False, description="Bypass the response cache and refetch.")


class CheckResponse(BaseModel):
    url: str
    domain: str
    decision: str = Field(description="block, flag or pass. `block` means the crawler "
                                      "response would be mistaken for the page.")
    verdict: str = Field(description="soft-blocked, substituted, crawler-only-text, "
                                     "refused, format-variant or clean.")
    rule: str | None = Field(description="Which test produced the verdict.")
    explanation: str = Field(description="What this means, in plain language.")
    reason: str
    human_words: int = Field(description="Words of visible text a browser receives.")
    word_ratio: dict = Field(description="Words each crawler received, as a fraction of "
                                         "what the browser received.")
    control_similarity: float | None = Field(description="How closely Googlebot matched the "
                                                         "browser. 1.0 is identical.")
    statuses: dict
    evidence: dict = Field(description="SHA-256 of each response body, so the finding can "
                                       "be rechecked later or by someone else.")
    attestation: dict
    payment: dict = Field(default_factory=dict)
    elapsed_ms: int


def normalise_url(raw: str) -> tuple[str, str | None]:
    """Returns the URL to fetch, or an explanation of why it cannot be one.

    Someone typing a bare domain into the box means a website, so that is accepted and
    completed. Anything that is not a web address is refused outright rather than answered
    with an empty result.
    """
    candidate = (raw or "").strip()
    if not candidate:
        return "", "No URL given."
    if "://" not in candidate:
        candidate = "https://" + candidate

    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return "", f"Only http and https can be checked, not {parsed.scheme!r}."
    host = parsed.hostname or ""
    if "." not in host or host.startswith(".") or host.endswith("."):
        return "", f"{raw!r} is not a web address. Try something like example.com."
    return candidate, None


def _to_response(result: CheckResult, payment: dict) -> CheckResponse:
    return CheckResponse(**{**result.__dict__, "payment": payment})


@app.get("/", include_in_schema=False)
def home() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/health", summary="Liveness and the user-agent matrix in use.")
def health() -> dict:
    topic = os.getenv("HEDERA_TOPIC_ID", "")
    network = os.getenv("HEDERA_NETWORK", "testnet")
    return {
        "status": "ok",
        "topicId": topic,
        "topicUrl": (
            f"https://{network}.mirrornode.hedera.com/api/v1/topics/{topic}/messages"
            if topic else ""
        ),
        "baseline": BASELINE,
        "control": CONTROL,
        "agents": list(AGENTS),
        "paymentRequired": not isinstance(verifier, OpenVerifier),
    }


@app.get("/agents", summary="The exact user-agent strings used, so a finding can be reproduced.")
def agent_strings() -> dict:
    return {"baseline": BASELINE, "control": CONTROL, "userAgents": dict(AGENTS)}


@app.get(
    "/evidence",
    summary="A frozen observation from the scan, for when a live fetch is not possible.",
    description="Live fetches can be rate-limited, and a publisher can change its "
                "configuration at any time. This returns what was recorded on the date of "
                "the scan, clearly marked as such.",
)
def evidence(domain: str) -> JSONResponse:
    manifest_path = Path(__file__).resolve().parents[1] / "evidence" / "manifest.json"
    if not manifest_path.exists():
        return JSONResponse(status_code=404, content={"error": "no evidence archive"})

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    wanted = domain.lower().removeprefix("www.")
    for record in manifest["properties"]:
        if record["domain"].lower().removeprefix("www.") == wanted:
            return JSONResponse(
                content={
                    "cached": True,
                    "capturedAt": manifest["captured_at"],
                    "note": manifest["note"],
                    # The archive stores observations, not prose. The reader still needs to
                    # be told what the verdict means.
                    "explanation": EXPLANATION.get(record["verdict"], ""),
                    "decision": DECISION.get(record["verdict"], "pass"),
                    **record,
                }
            )
    return JSONResponse(
        status_code=404,
        content={"error": f"{domain} is not in the frozen evidence set"},
    )


@app.get("/terms", summary="What a paid call costs and how payment is verified.")
def payment_terms() -> dict:
    return terms.challenge()


@app.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
def llms_txt() -> str:
    # Served identically to every user-agent, which given the subject matter is the least
    # this project can do.
    return (
        "# Botvue\n\n"
        "> Reports what a website serves to AI crawlers that it does not serve to people.\n\n"
        "This file is served identically to every user-agent.\n\n"
        "## API\n"
        "- POST /check {\"url\": \"...\"} - check one URL\n"
        "- GET /terms - payment terms for a paid call\n"
        "- GET /openapi.json - full specification\n"
    )


@app.post(
    "/check",
    response_model=CheckResponse,
    summary="Check one URL for content served only to AI crawlers.",
    responses={402: {"description": "Payment required. The body describes what would satisfy it."}},
)
def check_url(
    req: CheckRequest,
    response: Response,
    x_payment: str | None = Header(default=None, alias="X-PAYMENT"),
) -> CheckResponse | JSONResponse:
    settlement = verifier.verify(x_payment, terms)
    if not settlement.ok:
        # 402 means 402 here. The point of the tool is that a status code should describe
        # what actually happened.
        return JSONResponse(
            status_code=402,
            content={**terms.challenge(), "error": settlement.reason},
        )

    target, problem = normalise_url(req.url)
    if problem:
        # A tool that reports on misleading status codes does not get to answer 200 to a
        # request it could not carry out.
        return JSONResponse(status_code=400, content={"error": problem, "url": req.url})

    result = check(target, fresh=req.fresh, attestor=attestor)

    if result.verdict in ("baseline-failed", "no-data"):
        return JSONResponse(
            status_code=502,
            content={
                "error": "Could not fetch this page as a browser, so there is nothing to "
                         "compare against.",
                "url": target,
                "statuses": result.statuses,
            },
        )

    response.headers["X-PAYMENT-RESPONSE"] = str(settlement.ok).lower()
    return _to_response(result, settlement.receipt())
