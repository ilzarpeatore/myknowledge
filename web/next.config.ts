import type { NextConfig } from "next";

// Development convenience only: with `npm run dev`, /api/* is proxied to the API.
// This proxy caps request bodies at 10 MB, so in Docker the gateway (gateway/Caddyfile)
// routes /api/* to the API directly and this rewrite is never used.
const apiUrl = process.env.KOS_API_INTERNAL_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
