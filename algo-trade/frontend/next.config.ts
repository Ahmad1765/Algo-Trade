import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static HTML export — served by the Python aiohttp server in production.
  output: "export",
  // Each route becomes <route>/index.html so a plain file server can resolve it.
  trailingSlash: true,
  // next/image optimization needs a server; disable for static export.
  images: { unoptimized: true },
  typescript: {
    // Auto-generated .next/types files conflict with TypeScript 5.9+.
    ignoreBuildErrors: true,
  },
  // NOTE: redirects()/rewrites() are intentionally omitted. They are ignored by
  // `output: export`. In production the Python server handles `/` → /dashboard/
  // and serves the JSON API under /api/*. For local `next dev`, run the Python
  // backend and use NEXT_PUBLIC_API_BASE if you need cross-origin calls.
};

export default nextConfig;
