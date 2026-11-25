import type { NextConfig } from "next";

// The browser only talks to this origin; /api/* is proxied to the FastAPI service.
// KOS_API_INTERNAL_URL is read at build time (standalone output bakes rewrites in).
const apiUrl = process.env.KOS_API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
