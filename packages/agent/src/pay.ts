/**
 * Signs a Hedera transfer for the Blocky402 facilitator to submit.
 *
 * The agent never talks to Blocky402 directly and never broadcasts anything itself — x402's
 * Hedera "exact" scheme has the facilitator sponsor the network fee and submit the
 * transaction on the client's behalf. This only builds a transfer, signs it as the paying
 * account, and hands the frozen, unsubmitted bytes to the resource server as proof.
 *
 * The fee-payer account is never hardcoded here: it comes from `terms.feePayer`, which the
 * server read live from Blocky402's own `/supported` endpoint. Building the transaction with
 * the wrong fee payer is a silent-wrong-network failure — the transaction looks correct,
 * signs correctly, and the facilitator simply refuses to recognise itself as the sponsor.
 */

import {
  AccountId,
  Client,
  Hbar,
  HbarUnit,
  PrivateKey,
  TransactionId,
  TransferTransaction,
} from "@hashgraph/sdk";
import "dotenv/config";

import type { Terms } from "./discover.js";

// Hedera's x402 exact scheme identifies native HBAR by this HTS-style asset id, not the
// string "HBAR" — every other value here is an actual HTS token id (e.g. USDC).
const HBAR_ASSET_ID = "0.0.0";

export interface SignedPayment {
  /** base64-encoded, payer-signed, unsubmitted transaction bytes. */
  transactionBase64: string;
  amountTinybar: number;
  payTo: string;
  network: string;
  feePayer: string;
}

function parseKey(raw: string): PrivateKey {
  const key = raw.trim();
  // An ECDSA account's key comes from the portal 0x-prefixed, which no parser accepts.
  const bare = key.replace(/^0x/i, "");
  const looksDer = /^30[0-9a-f]{2}/i.test(bare) && bare.length > 70;
  const attempts = looksDer
    ? [() => PrivateKey.fromStringDer(bare)]
    : [
        () => PrivateKey.fromStringECDSA(bare),
        () => PrivateKey.fromStringED25519(bare),
        () => PrivateKey.fromStringDer(bare),
      ];
  for (const attempt of attempts) {
    try {
      return attempt();
    } catch {
      continue;
    }
  }
  throw new Error(
    'HEDERA_PRIVATE_KEY could not be parsed. Copy the "HEX Encoded Private Key" from ' +
      "portal.hedera.com.",
  );
}

function credentials(): { accountId: AccountId; privateKey: PrivateKey } {
  const id = process.env.HEDERA_ACCOUNT_ID;
  const key = process.env.HEDERA_PRIVATE_KEY;
  if (!id || !key) {
    throw new Error(
      "HEDERA_ACCOUNT_ID and HEDERA_PRIVATE_KEY must be set to pay. Copy .env.example " +
        "to .env and fill them in from portal.hedera.com.",
    );
  }
  return { accountId: AccountId.fromString(id), privateKey: parseKey(key) };
}

function hederaClient(network: string): Client {
  // "hedera:testnet" (CAIP-2, from the 402 body) -> pick the matching SDK client. Never
  // assume testnet — an unrecognised network id is a configuration problem to surface, not
  // silently default away.
  if (network === "hedera:mainnet") return Client.forMainnet();
  if (network === "hedera:testnet") return Client.forTestnet();
  throw new Error(`unsupported network: ${JSON.stringify(network)}`);
}

export async function sign(terms: Terms): Promise<SignedPayment> {
  if (terms.scheme !== "exact") {
    throw new Error(`unsupported payment scheme: ${terms.scheme}`);
  }
  if (terms.asset !== HBAR_ASSET_ID) {
    throw new Error(`unsupported asset: ${terms.asset}`);
  }
  if (!terms.feePayer) {
    throw new Error("terms.feePayer is empty — cannot name a fee payer on the transaction.");
  }

  const amount = Number(terms.amount);
  const { accountId, privateKey } = credentials();
  const recipient = AccountId.fromString(terms.payTo);
  const feePayer = AccountId.fromString(terms.feePayer);
  const client = hederaClient(terms.network);

  // The fee payer — not the caller's own account — owns the transaction id, because the
  // facilitator is the one who will actually broadcast and pay for this. The caller's
  // signature only authorises the debit from its own account.
  const frozen = new TransferTransaction()
    .setTransactionId(TransactionId.generate(feePayer))
    .addHbarTransfer(accountId, Hbar.from(-amount, HbarUnit.Tinybar))
    .addHbarTransfer(recipient, Hbar.from(amount, HbarUnit.Tinybar))
    .setTransactionMemo("botvue: one url check")
    .freezeWith(client);

  const signed = await frozen.sign(privateKey);
  const transactionBase64 = Buffer.from(signed.toBytes()).toString("base64");
  client.close();

  return {
    transactionBase64,
    amountTinybar: amount,
    payTo: terms.payTo,
    network: terms.network,
    feePayer: terms.feePayer,
  };
}

/** The x402 v2 PaymentPayload for the Hedera exact scheme, base64-encoded for X-PAYMENT. */
export function proofHeader(payment: SignedPayment, terms: Terms): string {
  const paymentPayload = {
    x402Version: 2,
    accepted: {
      scheme: terms.scheme,
      network: payment.network,
      amount: String(payment.amountTinybar),
      asset: terms.asset,
      payTo: payment.payTo,
      maxTimeoutSeconds: terms.maxTimeoutSeconds,
      extra: { feePayer: payment.feePayer },
    },
    payload: { transaction: payment.transactionBase64 },
  };
  return Buffer.from(JSON.stringify(paymentPayload), "utf8").toString("base64");
}
