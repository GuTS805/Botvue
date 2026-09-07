/**
 * An agent that checks a page before trusting it.
 *
 *   npx tsx src/run.ts <url> [--service http://localhost:8000]
 *
 * The whole point is the last beat. The server settles payment through Blocky402, then
 * independently re-checks that settlement against Hedera's public mirror node before ever
 * reporting success — two sources agreeing on one truth, printed here exactly as the server
 * saw it, including the case where they disagree.
 */

import { discover, termsFromChallenge } from "./discover.js";
import { proofHeader, sign } from "./pay.js";

interface CrossCheck {
  agrees: boolean;
  detail: string;
  mirrorStatus?: string | null;
  mirrorCreditedTinybar?: number | null;
}

interface Verdict {
  url: string;
  decision: "block" | "flag" | "pass";
  verdict: string;
  explanation: string;
  human_words: number;
  word_ratio: Record<string, number>;
  statuses: Record<string, number | string>;
  evidence: Record<string, string>;
  payment?: {
    settled?: boolean;
    transactionId?: string;
    paidTinybar?: number;
    reason?: string;
    crossCheck?: CrossCheck | null;
  };
}

// A slow or unreachable facilitator must not hang the agent forever — the gateway itself
// bounds its own facilitator calls, but the network hop to the gateway needs its own limit
// too, with a message that says what is actually being waited on.
const REQUEST_TIMEOUT_MS = 45_000;

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

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    const res = await fetch(found.endpoint, {
      method: found.method,
      headers,
      body: JSON.stringify({ [found.urlField]: target }),
      signal: controller.signal,
    });
    return { status: res.status, body: await res.json() };
  } catch (err) {
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(
        `the service did not respond within ${REQUEST_TIMEOUT_MS / 1000}s — it may be ` +
          "waiting on the Blocky402 facilitator",
      );
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

function printCrossCheck(crossCheck: CrossCheck | null | undefined): void {
  if (!crossCheck) return;
  if (crossCheck.agrees) {
    console.log(`  cross-check: ${crossCheck.detail}`);
  } else {
    // This is the interesting case, not the embarrassing one: the server settled through
    // Blocky402 but its own independent mirror-node check did not confirm it yet (or found
    // something different) — and it said so rather than papering over the gap.
    console.log(`  CROSS-CHECK DISAGREEMENT: ${crossCheck.detail}`);
  }
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

  if (found.paid && !found.paymentRequired) {
    console.log("  note: this service documents payment but is running open\n");
  }

  let call = await callCheck(found, target);

  if (call.status === 402) {
    const terms = termsFromChallenge(call.body);
    console.log(
      `402 payment required: ${terms.amount} tinybar of ${terms.asset} to ${terms.payTo}`,
    );
    console.log(`  fee payer (Blocky402): ${terms.feePayer}`);

    const payment = await sign(terms);
    console.log(`  signed a transfer for the facilitator to submit`);

    call = await callCheck(found, target, proofHeader(payment, terms));
    if (call.status === 402) {
      throw new Error(`payment rejected: ${call.body.error}`);
    }

    const receipt = call.body?.payment;
    console.log(`  settled: ${receipt?.transactionId ?? "(no transaction id returned)"}`);
    printCrossCheck(receipt?.crossCheck);
    console.log();
  } else {
    console.log("service is running without payment required\n");
  }

  if (call.status !== 200) {
    throw new Error(`check failed: HTTP ${call.status} — ${call.body?.error ?? ""}`);
  }
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
