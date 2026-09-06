# Evidence

A frozen record of what was served, captured 2026-09-06.

These responses carry `cache-control: no-store` and a CDN rule can be changed in an
afternoon. Once it is changed there is nothing left to point at, so this directory records
what was observed while it was still observable.

## What is here

- `manifest.json` — every property where a crawler received something a browser did not.
  For each user-agent: status code, byte length, SHA-256 of the body, word count of the
  visible text, and the response headers that describe delivery.
- `stubs/` — the verbatim bodies that were served to AI crawlers, named by the first 12
  characters of their SHA-256.

## What it says

Two bodies were served **byte-identically across many properties**:

| SHA-256 (first 12) | Bytes | Properties |
|---|---|---|
| `32ed63159c77` | 3,036 | 23 |
| `ad3a87823990` | 161 | 14 |

The same body on twenty-three domains is one configuration, not twenty-three decisions.

## Verifying a record

Every claim here is a hash of a body that anyone can fetch for themselves:

    curl -s -H 'User-Agent: Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; +https://openai.com/gptbot)' \
      https://www.houstonchronicle.com/ | sha256sum

Compare against the `gptbot` entry for that domain in `manifest.json`. The browser and
Googlebot bodies can be checked the same way with their own user-agent strings.

If a publisher's configuration has since changed, the hashes will no longer match — which is
the reason this directory exists.

## Scope of the claim

The manifest records observed facts: these bytes were served to this user-agent at this
timestamp. It makes no claim about intent, and none should be read into it. A response can
be a deliberate policy, a vendor default, or a misconfiguration; nothing here distinguishes
between those, and the harm it measures does not depend on which it is — an agent receiving
`HTTP 200` cannot tell any of them from success.

Corrections are welcome. If a record here is wrong, or a response is explained by something
this capture could not see, open an issue and it will be annotated rather than deleted.
