import { createHash } from "node:crypto";

import type { Attestation } from "./attestation.js";
import { mirrorUrl } from "./client.js";

/**
 * Reads the attestations back and checks them against the live web.
 *
 * This runs with no credentials and no dependency on this project's infrastructure — the
 * mirror node is public, and the bodies are fetched from the publishers directly. That is
 * the whole point of anchoring the record: a reader does not have to take the scanner's
 * word for anything, including that the record has not been edited since.
 *
 *   npx tsx src/verify.ts <topicId> [--live]
 */

const UA: Record<string, string> = {
  chrome:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
    "Chrome/128.0.0.0 Safari/537.36",
  gptbot:
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.1; " +
    "+https://openai.com/gptbot)",
  googlebot:
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Googlebot/2.1; " +
    "+http://www.google.com/bot.html) Chrome/128.0.0.0 Safari/537.36",
  perplexitybot:
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; PerplexityBot/1.0; " +
    "+https://perplexity.ai/perplexitybot)",
};

const sha256 = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

async function readTopic(network: string, topicId: string): Promise<Attestation[]> {
  const res = await fetch(`${mirrorUrl(network, topicId)}?limit=100&order=asc`);
  if (!res.ok) throw new Error(`mirror node returned ${res.status}`);
  const body = (await res.json()) as { messages: { message: string }[] };
  return body.messages.map(
    (m) => JSON.parse(Buffer.from(m.message, "base64").toString("utf8")) as Attestation,
  );
}

async function fetchAs(url: string, agent: string): Promise<{ status: number; sha: string }> {
  try {
    const res = await fetch(url, {
      headers: { "User-Agent": UA[agent] ?? UA.chrome, "Accept-Encoding": "gzip, deflate" },
      redirect: "follow",
    });
    return { status: res.status, sha: sha256(await res.text()) };
  } catch {
    return { status: 0, sha: "" };
  }
}

async function main() {
  const topicId = process.argv[2] ?? process.env.HEDERA_TOPIC_ID;
  const live = process.argv.includes("--live");
  const network = process.env.HEDERA_NETWORK ?? "testnet";
  if (!topicId) throw new Error("usage: tsx src/verify.ts <topicId> [--live]");

  const attestations = await readTopic(network, topicId);
  console.log(`read ${attestations.length} attestations from topic ${topicId}\n`);

  for (const a of attestations) {
    console.log(`${a.domain}  ${a.verdict}  (${a.rule}, observed ${a.observedAt})`);
    for (const agent of a.agents) {
      const ratio = a.wordRatio[agent];
      const pct = ratio === undefined ? "—" : `${Math.round(ratio * 100)}%`;
      console.log(`   ${agent.padEnd(15)} recorded ${a.bodies[agent]?.slice(0, 12)}  ${pct} of the human page`);
    }
    if (!live) continue;

    for (const agent of [...a.agents, "googlebot"]) {
      const recorded = a.bodies[agent];
      if (!recorded) continue;
      const now = await fetchAs(a.url, agent);
      const verdict =
        now.sha === recorded
          ? "unchanged — still serving the recorded body"
          : now.status === 0
            ? "unreachable"
            : `CHANGED — now ${now.sha.slice(0, 12)} (status ${now.status})`;
      console.log(`   live ${agent.padEnd(14)} ${verdict}`);
    }
    console.log();
  }

  if (!live) {
    console.log("pass --live to re-fetch each URL and compare against the recorded hashes");
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
