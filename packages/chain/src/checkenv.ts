import { loadConfig, makeClient } from "./client.js";

/** Confirms the credentials parse and the account exists, before spending a transaction. */
async function main() {
  const config = loadConfig();
  console.log(`account:  ${config.accountId.toString()}`);
  console.log(`network:  ${config.network}`);
  console.log(`key:      parsed ok (${config.privateKey.publicKey.toString().slice(0, 24)}…)`);

  const host =
    config.network === "mainnet"
      ? "https://mainnet.mirrornode.hedera.com"
      : "https://testnet.mirrornode.hedera.com";
  const res = await fetch(`${host}/api/v1/accounts/${config.accountId.toString()}`);
  if (!res.ok) {
    console.log(`balance:  could not read the mirror node (${res.status})`);
    return;
  }
  const account = (await res.json()) as { balance?: { balance?: number } };
  const tinybar = account.balance?.balance ?? 0;
  console.log(`balance:  ${(tinybar / 100_000_000).toFixed(4)} HBAR`);
  console.log(`topic:    ${config.topicId || "not created yet — run: npm run create-topic"}`);

  makeClient(config).close();
  console.log("\nready.");
}

main().catch((err) => {
  console.error(`\n${err instanceof Error ? err.message : err}`);
  process.exit(1);
});
