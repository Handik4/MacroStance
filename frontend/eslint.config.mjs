import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// Deliberately NOT `eslint-config-next`: its @next/eslint-plugin-next depends on
// fast-glob -> micromatch -> braces, and the braces advisory
// (GHSA-vfj7-8cjw-p6xm) has no patched release. The rule sets below are the ones
// that matter for this code base (TypeScript, hooks correctness incl. the
// React 19 compiler rules, accessibility). The dropped Next-specific rules guard
// <img>, <a href="/...">, next/script and pages-router constructs, none of which
// this static client app uses.
export default defineConfig([
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  jsxA11y.flatConfigs.recommended,
  globalIgnores([".next/**", "out/**", "node_modules/**", "next-env.d.ts", "src/lib/__fixtures__/**"]),
]);
