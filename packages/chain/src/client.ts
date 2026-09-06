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

export function loadConfig(): ChainConfig {
  const network = (process.env.HEDERA_NETWORK ?? "testnet") as "testnet" | "mainnet";
  return {
    accountId: AccountId.fromString(required("HEDERA_ACCOUNT_ID")),
    // ECDSA and ED25519 keys are both issued by the portal, so accept either rather than
    // making the operator care which one they were given.
    privateKey: PrivateKey.fromStringDer(required("HEDERA_PRIVATE_KEY")),
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
