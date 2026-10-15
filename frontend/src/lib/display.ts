/**
 * Unknown is not zero.
 *
 * A collateral or debt figure that failed to read, or has not been read yet,
 * must never render as "0 GEN": a borrower would read an unknown position as an
 * empty one. These helpers collapse every non-fresh state to an em dash.
 */

import { formatGen } from "./inputs";

export const UNKNOWN = "—";

export interface ReadState {
  /** the item's read failed on its latest attempt */
  failed: boolean;
}

/** The value if it is both present and not failed; otherwise null (unknown). */
export function knownValue<T>(value: T | null | undefined, state: ReadState): T | null {
  if (state.failed) return null;
  return value === null || value === undefined ? null : value;
}

export function displayGen(value: bigint | null, suffix = ""): string {
  return value === null ? UNKNOWN : `${formatGen(value)}${suffix}`;
}

export function displayText(value: string | number | null, fallback = UNKNOWN): string {
  return value === null || value === undefined ? fallback : String(value);
}
