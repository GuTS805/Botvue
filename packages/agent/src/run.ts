/**
 * An agent that checks a page before trusting it.
 *
 *   npx tsx src/run.ts <url> [--service http://localhost:8000]
 *
 * The whole point is the last step. After paying and receiving a verdict, the agent
 * re-checks the payment against the public mirror node itself. It does not have to believe
 * the service about what it was charged, and it does not have to believe the service about
 * what it observed — every response body is reported as a hash the agent can recompute.
 */

import { discover, termsFromChallenge, type Terms } from "./discover.js";
import { pay, proofHeader } from "./pay.js";

const MIRROR: Record<string, string> = {
  "hedera-testnet": "https://testnet.mirrornode.hedera.com",
  "hedera-mainnet": "https://mainnet.mirrornode.hedera.com",
};

interface Verdict {
  url: string;
  decision: "block" | "flag" | "pass";
  verdict: string;
  explanation: string;
  human_words: number;
  word_ratio: Record<string, number>;
  statuses: Record<string, number | string>;
  evidence: Record<string, string>;
  payment?: { transactionId?: string; paidTinybar?: number };
}

function arg(name: string, fallback: string): string {
  const i = process.argv.indexOf(name);
  return i > -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

async function callCheck(
  found: Awaited<ReturnType<typeof discover>>,
  target: string,
  proof?: string,
): Promise<{ status: number; body: any }> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (proof) headers["X-PAYMENT"] = proof;
  const res = await fetch(found.endpoint, {
    method: found.method,
    headers,
    body: JSON.stringify({ [found.urlField]: target }),
  });
  return { status: res.status, body: await res.json() };
}

async function confirmPayment(terms: Terms, transactionId: string): Promise<string> {
  const host = MIRROR[terms.network];
  if (!host) return "unknown network, cannot confirm independently";
  const id = transactionId.includes("@")
    ? `${transactionId.split("@")[0]}-${transactionId.split("@")[1].replace(".", "-")}`
    : transactionId;

  const res = await fetch(`${host}/api/v1/transactions/${id}`);
  if (!res.ok) return `mirror node returned ${res.status}`;
  const tx = (await res.json()).transactions?.[0];
  if (!tx) return "not found on the mirror node";

  const credited = (tx.transfers ?? [])
    .filter((t: any) => t.account === terms.payTo && t.amount > 0)
    .reduce((sum: number, t: any) => sum + t.amount, 0);
  return `${tx.result}, ${credited} tinybar to ${terms.payTo} (checked independently)`;
}

async function main() {
  const target = process.argv[2];
  if (!target || target.startsWith("--")) {
    throw new Error("usage: tsx src/run.ts <url> [--service http://localhost:8000]");
  }
  const service = arg("--service", process.env.BOTVUE_SERVICE ?? "http://localhost:8000");

  console.log(`agent wants to read: ${target}\n`);

  const found = await discover(service);
  console.log(`discovered ${found.method} ${found.endpoint}`);
  console.log(`  takes field "${found.urlField}", payment expected: ${found.paid}`);
  console.log(`  service reports payment required: ${found.paymentRequired}\n`);

  // Belt and braces: if the spec documents a 402 but the running service says payment is
  // off, we are not talking to the service we think we are.
  if (found.paid && !found.paymentRequired) {
    console.log("  note: this service documents payment but is running open\n");
  }

  let call = await callCheck(found, target);

  if (call.status === 402) {
    const terms = termsFromChallenge(call.body);
    console.log(`402 payment required: ${terms.amount} tinybar of ${terms.asset} to ${terms.payTo}`);
    const payment = await pay(terms);
    console.log(`  paid, transaction ${payment.transactionId}`);

    call = await callCheck(found, target, proofHeader(payment));
    if (call.status === 402) throw new Error(`payment rejected: ${call.body.error}`);

    const confirmation = await confirmPayment(terms, payment.transactionId);
    console.log(`  ledger says: ${confirmation}\n`);
  } else {
    console.log("service is running without payment required\n");
  }

  if (call.status !== 200) throw new Error(`check failed: ${call.status}`);
  const verdict = call.body as Verdict;

  console.log(`verdict: ${verdict.verdict}  ->  ${verdict.decision.toUpperCase()}`);
  console.log(`  ${verdict.explanation}\n`);

  const ratios = Object.entries(verdict.word_ratio)
    .map(([agent, r]) => `${agent}=${Math.round(r * 100)}%`)
    .join("  ");
  console.log(`  a browser receives ${verdict.human_words} words`);
  console.log(`  crawlers receive:  ${ratios}`);
  console.log(`  statuses:          ${JSON.stringify(verdict.statuses)}\n`);

  if (verdict.decision === "block") {
    console.log("NOT READING THIS PAGE.");
    console.log(
      "Without the check, this agent would have treated the response it was given as the\n" +
        "article, and reported on something it never read.",
    );
    process.exitCode = 2;
  } else if (verdict.decision === "flag") {
    console.log("READING WITH A WARNING — some of this was written for machines only.");
  } else {
    console.log("SAFE TO READ.");
  }
}

main().catch((err) => {
  console.error(`\n${err instanceof Error ? err.message : err}`);
  process.exit(1);
});
