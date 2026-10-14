/**
 * Integer-BPS lending previews mirroring the contract's vault arithmetic.
 *
 * Everything is BigInt. Debt is `ceil(scaled * index / RAY)` and borrow adds
 * `ceil(amount * RAY / index)` scaled units, so the protocol rounds in its own
 * favour at every step. These previews exist so the sandbox can warn before a
 * transaction; the contract remains the authority.
 */

export const BPS = 10000n;
export const RAY = 10n ** 27n;
export const SECONDS_PER_YEAR = 365n * 24n * 3600n;

/** Correct ceiling division for non-negative operands. */
export function ceilDivPos(a: bigint, b: bigint): bigint {
  if (b <= 0n) throw new RangeError("divisor must be positive");
  return (a + b - 1n) / b;
}

export function debtFromScaled(scaled: bigint, index: bigint): bigint {
  return ceilDivPos(scaled * index, RAY);
}

export function scaledFromBorrow(amount: bigint, index: bigint): bigint {
  return ceilDivPos(amount * RAY, index);
}

export function capacity(collateral: bigint, maxLtvBps: number): bigint {
  return (collateral * BigInt(maxLtvBps)) / BPS;
}

/** True when `debt` exceeds what `collateral` supports at `maxLtvBps` (no division). */
export function isOverLtv(collateral: bigint, debt: bigint, maxLtvBps: number): boolean {
  return debt * BPS > collateral * BigInt(maxLtvBps);
}

export function ltvBps(collateral: bigint, debt: bigint): number {
  if (debt === 0n) return 0;
  if (collateral === 0n) return 1_000_000_000;
  return Number((debt * BPS) / collateral);
}

/** Largest borrow that passes the contract's check (walks down the rounding overshoot). */
export function maxBorrow(collateral: bigint, scaled: bigint, index: bigint, maxLtvBps: number): bigint {
  const cap = capacity(collateral, maxLtvBps);
  const debt = debtFromScaled(scaled, index);
  let cand = cap - debt;
  if (cand <= 0n) return 0n;
  for (let i = 0; i < 4; i++) {
    const after = debtFromScaled(scaled + scaledFromBorrow(cand, index), index);
    if (!isOverLtv(collateral, after, maxLtvBps)) return cand;
    cand -= 1n;
    if (cand <= 0n) return 0n;
  }
  return 0n;
}

/** Index after `dt` seconds at `rateBps`; rounds UP like the contract. */
export function projectIndex(index: bigint, rateBps: number, dtSeconds: bigint): bigint {
  if (dtSeconds <= 0n) return index;
  return index + ceilDivPos(index * BigInt(rateBps) * dtSeconds, BPS * SECONDS_PER_YEAR);
}

export type BorrowPreview =
  | { ok: true; newDebt: bigint; newLtvBps: number }
  | { ok: false; reason: "no-collateral" | "ltv-exceeded" };

export function previewBorrow(
  collateral: bigint,
  scaled: bigint,
  index: bigint,
  amount: bigint,
  maxLtvBps: number,
): BorrowPreview {
  if (collateral === 0n) return { ok: false, reason: "no-collateral" };
  const newDebt = debtFromScaled(scaled + scaledFromBorrow(amount, index), index);
  if (isOverLtv(collateral, newDebt, maxLtvBps)) return { ok: false, reason: "ltv-exceeded" };
  return { ok: true, newDebt, newLtvBps: ltvBps(collateral, newDebt) };
}

export type WithdrawPreview =
  | { ok: true; remaining: bigint; newLtvBps: number }
  | { ok: false; reason: "exceeds-collateral" | "ltv-exceeded" };

export function previewWithdraw(
  collateral: bigint,
  debt: bigint,
  amount: bigint,
  maxLtvBps: number,
): WithdrawPreview {
  if (amount > collateral) return { ok: false, reason: "exceeds-collateral" };
  const remaining = collateral - amount;
  if (isOverLtv(remaining, debt, maxLtvBps)) return { ok: false, reason: "ltv-exceeded" };
  return { ok: true, remaining, newLtvBps: ltvBps(remaining, debt) };
}

export type HealthStatus = "NO_DEBT" | "HEALTHY" | "OVER_LTV";
export function healthStatus(collateral: bigint, debt: bigint, maxLtvBps: number): HealthStatus {
  if (debt === 0n) return "NO_DEBT";
  return isOverLtv(collateral, debt, maxLtvBps) ? "OVER_LTV" : "HEALTHY";
}
