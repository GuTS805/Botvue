"""User-agent matrix. Googlebot is the control: existing cloaking checkers only test it,
so divergence between Googlebot and the AI crawlers is the finding."""

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

AGENTS = {
    "chrome": CHROME,
    "claudebot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
        "ClaudeBot/1.0; +claudebot@anthropic.com)"
    ),
    "gptbot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
        "GPTBot/1.1; +https://openai.com/gptbot)"
    ),
    "oai-searchbot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
        "OAI-SearchBot/1.0; +https://openai.com/searchbot)"
    ),
    "perplexitybot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
        "PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)"
    ),
    "googlebot": (
        "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; "
        "Googlebot/2.1; +http://www.google.com/bot.html) Chrome/128.0.0.0 Safari/537.36"
    ),
}

BASELINE = "chrome"
CONTROL = "googlebot"

BROWSER_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)

# Variants isolate the switch mechanism: content negotiation vs UA-keyed edge substitution.
VARIANTS = {
    "default": {"accept": BROWSER_ACCEPT, "md_suffix": False},
    "accept-md": {"accept": "text/markdown,text/plain;q=0.9,*/*;q=0.8", "md_suffix": False},
    "path-md": {"accept": BROWSER_ACCEPT, "md_suffix": True},
}

# Headers a publisher's edge might use to mark a substituted response.
SIGNAL_HEADERS = (
    "x-mobian-format",
    "x-mobian-tokens",
    "x-mobian-registry-version",
    "x-mobian-impression",
    "cache-control",
    "vary",
    "content-type",
    "server",
    "via",
    "cf-cache-status",
    "x-served-by",
)


def headers_for(agent_key: str, variant_key: str = "default") -> dict:
    variant = VARIANTS[variant_key]
    return {
        "User-Agent": AGENTS[agent_key],
        "Accept": variant["accept"],
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }
