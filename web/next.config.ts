import { readFileSync } from "node:fs";
import { join } from "node:path";
import type { NextConfig } from "next";

// Development convenience only: with `npm run dev`, /api/* is proxied to the API.
// This proxy caps request bodies at 10 MB, so in Docker the gateway (gateway/Caddyfile)
// routes /api/* to the API directly and this rewrite is never used.
const apiUrl = process.env.KOS_API_INTERNAL_URL ?? "http://localhost:8000";

// Browser-side protections for every page. Next.js needs inline scripts/styles for
// hydration; no external script is ever loaded. The PDF viewer (Inkbar on PDF.js) runs
// its worker from /inkbar/ and decodes some images with WebAssembly ('wasm-unsafe-eval',
// not 'unsafe-eval'); printing goes through a blob: frame. Media and files come from /api (same
// origin); the only embedded third party is YouTube's privacy-enhanced player.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
  "worker-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-src https://www.youtube-nocookie.com blob:",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

// The PDF.js version the viewer's files are copied under (scripts/copy-inkbar.mjs).
const pdfjsVersion: string = JSON.parse(
  readFileSync(join(process.cwd(), "node_modules", "pdfjs-dist", "package.json"), "utf8"),
).version;

const nextConfig: NextConfig = {
  env: { NEXT_PUBLIC_PDFJS_VERSION: pdfjsVersion },
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [{
      // the viewer's engine and fonts: the folder is named for the version, so a
      // cached file is never stale
      source: "/inkbar/:path*",
      headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
    }, {
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
