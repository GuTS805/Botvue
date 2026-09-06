# Botvue

See what the bots see.

Some websites serve different content to AI crawlers than they serve to people. The
substitution happens at the CDN edge, keyed on the `User-Agent` header, and the version
the machine receives is never rendered for a human visitor. Two documented cases exist:
one where the injected content is sponsored advertising, and one where it is an
instruction aimed at the agent itself.

Botvue fetches the same URL as a browser and as each major AI crawler, strips both
responses down to comparable text, and reports the blocks that appear in one version and
not the other.

## Why existing tools miss this

Cloaking detection is a mature field, but every mainstream checker compares **Googlebot**
against a human browser. At least one documented case deliberately excludes Googlebot from
the substitution, so those tools return a clean verdict on a page that is provably serving
different content to ClaudeBot, GPTBot and PerplexityBot.

Botvue treats Googlebot as a control rather than as the test case.

## Status

Early. The measurement pass that establishes how widespread this is has not finished yet,
and no numbers are published here until it has.

## Layout

    scanner/    fetch matrix, normalisation, block-level diff
    corpus/     URL lists and cached responses (not committed)

## Licence

MIT
