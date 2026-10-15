import { describe, expect, it } from "vitest";

import { SerialLoader } from "./serialLoader";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("SerialLoader: values and failures", () => {
  it("loads values", async () => {
    const l = new SerialLoader<number>(async (id) => id.length);
    await l.refresh(["a", "bb", "ccc"]);
    expect([...l.getState().values.entries()]).toEqual([["a", 1], ["bb", 2], ["ccc", 3]]);
    expect(l.getState().failedIds.size).toBe(0);
    expect(l.getState().lastCompletedAt).not.toBeNull();
  });

  it("records a partial failure and keeps the other items", async () => {
    const l = new SerialLoader<number>(async (id) => {
      if (id === "bad") throw new Error("rpc 502");
      return 1;
    });
    await l.refresh(["ok", "bad"]);
    const s = l.getState();
    expect([...s.failedIds]).toEqual(["bad"]);
    expect(s.values.get("ok")).toBe(1);
    expect(s.errors.get("bad")).toBe("rpc 502");
    expect(l.hasPartialFailure).toBe(true);
  });

  it("a later success of OTHER items never masks an earlier failure", async () => {
    let n = 0;
    const l = new SerialLoader<number>(async (id) => {
      if (id === "b") throw new Error("down");
      return ++n;
    });
    await l.refresh(["a", "b"]);
    await l.refresh(["a", "b"]);
    await l.refresh(["a", "b"]);
    expect([...l.getState().failedIds]).toEqual(["b"]);
  });

  it("an item leaves failedIds only when that item reads successfully", async () => {
    let bHealthy = false;
    const l = new SerialLoader<string>(async (id) => {
      if (id === "b" && !bHealthy) throw new Error("down");
      return id;
    });
    await l.refresh(["a", "b"]);
    expect(l.getState().failedIds.has("b")).toBe(true);
    bHealthy = true;
    await l.refresh(["a", "b"]);
    expect(l.getState().failedIds.size).toBe(0);
    expect(l.getState().values.get("b")).toBe("b");
  });

  it("keeps the last good value (stale) when a re-read fails", async () => {
    let fail = false;
    const l = new SerialLoader<number>(async () => {
      if (fail) throw new Error("x");
      return 7;
    });
    await l.refresh(["a"]);
    fail = true;
    await l.refresh(["a"]);
    expect(l.getState().values.get("a")).toBe(7);
    expect(l.getState().failedIds.has("a")).toBe(true);
  });

  it("drops items that are no longer requested, with their failure", async () => {
    const l = new SerialLoader<number>(async (id) => {
      if (id === "gone") throw new Error("x");
      return 1;
    });
    await l.refresh(["keep", "gone"]);
    expect(l.getState().failedIds.has("gone")).toBe(true);
    await l.refresh(["keep"]);
    expect(l.getState().failedIds.size).toBe(0);
    expect(l.getState().values.has("gone")).toBe(false);
  });

  it("de-duplicates requested ids", async () => {
    let calls = 0;
    const l = new SerialLoader<number>(async () => ++calls);
    await l.refresh(["a", "a", "a"]);
    expect(calls).toBe(1);
  });
});

describe("SerialLoader: single flight, no overlapping abort loops", () => {
  it("never runs two loads at once", async () => {
    let active = 0;
    let max = 0;
    const gate = deferred<void>();
    const l = new SerialLoader<number>(async () => {
      active++;
      max = Math.max(max, active);
      await gate.promise;
      active--;
      return 1;
    });
    const p1 = l.refresh(["a"]);
    const p2 = l.refresh(["a"]);
    const p3 = l.refresh(["a"]);
    gate.resolve();
    await Promise.all([p1, p2, p3]);
    expect(max).toBe(1);
  });

  it("coalesces many requests during a load into exactly one follow-up", async () => {
    let calls = 0;
    const gate = deferred<void>();
    const l = new SerialLoader<number>(async () => {
      calls++;
      if (calls === 1) await gate.promise;
      return calls;
    });
    const first = l.refresh(["a"]);
    for (let i = 0; i < 10; i++) void l.refresh(["a"]);
    gate.resolve();
    await first;
    expect(calls).toBe(2);
  });

  it("does not abort the in-flight load when a tick arrives", async () => {
    const aborted: boolean[] = [];
    const gate = deferred<void>();
    const l = new SerialLoader<number>(async (_id, signal) => {
      await gate.promise;
      aborted.push(signal.aborted);
      return 1;
    });
    const p = l.refresh(["a"]);
    void l.refresh(["a"]);
    gate.resolve();
    await p;
    expect(aborted.every((a) => a === false)).toBe(true);
    expect(l.getState().values.get("a")).toBe(1);
  });

  it("the follow-up pass reads the LATEST requested ids", async () => {
    const seen: string[][] = [];
    let current: string[] = [];
    const gate = deferred<void>();
    const l = new SerialLoader<number>(async (id) => {
      current.push(id);
      if (seen.length === 0) await gate.promise;
      return 1;
    });
    const p = l.refresh(["a"]);
    void l.refresh(["b"]);
    void l.refresh(["c"]);
    gate.resolve();
    seen.push(current);
    await p;
    expect(current).toEqual(["a", "c"]);
  });

  it("loading flag toggles and notifies subscribers", async () => {
    const l = new SerialLoader<number>(async () => 1);
    const seen: boolean[] = [];
    l.subscribe(() => seen.push(l.getState().loading));
    await l.refresh(["a"]);
    expect(seen[0]).toBe(true);
    expect(seen[seen.length - 1]).toBe(false);
    expect(l.getState().loading).toBe(false);
  });
});

