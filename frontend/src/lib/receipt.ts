/**
 * Strict transaction-receipt classification.
 *
 * A write is only reported as successful when ALL THREE hold:
 *   status           in {ACCEPTED, FINALIZED}
 *   execution result == FINISHED_WITH_RETURN
 *   consensus result == MAJORITY_AGREE
 *
 * Each condition guards a different failure: a contract revert still reaches
 * ACCEPTED (but ends FINISHED_WITH_ERROR), and a round with no majority can
 * carry a return value that the network did not agree on.
 */

const STATUS_BY_NUMBER: Record<number, string> = {
  0: "UNINITIALIZED", 1: "PENDING", 2: "PROPOSING", 3: "COMMITTING", 4: "REVEALING",
  5: "ACCEPTED", 6: "UNDETERMINED", 7: "FINALIZED", 8: "CANCELED", 9: "APPEAL_REVEALING",
  10: "APPEAL_COMMITTING", 11: "VALIDATORS_TIMEOUT", 12: "LEADER_TIMEOUT", 13: "LEADER_REVEALING",
};
// Consensus RESULT codes as the SDK maps them (genlayer-js
// transactionResultNumberToName). Note this is NOT the order of the per-validator
// vote enum: on-chain 1 is MAJORITY_AGREE. Confirmed against a live Studio Next
// receipt (result: 1, result_name: "MAJORITY_AGREE").
const RESULT_BY_NUMBER: Record<number, string> = {
  0: "IDLE", 1: "MAJORITY_AGREE", 2: "MAJORITY_DISAGREE", 3: "MAJORITY_TIMEOUT",
  4: "DETERMINISTIC_VIOLATION", 5: "NO_MAJORITY",
};
const EXECUTION_BY_NUMBER: Record<number, string> = {
  0: "NOT_VOTED", 1: "FINISHED_WITH_RETURN", 2: "FINISHED_WITH_ERROR", 3: "TIMEOUT",
  4: "NONDET_DISAGREE", 5: "DETERMINISTIC_VIOLATION",
};

export interface ReceiptLike {
  status?: number | string;
  statusName?: string;
  result?: number | string;
  resultName?: string;
  /** snake_case spelling carried by raw (fullTransaction) receipts */
  result_name?: string;
  txExecutionResult?: number | string;
  txExecutionResultName?: string;
}

export type ReceiptVerdict =
  | { kind: "success"; status: string }
  | { kind: "pending"; status: string }
  | { kind: "failed"; reason: string; status: string };

const PENDING = new Set([
  "UNINITIALIZED", "PENDING", "PROPOSING", "COMMITTING", "REVEALING",
  "APPEAL_REVEALING", "APPEAL_COMMITTING", "LEADER_REVEALING",
]);
const SUCCESS_STATUS = new Set(["ACCEPTED", "FINALIZED"]);

function named(
  name: string | undefined,
  num: number | string | undefined,
  table: Record<number, string>,
): string | undefined {
  if (typeof name === "string" && name !== "") return name;
  if (num === undefined || num === null || num === "") return undefined;
  const n = typeof num === "number" ? num : Number(num);
  if (!Number.isInteger(n)) return undefined;
  return table[n];
}

export function classifyReceipt(r: ReceiptLike | null | undefined): ReceiptVerdict {
  if (!r) return { kind: "pending", status: "UNKNOWN" };
  const status = named(r.statusName, r.status, STATUS_BY_NUMBER);
  if (status === undefined) return { kind: "pending", status: "UNKNOWN" };
  if (PENDING.has(status)) return { kind: "pending", status };
  if (!SUCCESS_STATUS.has(status)) {
    return { kind: "failed", reason: `transaction ended ${status}`, status };
  }
  const execution = named(r.txExecutionResultName, r.txExecutionResult, EXECUTION_BY_NUMBER);
  if (execution !== "FINISHED_WITH_RETURN") {
    return {
      kind: "failed",
      reason: `execution ended ${execution ?? "UNKNOWN"} (expected FINISHED_WITH_RETURN)`,
      status,
    };
  }
  const consensus = named(r.resultName ?? r.result_name, r.result, RESULT_BY_NUMBER);
  if (consensus !== "MAJORITY_AGREE") {
    return {
      kind: "failed",
      reason: `consensus ended ${consensus ?? "UNKNOWN"} (expected MAJORITY_AGREE)`,
      status,
    };
  }
  return { kind: "success", status };
}

export class ReceiptError extends Error {
  constructor(
    message: string,
    readonly verdict: ReceiptVerdict,
  ) {
    super(message);
    this.name = "ReceiptError";
  }
}

/** Throws unless the receipt is a strict success; returns it otherwise. */
export function assertStrictSuccess<T extends ReceiptLike>(r: T | null | undefined): T {
  const v = classifyReceipt(r);
  if (v.kind === "success") return r as T;
  const msg = v.kind === "pending" ? `transaction not decided (${v.status})` : v.reason;
  throw new ReceiptError(msg, v);
}
