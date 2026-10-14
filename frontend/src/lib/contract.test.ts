import { beforeEach, describe, expect, it, vi } from "vitest";

const calls: { method: string; arg: Record<string, unknown> }[] = [];
let receipt: Record<string, unknown> = {};
let quote = { distribution: { d: 1 }, messageAllocations: [], feeValue: 777n };
let readValue: unknown = null;
let order: string[] = [];

vi.mock("genlayer-js", () => ({
  createClient: () => ({
    readContract: async (a: Record<string, unknown>) => {
      calls.push({ method: "readContract", arg: a });
      return readValue;
    },
    estimateTransactionFeesForWrite: async (a: Record<string, unknown>) => {
      order.push("estimate");
      calls.push({ method: "estimateTransactionFeesForWrite", arg: a });
      return quote;
    },
    writeContract: async (a: Record<string, unknown>) => {
      order.push("write");
      calls.push({ method: "writeContract", arg: a });
      return "0xhash";
    },
    waitForTransactionReceipt: async (a: Record<string, unknown>) => {
      order.push("wait");
      calls.push({ method: "waitForTransactionReceipt", arg: a });
      return receipt;
    },
  }),
}));
vi.mock("genlayer-js/chains", () => ({ studioDevnet: { id: 61997, name: "studio", rpcUrls: { default: { http: ["x"] } } } }));

import { ContractError, InputError, MacroStanceClient, raceSignal, toPlain } from "./contract";
import { ReceiptError } from "./receipt";

const ADDR = "0x" + "ab".repeat(20);
const ACCOUNT = "0x81b637d8fCD2C6da6359E6963113a1170de795e4";
const good = { statusName: "ACCEPTED", txExecutionResultName: "FINISHED_WITH_RETURN", resultName: "MAJORITY_AGREE" };

function connected() {
  const c = new MacroStanceClient(ADDR, "https://rpc.test/api");
  c.connect(ACCOUNT, { request: async () => null });
  return c;
}

beforeEach(() => {
  calls.length = 0;
  order = [];
  receipt = { ...good };
  quote = { distribution: { d: 1 }, messageAllocations: [], feeValue: 777n };
  readValue = null;
});

describe("fees come from the SDK, never from hard-coded overrides", () => {
  it("estimates, then writes with exactly the quoted fees", async () => {
    const c = connected();
    await c.proposeEpochSync("2026-Q4-FOMC", 10n ** 17n);
    expect(order).toEqual(["estimate", "write", "wait"]);
    const est = calls.find((x) => x.method === "estimateTransactionFeesForWrite")!.arg;
    const wr = calls.find((x) => x.method === "writeContract")!.arg;
    expect(est).toMatchObject({ address: ADDR, functionName: "propose_epoch_sync", args: ["2026-Q4-FOMC"], value: 10n ** 17n });
    expect(wr.fees).toEqual({ distribution: { d: 1 }, messageAllocations: [], feeValue: 777n });
    expect(wr.value).toBe(10n ** 17n);
  });
  it("never sets a gas override", async () => {
    await connected().claimPayout();
    for (const c of calls) {
      expect(Object.keys(c.arg)).not.toContain("gas");
      expect(Object.keys(c.arg)).not.toContain("gasLimit");
      expect(Object.keys(c.arg)).not.toContain("maxFeePerGas");
    }
  });
  it("waits until decided", async () => {
    await connected().claimPayout();
    const w = calls.find((x) => x.method === "waitForTransactionReceipt")!.arg;
    expect(w.waitUntil).toBe("decided");
    expect(w.hash).toBe("0xhash");
  });
});

describe("strict receipt finality on writes", () => {
  it("returns the outcome on strict success", async () => {
    const out = await connected().claimPayout();
    expect(out.hash).toBe("0xhash");
    expect(out.feeValue).toBe(777n);
  });
  it("FINALIZED also passes", async () => {
    receipt = { ...good, statusName: "FINALIZED" };
    await expect(connected().claimPayout()).resolves.toBeTruthy();
  });
  it.each([
    [{ ...good, txExecutionResultName: "FINISHED_WITH_ERROR" }, /FINISHED_WITH_ERROR/],
    [{ ...good, resultName: "NO_MAJORITY" }, /NO_MAJORITY/],
    [{ ...good, statusName: "UNDETERMINED" }, /UNDETERMINED/],
    [{ ...good, statusName: "PROPOSING" }, /not decided/],
    [{}, /not decided/],
  ])("rejects %j", async (r, re) => {
    receipt = r;
    await expect(connected().claimPayout()).rejects.toThrow(re);
    await expect(connected().claimPayout()).rejects.toBeInstanceOf(ReceiptError);
  });
});

