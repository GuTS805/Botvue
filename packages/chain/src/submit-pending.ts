import { readFileSync, readdirSync, mkdirSync, renameSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { TopicMessageSubmitTransaction } from "@hashgraph/sdk";

import type { Attestation } from "./attestation.js";
import { loadConfig, makeClient, mirrorUrl } from "./client.js";

/**
 * Drains the queue the gateway writes to.
 *
 * The gateway returns its verdict without waiting for consensus, so findings accumulate on
 * disk. Submitted records are moved rather than deleted: the local copy is what lets anyone
 * check that what reached the topic is what the scanner actually observed.
 */

const HERE = dirname(fileURLToPath(import.meta.url));
const PENDING = resolve(HERE, "../../../attestations/pending");
const SUBMITTED = resolve(HERE, "../../../attestations/submitted");

async function main() {
  const config = loadConfig();
  if (!config.topicId) {
    throw new Error("HEDERA_TOPIC_ID is not set. Run `npm run create-topic` first.");
  }

  let files: string[];
  try {
    files = readdirSync(PENDING).filter((f) => f.endsWith(".json"));
  } catch {
    console.log("nothing queued");
    return;
  }
  if (files.length === 0) {
    console.log("nothing queued");
    return;
  }

  mkdirSync(SUBMITTED, { recursive: true });
  const client = makeClient(config);
  console.log(`submitting ${files.length} queued attestations to ${config.topicId}\n`);

  for (const file of files) {
    const raw = readFileSync(join(PENDING, file), "utf8");
    const record = JSON.parse(raw) as Attestation;

    const receipt = await (
      await new TopicMessageSubmitTransaction()
        .setTopicId(config.topicId)
        .setMessage(raw)
        .execute(client)
    ).getReceipt(client);

    renameSync(join(PENDING, file), join(SUBMITTED, file));
    console.log(
      `  #${receipt.topicSequenceNumber?.toString().padStart(3)} ` +
        `${record.domain.padEnd(30)} ${record.verdict}`,
    );
  }

  console.log(`\nreadable by anyone: ${mirrorUrl(config.network, config.topicId)}`);
  client.close();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
