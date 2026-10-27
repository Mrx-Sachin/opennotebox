/**
 * Where the API lives, from the browser's point of view.
 *
 * The deployment serves the frontend and the API from one origin behind
 * Traefik, so the default is a relative base: requests go to whatever origin
 * the page came from. That is deliberately the simplest possible answer -
 * it is automatically correct on localhost, on a LAN address and over
 * Tailscale, with no rebuild and no per-address configuration, and it means
 * the browser never makes a cross-origin request in the first place.
 *
 * This replaces an older scheme that baked in http://localhost:8001 and then
 * string-replaced "localhost" with window.location.hostname at runtime. That
 * worked only because the two ports happened to be published side by side,
 * and it forced every LAN device into a cross-origin request that CORS then
 * had to be widened to permit.
 *
 * NEXT_PUBLIC_API_URL still overrides, for the genuinely split deployment
 * where the API is on another host. Empty or unset means same origin.
 */
const configured = (process.env.NEXT_PUBLIC_API_URL || "").trim().replace(/\/$/, "")

/** Base for HTTP calls. Empty string means "relative to the current origin". */
export const API_BASE = configured

/** Base for WebSocket URLs, which cannot be relative and so need an origin. */
export function wsBase(): string {
  const configuredWs = (process.env.NEXT_PUBLIC_WS_URL || "").trim().replace(/\/$/, "")
  if (configuredWs) return configuredWs

  if (configured) return configured.replace(/^http/, "ws")

  if (typeof window === "undefined") return ""
  // wss when the page is https, otherwise a browser blocks the connection as
  // mixed content.
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}`
}

/** Absolute URL for a path, for the cases that need one - <audio src>, links
 *  opened in a new tab, anything handed to something other than fetch/axios. */
export function apiUrl(path: string): string {
  if (API_BASE) return `${API_BASE}${path}`
  if (typeof window === "undefined") return path
  return `${window.location.origin}${path}`
}
