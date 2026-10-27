/**
 * Client-side unique ids that work off localhost.
 *
 * `crypto.randomUUID()` is restricted to secure contexts, which means HTTPS
 * or localhost - and this app is normally reached over plain HTTP at a LAN
 * address (http://192.168.x.x:3003) from a phone or a second laptop. There
 * `crypto.randomUUID` is simply undefined, so calling it throws a TypeError
 * and takes down whatever handler touched it. That is not a theoretical
 * edge: it silently broke file uploads for every non-localhost device.
 *
 * `crypto.getRandomValues` has no secure-context restriction, so it covers
 * the same browsers with the same randomness. The Math.random branch exists
 * only for environments with no Web Crypto at all; these ids are local list
 * keys, never security tokens, so a weaker fallback is acceptable there.
 */
export function uid(): string {
  const c = typeof crypto !== "undefined" ? crypto : undefined

  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID()
  }

  if (c && typeof c.getRandomValues === "function") {
    const bytes = c.getRandomValues(new Uint8Array(16))
    bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10xx
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
  }

  return `uid-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}
