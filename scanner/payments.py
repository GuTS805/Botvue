"""x402 payment gating, settled on Hedera.

The protocol is: an unpaid request gets `402 Payment Required` and a description of what
would satisfy it; the caller pays, then retries carrying proof; the server checks that proof
against the ledger before serving.

Verification deliberately reads the **public mirror node**. The server needs no key and no
account access to confirm a payment — it asks the same public record the payer can check,
which means a caller never has to trust this service's accounting.

There is an irony worth stating out loud in the demo: this service answers 402 when it means
"pay me". Fourteen of the news properties in the evidence set answer 200 when they mean
"you may not read this". Returning the right status code is not a technical problem.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

TINYBAR_PER_HBAR = 100_000_000
DEFAULT_PRICE_TINYBAR = 1_000_000  # 0.01 HBAR
MIRROR = {
    "testnet": "https://testnet.mirrornode.hedera.com",
    "mainnet": "https://mainnet.mirrornode.hedera.com",
}
# A payment older than this cannot be presented as proof, so a single transaction cannot be
# replayed indefinitely even if the ledger of spent ids is lost.
MAX_PAYMENT_AGE_SECONDS = 3600


@dataclass
class PaymentTerms:
    recipient: str
    price_tinybar: int = DEFAULT_PRICE_TINYBAR
    network: str = "testnet"
    resource: str = "/check"

    def challenge(self) -> dict:
        """The body returned with a 402. Everything the caller needs to pay, and nothing
        that requires asking us a second question."""
        return {
            "x402Version": 1,
            "accepts": [
                {
                    "scheme": "exact",
                    "network": f"hedera-{self.network}",
                    "asset": "HBAR",
                    "amount": str(self.price_tinybar),
                    "amountFormatted": f"{self.price_tinybar / TINYBAR_PER_HBAR:.8f} HBAR",
                    "payTo": self.recipient,
                    "resource": self.resource,
                    "description": "One URL check: fetches the page as a browser and as each "
                                   "AI crawler, and reports what differs.",
                    "maxTimeoutSeconds": MAX_PAYMENT_AGE_SECONDS,
                }
            ],
            "proof": {
                "header": "X-PAYMENT",
                "encoding": "base64(json)",
                "fields": {"transactionId": "the Hedera transaction id of your payment"},
            },
            "verifiedAgainst": f"{MIRROR[self.network]}/api/v1/transactions",
        }


@dataclass
class PaymentResult:
    ok: bool
    reason: str = ""
    transaction_id: str = ""
    paid_tinybar: int = 0

    def receipt(self) -> dict:
        return {
            "settled": self.ok,
            "transactionId": self.transaction_id,
            "paidTinybar": self.paid_tinybar,
            "reason": self.reason,
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


@dataclass
class MirrorNodeVerifier:
    """Confirms a payment by reading the public mirror node.

    Checks that the transaction succeeded, credited the expected account with at least the
    asking price, and is recent. Spent transaction ids are remembered so one payment buys
    one call.
    """

    spent: set[str] = field(default_factory=set)
    timeout: float = 12.0

    def verify(self, proof: str | None, terms: PaymentTerms) -> PaymentResult:
        if not proof:
            return PaymentResult(False, "no payment presented")

        payload = _decode(proof)
        tx_id = str(payload.get("transactionId") or payload.get("txId") or "").strip()
        if not tx_id:
            return PaymentResult(False, "payment proof has no transactionId")
        if tx_id in self.spent:
            return PaymentResult(False, "this payment has already been used", tx_id)

        # Clients hand out ids as `0.0.x@secs.nanos`; the mirror node addresses the same
        # transaction as `0.0.x-secs-nanos`. Sending the wrong one is a 400, not a 404, so
        # it reads as a broken payment rather than a missing one.
        lookup = _mirror_id(tx_id)

        url = f"{MIRROR.get(terms.network, MIRROR['testnet'])}/api/v1/transactions/{lookup}"
        try:
            response = httpx.get(url, timeout=self.timeout)
        except Exception as exc:
            return PaymentResult(False, f"could not reach the mirror node: {exc}", tx_id)

        if response.status_code == 404:
            return PaymentResult(False, "transaction not found on the mirror node", tx_id)
        if response.status_code != 200:
            return PaymentResult(False, f"mirror node returned {response.status_code}", tx_id)

        transactions = response.json().get("transactions") or []
        if not transactions:
            return PaymentResult(False, "transaction not found", tx_id)

        tx = transactions[0]
        if tx.get("result") != "SUCCESS":
            return PaymentResult(False, f"transaction did not succeed: {tx.get('result')}", tx_id)

        credited = sum(
            t.get("amount", 0)
            for t in tx.get("transfers", [])
            if t.get("account") == terms.recipient and t.get("amount", 0) > 0
        )
        if credited < terms.price_tinybar:
            return PaymentResult(
                False,
                f"paid {credited} tinybar, price is {terms.price_tinybar}",
                tx_id,
                credited,
            )

        consensus = float(tx.get("consensus_timestamp", 0) or 0)
        if consensus and time.time() - consensus > MAX_PAYMENT_AGE_SECONDS:
            return PaymentResult(False, "payment is too old to present as proof", tx_id, credited)

        self.spent.add(tx_id)
        return PaymentResult(True, "verified against the mirror node", tx_id, credited)


class OpenVerifier:
    """Lets every request through, for local development and for the free tier."""

    def verify(self, proof: str | None, terms: PaymentTerms) -> PaymentResult:
        return PaymentResult(True, "payment not required")


def terms_from_env() -> PaymentTerms:
    return PaymentTerms(
        recipient=os.getenv("HEDERA_ACCOUNT_ID", ""),
        price_tinybar=int(os.getenv("BOTVUE_PRICE_TINYBAR", DEFAULT_PRICE_TINYBAR)),
        network=os.getenv("HEDERA_NETWORK", "testnet"),
    )


def verifier_from_env() -> PaymentVerifier:
    """Paid mode needs an account to be paid; without one the gate would reject every
    caller for a reason they cannot fix, so it stays open instead."""
    if os.getenv("BOTVUE_REQUIRE_PAYMENT", "").lower() in ("1", "true", "yes"):
        if not os.getenv("HEDERA_ACCOUNT_ID"):
            raise RuntimeError(
                "BOTVUE_REQUIRE_PAYMENT is set but HEDERA_ACCOUNT_ID is not — there is no "
                "account to pay."
            )
        return MirrorNodeVerifier()
    return OpenVerifier()
