import { describe, expect, it } from "vitest";

import {
  BPS,
  RAY,
  SECONDS_PER_YEAR,
  capacity,
  ceilDivPos,
  debtFromScaled,
  healthStatus,
  isOverLtv,
  ltvBps,
  maxBorrow,
  previewBorrow,
  previewWithdraw,
  projectIndex,
  scaledFromBorrow,
} from "./lending";

const GEN = 10n ** 18n;

describe("integer helpers", () => {
  it.each([[7n, 2n, 4n], [8n, 2n, 4n], [0n, 5n, 0n], [1n, 5n, 1n], [10n ** 30n + 1n, 10n ** 30n, 2n]])(
    "ceilDivPos(%s, %s) = %s",
    (a, b, out) => expect(ceilDivPos(a, b)).toBe(out),
  );
  it("rejects a non-positive divisor", () => {
    expect(() => ceilDivPos(1n, 0n)).toThrow(RangeError);
  });
  it("capacity floors", () => {
    expect(capacity(1n, 7500)).toBe(0n);
    expect(capacity(10001n, 7500)).toBe(7500n);
    expect(capacity(GEN, 8000)).toBe((GEN * 8n) / 10n);
  });
});

describe("LTV checks use cross-multiplication (no division, no rounding)", () => {
  it("boundary is exact", () => {
    expect(isOverLtv(GEN, (GEN * 75n) / 100n, 7500)).toBe(false);
    expect(isOverLtv(GEN, (GEN * 75n) / 100n + 1n, 7500)).toBe(true);
  });
  it("health statuses", () => {
    expect(healthStatus(GEN, 0n, 7500)).toBe("NO_DEBT");
    expect(healthStatus(GEN, GEN / 2n, 7500)).toBe("HEALTHY");
    expect(healthStatus(GEN, GEN, 7500)).toBe("OVER_LTV");
  });
  it("ltvBps floors and handles zero collateral", () => {
    expect(ltvBps(GEN, GEN / 2n)).toBe(5000);
    expect(ltvBps(3n, 1n)).toBe(3333);
    expect(ltvBps(0n, 0n)).toBe(0);
    expect(ltvBps(0n, 1n)).toBe(1_000_000_000);
  });
});

describe("scaled debt rounds against the borrower", () => {
  it("borrow never records less debt than requested", () => {
    for (const idx of [RAY, RAY + 1n, RAY + 123456789n, (RAY * 1055n) / 1000n]) {
      for (const amt of [1n, 2n, 7n, 1000n, GEN]) {
        expect(debtFromScaled(scaledFromBorrow(amt, idx), idx)).toBeGreaterThanOrEqual(amt);
      }
    }
  });
  it("is exact at index == RAY", () => {
    expect(debtFromScaled(scaledFromBorrow(12345n, RAY), RAY)).toBe(12345n);
  });
});

describe("maxBorrow", () => {
  it("equals floor(collateral * ltv) at unit index", () => {
    expect(maxBorrow(GEN, 0n, RAY, 7500)).toBe((GEN * 75n) / 100n);
  });
  it("is zero when there is no room", () => {
    expect(maxBorrow(GEN, scaledFromBorrow((GEN * 75n) / 100n, RAY), RAY, 7500)).toBe(0n);
    expect(maxBorrow(1n, 0n, RAY, 7500)).toBe(0n);
  });
  it("is borrowable and one more is not, above unit index", () => {
    const idx = RAY + 987654321987654321n;
    const a = maxBorrow(GEN, 0n, idx, 6500);
    expect(a).toBeGreaterThan(0n);
    expect(previewBorrow(GEN, 0n, idx, a, 6500).ok).toBe(true);
    expect(previewBorrow(GEN, 0n, idx, a + 1n, 6500).ok).toBe(false);
    expect((GEN * 65n) / 100n - a).toBeLessThanOrEqual(2n);
  });
});

