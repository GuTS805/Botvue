import { AccountCreateTransaction, Hbar, PrivateKey } from "@hashgraph/sdk";
import { loadConfig, makeClient } from "./client.js";

/**
 * Creates the account the service is paid into.
 *
 * The agent and the service have to be different accounts or there is no payment to verify —
 * paying yourself nets to zero and credits nobody. This also matches how the thing would
 * actually be run: the operator holds the payee account, the caller holds their own.
 */
async function main() {
  const config = loadConfig();
  const client = makeClient(config);

  const key = PrivateKey.generateED25519();
  const receipt = await (
    await new AccountCreateTransaction()
      .setKeyWithoutAlias(key.publicKey)
      .setInitialBalance(new Hbar(1))
      .setAccountMemo("botvue: service payee")
      .execute(client)
  ).getReceipt(client);

  const accountId = receipt.accountId!.toString();
  client.close();

  console.log(`payee account: ${accountId}`);
  console.log(`\nStart the gateway paid into it:`);
  console.log(`  BOTVUE_REQUIRE_PAYMENT=1 BOTVUE_PAYEE_ACCOUNT_ID=${accountId} \\`);
  console.log(`    .venv/Scripts/python -m uvicorn scanner.gateway:app --port 8000`);
  console.log(`\nThe payee's own key, only needed if you want to spend what it earns:`);
  console.log(`  ${key.toStringDer()}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
