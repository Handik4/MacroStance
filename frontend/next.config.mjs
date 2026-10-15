import path from "node:path";
import { fileURLToPath } from "node:url";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Pin the workspace root. Without it Next infers one from lockfiles it finds
  // upward on disk (a stray package-lock.json in a parent directory is enough to
  // make it warn and guess).
  turbopack: { root: path.dirname(fileURLToPath(import.meta.url)) },
  reactStrictMode: true,
  poweredByHeader: false,
  // Static export. The terminal is a client-only app (all chain I/O happens in
  // the browser through the wallet and the public RPC), so the deployable
  // artifact is plain files in `out/` and there is NO Next.js server process:
  // no image optimizer, no Server Actions, no rewrites, no middleware. That
  // matters because the Next advisories that live in the server
  // runtime (the previous next@14.2.35 had 23) are not reachable (see README,
  // "npm audit disclosure").
  output: "export",
  images: { unoptimized: true },
  // The SDK is ESM-only; let Next transpile it for the client bundle.
  transpilePackages: ["genlayer-js"],
};

export default nextConfig;