describe("previews", () => {
  it("borrow without collateral", () => {
    expect(previewBorrow(0n, 0n, RAY, 1n, 7500)).toEqual({ ok: false, reason: "no-collateral" });
  });
  it("borrow over LTV", () => {
    expect(previewBorrow(GEN, 0n, RAY, GEN, 7500)).toEqual({ ok: false, reason: "ltv-exceeded" });
  });
  it("borrow within LTV reports new debt and LTV", () => {
    expect(previewBorrow(GEN, 0n, RAY, GEN / 2n, 7500)).toEqual({ ok: true, newDebt: GEN / 2n, newLtvBps: 5000 });
  });
  it("withdraw bounds", () => {
    expect(previewWithdraw(GEN, 0n, GEN + 1n, 7500)).toEqual({ ok: false, reason: "exceeds-collateral" });
    expect(previewWithdraw(GEN, GEN / 2n, GEN / 2n, 7500)).toEqual({ ok: false, reason: "ltv-exceeded" });
    expect(previewWithdraw(GEN, 0n, GEN, 7500)).toEqual({ ok: true, remaining: 0n, newLtvBps: 0 });
  });
});

describe("index projection mirrors the contract formula", () => {
  it.each([
    [550, 1n],
    [550, 86400n],
    [350, SECONDS_PER_YEAR],
    [900, SECONDS_PER_YEAR],
    [900, 7n * 86400n + 13n],
  ])("rate %i bps for %s s", (rate, dt) => {
    const expected = RAY + ceilDivPos(RAY * BigInt(rate) * dt, BPS * SECONDS_PER_YEAR);
    expect(projectIndex(RAY, rate, dt)).toBe(expected);
  });
  it("one year at 5.5% is exactly +5.5%", () => {
    expect(projectIndex(RAY, 550, SECONDS_PER_YEAR)).toBe((RAY * 1055n) / 1000n);
  });
  it("is the identity for non-positive elapsed time", () => {
    expect(projectIndex(RAY, 550, 0n)).toBe(RAY);
    expect(projectIndex(RAY, 550, -5n)).toBe(RAY);
  });
  it("splitting never undercharges", () => {
    let idx = RAY;
    for (let i = 0; i < 40; i++) idx = projectIndex(idx, 550, 5n);
    expect(idx).toBeGreaterThanOrEqual(projectIndex(RAY, 550, 200n));
  });
});

describe("maxBorrow is exact at any index magnitude (audit finding #7)", () => {
  // Reference: the largest `a` the contract's borrow rule accepts, by bisection.
  function brute(collateral: bigint, scaled: bigint, idx: bigint, ltv: number): bigint {
    const dMax = capacity(collateral, ltv);
    let lo = 0n;
    let hi = dMax;
    while (lo < hi) {
      const mid = (lo + hi + 1n) / 2n;
      const debt = debtFromScaled(scaled + scaledFromBorrow(mid, idx), idx);
      if (debt <= dMax) lo = mid;
      else hi = mid - 1n;
    }
    return lo;
  }

  it.each([1n, 2n, 3n, 5n, 25n, 500n, 5000n, 10n ** 6n])("index = %sx RAY", (mult) => {
    for (const idx of [RAY * mult, RAY * mult + 123456789012345678901n, RAY * mult + 1n]) {
      for (const [c, s, ltv] of [
        [GEN, 0n, 7500],
        [3n * GEN + 17n, 0n, 8000],
        [10n * GEN, scaledFromBorrow(GEN, idx), 6500],
        [999_999_999_999_999n, 0n, 7500],
      ] as [bigint, bigint, number][]) {
        const got = maxBorrow(c, s, idx, ltv);
        expect(got).toBe(brute(c, s, idx, ltv));
        // and it is borrowable, one more is not
        if (got > 0n) expect(previewBorrow(c, s, idx, got, ltv).ok).toBe(true);
        expect(previewBorrow(c, s, idx, got + 1n, ltv).ok).toBe(false);
      }
    }
  });

  it("does not report 0 for a borrowable position once the index passed 3x (old walk-down bug)", () => {
    const idx = RAY * 25n;
    expect(maxBorrow(10n * GEN, 0n, idx, 7500)).toBeGreaterThan(0n);
  });

  it("is zero when already over the ceiling", () => {
    expect(maxBorrow(GEN, scaledFromBorrow(GEN, RAY), RAY, 7500)).toBe(0n);
  });

  it("never exceeds the ideal floor(collateral * ltv)", () => {
    for (const mult of [1n, 7n, 90n]) {
      const idx = RAY * mult;
      expect(maxBorrow(GEN, 0n, idx, 7500)).toBeLessThanOrEqual(capacity(GEN, 7500));
    }
  });
});
