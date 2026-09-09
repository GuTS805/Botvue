"""MCP adapter for the check.

The second adapter promised in `gateway.py`'s own docstring: same `scanner.service.check`,
same verdict logic, a different transport. An agent talking MCP gets exactly what a browser
hitting `/check/preview` gets — no separate code path to drift out of sync with the one this
project's own findings are built on.

Free and unattested, deliberately: an MCP call has no payment leg the way `/check` does, and
writing every anonymous MCP query to the Hedera consensus topic would turn a demonstration
tool into unbounded, uncontrolled evidence. `NullAttestor` matches the web page's own
`/check/preview` path for the same reason.

    python -m scanner.mcp_server

Point an MCP client (Claude Desktop, an agent framework, `mcp dev`) at this command over
stdio. No API key, no payment -- this is the free path, same as the page's own tool.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from .agents import AGENTS, BASELINE, CONTROL
from .attest import NullAttestor
from .service import check, normalise_url

mcp = MCPServer(
    "botvue",
    title="Botvue",
    instructions=(
        "Checks whether a URL serves AI crawlers something different from what a browser "
        "and Googlebot receive. Fetches the URL six ways -- as a browser, as Googlebot, "
        "and as four real AI crawler user-agents -- and reports whether any of them got "
        "an empty page dressed as success, or text a browser never receives."
    ),
)


@mcp.tool()
def check_url(url: str, fresh: bool = False) -> dict:
    """Check one URL for content served only to AI crawlers.

    Fetches the page as a browser, as Googlebot, and as four AI crawlers (ClaudeBot, GPTBot,
    OAI-SearchBot, PerplexityBot), then compares what came back. Results are cached; pass
    fresh=true to force a live refetch.

    Returns a decision (`block`, `flag`, or `pass`), the verdict class, a plain-language
    explanation, per-crawler word ratios against the browser baseline, a SHA-256 of every
    response body so the finding can be independently re-checked, and -- when the verdict
    rests on specific text -- the actual quoted sentences, not a description of them.
    """
    target, problem = normalise_url(url)
    if problem:
        return {"error": problem, "url": url}

    result = check(target, fresh=fresh, attestor=NullAttestor())
    if result.verdict in ("baseline-failed", "no-data"):
        return {
            "error": "Could not fetch this page as a browser, so there is nothing to "
                     "compare against.",
            "url": target,
            "statuses": result.statuses,
        }

    return {
        "url": result.url,
        "domain": result.domain,
        "decision": result.decision,
        "verdict": result.verdict,
        "explanation": result.explanation,
        "reason": result.reason,
        "human_words": result.human_words,
        "word_ratio": result.word_ratio,
        "control_similarity": result.control_similarity,
        "statuses": result.statuses,
        "evidence_sha256": result.evidence,
        "worst_classification": result.worst,
        "samples": result.samples,
    }


@mcp.tool()
def fetch_url(url: str) -> dict:
    """Drop-in replacement for a generic fetch tool. Returns page content only when it
    matches what a human browser would see.

    Every other fetch-tool MCP server competes on reliability -- did the page load, was
    it parsed cleanly. None of them ask whether what loaded is what a human would see.
    This one checks that first: if AI crawlers receive different content than a browser
    (soft-blocked, substituted, or carrying machine-only text), content is withheld and a
    warning explains why, instead of silently handing back a page that was never really
    served to an agent.

    Point an MCP client's fetch tool at this instead of a generic one, and every read
    gets the same authenticity check every finding in this project goes through.
    """
    target, problem = normalise_url(url)
    if problem:
        return {"status": "error", "reason": problem, "content": None, "warning": None}

    result = check(target, attestor=NullAttestor())
    if result.verdict in ("baseline-failed", "no-data"):
        return {
            "status": "error",
            "reason": "Could not fetch this page as a browser, so there is nothing to "
                      "compare against.",
            "content": None,
            "warning": None,
        }

    if result.decision == "block":
        return {
            "status": "blocked",
            "reason": result.explanation,
            "verdict": result.verdict,
            "content": None,
            "warning": "This page returns different content to AI crawlers than to "
                       "browsers. Content withheld -- see 'reason'.",
        }

    if result.decision == "flag":
        return {
            "status": "flagged",
            "reason": result.explanation,
            "verdict": result.verdict,
            "content": result.browser_content,
            "warning": "Some content on this page was written for machines only.",
        }

    return {
        "status": "clean",
        "reason": None,
        "verdict": result.verdict,
        "content": result.browser_content,
        "warning": None,
    }


@mcp.tool()
def list_agents() -> dict:
    """The exact user-agent strings this service sends, so a finding can be reproduced
    with a plain curl or fetch rather than taken on trust."""
    return {"baseline": BASELINE, "control": CONTROL, "userAgents": dict(AGENTS)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
