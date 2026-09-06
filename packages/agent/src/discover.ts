/**
 * Finds the check operation without being told where it is.
 *
 * The agent is given a service root and nothing else. It reads the OpenAPI document, looks
 * for an operation that takes a URL and documents a 402, and learns the price from the
 * payment terms that operation points at. Nothing about the path, the request shape or the
 * amount is hardcoded, so the same agent works against any service describing itself this
 * way — which is also the form Bazantic ingests.
 */

export interface Discovered {
  root: string;
  /** Absolute URL of the operation that performs a check. */
  endpoint: string;
  method: string;
  /** Name of the request field that carries the URL to check. */
  urlField: string;
  /** True when the operation documents a 402, so payment is expected. */
  paid: boolean;
  summary: string;
}

export interface Terms {
  scheme: string;
  network: string;
  asset: string;
  amount: string;
  payTo: string;
  proofHeader: string;
  verifiedAgainst?: string;
}

const URL_FIELD_HINTS = ["url", "uri", "href", "target", "page", "link"];

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${url} returned ${res.status}`);
  return (await res.json()) as T;
}

function resolveSchema(spec: any, node: any): any {
  if (!node) return node;
  if (node.$ref?.startsWith("#/")) {
    const path = node.$ref.slice(2).split("/");
    return path.reduce((acc: any, key: string) => acc?.[key], spec);
  }
  return node;
}

export async function discover(root: string): Promise<Discovered> {
  const base = root.replace(/\/+$/, "");
  const spec = await getJson<any>(`${base}/openapi.json`);

  for (const [path, operations] of Object.entries<any>(spec.paths ?? {})) {
    for (const [method, operation] of Object.entries<any>(operations)) {
      if (!["post", "get", "put"].includes(method)) continue;

      const body = operation.requestBody?.content?.["application/json"]?.schema;
      const schema = resolveSchema(spec, body);
      const properties = schema?.properties ?? {};
      const field = Object.keys(properties).find((name) =>
        URL_FIELD_HINTS.includes(name.toLowerCase()),
      );
      if (!field) continue;

      return {
        root: base,
        endpoint: `${base}${path}`,
        method: method.toUpperCase(),
        urlField: field,
        paid: Boolean(operation.responses?.["402"]),
        summary: operation.summary ?? spec.info?.summary ?? "",
      };
    }
  }

  throw new Error(
    `no operation at ${base} accepts a URL — cannot work out how to call this service`,
  );
}

/** Reads terms from a 402 body, which carries everything needed to pay. */
export function termsFromChallenge(challenge: any): Terms {
  const accepts = challenge?.accepts?.[0];
  if (!accepts) throw new Error("402 body did not describe how to pay");
  return {
    scheme: accepts.scheme,
    network: accepts.network,
    asset: accepts.asset,
    amount: String(accepts.amount),
    payTo: accepts.payTo,
    proofHeader: challenge?.proof?.header ?? "X-PAYMENT",
    verifiedAgainst: challenge?.verifiedAgainst,
  };
}
