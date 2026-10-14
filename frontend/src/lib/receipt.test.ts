import { describe, expect, it } from "vitest";

import { ReceiptError, assertStrictSuccess, classifyReceipt } from "./receipt";

const ok = { statusName: "ACCEPTED", txExecutionResultName: "FINISHED_WITH_RETURN", resultName: "MAJORITY_AGREE" };

describe("classifyReceipt: strict success", () => {
  it("ACCEPTED + FINISHED_WITH_RETURN + MAJORITY_AGREE is success", () => {
    expect(classifyReceipt(ok)).toEqual({ kind: "success", status: "ACCEPTED" });
  });
  it("FINALIZED is success too", () => {
    expect(classifyReceipt({ ...ok, statusName: "FINALIZED" }).kind).toBe("success");
  });
  it("numeric codes are decoded with the SDK's tables (status 5|7, exec 1, result 1)", () => {
    expect(classifyReceipt({ status: 5, txExecutionResult: 1, result: 1 }).kind).toBe("success");
    expect(classifyReceipt({ status: 7, txExecutionResult: 1, result: 1 }).kind).toBe("success");
    expect(classifyReceipt({ status: "5", txExecutionResult: "1", result: "1" }).kind).toBe("success");
  });
  it("accepts the raw snake_case result_name of a fullTransaction receipt", () => {
    // Shape of the live Studio Next deployment receipt that exposed the old table.
    const live = { status: 5, statusName: "ACCEPTED", result: 1, result_name: "MAJORITY_AGREE", txExecutionResult: 1, txExecutionResultName: "FINISHED_WITH_RETURN" };
    expect(classifyReceipt(live).kind).toBe("success");
  });
  it.each([0, 2, 3, 4, 5])("numeric consensus result %i is not success", (result) => {
    expect(classifyReceipt({ status: 5, txExecutionResult: 1, result }).kind).toBe("failed");
  });
  it("the old (wrong) assumption that 6 means MAJORITY_AGREE is rejected", () => {
    expect(classifyReceipt({ status: 5, txExecutionResult: 1, result: 6 }).kind).toBe("failed");
  });
});

describe("classifyReceipt: every missing leg fails", () => {
  it("a contract revert still reaches ACCEPTED but is not success", () => {
    const v = classifyReceipt({ ...ok, txExecutionResultName: "FINISHED_WITH_ERROR" });
    expect(v.kind).toBe("failed");
    expect((v as { reason: string }).reason).toContain("FINISHED_WITH_ERROR");
  });
  it.each(["NO_MAJORITY", "MAJORITY_DISAGREE", "DISAGREE", "TIMEOUT", "IDLE", "MAJORITY_TIMEOUT", "DETERMINISTIC_VIOLATION", "AGREE"])(
    "consensus result %s is not success",
    (resultName) => {
      expect(classifyReceipt({ ...ok, resultName }).kind).toBe("failed");
    },
  );
  it.each(["NOT_VOTED", "TIMEOUT", "NONDET_DISAGREE", "DETERMINISTIC_VIOLATION"])(
    "execution result %s is not success",
    (txExecutionResultName) => {
      expect(classifyReceipt({ ...ok, txExecutionResultName }).kind).toBe("failed");
    },
  );
  it("missing execution or consensus fields fail rather than default to success", () => {
    expect(classifyReceipt({ statusName: "ACCEPTED" }).kind).toBe("failed");
    expect(classifyReceipt({ statusName: "ACCEPTED", txExecutionResultName: "FINISHED_WITH_RETURN" }).kind).toBe("failed");
  });
});

describe("classifyReceipt: lifecycle", () => {
  it.each(["PENDING", "PROPOSING", "COMMITTING", "REVEALING", "UNINITIALIZED", "LEADER_REVEALING"])(
    "%s is pending",
    (statusName) => {
      expect(classifyReceipt({ statusName }).kind).toBe("pending");
    },
  );
  it.each(["CANCELED", "UNDETERMINED", "VALIDATORS_TIMEOUT", "LEADER_TIMEOUT"])("%s is failed", (statusName) => {
    expect(classifyReceipt({ statusName }).kind).toBe("failed");
  });
  it("null / undefined / empty are pending, never success", () => {
    expect(classifyReceipt(null).kind).toBe("pending");
    expect(classifyReceipt(undefined).kind).toBe("pending");
    expect(classifyReceipt({}).kind).toBe("pending");
  });
  it("an unknown numeric status is pending", () => {
    expect(classifyReceipt({ status: 99 }).kind).toBe("pending");
  });
});

describe("assertStrictSuccess", () => {
  it("returns the receipt on success", () => {
    expect(assertStrictSuccess(ok)).toBe(ok);
  });
  it("throws ReceiptError carrying the verdict", () => {
    try {
      assertStrictSuccess({ ...ok, resultName: "NO_MAJORITY" });
      throw new Error("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(ReceiptError);
      expect((e as ReceiptError).verdict.kind).toBe("failed");
      expect((e as ReceiptError).message).toContain("NO_MAJORITY");
    }
  });
  it("throws on undecided transactions", () => {
    expect(() => assertStrictSuccess({ statusName: "PROPOSING" })).toThrow(/not decided/);
  });
});