describe("SerialLoader: account switching", () => {
  it("reset clears values, failures and errors", async () => {
    const l = new SerialLoader<number>(async (id) => {
      if (id === "bad") throw new Error("x");
      return 1;
    });
    await l.refresh(["ok", "bad"]);
    l.reset();
    const s = l.getState();
    expect(s.values.size + s.failedIds.size + s.errors.size).toBe(0);
    expect(s.lastCompletedAt).toBeNull();
  });

  it("results arriving after a reset are discarded", async () => {
    const gate = deferred<void>();
    const l = new SerialLoader<string>(async (id) => {
      await gate.promise;
      return `old-account-${id}`;
    });
    const p = l.refresh(["a"]);
    l.reset(); // wallet switched
    gate.resolve();
    await p;
    expect(l.getState().values.size).toBe(0);
  });

  it("failures arriving after a reset are discarded too", async () => {
    const gate = deferred<void>();
    const l = new SerialLoader<string>(async () => {
      await gate.promise;
      throw new Error("old account failed");
    });
    const p = l.refresh(["a"]);
    l.reset();
    gate.resolve();
    await p;
    expect(l.getState().failedIds.size).toBe(0);
  });

  it("aborts the signal handed to in-flight readers on reset", async () => {
    let captured: AbortSignal | null = null;
    const gate = deferred<void>();
    const l = new SerialLoader<number>(async (_id, signal) => {
      captured = signal;
      await gate.promise;
      return 1;
    });
    const p = l.refresh(["a"]);
    await Promise.resolve();
    l.reset();
    expect(captured!.aborted).toBe(true);
    gate.resolve();
    await p;
  });

  it("works normally after a reset (new generation)", async () => {
    const l = new SerialLoader<number>(async () => 5);
    await l.refresh(["a"]);
    const g0 = l.getState().generation;
    l.reset();
    await l.refresh(["b"]);
    expect(l.getState().generation).toBe(g0 + 1);
    expect([...l.getState().values.entries()]).toEqual([["b", 5]]);
  });
});

describe("SerialLoader: readAt is stamped on SUCCESS only", () => {
  it("records the time of each successful read", async () => {
    let clock = 100;
    const l = new SerialLoader<number>(async () => 1, () => clock);
    await l.refresh(["a"]);
    expect(l.getState().readAt.get("a")).toBe(100);
    clock = 250;
    await l.refresh(["a"]);
    expect(l.getState().readAt.get("a")).toBe(250);
  });

  it("a failed re-read leaves the previous success time untouched", async () => {
    let clock = 100;
    let fail = false;
    const l = new SerialLoader<number>(async () => {
      if (fail) throw new Error("rpc down");
      return 1;
    }, () => clock);
    await l.refresh(["a"]);
    fail = true;
    clock = 900;
    await l.refresh(["a"]);
    expect(l.getState().readAt.get("a")).toBe(100); // not 900
    expect(l.getState().failedIds.has("a")).toBe(true);
  });

  it("an item that never succeeded has no readAt", async () => {
    const l = new SerialLoader<number>(async () => {
      throw new Error("x");
    });
    await l.refresh(["a"]);
    expect(l.getState().readAt.has("a")).toBe(false);
  });

  it("reset clears readAt", async () => {
    const l = new SerialLoader<number>(async () => 1);
    await l.refresh(["a"]);
    l.reset();
    expect(l.getState().readAt.size).toBe(0);
  });
});
