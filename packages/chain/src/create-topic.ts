import { TopicCreateTransaction } from "@hashgraph/sdk";
import { loadConfig, makeClient, mirrorUrl } from "./client.js";

/**
 * Creates the consensus topic findings are written to.
 *
 * The topic is deliberately left without a submit key: anyone may append to it. A topic only
 * this project can write to would prove nothing beyond "this project wrote something down",
 * which is what a database already does. What matters is that entries cannot be removed or
 * back-dated, and that a publisher who disputes a finding can append their answer to the same
 * ordered record.
 */
async function main() {
  const config = loadConfig();
  const client = makeClient(config);

  const receipt = await (
    await new TopicCreateTransaction()
      .setTopicMemo("botvue: observations of content served to AI crawlers")
      .execute(client)
  ).getReceipt(client);

  const topicId = receipt.topicId!.toString();
  console.log(`topic created: ${topicId}`);
  console.log(`mirror:        ${mirrorUrl(config.network, topicId)}`);
  console.log(`\nadd to .env:\nHEDERA_TOPIC_ID=${topicId}`);
  client.close();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
