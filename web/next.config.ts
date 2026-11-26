import type { NextConfig } from "next";

// Development convenience only: with `npm run dev`, /api/* is proxied to the API.
// This proxy caps request bodies at 10 MB, so in Docker the gateway (gateway/Caddyfile)
// routes /api/* to the API directly and this rewrite is never used.
const apiUrl = process.env.KOS_API_INTERNAL_URL ?? "http://localhost:8000";

// Browser-side protections for every page. Next.js needs inline scripts/styles for
// hydration; no external script is ever loaded. Media and files come from /api (same
// origin); the only embedded third party is YouTube's privacy-enhanced player.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-src https://www.youtube-nocookie.com",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{
      source: "/:path*",
      headers: [
        { key: "Content-Security-Policy", value: CSP },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Referrer-Policy", value: "same-origin" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
      ],
    }];
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
