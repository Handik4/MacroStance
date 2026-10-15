import { describe, expect, it } from "vitest";

import { UNKNOWN, displayGen, displayText, knownValue } from "./display";

describe("unknown is never rendered as zero", () => {
  it("null renders as an em dash, not '0 GEN'", () => {
    expect(displayGen(null, " GEN")).toBe("—");
    expect(displayGen(null)).toBe(UNKNOWN);
  });
  it("a genuine zero still renders as 0", () => {
    expect(displayGen(0n, " GEN")).toBe("0 GEN");
  });
  it("formats known amounts exactly", () => {
    expect(displayGen(1_500_000_000_000_000_000n, " GEN")).toBe("1.5 GEN");
  });
  it("a failed read collapses to unknown even when a stale value is held", () => {
    expect(knownValue(5n, { failed: true })).toBeNull();
  });
  it("a missing value is unknown", () => {
    expect(knownValue(null, { failed: false })).toBeNull();
    expect(knownValue(undefined, { failed: false })).toBeNull();
  });
  it("a present, non-failed value passes through (including zero)", () => {
    expect(knownValue(0n, { failed: false })).toBe(0n);
    expect(knownValue("x", { failed: false })).toBe("x");
  });
  it("displayText falls back to the dash", () => {
    expect(displayText(null)).toBe("—");
    expect(displayText(7)).toBe("7");
    expect(displayText(null, "n/a")).toBe("n/a");
  });
});
