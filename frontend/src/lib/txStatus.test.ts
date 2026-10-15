import { describe, expect, it } from "vitest";

import { TxStatusStore } from "./txStatus";

const A = "0x" + "aa".repeat(20);
const B = "0x" + "bb".repeat(20);

describe("TxStatusStore: statuses are keyed by the originating account", () => {
  it("a status is visible only to the account that started it", () => {
    const s = new TxStatusStore();
    const id = s.begin(A, "Claim payout");
    s.succeed(A, id, "0xhash");
    expect(s.get(A)?.phase).toBe("done");
    expect(s.get(B)).toBeNull();
    expect(s.get(null)).toBeNull();
  });

  it("keys are case-insensitive", () => {
    const s = new TxStatusStore();
    s.begin(A.toUpperCase().replace("0X", "0x"), "x");
    expect(s.get(A)?.label).toBe("x");
  });

  it("two accounts hold independent statuses", () => {
    const s = new TxStatusStore();
    s.begin(A, "A's deposit");
    s.begin(B, "B's borrow");
    expect(s.get(A)?.label).toBe("A's deposit");
    expect(s.get(B)?.label).toBe("B's borrow");
  });
});

describe("accountsChanged purges the previous account's statuses", () => {
  it("drops a finished banner of the old account", () => {
    const s = new TxStatusStore();
    const id = s.begin(A, "Claim payout");
    s.succeed(A, id, "0xhash");
    s.purgeExcept(B); // wallet switched A -> B
    expect(s.get(A)).toBeNull();
    expect(s.get(B)).toBeNull();
    expect(s.size).toBe(0);
  });

  it("drops a pending banner of the old account", () => {
    const s = new TxStatusStore();
    s.begin(A, "Propose 2026-Q4-FOMC");
    s.purgeExcept(B);
    expect(s.get(A)).toBeNull();
  });

  it("keeps the new account's own entry", () => {
    const s = new TxStatusStore();
    s.begin(A, "a");
    s.begin(B, "b");
    s.purgeExcept(B);
    expect(s.get(B)?.label).toBe("b");
    expect(s.get(A)).toBeNull();
  });

  it("disconnecting (null) clears everything", () => {
    const s = new TxStatusStore();
    s.begin(A, "a");
    s.begin(B, "b");
    s.purgeExcept(null);
    expect(s.size).toBe(0);
  });

  it("a completion that lands AFTER the purge cannot resurrect the old status", () => {
    const s = new TxStatusStore();
    const id = s.begin(A, "Slow write");
    s.purgeExcept(B);
    expect(s.succeed(A, id, "0xlate")).toBe(false);
    expect(s.fail(A, id, "late failure")).toBe(false);
    expect(s.get(A)).toBeNull();
    expect(s.get(B)).toBeNull();
  });

  it("a late completion of an OLD attempt cannot overwrite a newer one", () => {
    const s = new TxStatusStore();
    const first = s.begin(A, "first");
    const second = s.begin(A, "second");
    expect(s.succeed(A, first, "0xold")).toBe(false);
    expect(s.get(A)?.label).toBe("second");
    expect(s.get(A)?.phase).toBe("pending");
    expect(s.succeed(A, second, "0xnew")).toBe(true);
  });

  it("switching back to the old account does not bring the banner back", () => {
    const s = new TxStatusStore();
    const id = s.begin(A, "x");
    s.succeed(A, id, "0xh");
    s.purgeExcept(B);
    s.purgeExcept(A);
    expect(s.get(A)).toBeNull();
  });
});

describe("subscription", () => {
  it("notifies on begin, settle and purge, and bumps the version", () => {
    const s = new TxStatusStore();
    let n = 0;
    s.subscribe(() => n++);
    const v0 = s.getVersion();
    const id = s.begin(A, "x");
    s.fail(A, id, "boom");
    s.purgeExcept(B);
    expect(n).toBe(3);
    expect(s.getVersion()).toBe(v0 + 3);
  });
  it("does not notify when a purge changes nothing", () => {
    const s = new TxStatusStore();
    let n = 0;
    s.subscribe(() => n++);
    s.purgeExcept(A);
    expect(n).toBe(0);
  });
  it("dismiss removes only that account's entry", () => {
    const s = new TxStatusStore();
    s.begin(A, "a");
    s.begin(B, "b");
    s.dismiss(A);
    expect(s.get(A)).toBeNull();
    expect(s.get(B)?.label).toBe("b");
  });
  it("a failed status carries its message", () => {
    const s = new TxStatusStore();
    const id = s.begin(A, "x");
    s.fail(A, id, "execution ended FINISHED_WITH_ERROR");
    expect(s.get(A)).toMatchObject({ phase: "failed", message: "execution ended FINISHED_WITH_ERROR" });
  });
});
