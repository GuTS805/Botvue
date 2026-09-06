/**
 * Pays a 402 challenge on Hedera and returns proof.
 *
 * The proof is just a transaction id. It is not a signature or a receipt this agent issues —
 * it is a pointer into the public ledger, so the service verifies it by reading the same
 * mirror node the agent could read. Neither side has to trust the other's account of what
 * happened.
 */

import {
  AccountId,
  Client,
  Hbar,
  HbarUnit,
  PrivateKey,
  TransferTransaction,
} from "@hashgraph/sdk";
import "dotenv/config";

import type { Terms } from "./discover.js";

export interface Payment {
  transactionId: string;
  amountTinybar: number;
  payTo: string;
  network: string;
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

export function payerClient(network: string): { client: Client; accountId: AccountId } {
  const id = process.env.HEDERA_ACCOUNT_ID;
  const key = process.env.HEDERA_PRIVATE_KEY;
  if (!id || !key) {
    throw new Error(
      "HEDERA_ACCOUNT_ID and HEDERA_PRIVATE_KEY must be set to pay. Copy .env.example " +
        "to .env and fill them in from portal.hedera.com.",
    );
  }
  const accountId = AccountId.fromString(id);
  const client = network.includes("mainnet") ? Client.forMainnet() : Client.forTestnet();
  client.setOperator(accountId, parseKey(key));
  return { client, accountId };
}

export async function pay(terms: Terms): Promise<Payment> {
  if (terms.scheme !== "exact") {
    throw new Error(`unsupported payment scheme: ${terms.scheme}`);
  }
  if (terms.asset !== "HBAR") {
    throw new Error(`unsupported asset: ${terms.asset}`);
  }

  const amount = Number(terms.amount);
  const { client, accountId } = payerClient(terms.network);
  const recipient = AccountId.fromString(terms.payTo);

  const response = await new TransferTransaction()
    .addHbarTransfer(accountId, Hbar.from(-amount, HbarUnit.Tinybar))
    .addHbarTransfer(recipient, Hbar.from(amount, HbarUnit.Tinybar))
    .setTransactionMemo("botvue: one url check")
    .execute(client);

  // Wait for consensus before presenting the id: the service looks the transaction up on
  // the mirror node, and an id that has not reached consensus is not there yet.
  await response.getReceipt(client);
  client.close();

  return {
    transactionId: response.transactionId.toString(),
    amountTinybar: amount,
    payTo: terms.payTo,
    network: terms.network,
  };
}

export function proofHeader(payment: Payment): string {
  return Buffer.from(
    JSON.stringify({ transactionId: payment.transactionId }),
    "utf8",
  ).toString("base64");
}
