import { describe, expect, it } from "vitest";

import {
  WEI_PER_GEN,
  formatBps,
  formatGen,
  isAddress,
  normalizeAddress,
  parseGenAmount,
  parseWei,
} from "./inputs";

describe("parseGenAmount: accepted", () => {
  it.each([
    ["1", 1n * WEI_PER_GEN],
    ["0.1", WEI_PER_GEN / 10n],
    ["0.000000000000000001", 1n],
    ["12.5", 125n * WEI_PER_GEN / 10n],
    ["100", 100n * WEI_PER_GEN],
    ["0.123456789012345678", 123456789012345678n],
    ["999999999999", 999999999999n * WEI_PER_GEN],
  ])("%s", (input, wei) => {
    const r = parseGenAmount(input);
    expect(r).toEqual({ ok: true, wei });
  });
});

describe("parseGenAmount: rejected", () => {
  it.each([
    ["", "empty"],
    [" ", "space"],
    [" 1", "leading space"],
    ["1 ", "trailing space"],
    ["0", "zero"],
    ["0.0", "zero decimal"],
    ["0.000000000000000000", "zero 18dp"],
    ["-1", "negative"],
    ["-0", "negative zero"],
    ["-0.5", "negative decimal"],
    ["+1", "plus sign"],
    ["1e3", "scientific"],
    ["1E3", "scientific upper"],
    ["1e-3", "scientific negative exponent"],
    ["0x10", "hex"],
    [".5", "leading dot"],
    ["5.", "trailing dot"],
    ["1,000", "thousands separator"],
    ["1,5", "decimal comma"],
    ["1 000", "inner space"],
    ["00.1", "leading zeros"],
    ["007", "leading zeros int"],
    ["1.2.3", "two dots"],
    ["abc", "letters"],
    ["1.5GEN", "unit suffix"],
    ["Infinity", "infinity"],
    ["NaN", "nan"],
    ["0.0000000000000000001", "19 decimals"],
    ["١٢٣", "arabic-indic digits"],
    ["1\n", "newline"],
    ["1_000", "underscore"],
    ["10000000000000000000000000000000", "above ceiling"],
  ])("%j (%s)", (input) => {
    const r = parseGenAmount(input);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error.length).toBeGreaterThan(0);
  });
  it("honours a caller-supplied maximum", () => {
    expect(parseGenAmount("2", { maxWei: WEI_PER_GEN }).ok).toBe(false);
    expect(parseGenAmount("1", { maxWei: WEI_PER_GEN }).ok).toBe(true);
  });
  it("is exact: no float rounding for values float64 cannot hold", () => {
    // 1e11 GEN + 1 wei: inside the 1e30-wei ceiling, far beyond 53 bits of float64.
    const r = parseGenAmount("100000000000.000000000000000001");
    expect(r).toEqual({ ok: true, wei: 100000000000n * WEI_PER_GEN + 1n });
    expect(Number(100000000000n * WEI_PER_GEN + 1n)).toBe(Number(100000000000n * WEI_PER_GEN)); // float64 cannot see the +1
  });
});

describe("formatGen / formatBps", () => {
  it.each([
    [0n, "0"], [1n, "0.000000000000000001"], [WEI_PER_GEN, "1"], [WEI_PER_GEN / 10n, "0.1"],
    [1500000000000000000n, "1.5"], [-WEI_PER_GEN, "-1"],
  ])("formatGen(%s) = %s", (wei, out) => {
    expect(formatGen(wei)).toBe(out);
  });
  it("round-trips through parseGenAmount", () => {
    for (const s of ["0.1", "3.14159", "42", "0.000000000000000007"]) {
      const r = parseGenAmount(s);
      expect(r.ok && formatGen(r.wei)).toBe(s);
    }
  });
  it.each([[7500, "75.00%"], [350, "3.50%"], [1, "0.01%"], [10000, "100.00%"], [0, "0.00%"]])(
    "formatBps(%i)",
    (bps, out) => {
      expect(formatBps(bps)).toBe(out);
    },
  );
});

describe("addresses and wei strings", () => {
  const good = "0x81b637d8fCD2C6da6359E6963113a1170de795e4";
  it("accepts a well-formed address", () => expect(isAddress(good)).toBe(true));
  it.each(["", "0x", "0x123", good.slice(0, -1), good + "0", good.replace("0x", ""), "0xZZ" + good.slice(4), 42, null])(
    "rejects %j",
    (v) => expect(isAddress(v)).toBe(false),
  );
  it("normalises to lowercase", () => expect(normalizeAddress(good)).toBe(good.toLowerCase()));
  it.each([["0", 0n], ["123", 123n], ["100000000000000000", 10n ** 17n]])("parseWei(%j)", (s, w) => {
    expect(parseWei(s)).toBe(w);
  });
  it.each(["", "-1", "1.5", "1e3", "01", " 1", "0x1", "abc"])("parseWei rejects %j", (s) => {
    expect(parseWei(s)).toBeNull();
  });
  it("parseWei handles numbers and bigints strictly", () => {
    expect(parseWei(5)).toBe(5n);
    expect(parseWei(1.5)).toBeNull();
    expect(parseWei(-1)).toBeNull();
    expect(parseWei(-1n)).toBeNull();
    expect(parseWei(7n)).toBe(7n);
    expect(parseWei(null)).toBeNull();
  });
});
