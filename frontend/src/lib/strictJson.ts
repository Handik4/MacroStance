/**
 * Strict JSON parser that preserves whether a number was written as an integer.
 *
 * Why this exists: the contract parses feed documents with Python's
 * `json.loads`, where `25.0` is a float and is REJECTED as a numeric field,
 * while `JSON.parse("25.0")` yields the indistinguishable number 25. A browser
 * preview that accepted `25.0` would show a source as CONFIRMED that every
 * validator classifies as an OUTAGE. This parser keeps the lexeme class so the
 * TypeScript port rejects exactly what Python rejects.
 *
 * Integers are returned as JS numbers; numbers with a fraction or exponent are
 * returned as `FloatLexeme`, which is never `Number.isInteger`-compatible.
 */

export class FloatLexeme {
  constructor(readonly lexeme: string) {}
}

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | FloatLexeme
  | JsonValue[]
  | { [key: string]: JsonValue };

export class JsonSyntaxError extends Error {}

const WS = new Set([" ", "\t", "\n", "\r"]);

export function parseStrictJson(text: string): JsonValue {
  let i = 0;

  const fail = (msg: string): never => {
    throw new JsonSyntaxError(`${msg} at ${i}`);
  };
  const skip = () => {
    while (i < text.length && WS.has(text[i])) i++;
  };

  function value(depth: number): JsonValue {
    if (depth > 64) fail("nesting too deep");
    skip();
    const c = text[i];
    if (c === "{") return object(depth);
    if (c === "[") return array(depth);
    if (c === '"') return string();
    if (c === "-" || (c >= "0" && c <= "9")) return number();
    if (text.startsWith("true", i)) {
      i += 4;
      return true;
    }
    if (text.startsWith("false", i)) {
      i += 5;
      return false;
    }
    if (text.startsWith("null", i)) {
      i += 4;
      return null;
    }
    return fail("unexpected token");
  }

  function object(depth: number): JsonValue {
    const out: { [key: string]: JsonValue } = {};
    i++; // {
    skip();
    if (text[i] === "}") {
      i++;
      return out;
    }
    for (;;) {
      skip();
      if (text[i] !== '"') fail("expected key");
      const k = string();
      skip();
      if (text[i] !== ":") fail("expected colon");
      i++;
      // last key wins, like Python
      Object.defineProperty(out, k, { value: value(depth + 1), enumerable: true, writable: true, configurable: true });
      skip();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "}") {
        i++;
        return out;
      }
      fail("expected , or }");
    }
  }

  function array(depth: number): JsonValue {
    const out: JsonValue[] = [];
    i++; // [
    skip();
    if (text[i] === "]") {
      i++;
      return out;
    }
    for (;;) {
      out.push(value(depth + 1));
      skip();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "]") {
        i++;
        return out;
      }
      fail("expected , or ]");
    }
  }

  function string(): string {
    i++; // opening quote
    let out = "";
    for (;;) {
      if (i >= text.length) fail("unterminated string");
      const c = text[i++];
      if (c === '"') return out;
      if (c.charCodeAt(0) < 0x20) fail("control character in string");
      if (c !== "\\") {
        out += c;
        continue;
      }
      const e = text[i++];
      switch (e) {
        case '"': out += '"'; break;
        case "\\": out += "\\"; break;
        case "/": out += "/"; break;
        case "b": out += "\b"; break;
        case "f": out += "\f"; break;
        case "n": out += "\n"; break;
        case "r": out += "\r"; break;
        case "t": out += "\t"; break;
        case "u": {
          const hex = text.slice(i, i + 4);
          if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail("bad unicode escape");
          out += String.fromCharCode(parseInt(hex, 16));
          i += 4;
          break;
        }
        default:
          fail("bad escape");
      }
    }
  }

  function number(): JsonValue {
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i));
    if (!m) return fail("bad number");
    const lexeme = m[0];
    i += lexeme.length;
    if (m[2] !== undefined || m[3] !== undefined) return new FloatLexeme(lexeme);
    const n = Number(lexeme);
    return n === 0 ? 0 : n; // normalise -0 to 0 (Python int has no negative zero)
  }

  const result = value(0);
  skip();
  if (i !== text.length) fail("trailing data");
  return result;
}
