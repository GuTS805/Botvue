/**
 * The shape of what gets written to the consensus topic.
 *
 * A record is only worth anchoring if a third party can recompute it. Every field here is
 * either a hash of a body anyone can fetch, or a number derived from those bodies — nothing
 * depends on trusting the scanner's own account of what it saw.
 *
 * There is deliberately no field for intent. What was served, to which user-agent, when.
 */

export type Verdict =
  | "soft-blocked"
  | "substituted"
  | "refused"
  | "format-variant"
  | "all-bots-differ";

/** Which test produced the verdict, so a reader can reproduce that specific check. */
export type Rule = "size-ratio" | "status-code" | "block-diff" | "marker-header";

export interface Attestation {
  /** Schema version — consumers must reject records they cannot interpret. */
  v: 1;
  domain: string;
  url: string;
  /** When the responses were fetched, not when this was submitted. */
  observedAt: string;
  verdict: Verdict;
  rule: Rule;
  /** The crawler user-agents this verdict is about. */
  agents: string[];
  /** SHA-256 of each response body, keyed by user-agent. */
  bodies: Record<string, string>;
  /** Visible words the crawler received as a fraction of what the browser received. */
  wordRatio: Record<string, number>;
  /** How closely the control (Googlebot) matched the browser. 1.0 means identical. */
  controlSimilarity: number | null;
}

const MAX_MESSAGE_BYTES = 1024;

export function canonicalise(a: Attestation): string {
  // Key order is fixed so the same observation always serialises to the same bytes, and a
  // hash of the message is therefore reproducible by anyone holding the same record.
  return JSON.stringify({
    v: a.v,
    domain: a.domain,
    url: a.url,
    observedAt: a.observedAt,
    verdict: a.verdict,
    rule: a.rule,
    agents: [...a.agents].sort(),
    bodies: Object.fromEntries(Object.entries(a.bodies).sort(([x], [y]) => x.localeCompare(y))),
    wordRatio: Object.fromEntries(
      Object.entries(a.wordRatio).sort(([x], [y]) => x.localeCompare(y)),
    ),
    controlSimilarity: a.controlSimilarity,
  });
}

/**
 * Long bodies maps would push a message past a single consensus chunk. Only the agents the
 * verdict is actually about need their hashes carried, plus the browser and the control.
 */
export function trim(a: Attestation, keep: string[]): Attestation {
  if (canonicalise(a).length <= MAX_MESSAGE_BYTES) return a;
  const wanted = new Set([...keep, "chrome", "googlebot"]);
  const pick = <T>(o: Record<string, T>) =>
    Object.fromEntries(Object.entries(o).filter(([k]) => wanted.has(k)));
  return { ...a, bodies: pick(a.bodies), wordRatio: pick(a.wordRatio) };
}

export function messageSize(a: Attestation): number {
  return Buffer.byteLength(canonicalise(a), "utf8");
}
