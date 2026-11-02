/** @type {import('next').NextConfig} */
const isDevelopment = process.env.NODE_ENV !== "production"
const scriptPolicy = isDevelopment
  ? "'self' 'unsafe-eval' 'unsafe-inline' 'wasm-unsafe-eval'"
  : "'self' 'unsafe-inline' 'wasm-unsafe-eval'"

const securityContentSecurityPolicy = [
  "default-src 'self'",
  `script-src ${scriptPolicy}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self' data:",
  "media-src 'self' blob: data:",
  "connect-src 'self' blob: data:",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join("; ")

const nextConfig = {
  // Standalone output traces the exact prod dependency set into .next/standalone
  // so the runtime image can ship without node_modules or the source tree -
  // the whole point of the multi-stage prod Dockerfile (see frontend/Dockerfile).
  //
  // Gated to production builds only: this file is volume-mounted into the
  // live dev container too, and `next dev` picked up the change immediately,
  // restarted itself, and broke chunk resolution for packages loaded via
  // dynamic import (pdfjs-dist, loaded in PdfViewer.tsx via `await
  // import("pdfjs-dist")") - every PDF failed to open with a 404 on its
  // chunk until this was reverted. `next build`/`next start` (what the prod
  // Dockerfile actually runs) always sets NODE_ENV=production, so this still
  // takes effect exactly where it needs to.
  output: process.env.NODE_ENV === "production" ? "standalone" : undefined,
  poweredByHeader: false,
  experimental: {
    serverActions: {
      bodySizeLimit: "50mb",
    },
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
          {
            key: "Referrer-Policy",
            value: "strict-origin-when-cross-origin",
          },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(self), geolocation=()",
          },
          {
            key: "Strict-Transport-Security",
            value: "max-age=31536000; includeSubDomains",
          },
          {
            key: "Content-Security-Policy",
            value: securityContentSecurityPolicy,
          },
        ],
      },
    ]
  },
  webpack: (config) => {
    config.resolve.alias = {
      ...config.resolve.alias,
      "@": "/src",
    }
    return config
  },
}

module.exports = nextConfig
