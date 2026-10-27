/** @type {import('next').NextConfig} */
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
  experimental: {
    serverActions: {
      bodySizeLimit: "50mb",
    },
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
