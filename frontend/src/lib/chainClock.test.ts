import { describe, expect, it } from "vitest";

import { ChainClock } from "./chainClock";

function clock() {
  let t = 0;
  const c = new ChainClock(() => t);
  return { c, advance: (ms: number) => (t += ms) };
}

describe("ChainClock: steady interval from the last verified chain time", () => {
  it("is null before the first verified read", () => {
    expect(clock().c.estimate()).toBeNull();
    expect(clock().c.anchored).toBe(false);
  });

  it("returns the observed time, then advances one second per second", () => {
    const { c, advance } = clock();
    c.observe(1_000);
    expect(c.estimate()).toBe(1_000);
    advance(5_000);
    expect(c.estimate()).toBe(1_005);
    advance(60_000);
    expect(c.estimate()).toBe(1_065);
  });

  it("floors partial seconds", () => {
    const { c, advance } = clock();
    c.observe(100);
    advance(2_999);
    expect(c.estimate()).toBe(102);
  });

  it("keeps counting forward across failed polls (no observe calls)", () => {
    const { c, advance } = clock();
    c.observe(10_000);
    const seen: number[] = [];
    for (let i = 0; i < 20; i++) {
      advance(15_000); // each poll attempt FAILS: nothing is observed
      seen.push(c.estimate() as number);
    }
    for (let i = 1; i < seen.length; i++) expect(seen[i]).toBeGreaterThan(seen[i - 1]);
    expect(seen[19]).toBe(10_000 + 300);
  });

  it("is monotone even when a lagging node later reports an OLDER block time", () => {
    const { c, advance } = clock();
    c.observe(5_000);
    advance(120_000); // estimate 5_120
    expect(c.estimate()).toBe(5_120);
    c.observe(5_050); // stale node: 70 s behind our estimate
    expect(c.estimate()).toBeGreaterThanOrEqual(5_120); // never goes backward
    advance(10_000);
    expect(c.estimate()).toBeGreaterThanOrEqual(5_120);
  });

  it("holds flat while a stale anchor catches up, then resumes", () => {
    const { c, advance } = clock();
    c.observe(5_000);
    advance(100_000); // 5_100
    c.observe(5_000); // very stale
    expect(c.estimate()).toBe(5_100);
    advance(50_000); // anchor line is 5_050 < floor 5_100
    expect(c.estimate()).toBe(5_100);
    advance(60_000); // anchor line 5_110
    expect(c.estimate()).toBe(5_110);
  });

  it("jumps forward immediately to a newer verified time", () => {
    const { c, advance } = clock();
    c.observe(1_000);
    advance(3_000);
    c.observe(1_500);
    expect(c.estimate()).toBe(1_500);
  });

  it("rejects non-finite and non-positive observations", () => {
    const { c } = clock();
    c.observe(NaN);
    c.observe(0);
    c.observe(-5);
    c.observe(Infinity);
    expect(c.estimate()).toBeNull();
  });

  it("a bad observation after a good one does not disturb the clock", () => {
    const { c, advance } = clock();
    c.observe(2_000);
    advance(4_000);
    c.observe(NaN);
    expect(c.estimate()).toBe(2_004);
  });

  it("an activation countdown never increases between consecutive renders", () => {
    const { c, advance } = clock();
    const activatesAt = 100_000 + 86_400;
    c.observe(100_000);
    let last = Infinity;
    for (let i = 0; i < 50; i++) {
      advance(1_000);
      if (i % 7 === 3) c.observe(100_000 + i - 40); // intermittently stale reads
      const remaining = activatesAt - (c.estimate() as number);
      expect(remaining).toBeLessThanOrEqual(last);
      last = remaining;
    }
  });
});
