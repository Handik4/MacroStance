import { describe, expect, it } from "vitest";

import { FloatLexeme, JsonSyntaxError, parseStrictJson } from "./strictJson";

describe("parseStrictJson", () => {
  it("parses ordinary documents", () => {
    expect(parseStrictJson('{"a":1,"b":[true,false,null,"x"],"c":{"d":-2}}')).toEqual({
      a: 1,
      b: [true, false, null, "x"],
      c: { d: -2 },
    });
  });
  it.each(["25.0", "2.5e1", "1E2", "-0.5", "1e-3"])("tags %s as a float lexeme", (lex) => {
    const v = parseStrictJson(lex);
    expect(v).toBeInstanceOf(FloatLexeme);
    expect((v as FloatLexeme).lexeme).toBe(lex);
  });
  it.each(["0", "7", "-7", "1234567890"])("keeps %s as an integer", (lex) => {
    expect(parseStrictJson(lex)).toBe(Number(lex));
  });
  it("normalises -0 to 0", () => {
    expect(Object.is(parseStrictJson("-0"), 0)).toBe(true);
  });
  it("last duplicate key wins, like Python", () => {
    expect(parseStrictJson('{"a":1,"a":2}')).toEqual({ a: 2 });
  });
  it("decodes escapes including unicode in keys", () => {
    expect(parseStrictJson('{"epoch\\u005fid":"x\\n\\u00e9"}')).toEqual({ epoch_id: "x\né" });
  });
  it("accepts surrounding whitespace", () => {
    expect(parseStrictJson(' \n\t{"a":1}\r\n ')).toEqual({ a: 1 });
  });
  it.each([
    "", "{", "}", "[1,]", '{"a":}', '{"a" 1}', "tru", "01", "1.", ".5", "+1", "NaN", "Infinity",
    '"unterminated', '{"a":1} trailing', "﻿{}", "'single'", '{"a":"\u0001"}', '{"a":"\\x41"}',
  ])("rejects %j", (bad) => {
    expect(() => parseStrictJson(bad)).toThrow(JsonSyntaxError);
  });
  it("limits nesting depth", () => {
    expect(() => parseStrictJson("[".repeat(100) + "]".repeat(100))).toThrow(JsonSyntaxError);
  });
});
