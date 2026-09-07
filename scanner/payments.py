"""x402 payment gating, settled through the Blocky402 facilitator on Hedera.

The protocol: an unpaid request gets `402 Payment Required` describing what would satisfy
it, including the facilitator's fee-payer account (`extra.feePayer`, read live from
Blocky402's `/supported` endpoint — never hardcoded, since a stale copy of that account id
would fail silently and look identical to every other rejection). The caller signs a
transfer as the paying account and hands over the frozen, unsubmitted bytes; the facilitator
adds its own signature as fee payer and broadcasts.

Settlement is checked twice, from two sources that do not trust each other:

1. Blocky402's `/settle` response is authoritative for the qualification requirement.
2. The public Hedera mirror node is read independently afterwards, using the settled
   transaction id Blocky402 returned. Two sources agreeing on one truth is stronger than
   either alone, and it is the same thesis the rest of this project rests on — a disagreement
   is not hidden, it is a recorded, demo-visible event (see `CrossCheck`).

There is an irony worth stating out loud in the demo: this service answers 402 when it means
"pay me". Fourteen of the news properties in the evidence set answer 200 when they mean
"you may not read this". Returning the right status code is not a technical problem.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

TINYBAR_PER_HBAR = 100_000_000
DEFAULT_PRICE_TINYBAR = 1_000_000  # 0.01 HBAR
# The Hedera x402 exact scheme identifies native HBAR by this HTS-style asset id ("0.0.0"),
# not the string "HBAR" — confirmed against @x402/hedera's own HBAR_ASSET_ID constant. Every
# other asset is an actual HTS token id (e.g. USDC), so this is the one reserved value.
HBAR_ASSET_ID = "0.0.0"

# The only place a Hedera network name becomes a CAIP-2 id, a facilitator URL, or a mirror
# node host. Every other line in this module reaches these through `terms.network`
# ("testnet" / "mainnet") — never repeats the literal string.
NETWORK_CAIP2 = {"testnet": "hedera:testnet", "mainnet": "hedera:mainnet"}
FACILITATOR_BASE = {
    "testnet": "https://api.testnet.blocky402.com",
    "mainnet": "https://api.blocky402.com/v1",
}
MIRROR = {
    "testnet": "https://testnet.mirrornode.hedera.com",
    "mainnet": "https://mainnet.mirrornode.hedera.com",
}

# A payment older than this cannot be presented as proof, so a single transaction cannot be
# replayed indefinitely even if the in-memory ledger of spent ids is lost on restart.
MAX_PAYMENT_AGE_SECONDS = 3600


def caip2_network(network: str) -> str:
    return NETWORK_CAIP2.get(network, NETWORK_CAIP2["testnet"])


def facilitator_base(network: str) -> str:
    return FACILITATOR_BASE.get(network, FACILITATOR_BASE["testnet"])


def mirror_base(network: str) -> str:
    return MIRROR.get(network, MIRROR["testnet"])


def fetch_fee_payer(network: str, timeout: float = 8.0) -> str:
    """Reads the facilitator's fee-payer account from its own `/supported` endpoint.

    A client's transaction must be built with this exact account as the fee payer, or
    Blocky402 will not recognise itself as the sponsor and settlement fails. Hardcoding a
    copy of this value would be the same silent-wrong-network failure mode this project
    spends the rest of its time measuring in other people's services.

    The live shape is `{"kinds": [{"network": "hedera:testnet", "extra": {"feePayer": ...}},
    ...], "signers": {"hedera:*": [...], ...}}` — one entry per network/scheme Blocky402
    supports, not a single flat object. `kinds` is checked first since it is scoped to the
    exact network asked for; `signers["hedera:*"]` is the fallback.
    """
    caip2 = caip2_network(network)
    url = f"{facilitator_base(network)}/supported"
    response = httpx.get(url, timeout=timeout)
    response.raise_for_status()
    data = response.json()

    for kind in data.get("kinds") or []:
        if kind.get("network") == caip2:
            fee_payer = (kind.get("extra") or {}).get("feePayer")
            if fee_payer:
                return fee_payer

    for key, accounts in (data.get("signers") or {}).items():
        if key.startswith("hedera") and accounts:
            return accounts[0]

    raise RuntimeError(
        f"Blocky402's /supported response had no Hedera fee-payer account for "
        f"{caip2}: {data}"
    )


@dataclass
class PaymentTerms:
    recipient: str
    price_tinybar: int = DEFAULT_PRICE_TINYBAR
    network: str = "testnet"
    resource: str = "/check"
    fee_payer: str = ""

    def requirements(self) -> dict:
        """PaymentRequirements (x402 v2), matching the Hedera exact scheme exactly —
        this is what both Blocky402 and the signing client parse."""
        return {
            "scheme": "exact",
            "network": caip2_network(self.network),
            "amount": str(self.price_tinybar),
            "asset": HBAR_ASSET_ID,
            "payTo": self.recipient,
            "maxTimeoutSeconds": MAX_PAYMENT_AGE_SECONDS,
            "extra": {"feePayer": self.fee_payer} if self.fee_payer else None,
        }

    def challenge(self) -> dict:
        """The body returned with a 402. Everything a client needs to build and sign a
        payment, and nothing that requires asking us a second question."""
        return {
            "x402Version": 2,
            "resource": {"url": self.resource},
            "accepts": [self.requirements()],
            "proof": {
                "header": "X-PAYMENT",
                "encoding": "base64(json PaymentPayload, x402 v2)",
            },
            "facilitator": facilitator_base(self.network),
            "crossCheckedAgainst": f"{mirror_base(self.network)}/api/v1/transactions",
        }


@dataclass
class CrossCheck:
    """Whether the public mirror node independently confirms what Blocky402 reported."""

    agrees: bool
    detail: str
    mirror_status: str | None = None
    mirror_credited_tinybar: int | None = None

    def to_dict(self) -> dict:
        return {
            "agrees": self.agrees,
            "detail": self.detail,
            "mirrorStatus": self.mirror_status,
            "mirrorCreditedTinybar": self.mirror_credited_tinybar,
        }


@dataclass
class PaymentResult:
    ok: bool
    reason: str = ""
    transaction_id: str = ""
    paid_tinybar: int = 0
    cross_check: CrossCheck | None = None

    def receipt(self) -> dict:
        return {
            "settled": self.ok,
            "transactionId": self.transaction_id,
            "paidTinybar": self.paid_tinybar,
            "reason": self.reason,
            "crossCheck": self.cross_check.to_dict() if self.cross_check else None,
        }


class PaymentVerifier(Protocol):
    def verify(self, proof: str | None, terms: PaymentTerms) -> PaymentResult: ...


def _mirror_id(tx_id: str) -> str:
    """`0.0.4@1757000000.000000000` -> `0.0.4-1757000000-000000000`."""
    if "@" in tx_id:
        account, _, stamp = tx_id.partition("@")
        return f"{account}-{stamp.replace('.', '-')}"
    return tx_id


def _decode(proof: str) -> dict:
    try:
        return json.loads(base64.b64decode(proof).decode("utf-8"))
    except Exception:
        try:
            return json.loads(proof)
        except Exception:
            return {}


def _lookup_mirror_transaction(
    tx_id: str, network: str, timeout: float, attempts: int, backoff: float
) -> dict | None:
    """A transaction reaches consensus before the mirror node has indexed it, so a check
    made immediately after settlement can miss it. A short retry absorbs that propagation
    delay rather than reporting a real payment as unfindable."""
    lookup = _mirror_id(tx_id)
    url = f"{mirror_base(network)}/api/v1/transactions/{lookup}"
    for attempt in range(attempts):
        try:
            response = httpx.get(url, timeout=timeout)
        except Exception:
            response = None
        if response is not None and response.status_code == 200:
            transactions = response.json().get("transactions") or []
            if transactions:
                return transactions[0]
        if attempt < attempts - 1:
            time.sleep(backoff * (attempt + 1))
    return None


@dataclass
class Blocky402Verifier:
    """Settles payment through Blocky402, then cross-checks the settlement independently
    against the public Hedera mirror node.

    Every call to the facilitator can fail in ways that are not "the payment was bad": it can
    be slow, unreachable, or return something unparseable. None of those are treated as a
    rejected payment — they get their own clear reason, distinct from an actually-invalid or
    already-spent one.
    """

    network: str = "testnet"
    timeout: float = 10.0
    facilitator_attempts: int = 2
    facilitator_backoff: float = 2.0
    cross_check_attempts: int = 4
    cross_check_backoff: float = 1.5
    _seen_payloads: set[str] = field(default_factory=set)
    _settled_tx_ids: set[str] = field(default_factory=set)

    def _post(self, path: str, body: dict) -> tuple[int, dict | None, str]:
        url = f"{facilitator_base(self.network)}{path}"
        last_error = "the facilitator did not respond"
        for attempt in range(self.facilitator_attempts):
            try:
                response = httpx.post(url, json=body, timeout=self.timeout)
            except httpx.TimeoutException:
                last_error = "the facilitator did not respond in time"
                response = None
            except Exception as exc:
                last_error = f"could not reach the facilitator: {exc}"
                response = None

            if response is not None:
                try:
                    return response.status_code, response.json(), ""
                except Exception:
                    return response.status_code, None, "the facilitator's response was not valid JSON"

            if attempt < self.facilitator_attempts - 1:
                time.sleep(self.facilitator_backoff * (attempt + 1))
        return 0, None, last_error

    def verify(self, proof: str | None, terms: PaymentTerms) -> PaymentResult:
        if not proof:
            return PaymentResult(False, "no payment presented")

        # Reject a replayed proof before spending a facilitator call on it.
        digest = hashlib.sha256(proof.encode("utf-8")).hexdigest()
        if digest in self._seen_payloads:
            return PaymentResult(False, "this payment has already been used")

        payload = _decode(proof)
        if not payload:
            return PaymentResult(False, "payment proof is not valid base64-encoded JSON")
        transaction_bytes = (payload.get("payload") or {}).get("transaction")
        if not transaction_bytes or not isinstance(transaction_bytes, str):
            return PaymentResult(False, "payment payload is missing the signed transaction")

        body = {
            "x402Version": 2,
            "paymentPayload": payload,
            "paymentRequirements": terms.requirements(),
        }

        status, data, error = self._post("/verify", body)
        if error:
            return PaymentResult(False, error)
        if status != 200 or data is None:
            return PaymentResult(False, f"facilitator /verify returned HTTP {status}")
        if not data.get("isValid"):
            # A malformed or badly-signed transaction surfaces here as a facilitator-provided
            # reason, e.g. InvalidSignature — not as an exception from this service.
            return PaymentResult(
                False, data.get("invalidMessage") or data.get("invalidReason") or "payment is not valid"
            )

        self._seen_payloads.add(digest)

        status, data, error = self._post("/settle", body)
        if error:
            return PaymentResult(False, error)
        if status != 200 or data is None:
            return PaymentResult(False, f"facilitator /settle returned HTTP {status}")
        if not data.get("success"):
            return PaymentResult(
                False, data.get("errorMessage") or data.get("errorReason") or "settlement failed"
            )

        tx_id = str(data.get("transaction") or "")
        if tx_id and tx_id in self._settled_tx_ids:
            return PaymentResult(False, "this payment has already been used", tx_id)
        if tx_id:
            self._settled_tx_ids.add(tx_id)

        cross_check = self._verify_independently(tx_id, terms)
        reason = "settled through Blocky402"
        if not cross_check.agrees:
            reason += "; the mirror node disagrees, see crossCheck"

        return PaymentResult(True, reason, tx_id, terms.price_tinybar, cross_check)

    def _verify_independently(self, tx_id: str, terms: PaymentTerms) -> CrossCheck:
        if not tx_id:
            msg = "Blocky402 did not return a transaction id, so there is nothing to check."
            print(f"[payments] CROSS-CHECK DISAGREEMENT: {msg}")
            return CrossCheck(False, msg)

        tx = _lookup_mirror_transaction(
            tx_id, terms.network, self.timeout, self.cross_check_attempts, self.cross_check_backoff
        )
        if tx is None:
            msg = (
                "Blocky402 reported this payment settled, but the Hedera mirror node has "
                f"not indexed transaction {tx_id} yet."
            )
            print(f"[payments] CROSS-CHECK DISAGREEMENT: {msg}")
            return CrossCheck(False, msg)

        if tx.get("result") != "SUCCESS":
            msg = (
                f"Blocky402 reported this payment settled, but the mirror node shows "
                f"{tx.get('result')} for {tx_id}."
            )
            print(f"[payments] CROSS-CHECK DISAGREEMENT: {msg}")
            return CrossCheck(False, msg, tx.get("result"))

        credited = sum(
            t.get("amount", 0)
            for t in tx.get("transfers", [])
            if t.get("account") == terms.recipient and t.get("amount", 0) > 0
        )
        if credited < terms.price_tinybar:
            msg = (
                f"Blocky402 reported this payment settled, but the mirror node shows only "
                f"{credited} tinybar credited to {terms.recipient} (price is "
                f"{terms.price_tinybar})."
            )
            print(f"[payments] CROSS-CHECK DISAGREEMENT: {msg}")
            return CrossCheck(False, msg, tx.get("result"), credited)

        return CrossCheck(
            True,
            "Blocky402 and the Hedera mirror node agree the payment settled.",
            tx.get("result"),
            credited,
        )


@dataclass
class MirrorNodeVerifier:
    """Confirms a payment by reading the public mirror node alone, given a bare transaction
    id as proof. Kept for local development without facilitator credentials — this does not
    satisfy the "settled through Blocky402" qualification requirement and is not the verifier
    `build_payment_layer` selects when payment is required."""

    spent: set[str] = field(default_factory=set)
    timeout: float = 12.0
    lookup_attempts: int = 5
    lookup_backoff: float = 1.5

    def verify(self, proof: str | None, terms: PaymentTerms) -> PaymentResult:
        if not proof:
            return PaymentResult(False, "no payment presented")

        payload = _decode(proof)
        tx_id = str(payload.get("transactionId") or payload.get("txId") or "").strip()
        if not tx_id:
            return PaymentResult(False, "payment proof has no transactionId")
        if tx_id in self.spent:
            return PaymentResult(False, "this payment has already been used", tx_id)

        tx = _lookup_mirror_transaction(
            tx_id, terms.network, self.timeout, self.lookup_attempts, self.lookup_backoff
        )
        if tx is None:
            return PaymentResult(
                False,
                f"transaction not found on the mirror node after {self.lookup_attempts} attempts",
                tx_id,
            )
        if tx.get("result") != "SUCCESS":
            return PaymentResult(False, f"transaction did not succeed: {tx.get('result')}", tx_id)

        credited = sum(
            t.get("amount", 0)
            for t in tx.get("transfers", [])
            if t.get("account") == terms.recipient and t.get("amount", 0) > 0
        )
        if credited < terms.price_tinybar:
            return PaymentResult(
                False, f"paid {credited} tinybar, price is {terms.price_tinybar}", tx_id, credited
            )

        self.spent.add(tx_id)
        return PaymentResult(True, "verified against the mirror node", tx_id, credited)


class OpenVerifier:
    """Lets every request through, for local development and for the free tier."""

    def verify(self, proof: str | None, terms: PaymentTerms) -> PaymentResult:
        return PaymentResult(True, "payment not required")


def build_payment_layer() -> tuple[PaymentVerifier, PaymentTerms]:
    """The single place payment mode is decided. Building `PaymentTerms` when payment is
    required means one network call — reading Blocky402's fee-payer account — so this is
    done once here rather than repeated per request."""
    network = os.getenv("HEDERA_NETWORK", "testnet")
    recipient = os.getenv("BOTVUE_PAYEE_ACCOUNT_ID") or os.getenv("HEDERA_ACCOUNT_ID", "")
    price = int(os.getenv("BOTVUE_PRICE_TINYBAR", DEFAULT_PRICE_TINYBAR))

    if os.getenv("BOTVUE_REQUIRE_PAYMENT", "").lower() not in ("1", "true", "yes"):
        return OpenVerifier(), PaymentTerms(recipient=recipient, price_tinybar=price, network=network)

    if not recipient:
        raise RuntimeError(
            "BOTVUE_REQUIRE_PAYMENT is set but HEDERA_ACCOUNT_ID is not — there is no "
            "account to pay."
        )

    fee_payer = fetch_fee_payer(network)
    terms = PaymentTerms(
        recipient=recipient, price_tinybar=price, network=network, fee_payer=fee_payer
    )
    return Blocky402Verifier(network=network), terms
