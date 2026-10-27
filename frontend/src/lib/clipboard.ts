/**
 * Copy to clipboard that also works off localhost.
 *
 * `navigator.clipboard` is secure-context-only (HTTPS or localhost), so it is
 * undefined when this app is opened over plain HTTP at a LAN address - the
 * normal way a second device reaches a self-hosted install. The legacy
 * `document.execCommand("copy")` path has no such restriction, so it covers
 * exactly the case the modern API drops.
 *
 * Returns whether the text actually made it to the clipboard, so callers can
 * avoid confirming a copy that never happened.
 */
export function copyText(text: string): boolean {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    // Fire-and-forget: the promise only rejects on a permissions denial, which
    // the execCommand fallback below could not fix anyway.
    navigator.clipboard.writeText(text)
    return true
  }

  if (typeof document === "undefined") return false

  const el = document.createElement("textarea")
  el.value = text
  // Off-screen rather than display:none - a hidden element cannot be selected.
  el.style.position = "fixed"
  el.style.top = "-9999px"
  el.setAttribute("readonly", "")
  document.body.appendChild(el)
  try {
    el.select()
    return document.execCommand("copy")
  } catch {
    return false
  } finally {
    document.body.removeChild(el)
  }
}
