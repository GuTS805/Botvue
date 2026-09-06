import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { TopicMessageSubmitTransaction } from "@hashgraph/sdk";

import { canonicalise, messageSize, trim, type Attestation, type Rule } from "./attestation.js";
import { loadConfig, makeClient, mirrorUrl } from "./client.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const MANIFEST = resolve(HERE, "../../../evidence/manifest.json");

interface ManifestProperty {
  domain: string;
  chain: string;
  url: string;
  verdict: Attestation["verdict"];
  control_similarity: number | null;
  word_ratio: Record<string, number>;
  soft_blocked: string[];
  substituted: string[];
  refused: string[];
  agents: Record<string, { status: number; sha256: string }>;
}

function ruleFor(p: ManifestProperty): Rule {
  if (p.soft_blocked.length) return "size-ratio";
  if (p.verdict === "refused") return "status-code";
  return "block-diff";
}

function toAttestation(p: ManifestProperty, observedAt: string): Attestation {
  const agents = p.soft_blocked.length
    ? p.soft_blocked
    : p.substituted.length
      ? p.substituted
      : p.refused.map((r) => r.split(":")[0]);

  const bodies = Object.fromEntries(
    Object.entries(p.agents).map(([agent, rec]) => [agent, rec.sha256]),
  );

  return trim(
    {
      v: 1,
      domain: p.domain,
      url: p.url,
      observedAt,
      verdict: p.verdict,
      rule: ruleFor(p),
      agents,
      bodies,
      wordRatio: p.word_ratio,
      controlSimilarity: p.control_similarity,
    },
    agents,
  );
}

async function main() {
  const limit = Number(process.argv[2] ?? 10);
  const config = loadConfig();
  if (!config.topicId) {
    throw new Error("HEDERA_TOPIC_ID is not set. Run `npm run create-topic` first.");
  }

  const manifest = JSON.parse(readFileSync(MANIFEST, "utf8"));
  const properties: ManifestProperty[] = manifest.properties;

  // Soft-blocks first: they are the findings that cannot be reconstructed from a status
  // code once the configuration changes.
  const ordered = [...properties].sort(
    (a, b) => Number(b.soft_blocked.length > 0) - Number(a.soft_blocked.length > 0),
  );
  const selected = ordered.slice(0, limit);

  const client = makeClient(config);
  console.log(`submitting ${selected.length} attestations to topic ${config.topicId}\n`);

  let submitted = 0;
  for (const property of selected) {
    const attestation = toAttestation(property, manifest.captured_at);
    const message = canonicalise(attestation);
    const receipt = await (
      await new TopicMessageSubmitTransaction()
        .setTopicId(config.topicId)
        .setMessage(message)
        .execute(client)
    ).getReceipt(client);

    submitted += 1;
    console.log(
      `  #${receipt.topicSequenceNumber?.toString().padStart(3)} ` +
        `${property.domain.padEnd(28)} ${property.verdict.padEnd(15)} ` +
        `${messageSize(attestation)}b`,
    );
  }

  console.log(`\n${submitted} attestations written`);
  console.log(`anyone can read them back: ${mirrorUrl(config.network, config.topicId)}`);
  client.close();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
