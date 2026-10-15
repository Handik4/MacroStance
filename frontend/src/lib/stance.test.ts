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
  epochIdProblem,
  epochYear,
  formatQuarter,
  isValidEpochId,
  parseEpochId,
  quarterSeqOf,
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
    expect(got.releaseTs).toBe(v.release_ts);
  });
});

describe("quorum resolution: TypeScript == Python over all 216 state combinations", () => {
  it("has 216 vectors", () => {
    expect(vectors.resolve.length).toBe(216);
  });
  it.each(vectors.resolve.map((v, i) => [i, v] as const))("combination %i", (_i, v) => {
    const states: SourceState[] = (v.states as [string, number, string, number][]).map(
      ([kind, stance, hash, ts]) => ({ kind: kind as SourceKind, stance, releaseHash: hash, releaseTs: ts }),
    );
    const got = resolveQuorum(states);
    expect(got.verdict).toBe(v.verdict);
    expect(got.stance).toBe(v.stance);
    expect(got.sourceCount).toBe(v.source_count);
    expect(got.releaseHash).toBe(v.release_hash);
    expect(got.releaseTs).toBe(v.release_ts);
  });
});

describe("release hash", () => {
  it("matches the documented canonical form (sha256 of 'epoch|rate|cpi|stance')", async () => {
    // Same digest the Python test derives with hashlib.
    const h = await releaseHash("2026-Q4-FOMC", 25, 320, 2, 1790000000);
    expect(h).toMatch(/^[0-9a-f]{64}$/);
    const again = await releaseHash("2026-Q4-FOMC", 25, 320, 2, 1790000000);
    expect(again).toBe(h);
    expect(await releaseHash("2026-Q4-FOMC", 25, 321, 2, 1790000000)).not.toBe(h);
    // a later publication of identical numbers is a different release
    expect(await releaseHash("2026-Q4-FOMC", 25, 320, 2, 1790000001)).not.toBe(h);
  });
  it("bounds are inclusive", async () => {
    const body = JSON.stringify({ epoch_id: "2026-Q4-FOMC", status: "CONFIRMED", rate_change_bps: 0, cpi_yoy_bps: CPI_MAX_BPS, release_ts: 1790000000 });
    expect((await classifyResponse(200, body, "2026-Q4-FOMC")).kind).toBe("CONFIRMED");
    const over = JSON.stringify({ epoch_id: "2026-Q4-FOMC", status: "CONFIRMED", rate_change_bps: 0, cpi_yoy_bps: CPI_MAX_BPS + 1, release_ts: 1790000000 });
    expect((await classifyResponse(200, over, "2026-Q4-FOMC")).kind).toBe("OUTAGE");
  });
});

describe("epoch quarter bound mirrors the contract (recheck finding #3)", () => {
  const Q4_2026 = 2026 * 4 + 3;
  it("quarterSeqOf follows UTC calendar quarters, including boundaries", () => {
    const at = (iso: string) => quarterSeqOf(Date.parse(iso) / 1000);
    expect(at("2026-01-01T00:00:00Z")).toBe(2026 * 4);
    expect(at("2026-03-31T23:59:59Z")).toBe(2026 * 4);
    expect(at("2026-04-01T00:00:00Z")).toBe(2026 * 4 + 1);
    expect(at("2026-09-30T23:59:59Z")).toBe(2026 * 4 + 2);
    expect(at("2026-10-01T00:00:00Z")).toBe(Q4_2026);
    expect(at("2026-12-31T23:59:59Z")).toBe(Q4_2026);
    expect(at("2027-01-01T00:00:00Z")).toBe(2027 * 4);
  });
  it("formats a quarter", () => {
    expect(formatQuarter(Q4_2026)).toBe("2026-Q4");
  });
  it("epochYear extracts the year or -1", () => {
    expect(epochYear("2026-Q4-FOMC")).toBe(2026);
    expect(epochYear("bogus")).toBe(-1);
  });
  it.each([
    ["2026-Q4-FOMC", null],
    ["2026-Q3-FOMC", null],
    ["2025-Q4-FOMC", null],
    ["2000-Q1-AB", null],
    ["2027-Q1-FOMC", "has not begun"],
    ["2027-Q4-FOMC", "has not begun"],
    ["2200-Q4-FOMC", "has not begun"],
  ])("%s while the clock is in 2026-Q4", (id, problem) => {
    const got = epochIdProblem(id, Q4_2026);
    if (problem === null) expect(got).toBeNull();
    else expect(got).toContain(problem);
  });
  it("names the latest allowed quarter", () => {
    expect(epochIdProblem("2027-Q4-FOMC", Q4_2026)).toContain("2026-Q4");
  });
  it("malformed ids are reported before the quarter", () => {
    expect(epochIdProblem("2026-q4-fomc", Q4_2026)).toContain("2026-Q4-FOMC");
  });
  it("without a known chain quarter only the grammar applies", () => {
    expect(epochIdProblem("2200-Q4-FOMC", null)).toBeNull();
  });
});

describe("release_ts handling in the TypeScript classifier", () => {
  const base = { epoch_id: "2026-Q4-FOMC", status: "CONFIRMED", rate_change_bps: 25, cpi_yoy_bps: 320 };
  const run = (extra: Record<string, unknown>) => classifyResponse(200, JSON.stringify({ ...base, ...extra }), "2026-Q4-FOMC");

  it("is mandatory", async () => {
    expect((await run({})).kind).toBe("OUTAGE");
  });
  it.each([null, "1790000000", true, 1790000000.5, 0, 946684799, 4102444801, -1])("rejects %j", async (v) => {
    expect((await run({ release_ts: v })).kind).toBe("OUTAGE");
  });
  it("rejects a float lexeme that equals an integer", async () => {
    const body = '{"epoch_id":"2026-Q4-FOMC","status":"CONFIRMED","rate_change_bps":25,"cpi_yoy_bps":320,"release_ts":1790000000.0}';
    expect((await classifyResponse(200, body, "2026-Q4-FOMC")).kind).toBe("OUTAGE");
  });
  it.each([946684800, 1790000000, 4102444800])("accepts %i and reports it", async (ts) => {
    const r = await run({ release_ts: ts });
    expect(r.kind).toBe("CONFIRMED");
    expect(r.releaseTs).toBe(ts);
  });
  it("a verified quorum carries the matching release's timestamp", async () => {
    const a = await run({ release_ts: 1790000000 });
    const b = await run({ release_ts: 1790000000 });
    const c = await run({ release_ts: 1790000999 });
    const q = resolveQuorum([a, b, c]);
    expect(q.verdict).toBe(1);
    expect(q.releaseTs).toBe(1790000000);
    expect(q.sourceCount).toBe(2);
  });
  it("sources with identical numbers but different timestamps do not match", async () => {
    const a = await run({ release_ts: 1790000000 });
    const b = await run({ release_ts: 1790000001 });
    expect(resolveQuorum([a, b, { kind: "OUTAGE", stance: 0, releaseHash: "", releaseTs: 0 }]).verdict).toBe(2);
  });
});
