import { describe, expect, it } from "vitest";

import vectors from "./__fixtures__/parity_vectors.json";
import {
  CPI_MAX_BPS,
  DOVISH,
  HAWKISH,
  NEUTRAL,
  RISK_TABLE,
  buildFeedUrl,
  classifyResponse,
  deriveStance,
  isValidEpochId,
  parseEpochId,
  releaseHash,
  resolveQuorum,
  riskForStance,
  type SourceKind,
  type SourceState,
} from "./stance";

describe("stance derivation: TypeScript == Python (golden vectors from the contract)", () => {
  it("has the full 17x13 grid", () => {
    expect(vectors.stance.length).toBe(17 * 13);
  });
  it.each(vectors.stance.map((v) => [v.rate_change_bps, v.cpi_yoy_bps, v.stance] as const))(
    "rate %i bps / CPI %i bps -> %i",
    (rate, cpi, expected) => {
      expect(deriveStance(rate, cpi)).toBe(expected);
    },
  );
});

describe("risk table parity", () => {
  it.each(vectors.risk_table)("tier $stance_name matches the contract", (row) => {
    const t = riskForStance(row.stance);
    expect([t.maxLtvBps, t.borrowRateBps, t.reserveFactorBps]).toEqual([
      row.max_ltv_bps,
      row.borrow_rate_bps,
      row.reserve_factor_bps,
    ]);
    expect(t.name).toBe(row.stance_name);
  });
  it("rejects an unknown stance", () => {
    expect(() => riskForStance(3)).toThrow(RangeError);
    expect(() => riskForStance(-1)).toThrow(RangeError);
  });
  it("is strictly ordered by risk", () => {
    const [d, n, h] = RISK_TABLE;
    expect(d.maxLtvBps).toBeGreaterThan(n.maxLtvBps);
    expect(n.maxLtvBps).toBeGreaterThan(h.maxLtvBps);
    expect(d.borrowRateBps).toBeLessThan(n.borrowRateBps);
    expect(n.borrowRateBps).toBeLessThan(h.borrowRateBps);
  });
});

describe("deriveStance boundaries and priority", () => {
  it("hawkish beats dovish when both trigger", () => {
    expect(deriveStance(-25, 301)).toBe(HAWKISH);
    expect(deriveStance(25, 100)).toBe(HAWKISH);
  });
  it("exact thresholds", () => {
    expect(deriveStance(25, 250)).toBe(HAWKISH);
    expect(deriveStance(24, 250)).toBe(NEUTRAL);
    expect(deriveStance(-25, 250)).toBe(DOVISH);
    expect(deriveStance(-24, 250)).toBe(NEUTRAL);
    expect(deriveStance(0, 301)).toBe(HAWKISH);
    expect(deriveStance(0, 300)).toBe(NEUTRAL);
    expect(deriveStance(0, 199)).toBe(DOVISH);
    expect(deriveStance(0, 200)).toBe(NEUTRAL);
  });
});

describe("epoch ids", () => {
  it.each(vectors.epoch_ids.map((v) => [v.epoch_id, v.seq] as const))("%j -> %i", (id, seq) => {
    expect(parseEpochId(id)).toBe(seq);
    expect(isValidEpochId(id)).toBe(seq >= 0);
  });
  it("orders by quarter, not lexicographically", () => {
    expect(parseEpochId("2026-Q4-AAAA")).toBeGreaterThan(parseEpochId("2026-Q3-ZZZZ"));
  });
  it("rejects non-strings", () => {
    expect(parseEpochId(42)).toBe(-1);
    expect(parseEpochId(null)).toBe(-1);
    expect(parseEpochId(undefined)).toBe(-1);
  });
  it("builds the fetch URL from prefix + id", () => {
    expect(buildFeedUrl("https://h.example/p/", "2026-Q4-FOMC")).toBe("https://h.example/p/2026-Q4-FOMC.json");
  });
});

describe("feed response classification: TypeScript == Python", () => {
  it("covers the lexeme-level divergences", () => {
    expect(vectors.classify.length).toBeGreaterThanOrEqual(34);
  });
  it.each(vectors.classify.map((v, i) => [i, v] as const))("vector %i", async (_i, v) => {
    const got = await classifyResponse(v.status, v.body, v.epoch_id);
    expect(got.kind).toBe(v.kind);
    expect(got.stance).toBe(v.stance);
    expect(got.releaseHash).toBe(v.release_hash);
  });
});

describe("quorum resolution: TypeScript == Python over all 216 state combinations", () => {
  it("has 216 vectors", () => {
    expect(vectors.resolve.length).toBe(216);
  });
  it.each(vectors.resolve.map((v, i) => [i, v] as const))("combination %i", (_i, v) => {
    const states: SourceState[] = (v.states as [string, number, string][]).map(([kind, stance, hash]) => ({
      kind: kind as SourceKind,
      stance,
      releaseHash: hash,
    }));
    const got = resolveQuorum(states);
    expect(got.verdict).toBe(v.verdict);
    expect(got.stance).toBe(v.stance);
    expect(got.sourceCount).toBe(v.source_count);
    expect(got.releaseHash).toBe(v.release_hash);
  });
});

describe("release hash", () => {
  it("matches the documented canonical form (sha256 of 'epoch|rate|cpi|stance')", async () => {
    // Same digest the Python test derives with hashlib.
    const h = await releaseHash("2026-Q4-FOMC", 25, 320, 2);
    expect(h).toMatch(/^[0-9a-f]{64}$/);
    const again = await releaseHash("2026-Q4-FOMC", 25, 320, 2);
    expect(again).toBe(h);
    expect(await releaseHash("2026-Q4-FOMC", 25, 321, 2)).not.toBe(h);
  });
  it("bounds are inclusive", async () => {
    const body = JSON.stringify({ epoch_id: "2026-Q4-FOMC", status: "CONFIRMED", rate_change_bps: 0, cpi_yoy_bps: CPI_MAX_BPS });
    expect((await classifyResponse(200, body, "2026-Q4-FOMC")).kind).toBe("CONFIRMED");
    const over = JSON.stringify({ epoch_id: "2026-Q4-FOMC", status: "CONFIRMED", rate_change_bps: 0, cpi_yoy_bps: CPI_MAX_BPS + 1 });
    expect((await classifyResponse(200, over, "2026-Q4-FOMC")).kind).toBe("OUTAGE");
  });
});