describe("input checks happen before any network call", () => {
  it.each(["", "bogus", "2026-Q5-FOMC", "2026-q4-fomc", "../x", "2026-Q4-FOMC.json"])("epoch id %j", (id) => {
    expect(() => connected().proposeEpochSync(id, 1n)).toThrow(InputError);
    expect(() => connected().challengeStagedEpoch(id, 1n)).toThrow(InputError);
    expect(() => connected().executeStanceTransition(id)).toThrow(InputError);
    expect(calls.length).toBe(0);
  });
  it.each([0n, -1n])("amount %s", (v) => {
    expect(() => connected().depositCollateral(v)).toThrow(InputError);
    expect(() => connected().borrowAgainstCollateral(v)).toThrow(InputError);
    expect(() => connected().repay(v)).toThrow(InputError);
    expect(() => connected().withdrawCollateral(v)).toThrow(InputError);
    expect(() => connected().proposeEpochSync("2026-Q4-FOMC", v)).toThrow(InputError);
    expect(calls.length).toBe(0);
  });
  it("connect rejects a malformed account", () => {
    const c = new MacroStanceClient(ADDR);
    expect(() => c.connect("0x123", { request: async () => null })).toThrow(InputError);
  });
});

describe("connection state", () => {
  it("writes require a connected wallet", async () => {
    const c = new MacroStanceClient(ADDR);
    await expect(c.claimPayout()).rejects.toBeInstanceOf(ContractError);
  });
  it("writes require a configured contract address", async () => {
    const c = new MacroStanceClient("", "https://rpc.test");
    c.connect(ACCOUNT, { request: async () => null });
    await expect(c.claimPayout()).rejects.toThrow(/not configured/);
  });
  it("disconnect drops the signer", async () => {
    const c = connected();
    expect(c.account).toBe(ACCOUNT.toLowerCase());
    c.disconnect();
    expect(c.account).toBeNull();
    await expect(c.claimPayout()).rejects.toThrow(/Connect a wallet/);
  });
  it("reconnecting rebinds the account", () => {
    const c = connected();
    c.connect("0x" + "cd".repeat(20), { request: async () => null });
    expect(c.account).toBe("0x" + "cd".repeat(20));
  });
});

describe("reads", () => {
  it("lowercases the owner and decodes Map results", async () => {
    readValue = new Map<string, unknown>([["collateral", "5"], ["debt", 0n]]);
    const c = new MacroStanceClient(ADDR);
    const h = await c.getAccountHealth(ACCOUNT);
    expect(h).toEqual({ collateral: "5", debt: 0 });
    expect(calls[0].arg.args).toEqual([ACCOUNT.toLowerCase()]);
  });
  it("claimableOf parses wei strictly", async () => {
    readValue = "100000000000000000";
    expect(await new MacroStanceClient(ADDR).claimableOf(ACCOUNT)).toBe(10n ** 17n);
    readValue = "1.5";
    await expect(new MacroStanceClient(ADDR).claimableOf(ACCOUNT)).rejects.toThrow(/non-integer/);
  });
  it("rejects a malformed owner before reading", async () => {
    await expect(new MacroStanceClient(ADDR).getAccountHealth("nope")).rejects.toBeInstanceOf(InputError);
    expect(calls.length).toBe(0);
  });
  it("an unconfigured client refuses to read", async () => {
    await expect(new MacroStanceClient("").getOverview()).rejects.toThrow(/not configured/);
  });
  it("epoch index reads pass a bigint index", async () => {
    readValue = {};
    await new MacroStanceClient(ADDR).getEpochByIndex(3);
    expect(calls[0].arg.args).toEqual([3n]);
  });
});

describe("helpers", () => {
  it("toPlain converts nested Maps, bigints and arrays", () => {
    const v = toPlain(new Map<string, unknown>([["a", new Map([["b", 5n]])], ["c", [1n, 2n]], ["big", 10n ** 40n]]));
    expect(v).toEqual({ a: { b: 5 }, c: [1, 2], big: (10n ** 40n).toString() });
  });
  it("raceSignal rejects immediately when already aborted", async () => {
    const ac = new AbortController();
    ac.abort();
    await expect(raceSignal(Promise.resolve(1), ac.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
  it("raceSignal rejects when aborted mid-flight", async () => {
    const ac = new AbortController();
    const p = raceSignal(new Promise(() => undefined), ac.signal);
    ac.abort();
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });
  it("raceSignal passes values through", async () => {
    await expect(raceSignal(Promise.resolve(9), new AbortController().signal)).resolves.toBe(9);
  });
});
