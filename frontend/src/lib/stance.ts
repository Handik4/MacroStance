/**
 * TypeScript port of the contract's pure logic. Every function here has a
 * golden-vector test generated FROM the Python contract
 * (src/lib/__fixtures__/parity_vectors.json), so the two cannot drift silently.
 */

import { FloatLexeme, parseStrictJson, type JsonValue } from "./strictJson";

export const DOVISH = 0;
export const NEUTRAL = 1;
export const HAWKISH = 2;
export type Stance = 0 | 1 | 2;

export const STANCE_NAMES = ["DOVISH", "NEUTRAL", "HAWKISH"] as const;
export type StanceName = (typeof STANCE_NAMES)[number];

export interface RiskRow {
  stance: Stance;
  name: StanceName;
  maxLtvBps: number;
  borrowRateBps: number;
  reserveFactorBps: number;
}

/** Fixed parameter table. Indexed by stance only, never by raw numbers. */
export const RISK_TABLE: readonly RiskRow[] = [
  { stance: 0, name: "DOVISH", maxLtvBps: 8000, borrowRateBps: 350, reserveFactorBps: 1000 },
  { stance: 1, name: "NEUTRAL", maxLtvBps: 7500, borrowRateBps: 550, reserveFactorBps: 1500 },
  { stance: 2, name: "HAWKISH", maxLtvBps: 6500, borrowRateBps: 900, reserveFactorBps: 2500 },
];

export const HIKE_BPS = 25;
export const CUT_BPS = 25;
export const CPI_HAWKISH_ABOVE_BPS = 300;
export const CPI_DOVISH_BELOW_BPS = 200;
export const RATE_MIN_BPS = -2000;
export const RATE_MAX_BPS = 2000;
export const CPI_MIN_BPS = -1000;
export const CPI_MAX_BPS = 10000;
export const MAX_FEED_BODY = 4096;

export function riskForStance(stance: number): RiskRow {
  const row = RISK_TABLE[stance];
  if (!row) throw new RangeError(`unknown stance ${stance}`);
  return row;
}

/** Priority: HAWKISH, then DOVISH, else NEUTRAL (integer inputs only). */
export function deriveStance(rateChangeBps: number, cpiYoyBps: number): Stance {
  if (rateChangeBps >= HIKE_BPS || cpiYoyBps > CPI_HAWKISH_ABOVE_BPS) return HAWKISH;
  if (rateChangeBps <= -CUT_BPS || cpiYoyBps < CPI_DOVISH_BELOW_BPS) return DOVISH;
  return NEUTRAL;
}

// ---------------------------------------------------------------- epoch ids
/** `YYYY-Qn-TAG` -> year*4 + (n-1), or -1 when malformed. */
export function parseEpochId(epochId: unknown): number {
  if (typeof epochId !== "string" || epochId.length > 24) return -1;
  const parts = epochId.split("-");
  if (parts.length !== 3) return -1;
  const [year, quarter, tag] = parts;
  if (!/^[0-9]{4}$/.test(year)) return -1;
  if (!/^Q[1-4]$/.test(quarter)) return -1;
  if (!/^[A-Z0-9]{2,12}$/.test(tag)) return -1;
  const y = Number(year);
  if (y < 2000 || y > 2200) return -1;
  return y * 4 + (Number(quarter[1]) - 1);
}

export function isValidEpochId(epochId: string): boolean {
  return parseEpochId(epochId) >= 0;
}

// ------------------------------------------------------------ source states
export type SourceKind = "CONFIRMED" | "RETRACTED" | "NOT_FOUND" | "OUTAGE";
export interface SourceState {
  kind: SourceKind;
  stance: number;
  releaseHash: string;
}

const OUTAGE: SourceState = { kind: "OUTAGE", stance: 0, releaseHash: "" };

function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function releaseHash(
  epochId: string,
  rateChangeBps: number,
  cpiYoyBps: number,
  stance: number,
): Promise<string> {
  const canon = `${epochId}|${rateChangeBps}|${cpiYoyBps}|${stance}`;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(canon));
  return toHex(digest);
}

function isPlainObject(v: JsonValue): v is { [k: string]: JsonValue } {
  return typeof v === "object" && v !== null && !Array.isArray(v) && !(v instanceof FloatLexeme);
}

