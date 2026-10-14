/**
 * Strict amount parsing. Amounts are decimal GEN strings converted to wei with
 * BigInt arithmetic only: no Number, no float rounding, no locale parsing.
 */

export const WEI_PER_GEN = 10n ** 18n;
export const MAX_WEI = 10n ** 30n; // a sanity ceiling well below u256

export type AmountResult =
  | { ok: true; wei: bigint }
  | { ok: false; error: string };

const DECIMAL = /^(0|[1-9][0-9]*)(\.[0-9]{1,18})?$/;

export function parseGenAmount(input: string, opts: { maxWei?: bigint } = {}): AmountResult {
  if (typeof input !== "string") return { ok: false, error: "Enter an amount." };
  if (input !== input.trim()) return { ok: false, error: "Remove surrounding spaces." };
  if (input === "") return { ok: false, error: "Enter an amount." };
  if (input.startsWith("-")) return { ok: false, error: "Amount cannot be negative." };
  if (/[eE]/.test(input)) return { ok: false, error: "Scientific notation is not accepted." };
  if (/^0[xX]/.test(input)) return { ok: false, error: "Hex amounts are not accepted." };
  if (input.startsWith("+")) return { ok: false, error: "Remove the leading +." };
  if (input.includes(",")) return { ok: false, error: "Use a dot, with no thousands separators." };
  if (/\s/.test(input)) return { ok: false, error: "Amount cannot contain spaces." };
  if (input.startsWith(".")) return { ok: false, error: "Write a leading zero, e.g. 0.5." };
  if (input.endsWith(".")) return { ok: false, error: "Amount cannot end with a dot." };
  if (/^0[0-9]/.test(input)) return { ok: false, error: "Remove leading zeros." };
  const m = DECIMAL.exec(input);
  if (!m) {
    if (/\.[0-9]{19,}$/.test(input)) return { ok: false, error: "At most 18 decimal places." };
    return { ok: false, error: "Enter a plain decimal number." };
  }
  const [whole, frac = ""] = input.split(".");
  const wei = BigInt(whole) * WEI_PER_GEN + BigInt(frac.padEnd(18, "0") || "0");
  if (wei === 0n) return { ok: false, error: "Amount must be greater than zero." };
  const cap = opts.maxWei ?? MAX_WEI;
  if (wei > cap) return { ok: false, error: "Amount exceeds the maximum allowed." };
  return { ok: true, wei };
}

/** Exact wei -> GEN decimal string (no rounding, trailing zeros trimmed). */
export function formatGen(wei: bigint, maxDecimals = 18): string {
  const neg = wei < 0n;
  const abs = neg ? -wei : wei;
  const whole = abs / WEI_PER_GEN;
  let frac = (abs % WEI_PER_GEN).toString().padStart(18, "0").slice(0, maxDecimals);
  frac = frac.replace(/0+$/, "");
  return `${neg ? "-" : ""}${whole}${frac ? "." + frac : ""}`;
}

export function formatBps(bps: number): string {
  const whole = Math.trunc(bps / 100);
  const rem = Math.abs(bps % 100);
  return `${whole}.${rem.toString().padStart(2, "0")}%`;
}

const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
export function isAddress(v: unknown): v is string {
  return typeof v === "string" && ADDRESS.test(v);
}
export function normalizeAddress(v: string): string {
  return v.toLowerCase();
}

/** Parse a wei integer string returned by the contract (decimal digits only). */
export function parseWei(v: unknown): bigint | null {
  if (typeof v === "bigint") return v >= 0n ? v : null;
  if (typeof v === "number") return Number.isSafeInteger(v) && v >= 0 ? BigInt(v) : null;
  if (typeof v === "string" && /^(0|[1-9][0-9]*)$/.test(v)) return BigInt(v);
  return null;
}
