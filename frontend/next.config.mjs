/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Static export. The terminal is a client-only app (all chain I/O happens in
  // the browser through the wallet and the public RPC), so the deployable
  // artifact is plain files in `out/` and there is NO Next.js server process:
  // no image optimizer, no Server Actions, no rewrites, no middleware. That
  // matters because the pinned next@14.2.35 has open advisories that all live in
  // that server runtime (see README, "npm audit disclosure").
  output: "export",
  images: { unoptimized: true },
  // The SDK is ESM-only; let Next transpile it for the client bundle.
  transpilePackages: ["genlayer-js"],
};

export default nextConfig;
