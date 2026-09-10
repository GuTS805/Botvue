"""HTTP adapter for the check.

All logic lives in `scanner.service`; this module is transport and payment only, so a
second adapter — an MCP server, say — reuses the same code rather than reimplementing it.

    uvicorn scanner.gateway:app --reload
    curl -X POST localhost:8000/check -H 'content-type: application/json' \
      -d '{"url":"https://www.houstonchronicle.com/"}'
"""

from __future__ import annotations

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

import json
import os
import time
from pathlib import Path

from .agents import AGENTS, BASELINE, CONTROL
from .attest import NullAttestor, QueueAttestor
from .demo_page import render as render_demo_page
from .payments import OpenVerifier, build_payment_layer
from .service import DECISION, EXPLANATION, CheckResult, check, normalise_url

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

# The paid endpoint is the agent API. A person reading the page is not the agent, and
# asking them to sign a Hedera transfer to see what the tool does would leave the site
# describing a check nobody visiting it can run. So the page gets its own free path,
# bounded per caller because each check costs six outbound fetches.
PREVIEW_LIMIT = 15
PREVIEW_WINDOW = 3600
_preview_hits: dict[str, list[float]] = {}


def _caller(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _preview_allowed(caller: str) -> tuple[bool, int]:
    """Returns whether this caller may run another preview, and how many remain."""
    now = time.monotonic()
    hits = [t for t in _preview_hits.get(caller, []) if now - t < PREVIEW_WINDOW]

    if len(hits) >= PREVIEW_LIMIT:
        _preview_hits[caller] = hits
        return False, 0

    hits.append(now)
    _preview_hits[caller] = hits

    # Callers that have aged out entirely are dropped rather than kept forever, so a
    # long-running instance does not accumulate an entry per visitor it ever saw.
    if len(_preview_hits) > 2048:
        for key in [k for k, v in _preview_hits.items() if not v or now - v[-1] > PREVIEW_WINDOW]:
            _preview_hits.pop(key, None)

    return True, PREVIEW_LIMIT - len(hits)


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
    worst: str = Field(default="COSMETIC",
                       description="Highest classification reached by any crawler-only "
                                   "block: COSMETIC, MACHINE_ONLY, PROMOTIONAL, "
                                   "POLICY_VIOLATION or PROMPT_INJECTION.")
    samples: list = Field(default_factory=list,
                          description="The crawler-only text this verdict rests on, quoted "
                                      "rather than described, so the caller can judge it.")
    attestation: dict
    payment: dict = Field(default_factory=dict,
                          description="Settlement receipt on a paid call: settled, "
                                      "transactionId, paidTinybar, reason, and crossCheck "
                                      "-- whether Hedera's public mirror node independently "
                                      "confirms what the facilitator reported. Empty on the "
                                      "free preview path, which never touches payment.")
    elapsed_ms: int


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


@app.get(
    "/evidence/all",
    summary="Every property in the frozen scan, not just the two examples on the homepage.",
    description="The full archive the scan produced, so a finding can be checked against the "
                "whole set rather than the handful chosen for the homepage.",
)
def evidence_all() -> dict:
    manifest_path = Path(__file__).resolve().parents[1] / "evidence" / "manifest.json"
    if not manifest_path.exists():
        return {"capturedAt": None, "note": "", "properties": []}

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    properties = []
    for record in manifest["properties"]:
        # Response headers are per-record evidence a reader can ask for by refetching the
        # page (see /evidence?domain=), not something the archive table renders — carrying
        # them here roughly doubles the payload for no reader-visible benefit.
        agents = {
            agent: {k: v for k, v in r.items() if k != "headers"}
            for agent, r in record.get("agents", {}).items()
        }
        properties.append({**record, "agents": agents, "decision": DECISION.get(record["verdict"], "pass")})
    return {
        "capturedAt": manifest["captured_at"],
        "note": manifest["note"],
        "properties": properties,
    }


@app.get(
    "/evidence/changelog",
    summary="Decisions that changed on a re-scan, not just what is true today.",
    description="The corpus is re-fetched on a schedule (see scanner/rescan.py), because "
                "these responses carry cache-control: no-store and a configuration can "
                "change between one run and the next. This lists domains whose decision — "
                "pass, flag, or block — moved since the previous run. Same-decision changes "
                "(a different verdict label with no different consequence for a caller) are "
                "not included; they are not something a reader needs to act on.",
)
def evidence_changelog(limit: int = 50) -> dict:
    changelog_path = Path(__file__).resolve().parents[1] / "evidence" / "changelog.json"
    if not changelog_path.exists():
        return {"entries": []}
    entries = json.loads(changelog_path.read_text(encoding="utf-8"))
    return {"entries": entries[: max(0, min(limit, len(entries)))]}


@app.get("/archive", include_in_schema=False)
def archive_page() -> FileResponse:
    return FileResponse(WEB / "archive.html")


@app.get("/judge", include_in_schema=False)
def judge_page() -> FileResponse:
    # A self-driving walkthrough for someone skimming many submissions: it advances on
    # its own, and every figure it shows is fetched from the same endpoints a person
    # clicking around the real page would hit -- not a slide deck with numbers typed in.
    return FileResponse(WEB / "judge.html")


@app.get("/og.png", include_in_schema=False)
def social_card() -> FileResponse:
    # Referenced by absolute URL from the page's og:image, so it has to be reachable
    # without the crawler that fetches it running any JavaScript.
    return FileResponse(WEB / "og.png", media_type="image/png")


@app.get("/logo-mark.png", include_in_schema=False)
def logo_mark() -> FileResponse:
    # The brand mark used in both page headers and as the favicon -- one file, one route,
    # so the two can never drift into two different icons.
    return FileResponse(WEB / "logo-mark.png", media_type="image/png")


@app.get("/terms", summary="What a paid call costs and how payment is verified.")
def payment_terms() -> dict:
    return terms.challenge()


@app.get(
    "/demo/injection-page",
    include_in_schema=False,
    summary="Synthetic capability demo — not a finding from the corpus.",
)
def demo_injection_page(user_agent: str | None = Header(default=None)) -> Response:
    # A page this project built and controls, to demonstrate the block path on an
    # unambiguous signal. Real corpus findings never contain an explicit instruction —
    # see scanner/demo_page.py for why that distinction matters.
    html = render_demo_page(user_agent or "")
    return Response(content=html, media_type="text/html")


@app.get(
    "/spec/classification.schema.json",
    summary="The crawler-only content taxonomy, as a JSON Schema.",
    description="The five severities scanner/classify.py sorts a crawler-only block "
                "into, published so another project measuring the same failure class "
                "can adopt the same vocabulary rather than inventing its own. See "
                "spec/README.md in the repository for how it's used and versioned.",
)
def classification_spec() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parents[1] / "spec" / "classification.schema.json",
        media_type="application/schema+json",
    )


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

    result = _scan(req, attestor)
    if isinstance(result, JSONResponse):
        return result

    response.headers["X-PAYMENT-RESPONSE"] = str(settlement.ok).lower()
    return _to_response(result, settlement.receipt())


