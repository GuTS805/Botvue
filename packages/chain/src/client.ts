import { AccountId, Client, PrivateKey } from "@hashgraph/sdk";
import "dotenv/config";

export interface ChainConfig {
  accountId: AccountId;
  privateKey: PrivateKey;
  network: "testnet" | "mainnet";
  topicId?: string;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(
      `${name} is not set. Copy .env.example to .env and fill in your Hedera testnet ` +
        `credentials from https://portal.hedera.com`,
    );
  }
  return value;
}

/**
 * The portal issues ED25519 and ECDSA keys in several encodings, and picking the wrong
 * parser fails at client init with an error that says nothing useful. Try each in turn
 * rather than making the operator work out which one they were given.
 */
function parseKey(raw: string): PrivateKey {
  const key = raw.trim();
  // The portal shows an ECDSA account's key as "HEX Encoded Private Key" with an 0x prefix,
  // which none of the parsers accept, so try the bare form too.
  const bare = key.replace(/^0x/i, "");
  // A DER string carries its own prefix and is longer than a raw 32-byte key. Matching on
  // that first keeps the SDK from warning about a lenient parse it did not need to make.
  const looksDer = /^30[0-9a-f]{2}/i.test(bare) && bare.length > 70;
  const attempts: Array<() => PrivateKey> = looksDer
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
    "HEDERA_PRIVATE_KEY could not be parsed. Copy the whole value of either the " +
      '"HEX Encoded Private Key" or "DER Encoded Private Key" field on ' +
      "portal.hedera.com — not the EVM address and not the public key.",
  );
}

export function loadConfig(): ChainConfig {
  const network = (process.env.HEDERA_NETWORK ?? "testnet") as "testnet" | "mainnet";
  return {
    accountId: AccountId.fromString(required("HEDERA_ACCOUNT_ID")),
    privateKey: parseKey(required("HEDERA_PRIVATE_KEY")),
    network,
    topicId: process.env.HEDERA_TOPIC_ID,
  };
}

export function makeClient(config: ChainConfig): Client {
  const client =
    config.network === "mainnet" ? Client.forMainnet() : Client.forTestnet();
  client.setOperator(config.accountId, config.privateKey);
  return client;
}

export function mirrorUrl(network: string, topicId: string): string {
  const host =
    network === "mainnet"
      ? "https://mainnet.mirrornode.hedera.com"
      : "https://testnet.mirrornode.hedera.com";
  return `${host}/api/v1/topics/${topicId}/messages`;
}
