# Setup

Two parts. The scanner and the public page run with no accounts at all. Only the payment
and attestation layer needs Hedera credentials.

If you only want to see it work, do part 1 and stop.

---

## 1. Scanner and public page — no accounts needed

Requires Python 3.11 or newer.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS / Linux
```

`brotli` and `zstandard` are in there and are not optional. Without them a site serving a
compressed response returns bytes that decode to nonsense, and two nonsense bodies compare
as total divergence — which reads as a spectacular finding and is not one. The fetch layer
now refuses an undecodable body rather than comparing it, but the codecs are what let it
read the page at all.

Run it:

```bash
.venv/Scripts/python -m uvicorn scanner.gateway:app --port 8000
```

Open <http://localhost:8000>. Click any of the four example buttons. Check that the health
endpoint agrees with what you expect:

```bash
curl -s localhost:8000/health
# {"status":"ok","baseline":"chrome","control":"googlebot",...,"paymentRequired":false}
```

### Re-running the scan

```bash
.venv/Scripts/python -m scanner.corpus --refresh    # build the url list
.venv/Scripts/python -m scanner.scan --chains       # newspaper chains
.venv/Scripts/python -m scanner.scan --articles     # publisher article pages
.venv/Scripts/python -m scanner.grade               # verdicts, from cache
.venv/Scripts/python -m scanner.archive             # refresh evidence/
```

Every response is cached under `corpus/cache/`, so re-grading costs no requests. Check a
single URL without the service:

```bash
.venv/Scripts/python -m scanner.probe https://www.houstonchronicle.com/
```

---

## 2. Hedera credentials — needed for payment and attestation

### Get an account

1. Go to <https://portal.hedera.com> and sign up.
2. Create a **testnet** account. Testnet HBAR is free and the portal will top it up again.
3. Copy two things from the dashboard:
   - **Account ID** — looks like `0.0.10393175`
   - **HEX Encoded Private Key** — starts with `0x`

An ECDSA account (the portal's default) shows a *HEX Encoded Private Key*; an ED25519
account shows a *DER Encoded Private Key*. Either works, with or without the `0x` prefix —
paste the whole value. Do not use the **EVM Address**: that is a public address, not a key.

An ECDSA account is the better default here, since it is the one compatible with smart
contract tooling.

### Configure

```bash
cd packages/chain
cp .env.example .env
```

Fill in `.env`:

```
HEDERA_ACCOUNT_ID=0.0.10393175
HEDERA_PRIVATE_KEY=0x2f96...
HEDERA_NETWORK=testnet
HEDERA_TOPIC_ID=
```

### Create the consensus topic

```bash
npm install
npm run create-topic
```

It prints a topic id. Paste it into `.env` as `HEDERA_TOPIC_ID`.

The topic is created **without a submit key**, so anyone can append to it. That is
deliberate. A topic only this project can write to would prove only that this project wrote
something down, which a database already does. What matters is that entries cannot be
removed or back-dated, and that a publisher who disputes a finding can answer on the same
ordered record.

### Write the findings

```bash
npm run attest              # the frozen evidence set, soft-blocks first
npm run submit-pending      # anything the running gateway has queued
```

### Read them back

```bash
npm run verify -- <topicId>
npm run verify -- <topicId> --live
```

`verify` uses **no credentials**. It reads the public mirror node and re-fetches the pages
directly from the publishers, so nothing about the result depends on trusting this project.
With `--live` it compares each recorded hash against the site as it is right now, and says
`CHANGED` if a publisher has altered its configuration since.

---

## 3. Paid mode and the demo agent

The agent needs the same credentials, because it is the one paying.

```bash
cp packages/chain/.env packages/agent/.env
cd packages/agent && npm install
```

Start the gateway with payment required:

```bash
BOTVUE_REQUIRE_PAYMENT=1 HEDERA_ACCOUNT_ID=0.0.12345 \
  .venv/Scripts/python -m uvicorn scanner.gateway:app --port 8000
```

Confirm it actually took effect before trusting a run:

```bash
curl -s localhost:8000/health   # paymentRequired must be true
```

Then:

```bash
cd packages/agent
npx tsx src/run.ts https://www.houstonchronicle.com/ --service http://localhost:8000
```

The agent reads the OpenAPI document to find the endpoint, receives a 402, pays, retries
with the transaction id as proof, and then re-checks that payment against the mirror node
itself. Nothing about the endpoint, price or recipient is hardcoded.

Price defaults to 0.01 HBAR. Change it with `BOTVUE_PRICE_TINYBAR`.

---

## When something looks wrong

**Every crawler seems to receive completely different content.** Check `brotli` and
`zstandard` are installed. Undecoded bodies compare as total divergence and look like a
spectacular finding.

**A paid run passes when it should not.** Check `paymentRequired` on `/health` before
believing the result. A server left running on the port will happily answer instead of the
one you just started, and on Windows `pkill` does not stop it. Use:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8000 |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```

**`transaction not found on the mirror node`.** Transaction ids are handed out as
`0.0.x@secs.nanos` but addressed on the mirror node as `0.0.x-secs-nanos`. The code converts
this; if you are querying by hand, convert it yourself. The wrong format returns 400, not
404, so it reads as a malformed payment rather than a missing one.

**`HEDERA_PRIVATE_KEY could not be parsed`.** Copy the whole *HEX Encoded Private Key* (or
*DER Encoded Private Key* on an ED25519 account). The `0x` prefix is fine. The **EVM
Address** is not a key and will not work.

**A check on a real site times out.** Cloudflare and Fastly rate-limit repeated probes.
Results are cached, and the public page falls back to the recorded observation with the date
shown.
