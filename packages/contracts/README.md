# contracts

`FindingRegistry` — the on-chain record of pages observed serving different content to AI
crawlers than to a browser.

Findings are emitted as events and indexed by the subgraph. Only the evidence hash, domain
hash and metadata are kept in storage; the URL and user-agent live in the event, which keeps
a finding to three storage slots.

Two properties matter:

- **Records cannot be removed.** The responses this tool observes are served with
  `cache-control: no-store`, so nothing else survives to check against once a page changes.
- **Anyone can dispute a finding.** `dispute()` is unpermissioned and annotates the record
  rather than deleting it, so a publisher can answer an observation without being able to
  erase it.

Writing is limited to addresses on the reporter allowlist, so the index cannot be spammed.

## Deploying

Targets a network supported by Subgraph Studio (Base Sepolia or Arbitrum Sepolia) rather
than Hedera, which the decentralised Graph network does not index.

    forge script script/Deploy.s.sol:Deploy \
      --rpc-url "$RPC_URL" --private-key "$PRIVATE_KEY" --broadcast

## Tests

    forge test -vv