function isIntField(v: JsonValue | undefined): v is number {
  return typeof v === "number" && Number.isInteger(v);
}

/**
 * Classify one HTTP response exactly as the contract does. Only a clean 200
 * with a schema-valid document is a vote; a 404 is "not yet published";
 * everything else is an OUTAGE.
 */
export async function classifyResponse(
  status: number,
  body: string,
  epochId: string,
): Promise<SourceState> {
  if (!Number.isInteger(status)) return OUTAGE;
  if (status === 404) return { kind: "NOT_FOUND", stance: 0, releaseHash: "" };
  if (status !== 200) return OUTAGE;
  const size = new TextEncoder().encode(body).length;
  if (size === 0 || size > MAX_FEED_BODY) return OUTAGE;
  let doc: JsonValue;
  try {
    doc = parseStrictJson(body);
  } catch {
    return OUTAGE;
  }
  if (!isPlainObject(doc)) return OUTAGE;
  if (doc.epoch_id !== epochId) return OUTAGE;
  const st = doc.status;
  if (st === "RETRACTED") return { kind: "RETRACTED", stance: 0, releaseHash: "" };
  if (st !== "CONFIRMED") return OUTAGE;
  const rate = doc.rate_change_bps;
  const cpi = doc.cpi_yoy_bps;
  if (!isIntField(rate) || !isIntField(cpi)) return OUTAGE;
  if (rate < RATE_MIN_BPS || rate > RATE_MAX_BPS || cpi < CPI_MIN_BPS || cpi > CPI_MAX_BPS) {
    return OUTAGE;
  }
  const stance = deriveStance(rate, cpi);
  return { kind: "CONFIRMED", stance, releaseHash: await releaseHash(epochId, rate, cpi, stance) };
}

// ----------------------------------------------------------------- quorum
export type Verdict = 0 | 1 | 2;
export const VERDICT_NAMES = ["REJECTED_RELEASE", "VERIFIED_STANCE", "QUORUM_FAILURE"] as const;

export interface QuorumResult {
  verdict: Verdict;
  stance: number;
  sourceCount: number;
  releaseHash: string;
}

/** Fold three source states into the 4-field validator payload. */
export function resolveQuorum(states: readonly SourceState[]): QuorumResult {
  let clean = 0;
  let outages = 0;
  let retracted = 0;
  const groups = new Map<string, { stance: number; hash: string; n: number }>();
  const stances: number[] = [];
  for (const s of states) {
    if (s.kind === "OUTAGE") {
      outages++;
      continue;
    }
    clean++;
    if (s.kind === "RETRACTED") retracted++;
    else if (s.kind === "CONFIRMED") {
      const key = `${s.stance}:${s.releaseHash}`;
      const g = groups.get(key) ?? { stance: s.stance, hash: s.releaseHash, n: 0 };
      g.n++;
      groups.set(key, g);
      stances.push(s.stance);
    }
  }
  const reject = (): QuorumResult => ({ verdict: 0, stance: 0, sourceCount: clean, releaseHash: "" });
  if (retracted >= 1) return reject();

  // best group: highest count, ties broken by smallest (stance, hash) like Python's sorted keys
  let best: { stance: number; hash: string; n: number } | null = null;
  const ordered = [...groups.values()].sort((a, b) =>
    a.stance !== b.stance ? a.stance - b.stance : a.hash < b.hash ? -1 : a.hash > b.hash ? 1 : 0,
  );
  for (const g of ordered) if (best === null || g.n > best.n) best = g;
  const bestN = best?.n ?? 0;

  if (best !== null && bestN >= 2) {
    if (stances.some((s) => s !== best!.stance)) return reject();
    return { verdict: 1, stance: best.stance, sourceCount: bestN, releaseHash: best.hash };
  }
  if (new Set(stances).size > 1) return reject();
  if (bestN + outages < 2) return reject();
  if (outages === 0) return reject();
  return { verdict: 2, stance: 0, sourceCount: clean, releaseHash: "" };
}

export function buildFeedUrl(prefix: string, epochId: string): string {
  return `${prefix}${epochId}.json`;
}