def _scan(req: CheckRequest, using) -> CheckResult | JSONResponse:
    """The scan itself, or the reason it could not be run. Shared so the paid endpoint
    and the page's own preview cannot drift apart in what they measure."""
    target, problem = normalise_url(req.url)
    if problem:
        # A tool that reports on misleading status codes does not get to answer 200 to a
        # request it could not carry out.
        return JSONResponse(status_code=400, content={"error": problem, "url": req.url})

    result = check(target, fresh=req.fresh, attestor=using)

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
    return result


@app.post(
    "/check/preview",
    response_model=CheckResponse,
    include_in_schema=False,
    summary="The same check, unpaid and rate-limited, for the page's own tool.",
)
def check_preview(req: CheckRequest, request: Request) -> CheckResponse | JSONResponse:
    # Deliberately absent from the OpenAPI document. That document is what a Bazantic
    # gateway wraps and prices, and a free twin of the paid operation listed beside it
    # would make the gate decorative.
    allowed, remaining = _preview_allowed(_caller(request))
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={
                "error": f"This page runs {PREVIEW_LIMIT} free checks an hour per visitor. "
                         "The archive is not rate-limited, and /check takes payment.",
            },
        )

    # Anonymous previews are not queued for the consensus record. That record carries
    # findings this project stands behind, not every URL a visitor happened to type.
    result = _scan(req, NullAttestor())
    if isinstance(result, JSONResponse):
        return result

    return _to_response(result, {"mode": "preview", "remaining": remaining})
